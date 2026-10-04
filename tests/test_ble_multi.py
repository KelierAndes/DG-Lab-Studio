from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
import unittest.mock

from dglab.ble import BleClient, V3_NOTIFY
from dglab.state import EngineState, Slot, StateEvents, family_of
from dglab.waves import CONTINUOUS, PULSE_STREAM, SILENT


class FakeBleakClient:
    instances: dict[str, "FakeBleakClient"] = {}

    def __init__(self, address: str, disconnected_callback=None, **kwargs):
        self.address = address
        self.disconnected_callback = disconnected_callback
        self.written: list[tuple[str, bytes]] = []
        self.notifies: dict[str, object] = {}
        FakeBleakClient.instances[address] = self

    def simulate_drop(self) -> None:
        if self.disconnected_callback:
            self.disconnected_callback(self)

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    async def start_notify(self, char: str, cb) -> None:
        self.notifies[char] = cb

    async def read_gatt_char(self, char: str) -> bytearray:
        return bytearray([77])

    async def write_gatt_char(self, char: str, data, response: bool = False) -> None:
        self.written.append((char, bytes(data)))


def free_udp_port() -> int:
    import socket as _socket

    s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _make_client() -> BleClient:
    events = StateEvents()
    return BleClient(
        events,
        soft_limit_a=200,
        soft_limit_b=200,
    )


class BleMultiDeviceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        FakeBleakClient.instances.clear()
        self.patcher = unittest.mock.patch("dglab.ble.BleakClient", FakeBleakClient)
        self.patcher.start()
        self.ble = _make_client()

    async def asyncTearDown(self):
        self.patcher.stop()

    async def test_two_devices_connect_and_route(self):
        await self.ble.connect("addr-coyote", "coyote_v3")
        await self.ble.connect("addr-ovc", "ovc")
        self.assertEqual(len(self.ble.sessions), 2)
        self.assertEqual(set(self.ble.state.slots), {"addr-coyote", "addr-ovc"})
        self.assertEqual(self.ble.state.slots["addr-coyote"].type, "COYOTE_030")
        self.assertEqual(self.ble.state.slots["addr-ovc"].type, "OVC_1")

        await self.ble.set_strength("A", 30, slot_id="addr-ovc")
        await self.ble.set_strength("A", 11, slot_id="addr-coyote")
        self.assertEqual(self.ble.state.slots["addr-ovc"].strength["A"], 30)
        self.assertEqual(self.ble.state.slots["addr-coyote"].strength["A"], 11)

        await self.ble.set_wave("A", "BUBBLE", slot_id="addr-coyote")
        await self.ble.set_wave("A", "ALARM", slot_id="addr-ovc")
        self.assertEqual(len(self.ble.sessions["addr-coyote"]._cycles["A"].frames), 2)
        self.assertTrue(len(self.ble.sessions["addr-ovc"]._cycles["A"].frames) >= 1)

        await asyncio.sleep(0.25)
        for address, expected_head in (("addr-coyote", 0xB0), ("addr-ovc", 0xB0)):
            client = FakeBleakClient.instances[address]
            b0s = [data for _char, data in client.written if data[0] == 0xB0]
            self.assertTrue(b0s, address)
            frame = b0s[-1]
            self.assertEqual(len(frame), 20)
            if address == "addr-ovc":
                self.assertEqual(bytes(frame[1:8]), bytes(7))

        await self.ble.emergency_stop()
        self.assertEqual(self.ble.state.slots["addr-ovc"].strength["A"], 0)
        self.assertEqual(self.ble.state.slots["addr-ovc"].strength["B"], 0)

        await self.ble.disconnect("addr-coyote")
        self.assertEqual(set(self.ble.sessions), {"addr-ovc"})
        self.assertEqual(self.ble.state.status_text, "已连接 1 台设备")

    async def test_wave_continuity_and_strength_ack_flow(self):
        await self.ble.connect("addr-c", "coyote_v3")
        session = self.ble.sessions["addr-c"]
        self.assertTrue(session._cycles["A"].frames)
        self.assertTrue(session._cycles["B"].frames)

        from dglab.official_waveforms import COYOTE_WAVEFORMS, CoyoteWaveform
        await self.ble.set_wave("A", CoyoteWaveform.RHYTHM, slot_id="addr-c")
        client = FakeBleakClient.instances["addr-c"]

        rhythm = {f.lower() for f in COYOTE_WAVEFORMS[CoyoteWaveform.RHYTHM]["raw"]}
        steady = bytes.fromhex("2828282864646464")
        await asyncio.sleep(0.5)
        b0s = [d for _c, d in client.written if d[0] == 0xB0]
        self.assertTrue(len(b0s) >= 4)
        self.assertTrue(all(b[4:12].hex() in rhythm for b in b0s))
        await asyncio.sleep(1.2)
        b0s = [d for _c, d in client.written if d[0] == 0xB0]
        self.assertTrue(len(b0s) >= 12)
        self.assertTrue(all(b[4:12].hex() in rhythm for b in b0s))

        await self.ble.set_strength("A", 30, slot_id="addr-c")
        await asyncio.sleep(0.15)
        b0s = [d for _c, d in client.written if d[0] == 0xB0]
        applied = [b for b in b0s if (b[1] & 0x0F) == 0b1100]
        self.assertTrue(applied, "no absolute-set frame written")
        frame = applied[-1]
        self.assertEqual(frame[2], 30)
        seq = frame[1] >> 4
        self.assertEqual(seq > 0, True)

        echo = bytes([0xB1, seq, 30, 0])
        self.ble._on_notify(session, None, echo)
        self.assertEqual(session._awaiting_seq, 0)
        await self.ble.set_strength("A", 50, slot_id="addr-c")
        await asyncio.sleep(0.15)
        b0s = [d for _c, d in client.written if d[0] == 0xB0]
        applied = [b for b in b0s if (b[1] & 0x0F) == 0b1100]
        self.assertEqual(applied[-1][2], 50)

    async def test_writer_survives_transient_errors(self):
        await self.ble.connect("addr-c", "coyote_v3")
        client = FakeBleakClient.instances["addr-c"]

        original = client.write_gatt_char
        failures = {"count": 0}

        async def flaky(char, data, response=False):
            if failures["count"] < 3:
                failures["count"] += 1
                raise OSError("transient radio hiccup")
            await original(char, data, response=response)

        client.write_gatt_char = flaky
        await asyncio.sleep(1.0)
        self.assertTrue(len(client.written) >= 5, len(client.written))

    async def test_battery_and_pressure_notify(self):
        await self.ble.connect("addr-bmtr", "bmtr")
        self.assertIsNotNone(self.ble.sessions["addr-bmtr"].writer_task)

        session = self.ble.sessions["addr-bmtr"]
        client = FakeBleakClient.instances["addr-bmtr"]
        battery_cb = client.notifies.get("00001500-0000-1000-8000-00805f9b34fb")
        if battery_cb:
            battery_cb(None, bytearray([55]))
            self.assertEqual(self.ble.state.slots["addr-bmtr"].battery, 55)

        notify_cb = client.notifies[V3_NOTIFY]
        data = bytearray(15)
        data[0] = 0xD0
        data[8:10] = (1704).to_bytes(2, "little")
        notify_cb(None, data)
        self.assertAlmostEqual(self.ble.state.slots["addr-bmtr"].pressure, 17.04)


class OscProbeCardTests(unittest.TestCase):
    def test_probe_card_states(self):
        import time as _time

        from ui import live

        class FakeOsc:
            _running = True
            last_rx = None
            rx_count = 0

        class FakeEngine:
            config = {"osc": {"in_port": 9001}}
            osc = None

        engine = FakeEngine()
        card = live.osc_probe_card(engine)
        self.assertEqual(card["value"], "已停止")
        self.assertEqual(len(card["detail"]), 2)

        osc = FakeOsc()
        engine.osc = osc
        card = live.osc_probe_card(engine)
        self.assertEqual(card["value"], "无数据")
        self.assertIn("9001", card["detail"][0])

        osc.last_rx = _time.monotonic() - 2.0
        osc.rx_count = 7
        card = live.osc_probe_card(engine)
        self.assertEqual(card["value"], "已连接")
        self.assertIn("7", card["detail"][0])

        osc.last_rx = _time.monotonic() - 120.0
        card = live.osc_probe_card(engine)
        self.assertEqual(card["value"], "无数据")


class ChartRenderTests(unittest.TestCase):
    def test_render_wave_live_png(self):
        import time as _time

        from ui import charts

        now = _time.monotonic()
        samples = [(now - i * 0.1, (10, 20, 30, 40), (50, 60, 70, 80))
                   for i in range(50)]
        for dark in (False, True):
            png = charts.render_wave_live(samples, dark=dark)
            self.assertEqual(png[:8],
                             bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]))
            self.assertTrue(len(png) > 1000)

    def test_render_wave_live_empty(self):
        from ui import charts

        png = charts.render_wave_live([], dark=True)
        self.assertEqual(png[:8],
                         bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]))


class OvcRoundingTests(unittest.TestCase):
    def test_clamp_ovc_strength(self):
        from ui import live

        self.assertEqual(live.clamp_ovc_strength(15), 20)
        self.assertEqual(live.clamp_ovc_strength(24), 20)
        self.assertEqual(live.clamp_ovc_strength(25), 30)
        self.assertEqual(live.clamp_ovc_strength(4), 10)
        self.assertEqual(live.clamp_ovc_strength(205), 200)
        self.assertEqual(live.clamp_ovc_strength(0, allow_zero=True), 0)
        self.assertEqual(live.clamp_ovc_strength(-3, allow_zero=True), 0)
        self.assertEqual(live.clamp_ovc_strength(0), 10)


class LiveDataTests(unittest.TestCase):
    def test_classify_log_levels(self):
        from ui import live

        self.assertEqual(live.classify_log("连接失败: timeout"), "error")
        self.assertEqual(live.classify_log("急停异常 1006"), "error")
        self.assertEqual(live.classify_log("心跳超时，重试 1/3"), "warn")
        self.assertEqual(live.classify_log("安全限幅触发：B 通道强度下降"), "warn")
        self.assertEqual(live.classify_log('<< {"type":"ping"}'), "debug")
        self.assertEqual(live.classify_log("监听已启动 0.0.0.0:9999"), "info")

    def test_output_row_percent(self):
        from ui import live

        slot = Slot(slot_id="s", name="t", type="47L121000",
                    strength={"A": 100, "B": 0},
                    strength_limit={"A": 200, "B": 200})
        row = live.output_row(slot, "A")
        self.assertEqual((row["value"], row["limit"], row["percent"]),
                         (100, 200, 50.0))

    def test_osc_value_rows_shapes(self):
        from ui import live

        state = EngineState(backend="ble", connected=True)
        state.slots["a"] = Slot(slot_id="a", type="COYOTE_1",
                                strength={"A": 1, "B": 2}, battery=50)
        state.slots["b"] = Slot(slot_id="b", type="BMTR_1",
                                pressure=30.0, edge_state=2)
        rows = live.osc_value_rows(state)
        addrs = [r["address"] for r in rows]
        self.assertIn("…COYOTEStrengthA", addrs)
        self.assertIn("…BMTRPressure", addrs)
        pressure = next(r for r in rows if r["address"] == "…BMTRPressure")
        self.assertEqual(pressure["percent"], 50)

    def test_wave_items_shapes(self):
        from ui import live

        coyote = live.wave_items("COYOTE")
        ovc = live.wave_items("OVC")
        self.assertEqual(coyote[0][1], SILENT)
        self.assertEqual(ovc[0][1], SILENT)
        self.assertEqual(coyote[-1][1], PULSE_STREAM)
        self.assertEqual(coyote[-2][1], CONTINUOUS)
        self.assertGreater(len(coyote), len(ovc))

    def test_family_of_helpers(self):
        self.assertEqual(family_of("OVC_1"), "OVC")
        self.assertEqual(family_of("BMTR_1"), "BMTR")
        self.assertEqual(family_of("COYOTE_030"), "COYOTE")


if __name__ == "__main__":
    unittest.main()

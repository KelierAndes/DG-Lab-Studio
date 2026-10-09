from __future__ import annotations

import asyncio
import unittest

from dglab.ble import BleClient
from dglab.socket_v3 import PULSE_SEND_S, PULSE_WINDOW_FRAMES, SocketV3Client
from dglab.socket_v4 import SocketV4Client
from dglab.state import StateEvents
from dglab.waves import (CONTINUOUS, PULSE_STREAM, SILENT, build_frame,
                         pulse_frame, resolve_wave_frames, wave_order)
from test_ble_multi import FakeBleakClient


def _v4_client() -> SocketV4Client:
    client = SocketV4Client(events=StateEvents())
    client._handle_frame({"type": "hello", "clientId": "ctrl"})
    client._handle_frame({"type": "client_attached", "clientId": "app"})
    client._replace_devices("app", [{"slotId": "s1", "type": "COYOTE_030"}])
    return client


class PulseFrameTests(unittest.TestCase):
    def test_resolve_returns_empty(self):
        self.assertEqual(resolve_wave_frames(PULSE_STREAM), [])
        self.assertTrue(resolve_wave_frames(SILENT))
        self.assertTrue(resolve_wave_frames(CONTINUOUS))

    def test_wave_order_appends_pulse_stream(self):
        for family, last_builtin in (("COYOTE", "TEASE_2"), ("OVC", None)):
            order = wave_order(family)
            self.assertEqual(order[-1], PULSE_STREAM)
            self.assertEqual(order.index(PULSE_STREAM), len(order) - 1)
            if last_builtin is not None:
                self.assertEqual(order[-2], last_builtin)
            self.assertEqual(len(order),
                             len([w for w in order if w != PULSE_STREAM]) + 1)

    def test_pulse_frame_builds_and_clamps(self):
        self.assertEqual(pulse_frame(440), build_frame([168] * 4, [100] * 4))
        self.assertEqual(pulse_frame(0)[:2], "0a")
        self.assertEqual(pulse_frame(99999)[:2], "f0")
        self.assertEqual(pulse_frame(100, level=250)[8:], "64646464")
        self.assertEqual(pulse_frame(100, level=-1)[8:], "00000000")

    def test_vibration_frame_square_wave(self):
        from dglab.waves import pulse_frame_vibration
        f1 = pulse_frame_vibration(1000, 80, t_start=0.0)
        raw = bytes.fromhex(f1)
        self.assertEqual(raw[4:8], bytearray([80, 80, 0, 0]))
        f2 = pulse_frame_vibration(1000, 80, t_start=0.05)
        raw2 = bytes.fromhex(f2)
        self.assertEqual(raw2[4:8], bytearray([0, 0, 80, 80]))
        self.assertEqual(bytes.fromhex(pulse_frame_vibration(100, 300, 0.0))[4:6],
                         bytearray([100, 100]))

    def test_engine_builds_family_aware_frames(self):
        import app as app_module
        from dglab.state import EngineState, Slot

        engine = app_module.Engine()

        class OvcBackend:
            def __init__(self):
                self.state = EngineState(backend="ble")
                self.state.slots["ovc"] = Slot(slot_id="ovc", name="o",
                                               type="OVC_1")
                self.pushed = []

            async def push_pulse_frame(self, slot_id, channel, frame):
                self.pushed.append((slot_id, channel, frame))

        backend = OvcBackend()
        engine._backend = backend
        engine._selected_wave["A"] = PULSE_STREAM
        asyncio.run(engine.push_pulse_stream(1000, channel="A", level=80))
        self.assertEqual(len(backend.pushed), 1)
        raw = bytes.fromhex(backend.pushed[0][2])
        self.assertTrue(all(s in (80, 0) for s in raw[4:8]))
        self.assertIn(len(set(raw[4:8])), (1, 2))
        self.assertEqual(raw[0], 0xf0)

        class CoyoteBackend(OvcBackend):
            def __init__(self):
                super().__init__()
                self.state.slots["cy"] = Slot(slot_id="cy", name="c",
                                              type="COYOTE_030")

        backend2 = CoyoteBackend()
        engine._backend = backend2
        engine._selected_wave["A"] = PULSE_STREAM
        engine._pulse_phase.clear()
        asyncio.run(engine.push_pulse_stream(1000, channel="A", level=80))
        raw2 = bytes.fromhex(backend2.pushed[0][2])
        self.assertEqual(raw2[4:8], bytearray([80] * 4))
        self.assertEqual(raw2[0], 0xf0)


class BlePulseStreamTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.patcher = unittest.mock.patch("dglab.ble.BleakClient", FakeBleakClient)
        self.patcher.start()
        self.ble = BleClient(StateEvents(), soft_limit_a=200, soft_limit_b=200)
        await self.ble.connect("addr-1", "coyote_v3")

    async def asyncTearDown(self):
        self.patcher.stop()

    async def test_select_starts_empty_and_push_replaces(self):
        await self.ble.set_wave("A", PULSE_STREAM, slot_id="addr-1")
        cycle = self.ble.sessions["addr-1"]._cycles["A"]
        self.assertEqual(cycle.frames, [])

        await self.ble.push_pulse_frame("addr-1", "A", pulse_frame(100))
        await self.ble.push_pulse_frame("addr-1", "A", pulse_frame(200))
        self.assertEqual(cycle.frames, [pulse_frame(200)])
        self.assertEqual(cycle.next_frame(), pulse_frame(200))

    async def test_push_stream_tracks_latest_under_load(self):
        await self.ble.push_pulse_frame("addr-1", "A", pulse_frame(100))
        cycle = self.ble.sessions["addr-1"]._cycles["A"]
        for i in range(100):
            freq = 100 + (i // 10) * 100
            await self.ble.push_pulse_frame("addr-1", "A", pulse_frame(freq))
        self.assertEqual(cycle.frames, [pulse_frame(1000)])
        for _ in range(5):
            self.assertEqual(cycle.next_frame(), pulse_frame(1000))

    async def test_push_unknown_device_or_bmtr_ignored(self):
        await self.ble.push_pulse_frame("nope", "A", pulse_frame(100))
        await self.ble.connect("addr-bmtr", "bmtr")
        await self.ble.push_pulse_frame("addr-bmtr", "A", pulse_frame(100))
        self.assertEqual(self.ble.sessions["addr-bmtr"]._cycles["A"].frames, [])


class V4PulseStreamTests(unittest.IsolatedAsyncioTestCase):
    def _client(self) -> SocketV4Client:
        client = _v4_client()
        sent: list[dict] = []

        async def fake_send(frame):
            sent.append(frame)

        client._send_raw = fake_send
        client.sent = sent
        return client

    def _ops(self, client):
        return [f["data"]["data"] for f in client.sent
                if f["data"].get("m") == "device.op"]

    async def test_select_marks_pulse_channel_and_skips_silent_fill(self):
        client = self._client()
        await client.set_wave("A", PULSE_STREAM, slot_id="s1")
        self.assertIn(("s1", "A"), client._pulse_keys)
        self.assertEqual(client._cycle("s1", "A").frames, [])
        client.sent.clear()
        await client._wave_tick()
        self.assertEqual([op for op in self._ops(client) if op.get("c") == 0], [])

    async def test_first_frame_sent_immediately_then_streamed(self):
        client = self._client()
        await client.set_wave("A", PULSE_STREAM, slot_id="s1")
        client.sent.clear()
        await client.push_pulse_frame("s1", "A", pulse_frame(440))
        ops = self._ops(client)
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0]["im"], True)
        self.assertEqual(ops[0]["v"], [pulse_frame(440)])

        await client.push_pulse_frame("s1", "A", pulse_frame(880))
        self.assertEqual(client._cycle("s1", "A").frames, [pulse_frame(880)])
        client.sent.clear()
        await client._wave_tick()
        ops = [op for op in self._ops(client) if op.get("c") == 0]
        self.assertTrue(ops)
        self.assertNotIn("im", ops[-1])
        self.assertTrue(all(f == pulse_frame(880) for f in ops[-1]["v"]))

    async def test_switching_away_discards_pulse_key(self):
        client = self._client()
        await client.set_wave("A", PULSE_STREAM, slot_id="s1")
        await client.set_wave("A", CONTINUOUS, slot_id="s1")
        self.assertNotIn(("s1", "A"), client._pulse_keys)
        client.sent.clear()
        client._play_deadline[("s1", "A")] = 0.0
        await client._wave_tick()
        self.assertTrue([op for op in self._ops(client) if op.get("c") == 0])


class V3PulseStreamTests(unittest.IsolatedAsyncioTestCase):
    def _paired_client(self) -> SocketV3Client:
        client = SocketV3Client(events=StateEvents())
        client.state.client_id = "ctrl"
        client.state.target_id = "app"
        client.state.paired = True
        sent: list[dict] = []

        async def fake_send(payload):
            sent.append(payload)

        client._send = fake_send
        client.sent = sent
        return client

    async def test_select_clears_buffer_and_queue(self):
        client = self._paired_client()
        await client.push_pulse_frame("s", "A", pulse_frame(100))
        self.assertIn("A", client._pulse_buf)
        await client.send_wave("A", PULSE_STREAM)
        self.assertNotIn("A", client._pulse_buf)
        self.assertEqual(client.sent[-1]["type"], 4)

    async def test_push_throttled_window_resend(self):
        client = self._paired_client()
        await client.push_pulse_frame("s", "A", pulse_frame(100))
        waves = [f for f in client.sent if f.get("type") == "clientMsg"]
        self.assertEqual(len(waves), 1)
        import json as _json
        frames = _json.loads(waves[0]["message"].split(":", 1)[1])
        self.assertEqual(len(frames), 1)
        self.assertEqual(waves[0]["time"], 0.2)
        for _ in range(2):
            await client.push_pulse_frame("s", "A", pulse_frame(100))
        waves = [f for f in client.sent if f.get("type") == "clientMsg"]
        self.assertEqual(len(waves), 1)

        client._pulse_last["A"] -= PULSE_SEND_S * 2
        await client.push_pulse_frame("s", "A", pulse_frame(100))
        for _ in range(20):
            await client.push_pulse_frame("s", "A", pulse_frame(100))
        client._pulse_last["A"] -= PULSE_SEND_S * 2
        await client.push_pulse_frame("s", "A", pulse_frame(100))
        waves = [f for f in client.sent if f.get("type") == "clientMsg"]
        self.assertEqual(len(waves), 3)
        frames = _json.loads(waves[-1]["message"].split(":", 1)[1])
        self.assertEqual(len(frames), PULSE_WINDOW_FRAMES)
        self.assertEqual(waves[-1]["time"], PULSE_WINDOW_FRAMES * 0.1 * 2)

    async def test_push_without_pairing_dropped(self):
        client = SocketV3Client(events=StateEvents())
        client.state.client_id = "ctrl"
        sent: list[dict] = []

        async def fake_send(payload):
            sent.append(payload)

        client._send = fake_send
        await client.push_pulse_frame("s", "A", pulse_frame(100))
        self.assertEqual(sent, [])


class FakeEngineBackend:

    def __init__(self, slots=("s1",)):
        from dglab.state import EngineState, Slot
        self.state = EngineState(backend="ble")
        for sid in slots:
            self.state.slots[sid] = Slot(slot_id=sid, name="t", type="COYOTE_030")
        self.pushed: list[tuple[str, str, str]] = []

    async def push_pulse_frame(self, slot_id, channel, frame):
        self.pushed.append((slot_id, channel, frame))


class _PulseApi:

    def __init__(self):
        self.pushed: list[tuple[str, int, int]] = []

    def resolve_slot(self, family=""):
        return "s1"

    def push_pulse(self, channel, value, level=100, slot_id=None):
        self.pushed.append((channel, int(value), int(level)))
        return None

    def run(self, coro):
        if coro is not None and hasattr(coro, "close"):
            coro.close()


class PulseParamTests(unittest.TestCase):

    def test_core_inputs_declare_pulse_params(self):
        from dglab.params import input_spec

        for key, family in (("in_pulse_a", "COYOTE"), ("in_pulse_b", "COYOTE"),
                            ("in_ovc_pulse_a", "OVC"), ("in_ovc_pulse_b", "OVC")):
            spec = input_spec(key)
            self.assertIsNotNone(spec, key)
            self.assertEqual(spec["action"], "pulse")
            self.assertEqual(spec["family"], family)
            self.assertEqual(spec["range"], (0, 1000))
            self.assertEqual(spec["channel"], key[-1].upper())

    def test_dispatcher_zero_pushes_silent_frame(self):
        from dglab.params import PULSE_PUSH_MIN_INTERVAL_S, build_dispatchers

        api = _PulseApi()
        with unittest.mock.patch("dglab.params.PULSE_PUSH_MIN_INTERVAL_S", 0):
            build_dispatchers(api)["in_pulse_a"](0)
        self.assertEqual(api.pushed, [("A", 10, 0)])

    def test_dispatcher_frequency_pushes_full_level(self):
        from dglab.params import build_dispatchers

        api = _PulseApi()
        with unittest.mock.patch("dglab.params.PULSE_PUSH_MIN_INTERVAL_S", 0):
            dispatchers = build_dispatchers(api)
            dispatchers["in_pulse_a"](440)
            dispatchers["in_pulse_b"](1000)
            dispatchers["in_pulse_a"](5)
        self.assertEqual(api.pushed, [("A", 440, 100), ("B", 1000, 100),
                                      ("A", 10, 100)])

    def test_dispatcher_level_follows_api_hook(self):
        from dglab.params import build_dispatchers

        api = _PulseApi()
        api.levels = {"A": 62}
        api.pulse_level = lambda channel: api.levels.get(channel, 0)
        with unittest.mock.patch("dglab.params.PULSE_PUSH_MIN_INTERVAL_S", 0):
            build_dispatchers(api)["in_pulse_a"](440)
            api.pulse_level = lambda ch: 1 / 0
            build_dispatchers(api)["in_pulse_a"](440)
        self.assertEqual(api.pushed, [("A", 440, 62), ("A", 440, 100)])

    def test_dispatcher_rate_limited_to_10hz(self):
        from dglab.params import build_dispatchers

        api = _PulseApi()
        dispatchers = build_dispatchers(api)
        dispatchers["in_pulse_a"](440)
        dispatchers["in_pulse_a"](880)
        self.assertEqual(len(api.pushed), 1)
        self.assertEqual(api.pushed[0], ("A", 440, 100))

    def test_fire_card_period_pushes_every_tick(self):
        from dglab.mapping import MappingEngine
        from dglab.params import input_ranges

        sent: list[tuple[str, int]] = []
        engine = MappingEngine(lambda t, v: sent.append((t, v)),
                               ranges=input_ranges())
        engine.set_event_cards([{
            "name": "推流", "trigger": "period", "arg": 100,
            "actions": [{"dir": "in", "param": "in_pulse_a", "var": "x"}],
        }])
        engine.signal("x", 440)
        engine.tick_event_cards(0.0)
        engine.tick_event_cards(0.05)
        engine.tick_event_cards(0.10)
        engine.tick_event_cards(0.20)
        pulse_frames = [v for t, v in sent if t == "in_pulse_a"]
        self.assertEqual(pulse_frames, [440, 440, 440])

    def test_fire_card_strength_keeps_dedup(self):
        from dglab.mapping import MappingEngine
        from dglab.params import input_ranges

        sent: list[tuple[str, int]] = []
        engine = MappingEngine(lambda t, v: sent.append((t, v)),
                               ranges=input_ranges())
        engine.set_event_cards([{
            "name": "强度", "trigger": "period", "arg": 100,
            "actions": [{"dir": "in", "param": "in_strength_a", "var": "x"}],
        }])
        engine.signal("x", 40)
        engine.tick_event_cards(100.0)
        engine.tick_event_cards(200.0)
        engine.tick_event_cards(300.0)
        self.assertEqual([v for t, v in sent if t == "in_strength_a"], [40])

    def test_pump_dispatches_pulse_target_every_pump(self):
        from dglab.mapping import MappingEngine
        from dglab.params import input_ranges

        sent: list[tuple[str, int]] = []
        engine = MappingEngine(lambda t, v: sent.append((t, v)),
                               ranges=input_ranges())
        engine.set_mappings({"in_pulse_a": "{x}"})
        engine.signal("x", 440)
        engine.pump()
        self.assertEqual([v for t, v in sent if t == "in_pulse_a"],
                         [0, 440, 440])

    def test_naming_pulse_template(self):
        from dglab.naming import default_input_name

        cfg = {"prefix": "DGLab",
               "device_prefixes": {"COYOTE": "DGLab", "OVC": "DGLabOvc"}}
        self.assertEqual(default_input_name(cfg, "in_pulse_a"), "DGLabPulseA")
        self.assertEqual(default_input_name(cfg, "in_ovc_pulse_b"),
                         "DGLabOvcInPulseB")


class EnginePulseStreamTests(unittest.IsolatedAsyncioTestCase):
    def _engine(self, backend):
        import app as app_module

        engine = app_module.Engine()
        engine._backend = backend
        return engine

    async def test_push_only_lands_when_selected(self):
        backend = FakeEngineBackend()
        engine = self._engine(backend)
        await engine.push_pulse_stream(440, channel="A")
        self.assertEqual(backend.pushed, [])
        engine._selected_wave["A"] = PULSE_STREAM
        await engine.push_pulse_stream(440, channel="A", level=60)
        self.assertEqual(len(backend.pushed), 1)
        sid, ch, frame = backend.pushed[0]
        self.assertEqual((sid, ch), ("s1", "A"))
        self.assertEqual(frame, pulse_frame(440, 60))

    async def test_push_without_backend_is_silent(self):
        engine = self._engine(FakeEngineBackend())
        engine._backend = None
        engine._selected_wave["A"] = PULSE_STREAM
        await engine.push_pulse_stream(440, channel="A")

    async def test_push_unsupported_backend_raises(self):
        class Bare:
            pass

        engine = self._engine(Bare())
        engine._selected_wave["A"] = PULSE_STREAM
        with self.assertRaises(RuntimeError):
            await engine.push_pulse_stream(440, channel="A")

    async def test_module_context_cannot_push_pulse(self):
        """模块直写设备输出被核心拦下：只记日志，不发帧。"""
        import plugins as plugins_module

        backend = FakeEngineBackend()
        engine = self._engine(backend)
        engine._selected_wave["A"] = PULSE_STREAM

        class FakeModule:
            id = "m"

        seen: list[str] = []
        engine.events.on("log", seen.append)
        ctx = plugins_module.ModuleContext(engine, FakeModule())
        self.assertIsNone(ctx.push_pulse_stream(500, channel="A", level=40))
        self.assertEqual(backend.pushed, [])
        self.assertTrue(any("已拦截模块直写设备输出" in text for text in seen))


if __name__ == "__main__":
    unittest.main()

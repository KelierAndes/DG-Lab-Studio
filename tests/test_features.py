from __future__ import annotations

import asyncio
import unittest
import unittest.mock

from dglab.ble import LED_COLORS
from dglab.socket_v4 import SocketV4Client
from dglab.state import StateEvents
from dglab.waves import CONTINUOUS, SILENT

from test_ble_multi import FakeBleakClient, _make_client


class V4ContinuousWaveTests(unittest.IsolatedAsyncioTestCase):
    async def test_wave_loop_sends_and_monitors(self):
        client = SocketV4Client(events=StateEvents())
        client._handle_frame({"type": "hello", "clientId": "ctrl"})
        client._handle_frame({"type": "client_attached", "clientId": "app"})
        client._replace_devices("app", [{"slotId": "s1", "type": "COYOTE_030"}])
        sent: list[dict] = []

        async def fake_send(frame):
            sent.append(frame)

        client._send_raw = fake_send

        from dglab.official_waveforms import COYOTE_WAVEFORMS, CoyoteWaveform
        await client.set_wave("A", CoyoteWaveform.BUBBLE, slot_id="s1")

        pulses = [f["data"]["data"] for f in sent if f["data"].get("m") == "device.op"]
        clears = [f for f in sent if f["data"].get("m") == "device.op.clear"]
        self.assertEqual(len(clears), 0)
        self.assertEqual(len(pulses), 1)
        self.assertEqual(pulses[0]["t"], 0)
        self.assertEqual(pulses[0]["s"], "s1")
        self.assertEqual(pulses[0]["c"], 0)
        self.assertEqual(pulses[0]["im"], True)
        self.assertEqual(len(pulses[0]["v"]), 10)
        self.assertEqual(pulses[0]["d"], 1000)
        self.assertEqual(pulses[0]["v"][0], COYOTE_WAVEFORMS[CoyoteWaveform.BUBBLE]["raw"][0])

        self.assertGreaterEqual(len(client.monitors["s1"].samples), 10)
        _t, a, b = client.monitors["s1"].samples[0]
        self.assertEqual(len(a), 4)
        self.assertEqual(b, (0, 0, 0, 0))

        await client.clear_wave("A", slot_id="s1")
        sent.clear()
        await client._wave_tick()
        silent_ops = [f["data"]["data"] for f in sent
                      if f["data"].get("m") == "device.op" and f["data"]["data"].get("c") == 0]
        self.assertTrue(silent_ops)
        for frame in silent_ops[-1]["v"]:
            self.assertEqual(frame[8:], "00000000")

    async def test_wave_tick_tops_up_before_deadline(self):
        client = SocketV4Client(events=StateEvents())
        client._handle_frame({"type": "hello", "clientId": "ctrl"})
        client._handle_frame({"type": "client_attached", "clientId": "app"})
        client._replace_devices("app", [{"slotId": "s1", "type": "COYOTE_030"}])
        sent: list[dict] = []

        async def fake_send(frame):
            sent.append(frame)

        client._send_raw = fake_send
        await client._wave_tick()
        ops = [f["data"]["data"] for f in sent if f["data"].get("m") == "device.op"]
        self.assertEqual(len(ops), 2)
        self.assertNotIn("im", ops[0])

        samples = client.monitors["s1"].samples
        deadline = max(client._play_deadline[("s1", "A")],
                       client._play_deadline[("s1", "B")])
        last = max(t for t, _a, _b in samples)
        self.assertAlmostEqual(deadline, last + 0.1, delta=0.001)

        sent.clear()
        await client._wave_tick()
        self.assertEqual([f for f in sent if f["data"].get("m") == "device.op"], [])

        client._play_deadline[("s1", "A")] = 0.0
        await client._wave_tick()
        ops = [f["data"]["data"] for f in sent if f["data"].get("m") == "device.op"
               and f["data"]["data"].get("c") == 0]
        self.assertEqual(len(ops), 1)
        self.assertEqual(len(ops[0]["v"]), 10)

    async def test_fire_sends_temp_intensity(self):
        client = SocketV4Client(events=StateEvents())
        client._handle_frame({"type": "hello", "clientId": "ctrl"})
        client._handle_frame({"type": "client_attached", "clientId": "app"})
        client._replace_devices("app", [{"slotId": "s1", "type": "COYOTE_030",
                                         "slotState": {"channelA": {"intensityMax": 100},
                                                       "channelB": {"intensityMax": 35}}}])
        sent: list[dict] = []

        async def fake_send(frame):
            sent.append(frame)

        client._send_raw = fake_send
        await client.fire(slot_id="s1", duration_s=1.0)
        ops = [f["data"]["data"] for f in sent if f["data"].get("m") == "device.op"]
        temps = [o for o in ops if o.get("t") == 4]
        self.assertEqual(len(temps), 2)
        self.assertEqual(temps[0]["v"], 100)
        self.assertEqual(temps[1]["v"], 35)
        self.assertEqual(temps[0]["d"], 1000)

    async def test_add_intensity_plain_ops(self):
        client = SocketV4Client(events=StateEvents())
        client._handle_frame({"type": "hello", "clientId": "ctrl"})
        client._handle_frame({"type": "client_attached", "clientId": "app"})
        client._replace_devices("app", [{"slotId": "s1", "type": "COYOTE_030"}])
        sent: list[dict] = []

        async def fake_send(frame):
            sent.append(frame)

        client._send_raw = fake_send
        await client.add_intensity("A", 2, slot_id="s1")
        op = [f["data"]["data"] for f in sent if f["data"].get("m") == "device.op"][-1]
        self.assertNotIn("im", op)
        self.assertNotIn("p", op)
        self.assertEqual(op["v"], 2)

    async def test_ovc_strength_quantized_to_10(self):
        client = SocketV4Client(events=StateEvents())
        client._handle_frame({"type": "hello", "clientId": "ctrl"})
        client._handle_frame({"type": "client_attached", "clientId": "app"})
        client._replace_devices("app", [{"slotId": "s1", "type": "OVC_1",
                                         "props": {"intensityA": 0}}])
        sent: list[dict] = []

        async def fake_send(frame):
            sent.append(frame)

        client._send_raw = fake_send
        await client.set_strength("A", 5, slot_id="s1")
        ops = [f["data"]["data"] for f in sent if f["data"].get("m") == "device.op"]
        self.assertEqual(ops[0]["t"], 3)
        self.assertEqual(ops[0]["v"], 10)

    async def test_replace_devices_merges_props(self):
        client = SocketV4Client(events=StateEvents())
        client._handle_frame({"type": "hello", "clientId": "ctrl"})
        client._handle_frame({"type": "client_attached", "clientId": "app"})
        client._replace_devices("app", [
            {"slotId": "s1", "type": "COYOTE_030",
             "props": {"power": 87, "intensityA": 9},
             "slotState": {"channelA": {"intensityMax": 100}}},
        ])
        self.assertEqual(client.state.slots["s1"].battery, 87)
        client._replace_devices("app", [{"slotId": "s1", "type": "COYOTE_030"}])
        self.assertEqual(client.state.slots["s1"].battery, 87)
        self.assertEqual(client.state.slots["s1"].strength["A"], 9)
        self.assertEqual(client.state.slots["s1"].strength_limit["A"], 100)

    async def test_set_strength_plain_relative_add(self):
        client = SocketV4Client(events=StateEvents())
        client._handle_frame({"type": "hello", "clientId": "ctrl"})
        client._handle_frame({"type": "client_attached", "clientId": "app"})
        client._replace_devices("app", [{"slotId": "s1", "type": "COYOTE_030",
                                         "props": {"intensityA": 0}}])
        sent: list[dict] = []

        async def fake_send(frame):
            sent.append(frame)

        client._send_raw = fake_send
        await client.set_strength("A", 5, slot_id="s1")
        ops = [f["data"]["data"] for f in sent if f["data"].get("m") == "device.op"]
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0]["t"], 3)
        self.assertEqual(ops[0]["v"], 5)
        self.assertNotIn("im", ops[0])
        self.assertNotIn("p", ops[0])
        self.assertEqual(client.state.slots["s1"].strength["A"], 0)

    async def test_set_strength_does_not_fight_app_limit(self):
        client = SocketV4Client(events=StateEvents())
        client._handle_frame({"type": "hello", "clientId": "ctrl"})
        client._handle_frame({"type": "client_attached", "clientId": "app"})
        client._replace_devices("app", [{"slotId": "s1", "type": "COYOTE_030",
                                         "props": {"intensityA": 120},
                                         "slotState": {"channelA": {"intensityMax": 103}}}])
        sent: list[dict] = []

        async def fake_send(frame):
            sent.append(frame)

        client._send_raw = fake_send
        await client.set_strength("A", 121, slot_id="s1")
        ops = [f["data"]["data"] for f in sent if f["data"].get("m") == "device.op"]
        self.assertEqual(ops[0]["t"], 3)
        self.assertEqual(ops[0]["v"], 1)

    async def test_output_ops_reject_bmtr_slot(self):
        client = SocketV4Client(events=StateEvents())
        client._handle_frame({"type": "hello", "clientId": "ctrl"})
        client._handle_frame({"type": "client_attached", "clientId": "app"})
        client._replace_devices("app", [
            {"slotId": "bm", "type": "BMTR_1"},
            {"slotId": "s1", "type": "COYOTE_030"},
        ])
        sent: list[dict] = []

        async def fake_send(frame):
            sent.append(frame)

        client._send_raw = fake_send
        with self.assertRaises(RuntimeError):
            await client.set_strength("A", 10, slot_id="bm")
        with self.assertRaises(RuntimeError):
            await client.set_wave("A", "continuous", slot_id="bm")
        with self.assertRaises(RuntimeError):
            await client.fire(slot_id="bm")
        await client._wave_tick()
        ops = [f["data"]["data"] for f in sent if f["data"].get("m") == "device.op"
               and f["data"]["data"].get("s") == "bm"]
        self.assertEqual(ops, [])

    async def test_single_sided_patches_keep_accumulated_state(self):
        client = SocketV4Client(events=StateEvents())
        client._handle_frame({"type": "hello", "clientId": "ctrl"})
        client._handle_frame({"type": "client_attached", "clientId": "app"})
        client._replace_devices("app", [
            {"slotId": "s1", "type": "COYOTE_030",
             "props": {"intensityA": 0, "intensityB": 0, "power": 77},
             "slotState": {"channelA": {"intensityMax": 100},
                           "channelB": {"intensityMax": 200}}},
        ])

        def patch(slots):
            client._handle_message_frame(
                {"clientId": "app", "data": {"t": "ev", "ev": "slots.patch",
                                             "slots": slots}})

        for v in range(1, 9):
            patch([{"slotId": "s1", "props": {"intensityA": v}}])
        slot = client.state.slots["s1"]
        self.assertEqual(slot.strength["A"], 8)
        self.assertEqual(slot.strength_limit["A"], 100)
        self.assertEqual(slot.battery, 77)

        patch([{"slotId": "s1", "slotState": {"channelA": {
            "comfortLimit": {"totalIncr": 1}, "intensityMax": 101}}}])
        slot = client.state.slots["s1"]
        self.assertEqual(slot.strength["A"], 8)
        self.assertEqual(slot.strength_limit["A"], 101)
        self.assertEqual(slot.battery, 77)

        patch([{"slotId": "s1", "props": {"intensityA": 9}}])
        slot = client.state.slots["s1"]
        self.assertEqual(slot.strength["A"], 9)
        self.assertEqual(slot.strength_limit["A"], 101)
        self.assertEqual(slot.battery, 77)


class BleFeatureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        FakeBleakClient.instances.clear()
        self.patcher = unittest.mock.patch("dglab.ble.BleakClient", FakeBleakClient)
        self.patcher.start()
        self.ble = _make_client()

    async def asyncTearDown(self):
        self.patcher.stop()

    async def test_fire_raises_then_restores(self):
        await self.ble.connect("addr-c", "coyote_v3")
        await self.ble.set_strength("A", 10, slot_id="addr-c")
        await self.ble.fire(slot_id="addr-c", duration_s=0.3, value=80)
        self.assertEqual(self.ble.state.slots["addr-c"].strength["A"], 80)
        await asyncio.sleep(0.45)
        self.assertEqual(self.ble.state.slots["addr-c"].strength["A"], 10)

    async def test_unexpected_drop_marks_for_reconnect(self):
        await self.ble.connect("addr-c", "coyote_v3")
        FakeBleakClient.instances["addr-c"].simulate_drop()
        self.assertNotIn("addr-c", self.ble.sessions)
        self.assertIn("addr-c", self.ble.dropped)

        await self.ble.connect("addr-c", "coyote_v3")
        await self.ble.disconnect("addr-c")
        self.assertNotIn("addr-c", self.ble.dropped)
        self.assertNotIn("addr-c", self.ble.sessions)

    async def test_led_and_flip_writes(self):
        await self.ble.connect("addr-ovc", "ovc")
        await self.ble.set_led("magenta", slot_id="addr-ovc")
        ovc_client = FakeBleakClient.instances["addr-ovc"]
        led_frames = [d for _c, d in ovc_client.written if d[0] == 0x50]
        self.assertEqual(led_frames[-1], bytes([0x50, LED_COLORS["magenta"], 0x01]))

        await self.ble.connect("addr-c", "coyote_v3")
        await self.ble.set_led("blue", slot_id="addr-c")
        coyote_client = FakeBleakClient.instances["addr-c"]
        coyote_led = [d for _c, d in coyote_client.written
                      if d[0] == 0x50 and len(d) == 17]
        self.assertEqual(coyote_led[-1],
                         bytes([0x50, LED_COLORS["blue"], 0x00]) + bytes(14))

        await self.ble.connect("addr-bmtr", "bmtr")
        await self.ble.bmtr_flip(slot_id="addr-bmtr")
        bmtr_client = FakeBleakClient.instances["addr-bmtr"]
        flips = [d for _c, d in bmtr_client.written if d[0] == 0x66]
        self.assertEqual(len(flips), 1)
        self.assertEqual(flips[0][10], 3)
        await self.ble.bmtr_flip(slot_id="addr-bmtr")
        flips = [d for _c, d in bmtr_client.written if d[0] == 0x66]
        self.assertEqual(flips[-1][10], 1)

    async def test_button_event_emitted(self):
        await self.ble.connect("addr-ovc", "ovc")
        events: list[tuple[str, int]] = []
        self.ble.events.on("ovc_button", lambda sid, bit: events.append((sid, bit)))
        session = self.ble.sessions["addr-ovc"]
        data = bytearray(16)
        data[0] = 0xD0
        data[2:4] = (1 << 13).to_bytes(2, "big")
        self.ble._on_notify(session, None, data)
        self.assertEqual(events, [("addr-ovc", 13)])
        release: list[tuple[str, int]] = []
        self.ble.events.on("ovc_button_up", lambda sid, bit: release.append((sid, bit)))
        data[2:4] = (0).to_bytes(2, "big")
        self.ble._on_notify(session, None, data)
        self.assertEqual(release, [("addr-ovc", 13)])

    async def test_bmtr_d0_stats_and_pressure(self):
        await self.ble.connect("addr-bmtr", "bmtr")
        session = self.ble.sessions["addr-bmtr"]
        data = bytearray(17)
        data[0] = 0xD0
        data[8:10] = (1234).to_bytes(2, "little")
        self.ble._on_notify(session, None, data)
        self.assertAlmostEqual(self.ble.state.slots["addr-bmtr"].pressure, 12.34)
        stats = session.__dict__["notify_stats"]
        uuid, entry = next(iter(stats.items()))
        self.assertEqual(entry[0], 1)
        self.assertEqual(entry[1], bytes(data))
        self.ble._log_d0_stats(session)
        self.assertEqual(stats[uuid][0], 0)


class OvcButtonBindingTests(unittest.IsolatedAsyncioTestCase):
    def _engine(self):
        import app as app_module

        engine = app_module.Engine()
        engine.start()
        return engine

    def _set_profile(self, engine, mapping: dict) -> None:
        ble = engine.config.setdefault("ble", {})
        ble["ovc_profiles"] = {"默认": dict(mapping)}
        ble["ovc_profile"] = "默认"

    async def test_binding_dispatches_fire_hold(self):
        engine = self._engine()
        try:
            started: list[tuple] = []
            stopped: list[tuple] = []

            async def fake_start(slot_id=None, channel=None):
                started.append((slot_id, channel))

            async def fake_stop(slot_id=None, channel=None):
                stopped.append((slot_id, channel))

            engine.fire_start = fake_start
            engine.fire_stop = fake_stop
            self._set_profile(engine, {"13": "fire", "12": "fire_a", "11": "fire_b"})
            engine._on_ovc_button("addr-ovc", 13)
            engine._on_ovc_button("addr-ovc", 12)
            engine._on_ovc_button("addr-ovc", 11)
            await asyncio.sleep(0.3)
            self.assertEqual(started, [("addr-ovc", None), ("addr-ovc", "A"),
                                       ("addr-ovc", "B")])
            engine._on_ovc_button_up("addr-ovc", 13)
            engine._on_ovc_button_up("addr-ovc", 12)
            engine._on_ovc_button_up("addr-ovc", 11)
            await asyncio.sleep(0.3)
            self.assertEqual(stopped, [("addr-ovc", None), ("addr-ovc", "A"),
                                       ("addr-ovc", "B")])
        finally:
            engine.stop()

    async def test_binding_dispatches_keyboard(self):
        import app as app_module

        engine = self._engine()
        try:
            pressed: list[str] = []
            released: list[str] = []
            original_press = app_module.keyboard_keys.press
            original_release = app_module.keyboard_keys.release
            app_module.keyboard_keys.press = lambda name: pressed.append(name)
            app_module.keyboard_keys.release = lambda name: released.append(name)
            try:
                self._set_profile(engine, {"13": "key:F1"})
                engine._on_ovc_button("addr-ovc", 13)
                engine._on_ovc_button_up("addr-ovc", 13)
                await asyncio.sleep(0.2)
                self.assertEqual(pressed, ["F1"])
                self.assertEqual(released, ["F1"])
            finally:
                app_module.keyboard_keys.press = original_press
                app_module.keyboard_keys.release = original_release
        finally:
            engine.stop()

    async def test_profile_migration_and_switch(self):
        engine = self._engine()
        try:
            ble = engine.config["ble"]
            self.assertIn("默认", ble["ovc_profiles"])
            self.assertEqual(ble["ovc_profile"], "默认")
            legacy = ble["ovc_profiles"]["默认"]
            self.assertEqual(engine.ovc_bindings().get("13"), legacy.get("13"))
            ble["ovc_profiles"]["配置2"] = {"13": "estop"}
            ble["ovc_profile"] = "配置2"
            self.assertEqual(engine.ovc_bindings()["13"], "estop")
            ble["ovc_profile"] = "不存在的配置"
            self.assertEqual(engine.ovc_bindings().get("13"),
                             ble["ovc_profiles"]["默认"].get("13"))
        finally:
            engine.stop()


class KeyboardKeysTests(unittest.TestCase):
    def test_vk_roundtrip(self):
        from dglab import keys

        for name in ("F1", "A", "0", "Space", "Left", "Num5", "Shift", "Quote"):
            vk = keys.vk_from_name(name)
            self.assertIsNotNone(vk, name)
            self.assertEqual(keys.key_name(vk), name)
        self.assertEqual(keys.vk_from_name("VK7B"), 0x7B)
        self.assertEqual(keys.key_name(0x7B), "F12")
        self.assertIsNone(keys.vk_from_name(""))
        self.assertIsNone(keys.vk_from_name("不是按键"))


class EngineCommandTests(unittest.IsolatedAsyncioTestCase):
    def _engine(self):
        import os
        import tempfile

        import app as app_module

        cfg_path = os.path.join(tempfile.gettempdir(), "dglab_test_engine_cfg.json")
        if os.path.exists(cfg_path):
            os.remove(cfg_path)
        engine = app_module.Engine(config_path=cfg_path)
        engine.start()
        return engine

    async def test_fire_reaches_v4_backend(self):
        engine = self._engine()
        try:
            calls: list[tuple] = []

            class FakeV4(SocketV4Client):
                async def fire(self, slot_id=None, duration_s=1.0, value=None,
                               channels=None):
                    calls.append((slot_id, duration_s, value, channels))

            backend = FakeV4(events=StateEvents())
            backend._replace_devices("app", [{"slotId": "s1", "type": "COYOTE_030",
                                              "slotState": {"channelA": {"intensityMax": 200},
                                                            "channelB": {"intensityMax": 200}}}])
            engine._backend = backend
            await engine.fire(slot_id="s1")
            self.assertEqual(calls, [("s1", 1.0, {"A": 100, "B": 100}, ("A", "B"))])

            calls.clear()
            await engine.fire(slot_id="s1", channel="A")
            self.assertEqual(calls, [("s1", 1.0, {"A": 100}, ("A",))])
        finally:
            engine.stop()

    async def test_fire_restores_original_wave(self):
        engine = self._engine()
        try:
            waves: list[str] = []

            class FakeV4(SocketV4Client):
                async def fire(self, slot_id=None, duration_s=1.0, value=None,
                               channels=None):
                    pass

                async def set_wave(self, channel, waveform, duration_s=10.0, slot_id=None):
                    waves.append(waveform)

            backend = FakeV4(events=StateEvents())
            backend._replace_devices("app", [{"slotId": "s1", "type": "COYOTE_030",
                                              "props": {"intensityA": 0, "intensityB": 0}}])
            engine._backend = backend
            engine._selected_wave["A"] = SILENT
            engine._selected_wave["B"] = "some_wave"
            await engine.fire(slot_id="s1", duration_s=0.05)
            self.assertEqual(waves[0], CONTINUOUS)
            self.assertEqual(waves[-1], SILENT)
            self.assertEqual(engine._selected_wave["A"], SILENT)
            self.assertNotIn(CONTINUOUS, waves[1:])

            waves.clear()
            engine._selected_wave["A"] = SILENT
            engine._selected_wave["B"] = SILENT
            await engine.fire(slot_id="s1", duration_s=0.05, channel="B")
            self.assertEqual(waves, [CONTINUOUS, SILENT])
            self.assertEqual(engine._selected_wave["A"], SILENT)
            self.assertEqual(engine._selected_wave["B"], SILENT)
        finally:
            engine.stop()

    async def test_fire_uses_configured_strength(self):
        engine = self._engine()
        try:
            seen: list = []

            class FakeV4(SocketV4Client):
                async def fire(self, slot_id=None, duration_s=1.0, value=None,
                               channels=None):
                    seen.append(value)

            backend = FakeV4(events=StateEvents())
            backend._replace_devices("app", [{"slotId": "s1", "type": "COYOTE_030",
                                              "slotState": {"channelA": {"intensityMax": 200},
                                                            "channelB": {"intensityMax": 200}}}])
            engine._backend = backend

            engine.config["fire_strength"] = 0
            await engine.fire(slot_id="s1", duration_s=0.01)
            self.assertEqual(seen[-1], {"A": 100, "B": 100})

            engine.config["fire_strength"] = 45
            await engine.fire(slot_id="s1", duration_s=0.01)
            self.assertEqual(seen[-1], {"A": 45, "B": 45})

            backend._replace_devices("app", [{"slotId": "s1", "type": "COYOTE_030",
                                              "slotState": {"channelA": {"intensityMax": 30},
                                                            "channelB": {"intensityMax": 30}}}])
            engine.config["fire_strength"] = 150
            await engine.fire(slot_id="s1", duration_s=0.01)
            self.assertEqual(seen[-1], {"A": 30, "B": 30})
        finally:
            engine.stop()

    async def test_fire_channel_strengths_separate(self):
        """通道分离：A/B 开火强度独立取值（0 = 跟随上限），互不影响。"""
        engine = self._engine()
        try:
            seen: list = []

            class FakeV4(SocketV4Client):
                async def fire(self, slot_id=None, duration_s=1.0, value=None,
                               channels=None):
                    seen.append((value, channels))

            backend = FakeV4(events=StateEvents())
            backend._replace_devices("app", [{"slotId": "s1", "type": "COYOTE_030",
                                              "slotState": {"channelA": {"intensityMax": 200},
                                                            "channelB": {"intensityMax": 120}}}])
            engine._backend = backend
            engine.config["fire_strength_a"] = 40
            engine.config["fire_strength_b"] = 0  # 0 = 跟随最大强度上限 (100)
            await engine.fire(slot_id="s1", duration_s=0.01)
            self.assertEqual(seen[-1], ({"A": 40, "B": 100}, ("A", "B")))

            # 旧版双通道 fire_strength 仍作两通道的兜底
            engine.config["fire_strength_a"] = 0
            engine.config["fire_strength_b"] = 0
            engine.config["fire_strength"] = 55
            await engine.fire(slot_id="s1", duration_s=0.01)
            self.assertEqual(seen[-1], ({"A": 55, "B": 55}, ("A", "B")))

            # 设备级覆盖优先于全局
            engine.config.setdefault("device_settings", {})["s1"] = {
                "fire_strength_a": 66}
            await engine.fire(slot_id="s1", duration_s=0.01)
            self.assertEqual(seen[-1], ({"A": 66, "B": 55}, ("A", "B")))

            # 每通道各自钳制到本通道上限（A 上限 200 / B 上限 120）
            engine.config["device_settings"]["s1"] = {
                "fire_strength_a": 0, "fire_strength_b": 150}
            await engine.fire(slot_id="s1", duration_s=0.01)
            self.assertEqual(seen[-1], ({"A": 100, "B": 120}, ("A", "B")))

            # intensity_params 公开快照同时给出 A/B 与遗留键（0 = 跟随上限哨兵保留）
            params = engine.intensity_params("s1")
            self.assertEqual(params["fire_strength_a"], 0)
            self.assertEqual(params["fire_strength_b"], 150)
            self.assertIn("fire_strength", params)
        finally:
            engine.stop()

    async def test_fire_hold_start_stop(self):
        engine = self._engine()
        try:
            added: list[tuple] = []
            waves: list[str] = []

            class FakeV4(SocketV4Client):
                async def add_intensity(self, channel, value, slot_id=None):
                    added.append((channel, value))

                async def set_wave(self, channel, waveform, duration_s=10.0, slot_id=None):
                    waves.append(waveform)

            backend = FakeV4(events=StateEvents())
            backend._replace_devices("app", [{"slotId": "s1", "type": "COYOTE_030",
                                              "props": {"intensityA": 3, "intensityB": 0},
                                              "slotState": {"channelA": {"intensityMax": 200},
                                                            "channelB": {"intensityMax": 200}}}])
            engine._backend = backend
            engine.config["fire_strength"] = 20
            engine._selected_wave["A"] = SILENT
            engine._selected_wave["B"] = SILENT

            await engine.fire_start(slot_id="s1")
            self.assertIn(("s1", "A"), engine._fire_holds)
            self.assertIn(("s1", "B"), engine._fire_holds)
            self.assertEqual(sorted(added), [("A", 17), ("B", 20)])
            self.assertEqual(waves[0], CONTINUOUS)

            added.clear()
            backend.state.slots["s1"].strength = {"A": 20, "B": 20}
            await engine.fire_stop(slot_id="s1")
            self.assertNotIn(("s1", "A"), engine._fire_holds)
            self.assertNotIn(("s1", "B"), engine._fire_holds)
            self.assertEqual(sorted(added), [("A", -17), ("B", -20)])
            self.assertEqual(waves[-1], SILENT)
            self.assertEqual(engine._selected_wave["A"], SILENT)

            added.clear()
            await engine.fire_stop(slot_id="s1")
            self.assertEqual(added, [])
        finally:
            engine.stop()

    async def test_fire_hold_per_channel(self):
        """通道分离：只开火 A 通道时 B 通道强度与波形都不受影响。"""
        engine = self._engine()
        try:
            added: list[tuple] = []
            waves: list[str] = []

            class FakeV4(SocketV4Client):
                async def add_intensity(self, channel, value, slot_id=None):
                    added.append((channel, value))

                async def set_wave(self, channel, waveform, duration_s=10.0, slot_id=None):
                    waves.append((channel, waveform))

            backend = FakeV4(events=StateEvents())
            backend._replace_devices("app", [{"slotId": "s1", "type": "COYOTE_030",
                                              "props": {"intensityA": 3, "intensityB": 7},
                                              "slotState": {"channelA": {"intensityMax": 200},
                                                            "channelB": {"intensityMax": 200}}}])
            engine._backend = backend
            engine.config["fire_strength"] = 20
            engine.config["fire_strength_a"] = 50
            engine._selected_wave["A"] = SILENT
            engine._selected_wave["B"] = "some_wave"

            await engine.fire_start(slot_id="s1", channel="A")
            self.assertIn(("s1", "A"), engine._fire_holds)
            self.assertNotIn(("s1", "B"), engine._fire_holds)
            self.assertEqual(added, [("A", 47)])
            self.assertEqual(waves, [("A", CONTINUOUS)])
            self.assertEqual(engine._selected_wave["B"], "some_wave")

            added.clear()
            backend.state.slots["s1"].strength = {"A": 50, "B": 7}
            await engine.fire_stop(slot_id="s1", channel="A")
            self.assertEqual(added, [("A", -47)])
            self.assertEqual(waves[-1], ("A", SILENT))
            self.assertEqual(engine._selected_wave["A"], SILENT)
            self.assertEqual(engine._selected_wave["B"], "some_wave")
        finally:
            engine.stop()

    async def test_fire_hold_restores_when_app_never_reports(self):
        engine = self._engine()
        try:
            added: list[tuple] = []

            class FakeV4(SocketV4Client):
                async def add_intensity(self, channel, value, slot_id=None):
                    added.append((channel, value))

                async def set_wave(self, channel, waveform, duration_s=10.0, slot_id=None):
                    pass

            backend = FakeV4(events=StateEvents())
            backend._replace_devices("app", [{"slotId": "s1", "type": "COYOTE_030",
                                              "props": {"intensityA": 4, "intensityB": 0},
                                              "slotState": {"channelA": {"intensityMax": 200},
                                                            "channelB": {"intensityMax": 200}}}])
            engine._backend = backend
            engine.config["fire_strength"] = 50
            engine._selected_wave["A"] = SILENT
            engine._selected_wave["B"] = SILENT

            await engine.fire_start(slot_id="s1")
            self.assertEqual(sorted(added), [("A", 46), ("B", 50)])

            added.clear()
            await engine.fire_stop(slot_id="s1")
            self.assertEqual(sorted(added), [("A", -46), ("B", -50)])
        finally:
            engine.stop()

    async def test_direct_strength_set(self):
        engine = self._engine()
        try:
            ops: list[tuple] = []

            class FakeV4(SocketV4Client):
                async def add_intensity(self, channel, value, slot_id=None):
                    ops.append((channel, value))

            backend = FakeV4(events=StateEvents())
            backend._replace_devices("app", [{"slotId": "s1", "type": "COYOTE_030",
                                              "props": {"intensityA": 6, "intensityB": 0}}])
            engine._backend = backend
            engine.config["max_strength"] = 100
            await engine.set_strength("A", 40, slot_id="s1")
            self.assertEqual(ops, [("A", 34)])
            ops.clear()
            await engine.set_strength("A", 500, slot_id="s1")
            self.assertEqual(ops, [("A", 94)])
        finally:
            engine.stop()


    async def test_reset_strength_switches_to_silent(self):
        engine = self._engine()
        try:
            waves: list[tuple] = []
            resets: list[tuple] = []

            class FakeV4(SocketV4Client):
                async def reset_intensity(self, channel=None, slot_id=None):
                    resets.append((channel, slot_id))

                async def set_wave(self, channel, waveform, duration_s=10.0, slot_id=None):
                    waves.append((channel, waveform, slot_id))

            backend = FakeV4(events=StateEvents())
            backend._replace_devices("app", [{"slotId": "s1", "type": "COYOTE_030"}])
            engine._backend = backend
            engine._selected_wave["A"] = "pulse"
            await engine.reset_strength("A", slot_id="s1")
            self.assertEqual(resets, [("A", "s1")])
            self.assertEqual(waves, [("A", SILENT, "s1")])
            self.assertEqual(engine._selected_wave["A"], SILENT)
        finally:
            engine.stop()

    async def test_add_strength_sends_pure_relative(self):
        engine = self._engine()
        try:
            adds: list[tuple] = []

            class FakeV4(SocketV4Client):
                async def add_intensity(self, channel, value, slot_id=None):
                    adds.append((channel, value, slot_id))

            backend = FakeV4(events=StateEvents())
            backend._replace_devices("app", [{"slotId": "s1", "type": "COYOTE_030",
                                              "props": {"intensityA": 5}}])
            engine._backend = backend
            await engine.add_strength("A", 1, slot_id="s1")
            self.assertEqual(adds, [("A", 1, "s1")])
            self.assertEqual(backend.state.slots["s1"].strength["A"], 5)

            backend.state.slots["s1"].strength["A"] = engine.config["max_strength"]
            await engine.add_strength("A", 1, slot_id="s1")
            self.assertEqual(len(adds), 1)
        finally:
            engine.stop()

    async def test_resolve_slot_skips_bmtr_for_output(self):
        engine = self._engine()
        try:
            backend = SocketV4Client(events=StateEvents())
            backend._replace_devices("app", [
                {"slotId": "bm", "type": "BMTR_1"},
                {"slotId": "s1", "type": "COYOTE_030"},
            ])
            engine._backend = backend
            self.assertEqual(engine.resolve_slot(output_only=True), "s1")
            self.assertEqual(engine.resolve_slot(), "bm")
        finally:
            engine.stop()


class ResetStrengthTests(unittest.IsolatedAsyncioTestCase):
    async def test_reset_strength_v4(self):
        client = SocketV4Client(events=StateEvents())
        client._handle_frame({"type": "hello", "clientId": "ctrl"})
        client._handle_frame({"type": "client_attached", "clientId": "app"})
        client._replace_devices("app", [{"slotId": "s1", "type": "COYOTE_030"}])
        sent: list[dict] = []

        async def fake_send(frame):
            sent.append(frame)

        client._send_raw = fake_send
        await client.reset_intensity("A", slot_id="s1")
        ops = [f["data"]["data"] for f in sent if f["data"].get("m") == "device.op"]
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0]["t"], 7)
        self.assertEqual(ops[0]["s"], "s1")
        self.assertEqual(ops[0]["c"], 0)

    async def test_reset_strength_ble(self):
        FakeBleakClient.instances.clear()
        with unittest.mock.patch("dglab.ble.BleakClient", FakeBleakClient):
            ble = _make_client()
            await ble.connect("addr-c", "coyote_v3")
            await ble.set_strength("A", 30, slot_id="addr-c")
            await asyncio.sleep(0.15)
            await ble.set_strength("A", 0, slot_id="addr-c")
            self.assertEqual(ble.sessions["addr-c"]._targets["A"], 0)
            await asyncio.sleep(0.15)
            client = FakeBleakClient.instances["addr-c"]
            applied = [d for _c, d in client.written
                       if d[0] == 0xB0 and (d[1] & 0x0F) == 0b1100]
            self.assertTrue(applied)
            self.assertEqual(applied[-1][2], 0)


class SavedDeviceTests(unittest.IsolatedAsyncioTestCase):
    async def test_remember_reconnect_forget(self):
        import os
        import tempfile

        import app as app_module

        cfg_path = os.path.join(tempfile.gettempdir(), "dglab_test_cfg.json")
        if os.path.exists(cfg_path):
            os.remove(cfg_path)
        FakeBleakClient.instances.clear()
        engine = app_module.Engine(config_path=cfg_path)
        engine.start()
        try:
            with unittest.mock.patch("dglab.ble.BleakClient", FakeBleakClient):
                await engine.ble_connect("addr-c", "coyote_v3")
                saved = engine.saved_device_list()
                self.assertEqual(len(saved), 1)
                self.assertEqual(saved[0]["address"], "addr-c")
                self.assertEqual(saved[0]["kind"], "coyote_v3")

                await engine._backend.disconnect("addr-c")
                await engine.ble_reconnect_saved("addr-c")
                self.assertIn("addr-c", engine._backend.sessions)

                engine.forget_device("addr-c")
                self.assertEqual(engine.saved_device_list(), [])
        finally:
            engine.stop()


class FrameLogTests(unittest.IsolatedAsyncioTestCase):
    async def test_v4_no_frame_log_but_error_reported(self):
        # 帧级日志已移除(高频写盘卡顿);指令错误仍以运行日志提示
        client = SocketV4Client(events=StateEvents())
        logs: list[str] = []
        client.events.on("log", logs.append)
        client._handle_frame({"type": "hello", "clientId": "ctrl"})
        client._handle_frame({"type": "message",
                              "data": {"t": "resp", "strength": 20}})
        self.assertEqual(logs, [])            # 正常帧不产生任何日志
        client._handle_frame({"type": "message",
                              "data": {"t": "resp", "error": "bad req",
                                       "reqId": 7}})
        self.assertEqual(len(logs), 1)
        self.assertIn("bad req", logs[0])


class WaveMonitorThreadTests(unittest.TestCase):
    def test_window_during_record_never_raises(self):
        import threading
        import time as _time

        from dglab.monitor import WaveMonitor

        monitor = WaveMonitor()
        stop = threading.Event()
        errors: list[str] = []

        def writer():
            i = 0
            while not stop.is_set():
                monitor.record((i % 100,) * 4, (i % 50,) * 4)
                i += 1

        def reader():
            while not stop.is_set():
                try:
                    monitor.window(5.0)
                    monitor.last_strength()
                except Exception as exc:
                    errors.append(repr(exc))
                _time.sleep(0.001)

        threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
        for t in threads:
            t.start()
        _time.sleep(0.5)
        stop.set()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])


class LogBufferMirrorTests(unittest.TestCase):
    def test_ui_messages_mirror_engine_messages_do_not(self):
        from ui.live import LogBuffer

        buffer = LogBuffer()
        mirrored: list[str] = []
        buffer.mirror = mirrored.append
        buffer.append("ui-msg")
        buffer.append("engine-msg", from_engine=True)
        self.assertEqual(mirrored, ["ui-msg"])
        self.assertEqual(buffer.snapshot()[-1][2], "engine-msg")


class OvcBindingActionTableTests(unittest.IsolatedAsyncioTestCase):
    def test_button_actions_match_engine(self):
        import app as app_module

        from ui import live

        self.assertEqual(tuple(k for k, _ in live.BUTTON_ACTIONS),
                         app_module.Engine._OVC_BUTTON_ACTIONS)
        self.assertEqual(live.button_actions(engine=None)[-1][0], "key")


    async def test_binding_strength_single_channel(self):
        import app as app_module

        engine = app_module.Engine()
        engine.start()
        try:
            calls: list[tuple] = []

            async def fake_add(channel, delta, slot_id=None):
                calls.append((channel, delta, slot_id))

            engine.add_strength = fake_add
            bindings = (engine.config.setdefault("ble", {})
                        .setdefault("ovc_profiles", {}).setdefault("默认", {}))
            engine.config["ble"]["ovc_profile"] = "默认"
            bindings["13"] = "a_strength_up"
            engine._on_ovc_button("addr-ovc", 13)
            bindings["13"] = "b_strength_down"
            engine._on_ovc_button("addr-ovc", 13)
            await asyncio.sleep(0.3)
            self.assertEqual(calls, [("A", 1, "addr-ovc"), ("B", -1, "addr-ovc")])
        finally:
            engine.stop()

    async def test_binding_osc_pass_through(self):
        import app as app_module
        from plugins import ButtonAction

        engine = app_module.Engine()
        engine.start()
        try:
            sent: list[tuple[str, int]] = []

            class FakeOsc:
                _running = True

                def __init__(self, sink):
                    self.sink = sink

                def send_value(self, address, value):
                    self.sink.append((address, value))

            class FakeModule:
                id = "osc_bridge"

                def __init__(self, sink):
                    self.bridge = FakeOsc(sink)

                def is_running(self):
                    return True

                def button_actions(self):
                    return [ButtonAction(
                        "osc", "发送 OSC 参数…",
                        on_press=lambda slot, arg: self.bridge.send_value(arg, 1),
                        on_release=lambda slot, arg: self.bridge.send_value(arg, 0))]

            engine.modules.register_instance("osc_bridge", FakeModule(sent))
            bindings = (engine.config.setdefault("ble", {})
                        .setdefault("ovc_profiles", {}).setdefault("默认", {}))
            engine.config["ble"]["ovc_profile"] = "默认"
            bindings["13"] = "osc:/avatar/parameters/Trigger"
            engine._on_ovc_button("addr-ovc", 13)
            engine._on_ovc_button_up("addr-ovc", 13)
            self.assertEqual(sent, [("/avatar/parameters/Trigger", 1),
                                    ("/avatar/parameters/Trigger", 0)])
        finally:
            engine.stop()

    async def test_binding_wave_single_channel(self):
        import app as app_module

        engine = app_module.Engine()
        engine.start()
        try:
            waves: list[tuple] = []

            async def fake_set_wave(channel, name, slot_id=None):
                waves.append((channel, name))

            engine.set_wave = fake_set_wave
            import types
            from dglab.state import EngineState, Slot
            state = EngineState()
            state.slots["addr-ovc"] = Slot(slot_id="addr-ovc", name="负鼠",
                                           type="OVC_1")
            engine._backend = types.SimpleNamespace(state=state)
            bindings = (engine.config.setdefault("ble", {})
                        .setdefault("ovc_profiles", {}).setdefault("默认", {}))
            engine.config["ble"]["ovc_profile"] = "默认"
            bindings["13"] = "a_wave_up"
            engine._on_ovc_button("addr-ovc", 13)
            bindings["13"] = "b_wave_down"
            engine._on_ovc_button("addr-ovc", 13)
            await asyncio.sleep(0.3)
            from dglab.waves import wave_order
            self.assertEqual(waves, [("A", CONTINUOUS),
                                     ("B", wave_order("OVC")[-1])])
        finally:
            engine.stop()


class DeviceSettingTests(unittest.IsolatedAsyncioTestCase):
    def _engine(self):
        import os
        import tempfile

        import app as app_module

        cfg_path = os.path.join(tempfile.gettempdir(), "dglab_test_engine_cfg.json")
        if os.path.exists(cfg_path):
            os.remove(cfg_path)
        engine = app_module.Engine(config_path=cfg_path)
        engine.start()
        return engine

    async def test_device_setting_falls_back_to_global(self):
        engine = self._engine()
        try:
            self.assertEqual(engine.device_setting("s1", "max_strength"), 100)
            engine.config.setdefault("device_settings", {})["s1"] = {"max_strength": 50}
            self.assertEqual(engine.device_setting("s1", "max_strength"), 50)
            self.assertEqual(engine.device_setting("s2", "max_strength"), 100)
            self.assertEqual(engine.device_setting("s1", "fire_strength"), 0)
        finally:
            engine.stop()

    async def test_set_strength_clamps_to_device_limit(self):
        engine = self._engine()
        try:
            calls: list[tuple] = []

            class FakeV4(SocketV4Client):
                async def set_strength(self, channel, value, slot_id=None):
                    calls.append((slot_id, channel, value))

            backend = FakeV4(events=StateEvents())
            backend._replace_devices("app", [{"slotId": "s1", "type": "COYOTE_030"}])
            engine._backend = backend
            engine.config.setdefault("device_settings", {})["s1"] = {"max_strength": 42}
            await engine.set_strength("A", 100, slot_id="s1")
            self.assertEqual(calls, [("s1", "A", 42)])
        finally:
            engine.stop()

    async def test_fire_uses_device_fire_strength(self):
        engine = self._engine()
        try:
            seen: list = []

            class FakeV4(SocketV4Client):
                async def fire(self, slot_id=None, duration_s=1.0, value=None,
                               channels=None):
                    seen.append(value)

            backend = FakeV4(events=StateEvents())
            backend._replace_devices("app", [{"slotId": "s1", "type": "COYOTE_030",
                                              "slotState": {"channelA": {"intensityMax": 200},
                                                            "channelB": {"intensityMax": 200}}}])
            engine._backend = backend
            engine.config.setdefault("device_settings", {})["s1"] = {"fire_strength": 33}
            await engine.fire(slot_id="s1", duration_s=0.01)
            self.assertEqual(seen[-1], {"A": 33, "B": 33})

            engine.config["device_settings"]["s1"] = {
                "fire_strength": 33, "fire_strength_a": 12, "fire_strength_b": 44}
            await engine.fire(slot_id="s1", duration_s=0.01, channel="B")
            self.assertEqual(seen[-1], {"B": 44})
        finally:
            engine.stop()

    async def test_emergency_stop_zeros_and_resets_wave(self):
        engine = self._engine()
        try:
            waves: list[tuple] = []

            class FakeV4(SocketV4Client):
                async def emergency_stop(self):
                    pass

                async def set_strength(self, channel, value, slot_id=None):
                    pass

                async def set_wave(self, channel, waveform, duration_s=10.0, slot_id=None):
                    waves.append((slot_id, channel, waveform))

            backend = FakeV4(events=StateEvents())
            backend._replace_devices("app", [
                {"slotId": "s1", "type": "COYOTE_030",
                 "props": {"intensityA": 50, "intensityB": 40}},
                {"slotId": "bm", "type": "BMTR_1"},
            ])
            engine._backend = backend
            engine._selected_wave["A"] = "wave_x"
            await engine.emergency_stop()
            silent = [w for w in waves if w[2] == SILENT]
            self.assertEqual({w[0] for w in silent}, {"s1"})
            self.assertEqual(len(silent), 2)
            self.assertEqual(engine._selected_wave["A"], SILENT)
            self.assertEqual(engine._selected_wave["B"], SILENT)
        finally:
            engine.stop()

    async def test_frame_logging_removed(self):
        # log_frame 接口与 frame_log 事件不复存在(用户指示:不记录帧信息)
        engine = self._engine()
        try:
            self.assertFalse(hasattr(engine, "log_frame"))
            self.assertEqual(engine.config.get("log_frames"), None)
        finally:
            engine.stop()

    async def test_ble_disconnect_device_targets_slot(self):
        from dglab.ble import BleClient

        engine = self._engine()
        try:
            calls: list[str | None] = []

            class FakeBle(BleClient):
                async def disconnect(self, slot_id=None):
                    calls.append(slot_id)

            engine._backend = FakeBle(events=StateEvents())
            await engine.ble_disconnect_device("addr-1")
            self.assertEqual(calls, ["addr-1"])
        finally:
            engine.stop()

    async def test_set_file_logging_toggles_handler(self):
        import logging as _logging

        engine = self._engine()
        try:
            logger = engine._file_log
            self.assertTrue(any(isinstance(h, _logging.FileHandler)
                                for h in logger.handlers))
            engine.set_file_logging(False)
            self.assertFalse(any(isinstance(h, _logging.FileHandler)
                                 for h in logger.handlers))
            self.assertFalse(engine.config["log_to_file"])
            engine.set_file_logging(True)
            self.assertTrue(any(isinstance(h, _logging.FileHandler)
                                for h in logger.handlers))
        finally:
            engine.stop()

    async def test_step_device_wave_cycles_order(self):
        engine = self._engine()
        try:
            waves: list[tuple] = []

            class FakeV4(SocketV4Client):
                async def set_wave(self, channel, waveform, duration_s=10.0, slot_id=None):
                    waves.append((channel, waveform))

            backend = FakeV4(events=StateEvents())
            backend._replace_devices("app", [{"slotId": "s1", "type": "COYOTE_030"}])
            engine._backend = backend
            engine._selected_wave["A"] = SILENT
            engine._selected_wave["B"] = SILENT
            await engine._step_device_wave("s1", "A", +1)
            self.assertEqual(waves, [("A", CONTINUOUS)])
            await engine._step_device_wave("s1", "B", -1)
            from dglab.waves import wave_order
            self.assertEqual(waves[-1], ("B", wave_order("COYOTE")[-1]))
            await engine._step_device_wave("s1", "A", -1)
            self.assertEqual(waves[-1], ("A", SILENT))
        finally:
            engine.stop()


if __name__ == "__main__":
    unittest.main()

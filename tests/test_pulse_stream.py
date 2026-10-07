"""外部脉冲流波形：常量 / 帧生成 / 三后端接收 / 引擎推流门控。

联动模块每 0.1s 经 ``ctx.push_pulse_stream`` 推入一次频率数据，核心把
「逻辑频率 + 电平」转成 100ms 脉冲帧追加到设备播放队列，替代内置波形
发生器；波形选择中出现「外部脉冲流 (PULSE_STREAM)」选项。
"""
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
        # 其余特殊波形不受影响
        self.assertTrue(resolve_wave_frames(SILENT))
        self.assertTrue(resolve_wave_frames(CONTINUOUS))

    def test_wave_order_appends_pulse_stream(self):
        for family, last_builtin in (("COYOTE", "TEASE_2"), ("OVC", None)):
            order = wave_order(family)
            # 追加在末尾：内置波形序号保持稳定
            self.assertEqual(order[-1], PULSE_STREAM)
            self.assertEqual(order.index(PULSE_STREAM), len(order) - 1)
            if last_builtin is not None:
                self.assertEqual(order[-2], last_builtin)
            self.assertEqual(len(order),
                             len([w for w in order if w != PULSE_STREAM]) + 1)

    def test_pulse_frame_builds_and_clamps(self):
        # 逻辑频率 440 → wire 168 (0xa8)
        self.assertEqual(pulse_frame(440), build_frame([168] * 4, [100] * 4))
        # 频率钳制到 10-1000、电平钳制到 0-100
        self.assertEqual(pulse_frame(0)[:2], "0a")
        self.assertEqual(pulse_frame(99999)[:2], "f0")
        self.assertEqual(pulse_frame(100, level=250)[8:], "64646464")
        self.assertEqual(pulse_frame(100, level=-1)[8:], "00000000")

    def test_vibration_frame_square_wave(self):
        """负鼠振动帧：频率合成进振幅方波（图案高低变化，相位跨帧连续）。"""
        from dglab.waves import pulse_frame_vibration
        # 1000 逻辑频率 → 速率 10Hz（周期 100ms = 4 段）：帧内先通后断
        f1 = pulse_frame_vibration(1000, 80, t_start=0.0)
        raw = bytes.fromhex(f1)
        self.assertEqual(raw[4:8], bytearray([80, 80, 0, 0]))
        # 相位跨帧连续：从 0.05s 起的帧取到方波后半（断相转通相）
        f2 = pulse_frame_vibration(1000, 80, t_start=0.05)
        raw2 = bytes.fromhex(f2)
        self.assertEqual(raw2[4:8], bytearray([0, 0, 80, 80]))
        # 电平钳制 + 频率字节保留（携带逻辑频率）
        self.assertEqual(bytes.fromhex(pulse_frame_vibration(100, 300, 0.0))[4:6],
                         bytearray([100, 100]))

    def test_engine_builds_family_aware_frames(self):
        """引擎按设备家族构建脉冲帧：负鼠走振动方波，郊狼走载波帧。"""
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
        # 相位取绝对时间（跨帧连续），段电平必为 通相电平/断相 0 之一
        self.assertTrue(all(s in (80, 0) for s in raw[4:8]))
        self.assertIn(len(set(raw[4:8])), (1, 2))               # 方波两态
        self.assertEqual(raw[0], 0xf0)                          # 1000 逻辑 → wire 240

        # 郊狼设备：载波帧（四段同电平）
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

        # 最新帧替换语义：连续推流不积压，播放列表始终只有最新一帧
        await self.ble.push_pulse_frame("addr-1", "A", pulse_frame(100))
        await self.ble.push_pulse_frame("addr-1", "A", pulse_frame(200))
        self.assertEqual(cycle.frames, [pulse_frame(200)])
        self.assertEqual(cycle.next_frame(), pulse_frame(200))

    async def test_push_stream_tracks_latest_under_load(self):
        """回归：同速推流 10 秒（100 帧）后播放的仍是最新频率（不积压）。"""
        await self.ble.push_pulse_frame("addr-1", "A", pulse_frame(100))
        cycle = self.ble.sessions["addr-1"]._cycles["A"]
        for i in range(100):
            freq = 100 + (i // 10) * 100          # 每 10 帧跳一档频率（至 1000）
            await self.ble.push_pulse_frame("addr-1", "A", pulse_frame(freq))
        self.assertEqual(cycle.frames, [pulse_frame(1000)])
        # 播放循环接下来的每一帧都是最新频率
        for _ in range(5):
            self.assertEqual(cycle.next_frame(), pulse_frame(1000))

    async def test_push_unknown_device_or_bmtr_ignored(self):
        await self.ble.push_pulse_frame("nope", "A", pulse_frame(100))
        await self.ble.connect("addr-bmtr", "bmtr")
        await self.ble.push_pulse_frame("addr-bmtr", "A", pulse_frame(100))
        # 灵猫无输出：循环保持空（未追加任何帧）
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
        # 队列为空时波形循环不下发静默帧（等待推流）
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

        # 后续推流替换播放列表，波形循环补批取到的始终是最新频率（非 immediate）
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
        # 越过补帧提前量后波形循环恢复正常静默填充
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
        # 首推立即下发（仅当时已缓存的 1 帧，时长 = 帧数 × 2）
        await client.push_pulse_frame("s", "A", pulse_frame(100))
        waves = [f for f in client.sent if f.get("type") == "clientMsg"]
        self.assertEqual(len(waves), 1)
        import json as _json
        frames = _json.loads(waves[0]["message"].split(":", 1)[1])
        self.assertEqual(len(frames), 1)
        self.assertEqual(waves[0]["time"], 0.2)
        # 节流周期内的后续推送只入缓存，不再下发
        for _ in range(2):
            await client.push_pulse_frame("s", "A", pulse_frame(100))
        waves = [f for f in client.sent if f.get("type") == "clientMsg"]
        self.assertEqual(len(waves), 1)

        # 越过节流窗口：窗口触发时已缓存 4 帧
        client._pulse_last["A"] -= PULSE_SEND_S * 2
        await client.push_pulse_frame("s", "A", pulse_frame(100))
        # 再缓存 20 帧（节流窗口内不再触发）
        for _ in range(20):
            await client.push_pulse_frame("s", "A", pulse_frame(100))
        # 窗口到期再推：整窗重发最近 PULSE_WINDOW_FRAMES 帧
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
    """记录 push_pulse_frame 调用的假后端（无内置波形支持面）。"""

    def __init__(self, slots=("s1",)):
        from dglab.state import EngineState, Slot
        self.state = EngineState(backend="ble")
        for sid in slots:
            self.state.slots[sid] = Slot(slot_id=sid, name="t", type="COYOTE_030")
        self.pushed: list[tuple[str, str, str]] = []

    async def push_pulse_frame(self, slot_id, channel, frame):
        self.pushed.append((slot_id, channel, frame))


class _PulseApi:
    """记录 push_pulse 调用的假模块 API 适配层。"""

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
    """核心输入参数目录与派发器：脉冲流数值推入（事件流周期驱动）。"""

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
            dispatchers["in_pulse_a"](5)      # 1-9 视作最低档 10
        self.assertEqual(api.pushed, [("A", 440, 100), ("B", 1000, 100),
                                      ("A", 10, 100)])

    def test_dispatcher_level_follows_api_hook(self):
        """提供 pulse_level 钩子时电平跟随响度（波形/振动包络起伏）。"""
        from dglab.params import build_dispatchers

        api = _PulseApi()
        api.levels = {"A": 62}
        api.pulse_level = lambda channel: api.levels.get(channel, 0)
        with unittest.mock.patch("dglab.params.PULSE_PUSH_MIN_INTERVAL_S", 0):
            build_dispatchers(api)["in_pulse_a"](440)
            # 钩子异常时回退满电平
            api.pulse_level = lambda ch: 1 / 0
            build_dispatchers(api)["in_pulse_a"](440)
        self.assertEqual(api.pushed, [("A", 440, 62), ("A", 440, 100)])

    def test_dispatcher_rate_limited_to_10hz(self):
        from dglab.params import build_dispatchers

        api = _PulseApi()
        # 真实节流窗口 0.1s：紧连的两次派发只推一帧
        dispatchers = build_dispatchers(api)
        dispatchers["in_pulse_a"](440)
        dispatchers["in_pulse_a"](880)
        self.assertEqual(len(api.pushed), 1)
        self.assertEqual(api.pushed[0], ("A", 440, 100))

    def test_fire_card_period_pushes_every_tick(self):
        """事件流周期卡驱动脉冲参数：同值也每拍推帧（流语义）。"""
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
        engine.tick_event_cards(0.0)       # 首拍立即到期
        engine.tick_event_cards(0.05)      # 未到期（50ms < 100ms 周期）
        engine.tick_event_cards(0.10)      # 到期：同值也推
        engine.tick_event_cards(0.20)      # 再到期：继续推
        pulse_frames = [v for t, v in sent if t == "in_pulse_a"]
        self.assertEqual(pulse_frames, [440, 440, 440])

    def test_fire_card_strength_keeps_dedup(self):
        """对照：非周期参数（强度）同值去重语义不变。"""
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
        """映射表路径：脉冲参数同值也每泵派发（映射表兼容路径一致）。"""
        from dglab.mapping import MappingEngine
        from dglab.params import input_ranges

        sent: list[tuple[str, int]] = []
        engine = MappingEngine(lambda t, v: sent.append((t, v)),
                               ranges=input_ranges())
        engine.set_mappings({"in_pulse_a": "{x}"})   # 装载泵一次：x 未定义 → 0
        engine.signal("x", 440)                      # 信号变化触发泵
        engine.pump()                                # 显式泵：同值也派发
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
        # 未选中脉冲流：推流静默丢弃
        await engine.push_pulse_stream(440, channel="A")
        self.assertEqual(backend.pushed, [])
        # 选中后落地为帧
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

    async def test_module_context_passthrough(self):
        import plugins as plugins_module

        backend = FakeEngineBackend()
        engine = self._engine(backend)
        engine._selected_wave["A"] = PULSE_STREAM

        class FakeModule:
            id = "m"

        ctx = plugins_module.ModuleContext(engine, FakeModule())
        await ctx.push_pulse_stream(500, channel="A", level=40)
        self.assertEqual(len(backend.pushed), 1)
        self.assertEqual(backend.pushed[0][2], pulse_frame(500, 40))


if __name__ == "__main__":
    unittest.main()

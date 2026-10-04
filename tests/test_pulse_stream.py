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
from dglab.waves import (CONTINUOUS, PULSE_STREAM, PULSE_STREAM_MAX_FRAMES,
                         SILENT, build_frame, pulse_frame, resolve_wave_frames,
                         trim_pulse_stream, wave_order)
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

    def test_trim_pulse_stream(self):
        frames = ["aa" * 8] * (PULSE_STREAM_MAX_FRAMES + 5)
        trim_pulse_stream(frames)
        self.assertEqual(len(frames), PULSE_STREAM_MAX_FRAMES)
        trim_pulse_stream([])
        self.assertEqual(frames[-1], "aa" * 8)


class BlePulseStreamTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.patcher = unittest.mock.patch("dglab.ble.BleakClient", FakeBleakClient)
        self.patcher.start()
        self.ble = BleClient(StateEvents(), soft_limit_a=200, soft_limit_b=200)
        await self.ble.connect("addr-1", "coyote_v3")

    async def asyncTearDown(self):
        self.patcher.stop()

    async def test_select_starts_empty_and_push_appends(self):
        await self.ble.set_wave("A", PULSE_STREAM, slot_id="addr-1")
        cycle = self.ble.sessions["addr-1"]._cycles["A"]
        self.assertEqual(cycle.frames, [])

        await self.ble.push_pulse_frame("addr-1", "A", pulse_frame(100))
        await self.ble.push_pulse_frame("addr-1", "A", pulse_frame(200))
        self.assertEqual(len(cycle.frames), 2)
        self.assertEqual(cycle.frames[0], pulse_frame(100))
        # 播放循环逐帧取用
        self.assertEqual(cycle.next_frame(), pulse_frame(100))

    async def test_push_trims_to_cap(self):
        await self.ble.push_pulse_frame("addr-1", "A", pulse_frame(100))
        cycle = self.ble.sessions["addr-1"]._cycles["A"]
        for i in range(PULSE_STREAM_MAX_FRAMES + 10):
            cycle.frames.append(pulse_frame(100 + i))
        await self.ble.push_pulse_frame("addr-1", "A", pulse_frame(999))
        self.assertEqual(len(cycle.frames), PULSE_STREAM_MAX_FRAMES)
        self.assertEqual(cycle.frames[-1], pulse_frame(999))

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
        self.assertEqual(ops[0]["v"][0], pulse_frame(440))

        # 后续帧走波形循环补帧（非 immediate）
        client.sent.clear()
        await client.push_pulse_frame("s1", "A", pulse_frame(880))
        await client._wave_tick()
        ops = [op for op in self._ops(client) if op.get("c") == 0]
        self.assertTrue(ops)
        self.assertNotIn("im", ops[-1])

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

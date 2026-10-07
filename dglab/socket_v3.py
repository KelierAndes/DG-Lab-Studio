from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from collections import deque
from typing import Any

import websockets

from .state import EngineState, Slot, StateEvents
from .waves import PULSE_STREAM, CoyoteWaveform, resolve_wave_frames

DEFAULT_V3_RELAY = "wss://ws.dungeon-lab.cn/"
QR_TEMPLATE = "https://www.dungeon-lab.com/app-download.php#DGLAB-SOCKET#{url}"

_STRENGTH_RE = re.compile(r"^strength-(\d+)[-+](\d+)[-+](\d+)[-+](\d+)$")
_FEEDBACK_RE = re.compile(r"^feedback-(\d+)$")

CHANNEL_NUM = {"A": 1, "B": 2}

# 外部脉冲流（V3 为尽力而为）：协议只能整段替换波形，故缓存推入的帧并按
# 节流周期把最近窗口整段下发（App 以该时长播放，窗口衔接近似实时）。
PULSE_WINDOW_FRAMES = 20
PULSE_SEND_S = 1.0


def build_v3_qr(relay_url: str, target_id: str) -> str:
    base = relay_url.rstrip("/")
    return QR_TEMPLATE.format(url=f"{base}/{target_id}")


class SocketV3Client:
    def __init__(self, relay_url: str = DEFAULT_V3_RELAY, events: StateEvents | None = None,
                 qr_base: str | None = None):
        self.relay_url = relay_url
        self.qr_base = qr_base or relay_url
        self.events = events or StateEvents()
        self.state = EngineState(backend="v3")

        self._ws: Any = None
        self._reader_task: asyncio.Task | None = None
        self._closing = False
        self._pulse_buf: dict[str, deque] = {}
        self._pulse_last: dict[str, float] = {}

    def _log(self, msg: str) -> None:
        self.events.emit("log", f"[V3] {msg}")

    def _publish(self, status: str | None = None) -> None:
        if status is not None:
            self.state.status_text = status
        self.events.emit("state", self.state.copy())

    def _slot(self) -> Slot:
        slot = self.state.slots.get("coyote")
        if slot is None:
            slot = Slot(slot_id="coyote", name="Coyote 3.0", type="COYOTE_030")
            self.state.slots["coyote"] = slot
            self.state.active_slot = "coyote"
        return slot

    async def _send(self, payload: dict) -> None:
        if self._ws is None:
            raise RuntimeError("WebSocket 未连接")
        payload.setdefault("clientId", self.state.client_id)
        payload.setdefault("targetId", self.state.target_id)
        await self._ws.send(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))

    async def connect(self) -> None:
        if self._ws is not None:
            return
        self._closing = False
        self._log(f"连接 {self.relay_url}")
        self.state = EngineState(backend="v3", status_text="正在连接…")
        self._publish()
        self._ws = await websockets.connect(self.relay_url, max_size=2**22)
        self.state.connected = True
        self._reader_task = asyncio.create_task(self._reader())

        deadline = asyncio.get_running_loop().time() + 5
        while not self.state.client_id and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.05)
        if not self.state.client_id:
            fallback_id = str(uuid.uuid4())
            self._log("服务器未自动分配 ID，发送注册请求…")
            await self._send(
                {"type": "bind", "clientId": fallback_id, "targetId": "", "message": "targetId"}
            )
            deadline = asyncio.get_running_loop().time() + 3
            while not self.state.client_id and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.05)
        if not self.state.client_id:
            await self.disconnect()
            raise RuntimeError("服务器未分配 clientId")
        self.state.qr_text = build_v3_qr(self.qr_base, self.state.client_id)
        self._publish("等待 App 扫码接入…")
        self._log(f"clientId={self.state.client_id}")

    async def disconnect(self) -> None:
        self._closing = True
        if self._reader_task:
            self._reader_task.cancel()
            self._reader_task = None
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass
        self.state = EngineState(backend="v3", status_text="未连接")
        self._publish()
        self._log("已断开")

    async def _reader(self) -> None:
        try:
            async for raw in self._ws:
                try:
                    frame = json.loads(raw)
                except Exception:
                    continue
                if isinstance(frame, dict):
                    self._handle_frame(frame)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._log(f"连接错误: {exc!r}")
        finally:
            if not self._closing:
                self.state = EngineState(backend="v3", status_text="连接已断开")
                self._publish()

    def _handle_frame(self, frame: dict) -> None:
        ftype = frame.get("type")
        client_id = frame.get("clientId")
        target_id = frame.get("targetId")
        message = frame.get("message")

        if ftype == "bind":
            if isinstance(client_id, str) and (target_id in ("", None)):
                self.state.client_id = client_id
                self._publish()
            elif (
                isinstance(client_id, str)
                and isinstance(target_id, str)
                and message == "200"
            ):
                self.state.target_id = target_id
                self.state.paired = True
                self._slot()
                self._publish("App 已配对")
                self.events.emit("log", f"[V3] App 已配对: {target_id}")
            elif message not in ("targetId", "200"):
                self._log(f"配对失败: {message}")
            return

        if ftype == "break":
            self.state.target_id = ""
            self.state.paired = False
            self.state.slots.clear()
            self._publish("App 已断开，等待重新扫码接入…")
            return

        if ftype == "error":
            self._log(f"服务器错误: {message}")
            return

        if ftype in ("msg", 4, "clientMsg"):
            self._handle_app_message(message if isinstance(message, str) else "")

    def _handle_app_message(self, message: str) -> None:
        if not message:
            return
        m = _STRENGTH_RE.match(message)
        if m:
            a, b, a_max, b_max = (int(g) for g in m.groups())
            slot = self._slot()
            slot.strength = {"A": a, "B": b}
            slot.strength_limit = {"A": a_max, "B": b_max}
            self._publish()
            return
        m = _FEEDBACK_RE.match(message)
        if m:
            action = int(m.group(1))
            self.state.last_action = action
            self._log(f"App 按钮反馈: {action}")
            self.events.emit("action", action)
            self._publish()
            return

    async def set_strength(self, channel: str, value: int) -> None:
        if not self.state.paired:
            raise RuntimeError("V3 尚未与 App 完成配对")
        value = max(0, min(200, int(value)))
        await self._send(
            {
                "type": 3,
                "channel": CHANNEL_NUM[channel],
                "strength": value,
                "message": "set channel",
            }
        )

    async def add_strength(self, channel: str, delta: int) -> None:
        if not self.state.paired:
            raise RuntimeError("V3 尚未与 App 完成配对")
        ctype = 2 if delta >= 0 else 1
        for _ in range(max(1, abs(int(delta)))):
            await self._send(
                {"type": ctype, "channel": CHANNEL_NUM[channel], "message": "set channel"}
            )

    async def send_wave(
        self,
        channel: str,
        waveform: CoyoteWaveform | list[str],
        duration_s: float = 10.0,
    ) -> None:
        if not self.state.paired:
            raise RuntimeError("V3 尚未与 App 完成配对")
        if waveform == PULSE_STREAM:
            # 选中外部脉冲流：清空该通道脉冲缓存与 App 队列，等待模块推流
            self._pulse_buf.pop(channel, None)
            self._pulse_last.pop(channel, None)
            await self._send({"type": 4, "channel": CHANNEL_NUM[channel],
                              "message": "clear"})
            return
        frames = resolve_wave_frames(waveform, "COYOTE_030")
        frames = frames[:100]
        payload = json.dumps(frames, ensure_ascii=False, separators=(",", ":"))
        await self._send(
            {
                "type": "clientMsg",
                "channel": channel,
                "time": duration_s,
                "message": f"{channel}:{payload}",
            }
        )

    async def push_pulse_frame(self, slot_id: str, channel: str, frame: str) -> None:
        """外部脉冲流（V3 尽力而为）：缓存推入帧并按节流周期整段重发最近窗口。

        V3 协议只能整段替换波形、无法逐帧追加，故每秒把最近
        ``PULSE_WINDOW_FRAMES`` 帧以两倍窗口时长下发（App 内循环衔接），
        实时性弱于蓝牙 / V4 直连。未配对时静默丢弃。"""
        if not self.state.paired:
            return
        buf = self._pulse_buf.setdefault(channel, deque(maxlen=100))
        buf.append(frame)
        now = time.monotonic()
        if now - self._pulse_last.get(channel, 0.0) < PULSE_SEND_S:
            return
        self._pulse_last[channel] = now
        window = list(buf)[-PULSE_WINDOW_FRAMES:]
        try:
            await self.send_wave(channel, window, len(window) * 0.1 * 2)
        except Exception as exc:
            self._log(f"{channel} 脉冲流窗口下发失败: {exc!r}")

    async def clear_pulse(self, channel: str | None = None) -> None:
        if not self.state.paired:
            raise RuntimeError("V3 尚未与 App 完成配对")
        channels = [channel] if channel else ["A", "B"]
        for ch in channels:
            await self._send({"type": 4, "channel": CHANNEL_NUM[ch], "message": "clear"})

    async def emergency_stop(self) -> None:
        try:
            for ch in ("A", "B"):
                await self.set_strength(ch, 0)
            await self.clear_pulse()
            self._log("急停已执行 (强度清零 + 波形清空)")
        except Exception as exc:
            self._log(f"急停失败: {exc!r}")

from __future__ import annotations

import asyncio
import copy
import json
import time
from random import choices
from string import ascii_lowercase, digits
from typing import Any, Callable
from urllib.parse import quote

import websockets

from .monitor import WaveMonitor
from .state import EngineState, Slot, StateEvents
from .waves import FrameCycle, resolve_wave_frames

DEFAULT_V4_RELAY = "wss://trex.dungeon-lab.cn/v4"
PING_INTERVAL = 2.0
MAX_MISSED_PONGS = 3
RESPONSE_TIMEOUT = 8.0

ACTION_RESET = 7
ACTION_ADD = 3
ACTION_TEMP = 4
ACTION_PULSE = 0

CHANNELS = ("A", "B")
WAVE_TICK_S = 0.1
WAVE_BATCH_FRAMES = 10
WAVE_TOPUP_S = 0.6

V4_QR_TEMPLATE = "https://dungeon-lab.cn/s/?v=1&action=socket&url={url}"


def build_v4_qr(relay_url: str, target_id: str) -> str:
    app_url = f"{relay_url.rstrip('/')}/?tid={target_id}"
    return V4_QR_TEMPLATE.format(url=quote(app_url, safe=""))


def _merge_patch(current: Any, patch: Any) -> Any:
    if patch is None:
        return copy.deepcopy(current)
    if not isinstance(current, dict) or not isinstance(patch, dict):
        return copy.deepcopy(patch)
    merged = dict(current)
    for key, value in patch.items():
        merged[key] = _merge_patch(current.get(key), value)
    return merged


def _request_id() -> str:
    stamp = ""
    n = int(time.time() * 1000)
    alphabet = digits + ascii_lowercase
    while n:
        n, i = divmod(n, 36)
        stamp = alphabet[i] + stamp
    return f"v4-{stamp}-{''.join(choices(ascii_lowercase + digits, k=6))}"


class SocketV4Client:
    def __init__(self, relay_url: str = DEFAULT_V4_RELAY, events: StateEvents | None = None,
                 qr_base: str | None = None):
        self.relay_url = relay_url
        self.qr_base = qr_base or relay_url
        self.events = events or StateEvents()
        self.state = EngineState(backend="v4")

        self._ws: Any = None
        self._reader_task: asyncio.Task | None = None
        self._ping_task: asyncio.Task | None = None
        self._closing = False
        self._missed_pongs = 0

        self._clients: dict[str, dict] = {}
        self._pending: dict[str, asyncio.Future] = {}

        self._cycles: dict[tuple[str, str], FrameCycle] = {}
        self.monitors: dict[str, WaveMonitor] = {}
        self._wave_task: asyncio.Task | None = None
        self._play_deadline: dict[tuple[str, str], float] = {}
        self._props_logged: set[str] = set()

    def _log(self, msg: str) -> None:
        self.events.emit("log", f"[V4] {msg}")

    def _publish(self, status: str | None = None) -> None:
        if status is not None:
            self.state.status_text = status
        self.events.emit("state", self.state.copy())

    @staticmethod
    def _slot_from_device(dev: dict) -> Slot:
        props = dev.get("props") or {}
        slot_state = dev.get("slotState") or {}
        slot = Slot(
            slot_id=str(dev.get("slotId", "")),
            name=str(dev.get("name", "")),
            type=str(dev.get("type", "")),
            props=copy.deepcopy(props),
            slot_state=copy.deepcopy(slot_state),
        )
        _apply_props(slot, props)
        _apply_slot_state(slot, slot_state)
        return slot

    def _active_client_id(self) -> str | None:
        for cid in self._clients:
            return cid
        return None

    def _active_slot_id(self) -> str | None:
        if self.state.active_slot and self.state.active_slot in self.state.slots:
            return self.state.active_slot
        for slot_id in self.state.slots:
            return slot_id
        return None

    def _require_peer(self, slot_id: str | None = None) -> tuple[str, str]:
        cid = self._active_client_id()
        sid = slot_id or self._active_slot_id()
        if not cid or not sid:
            raise RuntimeError("V4 尚未与 App/设备完成配对")
        return cid, sid

    def _cycle(self, slot_id: str, channel: str) -> FrameCycle:
        return self._cycles.setdefault((slot_id, channel), FrameCycle())

    def _monitor(self, slot_id: str) -> WaveMonitor:
        return self.monitors.setdefault(slot_id, WaveMonitor())

    def set_wave_frames(self, slot_id: str, channel: str, frames: list[str] | None) -> None:
        if frames is None or not frames:
            from .waves import SILENT_FRAMES
            frames = list(SILENT_FRAMES)
        self._cycle(slot_id, channel).reset(frames)

    def _slot_wave_frames(self, slot_id: str, device_type: str,
                          channel: str, count: int = WAVE_BATCH_FRAMES) -> list[str] | None:
        cycle = self._cycles.get((slot_id, channel))
        if cycle is None or not cycle.frames:
            return None
        return [cycle.next_frame() for _ in range(count)]

    async def _wave_loop(self) -> None:
        try:
            while not self._closing:
                await asyncio.sleep(WAVE_TICK_S)
                try:
                    await self._wave_tick()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._log(f"波形发送异常: {exc!r}")
        except asyncio.CancelledError:
            pass

    async def _send_batch(self, sid: str, channel: str, frames: list[str],
                          immediate: bool = False) -> None:
        import time as _time

        cid, _sid = self._require_peer(sid)
        req_id = _request_id()
        payload = {"s": sid, "c": CHANNELS.index(channel), "t": ACTION_PULSE,
                   "d": len(frames) * 100, "v": frames}
        if immediate:
            payload["im"] = True
        await self._send_raw({
            "type": "message", "clientId": cid,
            "data": {"t": "req", "reqId": req_id, "m": "device.op",
                     "data": payload},
        })
        key = (sid, channel)
        now = _time.monotonic()
        start = now if immediate else max(now, self._play_deadline.get(key, now))
        self._play_deadline[key] = start + len(frames) * 0.1
        monitor = self._monitor(sid)
        for i, frame in enumerate(frames):
            segs = list(bytes.fromhex(frame)[4:8])
            segs_a = segs if channel == "A" else [0, 0, 0, 0]
            segs_b = segs if channel == "B" else [0, 0, 0, 0]
            monitor.record_at(start + i * 0.1, segs_a, segs_b)

    async def _wave_tick(self) -> None:
        import time as _time

        if self._active_client_id() is None:
            return
        now = _time.monotonic()
        for sid in list(self.state.slots):
            slot = self.state.slots.get(sid)
            if slot is None:
                continue
            if not slot.is_output_device:
                continue
            for ch in ("A", "B"):
                key = (sid, ch)
                deadline = self._play_deadline.get(key)
                if deadline is not None and deadline - now >= WAVE_TOPUP_S:
                    continue
                cycle = self._cycles.get(key)
                if cycle is None or not cycle.frames:
                    self.set_wave_frames(sid, ch,
                                         getattr(self, "_silent_frames", None))
                    cycle = self._cycle(sid, ch)
                frames = [cycle.next_frame() for _ in range(WAVE_BATCH_FRAMES)]
                try:
                    await self._send_batch(sid, ch, frames)
                except Exception as exc:
                    self._play_deadline[key] = now + 0.5
                    self._log(f"{sid}/{ch} 波形批次发送失败: {exc!r}")

    async def start_wave_loop(self) -> None:
        if self._wave_task is None or self._wave_task.done():
            self._wave_task = asyncio.create_task(self._wave_loop())

    async def stop_wave_loop(self) -> None:
        if self._wave_task is not None:
            self._wave_task.cancel()
            self._wave_task = None

    async def connect(self) -> None:
        if self._ws is not None:
            return
        self._closing = False
        self._log(f"连接 {self.relay_url}")
        self.state = EngineState(backend="v4", status_text="正在连接…")
        self._publish()
        self._ws = await websockets.connect(self.relay_url, max_size=2**22)
        self.state.connected = True
        self._reader_task = asyncio.create_task(self._reader())
        self._ping_task = asyncio.create_task(self._pinger())
        deadline = time.monotonic() + 8
        while self.state.client_id == "" and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        if not self.state.client_id:
            await self.disconnect()
            raise RuntimeError("服务器未返回 hello (clientId)")
        self.state.qr_text = build_v4_qr(self.qr_base, self.state.client_id)
        self._publish("等待 App 扫码接入…")
        self._log(f"targetId={self.state.client_id}")
        from .waves import resolve_wave_frames as _rwf, SILENT as _SILENT
        self._silent_frames = _rwf(_SILENT, "COYOTE_030")
        await self.start_wave_loop()

    async def disconnect(self) -> None:
        self._closing = True
        await self.stop_wave_loop()
        for task in (self._reader_task, self._ping_task):
            if task:
                task.cancel()
        self._reader_task = self._ping_task = None
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass
        self.state = EngineState(backend="v4", status_text="未连接")
        self._publish()
        self._log("已断开")

    async def _pinger(self) -> None:
        try:
            while True:
                await asyncio.sleep(PING_INTERVAL)
                if self._missed_pongs >= MAX_MISSED_PONGS:
                    self._log("服务器 ping 超时，断开")
                    await self.disconnect()
                    return
                await self._send_raw({"type": "ping"})
                self._missed_pongs += 1
        except asyncio.CancelledError:
            pass

    async def _reader(self) -> None:
        try:
            async for raw in self._ws:
                try:
                    frame = json.loads(raw)
                except Exception:
                    continue
                self._handle_frame(frame)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._log(f"连接错误: {exc!r}")
        finally:
            if not self._closing:
                self.state = EngineState(backend="v4", status_text="连接已断开")
                self._publish()
                for fut in self._pending.values():
                    if not fut.done():
                        fut.set_exception(RuntimeError("连接已断开"))
                self._pending.clear()

    async def _send_raw(self, frame: dict) -> None:
        if self._ws is None:
            raise RuntimeError("WebSocket 未连接")
        self._log_frame(">>", frame)
        await self._ws.send(json.dumps(frame, ensure_ascii=False, separators=(",", ":")))

    def _log_frame(self, direction: str, frame: dict) -> None:
        ftype = frame.get("type")
        if ftype in ("ping", "pong", "heartbeat"):
            return
        data = frame.get("data")
        if isinstance(data, dict) and data.get("t") == "resp":
            if data.get("error"):
                self.events.emit("log", f"[V4] 指令错误: {data.get('error')} req={data.get('reqId')}")
                return
        self.events.emit("frame_log", direction, frame)

    def _handle_frame(self, frame: dict) -> None:
        self._log_frame("<<", frame)
        ftype = frame.get("type")
        if ftype == "hello":
            self.state.client_id = str(frame.get("clientId", ""))
            self._publish()
        elif ftype == "pong":
            self._missed_pongs = 0
        elif ftype == "client_attached":
            cid = str(frame.get("clientId", ""))
            self._clients.setdefault(cid, {"devices": {}})
            self.state.target_id = cid
            self.state.paired = True
            self._publish("App 已接入")
            self.events.emit("log", f"[V4] App 接入: {cid}")
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop is not None:
                loop.create_task(self._after_attached(cid))
        elif ftype == "client_disconnected":
            cid = str(frame.get("clientId", ""))
            self._clients.pop(cid, None)
            if self.state.target_id == cid:
                self.state.target_id = ""
                self.state.paired = bool(self._clients)
            self._publish("App 已断开，等待重新扫码接入…")
        elif ftype == "idle_timeout":
            self._log("服务器空闲超时 (5 分钟无 App 接入)")
        elif ftype == "error":
            self._log(f"服务器错误: {frame.get('message') or frame.get('code')}")
        elif ftype == "message":
            self._handle_message_frame(frame)

    async def _after_attached(self, cid: str) -> None:
        try:
            await self.request_devices(cid)
        except Exception as exc:
            self._log(f"devices.get 失败: {exc!r}")

    def _handle_message_frame(self, frame: dict) -> None:
        cid = frame.get("clientId")
        data = frame.get("data")
        if not isinstance(cid, str) or not isinstance(data, dict):
            return
        entry = self._clients.setdefault(cid, {"devices": {}})

        if data.get("t") == "resp":
            req_id = data.get("requestId") or data.get("reqId")
            fut = self._pending.get(req_id)
            if fut and not fut.done():
                if data.get("error"):
                    fut.set_exception(RuntimeError(f"V4 指令失败: {data['error']}"))
                else:
                    fut.set_result(data.get("result"))
            if isinstance(data.get("result"), dict) and isinstance(
                data["result"].get("devices"), list
            ):
                self._replace_devices(cid, data["result"]["devices"])
            return

        ev = data.get("ev")
        if data.get("t") == "ev" and isinstance(ev, str):
            if ev == "devices.snapshot":
                self._replace_devices(cid, data.get("devices") or [])
            elif ev == "devices.patch":
                for dev in data.get("added") or []:
                    self._upsert_device(cid, dev)
                for sid in data.get("removed") or []:
                    entry["devices"].pop(str(sid), None)
                self._sync_state(cid)
            elif ev == "slots.patch":
                for slot in data.get("slots") or []:
                    self._patch_slot(cid, slot)
                self._sync_state(cid)
            elif ev == "custom.action":
                action = data.get("action")
                self.state.last_action = int(action) if isinstance(action, (int, float)) else None
                self._log(f"App 按钮反馈: {self.state.last_action}")
                self.events.emit("action", self.state.last_action)
                self._publish()

    def _replace_devices(self, cid: str, devices: list) -> None:
        entry = self._clients.setdefault(cid, {"devices": {}})
        merged: dict[str, dict] = {}
        for dev in devices:
            if isinstance(dev, dict) and dev.get("slotId"):
                sid = str(dev["slotId"])
                old = entry["devices"].get(sid, {})
                if "props" not in dev and old.get("props"):
                    dev = {**dev, "props": old["props"]}
                if "slotState" not in dev and old.get("slotState"):
                    dev = {**dev, "slotState": old["slotState"]}
                merged[sid] = copy.deepcopy(dev)
        entry["devices"] = merged
        self._sync_state(cid)

    def _upsert_device(self, cid: str, dev: dict) -> None:
        if not isinstance(dev, dict) or not dev.get("slotId"):
            return
        entry = self._clients.setdefault(cid, {"devices": {}})
        old = entry["devices"].get(str(dev["slotId"]))
        if old:
            self._log_app_zero(str(dev["slotId"]), old.get("props") or {},
                               dev.get("props") or {})
            merged = {**old, **copy.deepcopy(dev)}
            merged["props"] = _merge_patch(old.get("props"), dev.get("props"))
            merged["slotState"] = _merge_patch(old.get("slotState"), dev.get("slotState"))
            entry["devices"][str(dev["slotId"])] = merged
        else:
            entry["devices"][str(dev["slotId"])] = copy.deepcopy(dev)
        self._sync_state(cid)

    def _patch_slot(self, cid: str, slot_patch: dict) -> None:
        sid = slot_patch.get("slotId")
        if not isinstance(sid, str):
            return
        entry = self._clients.setdefault(cid, {"devices": {}})
        dev = entry["devices"].get(sid, {"slotId": sid})
        dev = copy.deepcopy(dev)
        self._log_app_zero(sid, dev.get("props") or {}, slot_patch.get("props") or {})
        dev["props"] = _merge_patch(dev.get("props"), slot_patch.get("props"))
        dev["slotState"] = _merge_patch(dev.get("slotState"), slot_patch.get("slotState"))
        entry["devices"][sid] = dev
        self._sync_state(cid)

    def _log_app_zero(self, sid: str, old_props: dict, new_props: dict) -> None:
        if not isinstance(old_props, dict) or not isinstance(new_props, dict):
            return
        for ch, key in (("A", "intensityA"), ("B", "intensityB")):
            new = new_props.get(key)
            old = old_props.get(key, 0)
            if isinstance(new, (int, float)) and new == 0 \
                    and isinstance(old, (int, float)) and old > 0:
                self._log(
                    f"{sid} 通道 {ch} 强度回报为 0 (归零指令回显或 App 端自适应归零)"
                )

    def _debug_props_once(self, sid: str, slot: Slot) -> None:
        return

    def _sync_state(self, cid: str) -> None:
        entry = self._clients.get(cid)
        if entry is None:
            return
        if self.state.target_id != cid:
            if self.state.target_id:
                return
            self.state.target_id = cid
        self.state.slots = {sid: self._slot_from_device(d) for sid, d in entry["devices"].items()}
        if self.state.active_slot not in self.state.slots:
            self.state.active_slot = next(iter(self.state.slots), "")
        for sid, slot in self.state.slots.items():
            self._debug_props_once(sid, slot)
        self._publish()

    async def request_devices(self, cid: str) -> dict:
        try:
            result = await self._rpc(cid, "devices.get", timeout=2.0)
        except asyncio.TimeoutError:
            self._log("devices.get 无 resp 响应 (App 已改用 snapshot 事件上报)")
            return {}
        if isinstance(result, dict) and isinstance(result.get("devices"), list):
            self._replace_devices(cid, result["devices"])
        return result

    async def _rpc(self, cid: str, method: str, data: Any = None, timeout: float = RESPONSE_TIMEOUT) -> Any:
        req_id = _request_id()
        req: dict[str, Any] = {"t": "req", "reqId": req_id, "requestId": req_id, "m": method}
        if data is not None:
            req["data"] = data
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        try:
            await self._send_raw({"type": "message", "clientId": cid, "data": req})
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._pending.pop(req_id, None)

    async def _operate(self, payload: dict, *, wait: bool = False, extra_timeout: float | None = None) -> Any:
        cid, _sid = self._require_peer()
        duration = payload.get("d")
        timeout = RESPONSE_TIMEOUT
        if isinstance(duration, (int, float)) and duration > RESPONSE_TIMEOUT * 1000:
            timeout = duration / 1000 + 1.0
        if extra_timeout:
            timeout = extra_timeout
        req_id = _request_id()
        req = {
            "t": "req",
            "reqId": req_id,
            "requestId": req_id,
            "m": "device.op",
            "data": payload,
        }
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        try:
            await self._send_raw({"type": "message", "clientId": cid, "data": req})
            if not wait:
                return None
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._pending.pop(req_id, None)

    def _check_output_slot(self, sid: str) -> None:
        slot = self.state.slots.get(sid)
        if slot is not None and not slot.is_output_device:
            raise RuntimeError("灵猫 (BMTR) 是气压传感器，无输出通道")

    async def set_strength(self, channel: str, value: int, slot_id: str | None = None) -> None:
        cid, sid = self._require_peer(slot_id)
        self._check_output_slot(sid)
        slot = self.state.slots.get(sid)
        if slot is None:
            raise RuntimeError(f"设备不存在: {sid}")
        value = max(0, min(200, int(value)))
        if slot.type.upper().startswith("OVC"):
            value = int(value / 10.0 + 0.5) * 10
        delta = value - slot.strength.get(channel, 0)
        if delta:
            await self.add_intensity(channel, delta, slot_id=sid)

    def select_slot(self, slot_id: str) -> None:
        if slot_id in self.state.slots:
            self.state.active_slot = slot_id
            self._publish()

    async def add_intensity(self, channel: str, value: float,
                            slot_id: str | None = None) -> None:
        cid, sid = self._require_peer(slot_id)
        self._check_output_slot(sid)
        await self._operate(
            {"s": sid, "c": CHANNELS.index(channel), "t": ACTION_ADD, "v": value}
        )

    async def set_intensity(self, channel: str, value: float,
                            slot_id: str | None = None) -> None:
        cid, sid = self._require_peer(slot_id)
        self._check_output_slot(sid)
        await self._operate(
            {"s": sid, "c": CHANNELS.index(channel), "t": ACTION_RESET, "v": value}
        )

    async def set_temp_intensity(self, channel: str, value: float, duration_ms: int,
                                 slot_id: str | None = None) -> None:
        cid, sid = self._require_peer(slot_id)
        self._check_output_slot(sid)
        await self._operate(
            {"s": sid, "c": CHANNELS.index(channel), "t": ACTION_TEMP, "v": value,
             "d": duration_ms}
        )

    async def reset_intensity(self, channel: str | None = None, slot_id: str | None = None) -> None:
        cid, sid = self._require_peer(slot_id)
        self._check_output_slot(sid)
        for ch in CHANNELS:
            if channel in (None, ch):
                await self._operate(
                    {"s": sid, "c": CHANNELS.index(ch), "t": ACTION_RESET, "v": 0}
                )

    async def set_wave(
        self,
        channel: str,
        waveform: "CoyoteWaveform | OvcWaveform | str | list[str]",
        duration_s: float = 10.0,
        slot_id: str | None = None,
    ) -> None:
        cid, sid = self._require_peer(slot_id)
        self._check_output_slot(sid)
        device_type = "COYOTE_030"
        slot = self.state.slots.get(sid)
        if slot is not None and slot.type:
            device_type = slot.type
        frames = resolve_wave_frames(waveform, device_type)
        self.set_wave_frames(sid, channel, frames)
        frames = self._slot_wave_frames(sid, device_type, channel) or frames
        try:
            await self._send_batch(sid, channel, frames, immediate=True)
        except Exception as exc:
            self._log(f"{sid} 波形切换失败: {exc!r}")
        self._log(f"{sid} 通道 {channel} 波形切换: {len(frames)} 帧 (持续循环)")

    async def clear_wave(self, channel: str | None = None, slot_id: str | None = None) -> None:
        cid, sid = self._require_peer(slot_id)
        if channel:
            self.set_wave_frames(sid, channel, None)
            self._play_deadline.pop((sid, channel), None)
            await self._rpc_or_clear(cid, {"s": sid, "c": CHANNELS.index(channel)})
        elif slot_id:
            self.set_wave_frames(sid, "A", None)
            self.set_wave_frames(sid, "B", None)
            self._play_deadline.pop((sid, "A"), None)
            self._play_deadline.pop((sid, "B"), None)
            await self._rpc_or_clear(cid, {"s": sid})
        else:
            for (s, _c) in list(self._cycles):
                self._cycles[(s, _c)].reset([])
            self._play_deadline.clear()
            await self._rpc_or_clear(cid, None)

    async def fire(self, slot_id: str | None = None, duration_s: float = 1.0,
                   value: float | None = None) -> None:
        cid, sid = self._require_peer(slot_id)
        self._check_output_slot(sid)
        duration_ms = max(1, round(duration_s * 1000))
        slot = self.state.slots.get(sid)
        for ch in CHANNELS:
            cap = 200
            if slot is not None:
                cap = slot.strength_limit.get(ch, 200)
            v = min(value, cap) if value is not None else cap
            await self.set_temp_intensity(ch, v, duration_ms, slot_id=sid)
        self._log(f"{sid} 一键开火 {duration_ms}ms (强度 {value or '通道上限'})")

    async def emergency_stop(self) -> None:
        await self.stop_wave_loop()
        self._play_deadline.clear()
        for (s, _c) in list(self._cycles):
            self._cycles[(s, _c)].reset([])
        cid = self._active_client_id()
        if cid:
            try:
                await self._rpc_or_clear(cid, None)
            except Exception as exc:
                self._log(f"急停清空队列失败: {exc!r}")
            for sid in list(self.state.slots):
                for ch in CHANNELS:
                    try:
                        await self._operate(
                            {"s": sid, "c": CHANNELS.index(ch), "t": ACTION_RESET, "v": 0}
                        )
                    except Exception as exc:
                        self._log(f"急停 {sid}/{ch} 清零失败: {exc!r}")
        await self.start_wave_loop()
        self._log(f"急停已执行 ({len(self.state.slots)} 台设备强度清零 + 队列清空)")

    async def _rpc_or_clear(self, cid: str, data: dict | None) -> Any:
        req_id = _request_id()
        req: dict[str, Any] = {"t": "req", "reqId": req_id, "requestId": req_id, "m": "device.op.clear"}
        if data:
            req["data"] = data
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        try:
            await self._send_raw({"type": "message", "clientId": cid, "data": req})
            return await asyncio.wait_for(fut, timeout=RESPONSE_TIMEOUT)
        except asyncio.TimeoutError:
            self._log("清除指令等待响应超时 (队列清空指令已送达)")
            return None
        finally:
            self._pending.pop(req_id, None)


def _find_battery(value: object, depth: int = 0) -> float | None:
    if depth > 4:
        return None
    if isinstance(value, dict):
        for key, item in value.items():
            k = str(key).lower()
            if isinstance(item, (int, float)) and any(
                token in k for token in ("power", "battery")
            ):
                return float(item)
        for item in value.values():
            found = _find_battery(item, depth + 1)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_battery(item, depth + 1)
            if found is not None:
                return found
    return None


def _apply_props(slot: Slot, props: dict) -> None:
    a = props.get("intensityA")
    b = props.get("intensityB")
    if isinstance(a, (int, float)):
        slot.strength["A"] = int(a)
    if isinstance(b, (int, float)):
        slot.strength["B"] = int(b)
    power = _find_battery(props)
    if power is not None:
        slot.battery = int(power)
    for ch in CHANNELS:
        status = props.get(f"channel{ch}Status")
        if isinstance(status, bool):
            slot.channel_status[ch] = 2 if status else 0
        elif isinstance(status, (int, float)):
            slot.channel_status[ch] = int(status)
    pressure = props.get("pressure")
    if isinstance(pressure, (int, float)):
        slot.pressure = float(pressure)


def _apply_slot_state(slot: Slot, slot_state: dict) -> None:
    for ch in CHANNELS:
        ch_state = slot_state.get(f"channel{ch}")
        if isinstance(ch_state, dict):
            limit = ch_state.get("intensityMax")
            if isinstance(limit, (int, float)):
                slot.strength_limit[ch] = int(limit)
    edge = slot_state.get("edge")
    if isinstance(edge, dict):
        state = edge.get("edgeState")
        if isinstance(state, (int, float)):
            slot.edge_state = int(state)

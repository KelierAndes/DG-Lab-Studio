from __future__ import annotations

import asyncio
import json
import re
import uuid
from typing import Any
from urllib.parse import parse_qs, urlsplit

import websockets
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from .state import StateEvents

HEARTBEAT_INTERVAL = 60.0
IDLE_TIMEOUT = 5 * 60.0
PULSE_REPLACE_DELAY = 0.15
DEFAULT_PULSE_DURATION = 5
DEFAULT_SENDS_PER_SECOND = 1
CLOSE_INVALID_TARGET_ID = 4001

_FRAME_RE = re.compile(r"^[0-9a-fA-F]{16}$")


class RelayV3Server:
    def __init__(self, host: str = "0.0.0.0", port: int = 9999, events: StateEvents | None = None):
        self.host = host
        self.port = port
        self.events = events or StateEvents()
        self._server: Any = None
        self._connections: dict[str, Any] = {}
        self._ws_to_id: dict[Any, str] = {}
        self._web_to_app: dict[str, str] = {}
        self._app_to_web: dict[str, str] = {}
        self._idle_tasks: dict[str, asyncio.TimerHandle] = {}
        self._pulse_tasks: dict[str, asyncio.Task] = {}
        self._heartbeat_task: asyncio.Task | None = None

    def log(self, msg: str) -> None:
        self.events.emit("log", f"[中继V3] {msg}")

    async def _send(self, ws: Any, payload: dict) -> bool:
        try:
            await ws.send(json.dumps(payload, ensure_ascii=False))
            return True
        except Exception:
            return False

    def _send_nowait(self, ws: Any, payload: dict) -> None:
        asyncio.get_running_loop().create_task(self._send(ws, payload))

    async def start(self) -> None:
        if self._server is not None:
            return
        self._server = await serve(self._handler, self.host, self.port)
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self.log(f"V3 中继已启动: ws://{self.host}:{self.port}")

    async def stop(self) -> None:
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None
        for task in list(self._pulse_tasks.values()):
            task.cancel()
        self._pulse_tasks.clear()
        for task in self._idle_tasks.values():
            task.cancel()
        self._idle_tasks.clear()
        server, self._server = self._server, None
        if server is not None:
            server.close()
            try:
                await server.wait_closed()
            except Exception:
                pass
        self.log("V3 中继已停止")

    async def _handler(self, ws: Any) -> None:
        path = getattr(getattr(ws, "request", None), "path", "/") or "/"
        split = urlsplit(path)
        query = parse_qs(split.query)
        raw_target = (
            query.get("targetId") or query.get("tid") or [None]
        )[0]
        if raw_target is None and len(split.path) > 1:
            raw_target = split.path.strip("/")

        client_id = str(uuid.uuid4())
        has_target = raw_target is not None and str(raw_target).strip() != ""
        target_id = str(raw_target).strip() if has_target else ""

        if has_target and not self._is_available_target(target_id):
            self.log(f"拒绝连接: 无效 targetId={target_id}")
            await self._send_error(ws, client_id, target_id, "4001")
            await ws.close(CLOSE_INVALID_TARGET_ID, "invalid_target_id")
            return

        self._connections[client_id] = ws
        self._ws_to_id[ws] = client_id
        self._start_idle(client_id)
        await self._send(ws, {"type": "bind", "clientId": client_id, "targetId": "", "message": "targetId"})

        if has_target:
            code = self._pair(target_id, client_id)
            if code != "200":
                self.log(f"配对失败 web={target_id} app={client_id} code={code}")
                self._cancel_idle(client_id)
                self._connections.pop(client_id, None)
                self._ws_to_id.pop(ws, None)
                await self._send_error(ws, client_id, target_id, code)
                await ws.close(CLOSE_INVALID_TARGET_ID, "invalid_target_id")
                return
            bind = {"type": "bind", "clientId": target_id, "targetId": client_id, "message": code}
            await self._send_to(target_id, bind)
            await self._send(ws, bind)
            self.log(f"被控方接入 {client_id} → 控制方 {target_id}")

        try:
            async for raw in ws:
                await self._on_message(ws, client_id, raw)
        except ConnectionClosed:
            pass
        except Exception as exc:
            self.log(f"连接异常: {exc!r}")
        finally:
            self._on_close(client_id)

    async def _on_message(self, ws: Any, sender_id: str, raw: Any) -> None:
        data = self._parse(raw)
        if data is None:
            await self._send_error(ws, sender_id, "", "403")
            return
        _type, client_id, target_id, message, extra = data

        if sender_id != client_id and sender_id != target_id:
            await self._send_error(ws, client_id, target_id, "404")
            return

        if _type == "bind":
            await self._handle_bind(client_id, target_id)
            return
        if isinstance(message, str) and (message.startswith("feedback") or message.startswith("strength")):
            await self._forward(ws, _type, client_id, target_id, message)
            return

        route_type = int(_type) if isinstance(_type, (int, float)) or (
            isinstance(_type, str) and _type.isdigit()
        ) else None

        if route_type in (1, 2, 3):
            await self._handle_strength_adjust(client_id, target_id, extra, route_type)
            return
        if route_type == 4:
            await self._handle_custom(client_id, target_id, extra, message)
            return
        if _type == "clientMsg":
            await self._handle_client_msg(client_id, target_id, extra, message)
            return
        if _type == "heartbeat":
            return
        await self._forward(ws, _type, client_id, target_id, message)

    def _parse(self, raw: Any) -> tuple[Any, str, str, str, dict] | None:
        try:
            parsed = json.loads(raw)
        except Exception:
            return None
        if not isinstance(parsed, dict):
            return None
        for key in ("type", "clientId", "targetId", "message"):
            if key not in parsed:
                return None
        _type = parsed["type"]
        if not ((isinstance(_type, str) and _type) or isinstance(_type, (int, float))):
            return None
        client_id, target_id, message = parsed.get("clientId"), parsed.get("targetId"), parsed.get("message")
        if not (isinstance(client_id, str) and isinstance(target_id, str) and isinstance(message, str)):
            return None
        if not client_id or not target_id:
            return None
        extra = {k: v for k, v in parsed.items() if k not in ("type", "clientId", "targetId", "message")}
        return _type, client_id, target_id, message, extra

    async def _handle_bind(self, web_id: str, app_id: str) -> None:
        code = self._pair(web_id, app_id)
        response = {"type": "bind", "clientId": web_id, "targetId": app_id, "message": code}
        if code != "200":
            ws = self._connections.get(web_id)
            if ws:
                await self._send(ws, response)
            self.log(f"绑定失败 {web_id} ↔ {app_id}: {code}")
            return
        await self._send_to(web_id, response)
        if app_id != web_id:
            await self._send_to(app_id, response)
        self.log(f"配对成功 {web_id} ↔ {app_id}")

    async def _handle_strength_adjust(self, client_id: str, target_id: str, extra: dict, route_type: int) -> None:
        if not self._is_paired(client_id, target_id):
            await self._error_to(client_id, target_id, "402")
            return
        channel = _normalize_channel(extra.get("channel"), default=1)
        if channel is None:
            await self._error_to(client_id, target_id, "406")
            return
        send_type = route_type - 1
        strength = _as_int(extra.get("strength"), 0) if route_type == 3 else 1
        text = f"strength-{channel[1]}+{send_type}+{strength}"
        sent = await self._send_to(target_id, {
            "type": "msg", "clientId": client_id, "targetId": target_id, "message": text,
        })
        if not sent:
            await self._error_to(client_id, target_id, "404")

    async def _handle_custom(self, client_id: str, target_id: str, extra: dict, message: str) -> None:
        if not self._is_paired(client_id, target_id):
            await self._error_to(client_id, target_id, "402")
            return
        channel = _normalize_channel(extra.get("channel"), default=1)
        if channel is None:
            await self._error_to(client_id, target_id, "406")
            return
        if "clear" in message:
            sent = await self._send_to(target_id, {
                "type": "msg", "clientId": client_id, "targetId": target_id, "message": f"clear-{channel[1]}",
            })
            if not sent:
                await self._error_to(client_id, target_id, "404")
            else:
                await self._notify_done(client_id, target_id)
            return
        strength = _as_int(extra.get("strength"), 0)
        sent = await self._send_to(target_id, {
            "type": "msg", "clientId": client_id, "targetId": target_id,
            "message": f"strength-{channel[1]}+2+{strength}",
        })
        if not sent:
            await self._error_to(client_id, target_id, "404")

    async def _handle_client_msg(self, client_id: str, target_id: str, extra: dict, message: str) -> None:
        if not self._is_paired(client_id, target_id):
            await self._error_to(client_id, target_id, "402")
            return
        channel = _normalize_channel(extra.get("channel"), default=None)
        if channel is None:
            await self._error_to(client_id, target_id, "406")
            return
        target_ws = self._connections.get(target_id)
        if target_ws is None:
            await self._error_to(client_id, target_id, "404")
            return

        time_s = _as_int(extra.get("time"), DEFAULT_PULSE_DURATION)
        if time_s <= 0:
            time_s = DEFAULT_PULSE_DURATION
        frames = _parse_frames(message)
        packet_count = max(1, time_s * DEFAULT_SENDS_PER_SECOND)
        if frames is None:
            packets = [f"pulse-{message}"] * packet_count
        else:
            total = max(1, time_s * 10)
            fitted = [frames[i % len(frames)] for i in range(total)]
            chunks = _split_frames(fitted, packet_count)
            packets = [f"pulse-{channel[0]}:{json.dumps(c)}" for c in chunks if c]

        key = f"{client_id}:{channel[0]}"
        old = self._pulse_tasks.pop(key, None)
        if old is not None:
            old.cancel()
            await self._send_to(target_id, {
                "type": "msg", "clientId": client_id, "targetId": target_id, "message": f"clear-{channel}",
            })
            await asyncio.sleep(PULSE_REPLACE_DELAY)

        task = asyncio.get_running_loop().create_task(
            self._run_pulse(key, client_id, target_id, packets, 1.0 / DEFAULT_SENDS_PER_SECOND)
        )
        self._pulse_tasks[key] = task

    async def _run_pulse(self, key: str, client_id: str, target_id: str, packets: list[str], interval: float) -> None:
        try:
            target_ws = self._connections.get(target_id)
            for index, text in enumerate(packets):
                if target_ws is None or self._connections.get(target_id) is not target_ws:
                    return
                await self._send(target_ws, {
                    "type": "msg", "clientId": client_id, "targetId": target_id, "message": text,
                })
                if index < len(packets) - 1:
                    await asyncio.sleep(interval)
            await self._notify_done(client_id, target_id)
        except asyncio.CancelledError:
            pass
        finally:
            if self._pulse_tasks.get(key) is not None:
                self._pulse_tasks.pop(key, None)

    async def _forward(self, ws: Any, _type: Any, client_id: str, target_id: str, message: str) -> None:
        if not self._is_paired(client_id, target_id):
            await self._error_to(client_id, target_id, "402")
            return
        sender_id = self._ws_to_id.get(ws)
        recipient = target_id if sender_id == client_id else client_id
        should_swap = _type == "msg" and sender_id is not None and sender_id in self._app_to_web
        if should_swap:
            payload = {"type": _type, "clientId": sender_id, "targetId": recipient, "message": message}
        else:
            payload = {"type": _type, "clientId": client_id, "targetId": target_id, "message": message}
        sent = await self._send_to(recipient, payload)
        if not sent:
            await self._error_to(client_id, target_id, "404")

    def _pair(self, web_id: str, app_id: str) -> str:
        if web_id == app_id:
            return "401"
        if web_id not in self._connections or app_id not in self._connections:
            return "401"
        if self._is_paired(web_id, app_id):
            return "200"
        if self._is_bound(web_id) or self._is_bound(app_id):
            return "400"
        self._web_to_app[web_id] = app_id
        self._app_to_web[app_id] = web_id
        self._cancel_idle(web_id)
        self._cancel_idle(app_id)
        return "200"

    def _unpair(self, client_id: str) -> None:
        app_id = self._web_to_app.pop(client_id, None)
        if app_id:
            self._app_to_web.pop(app_id, None)
            return
        web_id = self._app_to_web.pop(client_id, None)
        if web_id:
            self._web_to_app.pop(web_id, None)

    def _is_bound(self, client_id: str) -> bool:
        return client_id in self._web_to_app or client_id in self._app_to_web

    def _is_paired(self, a: str, b: str) -> bool:
        return self._web_to_app.get(a) == b or self._app_to_web.get(a) == b

    def _paired_id(self, client_id: str) -> str | None:
        return self._web_to_app.get(client_id) or self._app_to_web.get(client_id)

    def _is_available_target(self, client_id: str) -> bool:
        return client_id in self._connections and not self._is_bound(client_id)

    def _start_idle(self, client_id: str) -> None:
        self._cancel_idle(client_id)
        loop = asyncio.get_running_loop()
        self._idle_tasks[client_id] = loop.call_later(IDLE_TIMEOUT, lambda: self._idle_timeout(client_id))

    def _cancel_idle(self, client_id: str) -> None:
        task = self._idle_tasks.pop(client_id, None)
        if task is not None:
            task.cancel()

    def _idle_timeout(self, client_id: str) -> None:
        self._idle_tasks.pop(client_id, None)
        if client_id in self._web_to_app or client_id in self._app_to_web:
            return
        ws = self._connections.get(client_id)
        if ws is not None:
            self.log(f"未配对连接超时关闭 {client_id}")
            self._send_nowait(ws, {"type": "error", "clientId": client_id, "targetId": "", "message": "idle_timeout"})
            asyncio.get_running_loop().create_task(ws.close(1000, "idle_timeout"))

    async def _heartbeat_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                for client_id, ws in list(self._connections.items()):
                    await self._send(ws, {
                        "type": "heartbeat",
                        "clientId": client_id,
                        "targetId": self._paired_id(client_id) or "",
                        "message": "200",
                    })
        except asyncio.CancelledError:
            pass

    def _on_close(self, client_id: str) -> None:
        found = client_id in self._connections
        for w, i in list(self._ws_to_id.items()):
            if i == client_id:
                self._ws_to_id.pop(w, None)
                found = True
                break
        if not found:
            return
        self._cancel_idle(client_id)
        self._connections.pop(client_id, None)

        paired_id = self._paired_id(client_id)
        web_id = client_id if client_id in self._web_to_app else self._app_to_web.get(client_id)
        app_id = self._web_to_app.get(web_id) if web_id else None
        for task in [k for k in self._pulse_tasks if k.startswith(f"{client_id}:")]:
            self._pulse_tasks.pop(task, None).cancel()
        self._unpair(client_id)

        if paired_id:
            paired_ws = self._connections.get(paired_id)
            if paired_ws is not None:
                self._send_nowait(paired_ws, {
                    "type": "break",
                    "clientId": web_id or paired_id,
                    "targetId": app_id or client_id,
                    "message": "209",
                })
                asyncio.get_running_loop().create_task(paired_ws.close(1000, "partner_disconnected"))
            self.log(f"{client_id} 断开，已通知配对方 {paired_id}")

    async def _send_to(self, client_id: str, payload: dict) -> bool:
        ws = self._connections.get(client_id)
        if ws is None:
            return False
        return await self._send(ws, payload)

    async def _send_error(self, ws: Any, client_id: str, target_id: str, code: str) -> None:
        await self._send(ws, {"type": "error", "clientId": client_id, "targetId": target_id, "message": code})

    async def _error_to(self, client_id: str, target_id: str, code: str) -> None:
        ws = self._connections.get(client_id)
        if ws is not None:
            await self._send(ws, {"type": "error", "clientId": client_id, "targetId": target_id, "message": code})

    async def _notify_done(self, client_id: str, target_id: str) -> None:
        ws = self._connections.get(client_id)
        if ws is not None:
            await self._send(ws, {
                "type": "notify", "clientId": client_id, "targetId": target_id, "message": "发送完毕",
            })


def _normalize_channel(value: Any, default: int | None) -> tuple[str, int] | None:
    normalized = value if value is not None else default
    if normalized in (1, "1", "A", "a"):
        return "A", 1
    if normalized in (2, "2", "B", "b"):
        return "B", 2
    return None


def _as_int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _parse_frames(message: str) -> list[str] | None:
    sep = message.find(":")
    if sep <= 0:
        return None
    try:
        parsed = json.loads(message[sep + 1:])
    except Exception:
        return None
    if (
        not isinstance(parsed, list)
        or not parsed
        or not all(isinstance(i, str) and _FRAME_RE.match(i) for i in parsed)
    ):
        return None
    return [i.upper() for i in parsed]


def _split_frames(frames: list[str], packet_count: int) -> list[list[str]]:
    chunks = []
    for index in range(packet_count):
        start = (index * len(frames)) // packet_count
        end = ((index + 1) * len(frames)) // packet_count
        if end > start:
            chunks.append(frames[start:end])
    return chunks

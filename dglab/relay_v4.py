from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any
from urllib.parse import parse_qs, urlsplit

import websockets
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from .state import StateEvents

HEARTBEAT_INTERVAL = 30.0
IDLE_TIMEOUT = 5 * 60.0
CLOSE_CONTROLLER_DISCONNECTED = 4000
CLOSE_CONTROLLER_NOT_FOUND = 4001
CLOSE_IDLE_TIMEOUT = 4002


def _is_record(value: Any) -> bool:
    return isinstance(value, dict)


class RelayV4Server:
    def __init__(self, host: str = "0.0.0.0", port: int = 9998, events: StateEvents | None = None):
        self.host = host
        self.port = port
        self.events = events or StateEvents()
        self._server: Any = None
        self._controllers: dict[str, Any] = {}
        self._clients_by_controller: dict[str, dict[str, Any]] = {}
        self._client_to_controller: dict[str, str] = {}
        self._id_to_ws: dict[str, Any] = {}
        self._idle_tasks: dict[str, asyncio.TimerHandle] = {}
        self._heartbeat_task: asyncio.Task | None = None
        self._closing = False

    def log(self, msg: str) -> None:
        self.events.emit("log", f"[中继V4] {msg}")

    def _send(self, ws: Any, frame: dict) -> None:
        try:
            asyncio.create_task(ws.send(json.dumps(frame, ensure_ascii=False)))
        except Exception:
            pass

    async def _send_wait(self, ws: Any, frame: dict) -> None:
        try:
            await ws.send(json.dumps(frame, ensure_ascii=False))
        except Exception:
            pass

    async def start(self) -> None:
        if self._server is not None:
            return
        self._server = await serve(self._handler, self.host, self.port)
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self.log(f"V4 中继已启动: ws://{self.host}:{self.port}")

    async def stop(self) -> None:
        self._closing = True
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None
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
        self.log("V4 中继已停止")

    async def _handler(self, ws: Any) -> None:
        path = getattr(getattr(ws, "request", None), "path", "/") or "/"
        query = parse_qs(urlsplit(path).query)
        tid = (query.get("tid") or query.get("targetId") or [None])[0]

        client_id = os.urandom(4).hex()
        while client_id in self._id_to_ws:
            client_id = os.urandom(4).hex()
        self._id_to_ws[client_id] = ws
        await self._send_wait(ws, {"type": "hello", "clientId": client_id})

        if tid:
            await self._attach_client(ws, client_id, str(tid))
        else:
            self._controllers[client_id] = ws
            self._clients_by_controller[client_id] = {}
            self._start_idle(client_id)
            self.log(f"控制方接入 {client_id}")

        try:
            async for raw in ws:
                try:
                    frame = json.loads(raw)
                except Exception:
                    continue
                if not _is_record(frame):
                    continue
                if await self._handle_ping(ws, frame):
                    continue
                if frame.get("type") != "message":
                    continue
                await self._route(ws, client_id, frame)
        except ConnectionClosed:
            pass
        except Exception as exc:
            self.log(f"连接异常: {exc!r}")
        finally:
            self._on_close(client_id)

    async def _handle_ping(self, ws: Any, frame: dict) -> bool:
        ftype = frame.get("type")
        if ftype == "pong":
            return True
        if ftype == "ping":
            await self._send_wait(ws, {"type": "pong", "ts": int(time.time() * 1000)})
            return True
        return False

    async def _attach_client(self, ws: Any, client_id: str, tid: str) -> None:
        controller = self._controllers.get(tid)
        if controller is None:
            await self._send_wait(
                ws, {"type": "error", "code": "controller_not_found"}
            )
            await ws.close(CLOSE_CONTROLLER_NOT_FOUND, "controller_not_found")
            self._id_to_ws.pop(client_id, None)
            self.log(f"拒绝被控方 {client_id}: 控制方 {tid} 不存在")
            return
        self._clients_by_controller[tid][client_id] = ws
        self._client_to_controller[client_id] = tid
        self._cancel_idle(tid)
        await self._send_wait(ws, {"type": "controller_attached", "clientId": tid})
        await self._send_wait(controller, {"type": "client_attached", "clientId": client_id})
        self.log(f"被控方接入 {client_id} → 控制方 {tid}")

    async def _route(self, ws: Any, sender_id: str, frame: dict) -> None:
        data = frame.get("data")
        if sender_id in self._controllers:
            target_id = frame.get("clientId")
            if not isinstance(target_id, str) or not target_id:
                await self._send_wait(
                    ws,
                    {"type": "error", "code": "bad_request",
                     "message": "message.clientId is required"},
                )
                return
            client_ws = self._clients_by_controller.get(sender_id, {}).get(target_id)
            if client_ws is not None:
                await self._send_wait(client_ws, {"type": "message", "data": data})
            else:
                await self._send_wait(
                    ws, {"type": "error", "code": "client_not_found", "clientId": target_id}
                )
            return

        controller_id = self._client_to_controller.get(sender_id)
        controller = self._controllers.get(controller_id) if controller_id else None
        if controller is not None:
            await self._send_wait(
                controller, {"type": "message", "clientId": sender_id, "data": data}
            )

    def _start_idle(self, controller_id: str) -> None:
        self._cancel_idle(controller_id)
        loop = asyncio.get_running_loop()
        self._idle_tasks[controller_id] = loop.call_later(
            IDLE_TIMEOUT, lambda: self._idle_timeout(controller_id)
        )

    def _cancel_idle(self, controller_id: str) -> None:
        task = self._idle_tasks.pop(controller_id, None)
        if task is not None:
            task.cancel()

    def _idle_timeout(self, controller_id: str) -> None:
        self._idle_tasks.pop(controller_id, None)
        ws = self._controllers.get(controller_id)
        if ws is not None:
            self.log(f"控制方 {controller_id} 空闲超时，关闭")
            self._send(ws, {"type": "idle_timeout"})
            asyncio.get_running_loop().create_task(
                ws.close(CLOSE_IDLE_TIMEOUT, "idle_timeout")
            )

    def _on_close(self, client_id: str) -> None:
        if client_id not in self._id_to_ws:
            return
        self._id_to_ws.pop(client_id, None)

        if client_id in self._controllers:
            self._controllers.pop(client_id, None)
            clients = self._clients_by_controller.pop(client_id, {})
            self._cancel_idle(client_id)
            for app_id, app_ws in list(clients.items()):
                self._client_to_controller.pop(app_id, None)
                self._send(app_ws, {"type": "controller_disconnected", "clientId": client_id})
                asyncio.get_running_loop().create_task(
                    app_ws.close(CLOSE_CONTROLLER_DISCONNECTED, "controller_disconnected")
                )
            self.log(f"控制方断开 {client_id}，已踢出 {len(clients)} 个被控方")
            return

        controller_id = self._client_to_controller.pop(client_id, None)
        if controller_id:
            clients = self._clients_by_controller.get(controller_id, {})
            clients.pop(client_id, None)
            controller = self._controllers.get(controller_id)
            if controller is not None:
                self._send(controller, {"type": "client_disconnected", "clientId": client_id})
                if not clients:
                    self._start_idle(controller_id)
            self.log(f"被控方断开 {client_id}")

    async def _heartbeat_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                for ws in list(self._id_to_ws.values()):
                    self._send(ws, {"type": "heartbeat"})
        except asyncio.CancelledError:
            pass

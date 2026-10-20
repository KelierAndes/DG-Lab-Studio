from __future__ import annotations

import asyncio
import ctypes
import json
import os
import sys
import threading
import time
import traceback
from concurrent.futures import Future
from typing import Any

import socket as _socket

from dglab import bindings as binding_store
from dglab.ble import BleClient
from dglab import keys as keyboard_keys
from dglab.official_waveforms import CoyoteWaveform
from dglab.relay_v3 import RelayV3Server
from dglab.relay_v4 import RelayV4Server
from dglab.socket_v3 import DEFAULT_V3_RELAY, SocketV3Client
from dglab.socket_v4 import DEFAULT_V4_RELAY, SocketV4Client
from dglab.state import EngineState, Slot, StateEvents, family_of
from dglab.waves import (CONTINUOUS, COYOTE_WAVEFORMS, CoyoteWaveform,
                         PULSE_STREAM, SILENT, pulse_frame,
                         pulse_frame_vibration, resolve_wave_frames,
                         wave_order)
from dglab.flow_host import FlowHost, migrate_module_flows
from plugins import PluginManager


def _base_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


# 三条通道并存：v4 / v3 各自一个客户端（可带一个本地中继），蓝牙自己管多台设备。
# 键名与各自 EngineState.backend 一致，连接页按这个顺序平铺三张卡。
CHANNEL_KEYS = ("v4", "v3", "ble")
CHANNEL_LABELS = {"v4": "Socket V4", "v3": "Socket V3", "ble": "蓝牙直连"}


class Config(dict):
    DEFAULTS = {
        "v4_url": DEFAULT_V4_RELAY,
        "v3_url": DEFAULT_V3_RELAY,
        "wave_duration_s": 10.0,
        "max_strength": 100,
        "strength_step": 1,
        "fire_duration_s": 1.0,
        "fire_strength": 0,
        "fire_strength_a": 0,
        "fire_strength_b": 0,
        "ble": {
            "soft_limit_a": 200,
            "soft_limit_b": 200,
            "freq_balance_a": 0,
            "freq_balance_b": 0,
            "strength_balance_a": 0,
            "strength_balance_b": 0,
            "ovc_buttons": {
                "0": "a_strength_zero", "1": "b_strength_zero", "2": "none",
                "8": "a_strength_up", "9": "a_strength_down",
                "10": "a_wave_up", "11": "a_wave_down",
                "12": "b_wave_down", "13": "b_strength_down",
                "14": "b_wave_up", "15": "b_strength_up",
            },
        },
        "relay": {"v4_port": 9998, "v3_port": 9999,
                  "v4_local": False, "v3_local": False},
        "device_settings": {},
        "log_to_file": True,
        "auto_reconnect": True,
        "saved_devices": [],
    }

    def __init__(self, path: str | None = None):
        super().__init__(json.loads(json.dumps(self.DEFAULTS)))
        self.path = path or os.path.join(_base_dir(), "config.json")
        self.load()

    def load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return
        for key, value in data.items():
            if isinstance(value, dict) and isinstance(self.get(key), dict):
                self[key].update(value)
            else:
                self[key] = value

    def save(self) -> None:
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self, f, ensure_ascii=False, indent=2)
        except OSError:
            pass


import logging


def _file_handler(base_dir: str) -> logging.FileHandler:
    handler = logging.FileHandler(
        os.path.join(base_dir, "dgstudio.log"), encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s.%(msecs)03d %(message)s", "%Y-%m-%d %H:%M:%S")
    )
    return handler


def _setup_file_logger(base_dir: str, enabled: bool = True) -> logging.Logger:
    logger = logging.getLogger("dgstudio")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if enabled and not any(
        isinstance(h, logging.FileHandler) for h in logger.handlers
    ):
        try:
            logger.addHandler(_file_handler(base_dir))
        except OSError:
            pass
    return logger


class Engine:
    def __init__(self, config_path: str | None = None):
        self.config = Config(config_path)
        self._file_log = _setup_file_logger(_base_dir(),
                                            bool(self.config.get("log_to_file", True)))
        self.events = StateEvents()
        self.loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

        self._backend: SocketV4Client | SocketV3Client | BleClient | None = None
        self._relay: RelayV4Server | RelayV3Server | None = None
        # 通道登记表：_backend / _relay 仍是「主用」那一对，旧代码与测试直接读它们。
        self._channels: dict[str, object] = {}
        self._relays: dict[str, RelayV4Server | RelayV3Server] = {}
        self._primary = ""
        self._selected_wave: dict[str, CoyoteWaveform | str] = {
            "A": SILENT,
            "B": SILENT,
        }
        self.events.on("log", self._file_only)
        self.modules = PluginManager(self)
        self.flow = FlowHost(self)
        self._modules_ready = False
        self.events.on("modules_changed", self._on_module_change)
        self.events.on("ovc_button", self._on_ovc_button)
        self.events.on("ovc_button_up", self._on_ovc_button_up)
        self._fire_holds: dict[tuple[str, str], dict] = {}
        self._pulse_phase: dict[tuple[str, str], float] = {}
        self._migrate_ovc_profiles()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._loop_ready = threading.Event()
        self._thread = threading.Thread(target=self._run_loop, name="dglab-engine", daemon=True)
        self._thread.start()
        if not self._loop_ready.wait(timeout=5.0):
            raise RuntimeError("engine loop failed to start")
        self.submit(self._reconnect_loop())
        self.submit(self._startup_modules())
        self.flow.start()

    def _run_loop(self) -> None:
        if sys.platform == "win32":
            ctypes.windll.ole32.CoInitializeEx(None, 0x0)
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self._loop_ready.set()
        try:
            self.loop.run_forever()
        finally:
            pending = asyncio.all_tasks(self.loop)
            for task in pending:
                task.cancel()
            self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self.loop.close()

    def stop(self) -> None:
        if self.loop and self.loop.is_running():
            fut = asyncio.run_coroutine_threadsafe(self.shutdown(), self.loop)
            try:
                fut.result(timeout=3.0)
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=3.0)

    async def shutdown(self) -> None:
        self.flow.stop()
        try:
            await self._disconnect_backend()
        except Exception:
            pass
        try:
            if self.osc:
                await self.osc.stop()
        except Exception:
            pass
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)

    def submit(self, coro) -> Future:
        if not self.loop:
            raise RuntimeError("engine not started")
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def _log(self, msg: str) -> None:
        self.events.emit("log", msg)

    def _file_only(self, msg: str) -> None:
        try:
            self._file_log.info(msg)
        except Exception:
            pass

    def get_state(self) -> EngineState:
        """多通道合并视图：槽位取各通道并集，其余字段以主用通道为准。"""
        chans = self.active_channels()
        if not chans:
            return EngineState()
        primary = chans.get(self._primary) or next(iter(chans.values()))
        if getattr(primary, "state", None) is None:
            return EngineState()
        merged = primary.state.copy()
        for key, backend in chans.items():
            if backend is primary or getattr(backend, "state", None) is None:
                continue
            for sid, slot in backend.state.slots.items():
                merged.slots.setdefault(sid, Slot(**{**vars(slot)}))
            merged.qr_text = merged.qr_text or backend.state.qr_text
            merged.client_id = merged.client_id or backend.state.client_id
        merged.connected = any(b.state.connected for b in chans.values()
                               if getattr(b, "state", None) is not None)
        merged.backend = primary.state.backend
        return merged

    def active_channels(self) -> dict[str, object]:
        """通道 → 后端。`_backend` 仍可被直接赋值（旧界面与测试），不在登记表里时按通道补上。"""
        out = {k: b for k, b in self._channels.items() if b is not None}
        if self._backend is not None and not any(b is self._backend for b in out.values()):
            # 直接赋值的主用后端（旧界面与测试的假后端可能没有 state）也要能被找到。
            key = str(getattr(getattr(self._backend, "state", None), "backend", "") or "")
            out[key if key in CHANNEL_KEYS else CHANNEL_KEYS[0]] = self._backend
        return {k: out[k] for k in CHANNEL_KEYS if k in out}

    def slot_channels(self) -> dict[str, str]:
        """槽位 → 通道：控制页卡头要写明这台设备是从哪条通道进来的。"""
        out: dict[str, str] = {}
        for key, backend in self.active_channels().items():
            state = getattr(backend, "state", None)
            for sid in getattr(state, "slots", {}) or {}:
                out.setdefault(sid, key)
        return out

    def channel_states(self) -> list[dict]:
        """连接页要按通道各说各话：三条通道永远都在，没连的那条给出空状态。"""
        chans = self.active_channels()
        rows = []
        for key in CHANNEL_KEYS:
            backend = chans.get(key)
            state = getattr(backend, "state", None) if backend is not None else None
            if state is None:
                state = EngineState(backend=key)
            rows.append({
                "key": key,
                "label": CHANNEL_LABELS[key],
                "backend": state.backend or key,
                "present": backend is not None,
                "primary": key == self._primary and backend is not None,
                "connected": bool(state.connected),
                "paired": bool(state.paired),
                "status_text": state.status_text,
                "client_id": state.client_id,
                "target_id": state.target_id,
                "qr_text": state.qr_text,
                "slots": sorted(state.slots),
                "local_relay": key in self._relays,
                "relay_port": self._relay_port(key),
            })
        return rows

    def _relay_port(self, key: str) -> int | None:
        relay = self._relays.get(key)
        return int(getattr(relay, "port", 0) or 0) or None

    def _adopt(self, key: str, backend) -> None:
        """连上的通道登记进表，并把「主用」指针挪过去。"""
        self._channels[key] = backend
        self._backend = backend
        self._primary = key

    def _release(self, key: str) -> None:
        backend = self._channels.pop(key, None)
        if self._backend is not backend and not (backend is None and self._primary == key):
            return
        # 先把主用指针摘干净，再去看还剩哪些通道：`active_channels()` 会把「没登记进
        # 表但还被 `_backend` 指着的后端」按通道补回来，刚释放的那个正好还挂在上面，
        # 于是被自己重新认成主用——断开以后 `present` 永远是真的，概览上那条通道
        # 一直显示「等待客户端」，可日志里早就写了「通道已断开」。
        self._backend = None
        self._primary = ""
        chans = self.active_channels()
        self._primary = next(iter(chans), "")
        self._backend = chans.get(self._primary)

    @property
    def backend_kind(self) -> str:
        return self._backend.state.backend if self._backend else "none"

    async def _stop_channel(self, key: str) -> None:
        await self._stop_relay(key)
        backend = self._channels.get(key)
        if backend is not None:
            try:
                await backend.disconnect()
            except Exception:
                pass
        self._release(key)

    async def _disconnect_backend(self) -> None:
        """断开全部通道（旧名字保留：旧界面与关停路径都在用）。"""
        for key in list(self._channels):
            await self._stop_channel(key)
        if self._backend is not None:
            try:
                await self._backend.disconnect()
            except Exception:
                pass
            self._backend = None
        self._primary = ""
        await self._stop_relay()
        self.flow.reset()
        self.events.emit("state", self.get_state())

    async def disconnect_channel(self, key: str) -> None:
        if key not in CHANNEL_KEYS:
            raise ValueError(f"未知通道 {key!r}")
        await self._stop_channel(key)
        if not self._channels:
            self.flow.reset()
        self.events.emit("state", self.get_state())
        self._log(f"{CHANNEL_LABELS[key]} 通道已断开")

    async def _connect_relay_channel(self, key: str, *, client_for, relay_for=None,
                                     port: int | None = None) -> None:
        """开一条中继通道：可选地先起本地中继，再连本通道的客户端。

        只顶掉同通道的旧连接——v4 与 v3、蓝牙之间互不打断，多通道可以并存。
        """
        await self._stop_channel(key)
        qr_base = None
        if port is None:
            url = self.config[f"{key}_url"]
        else:
            if relay_for is None:
                raise ValueError(f"{key} 通道不支持本地中继")
            relay = relay_for(port)
            try:
                await relay.start()
            except OSError as exc:
                self._log(f"{CHANNEL_LABELS[key]} 本地中继启动失败 (端口 {port} 被占用?): {exc!r}")
                raise
            self._relays[key] = relay
            self._relay = relay
            qr_base = f"ws://{local_lan_ip()}:{port}"
            self._log(f"本地中继地址: {qr_base} (App 需与电脑同一局域网)")
            url = f"ws://127.0.0.1:{port}"
        client = client_for(url, qr_base)
        self._adopt(key, client)
        try:
            await client.connect()
        except Exception:
            await self._stop_channel(key)
            raise
        self.events.emit("state", self.get_state())

    async def connect_v4(self) -> None:
        await self._connect_relay_channel(
            "v4", client_for=lambda url, qr: SocketV4Client(url, events=self.events, qr_base=qr))

    async def connect_v3(self) -> None:
        await self._connect_relay_channel(
            "v3", client_for=lambda url, qr: SocketV3Client(url, events=self.events, qr_base=qr))

    async def connect_v4_local(self, port: int | None = None) -> None:
        await self._connect_relay_channel(
            "v4",
            client_for=lambda url, qr: SocketV4Client(url, events=self.events, qr_base=qr),
            relay_for=lambda p: RelayV4Server(port=p, events=self.events),
            port=int(port or self.config["relay"]["v4_port"]))

    async def connect_v3_local(self, port: int | None = None) -> None:
        await self._connect_relay_channel(
            "v3",
            client_for=lambda url, qr: SocketV3Client(url, events=self.events, qr_base=qr),
            relay_for=lambda p: RelayV3Server(port=p, events=self.events),
            port=int(port or self.config["relay"]["v3_port"]))

    async def _stop_relay(self, key: str | None = None) -> None:
        """停中继：给了通道号就只停那一条，没给就全停（`_relay` 字段留给旧代码读）。"""
        keys = [key] if key else list(self._relays)
        for k in keys:
            relay = self._relays.pop(k, None)
            if relay is None:
                continue
            try:
                await relay.stop()
            except Exception:
                pass
            if self._relay is relay:
                self._relay = next(iter(self._relays.values()), None)
        if key is None:
            self._relay = None

    async def ble_connect(self, address: str, kind: str = "coyote_v3") -> None:
        ble_cfg = self.config["ble"]
        existing = self._channels.get("ble")
        if isinstance(existing, BleClient):
            client = existing
        else:
            # 只顶掉蓝牙通道自己：v4 / v3 的客户端与本地中继不受影响。
            await self._stop_channel("ble")
            client = BleClient(
                events=self.events,
                soft_limit_a=int(ble_cfg["soft_limit_a"]),
                soft_limit_b=int(ble_cfg["soft_limit_b"]),
                freq_balance_a=int(ble_cfg["freq_balance_a"]),
                freq_balance_b=int(ble_cfg["freq_balance_b"]),
                strength_balance_a=int(ble_cfg["strength_balance_a"]),
                strength_balance_b=int(ble_cfg["strength_balance_b"]),
            )
            self._adopt("ble", client)
        try:
            await client.connect(address, kind)
        except Exception:
            if not client.sessions:
                self._release("ble")
            raise
        slot = client.state.slots.get(address)
        self.remember_device(address, kind, (slot.name if slot else None) or kind)

    async def ble_disconnect_device(self, slot_id: str) -> None:
        client = self._channels.get("ble")
        if isinstance(client, BleClient):
            await client.disconnect(slot_id)
            if not client.sessions:
                self._release("ble")
            self.events.emit("state", self.get_state())
            return
        if isinstance(self._backend, BleClient):
            await self._backend.disconnect(slot_id)

    def saved_device_list(self) -> list[dict]:
        return list(self.config.get("saved_devices", []))

    def remember_device(self, address: str, kind: str, name: str) -> None:
        devices = self.config.setdefault("saved_devices", [])
        for dev in devices:
            if dev.get("address") == address:
                dev.update(kind=kind, name=name)
                self.config.save()
                return
        devices.append({"address": address, "kind": kind, "name": name})
        self._log(f"设备已记录: {name} [{kind}] {address} (可一键重连)")
        self.config.save()
        self.events.emit("saved_devices", self.saved_device_list())

    def forget_device(self, address: str) -> None:
        devices = self.config.setdefault("saved_devices", [])
        self.config["saved_devices"] = [d for d in devices if d.get("address") != address]
        self.config.save()
        self.events.emit("saved_devices", self.saved_device_list())
        self._log(f"已删除设备记录: {address}")

    async def ble_reconnect_saved(self, address: str) -> None:
        for dev in self.config.get("saved_devices", []):
            if dev.get("address") == address:
                await self.ble_connect(address, dev.get("kind", "coyote_v3"))
                return
        raise RuntimeError(f"没有 {address} 的设备记录")

    async def _reconnect_loop(self) -> None:
        last_attempt: dict[str, float] = {}
        try:
            while True:
                await asyncio.sleep(2.0)
                if not self.config.get("auto_reconnect"):
                    continue
                backend = self._channels.get("ble")
                if not isinstance(backend, BleClient):
                    continue
                now = time.monotonic()
                saved = {d.get("address"): d for d in self.config.get("saved_devices", [])}
                for address in list(backend.dropped):
                    if now - last_attempt.get(address, 0.0) < 5.0:
                        continue
                    last_attempt[address] = now
                    dev = saved.get(address) or {}
                    name = dev.get("name", address)
                    kind = dev.get("kind", "coyote_v3")
                    self._log(f"自动重连 {name} …")
                    try:
                        await backend.connect(address, kind)
                        self._log(f"自动重连成功: {address}")
                    except Exception as exc:
                        self._log(f"自动重连失败 ({address}): {exc!r}")
        except asyncio.CancelledError:
            pass

    async def ble_scan(self, timeout: float = 6.0) -> list[dict]:
        return await BleClient.scan(timeout)

    def _require_backend(self, slot_id: str | None = None):
        """取要发指令的后端：给了槽位就找持有它的通道，否则用主用通道。"""
        if slot_id:
            for _key, backend in self.active_channels().items():
                if slot_id in getattr(backend.state, "slots", {}):
                    return backend
        if self._backend is None:
            chans = self.active_channels()
            if not chans:
                raise RuntimeError("没有已连接的设备")
            return chans.get(self._primary) or next(iter(chans.values()))
        return self._backend

    def devices(self) -> list[dict]:
        state = self.get_state()
        out = []
        for sid in sorted(state.slots):
            slot = state.slots[sid]
            out.append({
                "slot_id": sid,
                "name": slot.name or slot.type or sid,
                "type": slot.type,
                "family": family_of(slot.type),
            })
        return out

    def resolve_slot(self, slot_id: str | None = None, family: str | None = None,
                     output_only: bool = False) -> str | None:
        state = self.get_state()
        if slot_id and slot_id in state.slots:
            return slot_id
        if family:
            for dev in self.devices():
                if dev["family"] == family:
                    return dev["slot_id"]
        for sid in sorted(state.slots):
            if not output_only or state.slots[sid].is_output_device:
                return sid
        return None

    def _is_ovc(self, slot_id: str | None) -> bool:
        slot = self.get_state().slots.get(slot_id or "")
        return slot is not None and slot.type.upper().startswith("OVC")

    @staticmethod
    def step10(value) -> int:
        """负鼠的最低调节量是 10：整数项一律按 10 取整并夹在 0 / 10..200。"""
        v = int(value)
        if v <= 0:
            return 0
        return max(10, min(200, int(round(v / 10.0) * 10)))

    def _quantize_for_device(self, slot, value: int) -> int:
        if slot is not None and slot.type.upper().startswith("OVC"):
            return int(value / 10.0 + 0.5) * 10
        return value

    def device_setting(self, slot_id: str | None, key: str):
        settings = self.config.get("device_settings", {}).get(slot_id or "", {})
        value = settings.get(key)
        if value is None:
            value = self.config.get(key)
        return value

    INTENSITY_PARAM_RANGES = {
        "max_strength": (0, 200, int),
        "strength_step": (1, 50, int),
        "fire_strength": (0, 200, int),
        "fire_strength_a": (0, 200, int),
        "fire_strength_b": (0, 200, int),
        "fire_duration_s": (0.1, 60.0, float),
    }

    def param_step(self, key: str, slot_id: str | None = None) -> float:
        """界面 +/- 一次走多少：负鼠整数项走 10，其余整数走 1，浮点走 0.1。

        步进要和写入时的取整一致，否则点一下加会被引擎抹回原值，看着像按钮失灵。
        """
        cast = self.INTENSITY_PARAM_RANGES.get(key, (0, 0, int))[2]
        if cast is float:
            return 0.1
        return 10.0 if self._is_ovc(slot_id) else 1.0

    def _ovc_num(self, sid: str | None, key: str, default: int) -> int:
        value = int(self.device_setting(sid, key) or default)
        return self.step10(value) if self._is_ovc(sid) else value

    def _ovc_fire(self, sid: str | None, channel: str) -> int:
        value = self._fire_setting(sid, channel)
        return self.step10(value) if self._is_ovc(sid) else value

    def intensity_params(self, slot_id: str | None = None) -> dict:
        state = self.get_state()
        sid = self.resolve_slot(slot_id)
        slot = state.slots.get(sid) if sid else None

        def _fire_raw(channel: str) -> int:
            return self._fire_setting(sid, channel)

        return {
            "slot_id": sid,
            "connected": bool(slot is not None),
            "strength": dict(slot.strength) if slot is not None else {"A": 0, "B": 0},
            "strength_limit": (dict(slot.strength_limit) if slot is not None
                               else {"A": 200, "B": 200}),
            "channel_status": (dict(slot.channel_status) if slot is not None
                               else {"A": 0, "B": 0}),
            "max_strength": self._ovc_num(sid, "max_strength", 100),
            "strength_step": self.device_step(sid),
            "fire_strength": self._ovc_num(sid, "fire_strength", 0),
            "fire_strength_a": self._ovc_fire(sid, "A"),
            "fire_strength_b": self._ovc_fire(sid, "B"),
            "fire_duration_s": float(self.device_setting(sid, "fire_duration_s") or 1.0),
            "wave_duration_s": float(self.config.get("wave_duration_s", 10.0)),
            "wave": self.wave_selection(),
        }

    def _fire_setting(self, slot_id: str | None, channel: str) -> int:
        low = str(channel or "A").lower()
        per_dev = self.config.get("device_settings", {}).get(slot_id or "") or {}
        for key in (f"fire_strength_{low}", "fire_strength"):
            if key in per_dev:
                try:
                    return max(0, min(200, int(per_dev[key] or 0)))
                except (TypeError, ValueError):
                    continue
            value = self.config.get(key)
            try:
                value = int(value) if value is not None else 0
            except (TypeError, ValueError):
                value = 0
            if value > 0:
                return max(0, min(200, value))
        return 0

    def fire_strength(self, slot_id: str | None, channel: str) -> int:
        cap = self._fire_setting(slot_id, channel)
        if cap <= 0:
            cap = int(self.device_setting(slot_id, "max_strength") or 100)
        cap = max(1, min(200, cap))
        slot = self.get_state().slots.get(slot_id) if slot_id else None
        if slot is not None:
            limit = int(slot.strength_limit.get(str(channel).upper(), 200))
            if limit > 0:
                cap = min(cap, limit)
            cap = self._quantize_for_device(slot, cap)
        return max(1, cap)

    def _fire_value(self, slot, sid: str | None = None, channel: str = "A") -> int:
        return self.fire_strength(sid, channel)

    def set_intensity_param(self, key: str, value, slot_id: str | None = None) -> None:
        if key == "wave_duration_s":
            try:
                value = max(1.0, min(120.0, float(value)))
            except (TypeError, ValueError):
                raise ValueError(f"无效的 wave_duration_s: {value!r}") from None
            self.config["wave_duration_s"] = value
        elif key in self.INTENSITY_PARAM_RANGES:
            low, high, cast = self.INTENSITY_PARAM_RANGES[key]
            try:
                value = max(low, min(high, cast(value)))
            except (TypeError, ValueError):
                raise ValueError(f"无效的 {key}: {value!r}") from None
            if cast is int and self._is_ovc(slot_id):
                # 写进去的也要在 10 的格子上，否则界面显示的和实际用的差一截。
                value = self.step10(value)
            if slot_id:
                self.config.setdefault("device_settings", {}).setdefault(
                    slot_id, {})[key] = value
            else:
                self.config[key] = value
        else:
            raise ValueError(f"未知的强度参数: {key}")
        self.events.emit("intensity_params", self.intensity_params(slot_id))
        self._log(f"强度参数 {key} → {value}"
                  + (f" (设备 {slot_id})" if slot_id else " (全局)"))

    def wave_selection(self) -> dict:
        return {ch: str(self._selected_wave.get(ch) or SILENT) for ch in ("A", "B")}

    async def set_strength(self, channel: str, value: int, slot_id: str | None = None) -> None:
        sid = self.resolve_slot(slot_id, output_only=True)
        backend = self._require_backend(sid)
        limit = int(self.device_setting(slot_id, "max_strength") or 100)
        value = max(0, min(limit, int(value)))
        if isinstance(backend, SocketV4Client):
            if sid is None:
                raise RuntimeError("V4 尚未接入设备")
            await backend.set_strength(channel, value, slot_id=sid)
        elif isinstance(backend, SocketV3Client):
            await backend.set_strength(channel, value)
        else:
            await backend.set_strength(channel, value, slot_id=self.resolve_slot(slot_id))

    async def add_strength(self, channel: str, delta: int, slot_id: str | None = None) -> None:
        sid = self.resolve_slot(slot_id, output_only=True)
        backend = self._require_backend(sid)
        if isinstance(backend, SocketV4Client):
            slot = backend.state.slots.get(sid) if sid else None
            if slot is None:
                raise RuntimeError("V4 尚未接入设备")
            step = int(delta)
            if slot.type.upper().startswith("OVC"):
                step = max(10, round(abs(delta) / 10.0) * 10) * (1 if delta > 0 else -1)
            if step > 0 and slot.strength.get(channel, 0) >= int(
                    self.device_setting(sid, "max_strength") or 100):
                return
            await backend.add_intensity(channel, step, slot_id=sid)
        elif isinstance(backend, SocketV3Client):
            await backend.add_strength(channel, delta)
        else:
            await backend.add_strength(channel, delta, slot_id=sid)

    async def set_wave(self, channel: str, name: str, slot_id: str | None = None) -> None:
        sid = self.resolve_slot(slot_id, output_only=True)
        backend = self._require_backend(sid)
        self._selected_wave[channel] = name
        if isinstance(backend, SocketV4Client):
            await backend.set_wave(channel, name, slot_id=sid)
        elif isinstance(backend, SocketV3Client):
            await backend.send_wave(
                channel, name, float(self.config["wave_duration_s"])
            )
        else:
            await backend.set_wave(channel, name, slot_id=sid)

    async def push_pulse_stream(self, frequency: int, channel: str = "A",
                                level: int = 100, slot_id: str | None = None) -> None:
        if self._selected_wave.get(channel) != PULSE_STREAM:
            return
        chans = self.active_channels()
        if self._backend is None and not chans:
            return
        # 先看后端支不支持：这条报错说的是连接方式，不该被槽位解析抢先短路。
        backend = self._require_backend(slot_id)
        push = getattr(backend, "push_pulse_frame", None)
        if push is None:
            raise RuntimeError("当前连接方式不支持外部脉冲流")
        sid = self.resolve_slot(slot_id, output_only=True)
        if sid is None:
            return
        now = time.monotonic()
        key = (sid, channel)
        t_prev = self._pulse_phase.get(key, now)
        self._pulse_phase[key] = now
        slot = self.get_state().slots.get(sid)
        family = family_of(slot.type) if slot is not None else "COYOTE"
        if family == "OVC":
            frame = pulse_frame_vibration(frequency, level, t_prev)
        else:
            frame = pulse_frame(frequency, level)
        await push(sid, channel, frame)

    async def reset_strength(self, channel: str, slot_id: str | None = None) -> None:
        sid = self.resolve_slot(slot_id, output_only=True)
        backend = self._require_backend(sid)
        if isinstance(backend, SocketV4Client):
            await backend.reset_intensity(channel, slot_id=sid)
        elif isinstance(backend, SocketV3Client):
            await backend.set_strength(channel, 0)
        else:
            await backend.set_strength(channel, 0, slot_id=sid)
        await self.set_wave(channel, SILENT, slot_id=sid)

    async def clear_wave(self, channel: str | None = None, slot_id: str | None = None) -> None:
        sid = self.resolve_slot(slot_id, output_only=True)
        backend = self._require_backend(sid)
        for ch in ("A", "B"):
            if channel in (None, ch):
                await self.set_wave(ch, SILENT, slot_id=sid)

    async def zap(self, channel: str, seconds: float = 1.0, slot_id: str | None = None) -> None:
        await self.fire(slot_id=slot_id, duration_s=seconds, channel=channel)

    async def emergency_stop(self) -> None:
        self._cancel_fire_holds()
        chans = self.active_channels()
        if not chans:
            raise RuntimeError("没有已连接的设备")
        # 急停要覆盖每一条通道：多通道并存时只停主用那条会留下另一路在开火。
        for _key, backend in chans.items():
            stop = getattr(backend, "emergency_stop", None)
            if stop is not None:
                await stop()
        state = self.get_state()
        for sid in sorted(state.slots):
            slot = state.slots[sid]
            if not slot.is_output_device:
                continue
            for ch in ("A", "B"):
                try:
                    await self.set_strength(ch, 0, slot_id=sid)
                    await self.set_wave(ch, SILENT, slot_id=sid)
                except Exception as exc:
                    self._log(f"急停清零失败 {sid}/{ch}: {exc!r}")
        self._selected_wave["A"] = SILENT
        self._selected_wave["B"] = SILENT
        self.flow.reset()
        self._log("急停完成：全部输出设备强度清零，波形已重置为静默")

    def _cancel_fire_holds(self) -> None:
        for hold in self._fire_holds.values():
            task = hold.get("task")
            if task is not None:
                task.cancel()
        self._fire_holds.clear()

    def _fire_waves(self, sid: str) -> dict[str, str]:
        return {ch: str(self._selected_wave.get(ch) or SILENT) for ch in ("A", "B")}

    @staticmethod
    def _fire_channels(channel: str | None) -> tuple[str, ...]:
        return (channel,) if channel in ("A", "B") else ("A", "B")

    async def fire(self, slot_id: str | None = None, duration_s: float | None = None,
                   channel: str | None = None) -> None:
        sid = self.resolve_slot(slot_id, output_only=True)
        backend = self._require_backend(sid)
        if sid is None:
            raise RuntimeError("没有可用设备")
        duration = float(duration_s or self.config["fire_duration_s"])
        channels = self._fire_channels(channel)
        state = self.get_state()
        slot = state.slots.get(sid)
        caps = {ch: self._fire_value(slot, sid, ch) for ch in channels}
        original = self._fire_waves(sid)
        switched = [ch for ch in channels if original[ch] in ("", SILENT)]
        try:
            for ch in switched:
                await self.set_wave(ch, CONTINUOUS, slot_id=sid)
            if isinstance(backend, (SocketV4Client, BleClient)):
                await backend.fire(slot_id=sid, duration_s=duration,
                                   channels=channels, value=caps)
            else:
                self._log("当前连接模式不支持一键开火")
                return
            await asyncio.sleep(duration)
        finally:
            for ch in switched:
                self._selected_wave[ch] = original[ch]
                try:
                    await self.set_wave(ch, original[ch], slot_id=sid)
                except Exception:
                    pass

    async def fire_start(self, slot_id: str | None = None,
                         channel: str | None = None) -> None:
        sid = self.resolve_slot(slot_id, output_only=True)
        backend = self._require_backend(sid)
        if sid is None:
            raise RuntimeError("没有可用设备")
        channels = self._fire_channels(channel)
        state = self.get_state()
        slot = state.slots.get(sid)
        started: list[tuple[str, dict]] = []
        for ch in channels:
            if (sid, ch) in self._fire_holds:
                continue
            value = self._fire_value(slot, sid, ch)
            hold = {
                "waves": {ch: str(self._selected_wave.get(ch) or SILENT)},
                "strength": {ch: slot.strength.get(ch, 0) if slot is not None else 0},
                "value": value,
                "applied": {ch: 0},
                "task": None,
            }
            self._fire_holds[(sid, ch)] = hold
            started.append((ch, hold))
        if not started:
            return
        try:
            for ch, hold in started:
                if hold["waves"][ch] in ("", SILENT):
                    await self.set_wave(ch, CONTINUOUS, slot_id=sid)
            if isinstance(backend, SocketV4Client):
                for ch, hold in started:
                    cur = slot.strength.get(ch, 0) if slot is not None else 0
                    if hold["value"] > cur:
                        delta = hold["value"] - cur
                        await backend.add_intensity(ch, delta, slot_id=sid)
                        hold["applied"][ch] = delta
            elif isinstance(backend, SocketV3Client):
                for ch, hold in started:
                    await backend.set_strength(ch, hold["value"])
            else:
                for ch, hold in started:
                    await backend.set_strength(ch, hold["value"], slot_id=sid)
        except Exception:
            for ch, hold in started:
                self._fire_holds.pop((sid, ch), None)
            raise
        for ch, hold in started:
            hold["task"] = asyncio.create_task(self._fire_hold_timeout(sid, ch))
            self._log(f"{sid} 通道 {ch} 触发开火开始 (强度 {hold['value']})")

    async def _fire_hold_timeout(self, sid: str, channel: str) -> None:
        try:
            await asyncio.sleep(FIRE_HOLD_MAX_S)
        except asyncio.CancelledError:
            return
        self._log(f"{sid} 通道 {channel} 触发开火超时自动停止 "
                  f"(安全上限 {FIRE_HOLD_MAX_S:.0f}s)")
        try:
            await self.fire_stop(slot_id=sid, channel=channel)
        except Exception:
            pass

    async def fire_stop(self, slot_id: str | None = None,
                        channel: str | None = None) -> None:
        sid = self.resolve_slot(slot_id, output_only=True)
        backend = self._require_backend(sid)
        if sid is None:
            return
        channels = self._fire_channels(channel)
        holds: list[tuple[str, dict]] = []
        for ch in channels:
            hold = self._fire_holds.pop((sid, ch), None)
            if hold is not None:
                holds.append((ch, hold))
        if not holds:
            return
        for _ch, hold in holds:
            task = hold.get("task")
            if task is not None:
                task.cancel()
        if isinstance(backend, SocketV4Client):
            raised = [
                ch for ch, hold in holds
                if hold["applied"].get(ch)
                and self.get_state().slots.get(sid) is not None
                and self.get_state().slots[sid].strength.get(ch, 0)
                > int(hold["strength"].get(ch, 0))
            ]
            for _ in range(10 if raised else 0):
                await asyncio.sleep(0.1)
                cur = self.get_state().slots.get(sid)
                if cur is None:
                    break
                if all(
                    ch not in raised
                    or cur.strength.get(ch, 0)
                    >= min(int(hold["value"]), int(hold["strength"].get(ch, 0))
                           + int(hold["applied"].get(ch, 0)))
                    for ch, hold in holds
                ):
                    break
        state = self.get_state()
        slot = state.slots.get(sid)
        for ch, hold in holds:
            wave = hold["waves"].get(ch, SILENT)
            self._selected_wave[ch] = wave
            try:
                await self.set_wave(ch, wave, slot_id=sid)
            except Exception:
                pass
        if isinstance(backend, SocketV4Client):
            for ch, hold in holds:
                cur = slot.strength.get(ch, 0) if slot is not None else 0
                delta = int(hold["strength"].get(ch, 0)) - cur
                if delta == 0:
                    delta = -int(hold["applied"].get(ch, 0))
                if delta:
                    try:
                        await backend.add_intensity(ch, delta, slot_id=sid)
                        self._log(f"{sid} 通道 {ch} 开火强度恢复 "
                                  f"{cur} → {int(hold['strength'].get(ch, 0))}"
                                  f" (增量 {delta:+d})")
                    except Exception as exc:
                        self._log(f"{sid} 开火恢复强度失败: {exc!r}")
        elif isinstance(backend, SocketV3Client):
            for ch, hold in holds:
                await backend.set_strength(ch, int(hold["strength"].get(ch, 0)))
        else:
            for ch, hold in holds:
                await backend.set_strength(ch, int(hold["strength"].get(ch, 0)),
                                           slot_id=sid)
        self._log(f"{sid} 通道 {'/'.join(ch for ch, _ in holds)} "
                  f"触发开火结束 (强度与波形已恢复)")

    async def set_led_color(self, color: str, slot_id: str | None = None) -> None:
        sid = self.resolve_slot(slot_id)
        backend = self._require_backend(sid)
        if isinstance(backend, BleClient):
            await backend.set_led(color, slot_id=sid)
        else:
            self._log("LED 颜色切换目前仅支持蓝牙直连 (郊狼/负鼠/灵猫)")

    async def bmtr_flip(self, slot_id: str | None = None) -> None:
        sid = self.resolve_slot(slot_id, family="BMTR")
        backend = self._require_backend(sid)
        if isinstance(backend, BleClient):
            await backend.bmtr_flip(slot_id=sid)
        else:
            self._log("屏幕翻转目前仅支持蓝牙直连的灵猫")

    def wave_history(self, slot_id: str | None = None):
        sid = self.resolve_slot(slot_id)
        chans = self.active_channels()
        backend = chans.get(self._primary) if self._primary in chans else None
        if sid:
            backend = next((b for b in chans.values() if sid in b.state.slots), backend)
        if backend is None:
            return None
        if isinstance(backend, BleClient):
            session = backend.sessions.get(sid) if sid else None
            return session.monitor if session else None
        if isinstance(backend, SocketV4Client):
            return backend.monitors.get(sid) if sid else None
        return None

    def _config_dir(self) -> str:
        return os.path.dirname(getattr(self.config, "path", "")) or ""

    def _migrate_ovc_profiles(self) -> None:
        """按键映射配置落进 config/bindings/ 文件夹（每份配置一个 JSON）。

        旧版整套塞在主配置文件的 ble.ovc_profiles 里；首次启动时搬出去，
        之后主配置文件不再保存任何映射配置。
        """
        ble = self.config.setdefault("ble", {})
        active, folder = binding_store.load_binding_profiles(
            binding_store.bindings_dir(self._config_dir()))
        legacy = ble.get("ovc_profiles")
        if folder:
            if isinstance(legacy, dict):
                ble.pop("ovc_profiles", None)
                self.config.save()
            return
        if not isinstance(legacy, dict) or not legacy:
            profiles = {"默认": dict(ble.get("ovc_buttons") or {})}
            active = "默认"
        else:
            profiles = {str(name): {str(bit): str(binding)
                                    for bit, binding in (rows or {}).items()}
                        for name, rows in legacy.items()}
            active = str(ble.get("ovc_profile") or next(iter(profiles), "默认"))
            if active not in profiles:
                active = next(iter(profiles))
        binding_store.save_binding_profiles(
            active, profiles, binding_store.bindings_dir(self._config_dir()))
        ble.pop("ovc_profiles", None)
        ble.pop("ovc_profile", None)
        self.config.save()
        self._log(f"按键映射配置已迁到 config/bindings/（{len(profiles)} 份）")

    def binding_profiles(self) -> tuple[str, dict[str, dict[str, str]]]:
        """(当前配置名, {配置名: 按键绑定})，读 config/bindings/ 文件夹。"""
        return binding_store.load_binding_profiles(
            binding_store.bindings_dir(self._config_dir()))

    def save_binding_profiles(self, active: str,
                              profiles: dict[str, dict[str, str]]) -> bool:
        return binding_store.save_binding_profiles(
            active, profiles, binding_store.bindings_dir(self._config_dir()))

    def switch_binding_profile(self, name: str) -> bool:
        active, profiles = self.binding_profiles()
        if name not in profiles or name == active:
            return False
        self.save_binding_profiles(name, profiles)
        return True

    def ovc_bindings(self) -> dict[str, str]:
        active, profiles = self.binding_profiles()
        return profiles.get(active) or {}

    def binding_missing_modules(self, bindings: dict | None = None) -> dict[str, str]:
        if bindings is None:
            bindings = self.ovc_bindings()
        missing: dict[str, str] = {}
        for bit, binding in (bindings or {}).items():
            binding = str(binding)
            if not binding or binding == "none":
                continue
            key = binding.partition(":")[0]
            if key == "key" or binding in self._OVC_BUTTON_ACTIONS:
                continue
            if self.modules.action(key) is not None:
                continue
            missing[str(bit)] = binding
        return missing

    def modules_for_bindings(self, bindings: dict) -> list[str]:
        module_ids = {self.modules.module_for_action(str(b).partition(":")[0])
                      for b in bindings.values()}
        return sorted(mid for mid in module_ids if mid)

    def reset_bindings(self, bits, profile: str | None = None) -> None:
        active, profiles = self.binding_profiles()
        if profile and profile in profiles:
            active = profile
        prof = profiles.setdefault(active, {})
        for bit in bits:
            prof[str(bit)] = "none"
        self.save_binding_profiles(active, profiles)
        self._log(f"配置「{active}」中 {len(list(bits))} 个按键绑定已重置为无动作 "
                  f"(引用未启用的模块)")

    def rename_ovc_profile(self, old: str, new: str) -> str | None:
        new = (new or "").strip()
        active, profiles = self.binding_profiles()
        if old not in profiles:
            return f"配置「{old}」不存在"
        if not new:
            return "名称不能为空"
        if new == old:
            return None
        if new in profiles:
            return f"配置「{new}」已存在"
        renamed = {(new if k == old else k): v for k, v in profiles.items()}
        self.save_binding_profiles(new if active == old else active, renamed)
        self._log(f"按键映射配置文件已重命名: {old} → {new}")
        return None

    async def _startup_modules(self) -> None:
        await self.modules.autostart()
        self._modules_ready = True
        self._check_missing_bindings()
        try:
            migrate_module_flows(self.flow)
        except Exception:
            self._log(f"事件流迁移失败:\n{traceback.format_exc()}")

    def _on_module_change(self, module_id: str) -> None:
        self.refresh_flow_catalog()
        if self._modules_ready and self.modules.instance(module_id) is None:
            self._check_missing_bindings()

    def refresh_flow_catalog(self) -> None:
        """模块启用 / 卸载后立刻重建事件流目录：变量表与卡片列表不能留残项。"""
        host = getattr(self, "flow", None)
        if host is None:
            return
        loop = self.loop
        if loop is not None and loop.is_running():
            try:
                loop.call_soon_threadsafe(host.modules_changed)
                return
            except RuntimeError:
                pass
        host.modules_changed()

    def _check_missing_bindings(self) -> None:
        missing = self.binding_missing_modules()
        if not missing:
            return
        module_ids = self.modules_for_bindings(missing)
        if module_ids:
            names = []
            for mid in module_ids:
                meta = self.modules.meta(mid) or {}
                names.append(meta.get("name") or mid)
            self._log(f"按键映射引用了未启用的模块: {', '.join(names)}")
        else:
            self._log("按键映射包含未知动作")
        self.events.emit("binding_modules_missing",
                         {"modules": module_ids, "bindings": missing})

    def _on_ovc_button(self, slot_id: str, bit: int) -> None:
        binding = self.ovc_bindings().get(str(bit), "none")
        key, _sep, argument = str(binding).partition(":")
        if key == "key":
            name = binding[4:].strip()
            if name:
                ok = keyboard_keys.press(name)
                target = keyboard_keys.foreground_window()
                self._log(f"按键 bit{bit} → 键盘 {name} 按下"
                          + ("" if ok else " (注入失败)")
                          + (f" [前台: {target}]" if target else " [无前台窗口]"))
            return
        action = self.modules.action(key)
        if action is not None:
            self._log(f"按键 bit{bit} → {action.label}"
                      + (f" {argument}" if argument else ""))
            try:
                if action.on_press:
                    action.on_press(slot_id, argument or None)
            except Exception:
                self._log(f"按键动作 {action.key} 按下处理失败:\n"
                          f"{traceback.format_exc()}")
            return
        if binding not in self._OVC_BUTTON_ACTIONS:
            return
        self._log(f"按键 bit{bit} → {binding}")

        channel = "A" if binding.startswith("a_") else "B"

        async def _run() -> None:
            if binding == "fire":
                await self.fire_start(slot_id=slot_id)
            elif binding == "fire_a":
                await self.fire_start(slot_id=slot_id, channel="A")
            elif binding == "fire_b":
                await self.fire_start(slot_id=slot_id, channel="B")
            elif binding == "estop":
                await self.emergency_stop()
            elif binding.endswith("_zero"):
                await self.set_strength(channel, 0, slot_id=slot_id)
            elif binding.endswith("_up") or binding.endswith("_down"):
                delta = +1 if binding.endswith("_up") else -1
                if "strength" in binding:
                    await self.add_strength(channel, self.device_step(slot_id) * delta,
                                            slot_id=slot_id)
                else:
                    await self._step_device_wave(slot_id, channel, delta)

        if self.loop is not None and self.loop.is_running():
            asyncio.run_coroutine_threadsafe(_run(), self.loop)

    _OVC_BUTTON_ACTIONS = ("none", "a_strength_up", "a_strength_down",
                           "a_strength_zero",
                           "a_wave_up", "a_wave_down",
                           "b_strength_up", "b_strength_down",
                           "b_strength_zero",
                           "b_wave_up", "b_wave_down",
                           "fire", "fire_a", "fire_b", "estop")

    def _on_ovc_button_up(self, slot_id: str, bit: int) -> None:
        binding = self.ovc_bindings().get(str(bit), "none")
        key, _sep, argument = str(binding).partition(":")
        if key == "key":
            name = binding[4:].strip()
            if name:
                keyboard_keys.release(name)
                target = keyboard_keys.foreground_window()
                self._log(f"按键 bit{bit} → 键盘 {name} 松开"
                          + (f" [前台: {target}]" if target else ""))
            return
        action = self.modules.action(key)
        if action is not None:
            try:
                if action.on_release:
                    action.on_release(slot_id, argument or None)
            except Exception:
                self._log(f"按键动作 {action.key} 松开处理失败:\n"
                          f"{traceback.format_exc()}")
            return
        if binding in ("fire", "fire_a", "fire_b"):
            stop_ch = ({"fire_a": "A", "fire_b": "B"}.get(binding))

            async def _stop() -> None:
                await self.fire_stop(slot_id=slot_id, channel=stop_ch)

            if self.loop is not None and self.loop.is_running():
                asyncio.run_coroutine_threadsafe(_stop(), self.loop)

    def device_step(self, slot_id: str) -> int:
        try:
            step = max(1, min(50, int(self.device_setting(slot_id, "strength_step"))))
        except (TypeError, ValueError):
            step = 1
        slot = self.get_state().slots.get(slot_id)
        if slot is not None and slot.type.upper().startswith("OVC"):
            step = max(10, (step + 5) // 10 * 10)
        return step

    async def _step_device_wave(self, slot_id: str, channel: str, delta: int) -> None:
        slot = self.get_state().slots.get(slot_id)
        family = family_of(slot.type) if slot is not None else "COYOTE"
        order = wave_order(family)
        current = str(self._selected_wave.get(channel) or SILENT)
        if current not in order:
            current = SILENT
        target = order[(order.index(current) + delta) % len(order)]
        await self.set_wave(channel, target, slot_id=slot_id)
        self._log(f"{slot_id} 通道 {channel} 波形步进 {'+' if delta > 0 else '-'}1 → {target}")

    async def reset_pressure(self, slot_id: str | None = None) -> None:
        sid = self.resolve_slot(slot_id, family="BMTR")
        backend = self._require_backend(sid)
        if isinstance(backend, BleClient):
            await backend.reset_pressure(slot_id=sid)
        else:
            self._log("气压清零仅支持蓝牙直连的灵猫 (BMTR)")

    async def select_slot(self, slot_id: str) -> None:
        backend = self._require_backend(slot_id)
        if isinstance(backend, SocketV4Client):
            backend.select_slot(slot_id)

    @property
    def osc(self):
        module = self.modules.instance("osc_bridge")
        return getattr(module, "bridge", None) if module is not None else None

    async def osc_start(self) -> None:
        await self.modules.start("osc_bridge")

    async def osc_stop(self) -> None:
        await self.modules.stop("osc_bridge")

    def set_file_logging(self, enabled: bool) -> None:
        self.config["log_to_file"] = bool(enabled)
        logger = self._file_log
        if enabled:
            if not any(isinstance(h, logging.FileHandler) for h in logger.handlers):
                try:
                    logger.addHandler(_file_handler(_base_dir()))
                except OSError:
                    pass
            self._log("日志文件输出已开启")
        else:
            for h in list(logger.handlers):
                if isinstance(h, logging.FileHandler):
                    logger.removeHandler(h)
                    try:
                        h.close()
                    except Exception:
                        pass

    def save_config(self) -> None:
        self.config.save()
        self._log("配置已保存")


def local_lan_ip() -> str:
    s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


FIRE_HOLD_MAX_S = 60.0


__all__ = ["Config", "Engine", "local_lan_ip"]

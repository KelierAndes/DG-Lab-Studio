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

from dglab.ble import BleClient
from dglab import keys as keyboard_keys
from dglab.official_waveforms import CoyoteWaveform
from dglab.relay_v3 import RelayV3Server
from dglab.relay_v4 import RelayV4Server
from dglab.socket_v3 import DEFAULT_V3_RELAY, SocketV3Client
from dglab.socket_v4 import DEFAULT_V4_RELAY, SocketV4Client
from dglab.state import EngineState, StateEvents, family_of
from dglab.waves import (CONTINUOUS, COYOTE_WAVEFORMS, CoyoteWaveform,
                         PULSE_STREAM, SILENT, pulse_frame,
                         pulse_frame_vibration, resolve_wave_frames,
                         wave_order)
from plugins import PluginManager


def _base_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


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
        self._selected_wave: dict[str, CoyoteWaveform | str] = {
            "A": SILENT,
            "B": SILENT,
        }
        self.events.on("log", self._file_only)
        self.modules = PluginManager(self)
        self._modules_ready = False
        self.events.on("modules_changed", self._on_module_change)
        self.events.on("ovc_button", self._on_ovc_button)
        self.events.on("ovc_button_up", self._on_ovc_button_up)
        self._fire_holds: dict[tuple[str, str], dict] = {}
        # 脉冲流相位簿记：(slot, channel) → 上次推帧时刻（负鼠方波图案跨帧连续）
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

    def _run_loop(self) -> None:
        if sys.platform == "win32":
            # 引擎线程固定为 MTA 套间（COINIT_MULTITHREADED）：音频模块的
            # PortAudio WASAPI 初始化会用 CoInitialize(NULL) 把线程改成 STA，
            # bleak 在无消息泵的 STA 线程上蓝牙扫描/连接会直接报错。
            # 返回 RPC_E_CHANGED_MODE 说明已是其他套间，此时不动。
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
        if self._backend is not None:
            return self._backend.state
        return EngineState()

    @property
    def backend_kind(self) -> str:
        return self._backend.state.backend if self._backend else "none"

    async def _disconnect_backend(self) -> None:
        if self._backend is not None:
            try:
                await self._backend.disconnect()
            except Exception:
                pass
            self._backend = None
        await self._stop_relay()
        self.events.emit("state", self.get_state())

    async def connect_v4(self) -> None:
        await self._disconnect_backend()
        client = SocketV4Client(self.config["v4_url"], events=self.events)
        self._backend = client
        try:
            await client.connect()
        except Exception:
            self._backend = None
            raise

    async def connect_v3(self) -> None:
        await self._disconnect_backend()
        client = SocketV3Client(self.config["v3_url"], events=self.events)
        self._backend = client
        try:
            await client.connect()
        except Exception:
            self._backend = None
            raise

    async def connect_v4_local(self, port: int | None = None) -> None:
        await self._disconnect_backend()
        port = int(port or self.config["relay"]["v4_port"])
        relay = RelayV4Server(port=port, events=self.events)
        try:
            await relay.start()
        except OSError as exc:
            self._log(f"V4 本地中继启动失败 (端口 {port} 被占用?): {exc!r}")
            raise
        self._relay = relay
        qr_base = f"ws://{local_lan_ip()}:{port}"
        self._log(f"本地中继地址: {qr_base} (App 需与电脑同一局域网)")
        client = SocketV4Client(
            f"ws://127.0.0.1:{port}", events=self.events, qr_base=qr_base
        )
        self._backend = client
        try:
            await client.connect()
        except Exception:
            await self._stop_relay()
            self._backend = None
            raise

    async def connect_v3_local(self, port: int | None = None) -> None:
        await self._disconnect_backend()
        port = int(port or self.config["relay"]["v3_port"])
        relay = RelayV3Server(port=port, events=self.events)
        try:
            await relay.start()
        except OSError as exc:
            self._log(f"V3 本地中继启动失败 (端口 {port} 被占用?): {exc!r}")
            raise
        self._relay = relay
        qr_base = f"ws://{local_lan_ip()}:{port}"
        self._log(f"本地中继地址: {qr_base} (App 需与电脑同一局域网)")
        client = SocketV3Client(
            f"ws://127.0.0.1:{port}", events=self.events, qr_base=qr_base
        )
        self._backend = client
        try:
            await client.connect()
        except Exception:
            await self._stop_relay()
            self._backend = None
            raise

    async def _stop_relay(self) -> None:
        if self._relay is not None:
            try:
                await self._relay.stop()
            except Exception:
                pass
            self._relay = None

    async def ble_connect(self, address: str, kind: str = "coyote_v3") -> None:
        ble_cfg = self.config["ble"]
        if isinstance(self._backend, BleClient):
            client = self._backend
        else:
            await self._disconnect_backend()
            client = BleClient(
                events=self.events,
                soft_limit_a=int(ble_cfg["soft_limit_a"]),
                soft_limit_b=int(ble_cfg["soft_limit_b"]),
                freq_balance_a=int(ble_cfg["freq_balance_a"]),
                freq_balance_b=int(ble_cfg["freq_balance_b"]),
                strength_balance_a=int(ble_cfg["strength_balance_a"]),
                strength_balance_b=int(ble_cfg["strength_balance_b"]),
            )
            self._backend = client
        try:
            await client.connect(address, kind)
        except Exception:
            if not client.sessions:
                self._backend = None
            raise
        slot = client.state.slots.get(address)
        self.remember_device(address, kind, (slot.name if slot else None) or kind)

    async def ble_disconnect_device(self, slot_id: str) -> None:
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
                backend = self._backend
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

    def _require_backend(self):
        if self._backend is None:
            raise RuntimeError("没有已连接的设备")
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

    def intensity_params(self, slot_id: str | None = None) -> dict:
        """强度参数公开快照（模块/界面统一读取入口）。

        返回 slot_id 对应设备（缺省解析为默认输出设备）的当前强度、上限、
        探活状态、最大强度/步长/开火强度等全部强度相关参数。
        开火强度按通道拆分：``fire_strength_a`` / ``fire_strength_b``（0 =
        跟随本设备上限），``fire_strength`` 为旧版双通道共用的遗留键（兼容保留）。
        """
        state = self.get_state()
        sid = self.resolve_slot(slot_id)
        slot = state.slots.get(sid) if sid else None

        def _fire_raw(channel: str) -> int:
            """通道开火强度原始设定（0 = 跟随上限；取值链见 _fire_setting）。"""
            return self._fire_setting(sid, channel)

        return {
            "slot_id": sid,
            "connected": bool(slot is not None),
            "strength": dict(slot.strength) if slot is not None else {"A": 0, "B": 0},
            "strength_limit": (dict(slot.strength_limit) if slot is not None
                               else {"A": 200, "B": 200}),
            "channel_status": (dict(slot.channel_status) if slot is not None
                               else {"A": 0, "B": 0}),
            "max_strength": int(self.device_setting(sid, "max_strength") or 100),
            "strength_step": self.device_step(sid),
            "fire_strength": int(self.device_setting(sid, "fire_strength") or 0),
            "fire_strength_a": _fire_raw("A"),
            "fire_strength_b": _fire_raw("B"),
            "fire_duration_s": float(self.device_setting(sid, "fire_duration_s") or 1.0),
            "wave_duration_s": float(self.config.get("wave_duration_s", 10.0)),
            "wave": self.wave_selection(),
        }

    def _fire_setting(self, slot_id: str | None, channel: str) -> int:
        """通道开火强度的「最具体非零」设定；全链无设定返回 0（跟随上限）。

        取值链：设备级 ``fire_strength_<通道>`` → 全局 ``fire_strength_<通道>``
        → 设备级 ``fire_strength``（旧版双通道键）→ 全局 ``fire_strength``。
        设备级键**存在即为用户意图**（显式 0 = 跟随上限，该层终结）；
        全局 0 视为未设定，继续向旧键回退（升级兼容）。
        """
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
        """某通道开火强度的实际生效值（显式 0 / 全链未设定 = 跟随最大强度上限）。

        在 ``_fire_setting`` 结果上做跟随上限替换，再按该通道实际上报
        上限与设备量程取整钳制。"""
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
        """开火强度取值（``fire_strength`` 的内部入口，slot 已由调用方解析）。"""
        return self.fire_strength(sid, channel)

    def set_intensity_param(self, key: str, value, slot_id: str | None = None) -> None:
        """强度参数公开写入口（max_strength/strength_step/fire_strength/
        fire_strength_a/fire_strength_b/fire_duration_s/wave_duration_s）。

        slot_id 给出时写入该设备的独立覆盖（device_settings），否则写全局配置。
        """
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
        """当前选定的波形名（A/B 通道）。"""
        return {ch: str(self._selected_wave.get(ch) or SILENT) for ch in ("A", "B")}

    async def set_strength(self, channel: str, value: int, slot_id: str | None = None) -> None:
        backend = self._require_backend()
        limit = int(self.device_setting(slot_id, "max_strength") or 100)
        value = max(0, min(limit, int(value)))
        if isinstance(backend, SocketV4Client):
            sid = self.resolve_slot(slot_id, output_only=True)
            if sid is None:
                raise RuntimeError("V4 尚未接入设备")
            await backend.set_strength(channel, value, slot_id=sid)
        elif isinstance(backend, SocketV3Client):
            await backend.set_strength(channel, value)
        else:
            await backend.set_strength(channel, value, slot_id=self.resolve_slot(slot_id))

    async def add_strength(self, channel: str, delta: int, slot_id: str | None = None) -> None:
        backend = self._require_backend()
        sid = self.resolve_slot(slot_id, output_only=True)
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
        backend = self._require_backend()
        self._selected_wave[channel] = name
        if isinstance(backend, SocketV4Client):
            await backend.set_wave(channel, name, slot_id=self.resolve_slot(slot_id, output_only=True))
        elif isinstance(backend, SocketV3Client):
            await backend.send_wave(
                channel, name, float(self.config["wave_duration_s"])
            )
        else:
            await backend.set_wave(channel, name, slot_id=self.resolve_slot(slot_id, output_only=True))

    async def push_pulse_stream(self, frequency: int, channel: str = "A",
                                level: int = 100, slot_id: str | None = None) -> None:
        """外部脉冲流：接收联动模块推入的频率数据（每 0.1s 一次）生成波形。

        模块以 0.1s 节奏调用（每帧 100ms），核心把「逻辑频率 (10-1000) +
        电平 (0-100)」转成一帧脉冲，作为**最新帧**刷新设备播放——替代内置
        波形发生器（追加历史会让循环播放指针越落越后，频率严重滞后）。
        按设备家族构建：郊狼由设备按频率字节生成载波（电平=包络）；负鼠
        振动无载波，频率合成进振幅方波图案（:func:`pulse_frame_vibration`，
        相位跨帧连续）。仅当该通道波形选中「外部脉冲流」时落地，其余情况
        静默丢弃（模块可常推不息）；未连接设备同样忽略。V3 为尽力而为。
        """
        backend = self._backend
        if backend is None:
            return
        if self._selected_wave.get(channel) != PULSE_STREAM:
            return
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
        backend = self._require_backend()
        sid = self.resolve_slot(slot_id, output_only=True)
        if isinstance(backend, SocketV4Client):
            await backend.reset_intensity(channel, slot_id=sid)
        elif isinstance(backend, SocketV3Client):
            await backend.set_strength(channel, 0)
        else:
            await backend.set_strength(channel, 0, slot_id=sid)
        await self.set_wave(channel, SILENT, slot_id=sid)

    async def clear_wave(self, channel: str | None = None, slot_id: str | None = None) -> None:
        backend = self._require_backend()
        sid = self.resolve_slot(slot_id, output_only=True)
        for ch in ("A", "B"):
            if channel in (None, ch):
                await self.set_wave(ch, SILENT, slot_id=sid)

    async def zap(self, channel: str, seconds: float = 1.0, slot_id: str | None = None) -> None:
        """瞬时脉冲：仅对指定通道开火（通道分离后 zap 不再波及另一通道）。"""
        await self.fire(slot_id=slot_id, duration_s=seconds, channel=channel)

    async def emergency_stop(self) -> None:
        backend = self._require_backend()
        self._cancel_fire_holds()
        await backend.emergency_stop()
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
        """开火通道集合：显式 A/B 只动该通道，其余（None/旧调用）双通道。"""
        return (channel,) if channel in ("A", "B") else ("A", "B")

    async def fire(self, slot_id: str | None = None, duration_s: float | None = None,
                   channel: str | None = None) -> None:
        """一键开火：指定通道（``channel``="A"/"B"）或全部通道（缺省）。

        通道处于静默时临时切持续波形，结束后切回原本选定的波形。
        """
        backend = self._require_backend()
        sid = self.resolve_slot(slot_id, output_only=True)
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
        """按住持续开火：按通道独立保持（``channel`` 缺省 = 双通道）。

        保持记录以 (设备, 通道) 为键；已处于开火保持的通道不会重复触发。
        V4 以增量抬升强度，结束（或超时）时按设备回报恢复；其余后端直接
        设为开火强度，结束时恢复原强度。
        """
        backend = self._require_backend()
        sid = self.resolve_slot(slot_id, output_only=True)
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
        backend = self._require_backend()
        sid = self.resolve_slot(slot_id, output_only=True)
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
        backend = self._require_backend()
        if isinstance(backend, BleClient):
            await backend.set_led(color, slot_id=self.resolve_slot(slot_id))
        else:
            self._log("LED 颜色切换目前仅支持蓝牙直连 (郊狼/负鼠/灵猫)")

    async def bmtr_flip(self, slot_id: str | None = None) -> None:
        backend = self._require_backend()
        if isinstance(backend, BleClient):
            await backend.bmtr_flip(slot_id=self.resolve_slot(slot_id, family="BMTR"))
        else:
            self._log("屏幕翻转目前仅支持蓝牙直连的灵猫")

    def wave_history(self, slot_id: str | None = None):
        backend = self._backend
        if backend is None:
            return None
        if isinstance(backend, BleClient):
            sid = self.resolve_slot(slot_id)
            session = backend.sessions.get(sid) if sid else None
            return session.monitor if session else None
        if isinstance(backend, SocketV4Client):
            sid = self.resolve_slot(slot_id)
            return backend.monitors.get(sid) if sid else None
        return None

    def _migrate_ovc_profiles(self) -> None:
        """旧版单一 ovc_buttons 映射迁移为配置文件组 (ovc_profiles + ovc_profile)."""
        ble = self.config.setdefault("ble", {})
        profiles = ble.get("ovc_profiles")
        if not isinstance(profiles, dict) or not profiles:
            ble["ovc_profiles"] = {"默认": dict(ble.get("ovc_buttons") or {})}
        if not ble.get("ovc_profile"):
            ble["ovc_profile"] = next(iter(ble["ovc_profiles"]), "默认")

    def ovc_bindings(self) -> dict[str, str]:
        """当前激活配置文件的按键映射 (旧 ovc_buttons 作为兜底)."""
        ble = self.config.get("ble", {})
        profiles = ble.get("ovc_profiles") or {}
        active = ble.get("ovc_profile") or next(iter(profiles), "默认")
        return profiles.get(active) or ble.get("ovc_buttons") or {}

    # ---- 负鼠按键映射配置文件：校验 / 重置 / 改名 ----

    def binding_missing_modules(self, bindings: dict | None = None) -> dict[str, str]:
        """返回引用了未加载模块动作的按键绑定 (bit → binding)。

        内置动作与键盘注入 (key:) 视为始终可用；模块动作只有在对应模块
        加载后才可用，卸载模块后相关绑定由界面提示启用模块或拒绝加载。
        """
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
        """绑定集合引用到的（可启用的）模块 id 列表。"""
        module_ids = {self.modules.module_for_action(str(b).partition(":")[0])
                      for b in bindings.values()}
        return sorted(mid for mid in module_ids if mid)

    def reset_bindings(self, bits, profile: str | None = None) -> None:
        """把指定按键位的绑定重置为 none（拒绝加载引用未启用模块的映射时）。"""
        ble = self.config.setdefault("ble", {})
        profiles = ble.setdefault("ovc_profiles", {})
        active = profile or ble.get("ovc_profile") or next(iter(profiles), "默认")
        prof = profiles.setdefault(active, {})
        for bit in bits:
            prof[str(bit)] = "none"
        self.config.save()
        self._log(f"配置「{active}」中 {len(list(bits))} 个按键绑定已重置为无动作 "
                  f"(引用未启用的模块)")

    def rename_ovc_profile(self, old: str, new: str) -> str | None:
        """重命名按键映射配置文件；成功返回 None，失败返回错误说明。"""
        new = (new or "").strip()
        ble = self.config.setdefault("ble", {})
        profiles = ble.setdefault("ovc_profiles", {})
        if old not in profiles:
            return f"配置「{old}」不存在"
        if not new:
            return "名称不能为空"
        if new == old:
            return None
        if new in profiles:
            return f"配置「{new}」已存在"
        ble["ovc_profiles"] = {(new if k == old else k): v
                               for k, v in profiles.items()}
        if ble.get("ovc_profile") == old:
            ble["ovc_profile"] = new
        self.config.save()
        self._log(f"按键映射配置文件已重命名: {old} → {new}")
        return None

    async def _startup_modules(self) -> None:
        await self.modules.autostart()
        self._modules_ready = True
        self._check_missing_bindings()

    def _on_module_change(self, module_id: str) -> None:
        """模块装卸钩子：卸载会新增「映射引用未启用模块」，立即重新校验。

        安装只会消除缺失、不会产生新的缺失，故只在实例被移除时触发；
        启动阶段（autostart 进行中）不校验，等启动完成统一检查一次。
        """
        if self._modules_ready and self.modules.instance(module_id) is None:
            self._check_missing_bindings()

    def _check_missing_bindings(self) -> None:
        """启动时校验当前映射：引用未启用模块的动作则发事件交由界面处理。"""
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
        backend = self._require_backend()
        if isinstance(backend, BleClient):
            await backend.reset_pressure(slot_id=self.resolve_slot(slot_id, family="BMTR"))
        else:
            self._log("气压清零仅支持蓝牙直连的灵猫 (BMTR)")

    async def select_slot(self, slot_id: str) -> None:
        backend = self._require_backend()
        if isinstance(backend, SocketV4Client):
            backend.select_slot(slot_id)

    @property
    def osc(self):
        """当前 OSC 桥接器实例（osc_bridge 模块未加载时为 None）。"""
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

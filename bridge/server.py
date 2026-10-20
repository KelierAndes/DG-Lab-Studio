"""DGStudio 界面桥：把 Python 引擎包成本地 JSON-RPC + 事件推送。

Flutter 外壳以 `python -m bridge.server --handshake` 启动本进程，从 stdout 读一行
握手 JSON（`{"bridge":1,"port":N,"token":"..."}`），再连 `ws://127.0.0.1:N/ws`。
连接后必须先发一次 `bridge.auth`，通过前不推送也不接受其它方法。
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
from collections import deque
import dataclasses
import json
import os
import secrets
import sys
import threading
import time
import traceback
from typing import Any, Callable

from websockets.asyncio.server import serve

from app import Engine, local_lan_ip
from bridge.bindings import BINDING_METHODS
from bridge.deps import MODULE_DEP_METHODS
from bridge.detectors import DETECTOR_METHODS
from bridge.flow import FLOW_METHODS
from bridge.settings import SETTINGS_METHODS, settings_payload
from bridge.update import UPDATE_METHODS, set_publisher
from dglab.state import EngineState
from dglab.version import APP_VERSION
from module_store import version_key
from dglab.logs import classify_log

BRIDGE_VERSION = 1
AUTH_METHOD = "bridge.auth"
LOG_LABELS = {"debug", "info", "warn", "error"}


def _to_stderr(text: str) -> None:
    """侧车会被冻结成无控制台的 exe，那时 sys.stderr 可能是 None：直接写 fd 2。"""
    try:
        os.write(2, (text.rstrip() + "\n").encode("utf-8", "replace"))
    except OSError:
        pass


# --------------------------------------------------------------- 序列化


def snapshot_payload(state: EngineState) -> dict:
    """直接沿用 dataclass 的字段名，槽位保持 {slot_id: {...}} 的映射形状。"""
    return dataclasses.asdict(state)


def snapshot_for(engine: Engine) -> dict:
    """快照 = 合并视图 + 每条通道各自的状态：连接页要按通道各说各话。"""
    payload = snapshot_payload(engine.get_state())
    payload["channels"] = engine.channel_states()
    payload["slot_channels"] = engine.slot_channels()
    return payload


_QR_CACHE: dict[str, str] = {}


def qr_png_base64(text: str) -> str:
    """二维码在引擎侧出图（qrcode + PIL），界面只管贴。"""
    if not text:
        return ""
    hit = _QR_CACHE.get(text)
    if hit is not None:
        return hit
    import base64
    import io

    from qrcode import QRCode

    qr = QRCode(box_size=8, border=2)
    qr.add_data(text)
    qr.make(fit=True)
    buf = io.BytesIO()
    qr.make_image(fill_color="black", back_color="white").save(buf, format="PNG")
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    if len(_QR_CACHE) > 12:
        _QR_CACHE.clear()
    _QR_CACHE[text] = encoded
    return encoded


SILENT_LABEL = "静默"

_PARAM_LABELS = {
    "max_strength": "强度上限",
    "strength_step": "调节步进",
    "fire_strength": "开火强度",
    "fire_strength_a": "A 通道开火强度",
    "fire_strength_b": "B 通道开火强度",
    "fire_duration_s": "开火时长 (秒)",
}


def _family_catalog(raw: dict) -> list[dict]:
    """波形目录给出 name + 中文标签：界面显示标签，回传时用 name。

    官方波形之外还要补「持续」与「外部脉冲流」——旧界面的下拉就是这三段。
    """
    from dglab.waves import CONTINUOUS, PULSE_STREAM, SILENT

    rows = [{"name": str(SILENT), "label": SILENT_LABEL}]
    for wave, meta in (raw or {}).items():
        label = ""
        if isinstance(meta, dict):
            label = str(((meta.get("label") or {}).get("cn")) or "")
        name = str(getattr(wave, "name", wave))
        rows.append({"name": name, "label": label or name})
    rows.append({"name": str(CONTINUOUS), "label": "持续"})
    rows.append({"name": str(PULSE_STREAM), "label": "外部脉冲流"})
    return rows


def _wave_names() -> dict[str, list[dict]]:
    """按设备家族给出波形目录，控制页取当前槽位那一家。"""
    out: dict[str, list[dict]] = {}
    try:
        from dglab.official_waveforms import COYOTE_WAVEFORMS

        out["COYOTE"] = _family_catalog(COYOTE_WAVEFORMS)
    except Exception:
        out["COYOTE"] = [{"name": "__SILENT__", "label": SILENT_LABEL}]
    try:
        from dglab.official_waveforms_ovc import OVC_WAVEFORMS

        out["OVC"] = _family_catalog(OVC_WAVEFORMS)
    except Exception:
        out["OVC"] = [{"name": "__SILENT__", "label": SILENT_LABEL}]
    return out


def intensity_rows(engine: Engine, slot_id: str | None = None) -> list[dict]:
    """只暴露可调的数值参数，附带取值范围；slot_id 之类元数据不进界面。"""
    params = engine.intensity_params(slot_id)
    rows = []
    for key, (low, high, typ) in Engine.INTENSITY_PARAM_RANGES.items():
        value = params.get(key)
        if value is None:
            continue
        rows.append({
            "key": key,
            "label": _PARAM_LABELS.get(key, key),
            "value": round(float(value), 2) if typ is float else int(value),
            "min": low,
            "max": high,
            "float": typ is float,
            "step": engine.param_step(key, slot_id),
        })
    return rows


def _action_labels(raw: Any) -> list[str]:
    out = []
    for item in raw or []:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            out.append(str(item.get("label") or item.get("key") or item.get("name") or ""))
        else:
            out.append(str(getattr(item, "label", item)))
    return [label for label in out if label]


def modules_payload(engine: Engine) -> list[dict]:
    out = []
    for meta in engine.modules.list_modules():
        out.append({
            "id": meta.get("id", ""),
            "name": meta.get("name") or meta.get("id", ""),
            "short": str(meta.get("short") or ""),
            "version": str(meta.get("version", "")),
            "description": str(meta.get("description", "")),
            "enabled": bool(meta.get("enabled")),
            "loaded": bool(meta.get("loaded")),
            "running": bool(meta.get("running")),
            "realtime_manager": bool(meta.get("realtime_manager")),
            "actions": _action_labels(meta.get("actions")),
            # 模块自带的默认配置：模块卡据此显示清单并给「一键套用」。
            "defaults": engine.modules.default_profiles(str(meta.get("id") or "")),
        })
    return out


def config_payload(engine: Engine) -> dict:
    cfg = engine.config
    relay = cfg.get("relay") or {}
    return {
        "app_version": APP_VERSION,
        "bridge_version": BRIDGE_VERSION,
        "relay": {
            "v3_port": relay.get("v3_port", 9999),
            "v4_port": relay.get("v4_port", 9998),
            "v3_url": cfg.get("v3_url", ""),
            "v4_url": cfg.get("v4_url", ""),
            # 连接页要按这个决定「连接」按钮走远程还是本地中继；以前没回传，
            # 设置页那两颗开关看着就像没作用。
            "v3_local": bool(relay.get("v3_local", False)),
            "v4_local": bool(relay.get("v4_local", False)),
        },
        "auto_reconnect": bool(cfg.get("auto_reconnect", True)),
        "file_logging": bool(cfg.get("log_to_file", True)),
        "saved_devices": engine.saved_device_list(),
        "devices": engine.devices(),
        "waveforms": _wave_names(),
        # 指示灯：键名发给引擎（LED_COLORS），标签与旧界面同一套。
        "led_options": [
            {"key": "off", "label": "熄灭", "color": "#9AA0A6"},
            {"key": "yellow", "label": "黄色", "color": "#F2C94C"},
            {"key": "magenta", "label": "红色", "color": "#EB5757"},
            {"key": "purple", "label": "紫色", "color": "#9B51E0"},
            {"key": "blue", "label": "蓝色", "color": "#2F80ED"},
            {"key": "cyan", "label": "青色", "color": "#27C4D3"},
            {"key": "green", "label": "绿色", "color": "#27AE60"},
        ],
        "ble": {
            "soft_limit_a": (cfg.get("ble") or {}).get("soft_limit_a", 200),
            "soft_limit_b": (cfg.get("ble") or {}).get("soft_limit_b", 200),
        },
        "wave_selection": engine.wave_selection(),
        "intensity_params": intensity_rows(engine),
        "max_strength": cfg.get("max_strength", 100),
        "strength_step": cfg.get("strength_step", 1),
        "fire_duration_s": cfg.get("fire_duration_s", 1.0),
        "market": [],
        "settings": settings_payload(engine),
        "paths": {
            "config": engine.config.path,
            "modules": engine.modules.config_dir,
        },
        "version": {
            "app": APP_VERSION,
            "shell": "Flutter Windows",
            "engine": "Python sidecar",
        },
    }


# --------------------------------------------------------------- 方法表


def _set_config_path(engine: Engine, path: str, value: Any) -> None:
    keys = [k for k in path.split(".") if k]
    if not keys:
        raise ValueError("配置路径为空")
    node: Any = engine.config
    for key in keys[:-1]:
        nxt = node.get(key) if isinstance(node, dict) else None
        if not isinstance(nxt, dict):
            nxt = {}
            node[key] = nxt
        node = nxt
    node[keys[-1]] = value


def _config_set(engine: Engine, params: dict):
    path = str(params.get("path") or "")
    if path in {"relay.v3_url", "relay.v4_url"}:
        _set_config_path(engine, path.split(".")[-1], params.get("value"))
    elif path == "file_logging":
        _set_config_path(engine, "log_to_file", bool(params.get("value")))
    else:
        _set_config_path(engine, path, params.get("value"))
    engine.save_config()
    return config_payload(engine)


def _market_rows(engine: Engine, entries: list[dict]) -> list[dict]:
    """市场清单补两列：装没装、要不要更新。

    版本比较只在引擎侧做一次（`version_key` 按数字段比，0.10.0 才比 0.9.0 新）；
    界面照着布尔值画按钮，别让前端再猜一遍。装的是同一版就不该出现「更新」。
    """
    installed = {str(m.get("id") or ""): str(m.get("version") or "")
                 for m in engine.modules.list_modules()}
    rows = []
    for e in entries:
        mid = str(e.get("id") or "")
        version = str(e.get("version") or "")
        current = installed.get(mid)
        rows.append({
            "id": mid,
            "name": e.get("name") or mid,
            "version": version,
            "description": str(e.get("description") or ""),
            "installed": current is not None,
            "update": current is not None
            and version_key(version) > version_key(current),
        })
    return rows


async def _market_fetch(engine: Engine, params: dict):
    # 取清单是阻塞的网络活，放到线程里，别把引擎循环卡住。
    entries = await asyncio.to_thread(
        engine.modules.store.fetch_market, force=bool(params.get("force"))
    )
    return _market_rows(engine, entries)


async def _modules_enabled(engine: Engine, params: dict):
    """启用 = 装依赖 + 起实例 + 注入配置档，与旧界面的「安装并启动」同一条路。

    只 `load()` 会把模块读进来但不跑，界面上看就是「点了启用没反应」。
    """
    module_id = str(params.get("module_id") or "")
    enabled = bool(params.get("enabled"))
    if enabled:
        await engine.modules.install(module_id)
    else:
        await engine.modules.deactivate(module_id)
    return modules_payload(engine)


def _quit(engine: Engine) -> None:
    engine.stop()
    os._exit(0)


BLE_CONNECT_TIMEOUT = 25.0


async def _ble_connect(engine: Engine, params: dict):
    # bleak 连不存在的设备可以挂很久，超时后让界面拿到错误，别把请求吊死。
    await asyncio.wait_for(
        engine.ble_connect(str(params.get("address")), str(params.get("kind", "coyote_v3"))),
        timeout=BLE_CONNECT_TIMEOUT,
    )


def _engine_qr(engine: Engine, params: dict) -> dict:
    """取某条通道的配对二维码：文本 + PNG（base64）。没二维码就回空串。"""
    want = str(params.get("channel") or "")
    rows = engine.channel_states()
    row = next((r for r in rows if r["key"] == want and r["qr_text"]), None) \
        or next((r for r in rows if r["primary"] and r["qr_text"]), None) \
        or next((r for r in rows if r["qr_text"]), None)
    text = str(row["qr_text"]) if row else ""
    return {"channel": (row or {}).get("key", ""), "text": text, "png": qr_png_base64(text)}


def _wave_history(engine: Engine, params: dict) -> dict:
    """波形柱状图要原始采样：给出每条采样的相对时间与 A/B 分段强度。"""
    monitor = engine.wave_history(params.get("slot_id"))
    seconds = float(params.get("window_s") or 5.0)
    if monitor is None:
        return {"samples": [], "window_s": seconds}
    import time as _time

    now = _time.monotonic()
    rows = [[round(t - now, 3), list(a), list(b)] for t, a, b in monitor.window(seconds)]
    return {"samples": rows, "window_s": seconds}


async def _ble_reconnect(engine: Engine, params: dict):
    await asyncio.wait_for(
        engine.ble_reconnect_saved(str(params.get("address"))), timeout=BLE_CONNECT_TIMEOUT
    )


async def _settings_get(engine: Engine, params: dict):
    module_id = str(params.get("module_id") or "")
    spec = engine.modules.config_spec_for(module_id)
    cfg = engine.modules.settings_for(module_id)
    settings = dict(cfg)
    # 固定表（OSC 的设备参数那一类）读的是模块此刻算出来的行，不在配置文件里：
    # 存下来就成第二份状态，设备一连上 / 一改名，文件里那份立刻过期。
    derived = engine.modules.derived_rows_for(module_id)
    for key, item in (spec or {}).items():
        if isinstance(item, dict) and item.get("fixed") is True:
            rows = derived.get(str(key))
            if rows is not None:
                settings[str(key)] = rows
    return {
        "module_id": module_id,
        "spec": {k: v for k, v in (spec or {}).items() if isinstance(v, dict)},
        "settings": settings,
    }


def _settings_set(engine: Engine, params: dict):
    module_id = str(params.get("module_id") or "")
    key = str(params.get("key") or "")
    if not key:
        raise ValueError("缺少设置项 key")
    cfg = engine.modules.settings_for(module_id)
    cfg[key] = params.get("value")
    cfg.save()
    engine._log(f"模块 {module_id} 设置 {key} 已保存")
    return {"module_id": module_id, "key": key, "value": params.get("value")}


async def _market_install(engine: Engine, params: dict):
    """下载 → 重新发现 → 备好但不启用，与旧界面同序。"""
    module_id = str(params.get("module_id") or "")
    entry = params.get("entry") or engine.modules.store.entry(module_id)
    if entry is None:
        raise RuntimeError(f"市场清单里没有 {module_id}")
    dest = await asyncio.to_thread(engine.modules.store.download, module_id, entry)
    engine.modules.discover()
    await engine.modules.prepare(module_id)
    engine._log(f"模块 {module_id} 已安装（未启用），点「启动」开始运行：{dest}")
    return modules_payload(engine)


def _modules_apply_defaults(engine: Engine, params: dict) -> dict:
    """卡片上的「快捷配置」：把模块自带的默认事件流 / 按键映射套为当前配置。"""
    module_id = str(params.get("module_id") or "")
    notes = engine.modules.apply_module_default_profiles(module_id)
    for line in notes:
        engine._log(line)
    return {"notes": notes, "modules": modules_payload(engine)}


async def _modules_update(engine: Engine, params: dict):
    module_id = str(params.get("module_id") or "")
    entry = engine.modules.store.entry(module_id)
    if (engine.modules.meta(module_id) or {}).get("loaded"):
        await engine.modules.uninstall(module_id)
    return await _market_install(engine, {"module_id": module_id, "entry": entry})


async def _modules_delete(engine: Engine, params: dict):
    module_id = str(params.get("module_id") or "")
    await engine.modules.uninstall(module_id)
    engine.modules.delete_module(module_id)
    engine.modules.discover()
    return modules_payload(engine)


METHODS: dict[str, Callable[[Engine, dict], Any]] = {
    "engine.state": lambda e, p: snapshot_for(e),
    "engine.config_get": lambda e, p: config_payload(e),
    "engine.config_set": _config_set,
    "engine.connect_v4": lambda e, p: e.connect_v4(),
    "engine.connect_v3": lambda e, p: e.connect_v3(),
    "engine.connect_v4_local": lambda e, p: e.connect_v4_local(p.get("port")),
    "engine.connect_v3_local": lambda e, p: e.connect_v3_local(p.get("port")),
    "engine.disconnect": lambda e, p: e._disconnect_backend(),
    "engine.disconnect_channel": lambda e, p: e.disconnect_channel(str(p.get("channel") or "")),
    "engine.channels": lambda e, p: e.channel_states(),
    "engine.wave_history": _wave_history,
    # 每台设备的「本卡参数」：同一个 key 在不同槽位上有各自的取值。
    "engine.intensity_params": lambda e, p: {
        "slot_id": p.get("slot_id"),
        "rows": intensity_rows(e, p.get("slot_id")),
        # 加减键实际走多大一步（OVC 会并到 10 的倍数），界面别自己算。
        "step": e.device_step(e.resolve_slot(p.get("slot_id"))),
    },
    "engine.qr": lambda e, p: _engine_qr(e, p),
    "engine.local_ip": lambda e, p: {"ip": local_lan_ip()},
    "engine.select_slot": lambda e, p: e.select_slot(p.get("slot_id")),
    "engine.ble_scan": lambda e, p: e.ble_scan(float(p.get("timeout", 6.0))),
    "engine.ble_connect": _ble_connect,
    "engine.ble_reconnect_saved": _ble_reconnect,
    "engine.ble_disconnect": lambda e, p: e.ble_disconnect_device(str(p.get("slot_id"))),
    "engine.forget_device": lambda e, p: e.forget_device(str(p.get("address"))),
    "engine.set_strength": lambda e, p: e.set_strength(str(p.get("channel", "A")), int(p.get("value", 0)), p.get("slot_id")),
    "engine.add_strength": lambda e, p: e.add_strength(str(p.get("channel", "A")), int(p.get("delta", 0)), p.get("slot_id")),
    "engine.reset_strength": lambda e, p: e.reset_strength(str(p.get("channel", "A")), p.get("slot_id")),
    "engine.set_wave": lambda e, p: e.set_wave(str(p.get("channel", "A")), str(p.get("name")), p.get("slot_id")),
    "engine.clear_wave": lambda e, p: e.clear_wave(p.get("channel"), p.get("slot_id")),
    "engine.fire": lambda e, p: e.fire(p.get("slot_id"), p.get("duration_s"), p.get("channel")),
    "engine.fire_start": lambda e, p: e.fire_start(p.get("slot_id"), p.get("channel")),
    "engine.fire_stop": lambda e, p: e.fire_stop(p.get("slot_id"), p.get("channel")),
    "engine.emergency_stop": lambda e, p: e.emergency_stop(),
    "engine.zap": lambda e, p: e.zap(str(p.get("channel", "A")), float(p.get("seconds", 1.0)), p.get("slot_id")),
    "engine.set_led_color": lambda e, p: e.set_led_color(str(p.get("color")), p.get("slot_id")),
    "engine.bmtr_flip": lambda e, p: e.bmtr_flip(p.get("slot_id")),
    "engine.reset_pressure": lambda e, p: e.reset_pressure(p.get("slot_id")),
    "engine.set_intensity_param": lambda e, p: e.set_intensity_param(str(p.get("key")), p.get("value"), p.get("slot_id")),
    "engine.modules_list": lambda e, p: modules_payload(e),
    **BINDING_METHODS,
    **DETECTOR_METHODS,
    **MODULE_DEP_METHODS,
    "modules.set_enabled": _modules_enabled,
    "modules.deactivate": lambda e, p: e.modules.deactivate(str(p.get("module_id"))),
    "modules.reload": lambda e, p: e.modules.reload(str(p.get("module_id"))),
    "modules.install": lambda e, p: e.modules.install(str(p.get("module_id"))),
    "modules.prepare": lambda e, p: e.modules.prepare(str(p.get("module_id"))),
    "modules.uninstall": lambda e, p: e.modules.uninstall(str(p.get("module_id"))),
    "modules.defaults": lambda e, p: e.modules.default_profiles(str(p.get("module_id") or "")),
    "modules.apply_defaults": _modules_apply_defaults,
    "market.fetch": _market_fetch,
    **FLOW_METHODS,
    **SETTINGS_METHODS,
    **UPDATE_METHODS,
    "market.install": _market_install,
    "modules.update": _modules_update,
    "modules.delete": _modules_delete,
    "modules.settings_get": _settings_get,
    "modules.settings_set": _settings_set,
    "app.quit": lambda e, p: _quit(e),
}


async def _dispatch(engine: Engine, method: str, params: dict) -> Any:
    handler = METHODS.get(method)
    if handler is None:
        raise KeyError(f"未实现的方法：{method}")
    result = handler(engine, params or {})
    if asyncio.iscoroutine(result):
        result = await result
    return result


# --------------------------------------------------------------- 服务


class Bridge:
    def __init__(self, engine: Engine, token: str) -> None:
        self.engine = engine
        self.token = token
        self.loop = asyncio.get_running_loop()
        self.queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=512)
        self.authed: set[Any] = set()
        # 外壳重连（或启动晚于引擎）时不该看到一片空白，最近几条日志随身带上。
        self.recent_logs: deque[list] = deque(maxlen=200)
        # 更新下载/校验的进度：侧车线程里算，推给界面画进度条。
        set_publisher(self.publish)

    # 事件回调可能来自引擎线程，也可能来自循环本身，统一走 threadsafe。
    def publish(self, event: str, data: Any) -> None:
        self.loop.call_soon_threadsafe(self._offer, {"event": event, "data": data})

    def _offer(self, message: dict) -> None:
        try:
            self.queue.put_nowait(message)
        except asyncio.QueueFull:
            # 界面掉线或太慢时丢最旧的一帧，状态快照会随后补上。
            try:
                self.queue.get_nowait()
                self.queue.put_nowait(message)
            except asyncio.QueueEmpty:
                pass

    def subscribe(self) -> None:
        events = self.engine.events
        events.on("state", lambda state: self.publish("snapshot", snapshot_for(self.engine)))
        def on_log(msg: str) -> None:
            row = [time.time(), classify_log(msg), msg]
            self.recent_logs.append(row)
            self.publish("log", row)

        events.on("log", on_log)
        events.on("modules_changed", lambda *_: self.publish("modules", modules_payload(self.engine)))
        # 启用注入 / 停用收回默认配置后，配置档列表要立刻跟着变，
        # 不能等用户下一次进事件流页才重新拉到。
        events.on("profiles_changed",
                  lambda module_id="": self.publish(
                      "profiles", {"module": str(module_id or "")}))
        events.on("saved_devices", lambda *_: self.publish("config", config_payload(self.engine)))
        events.on("intensity_params", lambda *_: self.publish("config", config_payload(self.engine)))
        # 负鼠按键按下 / 松开：可视化绑定面板要亮对应那颗键，和旧界面一样。
        events.on("ovc_button", lambda sid, bit: self.publish("button", [str(sid), int(bit), 1]))
        events.on("ovc_button_up", lambda sid, bit: self.publish("button", [str(sid), int(bit), 0]))

    async def writer(self) -> None:
        while True:
            message = await self.queue.get()
            if not self.authed:
                continue
            raw = json.dumps(message, ensure_ascii=False)
            for client in list(self.authed):
                try:
                    await client.send(raw)
                except Exception:
                    self.authed.discard(client)

    async def handler(self, connection) -> None:
        authenticated = False
        try:
            async for raw in connection:
                try:
                    request = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                method = str(request.get("method") or "")
                if method != AUTH_METHOD:
                    # 取市场清单这类调用要几十秒，串行处理会让整条连接在此期间
                    # 连 engine.state 都回不了，所以每条请求各起一个任务。
                    asyncio.create_task(self._call(connection, request, lambda: authenticated))
                    continue
                params = request.get("params") or {}
                authenticated = secrets.compare_digest(str(params.get("token") or ""), self.token)
                rid = request.get("id")
                await _reply(connection, rid, authenticated,
                             None if authenticated else "令牌不匹配", authenticated)
                if authenticated:
                    self.authed.add(connection)
                    await self._seed(connection)
        except Exception:
            pass
        finally:
            self.authed.discard(connection)

    #: 只读方法答完不必回推状态，其余调用都可能改动 config 或设备状态。
    READ_ONLY = {"engine.state", "engine.config_get", "engine.modules_list",
                 "engine.ble_scan", "flow.state", "flow.vars", "flow.live",
                 "flow.dataflow",
                 "bindings.get", "detectors.state", "modules.deps",
                 "modules.readme",
                 "engine.channels", "engine.qr", "engine.local_ip", "engine.wave_history",
                 "engine.intensity_params",
                 "update.check", "update.download", "update.apply"}

    async def _call(self, connection, request: dict, is_authed) -> None:
        rid = request.get("id")
        method = str(request.get("method") or "")
        params = request.get("params") or {}
        if not is_authed():
            await _reply(connection, rid, False, "未通过 bridge.auth")
            return
        try:
            result = await _dispatch(self.engine, method, params)
            await _reply(connection, rid, True, None, result)
            if method == "market.fetch":
                # 清单是取回来的而不是引擎持有的状态，推一帧让页面不必自己留结果。
                self.publish("market", result)
            if method not in self.READ_ONLY:
                # 选波形、改参数这类写入不会自己发事件，回推一次才能让界面跟上。
                self.publish("snapshot", snapshot_for(self.engine))
                self.publish("config", config_payload(self.engine))
            if method.startswith("modules.") or method.startswith("market."):
                self.publish("modules", modules_payload(self.engine))
        except Exception as exc:
            # 界面只看到一行错误，排查要靠这里的完整栈。
            _to_stderr(traceback.format_exc())
            await _reply(connection, rid, False, f"{type(exc).__name__}: {exc}")

    async def _seed(self, connection) -> None:
        """新客户端一上线先补齐三份初始状态，避免等下一次事件才有画面。"""
        for event, data in (
            ("snapshot", snapshot_for(self.engine)),
            ("modules", modules_payload(self.engine)),
            ("config", config_payload(self.engine)),
            ("logs", list(self.recent_logs)),
        ):
            await connection.send(json.dumps({"event": event, "data": data}, ensure_ascii=False))


async def _reply(connection, rid, ok: bool, error: str | None, result: Any = None) -> None:
    body: dict = {"id": rid, "ok": ok}
    if ok:
        body["result"] = result
    else:
        body["error"] = error
    await connection.send(json.dumps(body, ensure_ascii=False))


async def run(engine: Engine, token: str, handshake_file: str = "", to_stdout: bool = False) -> None:
    bridge = Bridge(engine, token)
    bridge.subscribe()
    server = await serve(bridge.handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    writer = asyncio.create_task(bridge.writer())
    hello = json.dumps({"bridge": BRIDGE_VERSION, "port": port, "token": token})
    # 冻结成无控制台的侧车 exe 之后 stdout 不一定还在（PyInstaller 会把它接到
    # NUL），所以外壳启动时另给一个文件当作可靠的握手通道。
    if handshake_file:
        try:
            with open(handshake_file, "w", encoding="utf-8") as fp:
                fp.write(hello + "\n")
        except OSError:
            pass
    if to_stdout:
        try:
            print(hello, flush=True)
        except Exception:
            pass
    try:
        await asyncio.Event().wait()
    finally:
        writer.cancel()
        server.close()
        await server.wait_closed()


def _watch_parent(engine: Engine, parent_pid: int) -> None:
    """外壳退出后自行收尾，避免留下孤儿引擎。

    不能靠 `sys.stdin.readline()` 等父进程：那一次阻塞读会攥住 GIL，之后
    `Thread.start()` 与 `asyncio.to_thread` 全部卡死，桥看起来就是「不回消息」。
    改成轮询父进程句柄——ctypes 调用会让出 GIL。
    """
    if not parent_pid or sys.platform != "win32":
        return
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    handle = kernel32.OpenProcess(0x00100000, False, parent_pid)  # SYNCHRONIZE
    if not handle:
        return

    def worker() -> None:
        while kernel32.WaitForSingleObject(handle, 2000) == 0x00000102:  # WAIT_TIMEOUT
            continue
        try:
            engine.stop()
        finally:
            os._exit(0)

    threading.Thread(target=worker, name="bridge-parent-watch", daemon=True).start()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DGStudio 界面桥")
    parser.add_argument("--handshake", action="store_true", help="在 stdout 打印一行握手 JSON（外壳启动时使用）")
    parser.add_argument("--handshake-file", default="", help="把同一行握手 JSON 写进这个文件（冻结侧车用）")
    parser.add_argument("--parent", type=int, default=0, help="外壳进程 PID，用于父进程退出后收尾")
    args = parser.parse_args(argv)

    # 外壳按 UTF-8 读我们的 stdout，Windows 控制台默认是 GBK，这里显式改过来。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

    token = secrets.token_hex(16)
    engine = Engine()
    engine.start()
    _watch_parent(engine, args.parent or getattr(os, "getppid", lambda: 0)())
    try:
        asyncio.run(run(engine, token, args.handshake_file, args.handshake))
    except KeyboardInterrupt:
        pass
    finally:
        engine.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

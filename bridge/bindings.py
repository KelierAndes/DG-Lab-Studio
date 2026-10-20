"""负鼠按键映射的界面接口：配置档 + 按键位 + 可选动作。

映射本身归引擎（`app.Engine.binding_profiles` / `dglab/bindings.py`），这里只把
「哪几个按键位、能绑什么、当前绑了什么」序列化给界面，写入仍然整包落盘——
配置文件是一份一个 JSON，半包写下去会毁掉别的按键。
"""

from __future__ import annotations

from typing import Any

from dglab import bindings as binding_store
from dglab import keys as key_store


def _actions(engine: Any) -> list[dict]:
    """可选动作。带参数占位符的要一起给界面：OSC 那类动作绑的是路径，
    不填参数就只是半个动作。"""
    placeholders = {}
    try:
        for action in engine.modules.button_actions():
            if getattr(action, "argument_placeholder", ""):
                placeholders[str(action.key)] = str(action.argument_placeholder)
    except Exception:
        pass
    placeholders.setdefault("key", "点击此处后按下要绑定的按键")
    builtin = {key for key, _ in binding_store.BUTTON_ACTIONS}
    return [{"key": key, "label": label, "argument": placeholders.get(key, ""),
             # 界面上分两组列：设备动作一屏列不完，键盘注入与模块动作要是混在
             # 尾巴上，用户翻不到就会当成「没搬过来」。
             "builtin": key in builtin}
            for key, label in binding_store.button_actions(engine)]


def _payload(engine: Any, params: dict) -> dict[str, Any]:
    active, profiles = engine.binding_profiles()
    rows = profiles.get(active) or {}
    return {
        "active": active,
        "order": list(profiles),
        "bindings": {str(bit): str(action) for bit, action in rows.items()},
        "buttons": [{"bit": bit, "label": label} for bit, label in binding_store.OVC_BUTTON_BITS],
        "actions": _actions(engine),
        "keys": sorted({str(name) for name in key_store.KEY_NAMES.values()}),
        "missing": {str(bit): str(action)
                    for bit, action in engine.binding_missing_modules(rows).items()},
        "connected": bool(engine.get_state().connected and engine.get_state().backend == "ble"),
    }


def _write(engine: Any, rows: dict[str, str], active: str | None = None) -> dict[str, Any]:
    _, profiles = engine.binding_profiles()
    current = active or engine.binding_profiles()[0]
    profiles[current] = {str(bit): str(action) for bit, action in rows.items() if str(action)}
    engine.save_binding_profiles(current, profiles)
    return _payload(engine, {})


def get(engine: Any, params: dict) -> dict:
    return _payload(engine, params)


def set_bit(engine: Any, params: dict) -> dict:
    active, profiles = engine.binding_profiles()
    rows = dict(profiles.get(active) or {})
    bit = str(params.get("bit") or "")
    action = str(params.get("action") or "none")
    if action in ("", "none"):
        rows.pop(bit, None)
    else:
        rows[bit] = action
    return _write(engine, rows, active)


def profile_switch(engine: Any, params: dict) -> dict:
    engine.switch_binding_profile(str(params.get("name") or ""))
    return _payload(engine, {})


def profile_new(engine: Any, params: dict) -> dict:
    name = str(params.get("name") or "").strip()
    active, profiles = engine.binding_profiles()
    if not name:
        name = f"映射 {len(profiles) + 1}"
    if name in profiles:
        return _payload(engine, {}) | {"error": "已有同名配置"}
    profiles[name] = {}
    engine.save_binding_profiles(name, profiles)
    return _payload(engine, {}) | {"error": ""}


def profile_rename(engine: Any, params: dict) -> dict:
    old = str(params.get("name") or "")
    new = str(params.get("new") or "").strip()
    active, profiles = engine.binding_profiles()
    if old not in profiles or not new:
        return _payload(engine, {}) | {"error": "配置名不可用"}
    if new in profiles:
        return _payload(engine, {}) | {"error": "已有同名配置"}
    profiles[new] = profiles.pop(old)
    engine.save_binding_profiles(new if active == old else active, profiles)
    return _payload(engine, {}) | {"error": ""}


def profile_remove(engine: Any, params: dict) -> dict:
    name = str(params.get("name") or "")
    active, profiles = engine.binding_profiles()
    if name not in profiles or len(profiles) <= 1:
        return _payload(engine, {}) | {"error": "至少保留一份配置"}
    profiles.pop(name)
    engine.save_binding_profiles(active if active != name else next(iter(profiles)), profiles)
    return _payload(engine, {}) | {"error": ""}


BINDING_METHODS: dict[str, Any] = {
    "bindings.get": get,
    "bindings.set": set_bit,
    "bindings.profile_switch": profile_switch,
    "bindings.profile_new": profile_new,
    "bindings.profile_rename": profile_rename,
    "bindings.profile_remove": profile_remove,
}

"""设置页的读写门面：把界面上的开关一一对到 config 的真实位置。

界面用点号键（`connection.v4_port`）而不是配置里的嵌套路径，避免两边各写一份
路径拼接；映射集中在这里，改配置结构时只需要动这一处。
"""

from __future__ import annotations

from typing import Any

from dglab.version import APP_VERSION

# 界面键 → (写回时走的配置路径, 类型)。类型只用于把 JSON 值收成正确形状。
_STRING, _BOOL, _INT = "str", "bool", "int"
_MAPS: dict[str, tuple[tuple[str, ...], str]] = {
    "appearance.dark": (("ui", "dark"), _BOOL),
    # 画布网格与左栏抽屉是外壳自己的外观，但跟着 config.json 走：换一台机器、
    # 重装软件时，「设置」页的开关状态和界面主题一起回来。
    "appearance.grid": (("ui", "grid"), _BOOL),
    "appearance.drawer": (("ui", "drawer"), _BOOL),
    "connection.auto_reconnect": (("auto_reconnect",), _BOOL),
    "connection.v4_url": (("v4_url",), _STRING),
    "connection.v4_local": (("relay", "v4_local"), _BOOL),
    "connection.v4_port": (("relay", "v4_port"), _INT),
    "connection.v3_url": (("v3_url",), _STRING),
    "connection.v3_local": (("relay", "v3_local"), _BOOL),
    "connection.v3_port": (("relay", "v3_port"), _INT),
    "market.repo": (("modules_market", "repo"), _STRING),
    "market.mirror": (("modules_market", "mirror"), _STRING),
    "market.proxy": (("modules_market", "proxy"), _STRING),
    "market.no_proxy": (("modules_market", "no_proxy"), _BOOL),
    "log.file": (("log_to_file",), _BOOL),
    # 启动时查一次 GitHub 最新 release；有新版提示，不会自动装。
    "update.auto": (("update", "auto"), _BOOL),
}


def _read(engine: Any, path: tuple[str, ...], default: Any) -> Any:
    node: Any = engine.config
    for key in path:
        node = node.get(key) if isinstance(node, dict) else None
        if node is None:
            return default
    return node


def settings_payload(engine: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, (path, kind) in _MAPS.items():
        raw = _read(engine, path, None)
        out[key] = {
            _BOOL: lambda v: bool(v) if v is not None else True,
            _INT: lambda v: int(v) if isinstance(v, (int, float)) else 0,
            _STRING: lambda v: "" if v is None else str(v),
        }[kind](raw)
    return out


def _write(engine: Any, key: str, value: Any) -> None:
    spec = _MAPS.get(key)
    if spec is None:
        raise KeyError(f"未知设置项：{key}")
    path, kind = spec
    if kind == _BOOL:
        value = bool(value)
    elif kind == _INT:
        value = int(value)
    else:
        value = "" if value is None else str(value)
    node: Any = engine.config
    for step in path[:-1]:
        nxt = node.get(step)
        if not isinstance(nxt, dict):
            nxt = {}
            node[step] = nxt
        node = nxt
    node[path[-1]] = value


def settings_set(engine: Any, params: dict) -> dict:
    _write(engine, str(params.get("key") or ""), params.get("value"))
    engine.save_config()
    return {"settings": settings_payload(engine)}


def _info(engine: Any) -> dict[str, Any]:
    return {
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


def _config_module(engine: Any):
    manager = engine.modules
    inst = manager.instance("config_init")
    if inst is None:
        inst = manager.load("config_init")
    if inst is None:
        raise RuntimeError("初始化配置模块不可用")
    return inst


def config_save_all(engine: Any, params: dict) -> dict:
    _config_module(engine).save_all()
    return {"saved": True}


async def config_load_from(engine: Any, params: dict) -> dict:
    path = str(params.get("path") or "")
    if not path:
        raise ValueError("没有指定配置文件路径")
    inst = _config_module(engine)
    applied = inst.load_from(path)
    restarted = await inst.restart_running()
    inst.save_all()
    return {"applied": applied, "restarted": len(restarted)}


def config_export(engine: Any, params: dict) -> dict:
    path = str(params.get("path") or "")
    if not path:
        raise ValueError("没有指定导出路径")
    return {"count": _config_module(engine).export_to(path), "path": path}


def config_bundle(engine: Any, params: dict) -> str:
    # 导出对话框要先拿到内容再落盘，所以这里只返回打包好的 JSON 文本。
    import json as _json

    return _json.dumps(_config_module(engine).collect_bundle(), ensure_ascii=False, indent=2)


def config_export_default(engine: Any, params: dict) -> dict:
    # 系统保存对话框不可用时也要有个落点：写进 config/export/ 带时间戳。
    import os
    import time

    folder = os.path.join(engine.modules.config_dir, "export")
    os.makedirs(folder, exist_ok=True)
    name = time.strftime("dgstudio-config-%Y%m%d-%H%M%S.json")
    path = os.path.join(folder, name)
    count = _config_module(engine).export_to(path)
    return {"count": count, "path": path}


SETTINGS_METHODS: dict[str, Any] = {
    "settings.info": lambda e, p: _info(e),
    "settings.set": settings_set,
    "config.save_all": config_save_all,
    "config.load_from": config_load_from,
    "config.export": config_export,
    "config.bundle": config_bundle,
    "config.export_default": config_export_default,
}

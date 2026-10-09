from __future__ import annotations

import ast
import asyncio
import copy
import importlib
import importlib.util
import json
import os
import shutil
import string
import sys
import time
import traceback
from typing import Any, Callable
from concurrent.futures import Future

from module_store import ModuleStore
from dglab.expr import variables as expr_variables
from dglab.mapping import as_number
from dglab.params import input_specs

_OUTPUT_GUARD_LOGGED: set[str] = set()


def _base_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


if getattr(sys, "frozen", False):
    _exe_dir = _base_dir()
    if _exe_dir not in sys.path:
        sys.path.insert(0, _exe_dir)


def _load_json_file(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def spec_defaults(spec: dict | None) -> dict:
    out: dict[str, Any] = {}
    for key, item in (spec or {}).items():
        if isinstance(item, dict) and "default" in item:
            out[key] = copy.deepcopy(item["default"])
    return out


class JsonDict(dict):

    def __init__(self, path: str, data: dict | None = None):
        super().__init__(data or {})
        self.path = path

    def save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def __setitem__(self, key, value) -> None:
        super().__setitem__(key, value)
        self.save()

    def __delitem__(self, key) -> None:
        super().__delitem__(key)
        self.save()

    def update(self, *args, **kwargs) -> None:
        super().update(*args, **kwargs)
        self.save()

    def pop(self, key, *default):
        value = super().pop(key, *default)
        self.save()
        return value

    def setdefault(self, key, default=None):
        if key not in self:
            self[key] = default
        return self[key]

    def clear(self) -> None:
        super().clear()
        self.save()


_CLEANUP_MARKER = ".dgstudio_pending_cleanup"


def module_roots() -> list[str]:
    roots: list[str] = [os.path.join(_base_dir(), "modules")]
    out: list[str] = []
    seen: set[str] = set()
    for root in roots:
        key = os.path.normcase(os.path.realpath(root))
        if key not in seen:
            seen.add(key)
            out.append(root)
    return out


class ButtonAction:

    def __init__(self, key: str, label: str, *, argument_placeholder: str = "",
                 on_press=None, on_release=None, owner: str = ""):
        self.key = key
        self.label = label
        self.argument_placeholder = argument_placeholder
        self.on_press = on_press
        self.on_release = on_release
        self.owner = owner


class ModuleBase:

    id: str = ""
    name: str = ""
    version: str = "0.1.0"
    description: str = ""
    settings_key: str = ""

    def config_spec(self) -> dict:
        return {}

    def link_params(self) -> list[tuple[str, str]]:
        return []

    def read_params(self) -> list[tuple[str, str]]:
        return []

    def temp_specs(self) -> list[dict]:
        return []

    def on_load(self, ctx: "ModuleContext") -> None:
        pass

    def on_unload(self) -> None:
        pass

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    def is_running(self) -> bool:
        return False

    def button_actions(self) -> list:
        return []


class ModuleContext:

    def __init__(self, engine, module: ModuleBase):
        self.engine = engine
        self.events = engine.events
        self.module_id = module.id

    def log(self, msg: str) -> None:
        self.engine.events.emit("log", f"[{self.module_id}] {msg}")

    def submit(self, coro) -> Future:
        return self.engine.submit(coro)

    def get_state(self):
        return self.engine.get_state()

    def devices(self) -> list[dict]:
        return self.engine.devices()

    def resolve_slot(self, slot_id: str | None = None, family: str | None = None,
                     output_only: bool = False) -> str | None:
        return self.engine.resolve_slot(slot_id, family, output_only)

    @property
    def settings(self) -> dict:
        return self.engine.modules.settings_for(self.module_id)

    def strength(self, slot_id: str | None = None, channel: str = "A") -> int:
        state = self.get_state()
        sid = self.resolve_slot(slot_id)
        slot = state.slots.get(sid) if sid else None
        return int(slot.strength.get(channel, 0)) if slot else 0

    def strength_limit(self, slot_id: str | None = None, channel: str = "A") -> int:
        state = self.get_state()
        sid = self.resolve_slot(slot_id)
        slot = state.slots.get(sid) if sid else None
        return int(slot.strength_limit.get(channel, 200)) if slot else 200

    def set_strength(self, channel: str, value: int, slot_id: str | None = None):
        return self._guard("set_strength")

    def add_strength(self, channel: str, delta: int, slot_id: str | None = None):
        return self._guard("add_strength")

    def reset_strength(self, channel: str, slot_id: str | None = None):
        return self._guard("reset_strength")

    def set_wave(self, channel: str, name: str, slot_id: str | None = None):
        return self._guard("set_wave")

    def push_pulse_stream(self, frequency: int, channel: str = "A", level: int = 100,
                          slot_id: str | None = None):
        return self._guard("push_pulse_stream")

    def wave_order(self, family: str = "COYOTE") -> list[str]:
        from dglab.waves import wave_order
        return wave_order(family)

    def wave_selection(self) -> dict:
        return self.engine.wave_selection()

    def intensity_params(self, slot_id: str | None = None) -> dict:
        return self.engine.intensity_params(slot_id)

    def set_intensity_param(self, key: str, value, slot_id: str | None = None) -> None:
        self._guard("set_intensity_param")

    def device_setting(self, slot_id: str | None, key: str):
        return self.engine.device_setting(slot_id, key)

    def device_step(self, slot_id: str | None = None) -> int:
        return self.engine.device_step(slot_id)

    def fire(self, slot_id: str | None = None, duration_s: float | None = None,
             channel: str | None = None):
        return self._guard("fire")

    def fire_start(self, slot_id: str | None = None, channel: str | None = None):
        return self._guard("fire_start")

    def fire_stop(self, slot_id: str | None = None, channel: str | None = None):
        return self._guard("fire_stop")

    def zap(self, channel: str, seconds: float = 1.0, slot_id: str | None = None):
        return self._guard("zap")

    def emergency_stop(self):
        return self.engine.emergency_stop()

    def _guard(self, what: str):
        """设备动作只能由事件流的写入卡片驱动：模块直写一律拦下并写日志。"""
        key = f"{self.module_id}:{what}"
        if key not in _OUTPUT_GUARD_LOGGED:
            _OUTPUT_GUARD_LOGGED.add(key)
            self.log(f"已拦截模块直写设备输出：{what}() —— 模块只登记变量，"
                     "设备动作请在事件流里用写入卡片驱动")
        return None


    def set_temp(self, key: str, value) -> None:
        self.engine.modules.set_temp(self.module_id, key, value)

    def get_temp(self, key: str, default: float = 0.0) -> float:
        return self.engine.modules.get_temp(self.module_id, key, default)


    def game_mods_dir(self) -> str | None:
        return self.engine.modules.module_mods_dir(self.module_id)

    def scan_game_roots(self, *, roots: list[str] | None = None,
                        max_depth: int = 3) -> list[str]:
        mods = ((self.engine.modules.meta(self.module_id) or {})
                .get("mods") or {})
        marker = str(mods.get("marker") or "")
        if not marker:
            return []
        return self.engine.modules.scan_game_roots(
            marker, roots=roots, max_depth=max_depth)

    def install_game_mod(self, game_root: str) -> int:
        return self.engine.modules.install_game_mod(self.module_id, game_root)


class PluginManager:

    def __init__(self, engine):
        self.engine = engine
        self._paths: dict[str, str] = {}
        self._meta: dict[str, dict] = {}
        self._instances: dict[str, ModuleBase] = {}
        self._ctxs: dict[str, ModuleContext] = {}
        self._button_actions: dict[str, ButtonAction] = {}
        self._settings_cache: dict[str, JsonDict] = {}
        self._temps: dict[str, float] = {}
        main_dir = os.path.dirname(os.path.abspath(getattr(engine.config, "path",
                                                           _base_dir())))
        self.config_dir = os.path.join(main_dir, "config")
        os.makedirs(self.config_dir, exist_ok=True)
        self._modules_state = JsonDict(
            os.path.join(self.config_dir, "modules.json"),
            _load_json_file(os.path.join(self.config_dir, "modules.json")))
        self.store = ModuleStore(self)
        self.discover()
        self._migrate_from_main_config()
        self._refresh_enabled_flags()
        self.ensure_all_configs()

    @property
    def base_dir(self) -> str:
        return module_roots()[-1]

    def module_dir(self, module_id: str) -> str | None:
        path = self._paths.get(module_id)
        return os.path.dirname(path) if path else None

    def module_mods_dir(self, module_id: str) -> str | None:
        module_dir = self.module_dir(module_id)
        if not module_dir:
            return None
        mods = os.path.join(module_dir, "mods")
        try:
            if os.path.isdir(mods) and any(
                    os.path.isfile(os.path.join(mods, name))
                    for name in os.listdir(mods)):
                return mods
        except OSError:
            pass
        return None

    def module_deps_dir(self, module_id: str) -> str:
        module_dir = self.module_dir(module_id)
        return os.path.join(module_dir, "_deps") if module_dir else ""

    def _attach_deps_path(self, module_id: str) -> None:
        deps = self.module_deps_dir(module_id)
        if deps and os.path.isdir(deps) and deps not in sys.path:
            sys.path.insert(0, deps)

    def _detach_deps_path(self, module_id: str) -> None:
        deps = self.module_deps_dir(module_id)
        if not deps:
            return
        key = os.path.normcase(deps)
        sys.path[:] = [p for p in sys.path if os.path.normcase(p) != key]

    def _purge_module_cache(self, module_id: str) -> None:
        package = f"modules.{module_id}"
        stale = [name for name in list(sys.modules)
                 if name == package or name.startswith(package + ".")
                 or name == f"dgstudio_module_{module_id}_plugin"]
        for name in stale:
            sys.modules.pop(name, None)
        parent = sys.modules.get("modules")
        if parent is not None:
            try:
                delattr(parent, module_id)
            except AttributeError:
                pass
        module_dir = self.module_dir(module_id)
        if module_dir:
            for cur, dirs, _names in os.walk(module_dir):
                if "__pycache__" in dirs:
                    shutil.rmtree(os.path.join(cur, "__pycache__"),
                                  ignore_errors=True)
                    dirs.remove("__pycache__")

    def install_game_mod(self, module_id: str, game_root: str) -> int:
        mods_cfg = dict((self.meta(module_id) or {}).get("mods") or {})
        dest_rel = str(mods_cfg.get("dest") or "").strip("/\\")
        marker = str(mods_cfg.get("marker") or "").strip()
        mods_dir = self.module_mods_dir(module_id)
        module_dir = self.module_dir(module_id)
        vendor_dir = os.path.join(module_dir, "vendor") if module_dir else ""
        if not dest_rel or not mods_dir:
            raise ValueError("该模块未携带游戏模组（META[\"mods\"] / mods/ 目录）")
        if not os.path.isdir(os.path.join(game_root, "BepInEx")):
            looks_like_game = (
                (bool(marker) and os.path.isfile(os.path.join(game_root,
                                                              marker)))
                or any(name.lower().endswith(".exe")
                       for name in self._safe_listdir(game_root)))
            if not looks_like_game:
                raise ValueError("目标目录不含 BepInEx，也找不到游戏主程序；"
                                 "请选择游戏根目录（含主程序 exe 的那一层）")
            count = self._install_bepinex(game_root, mods_dir, vendor_dir)
            if count == 0:
                raise ValueError("模块未携带 BepInEx 发行包"
                                 "（modules/<id>/vendor/BepInEx_win_*.zip），"
                                 "无法自动安装；请先手动安装 BepInEx 5 后重试")
            self.engine._log(f"已自动安装 BepInEx 到 {game_root}"
                             f"（{count} 个文件）")
        dest = os.path.join(game_root, *dest_rel.split("/"))
        os.makedirs(dest, exist_ok=True)
        count = 0
        for name in sorted(os.listdir(mods_dir)):
            src = os.path.join(mods_dir, name)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(dest, name))
                count += 1
        return count

    def _install_bepinex(self, game_root: str, mods_dir: str,
                         vendor_dir: str) -> int:
        bundled = os.path.join(mods_dir, "BepInEx")
        if os.path.isdir(bundled):
            files = sum(len(names) for _root, _dirs, names in
                        os.walk(bundled))
            if files:
                shutil.copytree(bundled, os.path.join(game_root, "BepInEx"),
                                dirs_exist_ok=True)
                return files
        try:
            zips = sorted(name for name in os.listdir(vendor_dir)
                          if name.startswith("BepInEx_win_")
                          and name.lower().endswith(".zip"))
        except OSError:
            return 0
        if not zips:
            return 0
        import zipfile

        with zipfile.ZipFile(os.path.join(vendor_dir, zips[0])) as zf:
            zf.extractall(game_root)
        return sum(1 for name in zf.namelist() if not name.endswith("/"))

    def _safe_listdir(self, path: str) -> list[str]:
        try:
            return os.listdir(path)
        except OSError:
            return []

    def scan_game_roots(self, marker: str, *,
                        roots: list[str] | None = None,
                        max_depth: int = 3) -> list[str]:
        marker = str(marker or "").strip().lower()
        if not marker:
            return []
        if roots is None:
            roots = [f"{drive}:\\" for drive in string.ascii_uppercase
                     if os.path.isdir(f"{drive}:\\")]
        found: list[str] = []
        seen: set[str] = set()
        stack = [(root, 0) for root in roots]
        while stack:
            cur, depth = stack.pop()
            key = os.path.normcase(os.path.realpath(cur))
            if key in seen:
                continue
            seen.add(key)
            try:
                entries = os.listdir(cur)
            except OSError:
                continue
            if any(entry.lower() == marker for entry in entries):
                found.append(cur)
                continue
            if depth >= max_depth:
                continue
            for entry in entries:
                path = os.path.join(cur, entry)
                if entry.startswith((".", "$")) or not os.path.isdir(path):
                    continue
                stack.append((path, depth + 1))
        return found

    def _settings_stem(self, module_id: str) -> str:
        inst = self._instances.get(module_id)
        if inst is not None and getattr(inst, "settings_key", ""):
            return str(inst.settings_key)
        meta = self._meta.get(module_id) or {}
        return str(meta.get("settings_key") or module_id)

    def settings_for(self, module_id: str) -> JsonDict:
        cached = self._settings_cache.get(module_id)
        if cached is not None:
            return cached
        path = os.path.join(self.config_dir, f"{self._settings_stem(module_id)}.json")
        settings = JsonDict(path, _load_json_file(path))
        self._apply_spec_defaults(module_id, settings)
        self._purge_legacy_tables(module_id, settings)
        self._purge_stale_event_actions(module_id, settings)
        self._settings_cache[module_id] = settings
        return settings

    def _purge_stale_event_actions(self, module_id: str, settings: JsonDict) -> None:
        events = [e for e in (settings.get("events") or [])
                  if isinstance(e, dict)]
        if not events:
            return
        valid = input_specs()
        changed = False
        for card in events:
            actions = [a for a in (card.get("actions") or [])
                       if isinstance(a, dict)]
            kept = [a for a in actions
                    if not (str(a.get("dir") or "in") == "in"
                            and str(a.get("param") or "") not in valid)]
            if len(kept) != len(actions):
                changed = True
                card["actions"] = kept
        if changed:
            settings["events"] = events
            settings.save()
            self.engine._log(
                f"模块 {module_id} 事件流中引用已下线核心参数的动作已清除")

    def _purge_legacy_tables(self, module_id: str, settings: JsonDict) -> None:
        mappings = [r for r in (settings.get("mappings") or [])
                    if isinstance(r, dict)]
        outputs = [r for r in (settings.get("outputs") or [])
                   if isinstance(r, dict)]
        temps = [r for r in (settings.get("temps") or []) if isinstance(r, dict)]
        events = [r for r in (settings.get("events") or [])
                  if isinstance(r, dict)]

        stale = [e for e in events
                 if str(e.get("name") or "") == "输入映射（迁移）"]
        if stale:
            events = [e for e in events
                      if str(e.get("name") or "") != "输入映射（迁移）"]
            candidates = {str(t.get("name") or "") for t in temps
                          if str(t.get("name") or "").startswith("map_")}
            referenced = set()
            for e in events:
                for a in (e.get("actions") or []):
                    if isinstance(a, dict):
                        referenced.add(str(a.get("var") or ""))
                        referenced.add(str(a.get("param") or ""))
                arg = e.get("arg")
                if str(e.get("trigger")) == "change" and arg:
                    referenced.add(str(arg))
                if str(e.get("trigger")) == "if" and arg:
                    referenced |= set(expr_variables(str(arg)))
            for t in temps:
                name = str(t.get("name") or "")
                if name in candidates:
                    continue
                if name:
                    referenced.add(name)
                referenced |= set(expr_variables(str(t.get("expr") or "")))
            temps = [t for t in temps
                     if str(t.get("name") or "") not in candidates
                     or str(t.get("name") or "") in referenced]

        dirty = "mappings" in settings or "outputs" in settings or bool(stale)
        if not dirty:
            return
        settings.pop("mappings", None)
        settings.pop("outputs", None)
        if stale:
            settings["temps"] = temps
            settings["events"] = events
        settings.save()
        if mappings or outputs or stale:
            parts = []
            if mappings:
                parts.append(f"清除遗留输入映射 {len(mappings)} 行")
            if outputs:
                parts.append(f"清除遗留输出映射 {len(outputs)} 行"
                             "（回传字段请在事件流以「输出」动作重建）")
            if stale:
                parts.append(f"清理自动迁移产物 {len(stale)} 张卡片")
            self.engine._log(f"模块 {module_id} 映射表时代设置项已清除："
                             + "；".join(parts))

    def config_spec_for(self, module_id: str) -> dict:
        inst = self._instances.get(module_id)
        if inst is not None:
            try:
                spec = inst.config_spec()
            except Exception:
                spec = None
            if isinstance(spec, dict) and spec:
                return spec
        meta = self._meta.get(module_id) or {}
        spec = meta.get("config")
        return spec if isinstance(spec, dict) else {}

    def _apply_spec_defaults(self, module_id: str, settings: JsonDict) -> bool:
        spec = self.config_spec_for(module_id)
        missing: dict[str, Any] = {}
        nested_changed = False
        for key, item in spec.items():
            if not isinstance(item, dict) or "default" not in item:
                continue
            default = copy.deepcopy(item["default"])
            current = settings.get(key)
            if key not in settings:
                missing[key] = default
            elif isinstance(default, dict) and isinstance(current, dict):
                for sub_key, sub_value in default.items():
                    if sub_key not in current:
                        current[sub_key] = copy.deepcopy(sub_value)
                        nested_changed = True
        if missing:
            dict.update(settings, missing)
        if missing or nested_changed:
            settings.save()
        return bool(missing) or nested_changed

    def ensure_all_configs(self) -> list[str]:
        touched: list[str] = []
        for module_id in sorted(self._paths):
            cached = self._settings_cache.get(module_id)
            if cached is not None:
                continue
            path = os.path.join(self.config_dir, f"{self._settings_stem(module_id)}.json")
            settings = JsonDict(path, _load_json_file(path))
            if self._apply_spec_defaults(module_id, settings):
                touched.append(module_id)
            self._purge_legacy_tables(module_id, settings)
            self._purge_stale_event_actions(module_id, settings)
            self._settings_cache[module_id] = settings
        return touched

    def _migrate_from_main_config(self) -> None:
        cfg = self.engine.config
        changed = False

        def write_settings(stem: str, data: dict) -> None:
            if not isinstance(data, dict) or not data:
                return
            path = os.path.join(self.config_dir, f"{stem}.json")
            merged = _load_json_file(path)
            merged.update(data)
            JsonDict(path, merged).save()

        mods_section = cfg.get("modules")
        if isinstance(mods_section, dict):
            enabled = self._enabled_map()
            for mid, value in (mods_section.get("enabled") or {}).items():
                enabled.setdefault(str(mid), bool(value))
            for mid, data in (mods_section.get("settings") or {}).items():
                write_settings(str(mid), data if isinstance(data, dict) else {})
            cfg.pop("modules", None)
            changed = True

        for module_id in list(self._paths):
            stem = self._settings_stem(module_id)
            old = cfg.get(stem)
            if not isinstance(old, dict) or not old:
                continue
            if "enabled" in old:
                self._enabled_map().setdefault(module_id, bool(old["enabled"]))
                old = {k: v for k, v in old.items() if k != "enabled"}
            write_settings(stem, old)
            cfg.pop(stem, None)
            changed = True

        if changed:
            self._modules_state.save()
            self.engine.config.save()
            self._settings_cache.clear()
            self.engine._log("已将模块配置从 config.json 拆分到 config/ 目录")

    def discover(self) -> list[dict]:
        self._sweep_pending_deletes()
        self._paths.clear()
        self._meta.clear()
        for root in module_roots():
            try:
                entries = sorted(os.listdir(root))
            except OSError:
                continue
            for entry in entries:
                folder = os.path.join(root, entry)
                plugin_py = os.path.join(folder, "plugin.py")
                if not os.path.isfile(plugin_py):
                    continue
                meta = _read_meta(plugin_py)
                module_id = str(meta.get("id") or entry)
                self._paths[module_id] = plugin_py
                self._meta[module_id] = {
                    "id": module_id,
                    "name": str(meta.get("name") or module_id),
                    "version": str(meta.get("version") or "0.1.0"),
                    "description": str(meta.get("description") or ""),
                    "settings_key": str(meta.get("settings_key") or ""),
                    "actions": [str(a) for a in (meta.get("actions") or [])],
                    "config": dict(meta.get("config") or {}),
                    "params": dict(meta.get("params") or {}),
                    "reads": dict(meta.get("reads") or {}),
                    "temps": [dict(t) for t in (meta.get("temps") or [])
                              if isinstance(t, dict)],
                    "mods": dict(meta.get("mods") or {}),
                    "dependencies": [str(d) for d in
                                     (meta.get("dependencies") or [])],
                    "dynamic_params": bool(meta.get("dynamic_params", False)),
                    "realtime_manager": bool(meta.get("realtime_manager",
                                                      False)),
                    "default_enabled": bool(meta.get("default_enabled", False)),
                    "loaded": module_id in self._instances,
                    "running": (self._instances[module_id].is_running()
                                if module_id in self._instances else False),
                    "enabled": False,
                }
        for module_id, inst in self._instances.items():
            if module_id in self._meta:
                self._meta[module_id]["loaded"] = True
                self._meta[module_id]["running"] = inst.is_running()
        self._refresh_enabled_flags()
        self.ensure_all_configs()
        return self.list_modules()

    def _refresh_enabled_flags(self) -> None:
        for module_id, entry in self._meta.items():
            entry["enabled"] = self.is_enabled(module_id)

    def list_modules(self) -> list[dict]:
        return [dict(self._meta[k]) for k in sorted(self._meta)]

    def meta(self, module_id: str) -> dict | None:
        return self._meta.get(module_id)

    def instance(self, module_id: str) -> ModuleBase | None:
        return self._instances.get(module_id)

    def register_instance(self, module_id: str, instance: ModuleBase | None) -> None:
        if instance is None:
            self._instances.pop(module_id, None)
            self._button_actions = {key: action for key, action
                                    in self._button_actions.items()
                                    if action.owner != module_id}
        else:
            if not getattr(instance, "id", ""):
                instance.id = module_id
            self._instances[module_id] = instance
            self._button_actions = {key: action for key, action
                                    in self._button_actions.items()
                                    if action.owner != module_id}
            self._register_actions(module_id, instance)
        if module_id in self._meta:
            self._meta[module_id]["loaded"] = instance is not None
            self._meta[module_id]["running"] = bool(instance.is_running()) if instance else False

    def _enabled_map(self) -> dict:
        return self._modules_state.setdefault("enabled", {})

    def is_enabled(self, module_id: str) -> bool:
        enabled = self._enabled_map()
        if module_id in enabled:
            return bool(enabled[module_id])
        meta = self._meta.get(module_id) or {}
        return bool(meta.get("default_enabled", False))

    def set_enabled(self, module_id: str, value: bool) -> None:
        self._enabled_map()[module_id] = bool(value)
        self._modules_state.save()
        if module_id in self._meta:
            self._meta[module_id]["enabled"] = bool(value)

    def button_actions(self) -> list[ButtonAction]:
        return list(self._button_actions.values())

    def action(self, key: str) -> ButtonAction | None:
        return self._button_actions.get(key)

    def module_for_action(self, key: str) -> str | None:
        action = self._button_actions.get(key)
        if action is not None:
            return action.owner
        for module_id, meta in self._meta.items():
            if key in meta.get("actions", []):
                return module_id
        return None

    def load(self, module_id: str) -> ModuleBase:
        inst = self._instances.get(module_id)
        if inst is not None:
            return inst
        plugin_py = self._paths.get(module_id)
        if not plugin_py:
            raise RuntimeError(f"未知模块: {module_id}")
        self._attach_deps_path(module_id)
        try:
            cls = _load_plugin_class(module_id, plugin_py)
        except ImportError:
            self.engine._log(
                f"模块 {module_id} 装载失败（可能缺依赖，"
                f"请在模块页「安装并启动」自动补装）:\n{traceback.format_exc()}")
            raise
        inst = cls()
        if not getattr(inst, "id", ""):
            inst.id = module_id
        ctx = ModuleContext(self.engine, inst)
        self._ctxs[module_id] = ctx
        inst.on_load(ctx)
        self._instances[module_id] = inst
        self._drop_module_temps(module_id)
        if module_id in self._meta:
            self._meta[module_id]["loaded"] = True
        self._register_actions(module_id, inst)
        self.engine._log(f"模块已加载: {inst.name or module_id} v{inst.version}")
        self.engine.events.emit("modules_changed", module_id)
        return inst

    def _unregister_actions(self, module_id: str) -> None:
        self._button_actions = {key: action for key, action
                                in self._button_actions.items()
                                if action.owner != module_id}

    def _register_actions(self, module_id: str, inst: ModuleBase) -> None:
        hook = getattr(inst, "button_actions", None)
        if not callable(hook):
            return
        try:
            actions = hook() or []
        except Exception:
            self.engine._log(f"模块 {module_id} button_actions() 失败:\n"
                             f"{traceback.format_exc()}")
            return
        for action in actions:
            action.owner = module_id
            existing = self._button_actions.get(action.key)
            if existing is not None and existing.owner != module_id:
                self.engine._log(f"模块 {module_id} 的按键动作 {action.key} "
                                 f"已被模块 {existing.owner} 注册，忽略重复项")
                continue
            self._button_actions[action.key] = action

    async def unload(self, module_id: str) -> None:
        inst = self._instances.get(module_id)
        if inst is None:
            return
        await self._stop_instance(inst)
        try:
            inst.on_unload()
        except Exception:
            self.engine._log(f"模块 {module_id} 卸载清理失败:\n{traceback.format_exc()}")
        self._drop_module_temps(module_id)
        self._instances.pop(module_id, None)
        self._ctxs.pop(module_id, None)
        self._unregister_actions(module_id)
        if module_id in self._meta:
            self._meta[module_id]["loaded"] = False
            self._meta[module_id]["running"] = False
        self._purge_module_cache(module_id)
        self._detach_deps_path(module_id)
        self.engine._log(f"模块已卸载: {inst.name or module_id}")
        self.engine.events.emit("modules_changed", module_id)

    async def start(self, module_id: str) -> None:
        inst = self.load(module_id)
        self._register_actions(module_id, inst)
        await inst.start()
        self.apply_logic_tables(module_id)
        if module_id in self._meta:
            self._meta[module_id]["running"] = inst.is_running()
        self.engine.events.emit("modules_changed", module_id)

    async def stop(self, module_id: str) -> None:
        inst = self._instances.get(module_id)
        if inst is None:
            return
        await self._stop_instance(inst)
        if module_id in self._meta:
            self._meta[module_id]["running"] = False

    async def deactivate(self, module_id: str) -> None:
        self._unregister_actions(module_id)
        self.set_enabled(module_id, False)
        await self.stop(module_id)
        self.engine.events.emit("modules_changed", module_id)


    def _mapping_engine(self, module_id: str):
        inst = self._instances.get(module_id)
        runtime = getattr(inst, "bridge", None) or getattr(inst, "server", None)
        return getattr(runtime, "engine", None) if runtime is not None else None

    def temp_specs_for(self, module_id: str) -> list[dict]:
        inst = self._instances.get(module_id)
        hook = getattr(inst, "temp_specs", None)
        if callable(hook):
            try:
                specs = hook()
                if specs:
                    return [dict(s) for s in specs if isinstance(s, dict)]
            except Exception:
                self.engine._log(f"模块 {module_id} temp_specs() 失败:\n"
                                 f"{traceback.format_exc()}")
        return [dict(s) for s in ((self._meta.get(module_id) or {})
                                  .get("temps") or [])]

    def temps_space(self, module_id: str = "") -> dict[str, float]:
        """全局共享的临时变量表：事件流卡片与所有模块读写同一命名空间。"""
        return self._temps

    def _declared_temp_names(self, module_id: str) -> set[str]:
        names: set[str] = set()
        for spec in self.temp_specs_for(module_id):
            for field in ("name", "key"):
                value = str(spec.get(field) or "").strip()
                if value:
                    names.add(value)
        return names

    def _drop_module_temps(self, module_id: str) -> None:
        for name in self._declared_temp_names(module_id):
            self._temps.pop(name, None)

    def set_temp(self, module_id: str, key: str, value) -> None:
        num = as_number(value)
        if num is None:
            return
        self._temps[str(key)] = num
        eng = self._mapping_engine(module_id)
        if eng is not None and getattr(eng, "temps", None) is not None:
            eng.pump()

    def get_temp(self, module_id: str, key: str, default: float = 0.0) -> float:
        return float(self._temps.get(str(key), default))

    def apply_logic_tables(self, module_id: str) -> None:
        eng = self._mapping_engine(module_id)
        if eng is not None and hasattr(eng, "attach_temps"):
            eng.attach_temps(self.temps_space())

    async def reload(self, module_id: str) -> None:
        inst = self._instances.get(module_id)
        if inst is None:
            return
        fn = getattr(inst, "reload_config", None)
        if fn is not None:
            await fn()
        self.apply_logic_tables(module_id)

    async def _stop_instance(self, inst: ModuleBase) -> None:
        try:
            await inst.stop()
        except Exception:
            self.engine._log(f"模块 {inst.id} 停止失败:\n{traceback.format_exc()}")

    async def _ensure_dependencies(self, module_id: str) -> None:
        requirements, source = self.store.requirements_of(module_id)
        if not requirements:
            return
        self.engine._log(f"模块 {module_id} 正在检查依赖（{source}）…")

        def _pip_log(line: str) -> None:
            self.engine._log(f"[pip] {line}")

        ok, still, output = await asyncio.to_thread(
            self.store.ensure_dependencies, module_id, log=_pip_log)
        if still or not ok:
            tail = "\n".join(output.strip().splitlines()[-8:]) or output
            names = "、".join(still) or "部分依赖安装命令失败"
            raise RuntimeError(f"依赖安装失败（{names}）\n{tail}")
        self.engine._log(f"模块 {module_id} 依赖就绪")

    async def install(self, module_id: str) -> None:
        await self._ensure_dependencies(module_id)
        self.set_enabled(module_id, True)
        await self.start(module_id)

    async def prepare(self, module_id: str) -> None:
        """下载后自动装载但不启用：依赖就绪并加载实例，不 start、不置开机自启。"""
        await self._ensure_dependencies(module_id)
        self.set_enabled(module_id, False)
        self.load(module_id)

    async def uninstall(self, module_id: str) -> None:
        self.set_enabled(module_id, False)
        await self.unload(module_id)

    def delete_module(self, module_id: str) -> None:
        if module_id in self._instances:
            raise RuntimeError(f"模块 {module_id} 正在运行，请先卸载")
        module_dir = self.module_dir(module_id)
        if not module_dir:
            raise RuntimeError(f"未知模块: {module_id}")
        if os.path.normcase(os.path.realpath(module_dir)) in (
                os.path.normcase(os.path.realpath(root))
                for root in module_roots()):
            raise RuntimeError("拒绝删除模块根目录")
        last_exc: OSError | None = None
        for attempt in range(3):
            try:
                shutil.rmtree(module_dir)
                last_exc = None
                break
            except OSError as exc:
                last_exc = exc
                if attempt < 2:
                    time.sleep(0.8)
        if last_exc is not None:
            try:
                os.rename(module_dir, module_dir + ".pending_delete")
                last_exc = None
                self.engine._log(
                    f"模块 {module_id} 的部分文件被运行中的应用占用"
                    "（已载入的扩展保留到进程退出），已整体移入 "
                    ".pending_delete 隔离区，下次启动自动清理")
            except OSError:
                stuck = self._quarantine_locked_files(module_dir)
                self.engine._log(
                    f"模块 {module_id} 有文件被系统占用无法删除（{last_exc}），"
                    f"{stuck} 个已挽救至隔离区，残余文件已标记下次启动自动清理")
                self._mark_auto_cleanup(module_dir)
        self.discover()
        self.engine._log(f"模块文件已删除: {module_id}")
        self.engine.events.emit("modules_changed", module_id)

    def _quarantine_locked_files(self, module_dir: str) -> int:
        trash = module_dir + ".pending_delete"
        stuck = 0
        for cur, dirs, names in os.walk(module_dir, topdown=False):
            rel = os.path.relpath(cur, module_dir)
            target_root = trash if rel == "." else os.path.join(trash, rel)
            try:
                os.makedirs(target_root, exist_ok=True)
            except OSError:
                stuck += len(names)
                continue
            for name in names:
                src = os.path.join(cur, name)
                try:
                    os.remove(src)
                    continue
                except OSError:
                    pass
                dst = os.path.join(target_root, name)
                serial = 0
                while os.path.lexists(dst):
                    serial += 1
                    dst = os.path.join(target_root, f"{serial}_{name}")
                try:
                    os.rename(src, dst)
                except OSError:
                    stuck += 1
            for name in dirs:
                try:
                    os.rmdir(os.path.join(cur, name))
                except OSError:
                    pass
        try:
            os.rmdir(module_dir)
        except OSError:
            pass
        return stuck

    def _mark_auto_cleanup(self, module_dir: str) -> None:
        try:
            with open(os.path.join(module_dir, _CLEANUP_MARKER), "w",
                      encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S"))
        except OSError:
            pass

    def _sweep_pending_deletes(self) -> None:
        for root in module_roots():
            try:
                entries = os.listdir(root)
            except OSError:
                continue
            for entry in entries:
                path = os.path.join(root, entry)
                if entry.endswith(".pending_delete"):
                    shutil.rmtree(path, ignore_errors=True)
                elif os.path.isdir(path) and os.path.isfile(
                        os.path.join(path, _CLEANUP_MARKER)):
                    shutil.rmtree(path, ignore_errors=True)
                    if os.path.isdir(path):
                        try:
                            with open(os.path.join(path, _CLEANUP_MARKER),
                                      "w", encoding="utf-8") as f:
                                f.write(time.strftime("%Y-%m-%d %H:%M:%S"))
                        except OSError:
                            pass

    async def autostart(self) -> None:
        for module_id in list(self._paths):
            if not self.is_enabled(module_id):
                continue
            try:
                await self.start(module_id)
            except Exception:
                self.engine._log(f"模块 {module_id} 自启动失败:\n{traceback.format_exc()}")


def _read_meta(plugin_py: str) -> dict:
    try:
        with open(plugin_py, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read())
    except (OSError, SyntaxError):
        return {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "META":
                try:
                    value = ast.literal_eval(node.value)
                    return value if isinstance(value, dict) else {}
                except (ValueError, SyntaxError):
                    return {}
    return {}


def _load_plugin_class(module_id: str, plugin_py: str) -> type[ModuleBase]:
    from plugins import ModuleBase as _Base

    module = None
    package_path = os.path.join(os.path.dirname(plugin_py), "__init__.py")
    if os.path.isfile(package_path):
        try:
            module = importlib.import_module(f"modules.{module_id}.plugin")
        except ImportError:
            module = None
    if module is None:
        name = f"dgstudio_module_{module_id}_plugin"
        spec = importlib.util.spec_from_file_location(name, plugin_py)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"无法加载模块文件: {plugin_py}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)

    def _is_module_class(attr) -> bool:
        if not isinstance(attr, type) or attr.__module__ != module.__name__:
            return False
        if attr is _Base:
            return False
        if issubclass(attr, _Base):
            return True
        protocol = ("id", "name", "on_load", "on_unload")
        return all(hasattr(attr, name) for name in protocol)

    cls = None
    for attr in vars(module).values():
        if _is_module_class(attr):
            cls = attr
            break
    if cls is None:
        raise RuntimeError(f"模块 {module_id} 未定义 ModuleBase 子类")

    if not issubclass(cls, _Base):
        if not hasattr(cls, "start"):
            cls.start = _noop
        if not hasattr(cls, "stop"):
            cls.stop = _noop
        if not hasattr(cls, "is_running"):
            cls.is_running = lambda self: False
    return cls


async def _noop(self) -> None:
    pass


__all__ = ["ButtonAction", "ModuleBase", "ModuleContext", "PluginManager", "spec_defaults"]

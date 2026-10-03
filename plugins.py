"""联动模块（插件）宿主：发现、加载、启停与卸载。

模块放置于应用目录 modules/<module_id>/ 下，至少包含一个 plugin.py：
声明模块级 META 字典并定义 ModuleBase 的子类。宿主负责把引擎的公开
命令层（强度参数、波形、开火、急停等）通过 ModuleContext 提供给模块。

模块设置独立于主 config.json，存放于 config/<模块>.json（与主配置同目录），
由宿主读写并自动落盘；启用/自启动状态存于 config/modules.json。
旧版主配置中的模块段会在首次运行时自动迁移。联动模块已外置到
dgstudio-modules-market 仓库（模块页按需下载），模块开发文档见该仓库 EXTENSIONS.md。
"""
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
import traceback
from typing import Any, Callable
from concurrent.futures import Future

from module_store import ModuleStore


def _base_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


if getattr(sys, "frozen", False):
    _exe_dir = _base_dir()
    if _exe_dir not in sys.path:
        # modules/ 提升到 exe 同级后，模块的包导入（modules.<id>.server）从 exe 旁解析
        sys.path.insert(0, _exe_dir)


def _load_json_file(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def spec_defaults(spec: dict | None) -> dict:
    """从配置项声明（键 → {default: ...}）提取扁平缺省值表。"""
    out: dict[str, Any] = {}
    for key, item in (spec or {}).items():
        if isinstance(item, dict) and "default" in item:
            out[key] = copy.deepcopy(item["default"])
    return out


class JsonDict(dict):
    """写穿式字典：任何顶层变更立即保存到对应 JSON 文件。

    嵌套字典的就地修改不会触发保存（模块可显式调用 save()）。
    """

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


def module_roots() -> list[str]:
    """模块扫描根列表：modules/ 与 exe 同级（打包后由 build_exe.py 复制到产物根）。"""
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
    """模块提供的负鼠按键绑定动作。

    绑定值格式为 "<key>" 或带参数的 "<key>:<参数>"（如 osc:/avatar/…）。
    模块加载时动作进入注册表（绑定选择框出现该项），卸载时撤下。
    """

    def __init__(self, key: str, label: str, *, argument_placeholder: str = "",
                 on_press=None, on_release=None, owner: str = ""):
        self.key = key
        self.label = label
        self.argument_placeholder = argument_placeholder
        self.on_press = on_press
        self.on_release = on_release
        self.owner = owner


class ModuleBase:
    """联动模块基类：子类放在 modules/<id>/plugin.py 中并由宿主实例化。"""

    id: str = ""
    name: str = ""
    version: str = "0.1.0"
    description: str = ""
    settings_key: str = ""

    def config_spec(self) -> dict:
        """声明本模块配置项：键 → {label, type, default, group, ...}。

        宿主在装载 config/<模块>.json 时按声明自动补齐缺失项并落盘，
        联动/设置等页面据此自动生成编辑控件。缺省实现返回空字典，
        此时宿主改用 plugin.py 中模块级 META["config"] 静态声明。
        """
        return {}

    def link_params(self) -> list[tuple[str, str]]:
        """模块侧可写参数表 ``(变量名, 说明)``，供联动页表达式变量池使用。

        可写参数是模块喂进核心信号空间的命名数值（MOD 上报、OSC 收包），
        可在输入映射表表达式中以 ``{名称}`` 引用。常规模块返回
        META["params"] 声明的固定参数集；OSC 之类动态建表的模块返回
        运行期实际收到的参数名。
        """
        return []

    def read_params(self) -> list[tuple[str, str]]:
        """模块侧可读参数表 ``(信号名, 说明)``（输出映射默认字段建议）。

        可读参数是模块从核心读走并回传给对端的字段：META["reads"] 按
        「核心输出信号名 → {label, name, type}」声明，模块装载时据此为
        空输出表落地默认行，联动页输出表也以它们作字段名联想。
        """
        return []

    def on_load(self, ctx: "ModuleContext") -> None:
        """模块被加载时调用一次（注册事件、读取配置）。"""

    def on_unload(self) -> None:
        """模块被卸载时调用（必须释放已注册的事件与资源）。"""

    async def start(self) -> None:
        """启动模块运行（异步，运行在引擎事件循环上）。"""

    async def stop(self) -> None:
        """停止模块运行（异步，运行在引擎事件循环上）。"""

    def is_running(self) -> bool:
        return False

    def button_actions(self) -> list:
        """模块注册的负鼠按键绑定动作（ButtonAction 列表，可选实现）。"""
        return []


class ModuleContext:
    """交给模块的公开 API：设备状态、强度参数、命令与事件总线。

    模块只应通过本对象访问引擎；除这里列出的方法外都视为内部实现。
    """

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
        """模块私有设置（JsonDict，写操作自动保存到 config/<模块>.json）。"""
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
        return self.engine.set_strength(channel, value, slot_id=slot_id)

    def add_strength(self, channel: str, delta: int, slot_id: str | None = None):
        return self.engine.add_strength(channel, delta, slot_id=slot_id)

    def reset_strength(self, channel: str, slot_id: str | None = None):
        return self.engine.reset_strength(channel, slot_id=slot_id)

    def set_wave(self, channel: str, name: str, slot_id: str | None = None):
        return self.engine.set_wave(channel, name, slot_id=slot_id)

    def wave_order(self, family: str = "COYOTE") -> list[str]:
        from dglab.waves import wave_order
        return wave_order(family)

    def wave_selection(self) -> dict:
        return self.engine.wave_selection()

    def intensity_params(self, slot_id: str | None = None) -> dict:
        return self.engine.intensity_params(slot_id)

    def set_intensity_param(self, key: str, value, slot_id: str | None = None) -> None:
        self.engine.set_intensity_param(key, value, slot_id=slot_id)

    def device_setting(self, slot_id: str | None, key: str):
        return self.engine.device_setting(slot_id, key)

    def device_step(self, slot_id: str | None = None) -> int:
        return self.engine.device_step(slot_id)

    def fire_start(self, slot_id: str | None = None):
        return self.engine.fire_start(slot_id=slot_id)

    def fire_stop(self, slot_id: str | None = None):
        return self.engine.fire_stop(slot_id=slot_id)

    def zap(self, channel: str, seconds: float = 1.0, slot_id: str | None = None):
        return self.engine.zap(channel, seconds, slot_id=slot_id)

    def emergency_stop(self):
        return self.engine.emergency_stop()

    # ---- 游戏模组（META["mods"] + mods/ 载荷；通用接口，携带模组的模块可用） ----

    def game_mods_dir(self) -> str | None:
        """模块携带的游戏模组目录（mods/，无载荷返回 None）。"""
        return self.engine.modules.module_mods_dir(self.module_id)

    def scan_game_roots(self, *, roots: list[str] | None = None,
                        max_depth: int = 3) -> list[str]:
        """按 META["mods"]["marker"] 浅层扫描游戏根目录（默认各盘符根）。"""
        mods = ((self.engine.modules.meta(self.module_id) or {})
                .get("mods") or {})
        marker = str(mods.get("marker") or "")
        if not marker:
            return []
        return self.engine.modules.scan_game_roots(
            marker, roots=roots, max_depth=max_depth)

    def install_game_mod(self, game_root: str) -> int:
        """把携带的游戏模组释放到游戏根目录，返回写入的文件数。

        目标缺 BepInEx 时用模块携带的 ``vendor/BepInEx_win_*.zip`` 自动
        安装（mods/ 内置 BepInEx/ 目录优先）；目标不含主程序时抛
        ValueError。"""
        return self.engine.modules.install_game_mod(self.module_id, game_root)


class PluginManager:
    """扫描 modules/ 目录，负责模块的加载、启停、卸载与启用状态持久化。"""

    def __init__(self, engine):
        self.engine = engine
        self._paths: dict[str, str] = {}
        self._meta: dict[str, dict] = {}
        self._instances: dict[str, ModuleBase] = {}
        self._ctxs: dict[str, ModuleContext] = {}
        self._button_actions: dict[str, ButtonAction] = {}
        self._settings_cache: dict[str, JsonDict] = {}
        # 模块配置目录：与主 config.json 同目录的 config/ 子目录
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
        """用户模块目录（放这里的新模块可被「扫描模块目录」发现）。"""
        return module_roots()[-1]

    def module_dir(self, module_id: str) -> str | None:
        """模块文件夹（plugin.py 所在目录）。"""
        path = self._paths.get(module_id)
        return os.path.dirname(path) if path else None

    def module_mods_dir(self, module_id: str) -> str | None:
        """模块携带的游戏模组目录（modules/<id>/mods/，存在且非空才返回）。"""
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
        """模块私有依赖目录（modules/<id>/_deps/，打包运行时装依赖用）。"""
        module_dir = self.module_dir(module_id)
        return os.path.join(module_dir, "_deps") if module_dir else ""

    def _attach_deps_path(self, module_id: str) -> None:
        """把模块私有依赖目录加入 sys.path（存在才加，置于最前）。"""
        deps = self.module_deps_dir(module_id)
        if deps and os.path.isdir(deps) and deps not in sys.path:
            sys.path.insert(0, deps)

    def _detach_deps_path(self, module_id: str) -> None:
        """卸载时移除模块私有依赖目录的 sys.path 项（与 _attach 对称）。"""
        deps = self.module_deps_dir(module_id)
        if not deps:
            return
        key = os.path.normcase(deps)
        sys.path[:] = [p for p in sys.path if os.path.normcase(p) != key]

    def _purge_module_cache(self, module_id: str) -> None:
        """热重载支持：清掉该模块的全部导入缓存与 pyc，再装时按盘上代码重新导入。

        覆盖包形式（modules.<id>.plugin 及其全部子模块）与散文件形式
        （dgstudio_module_<id>_plugin），并清掉父包上的子模块属性引用。
        """
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
        """把模块携带的游戏模组释放到游戏目录（BepInEx），返回模组文件数。

        目标缺 BepInEx 时自动安装：优先合并 mods/ 自带的 ``BepInEx/`` 子目录，
        否则解压模块携带的 ``vendor/BepInEx_win_*.zip``——仅当目标目录
        看起来是游戏根目录（含 marker 主程序或任意 exe）才执行，避免污染
        随手选中的文件夹。"""
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
        """安装 BepInEx 运行时：mods/ 自带 ``BepInEx/``（非空）优先合并，
        否则解压模块携带的 ``vendor/BepInEx_win_*.zip``；两者皆无返回 0。"""
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
        """在盘符根（或指定根列表）浅层扫描包含 marker 可执行文件的游戏目录。"""
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
        """模块设置文件名（不含扩展名）：实例/META 的 settings_key 优先，缺省用 id。"""
        inst = self._instances.get(module_id)
        if inst is not None and getattr(inst, "settings_key", ""):
            return str(inst.settings_key)
        meta = self._meta.get(module_id) or {}
        return str(meta.get("settings_key") or module_id)

    def settings_for(self, module_id: str) -> JsonDict:
        """模块私有设置（加载后缓存，写操作自动落盘 config/<stem>.json）。

        装载时按模块声明（config_spec / META["config"]）自动补齐缺失项。
        """
        cached = self._settings_cache.get(module_id)
        if cached is not None:
            return cached
        path = os.path.join(self.config_dir, f"{self._settings_stem(module_id)}.json")
        settings = JsonDict(path, _load_json_file(path))
        self._apply_spec_defaults(module_id, settings)
        self._settings_cache[module_id] = settings
        return settings

    def config_spec_for(self, module_id: str) -> dict:
        """模块配置项声明：实例 config_spec() 优先，其次 META["config"] 静态声明。"""
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
        """把声明中缺失的缺省值写入模块配置并落盘，返回是否发生变更。"""
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
        """为全部已发现模块按声明补齐配置文件，返回发生补齐的模块 id。"""
        touched: list[str] = []
        for module_id in sorted(self._paths):
            cached = self._settings_cache.get(module_id)
            if cached is not None:
                continue
            path = os.path.join(self.config_dir, f"{self._settings_stem(module_id)}.json")
            settings = JsonDict(path, _load_json_file(path))
            if self._apply_spec_defaults(module_id, settings):
                touched.append(module_id)
            self._settings_cache[module_id] = settings
        return touched

    def _migrate_from_main_config(self) -> None:
        """旧版把模块设置存在主 config.json（顶层键 + modules 段）——一次性拆出。"""
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
                # 旧语义：模块启用状态混在设置里（如 osc.enabled），移入 modules.json
                self._enabled_map().setdefault(module_id, bool(old["enabled"]))
                old = {k: v for k, v in old.items() if k != "enabled"}
            write_settings(stem, old)
            cfg.pop(stem, None)
            changed = True

        if changed:
            self._modules_state.save()  # enabled 走嵌套变更，显式落盘
            self.engine.config.save()
            # 迁移直接改写了磁盘文件，丢弃 discover 期间预载的缓存再按盘重取
            self._settings_cache.clear()
            self.engine._log("已将模块配置从 config.json 拆分到 config/ 目录")

    def discover(self) -> list[dict]:
        """扫描各模块根目录，返回全部模块元数据（不执行模块代码）。

        同一模块 id 在多个根下出现时，靠后的根（用户目录）覆盖靠前的（内置）。
        """
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
        # is_enabled 依赖 self._meta 中的 default_enabled，须在整表构建完成后回填
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
        """直接注入/移除模块实例（测试与程序化替换用，正常装卸请用 install/uninstall）。"""
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
        """模块是否启用（安装自启动）。显式记录优先，否则取 META default_enabled。"""
        enabled = self._enabled_map()
        if module_id in enabled:
            return bool(enabled[module_id])
        meta = self._meta.get(module_id) or {}
        return bool(meta.get("default_enabled", False))

    def set_enabled(self, module_id: str, value: bool) -> None:
        self._enabled_map()[module_id] = bool(value)
        self._modules_state.save()  # 嵌套字典变更不触发写穿，显式落盘
        if module_id in self._meta:
            self._meta[module_id]["enabled"] = bool(value)

    def button_actions(self) -> list[ButtonAction]:
        """已加载模块注册的全部按键绑定动作。"""
        return list(self._button_actions.values())

    def action(self, key: str) -> ButtonAction | None:
        return self._button_actions.get(key)

    def module_for_action(self, key: str) -> str | None:
        """提供该按键动作的模块 id：优先查已加载注册表，其次各模块 META 声明。"""
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
        if module_id in self._meta:
            self._meta[module_id]["loaded"] = True
        self._register_actions(module_id, inst)
        self.engine._log(f"模块已加载: {inst.name or module_id} v{inst.version}")
        self.engine.events.emit("modules_changed", module_id)
        return inst

    def _register_actions(self, module_id: str, inst: ModuleBase) -> None:
        try:
            actions = inst.button_actions() or []
        except Exception:
            self.engine._log(f"模块 {module_id} button_actions() 失败:\n"
                             f"{traceback.format_exc()}")
            return
        for action in actions:
            if action.key in self._button_actions:
                self.engine._log(f"模块 {module_id} 的按键动作 {action.key} "
                                 f"已被模块 {self._button_actions[action.key].owner} "
                                 f"注册，忽略重复项")
                continue
            action.owner = module_id
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
        self._instances.pop(module_id, None)
        self._ctxs.pop(module_id, None)
        self._button_actions = {key: action for key, action
                                in self._button_actions.items()
                                if action.owner != module_id}
        if module_id in self._meta:
            self._meta[module_id]["loaded"] = False
            self._meta[module_id]["running"] = False
        # 热重载：卸载即清导入缓存与依赖路径，再安装/更新无需重启
        self._purge_module_cache(module_id)
        self._detach_deps_path(module_id)
        self.engine._log(f"模块已卸载: {inst.name or module_id}")
        self.engine.events.emit("modules_changed", module_id)

    async def start(self, module_id: str) -> None:
        inst = self.load(module_id)
        await inst.start()
        if module_id in self._meta:
            self._meta[module_id]["running"] = inst.is_running()

    async def stop(self, module_id: str) -> None:
        inst = self._instances.get(module_id)
        if inst is None:
            return
        await self._stop_instance(inst)
        if module_id in self._meta:
            self._meta[module_id]["running"] = False

    async def _stop_instance(self, inst: ModuleBase) -> None:
        try:
            await inst.stop()
        except Exception:
            self.engine._log(f"模块 {inst.id} 停止失败:\n{traceback.format_exc()}")

    async def _ensure_dependencies(self, module_id: str) -> None:
        """安装时按模块 requirements.txt（回退 META 声明）补装缺失依赖。"""
        requirements, source = self.store.requirements_of(module_id)
        if not requirements:
            return
        self.engine._log(f"模块 {module_id} 正在检查依赖（{source}）…")

        def _pip_log(line: str) -> None:
            self.engine._log(f"[pip] {line}")

        ok, still, output = await asyncio.to_thread(
            self.store.ensure_dependencies, module_id, log=_pip_log)
        if still:
            tail = "\n".join(output.strip().splitlines()[-8:]) or output
            raise RuntimeError(f"依赖安装失败（{'、'.join(still)}）\n{tail}")
        self.engine._log(f"模块 {module_id} 依赖就绪")

    async def install(self, module_id: str) -> None:
        """安装 = 按声明补装依赖 + 记住启用状态并立即加载启动。"""
        await self._ensure_dependencies(module_id)
        self.set_enabled(module_id, True)
        await self.start(module_id)

    async def uninstall(self, module_id: str) -> None:
        """卸载 = 停止并移除实例，启用状态记为关闭（文件保留在模块目录）。"""
        self.set_enabled(module_id, False)
        await self.unload(module_id)

    def delete_module(self, module_id: str) -> None:
        """删除模块文件夹（含私有 _deps）。已加载的模块须先卸载。

        Windows 下已 import 的扩展（.pyd/.dll）映像保留到进程退出，即时删除
        会 WinError 5，而目录重命名不受映像锁影响：删除失败时把整个目录改名
        <id>.pending_delete 摘出扫描，下次启动 discover 清扫（锁已释放）。
        """
        if module_id in self._instances:
            raise RuntimeError(f"模块 {module_id} 正在运行，请先卸载")
        module_dir = self.module_dir(module_id)
        if not module_dir:
            raise RuntimeError(f"未知模块: {module_id}")
        if os.path.normcase(os.path.realpath(module_dir)) in (
                os.path.normcase(os.path.realpath(root))
                for root in module_roots()):
            raise RuntimeError("拒绝删除模块根目录")
        try:
            shutil.rmtree(module_dir)
        except OSError:
            trash = module_dir + ".pending_delete"
            shutil.rmtree(trash, ignore_errors=True)
            try:
                os.rename(module_dir, trash)
            except OSError as exc:
                raise RuntimeError(
                    f"模块 {module_id} 文件被占用且无法转移，请重启应用后重试"
                ) from exc
            self.engine._log(
                f"模块 {module_id} 的部分文件被运行中的应用占用"
                "（扩展 .pyd 载入后保留到进程退出），已标记重启后自动清理")
        self.discover()
        self.engine._log(f"模块文件已删除: {module_id}")
        self.engine.events.emit("modules_changed", module_id)

    def _sweep_pending_deletes(self) -> None:
        """清扫 <id>.pending_delete 删除残留（delete_module 被映像锁让位时留下）。

        调用时机为进程启动后首次 discover（上一进程的映像锁已释放），
        ignore_errors 兜底杀软瞬时占用——删不净留待下次。
        """
        for root in module_roots():
            try:
                entries = os.listdir(root)
            except OSError:
                continue
            for entry in entries:
                if entry.endswith(".pending_delete"):
                    shutil.rmtree(os.path.join(root, entry),
                                  ignore_errors=True)

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
        # 鸭子类型模块：补齐缺失的生命周期方法为空实现
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

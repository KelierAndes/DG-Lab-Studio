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
import time
import traceback
from typing import Any, Callable
from concurrent.futures import Future

from module_store import ModuleStore
from dglab.expr import variables as expr_variables
from dglab.mapping import as_number
from dglab.params import input_specs


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


# 残留目录自动清理标记：delete_module 删不净时写入，下次启动整目录清扫
_CLEANUP_MARKER = ".dgstudio_pending_cleanup"


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

    def temp_specs(self) -> list[dict]:
        """模块读写的临时变量声明 ``[{key, label, desc}]``（可选实现）。

        联动页「临时变量」面板将其展示为模块维护行（实时值可引用）；
        缺省实现取 META["temps"]。模块代码经 ``ctx.set_temp/get_temp``
        读写，值空间与临时变量表表达式共享。
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

    def push_pulse_stream(self, frequency: int, channel: str = "A", level: int = 100,
                          slot_id: str | None = None):
        """外部脉冲流：模块每 0.1s 推入一次频率数据（核心生成波形用）。

        返回协程——引擎循环上下文直接 ``await``，否则 ``ctx.submit``。
        仅当该通道波形选中「外部脉冲流」时落地；逻辑频率 10-1000，
        电平 0-100（0 即该帧静音）。"""
        return self.engine.push_pulse_stream(frequency, channel, level=level,
                                             slot_id=slot_id)

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

    def fire(self, slot_id: str | None = None, duration_s: float | None = None,
             channel: str | None = None):
        """一键开火（定时，到时自动恢复强度/波形）。

        ``channel``="A"/"B" 只开火该通道，缺省双通道；需当前连接方式
        支持（Socket V4 / 蓝牙）。"""
        return self.engine.fire(slot_id=slot_id, duration_s=duration_s,
                                channel=channel)

    def fire_start(self, slot_id: str | None = None, channel: str | None = None):
        """按住持续开火（``channel``="A"/"B" 只动该通道，缺省双通道）。"""
        return self.engine.fire_start(slot_id=slot_id, channel=channel)

    def fire_stop(self, slot_id: str | None = None, channel: str | None = None):
        """停止开火并恢复强度/波形（``channel`` 缺省 = 全部通道）。"""
        return self.engine.fire_stop(slot_id=slot_id, channel=channel)

    def zap(self, channel: str, seconds: float = 1.0, slot_id: str | None = None):
        """瞬时脉冲：仅对指定通道开火 ``seconds`` 秒（通道分离语义）。"""
        return self.engine.zap(channel, seconds, slot_id=slot_id)

    def emergency_stop(self):
        return self.engine.emergency_stop()

    # ---- 临时变量（声明见 META["temps"]；事件流在联动页配置，运算只在临时变量表） ----

    def set_temp(self, key: str, value) -> None:
        """写临时变量（数值化；与联动页临时变量表、事件流动作共享值空间，
        写入即触发映射重算）。"""
        self.engine.modules.set_temp(self.module_id, key, value)

    def get_temp(self, key: str, default: float = 0.0) -> float:
        """读临时变量（未写过返回 ``default``）。"""
        return self.engine.modules.get_temp(self.module_id, key, default)

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
        # 每模块临时变量共享空间（模块 ctx 读写、映射引擎求值同源）
        self._temps: dict[str, dict[str, float]] = {}
        # 每模块事件流节拍任务（asyncio Future，卸载/停用时取消）
        self._event_tasks: dict[str, Future] = {}
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

        装载时按模块声明（config_spec / META["config"]）自动补齐缺失项，
        并清除映射表时代的 mappings/outputs 遗留设置项（事件流是唯一数据面，不迁移）。
        """
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
        """清除事件动作里引用已下线核心参数的僵尸行（如已移除的瞬时脉冲）。

        参数目录之外的输入动作在派发时只会报「派发失败」，留在配置里即是
        死行：直接删除并记日志。输出动作的目标是输出信号，不在清洗范围。
        """
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
        """清除映射表时代的 mappings/outputs 设置项（用户指示：不迁移）。

        事件流是唯一数据面：遗留映射/输出设置**直接删除键**（不转换、
        不保留空项；模块代码对缺失键按空表处理，兼容无忧），配置里确保
        无此项。同时清掉早期自动迁移的产物（「输入映射（迁移）」卡片与
        仅被其引用的 map_* 临时行——用户已在事件流重建行为，保留即重复
        驱动的幽灵流）。非空内容被清除时记日志，空键静默删除。
        """
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
                    continue          # 候选行本身不算引用(避免自保活)
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
            self._purge_legacy_tables(module_id, settings)
            self._purge_stale_event_actions(module_id, settings)
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
        self._temps.pop(module_id, None)   # 全新装载 = 临时变量空间清零
        if module_id in self._meta:
            self._meta[module_id]["loaded"] = True
        self._register_actions(module_id, inst)
        self.engine._log(f"模块已加载: {inst.name or module_id} v{inst.version}")
        self.engine.events.emit("modules_changed", module_id)
        return inst

    def _unregister_actions(self, module_id: str) -> None:
        """注销该模块注册的全部按键动作（卸载与停用时调用）。"""
        self._button_actions = {key: action for key, action
                                in self._button_actions.items()
                                if action.owner != module_id}

    def _register_actions(self, module_id: str, inst: ModuleBase) -> None:
        try:
            actions = inst.button_actions() or []
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
            # 同模块重注册（停用→重启）静默覆盖，幂等
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
        self._temps.pop(module_id, None)
        self._cancel_event_stream(module_id)
        self._unregister_actions(module_id)
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
        self._register_actions(module_id, inst)  # 停用期间注销过，幂等重注册
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
        """停用（联动页开关关）：停止运行并记为关闭，不卸载实例。

        与 uninstall（模块页显式卸载）的区别：实例与导入缓存保留，联动页
        卡片仅折叠不移除，重开开关复用已加载实例秒启。按键动作随停止注销
        （停止中的模块不再响应绑定），重新启动时在 start 里幂等重注册。
        """
        self._unregister_actions(module_id)
        self.set_enabled(module_id, False)
        self._cancel_event_stream(module_id)
        await self.stop(module_id)
        self.engine.events.emit("modules_changed", module_id)

    # --------------------------------------------- 事件流与临时变量（宿主侧）

    def _mapping_engine(self, module_id: str):
        """模块的映射引擎（bridge.engine / server.engine），无则 None。"""
        inst = self._instances.get(module_id)
        runtime = getattr(inst, "bridge", None) or getattr(inst, "server", None)
        return getattr(runtime, "engine", None) if runtime is not None else None

    def temp_specs_for(self, module_id: str) -> list[dict]:
        """临时变量声明：实例 temp_specs() 优先，其次 META["temps"]。"""
        inst = self._instances.get(module_id)
        if inst is not None:
            try:
                specs = inst.temp_specs()
                if specs:
                    return [dict(s) for s in specs if isinstance(s, dict)]
            except Exception:
                self.engine._log(f"模块 {module_id} temp_specs() 失败:\n"
                                 f"{traceback.format_exc()}")
        return [dict(s) for s in ((self._meta.get(module_id) or {})
                                  .get("temps") or [])]

    def temps_space(self, module_id: str) -> dict[str, float]:
        """模块临时变量共享空间（引擎求值与模块 ctx 读写同源）。"""
        return self._temps.setdefault(module_id, {})

    def set_temp(self, module_id: str, key: str, value) -> None:
        num = as_number(value)
        if num is None:
            return
        self.temps_space(module_id)[str(key)] = num
        eng = self._mapping_engine(module_id)
        if eng is not None and getattr(eng, "temps", None) is not None:
            eng.pump()          # 新数据 → 重算映射表（与 signal 同语义）

    def get_temp(self, module_id: str, key: str, default: float = 0.0) -> float:
        return float(self.temps_space(module_id).get(str(key), default))

    def apply_logic_tables(self, module_id: str) -> None:
        """把联动页配置的临时变量表/事件流卡片装载进模块映射引擎。

        临时变量空间由宿主持有并注入引擎（attach_temps），模块 ctx 与
        配置表达式读写同一份；事件流由宿主节拍循环驱动（每 50ms 一拍，
        周期更新/变量变更/if 判断三种驱动事件）。模块 reload_config 后
        需再次调用（reload）。
        """
        eng = self._mapping_engine(module_id)
        if eng is None or not hasattr(eng, "attach_temps"):
            return
        cfg = self.settings_for(module_id)
        eng.attach_temps(self.temps_space(module_id))
        eng.set_temp_rows(cfg.get("temps"))
        eng.set_event_cards(cfg.get("events"))
        self._ensure_event_stream(module_id)

    def _ensure_event_stream(self, module_id: str) -> None:
        """启动模块的事件流节拍循环（已运行则跳过）。"""
        if self._event_tasks.get(module_id) is not None:
            return
        eng = self._mapping_engine(module_id)
        if eng is None or not hasattr(eng, "tick_event_cards") \
                or not eng.has_events():
            return
        self._event_tasks[module_id] = self.engine.submit(
            self._event_stream_loop(module_id))

    def _cancel_event_stream(self, module_id: str) -> None:
        task = self._event_tasks.pop(module_id, None)
        if task is not None:
            task.cancel()

    async def _event_stream_loop(self, module_id: str) -> None:
        """事件流节拍：每 50ms 驱动一次该模块的事件卡片（触发判定+动作）。"""
        import time as _time
        try:
            while True:
                await asyncio.sleep(0.05)
                eng = self._mapping_engine(module_id)
                if eng is None or not hasattr(eng, "tick_event_cards"):
                    return
                eng.tick_event_cards(_time.monotonic())
        except asyncio.CancelledError:
            pass

    async def reload(self, module_id: str) -> None:
        """重载运行中模块：原生 reload_config + 宿主逻辑表（temps/事件流）。"""
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

        Windows 句柄实验结论（_tools/probe_file_lock*.py）：已载入扩展
        （.pyd/.dll 映像锁）删不掉但**可改名**；普通打开句柄/数据内存映射
        （杀软实时扫描等，瞬时为主）删不掉且**不可改名**，连整目录改名都
        会被阻止。策略：短重试等瞬时占用释放 → 整目录改名摘出命名空间
        （纯映像锁场景）→ 逐文件挽救可改名文件。残余文件打标记，下次
        启动 _sweep_pending_deletes 清扫（锁已释放）。
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
        last_exc: OSError | None = None
        for attempt in range(3):
            try:
                shutil.rmtree(module_dir)
                last_exc = None
                break
            except OSError as exc:
                last_exc = exc
                if attempt < 2:
                    time.sleep(0.8)  # 杀软等瞬时占用：稍候重试
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
        """删不净时的逐文件挽救：删得掉的删，删不掉但可改名的挪入隔离区。

        自底向上遍历（叶子先处理，目录清空后即可删除），返回连改名都
        拒绝的文件数。隔离区为同级 <id>.pending_delete 目录（保持相对
        结构），由 _sweep_pending_deletes 在下次启动时清扫。
        """
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
        """在残留目录里写自动清理标记，供 _sweep_pending_deletes 识别。"""
        try:
            with open(os.path.join(module_dir, _CLEANUP_MARKER), "w",
                      encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S"))
        except OSError:
            pass

    def _sweep_pending_deletes(self) -> None:
        """清扫删除残留（delete_module 让位/标记时留下）。

        两类：整目录改名让位的 <id>.pending_delete，与写有
        _CLEANUP_MARKER 的部分残留目录。调用时机为进程启动后首次
        discover（上一进程的锁已释放），ignore_errors 兜底杀软瞬时
        占用——删不净留待下次。
        """
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
                        # 仍有占用残留：补回标记，下次启动继续清扫
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

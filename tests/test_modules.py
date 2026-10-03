"""模块宿主（plugins.py）与强度参数公开 API 回归测试。

联动模块已外置到 dgstudio-modules-market 仓库；本文件用临时写入的夹具模块
（fixture）覆盖宿主逻辑：发现、装载、动作注册、配置声明补齐、
配置迁移、游戏模组释放与导出/载入。
"""
from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile
import unittest
import unittest.mock
import zipfile
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from plugins import ModuleBase, ModuleContext, PluginManager


class _FakeConfig(dict):
    def __init__(self, path):
        super().__init__()
        self.path = path
        self.saved = False

    def save(self):
        self.saved = True


class _FakeEngine:
    def __init__(self):
        from dglab.state import StateEvents

        self.events = StateEvents()
        # 每个实例独立临时目录，避免测试间共享 config/ 造成串扰
        self.config = _FakeConfig(
            os.path.join(tempfile.mkdtemp(prefix="dgstudio_test_"), "config.json"))
        self._logs: list[str] = []

    def _log(self, msg: str) -> None:
        self._logs.append(msg)

    def submit(self, coro):
        import asyncio

        return asyncio.run_coroutine_threadsafe(coro, asyncio.new_event_loop())


# ------------------------------------------------------------- 夹具模块写入

def _write_module(root: str, module_id: str, meta: dict, body: str = "",
                  files: dict[str, bytes] | None = None) -> str:
    folder = os.path.join(root, module_id)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "plugin.py"), "w", encoding="utf-8") as f:
        f.write(f"META = {meta!r}\n\n{body}")
    for name, data in (files or {}).items():
        path = os.path.join(folder, *name.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
    return folder


# 哑类型模块：鸭子类型（不继承 ModuleBase），验证宿主协议兼容
_DUMMY_META = {"id": "dummy", "name": "哑模块", "version": "0.2.0",
               "description": "鸭子类型最小模块。"}
_DUMMY_BODY = '''

class DummyModule:
    id = META["id"]
    name = META["name"]
    version = META["version"]
    description = META["description"]

    def on_load(self, ctx):
        self.ctx = ctx

    def on_unload(self):
        pass

    def is_running(self):
        return True
'''

# 动作模块：ModuleBase 子类 + 按键动作 + 配置声明（settings_key=osc，
# 与旧版真实模块同键，便于迁移与绑定校验测试沿用同一绑定值）
_ACTOR_META = {"id": "actor", "name": "动作模块", "version": "1.0.0",
               "description": "带按键动作与配置声明的模块。",
               "settings_key": "osc", "actions": ["osc"],
               "default_enabled": True,
               "config": {
                   "rate_hz": {"label": "频率", "type": "int", "default": 10,
                               "min": 1, "max": 30},
                   "mappings": {"label": "输入映射表", "type": "list",
                                "default": [], "group": "map", "rows": "in"},
                   "outputs": {"label": "输出映射表", "type": "list",
                               "default": [], "group": "map", "rows": "out"},
               }}
_ACTOR_BODY = '''
from plugins import ButtonAction, ModuleBase


class ActorModule(ModuleBase):
    id = META["id"]
    name = META["name"]
    version = META["version"]
    description = META["description"]
    settings_key = META["settings_key"]

    def on_load(self, ctx):
        self.ctx = ctx

    def on_unload(self):
        pass

    async def start(self):
        pass

    async def stop(self):
        pass

    def is_running(self):
        return False

    def button_actions(self):
        return [ButtonAction(
            key="osc", label="发送 OSC 参数…",
            argument_placeholder="/avatar/parameters/…",
            on_press=lambda slot_id, arg: None,
            on_release=lambda slot_id, arg: None)]
'''

# 游戏模组携带模块：META["mods"] 声明 + mods/ 假 DLL + vendor/ 假发行包
_CARRIER_META = {"id": "carrier", "name": "模组携带", "version": "0.1.0",
                 "description": "携带游戏端模组的模块。",
                 "mods": {"dest": "BepInEx/plugins/AliceInCradleLink",
                          "marker": "AliceInCradle.exe"}}


def _write_fixtures(root: str) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("winhttp.dll", b"MZ-winhttp")
        zf.writestr("BepInEx/core/BepInEx.Preloader.dll", b"MZ-bepinex")
    _write_module(root, "dummy", _DUMMY_META, _DUMMY_BODY)
    _write_module(root, "actor", _ACTOR_META, _ACTOR_BODY)
    _write_module(root, "carrier", _CARRIER_META,
                  files={"mods/AliceInCradleLink.dll": b"MZ-fake-dll",
                         "vendor/BepInEx_win_test.zip": buf.getvalue()})


class _FixtureRoots:
    """把 plugins.module_roots 指到写入夹具模块的临时目录。"""

    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="dgstudio_mods_")
        _write_fixtures(self.root)
        self.patcher = unittest.mock.patch(
            "plugins.module_roots", return_value=[self.root])

    def __enter__(self):
        self.patcher.start()
        return self

    def __exit__(self, *exc):
        self.patcher.stop()
        shutil.rmtree(self.root, ignore_errors=True)


class ModuleDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self._roots = _FixtureRoots()
        self._roots.__enter__()
        self.addCleanup(self._roots.__exit__, None, None, None)
        self.engine = _FakeEngine()
        self.manager = PluginManager(self.engine)
        self.engine.modules = self.manager

    def test_fixture_modules_discovered(self):
        ids = {m["id"] for m in self.manager.list_modules()}
        self.assertIn("dummy", ids)
        self.assertIn("actor", ids)
        self.assertIn("carrier", ids)

    def test_meta_has_name_and_version(self):
        meta = self.manager.meta("dummy")
        self.assertEqual(meta["name"], "哑模块")
        self.assertEqual(meta["version"], "0.2.0")
        self.assertFalse(meta["loaded"])

    def test_load_module_base_subclass(self):
        instance = self.manager.load("actor")
        self.assertIsInstance(instance, ModuleBase)
        self.assertEqual(instance.id, "actor")
        self.assertTrue(self.manager.meta("actor")["loaded"])
        # on_load 回填模块设置文件默认值
        self.assertEqual(self.manager.settings_for("actor").get("rate_hz"), 10)

    def test_load_duck_typed_module(self):
        instance = self.manager.load("dummy")
        self.assertIsNotNone(instance)
        self.assertEqual(instance.id, "dummy")

    def test_unknown_module_raises(self):
        with self.assertRaises(RuntimeError):
            self.manager.load("no_such_module")

    def test_enable_persist_split(self):
        self.manager.set_enabled("m1", True)
        self.assertTrue(self.manager._modules_state["enabled"]["m1"])
        self.assertTrue(self.manager.is_enabled("m1"))
        self.manager.set_enabled("m1", False)
        self.assertFalse(self.manager.is_enabled("m1"))

    def test_default_enabled_from_meta(self):
        self.assertTrue(self.manager.is_enabled("actor"))
        self.assertFalse(self.manager.is_enabled("dummy"))
        self.manager.set_enabled("actor", False)
        self.assertFalse(self.manager.is_enabled("actor"))

    def test_list_meta_enabled_reflects_default(self):
        # discover 缓存的 enabled 必须回落到 META default_enabled，
        # 否则未显式记录的模块在模块页显示「已停用」
        self.assertTrue(self.manager.meta("actor")["enabled"])
        self.assertFalse(self.manager.meta("dummy")["enabled"])
        self.manager.set_enabled("actor", False)
        self.assertFalse(self.manager.meta("actor")["enabled"])

    def test_dependencies_meta_read(self):
        root = self._roots.root
        _write_module(root, "needy", {"id": "needy", "name": "带依赖",
                                      "version": "0.1.0",
                                      "dependencies": ["some-pkg>=1.0"]})
        self.manager.discover()
        self.assertEqual(self.manager.meta("needy")["dependencies"],
                         ["some-pkg>=1.0"])

    def test_unload_purges_import_cache_for_hot_reload(self):
        # 装载 → 卸载 → 改写模块代码 → 再装载：新代码生效（安装/卸载热重载）
        root = self._roots.root
        _write_module(root, "dummy",
                      {"id": "dummy", "name": "哑模块", "version": "0.1.0",
                       "description": "鸭子类型最小模块。"}, _DUMMY_BODY)
        self.manager.discover()
        self.manager.load("dummy")
        self.assertEqual(self.manager.instance("dummy").version, "0.1.0")
        import asyncio

        asyncio.run(self.manager.unload("dummy"))
        self.assertNotIn("modules.dummy.plugin", sys.modules)
        _write_module(root, "dummy",
                      {"id": "dummy", "name": "哑模块", "version": "0.2.0",
                       "description": "鸭子类型最小模块。"}, _DUMMY_BODY)
        self.manager.discover()
        self.manager.load("dummy")
        self.assertEqual(self.manager.instance("dummy").version, "0.2.0")

    def test_unload_detaches_deps_path(self):
        root = self._roots.root
        deps = os.path.join(root, "dummy", "_deps")
        os.makedirs(deps)

        def _discard():
            if deps in sys.path:
                sys.path.remove(deps)

        self.addCleanup(_discard)
        sys.path.insert(0, deps)
        import asyncio

        self.manager.load("dummy")
        asyncio.run(self.manager.unload("dummy"))
        self.assertNotIn(os.path.normcase(deps),
                         [os.path.normcase(p) for p in sys.path])

    def test_migration_from_main_config(self):
        engine = _FakeEngine()
        engine.config.update({
            "osc": {"enabled": False, "rate_hz": 25, "in_strength_a": "X"},
            "modules": {"enabled": {"m1": True}, "settings": {"m1": {"k": 1}}},
        })
        with _FixtureRoots():
            manager = PluginManager(engine)
        self.assertNotIn("osc", engine.config)
        self.assertNotIn("modules", engine.config)
        self.assertTrue(engine.config.saved)
        self.assertFalse(manager.is_enabled("actor"))  # 旧 enabled=False → 显式关闭
        osc = manager.settings_for("actor")
        self.assertEqual(osc.get("rate_hz"), 25)
        self.assertNotIn("enabled", osc)
        self.assertTrue(manager.is_enabled("m1"))
        self.assertEqual(manager.settings_for("m1").get("k"), 1)

    def test_register_instance_injection(self):
        class Fake:
            id = ""

            def is_running(self):
                return True

        fake = Fake()
        self.manager.register_instance("dummy", fake)
        self.assertIs(self.manager.instance("dummy"), fake)
        self.assertEqual(fake.id, "dummy")
        self.manager.register_instance("dummy", None)
        self.assertIsNone(self.manager.instance("dummy"))


class ModuleContextApiTests(unittest.TestCase):
    def test_context_exposes_public_api(self):
        import app as app_module

        engine = app_module.Engine(
            config_path=os.path.join(tempfile.gettempdir(), "dgstudio_test_ctx.json"))
        try:
            ctx = ModuleContext(engine, _ProbeModule())
            self.assertEqual(ctx.wave_order("OVC")[0], "__SILENT__")
            self.assertEqual(ctx.wave_selection(), {"A": "__SILENT__", "B": "__SILENT__"})
            self.assertIn("strength_step", ctx.intensity_params())
            ctx.settings["probe_key"] = 7
            self.assertEqual(engine.modules.settings_for("probe").get("probe_key"), 7)
            with self.assertRaises(ValueError):
                ctx.set_intensity_param("nope", 1)
            ctx.set_intensity_param("max_strength", 180)
            self.assertEqual(ctx.intensity_params()["max_strength"], 180)
        finally:
            engine.stop()


class _ProbeModule(ModuleBase):
    id = "probe"
    name = "probe"


class ModuleButtonActionTests(unittest.TestCase):
    def setUp(self):
        self._roots = _FixtureRoots()
        self._roots.__enter__()
        self.addCleanup(self._roots.__exit__, None, None, None)
        self.engine = _FakeEngine()
        self.manager = PluginManager(self.engine)
        self.engine.modules = self.manager

    def test_action_registered_on_load(self):
        self.manager.load("actor")
        actions = self.manager.button_actions()
        self.assertEqual([a.key for a in actions], ["osc"])
        self.assertEqual(actions[0].owner, "actor")
        self.assertTrue(actions[0].label)
        self.assertIsNotNone(actions[0].on_press)
        self.assertIsNotNone(actions[0].on_release)

    def test_unload_removes_actions(self):
        self.manager.load("actor")
        self.assertIsNotNone(self.manager.action("osc"))
        import asyncio

        asyncio.run(self.manager.unload("actor"))
        self.assertIsNone(self.manager.action("osc"))
        self.assertEqual(self.manager.button_actions(), [])

    def test_module_for_action_via_meta_without_load(self):
        self.assertEqual(self.manager.module_for_action("osc"), "actor")
        self.assertIsNone(self.manager.module_for_action("nope"))

    def test_duplicate_action_key_ignored(self):
        self.manager.load("actor")
        from plugins import ButtonAction

        self.manager._register_actions(
            "dummy",
            type("M", (), {"button_actions": lambda self: [
                ButtonAction("osc", "重复项")]})())
        self.assertIs(self.manager.action("osc").owner, "actor")
        self.assertEqual(len(self.manager.button_actions()), 1)


class BindingProfileCheckTests(unittest.TestCase):
    def setUp(self):
        import app as app_module

        self._roots = _FixtureRoots()
        self._roots.__enter__()
        self.addCleanup(self._roots.__exit__, None, None, None)
        path = os.path.join(tempfile.gettempdir(), "dgstudio_test_bindings.json")
        if os.path.exists(path):
            os.remove(path)
        self.engine = app_module.Engine(config_path=path)

    def tearDown(self):
        self.engine.stop()

    def test_missing_detection_without_loaded_module(self):
        missing = self.engine.binding_missing_modules({
            "13": "osc:/avatar/parameters/X",
            "12": "key:F1",
            "15": "fire",
            "14": "none",
        })
        self.assertEqual(missing, {"13": "osc:/avatar/parameters/X"})
        self.assertEqual(self.engine.modules_for_bindings(missing), ["actor"])

    def test_no_missing_after_module_load(self):
        self.engine.modules.load("actor")
        missing = self.engine.binding_missing_modules({"13": "osc:/avatar/X"})
        self.assertEqual(missing, {})

    def test_unload_triggers_missing_detection(self):
        import asyncio

        self.engine.start()
        try:
            ble = self.engine.config["ble"]
            ble["ovc_profiles"] = {"默认": {"13": "osc:/avatar/X"}}
            ble["ovc_profile"] = "默认"
            events = []
            self.engine.events.on("binding_modules_missing",
                                  lambda payload: events.append(payload))
            self.engine.modules.load("actor")
            self.assertEqual(events, [])  # 加载只会消除缺失，不触发提示
            fut = asyncio.run_coroutine_threadsafe(
                self.engine.modules.unload("actor"), self.engine.loop)
            fut.result(timeout=10)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["modules"], ["actor"])
            self.assertEqual(events[0]["bindings"], {"13": "osc:/avatar/X"})
        finally:
            self.engine.stop()

    def test_reset_bindings_clears_bits(self):
        self.engine.config["ble"]["ovc_profiles"] = {"默认": {"13": "osc:/x",
                                                             "15": "fire"}}
        self.engine.config["ble"]["ovc_profile"] = "默认"
        self.engine.reset_bindings(["13"])
        self.assertEqual(self.engine.config["ble"]["ovc_profiles"]["默认"]["13"],
                         "none")
        self.assertEqual(self.engine.config["ble"]["ovc_profiles"]["默认"]["15"],
                         "fire")

    def test_rename_profile(self):
        ble = self.engine.config["ble"]
        ble["ovc_profiles"] = {"默认": {"13": "fire"}, "配置1": {"15": "estop"}}
        ble["ovc_profile"] = "配置1"
        self.assertIsNone(self.engine.rename_ovc_profile("配置1", "急停方案"))
        self.assertEqual(list(ble["ovc_profiles"]), ["默认", "急停方案"])
        self.assertEqual(ble["ovc_profiles"]["急停方案"], {"15": "estop"})
        self.assertEqual(ble["ovc_profile"], "急停方案")
        self.assertIn("已存在", self.engine.rename_ovc_profile("急停方案", "默认"))
        self.assertIn("不存在", self.engine.rename_ovc_profile("缺失", "x"))
        self.assertIsNone(self.engine.rename_ovc_profile("急停方案", "急停方案"))
        self.assertEqual(self.engine.rename_ovc_profile("急停方案", "  "),
                         "名称不能为空")


class ConfigDrivenTests(unittest.TestCase):
    """配置声明自动装载回归（夹具模块 META["config"]）。"""

    def setUp(self):
        self._roots = _FixtureRoots()
        self._roots.__enter__()
        self.addCleanup(self._roots.__exit__, None, None, None)
        self.engine = _FakeEngine()
        self.manager = PluginManager(self.engine)
        self.engine.modules = self.manager

    def test_declared_defaults_auto_filled_on_discover(self):
        osc = self.manager.settings_for("actor")
        self.assertIn("rate_hz", osc)
        self.assertIn("mappings", osc)
        self.assertIn("outputs", osc)
        self.assertEqual(osc["rate_hz"], 10)
        self.assertEqual(osc["mappings"], [])
        self.assertEqual(osc["outputs"], [])
        path = os.path.join(self.manager.config_dir, "osc.json")
        self.assertTrue(os.path.isfile(path))

    def test_existing_values_not_overwritten(self):
        osc = self.manager.settings_for("actor")
        osc["rate_hz"] = 25
        osc.save()
        self.manager._settings_cache.clear()
        self.assertEqual(self.manager.settings_for("actor")["rate_hz"], 25)

    def test_config_spec_via_meta_without_load(self):
        spec = self.manager.config_spec_for("actor")
        self.assertEqual(spec["rate_hz"]["max"], 30)
        self.assertEqual(spec["mappings"]["type"], "list")
        self.assertEqual(spec["mappings"]["group"], "map")
        self.assertEqual(spec["mappings"]["rows"], "in")
        self.assertEqual(spec["outputs"]["rows"], "out")


class ConfigInitModuleTests(unittest.TestCase):
    """初始化配置模块：导出包 / 载入恢复。"""

    def setUp(self):
        self._roots = _FixtureRoots()
        self._roots.__enter__()
        self.addCleanup(self._roots.__exit__, None, None, None)
        self.engine = _FakeEngine()
        self.manager = PluginManager(self.engine)
        self.engine.modules = self.manager
        from modules.config_init.plugin import ConfigInitModule
        from plugins import ModuleContext

        self.inst = ConfigInitModule()
        self.ctx = ModuleContext(self.engine, self.inst)
        self.inst.on_load(self.ctx)

    def _tmp(self, name: str) -> str:
        return os.path.join(tempfile.mkdtemp(), name)

    def test_export_bundle_contains_files(self):
        import json

        path = self._tmp("export.json")
        count = self.inst.export_to(path)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["__bundle__"], "dgstudio-config-bundle")
        self.assertIn("config.json", data["files"])
        self.assertIn("config/osc.json", data["files"])
        self.assertGreaterEqual(count, 2)

    def test_load_bundle_restores_values(self):
        osc = self.manager.settings_for("actor")
        path = self._tmp("export.json")
        self.inst.export_to(path)
        osc["rate_hz"] = 99
        applied = self.inst.load_from(path)
        self.assertGreaterEqual(applied, 1)
        self.assertEqual(self.manager.settings_for("actor")["rate_hz"], 10)

    def test_load_plain_config_merges_main(self):
        import json

        path = self._tmp("plain.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"max_strength": 120}, f)
        self.inst.load_from(path)
        self.assertEqual(self.engine.config["max_strength"], 120)
        self.assertTrue(self.engine.config.saved)

    def test_load_plain_config_then_spec_refill(self):
        import json

        path = self._tmp("partial.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"some_key": 9100}, f)
        # 作为主配置载入后，模块声明缺省仍在（模块文件未被该文件覆盖）
        self.inst.load_from(path)
        self.assertEqual(self.engine.config["some_key"], 9100)
        self.assertEqual(self.manager.settings_for("actor")["rate_hz"], 10)


class GameModTests(unittest.TestCase):
    """模块携带游戏端模组：META 声明、mods/ 目录与一键释放安装。"""

    def setUp(self):
        self._roots = _FixtureRoots()
        self._roots.__enter__()
        self.addCleanup(self._roots.__exit__, None, None, None)
        self.engine = _FakeEngine()
        self.manager = PluginManager(self.engine)
        self.engine.modules = self.manager

    def test_meta_declares_game_mod(self):
        meta = self.manager.meta("carrier")
        self.assertEqual(meta["mods"]["dest"],
                         "BepInEx/plugins/AliceInCradleLink")
        self.assertEqual(meta["mods"]["marker"], "AliceInCradle.exe")
        self.assertIsNone(self.manager.module_mods_dir("actor"))

    def test_module_mods_dir_carries_dll(self):
        mods = self.manager.module_mods_dir("carrier")
        self.assertTrue(mods)
        self.assertTrue(os.path.isfile(
            os.path.join(mods, "AliceInCradleLink.dll")))

    def test_install_game_mod_to_bepinex_root(self):
        root = tempfile.mkdtemp(prefix="dgstudio_game_")
        os.makedirs(os.path.join(root, "BepInEx", "plugins"))
        count = self.manager.install_game_mod("carrier", root)
        self.assertGreaterEqual(count, 1)
        self.assertTrue(os.path.isfile(os.path.join(
            root, "BepInEx", "plugins", "AliceInCradleLink",
            "AliceInCradleLink.dll")))

    def test_install_game_mod_rejects_non_bepinex_root(self):
        root = tempfile.mkdtemp(prefix="dgstudio_game_")
        with self.assertRaises(ValueError) as ctx:
            self.manager.install_game_mod("carrier", root)
        self.assertIn("游戏主程序", str(ctx.exception))

    def test_install_auto_installs_bepinex_from_vendor_zip(self):
        root = tempfile.mkdtemp(prefix="dgstudio_game_")
        with open(os.path.join(root, "AliceInCradle.exe"), "wb"):
            pass
        count = self.manager.install_game_mod("carrier", root)
        self.assertGreaterEqual(count, 1)
        self.assertTrue(os.path.isfile(os.path.join(
            root, "BepInEx", "plugins", "AliceInCradleLink",
            "AliceInCradleLink.dll")))
        self.assertTrue(os.path.isfile(os.path.join(root, "winhttp.dll")))
        self.assertTrue(os.path.isdir(os.path.join(root, "BepInEx", "core")))
        self.assertTrue(any("自动安装 BepInEx" in msg
                            for msg in self.engine._logs))

    def test_install_rejects_when_vendor_zip_missing(self):
        root = tempfile.mkdtemp(prefix="dgstudio_game_")
        with open(os.path.join(root, "AliceInCradle.exe"), "wb"):
            pass
        with unittest.mock.patch.object(type(self.manager), "_install_bepinex",
                                        return_value=0):
            with self.assertRaises(ValueError) as ctx:
                self.manager.install_game_mod("carrier", root)
        self.assertIn("发行包", str(ctx.exception))
        self.assertFalse(os.path.isdir(os.path.join(root, "BepInEx")))

    def test_bundled_bepinex_in_mods_takes_priority(self):
        mods = self.manager.module_mods_dir("carrier")
        bundled = os.path.join(mods, "BepInEx", "plugins", "Bundled")
        os.makedirs(bundled)
        with open(os.path.join(bundled, "bundled.dll"), "wb"):
            pass
        try:
            root = tempfile.mkdtemp(prefix="dgstudio_game_")
            with open(os.path.join(root, "AliceInCradle.exe"), "wb"):
                pass
            self.manager.install_game_mod("carrier", root)
            self.assertTrue(os.path.isfile(os.path.join(
                root, "BepInEx", "plugins", "Bundled", "bundled.dll")))
        finally:
            shutil.rmtree(os.path.join(mods, "BepInEx"), ignore_errors=True)

    def test_scan_game_roots_finds_marker(self):
        root = tempfile.mkdtemp(prefix="dgstudio_game_")
        game = os.path.join(root, "Download", "Game Dir", "GameRoot")
        os.makedirs(game)
        with open(os.path.join(game, "AliceInCradle.exe"), "wb"):
            pass
        found = self.manager.scan_game_roots("aliceincradle.exe",
                                             roots=[root], max_depth=4)
        self.assertEqual(
            [os.path.normcase(os.path.realpath(g)) for g in found],
            [os.path.normcase(os.path.realpath(game))])

    def test_module_context_game_mod_api(self):
        # 通用接口：任意携带 META["mods"] 的模块经 ModuleContext 即得
        # 查载荷 / 扫游戏 / 释放安装三个原语（BepInEx 由模块 vendor/ 提供）
        ctx = ModuleContext(self.engine, SimpleNamespace(id="carrier"))
        self.assertTrue(ctx.game_mods_dir())
        root = tempfile.mkdtemp(prefix="dgstudio_game_")
        game = os.path.join(root, "Game")
        os.makedirs(game)
        with open(os.path.join(game, "AliceInCradle.exe"), "wb"):
            pass
        found = ctx.scan_game_roots(roots=[root], max_depth=2)
        self.assertTrue(found)
        self.assertGreaterEqual(ctx.install_game_mod(found[0]), 1)
        self.assertTrue(os.path.isfile(os.path.join(
            found[0], "BepInEx", "plugins", "AliceInCradleLink",
            "AliceInCradleLink.dll")))
        self.assertTrue(os.path.isfile(os.path.join(found[0], "winhttp.dll")))


if __name__ == "__main__":
    unittest.main()

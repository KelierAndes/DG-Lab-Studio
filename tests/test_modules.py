from __future__ import annotations

import io
import json
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
        self.config = _FakeConfig(
            os.path.join(tempfile.mkdtemp(prefix="dgstudio_test_"), "config.json"))
        self._logs: list[str] = []

    def _log(self, msg: str) -> None:
        self._logs.append(msg)

    def submit(self, coro):
        import asyncio

        return asyncio.run_coroutine_threadsafe(coro, asyncio.new_event_loop())


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

_CARRIER_META = {"id": "carrier", "name": "模组携带", "version": "0.1.0",
                 "description": "携带游戏端模组的模块。",
                 "mods": {"dest": "BepInEx/plugins/AliceInCradleLink",
                          "marker": "AliceInCradle.exe"}}

_LOGIC_META = {"id": "logic", "name": "事件模块", "version": "0.1.0",
               "description": "声明临时变量的模块。",
               "temps": [{"key": "count", "label": "计数",
                          "desc": "节拍计数"}]}
_LOGIC_BODY = '''
from dglab.mapping import MappingEngine


class _Bridge:
    def __init__(self, engine):
        self.engine = engine


class LogicModule:
    id = META["id"]
    name = META["name"]
    version = META["version"]

    def on_load(self, ctx):
        self.ctx = ctx
        self.sent = []
        self.engine = MappingEngine(lambda k, v: self.sent.append((k, v)))
        self.bridge = _Bridge(self.engine)

    def on_unload(self):
        pass

    def is_running(self):
        return False
'''


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
    _write_module(root, "logic", _LOGIC_META, _LOGIC_BODY)


class _FixtureRoots:

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
        self.assertIn("logic", ids)

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
        self.assertFalse(manager.is_enabled("actor"))
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
            ctx.set_intensity_param("max_strength", 180)
            self.assertNotEqual(ctx.intensity_params()["max_strength"], 180)
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
            self.assertEqual(events, [])
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
        self.assertEqual(osc["rate_hz"], 10)
        self.assertNotIn("mappings", osc)
        self.assertNotIn("outputs", osc)
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
        self.inst.load_from(path)
        self.assertEqual(self.engine.config["some_key"], 9100)
        self.assertEqual(self.manager.settings_for("actor")["rate_hz"], 10)


class GameModTests(unittest.TestCase):

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


class EventTempInterfaceTests(unittest.TestCase):

    def setUp(self):
        self._roots = _FixtureRoots()
        self._roots.__enter__()
        self.addCleanup(self._roots.__exit__, None, None, None)
        self.engine = _FakeEngine()
        self.manager = PluginManager(self.engine)
        self.engine.modules = self.manager

    def _load(self):
        inst = self.manager.load("logic")
        self.manager.apply_logic_tables("logic")
        return inst

    def test_meta_temps_flow_through(self):
        meta = self.manager.meta("logic")
        self.assertEqual(meta["temps"],
                         [{"key": "count", "label": "计数",
                           "desc": "节拍计数"}])
        self.assertEqual([s["key"] for s in self.manager.temp_specs_for("logic")],
                         ["count"])

    def test_temp_specs_instance_overrides_meta(self):
        inst = self.manager.load("logic")
        inst.temp_specs = lambda: [{"key": "custom", "label": "自定义"}]
        self.assertEqual([s["key"] for s in self.manager.temp_specs_for("logic")],
                         ["custom"])

    def test_optional_hooks_stay_quiet_when_module_skips_them(self):
        inst = self.manager.load("logic")
        self.assertFalse(hasattr(inst, "temp_specs"))
        self.assertFalse(callable(getattr(inst, "button_actions", None)))
        mark = len(self.engine._logs)
        for _ in range(5):
            self.manager.temp_specs_for("logic")
            self.manager._register_actions("logic", inst)
        self.assertEqual(self.engine._logs[mark:], [])
        self.assertEqual([s["key"] for s in self.manager.temp_specs_for("logic")],
                         ["count"])

    def test_broken_temp_specs_hook_falls_back_and_logs(self):
        inst = self.manager.load("logic")

        def _boom():
            raise AttributeError("no such attribute")

        inst.temp_specs = _boom
        self.assertEqual([s["key"] for s in self.manager.temp_specs_for("logic")],
                         ["count"])
        self.assertIn("temp_specs() 失败", "\n".join(self.engine._logs))

    def test_apply_logic_tables_shares_flow_temps(self):
        inst = self._load()
        shared = self.manager.temps_space()
        self.assertIs(inst.engine.temps, shared)
        self.assertIs(self.manager.temps_space("other"), shared)
        cfg = self.manager.settings_for("logic")
        cfg["temps"] = [{"name": "count", "expr": "{count}+1"}]
        cfg["events"] = [{"name": "拍", "trigger": "period", "arg": 100,
                          "actions": [{"dir": "in", "param": "in_fire",
                                       "var": "count"}]}]
        self.manager.apply_logic_tables("logic")
        self.assertEqual(inst.engine._temp_table, [])
        self.assertEqual(inst.engine._cards, [])
        self.assertEqual(inst.sent, [])
        self.manager.set_temp("logic", "count", 3)
        self.assertEqual(inst.engine.temps["count"], 3.0)
        self.assertEqual(self.manager.get_temp("logic", "count"), 3.0)

    def test_ctx_temp_read_write(self):
        inst = self._load()
        inst.ctx.set_temp("x", 5)
        self.assertEqual(inst.ctx.get_temp("x"), 5.0)
        self.assertEqual(self.manager.get_temp("logic", "x"), 5.0)
        self.assertEqual(inst.ctx.get_temp("missing", 3), 3.0)

    def test_load_resets_declared_temps_only(self):
        self.manager.set_temp("logic", "count", 1)
        self.manager.set_temp("logic", "x", 2)
        self.manager.load("logic")
        space = self.manager.temps_space("logic")
        self.assertNotIn("count", space)
        self.assertEqual(space["x"], 2.0)


class LegacyPurgeTests(unittest.TestCase):

    def setUp(self):
        self._roots = _FixtureRoots()
        self._roots.__enter__()
        self.addCleanup(self._roots.__exit__, None, None, None)
        self.engine = _FakeEngine()
        self.manager = PluginManager(self.engine)
        self.engine.modules = self.manager

    def test_mappings_and_outputs_keys_removed(self):
        cfg = self.manager.settings_for("logic")
        cfg["mappings"] = [
            {"param": "in_strength_a", "expr": "{HPmax}/4 - {HP}/4"},
            {"param": "in_fire", "expr": "{Orgasming}"},
        ]
        cfg["outputs"] = [
            {"param": "COYOTE.Battery", "name": "Battery",
             "expr": "{COYOTE.Battery}", "type": "Int"},
        ]
        cfg["temps"] = [{"name": "HLost", "expr": "{HPmax} - {HP}"}]
        cfg["events"] = [{"name": "帧事件流", "trigger": "if",
                          "arg": "{Hurt} > 0", "actions": []}]
        self.manager._purge_legacy_tables("logic", cfg)
        self.assertNotIn("mappings", cfg)
        self.assertNotIn("outputs", cfg)
        self.assertEqual(cfg["temps"],
                         [{"name": "HLost", "expr": "{HPmax} - {HP}"}])
        self.assertEqual([e["name"] for e in cfg["events"]], ["帧事件流"])
        self.assertTrue(any("清除遗留" in m for m in self.engine._logs))

    def test_keys_removed_even_when_empty(self):
        cfg = self.manager.settings_for("logic")
        cfg["mappings"] = []
        cfg["outputs"] = []
        self.manager._purge_legacy_tables("logic", cfg)
        self.assertNotIn("mappings", cfg)
        self.assertNotIn("outputs", cfg)

    def test_first_generation_migration_artifacts_cleaned(self):
        cfg = self.manager.settings_for("logic")
        cfg.pop("mappings", None)
        cfg.pop("outputs", None)
        cfg["temps"] = [{"name": "HLost", "expr": "{HPmax} - {HP}"},
                        {"name": "map_in_strength_a",
                         "expr": "{HPmax}/4 - {HP}/4"}]
        cfg["events"] = [
            {"name": "输入映射（迁移）", "trigger": "period", "arg": 50,
             "actions": [{"dir": "in", "param": "in_strength_a",
                          "var": "map_in_strength_a"}]},
            {"name": "帧事件流", "trigger": "if", "arg": "{Hurt} > 0",
             "actions": [{"dir": "in", "param": "in_strength_b",
                          "var": "HLost"}]},
        ]
        self.manager._purge_legacy_tables("logic", cfg)
        self.assertNotIn("mappings", cfg)
        self.assertEqual([e["name"] for e in cfg["events"]], ["帧事件流"])
        self.assertEqual([t["name"] for t in cfg["temps"]], ["HLost"])
        before = json.dumps(cfg, ensure_ascii=False, default=str)
        self.manager._purge_legacy_tables("logic", cfg)
        self.assertEqual(json.dumps(cfg, ensure_ascii=False, default=str),
                         before)

    def test_stale_event_actions_purged(self):
        cfg = self.manager.settings_for("logic")
        cfg["events"] = [{"name": "拍", "trigger": "period", "arg": 50,
                          "actions": [
                              {"dir": "in", "param": "in_zap_a", "var": "x"},
                              {"dir": "in", "param": "in_fire", "var": "x"},
                              {"dir": "out", "param": "COYOTE.Battery",
                               "var": "bat", "name": "Battery",
                               "type": "Int"}]}]
        self.manager._purge_stale_event_actions("logic", cfg)
        kept = cfg["events"][0]["actions"]
        self.assertEqual([(a["dir"], a["param"]) for a in kept],
                         [("in", "in_fire"), ("out", "COYOTE.Battery")])
        self.assertTrue(any("已下线核心参数" in m
                            for m in self.engine._logs))


if __name__ == "__main__":
    unittest.main()

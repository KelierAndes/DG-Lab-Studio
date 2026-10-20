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

    def test_delete_module_takes_the_file_and_the_enabled_flag(self):
        """卸载 = 文件与启用标记一起走。

        标记留在 modules.json 里，下次开机就去加载一个已经不存在的模块；
        列表里那条还留着，用户看到的就是「点了卸载没反应」。
        """
        self.manager.set_enabled("actor", True)
        path = self.manager.module_dir("actor")
        self.assertTrue(path and os.path.isdir(path))
        self.manager.delete_module("actor")
        self.assertNotIn("actor", {m["id"] for m in self.manager.list_modules()})
        self.assertNotIn("actor", self.manager._enabled_map())
        self.assertFalse(os.path.isdir(path))

    def test_delete_module_refuses_a_running_one(self):
        self.manager.load("actor")
        with self.assertRaises(RuntimeError) as ctx:
            self.manager.delete_module("actor")
        self.assertIn("还在运行", str(ctx.exception))

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
        import shutil

        self._roots = _FixtureRoots()
        self._roots.__enter__()
        self.addCleanup(self._roots.__exit__, None, None, None)
        # 按键映射 / 事件流配置都落 tempdir/config/ 下的文件夹，先清干净防串场
        shutil.rmtree(os.path.join(tempfile.gettempdir(), "config"),
                      ignore_errors=True)
        path = os.path.join(tempfile.gettempdir(), "dgstudio_test_bindings.json")
        if os.path.exists(path):
            os.remove(path)
        self.engine = app_module.Engine(config_path=path)

    def tearDown(self):
        self.engine.stop()

    def test_legacy_config_profiles_migrate_to_folder(self):
        """主配置文件里的 ble.ovc_profiles 首次启动搬进 config/bindings/。"""
        import app as app_module
        import json as _json

        shutil.rmtree(os.path.join(tempfile.gettempdir(), "config", "bindings"),
                      ignore_errors=True)
        path = os.path.join(tempfile.gettempdir(),
                            "dgstudio_test_bindings_mig.json")
        if os.path.exists(path):
            os.remove(path)
        with open(path, "w", encoding="utf-8") as handle:
            _json.dump({"ble": {"ovc_profiles": {"默认": {"13": "fire"},
                                                 "备用": {"15": "estop"}},
                                "ovc_profile": "备用"}}, handle)
        engine = app_module.Engine(config_path=path)
        try:
            active, profiles = engine.binding_profiles()
            self.assertEqual(active, "备用")
            self.assertEqual(profiles["默认"], {"13": "fire"})
            self.assertEqual(profiles["备用"], {"15": "estop"})
            self.assertEqual(engine.ovc_bindings(), {"15": "estop"})
            # 主配置文件不再保存映射配置
            self.assertNotIn("ovc_profiles", engine.config["ble"])
            self.assertNotIn("ovc_profile", engine.config["ble"])
        finally:
            engine.stop()
            if os.path.exists(path):
                os.remove(path)

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
            self.engine.save_binding_profiles("默认",
                                              {"默认": {"13": "osc:/avatar/X"}})
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
        self.engine.save_binding_profiles(
            "默认", {"默认": {"13": "osc:/x", "15": "fire"}})
        self.engine.reset_bindings(["13"])
        active, profiles = self.engine.binding_profiles()
        self.assertEqual(profiles["默认"]["13"], "none")
        self.assertEqual(profiles["默认"]["15"], "fire")

    def test_rename_profile(self):
        self.engine.save_binding_profiles(
            "配置1", {"默认": {"13": "fire"}, "配置1": {"15": "estop"}})
        self.assertIsNone(self.engine.rename_ovc_profile("配置1", "急停方案"))
        active, profiles = self.engine.binding_profiles()
        self.assertEqual(list(profiles), ["默认", "急停方案"])
        self.assertEqual(profiles["急停方案"], {"15": "estop"})
        self.assertEqual(active, "急停方案")
        self.assertIn("已存在", self.engine.rename_ovc_profile("急停方案", "默认"))
        self.assertIn("不存在", self.engine.rename_ovc_profile("缺失", "x"))
        self.assertIsNone(self.engine.rename_ovc_profile("急停方案", "急停方案"))
        self.assertEqual(self.engine.rename_ovc_profile("急停方案", "  "),
                         "名称不能为空")


class ModuleProfileInjectionTests(unittest.TestCase):
    """模块自带 flow/ bindings/ 默认配置：装载时登记为可选配置文件。"""

    def setUp(self):
        self._roots = _FixtureRoots()
        self._roots.__enter__()
        self.addCleanup(self._roots.__exit__, None, None, None)
        base = tempfile.mkdtemp(prefix="dgstudio_inject_")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        self.engine = _FakeEngine()
        self.engine.config.path = os.path.join(base, "config.json")
        self.manager = PluginManager(self.engine)
        self.engine.modules = self.manager
        import dglab.event_flow as EF
        import dglab.bindings as bs
        self.EF = EF
        self.bs = bs
        self.flow_dir = EF.profiles_dir(os.path.dirname(self.engine.config.path))
        self.bind_dir = bs.bindings_dir(os.path.dirname(self.engine.config.path))

    def _flow_payload(self, value: float) -> bytes:
        graphs = {"version": 3, "graphs": {
            "input": {"nodes": [{"id": "i1", "def": "var.const_float",
                                 "x": 0.0, "y": 0.0, "params": {"v": value},
                                 "alias": "", "overrides": []}],
                      "wires": []},
            "output": {"nodes": [], "wires": []}}}
        return json.dumps(graphs, ensure_ascii=False).encode("utf-8")

    def _write_profile_module(self, version: str, flow_value: float,
                              flow_name: str = "默认接线") -> None:
        meta = {"id": "flowy", "name": "带默认配置的模块", "version": version,
                "description": "测试注入。", "settings_key": "flowy"}
        body = """

class FlowyModule:
    id = META["id"]
    name = META["name"]
    version = META["version"]
    settings_key = META["settings_key"]

    def on_load(self, ctx):
        pass

    def on_unload(self):
        pass
"""
        _write_module(self._roots.root, "flowy", meta, body, files={
            f"flow/{flow_name}.json": self._flow_payload(flow_value),
            "bindings/默认按键.json": b'{"bindings": {"13": "fire"}}',
        })

    def test_load_injects_module_profiles_as_selectable(self):
        self._write_profile_module("0.1.0", 42.0)
        self.manager.discover()
        self.manager.load("flowy")
        active, profiles = self.EF.load_profiles(self.flow_dir)
        self.assertEqual(active, "默认")
        self.assertIn("默认接线", profiles)
        self.assertAlmostEqual(
            profiles["默认接线"][self.EF.PAGE_INPUT].nodes[0].params["v"], 42.0)
        bind_active, binds = self.bs.load_binding_profiles(self.bind_dir)
        self.assertEqual(binds["默认按键"], {"13": "fire"})
        settings = self.manager.settings_for("flowy")
        self.assertEqual(settings["_injected_profiles"]["version"], "0.1.0")
        # 再装载（重启场景）不重复注入也不改当前配置
        import asyncio
        asyncio.run(self.manager.unload("flowy"))
        self.manager.load("flowy")
        active2, profiles2 = self.EF.load_profiles(self.flow_dir)
        self.assertEqual(active2, "默认")
        self.assertEqual(sorted(profiles2), ["默认", "默认接线"])

    def test_reload_keeps_user_layout_of_injected_profile(self):
        """整理过的默认配置要活过重启：装载时不能把模块自带坐标又盖回去。

        重启走的是「新建管理器再装载」，不是停用——停用会真的收回注入。
        """
        self._write_profile_module("0.1.0", 42.0)
        self.manager.discover()
        self.manager.load("flowy")
        active, profiles = self.EF.load_profiles(self.flow_dir)
        node = profiles["默认接线"][self.EF.PAGE_INPUT].nodes[0]
        node.x, node.y = 640.0, 220.0          # 用户在画布上整理过
        self.EF.save_profiles(active, profiles, self.flow_dir)

        self.manager = PluginManager(self.engine)
        self.manager.discover()
        self.manager.load("flowy")
        _active, again = self.EF.load_profiles(self.flow_dir)
        moved = again["默认接线"][self.EF.PAGE_INPUT].nodes[0]
        self.assertEqual((moved.x, moved.y), (640.0, 220.0),
                         "同版本再装载不该重置用户整理好的坐标")

        # 模块升级是另一回事：新版本自带的默认配置就该刷新自己注入过的那份。
        self._write_profile_module("0.2.0", 43.0)
        self.manager = PluginManager(self.engine)
        self.manager.discover()
        self.manager.load("flowy")
        _active, upgraded = self.EF.load_profiles(self.flow_dir)
        row = upgraded["默认接线"][self.EF.PAGE_INPUT].nodes[0]
        self.assertAlmostEqual(row.params["v"], 43.0)

    def test_same_name_user_profile_is_never_clobbered(self):
        graphs = {page: self.EF.FlowGraph(page) for page in self.EF.PAGES}
        self.EF.save_profiles("默认", {"默认": graphs, "默认接线": graphs},
                              self.flow_dir)
        self._write_profile_module("0.1.0", 42.0)
        self.manager.discover()
        self.manager.load("flowy")
        _active, profiles = self.EF.load_profiles(self.flow_dir)
        # 用户自建的同名配置原样保留：还是空画布，没有模块那颗 v=42 的卡
        row = profiles["默认接线"][self.EF.PAGE_INPUT]
        self.assertEqual(row.nodes, [])
        self.assertTrue(any("同名" in line for line in self.engine._logs))

    def test_apply_defaults_restores_and_switches(self):
        """快捷配置：默认配置被删后一键补回并切为当前配置。"""
        import os
        self._write_profile_module("0.1.0", 42.0)
        self.manager.discover()
        self.manager.load("flowy")
        # 用户删掉了模块默认的那两份配置
        os.remove(os.path.join(self.flow_dir, "默认接线.json"))
        os.remove(os.path.join(self.bind_dir, "默认按键.json"))
        self.assertEqual(list(self.EF.load_profiles(self.flow_dir)[1]), ["默认"])
        self.assertEqual(self.manager.default_profiles("flowy"),
                         {"flow": ["默认接线"], "bindings": ["默认按键"]},
                         "模块卡要能列出自带哪几套默认配置")
        notes = self.manager.apply_module_default_profiles("flowy")
        self.assertTrue(any("默认接线" in n for n in notes), notes)
        self.assertTrue(any("默认按键" in n for n in notes), notes)
        active, profiles = self.EF.load_profiles(self.flow_dir)
        self.assertEqual(active, "默认接线")
        self.assertAlmostEqual(
            profiles["默认接线"][self.EF.PAGE_INPUT].nodes[0].params["v"], 42.0)
        bind_active, binds = self.bs.load_binding_profiles(self.bind_dir)
        self.assertEqual(bind_active, "默认按键")
        self.assertEqual(binds["默认按键"], {"13": "fire"})

    def test_apply_defaults_on_already_active_profile_is_not_a_restart(self):
        """当前配置正好是模块默认那份时，别报「重启后生效」。

        switch_profile 对「已经是这一份」返回 False，界面据此提示重启，
        用户点完「一键套用」看着就像按钮坏了。
        """
        class _Runtime:
            def __init__(self, flow_dir):
                self.flow_dir = flow_dir
                self.active = "默认接线"
                self.profiles = {}

        class _Host:
            def __init__(self, runtime):
                self.runtime = runtime

            def switch_profile(self, name):
                if name not in self.runtime.profiles or name == self.runtime.active:
                    return False
                self.runtime.active = name
                return True

        self._write_profile_module("0.1.0", 42.0)
        self.manager.discover()
        self.manager.load("flowy")
        runtime = _Runtime(self.flow_dir)
        self.engine.flow = _Host(runtime)
        notes = self.manager.apply_module_default_profiles("flowy")
        self.assertTrue(any("默认接线" in n for n in notes), notes)
        self.assertFalse(any("重启后生效" in n for n in notes),
                         "已经是当前配置也算套用成功，不该提示重启")
        self.assertEqual(runtime.active, "默认接线")

    def test_uninstall_removes_injected_profiles_and_clears_marker(self):
        """停用与卸载都把默认配置移出可选列表，正在用的自动切回「默认」。

        原版就是「启用注入、停止清除」：模块不跑了还留着它的配置档，事件流与
        按键映射下拉会列出一堆引用不到动作的幽灵选项。
        """
        import asyncio
        self._write_profile_module("0.1.0", 42.0)
        self.manager.discover()
        self.manager.load("flowy")
        # 先把当前配置切到模块默认那份，模拟用户正在用它
        self.manager.apply_module_default_profiles("flowy")
        active, profiles = self.EF.load_profiles(self.flow_dir)
        self.assertEqual(active, "默认接线")
        bind_active, binds = self.bs.load_binding_profiles(self.bind_dir)
        self.assertEqual(bind_active, "默认按键")
        # 停用即清除注入
        asyncio.run(self.manager.unload("flowy"))
        active, profiles = self.EF.load_profiles(self.flow_dir)
        self.assertNotIn("默认接线", profiles)
        self.assertEqual(active, "默认")
        bind_active, binds = self.bs.load_binding_profiles(self.bind_dir)
        self.assertNotIn("默认按键", binds)
        self.assertEqual(bind_active, "默认")
        # 卸载同样收干净（此时已无注入，不该报错）
        asyncio.run(self.manager.uninstall("flowy"))
        self.assertNotIn("默认接线", self.EF.load_profiles(self.flow_dir)[1])
        # 注入标记已清：重新装载会重新注入
        self.assertNotIn("_injected_profiles",
                         self.manager.settings_for("flowy"))
        self.manager.load("flowy")
        _active, profiles = self.EF.load_profiles(self.flow_dir)
        self.assertIn("默认接线", profiles)

    def test_deactivate_clears_injected_profiles(self):
        """界面「停用」这条路（deactivate）也要收回注入的配置档。

        启用会注入、停用不清，等于每关一次模块就在配置下拉里留一份幽灵选项。
        """
        import asyncio
        self._write_profile_module("0.1.0", 42.0)
        self.manager.discover()
        self.manager.load("flowy")
        self.manager.apply_module_default_profiles("flowy")
        self.assertEqual(self.EF.load_profiles(self.flow_dir)[0], "默认接线")
        asyncio.run(self.manager.deactivate("flowy"))
        self.assertNotIn("默认接线", self.EF.load_profiles(self.flow_dir)[1])
        self.assertEqual(self.EF.load_profiles(self.flow_dir)[0], "默认")
        self.assertNotIn("默认按键", self.bs.load_binding_profiles(self.bind_dir)[1])
        self.assertFalse(self.manager.is_enabled("flowy"))
        self.assertIsNone(self.manager.instance("flowy"))

    def test_inject_recovers_after_config_wipe(self):
        """配置文件夹被清空后标记仍在：再装载必须照样补注，不能按版本早退。"""
        import shutil
        self._write_profile_module("0.1.0", 42.0)
        self.manager.discover()
        self.manager.load("flowy")
        shutil.rmtree(self.flow_dir)
        shutil.rmtree(self.bind_dir)
        import asyncio
        asyncio.run(self.manager.unload("flowy"))
        self.manager.load("flowy")
        _active, profiles = self.EF.load_profiles(self.flow_dir)
        self.assertIn("默认接线", profiles)
        _bind_active, binds = self.bs.load_binding_profiles(self.bind_dir)
        self.assertIn("默认按键", binds)

    def test_apply_does_not_switch_bindings_without_default_named(self):
        """非「默认」命名的按键映射只登记为可选，不切换当前配置。"""
        self._write_profile_module("0.1.0", 42.0)
        # 改名模块的按键映射文件：头像开火键这类分发的映射不该被自动套用
        folder = os.path.join(self._roots.root, "flowy", "bindings")
        os.rename(os.path.join(folder, "默认按键.json"),
                  os.path.join(folder, "自定义按键.json"))
        self.manager.discover()
        self.manager.load("flowy")
        self.manager.apply_module_default_profiles("flowy")
        bind_active, binds = self.bs.load_binding_profiles(self.bind_dir)
        self.assertIn("自定义按键", binds)
        self.assertEqual(bind_active, "默认")

    def test_apply_defaults_without_files_says_so(self):
        self.manager.discover()
        notes = self.manager.apply_module_default_profiles("dummy")
        self.assertEqual(len(notes), 1)
        self.assertIn("没有自带默认配置", notes[0])

    def test_version_bump_overwrites_own_injection(self):
        self._write_profile_module("0.1.0", 42.0)
        self.manager.discover()
        self.manager.load("flowy")
        import asyncio
        asyncio.run(self.manager.unload("flowy"))
        self._write_profile_module("0.2.0", 99.0)
        self.manager.discover()
        self.manager.load("flowy")
        _active, profiles = self.EF.load_profiles(self.flow_dir)
        self.assertAlmostEqual(
            profiles["默认接线"][self.EF.PAGE_INPUT].nodes[0].params["v"], 99.0)


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


def _connected_state():
    """一台真的连上的负鼠：connected + paired 同时为直才算「设备在场」。"""
    from dglab.state import EngineState, Slot

    return EngineState(
        backend="socket_v4", connected=True, paired=True,
        slots={"1": Slot(slot_id="1", type="COYOTE", battery=72)})


def _bare_state():
    """槽位里还留着历史设备，但链路已经断了。"""
    from dglab.state import EngineState, Slot

    return EngineState(
        backend="socket_v4", connected=False, paired=False,
        slots={"1": Slot(slot_id="1", type="COYOTE", battery=72)})


class OscCustomParamTests(unittest.TestCase):
    """「自定义 OSC 参数」：填一条完整地址就建立变量，事件流能读能写。"""

    def _settings(self, rows):
        cfg = _FakeConfig(os.path.join(tempfile.gettempdir(), "dgstudio_osc_custom.json"))
        cfg["prefix"] = "DGLab"
        # 真配置里这一项来自设置项默认值：没有它，设备参数名会按家族自己拼出来。
        cfg.setdefault("device_prefixes",
                       {"COYOTE": "DGLab", "OVC": "DGLabOvc", "BMTR": "DGLabBmtr"})
        cfg["custom_params"] = rows
        return cfg

    def test_rows_normalise_and_bad_addresses_drop(self):
        from modules.osc_bridge.plugin import _custom_rows

        rows = _custom_rows(self._settings([
            {"address": "/avatar/parameters/MyParam", "dir": "out", "type": "Int"},
            {"address": "custom/Param"},
            {"address": "dglab/bad name", "dir": "in"},
            {"address": "/bad//path", "dir": "in"},
            {"address": "", "dir": "out"},
            "not-a-row",
        ]))
        self.assertEqual(sorted(rows), ["avatar/parameters/MyParam", "custom/Param"])
        self.assertEqual(rows["avatar/parameters/MyParam"]["dir"], "out")
        self.assertEqual(rows["avatar/parameters/MyParam"]["type"], "Int")
        # 没写方向的按「收发」，没写类型的按浮点
        self.assertEqual(rows["custom/Param"]["dir"], "inout")
        self.assertEqual(rows["custom/Param"]["type"], "Float")

    def test_link_params_registers_custom_rows(self):
        from modules.osc_bridge.plugin import OscModule

        mod = OscModule()
        mod.bridge = SimpleNamespace(
            get_state=lambda: (_ for _ in ()).throw(RuntimeError("无设备")),
            param_names=lambda: ["avatar/parameters/Heard"],
        )
        mod.ctx = SimpleNamespace(
            settings=self._settings([
                {"address": "/avatar/parameters/MyParam", "dir": "out", "type": "Int"}]),
            engine=SimpleNamespace(get_state=lambda: None),
            log=lambda *_: None)
        by_name = {row["name"]: row for row in mod.link_params()}
        self.assertIn("avatar/parameters/MyParam", by_name,
                      "自定义地址要进宿主变量登记，才能在事件流建卡")
        self.assertEqual(by_name["avatar/parameters/MyParam"]["dir"], "out")
        self.assertTrue(by_name["avatar/parameters/MyParam"]["renamable"])
        # 收包遇到的未登记地址不再自动铺一行：要当变量用就得先登记。
        self.assertNotIn("avatar/parameters/Heard", by_name)

    def _module(self, rows, state):
        from modules.osc_bridge.plugin import OscModule

        mod = OscModule()
        mod.bridge = None
        mod.ctx = SimpleNamespace(
            settings=self._settings(rows),
            engine=SimpleNamespace(get_state=lambda: state),
            log=lambda *_: None)
        return mod

    def test_registered_rows_follow_the_connected_device(self):
        """设备参数按当前在连设备现算：连上才有行，断了就收回。"""
        mod = self._module([], _connected_state())
        by_name = {row["name"]: row for row in mod.link_params()}
        self.assertIn("avatar/parameters/DGLabStrengthA", by_name)
        self.assertIn("DGLab/Action", by_name)
        self.assertTrue(all(row["renamable"] for row in by_name.values()),
                        "登记行都要能改名，改名才改得出收发地址")
        # temp_specs 是同一份表，只是换成宿主卸载时清残值用的 key 形状
        self.assertEqual({s["key"] for s in mod.temp_specs()}, set(by_name))
        self.assertEqual(self._module([], _bare_state()).link_params(), [],
                         "设备不在场就不登记：变量表里不留拿不到值的行")

    def test_device_rows_do_not_shadow_custom_rows(self):
        """同名时设备登记行优先，自定义那行不再另造一份。"""
        mod = self._module([
            {"address": "/avatar/parameters/DGLabStrengthA", "dir": "in",
             "type": "Float"}], _connected_state())
        by_name = {row["name"]: row for row in mod.link_params()}
        self.assertEqual(by_name["avatar/parameters/DGLabStrengthA"]["dir"],
                         "inout")
        self.assertNotIn("自定义", by_name["avatar/parameters/DGLabStrengthA"]["label"],
                         "同名时以设备登记那行为准，别出现两条读不出的地址")

    def test_derived_rows_are_the_config_page_table(self):
        """配置页「设备 OSC 参数」那一栏：带键名与读写类型，界面按它只让改名。"""
        mod = self._module([], _connected_state())
        rows = mod.derived_rows()["device_params"]
        self.assertTrue(rows)
        self.assertTrue(all({"key", "address", "dir", "type", "label"} == set(r)
                            for r in rows))
        strength = [r for r in rows if r["address"].endswith("StrengthA")]
        self.assertEqual([(r["dir"], r["key"]) for r in strength],
                         [("inout", "in_strength_a")],
                         "既下发又回读的参数记成读写，一行只管一个键")
        self.assertEqual(self._module([], _bare_state()).derived_rows(),
                         {"device_params": []})
        # 不落盘：表读的是现算结果，配置文件里不该出现这一项
        self.assertNotIn("device_params", self._settings([]))

    def test_rename_from_the_config_page_moves_both_keys(self):
        """在配置页改名 = 走引擎那条改名：两个同名键一起换，表也立刻跟着新名字。"""
        mod = self._module([], _connected_state())
        old = "avatar/parameters/DGLabStrengthA"
        new = "avatar/parameters/MyStrengthA"
        self.assertEqual(mod.rename_var(old, new), "")
        table = mod.ctx.settings["param_names"]
        self.assertEqual(table.get("in_strength_a"), "MyStrengthA",
                         "下发那一路要跟着换地址")
        self.assertEqual(table.get("COYOTE.StrengthA"), "MyStrengthA",
                         "回读那一路也要跟着换，不然收发两套名字")
        self.assertEqual([r["address"] for r in mod.derived_rows()["device_params"]
                          if r["address"].endswith("StrengthA")], [new])
        self.assertIn(new, {row["name"] for row in mod.link_params()})

    def test_rename_rejects_bad_or_taken_name(self):
        mod = self._module([], _connected_state())
        taken = "avatar/parameters/DGLabBattery"
        self.assertIn("已被其它 OSC 参数占用",
                      mod.rename_var("avatar/parameters/DGLabStrengthA", taken))
        self.assertIn("字母开头",
                      mod.rename_var("avatar/parameters/DGLabStrengthA",
                                     "avatar/parameters/9bad"))
        self.assertEqual(mod.ctx.settings.get("param_names") or {}, {},
                         "两次失败的改名都不该留下覆盖")
        self.assertIn("avatar/parameters/DGLabStrengthA",
                      {r["address"] for r in mod.derived_rows()["device_params"]},
                      "失败之后表里还是原来那个地址")

    def test_lifecycle_hooks_do_not_write_a_second_table(self):
        """设备变化 / 重新加载这两个钩子只通知界面，不再往配置里写登记副本。"""
        import asyncio

        from modules.osc_bridge.plugin import OscModule

        cfg = self._settings([])
        emitted: list[str] = []
        mod = OscModule()
        mod.ctx = SimpleNamespace(
            settings=cfg,
            engine=SimpleNamespace(get_state=lambda: _connected_state()),
            events=SimpleNamespace(emit=lambda kind, *a: emitted.append(kind)),
            log=lambda *_: None)
        mod.bridge = None
        mod._on_devices_changed(_connected_state())
        self.assertEqual(emitted, ["modules_changed"],
                         "设备一连上要让界面重取那张现算的表，别的都不做")
        self.assertNotIn("device_params", cfg,
                         "设备参数不落配置文件：存下来就是会过期的第二份状态")
        asyncio.run(mod.reload_config())     # 桥没起来也要能走通，别按 NameError 挂掉

    def test_push_values_honours_custom_direction_and_type(self):
        from modules.osc_bridge.bridge import OscBridge, OscConfig
        from modules.osc_bridge.plugin import OSC_CONFIG_DEFAULTS

        bridge = OscBridge(OscConfig(
            {"custom_params": [
                {"address": "avatar/parameters/OnlyIn", "dir": "in", "type": "Float"},
                {"address": "avatar/parameters/Count", "dir": "out", "type": "Int"},
            ]}, defaults=OSC_CONFIG_DEFAULTS), lambda: None)
        sent: list[tuple] = []
        bridge.send_value = lambda addr, value: sent.append((addr, value))
        bridge.engine.temps.update({
            "avatar/parameters/OnlyIn": 3.0,
            "avatar/parameters/Count": 2.0,
            "avatar/parameters/Plain": 1.5,
        })
        bridge._push_values()
        out = dict(sent)
        self.assertNotIn("/avatar/parameters/OnlyIn", out,
                         "只收的地址不该回头发给 VRChat")
        # pythonosc 按实参类型打标签，声明整数的要发成 int
        self.assertEqual(out["/avatar/parameters/Count"], 2)
        self.assertEqual(out["/avatar/parameters/Plain"], 1.5)
        bridge.close()

    def test_rename_custom_row_writes_back_the_table(self):
        from modules.osc_bridge.plugin import _rename_custom

        settings = self._settings([
            {"address": "/avatar/parameters/Old", "dir": "out", "type": "Float"},
            {"address": "avatar/parameters/Other", "dir": "in"},
        ])
        self.assertEqual(_rename_custom(settings, "avatar/parameters/Old",
                                        "/avatar/parameters/New"), "")
        addresses = [r["address"] for r in settings["custom_params"]]
        self.assertIn("avatar/parameters/New", addresses)
        self.assertNotIn("/avatar/parameters/Old", addresses)
        self.assertTrue(settings.saved, "改名要落盘")
        # 非自定义行返回 None：交给内置参数的改名流程
        self.assertIsNone(_rename_custom(settings, "avatar/parameters/Action", "x"))
        self.assertEqual(
            _rename_custom(settings, "avatar/parameters/New", "avatar/parameters/Other"),
            "「avatar/parameters/Other」已在自定义参数里，换一个地址")
        self.assertEqual(
            _rename_custom(settings, "avatar/parameters/New", "bad name"),
            "OSC 地址要按 头像/参数/名字 那样分段，每段字母开头，可用字母 / 数字 / 下划线")


if __name__ == "__main__":
    unittest.main()

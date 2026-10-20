"""桥的方法表要能真的导入、每一条都取得到实现。

历史教训：往 `METHODS` 里加了 `"modules.apply_defaults": _modules_apply_defaults`
却漏了函数本体——引擎那 300 多个测试全绿（没有一个导入过 bridge.server），
打包版一启动就 `NameError` 弹错误框，界面整个连不上侧车。
"""

from __future__ import annotations

import unittest

import bridge.server as srv


class BridgeMethodTableTests(unittest.TestCase):

    def test_every_method_resolves(self):
        self.assertTrue(srv.METHODS, "方法表空的说明导入没走通")
        bad = sorted(name for name, fn in srv.METHODS.items() if not callable(fn))
        self.assertEqual(bad, [], "这些方法名指向了取不到的实现")

    def test_ui_entry_points_are_registered(self):
        # 这几条是这轮界面功能直接依赖的，漏一条就是界面上某颗按钮没反应。
        for name in ("engine.qr", "engine.channels", "engine.disconnect_channel",
                     "engine.intensity_params", "engine.wave_history",
                     "flow.nodes_move", "modules.defaults", "modules.apply_defaults",
                     "modules.deps", "modules.mods_install",
                     "bindings.get", "detectors.state"):
            self.assertIn(name, srv.METHODS, f"桥没注册 {name}")

    def test_no_read_only_method_can_write(self):
        # 只读集合里混进写方法的话，鉴权分级就形同虚设。
        writable = {"engine.connect_v4", "engine.ble_connect", "flow.node_add",
                    "modules.apply_defaults", "engine.set_strength"}
        overlap = writable & set(srv.Bridge.READ_ONLY)
        self.assertFalse(overlap, f"这些写方法被标成只读：{sorted(overlap)}")
        # 反过来：只读的必须真在方法表里，否则连接后第一帧就取不到状态。
        self.assertTrue(set(srv.Bridge.READ_ONLY) <= set(srv.METHODS),
                        "只读清单里有方法表没注册的名字")


class BindingActionGroupsTests(unittest.TestCase):
    """按键动作清单要带「是不是设备内置」的标记。

    界面上分两组列：设备动作本身就有十几条，键盘注入与随模块注册的「发送 OSC」
    要是混在尾巴上，用户翻不到就会当成「这功能没搬过来」。
    """

    def test_key_and_module_actions_are_flagged(self):
        from bridge import bindings as bd
        from plugins import ButtonAction

        class _Modules:
            def button_actions(self):
                return [ButtonAction("osc", "发送 OSC 参数…",
                                     argument_placeholder="/avatar/parameters/…"),
                        ButtonAction("margin_guard_toggle", "边控闭环 暂停/恢复")]

        class _Engine:
            modules = _Modules()

        rows = {row["key"]: row for row in bd._actions(_Engine())}
        self.assertTrue(rows["a_strength_up"]["builtin"])
        self.assertFalse(rows["key"]["builtin"],
                         "模拟键盘按键归在「键盘与模块动作」那一组")
        self.assertFalse(rows["osc"]["builtin"])
        self.assertEqual(rows["osc"]["argument"], "/avatar/parameters/…")


class FlowProfileRemoveTests(unittest.TestCase):
    """删除当前那份事件流配置时，桥要先替用户切走。

    界面上「删除」就挂在当前配置那一排，如果回一句「先切到别的配置」，
    用户看到的就是这颗按钮没反应。
    """

    def test_deleting_the_active_profile_switches_first(self):
        import tempfile
        from dglab import event_flow as ef

        path = tempfile.mkdtemp(suffix="_flowremove")
        self.addCleanup(_rmtree, path)
        graphs = {page: ef.FlowGraph(page) for page in ef.PAGES}
        runtime = ef.FlowRuntime(ef.Catalog(), graphs, base_dir=path)
        runtime.new_profile("演出")
        self.assertEqual(runtime.active, "演出")

        class _Host:
            def __init__(self, rt):
                self.runtime = rt
                self.switched: list[str] = []

            def switch_profile(self, name):
                self.switched.append(name)
                return self.runtime.switch_profile(name)

        host = _Host(runtime)

        class _Engine:
            flow = host

        import bridge.flow as fl
        original = fl._state
        fl._state = lambda e, p: {
            "profiles": {"active": e.flow.runtime.active,
                         "order": e.flow.runtime.profile_names}}
        self.addCleanup(setattr, fl, "_state", original)

        out = fl.profile_remove(_Engine(), {"name": "演出"})
        self.assertEqual(out["error"], "")
        self.assertEqual(host.switched, ["默认"])
        self.assertEqual(out["profiles"]["active"], "默认")
        self.assertNotIn("演出", runtime.profile_names)


class FlowIdleSurfaceTests(unittest.TestCase):
    """界面上「这张卡失效」要能当场收敛：状态与实时值那一轮都带着判断结果。

    红字原来只在 flow.state 那份快照里回传，界面按秒拉实时值时不带着它，
    用户把执行流接上以后卡片还挂着「这张没执行流」的注释。
    """

    def test_state_and_live_carry_the_idle_cards(self):
        import tempfile
        from dglab import event_flow as ef
        import bridge.flow as fl

        path = tempfile.mkdtemp(suffix="_flowidle")
        self.addCleanup(_rmtree, path)
        graphs = {page: ef.FlowGraph(page) for page in ef.PAGES}
        runtime = ef.FlowRuntime(ef.Catalog(), graphs, base_dir=path)
        graph = graphs[ef.PAGE_INPUT]
        driver = graph.add_node(runtime.catalog, "mod.period", 0.0, 0.0)
        driver.params["period_ms"] = 10000.0
        live = graph.add_node(runtime.catalog, "var.temp_write", 400.0, 0.0)
        live.params["name"] = "fire"
        graph.connect(runtime.catalog, driver, 0, live, -1)
        branch = graph.add_node(runtime.catalog, "flow.branch", 200.0, 200.0)
        dead = graph.add_node(runtime.catalog, "var.temp_write", 400.0, 200.0)
        dead.params["name"] = "fire"
        graph.connect(runtime.catalog, branch, 0, dead, -1)
        runtime.tick(now=0.0)

        class _Host:
            def __init__(self, rt):
                self.runtime = rt
                self.catalog = rt.catalog

            def missing_modules(self):
                return []

        engine = type("_Engine", (), {"flow": _Host(runtime)})()

        state = fl._state(engine, {"page": "input"})
        self.assertEqual(set(state["idle"]), {branch.id, dead.id}, state["idle"])
        self.assertIn(dead.id, state["errors"])
        self.assertNotIn(live.id, state["errors"])

        live_out = fl.live_values(engine, {"page": "input"})
        self.assertEqual(set(live_out), {"values", "errors", "idle", "catalog_rev"},
                         live_out)
        self.assertEqual(set(live_out["idle"]), {branch.id, dead.id})
        self.assertIn(live.id, live_out["values"])

        # 接上执行流：下一拍的错误清单里就没有它了，灰显与注释跟着收掉。
        graph.connect(runtime.catalog, driver, 0, branch, -1)
        runtime.tick(now=0.05)
        self.assertEqual(runtime.idle, [], runtime.idle)
        self.assertNotIn(dead.id, fl.live_values(engine, {"page": "input"})["idle"])


class FlowDataFlowTests(unittest.TestCase):
    """概览页那张探测卡取的线路清单：桥只转发引擎的判断，键名要和界面对上。"""

    def test_dataflow_returns_lines(self):
        import tempfile
        from dglab import event_flow as ef
        import bridge.flow as fl

        path = tempfile.mkdtemp(suffix="_flowdata")
        self.addCleanup(_rmtree, path)
        catalog = ef.Catalog()
        catalog.refresh(
            [{"id": "osc_bridge", "name": "OSC 桥接模块", "short": "OSC",
              "params": [{"name": "osc_strength", "label": "OSC 强度",
                          "dir": "inout"}]}],
            1)
        graphs = {page: ef.FlowGraph(page) for page in ef.PAGES}
        runtime = ef.FlowRuntime(catalog, graphs, base_dir=path)
        graph = graphs[ef.PAGE_INPUT]
        reader = graph.add_node(catalog,
                                "mod.read.osc_bridge.osc_strength", 0.0, 0.0)
        sink = graph.add_node(catalog, "core.write.in_strength_a", 320.0, 0.0)
        graph.connect(catalog, reader, 0, sink, 0)

        class _Host:
            def __init__(self, rt):
                self.runtime = rt
                self.catalog = rt.catalog

        engine = type("_Engine", (), {"flow": _Host(runtime)})()
        out = fl.data_flow(engine, {})
        self.assertEqual(list(out), ["lines"], out)
        self.assertEqual(out["lines"][0]["kind"], "to_core", out)
        self.assertEqual(out["lines"][0]["otherShort"], "OSC", out)


class ModuleDepsReadmeTests(unittest.TestCase):
    """详情页那两条附加信息：pip 依赖要按 requirements.txt 报，README 只读模块目录里的。"""

    def _engine(self, root: str):
        import types

        store = types.SimpleNamespace(
            requirements_of=lambda mid: (["python-osc>=1.9"], "requirements.txt"),
            deps_dir=lambda mid: "",
        )
        modules = types.SimpleNamespace(
            store=store,
            module_dir=lambda mid: root if mid == "osc_bridge" else None,
        )
        return types.SimpleNamespace(modules=modules)

    def test_deps_rows_come_from_requirements_txt(self):
        import bridge.deps as bd

        out = bd._deps_payload(self._engine("C:/nowhere"), "osc_bridge")
        self.assertEqual([r["req"] for r in out["rows"]], ["python-osc>=1.9"], out)
        self.assertEqual(out["source"], "requirements.txt", "界面上要能说出依赖是在哪儿声明的")

    def test_readme_reads_only_inside_the_module_dir(self):
        import os
        import tempfile
        import bridge.deps as bd

        root = tempfile.mkdtemp(suffix="_readme")
        self.addCleanup(_rmtree, root)
        engine = self._engine(root)
        self.assertTrue(bd.readme(engine, {"module_id": "osc_bridge"})["missing"],
                        "模块没带 README 时要如实报缺")

        with open(os.path.join(root, "README.md"), "w", encoding="utf-8") as fh:
            fh.write("# OSC 桥\n\n说明文字")
        out = bd.readme(engine, {"module_id": "osc_bridge"})
        self.assertFalse(out["missing"])
        self.assertIn("OSC 桥", out["text"])
        self.assertEqual(out["path"], "osc_bridge/README.md")

        self.assertTrue(bd.readme(engine, {"module_id": "unknown"})["missing"])
        self.assertTrue(bd.readme(engine, {"module_id": "../.."})["missing"])


def _rmtree(path: str) -> None:
    import shutil
    shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

class MarketUpdateFlagTests(unittest.TestCase):
    """市场清单的「更新」只在市场版本比装着的版本新时出现。"""

    def _rows(self, installed, entries):
        import asyncio
        from types import SimpleNamespace

        import bridge.server as server

        store = SimpleNamespace(fetch_market=lambda force=False: entries)
        modules = SimpleNamespace(store=store, list_modules=lambda: installed)
        engine = SimpleNamespace(modules=modules)
        return asyncio.run(server._market_fetch(engine, {}))

    def test_update_only_when_the_market_version_is_newer(self):
        rows = self._rows(
            [{"id": "osc_bridge", "version": "1.21.0"},
             {"id": "vision_link", "version": "0.4.1"}],
            [{"id": "osc_bridge", "name": "OSC", "version": "1.21.0"},
             {"id": "vision_link", "name": "视觉", "version": "0.4.2"},
             {"id": "sound_link", "name": "音频", "version": "0.5.3"}])
        by = {r["id"]: r for r in rows}
        self.assertEqual((by["osc_bridge"]["installed"],
                          by["osc_bridge"]["update"]), (True, False))
        self.assertEqual((by["vision_link"]["installed"],
                          by["vision_link"]["update"]), (True, True))
        self.assertEqual((by["sound_link"]["installed"],
                          by["sound_link"]["update"]), (False, False))

    def test_versions_compare_by_number_not_by_string(self):
        # 0.10.0 比 0.9.0 新；按字符串比会反过来，用户就永远等不到更新。
        newer = self._rows([{"id": "x", "version": "0.9.0"}],
                            [{"id": "x", "version": "0.10.0"}])
        self.assertTrue(newer[0]["update"])
        # 装的是开发版（比市场里的新）不该提示更新。
        dev = self._rows([{"id": "y", "version": "2.0.0"}],
                         [{"id": "y", "version": "1.9.9"}])
        self.assertFalse(dev[0]["update"])

_TEST_N = int(
    "af3d5368df57754e50a4e08a62b344c56d991acd7701342c2a8dee543019fdf5"
    "2d150ecb98f175fb52b03f43b0cf3e97f0d2bbcd69671c54142100cee692f053"
    "c52deb620b512ef05b9a5e8bd2f925f3b66e0c8dbf047883427429d153bb43e0"
    "62bbe6c2740ca5549fcb14b154894ba99caad46974cf55bf7fe62756668b5aa3", 16)
_TEST_E = 65537
_TEST_D = int(
    "27107b331232ab664968efdb39946ffcb8e95c05b8852eadb7d0caf3be8354cf"
    "1e7d26beb13a068c7f8ae5977fe73b3360ce816a119898428ff8297776040489"
    "b0c524869ab37e9974011747c258e6e32210db52c5e3c3b15be0f5487bac7036"
    "c911cf1cf224bcfda7bd91a5382347fa7c09a2f2d3e3b46b7f2350266c2344b5", 16)

#: PKCS#1 v1.5 填充头；测试签名与运行时校验共用同一份。
_PAD_HEAD = bytes([0, 1])
_PAD_SEP = bytes([0])


def _test_sign(manifest: bytes) -> bytes:
    """测试用私钥按 PKCS#1 v1.5 签一份 manifest（生产私钥不在仓库里）。"""
    import hashlib

    from bridge.update import _DIGEST_INFO

    size = (_TEST_N.bit_length() + 7) // 8
    digest = hashlib.sha256(manifest).digest()
    padding = size - 3 - len(_DIGEST_INFO) - len(digest)
    block = (_PAD_HEAD + bytes([0xFF]) * padding + _PAD_SEP
             + _DIGEST_INFO + digest)
    return pow(int.from_bytes(block, "big"), _TEST_D, _TEST_N).to_bytes(size, "big")


class UpdateSourceOrderTests(unittest.TestCase):
    """取包顺序：直连 → 常用镜像 → 本地端口代理。"""

    def test_direct_then_mirrors_then_proxies(self):
        from types import SimpleNamespace

        import bridge.update as update
        from module_store import MIRROR_PRESETS

        engine = SimpleNamespace(
            config={"modules_market": {"proxy": "http://127.0.0.1:1234"}})
        labels = [label for label, _opener, _url in
                  update._sources("https://github.com/a/b.zip", engine)]
        self.assertEqual(labels[0], "直连")
        self.assertEqual(labels[1:4], [m.rstrip("/") for m in MIRROR_PRESETS])
        self.assertTrue(labels[4].startswith("代理 http://127.0.0.1:1234"))
        self.assertTrue(all(label.startswith("代理") for label in labels[4:]))


class UpdateCheckTests(unittest.TestCase):
    """软件自更新：版本比较、哈希与签名校验、失败拒绝安装。"""

    def _check(self, tag: str) -> dict:
        import json
        from types import SimpleNamespace
        from unittest import mock

        import bridge.update as update

        release = {
            "tag_name": tag,
            "name": f"DGStudio {tag}",
            "body": "- 修复若干问题",
            "html_url": "https://example.invalid/release",
            "assets": [
                {"name": f"DGStudio_{tag}_win64.zip", "size": 123,
                 "browser_download_url": "https://example.invalid/a.zip"},
                {"name": "source.zip", "size": 1,
                 "browser_download_url": "https://example.invalid/s.zip"},
            ],
        }
        with mock.patch.object(
                update, "_read",
                return_value=(json.dumps(release).encode(), "直连")):
            return update.check(SimpleNamespace(config={}), {})

    def test_newer_release_is_an_update(self):
        from dglab.version import APP_VERSION

        info = self._check("v9.9.9")
        self.assertTrue(info["ok"])
        self.assertEqual(info["current"], APP_VERSION)
        self.assertEqual(info["latest"], "9.9.9")
        self.assertTrue(info["has_update"])
        self.assertEqual(info["asset"]["name"], "DGStudio_v9.9.9_win64.zip")
        self.assertEqual(info["asset"]["size"], 123)
        self.assertEqual(info["notes"], "- 修复若干问题")

    def test_same_or_older_release_is_not_an_update(self):
        from dglab.version import APP_VERSION

        self.assertFalse(self._check("v" + APP_VERSION)["has_update"])
        self.assertFalse(self._check("v0.0.1")["has_update"])

    def test_download_refuses_without_a_newer_release(self):
        import bridge.update as update

        update._LAST.clear()
        result = update.download(None, {})
        self.assertFalse(result["ok"])
        self.assertIn("最新", result["error"])

    def test_apply_refuses_when_running_from_source(self):
        import bridge.update as update

        result = update.apply(None, {"path": "x.zip"})
        self.assertFalse(result["ok"])
        self.assertIn("源码", result["error"])


class UpdateFallbackTests(unittest.TestCase):
    """API 限流时走 releases/latest 跳转，安装包按命名约定拼出来。"""

    def test_redirect_fallback_resolves_tag_and_asset(self):
        from unittest import mock

        import bridge.update as update

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def geturl(self):
                return "https://github.com/KelierAndes/DG-Lab-Studio/releases/tag/v1.2.3"

        class _Opener:
            def open(self, request, timeout=0):
                return _Resp()

        with mock.patch.object(update, "_sources",
                               return_value=[("直连", _Opener(),
                                              "https://example.invalid")]),                 mock.patch.object(update, "_asset_size", return_value=42):
            info = update._check_via_redirect({})
        self.assertEqual(info["tag"], "v1.2.3")
        self.assertEqual(info["asset_name"], "DGStudio_v1.2.3_win64.zip")
        self.assertEqual(info["asset_url"],
                         "https://github.com/KelierAndes/DG-Lab-Studio/"
                         "releases/download/v1.2.3/DGStudio_v1.2.3_win64.zip")
        self.assertEqual(info["size"], 42)

    def test_check_uses_fallback_when_api_fails(self):
        from unittest import mock

        import bridge.update as update

        with mock.patch.object(update, "_fetch_json",
                               side_effect=RuntimeError("403 rate limit")),                 mock.patch.object(update, "_check_via_redirect",
                                  return_value={
                                      "tag": "v9.9.9",
                                      "asset_name": "DGStudio_v9.9.9_win64.zip",
                                      "asset_url": "https://example.invalid/a.zip",
                                      "size": 7, "name": "DGStudio v9.9.9",
                                      "notes": "", "page": "https://example.invalid",
                                  }):
            info = update.check(None, {})
        self.assertTrue(info["ok"], info)
        self.assertTrue(info["has_update"])
        self.assertEqual(info["latest"], "9.9.9")
        self.assertEqual(info["source"], "releases/latest")
        self.assertEqual(info["asset"]["size"], 7)


class UpdateVerifyTests(unittest.TestCase):
    """哈希 + 签名：任何一项对不上都不能进入安装流程。"""

    def setUp(self):
        import hashlib
        import shutil
        import tempfile
        from pathlib import Path

        import bridge.update as update

        self.update = update
        self.dir = Path(tempfile.mkdtemp(prefix="dgstudio_update_"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.payload = b"PK fake zip payload"
        self.manifest = (hashlib.sha256(self.payload).hexdigest()
                         + "  DGStudio_test_win64.zip\n").encode()
        self.signature = _test_sign(self.manifest)
        update._LAST.clear()
        update._LAST.update({
            "has_update": True,
            "asset": {"url": "https://example.invalid/a.zip",
                      "name": "DGStudio_test_win64.zip",
                      "size": len(self.payload)},
        })

    def _download(self, manifest=None, signature=None, missing_sig=False):
        from unittest import mock

        manifest = self.manifest if manifest is None else manifest
        signature = self.signature if signature is None else signature

        def fake_stream(_url, _engine, target, _total):
            target.write_bytes(self.payload)
            return "直连"

        def fake_read(url, _engine, **_kwargs):
            if url.endswith(".sha256"):
                return manifest, "直连"
            if url.endswith(".sig"):
                if missing_sig:
                    raise RuntimeError("404")
                return signature, "直连"
            raise RuntimeError("unexpected")

        with mock.patch.object(self.update, "_update_dir",
                               return_value=self.dir), \
                mock.patch.object(self.update, "_stream", fake_stream), \
                mock.patch.object(self.update, "_read", fake_read), \
                mock.patch.object(self.update.update_key, "MODULUS", _TEST_N), \
                mock.patch.object(self.update.update_key, "EXPONENT", _TEST_E):
            return self.update.download(None, {})

    def test_good_package_is_verified(self):
        result = self._download()
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["verified"])
        self.assertTrue(result["path"].endswith(".zip"))
        self.assertEqual(result["size"], len(self.payload))

    def test_wrong_hash_is_rejected(self):
        bad = ("0" * 64 + "  x.zip\n").encode()
        result = self._download(manifest=bad)
        self.assertFalse(result["ok"])
        self.assertIn("哈希", result["error"])

    def test_missing_signature_is_rejected(self):
        result = self._download(missing_sig=True)
        self.assertFalse(result["ok"])
        self.assertIn("签名", result["error"])

    def test_tampered_signature_is_rejected(self):
        result = self._download(signature=bytes(128))
        self.assertFalse(result["ok"])
        self.assertIn("签名", result["error"])

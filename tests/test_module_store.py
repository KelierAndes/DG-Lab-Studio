from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
import unittest.mock
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import module_store
from module_store import (ModuleStore, _embedded_python_dir,
                          deps_abi_ok, parse_requirements_text,
                          requirement_name, requirement_satisfied, version_key,
                          stale_wheels, wheel_abi_ok, pip_install)
from plugins import PluginManager, _CLEANUP_MARKER
from types import SimpleNamespace

from ui.modules_page import online_section_state


class _FakeConfig(dict):
    def __init__(self, path):
        super().__init__()
        self.path = path

    def save(self):
        pass


class _FakeEngine:
    def __init__(self):
        from dglab.state import StateEvents

        self.events = StateEvents()
        self.config = _FakeConfig(
            os.path.join(tempfile.mkdtemp(prefix="dgstudio_store_"),
                         "config.json"))
        self._logs: list[str] = []

    def _log(self, msg: str) -> None:
        self._logs.append(msg)

    def submit(self, coro):
        import asyncio

        return asyncio.run_coroutine_threadsafe(coro,
                                                asyncio.new_event_loop())


def _file_url(path: str) -> str:
    return "file:///" + os.path.abspath(path).replace("\\", "/")


def _mapped_pyd(path: str):
    import ctypes
    import _queue

    shutil.copy2(_queue.__file__, path)
    return ctypes.WinDLL(path)


def _unmap(lib) -> None:
    import ctypes

    kernel32 = ctypes.windll.kernel32
    kernel32.FreeLibrary.argtypes = [ctypes.c_void_p]
    kernel32.FreeLibrary(ctypes.c_void_p(lib._handle))


def _stage_runtime(base: str) -> str:
    py_dir = os.path.join(base, "_python")
    os.makedirs(py_dir, exist_ok=True)
    with open(os.path.join(py_dir, "python.exe"), "wb") as f:
        f.write(b"")
    return py_dir


def _frozen_exe(base: str):
    return unittest.mock.patch.object(
        sys, "executable", os.path.join(base, "DGStudio.exe"))


def _make_sub_repo_zip(path: str, repo: str, module_id: str,
                       version: str, requirements: str = "") -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"{repo}-main/README.md", f"# {repo}\n")
        zf.writestr(
            f"{repo}-main/modules/{module_id}/plugin.py",
            'META = {"id": "%s", "name": "%s", "version": "%s"}\n'
            % (module_id, module_id, version))
        zf.writestr(
            f"{repo}-main/modules/{module_id}/extra/inner.py", "# inner\n")
        if requirements:
            zf.writestr(f"{repo}-main/modules/{module_id}/requirements.txt",
                        requirements)


def _market_yaml(repo: str, module_id: str, version: str,
                 extra_declared: tuple[str, ...] = ()) -> bytes:
    listed = ["README.md", f"modules/{module_id}/plugin.py"]
    listed += [f"modules/{module_id}/{rel}" for rel in extra_declared]
    files = ", ".join(listed)
    return (
        "schema: 1\n"
        "modules:\n"
        f"  - id: {module_id}\n"
        f"    repo: {repo}\n"
        "    path: modules/%s\n" % module_id +
        "    branch: main\n"
        "    author: KelierAndes\n"
        f"    name: {module_id}\n"
        f"    version: {version}\n"
        '    description: "demo"\n'
        "    default_enabled: false\n"
        "    requirements: []\n"
        f"    files: [{files}]\n"
    ).encode("utf-8")


class RequirementCheckTests(unittest.TestCase):
    def test_requirement_name_parses_specs(self):
        self.assertEqual(requirement_name("python-osc>=1.9"), "python-osc")
        self.assertEqual(requirement_name("onnxruntime==1.19.2"), "onnxruntime")
        self.assertEqual(requirement_name("!PyYAML"), "pyyaml")
        self.assertEqual(requirement_name("Shapely"), "shapely")

    def test_parse_requirements_text(self):
        text = (
            "# 注释行\n"
            "\n"
            "python-osc>=1.9\n"
            "  opencv-python-headless>=4.10  \n"
            "!rapidocr-onnxruntime>=1.4.4\n"
        )
        self.assertEqual(parse_requirements_text(text),
                         ["python-osc>=1.9", "opencv-python-headless>=4.10",
                          "!rapidocr-onnxruntime>=1.4.4"])

    def test_satisfied_for_installed_package(self):
        self.assertTrue(requirement_satisfied("bleak>=3.0"))

    def test_missing_for_nonexistent_package(self):
        self.assertFalse(requirement_satisfied(
            "dgstudio-definitely-not-a-package>=1.0"))

    def test_satisfied_via_import_mapping(self):
        self.assertTrue(requirement_satisfied("opencv-python-headless>=4.0"))

    def test_version_key_orders(self):
        self.assertLess(version_key("1.4.9"), version_key("1.5.0"))
        self.assertEqual(version_key("1.5"), version_key("1.5.0"))
        self.assertLess(version_key("0.9.9"), version_key("0.10.0"))


class EmbeddedPythonTests(unittest.TestCase):

    def test_embedded_python_empty_when_not_frozen(self):
        self.assertEqual(_embedded_python_dir(), "")

    def test_embedded_python_resolved_next_to_exe(self):
        base = tempfile.mkdtemp(prefix="dgstudio_embedpy_")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        py_dir = _stage_runtime(base)
        with unittest.mock.patch.object(sys, "frozen", True, create=True), \
                _frozen_exe(base):
            self.assertEqual(_embedded_python_dir(), py_dir)
        os.remove(os.path.join(py_dir, "python.exe"))
        with unittest.mock.patch.object(sys, "frozen", True, create=True), \
                _frozen_exe(base):
            self.assertEqual(_embedded_python_dir(), "")

    def _capture_run(self):
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["cmd"], seen["kwargs"] = cmd, kwargs
            return SimpleNamespace(returncode=0, stdout="ok", stderr="")

        return seen, unittest.mock.patch.object(module_store.subprocess,
                                                "run", fake_run)

    def test_frozen_install_spawns_embedded_python(self):
        base = tempfile.mkdtemp(prefix="dgstudio_embedpy_")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        py_dir = _stage_runtime(base)
        target = os.path.join(base, "_deps")
        seen, patched = self._capture_run()
        with unittest.mock.patch.object(sys, "frozen", True, create=True), \
                _frozen_exe(base), patched:
            ok, out = pip_install(["python-osc>=1.9"], target=target)
        self.assertTrue(ok)
        self.assertEqual(out, "ok")
        self.assertEqual(seen["cmd"][:7],
                         [os.path.join(py_dir, "python.exe"), "-X", "utf8",
                          "-E", "-s", "-m", "pip"])
        self.assertIn("--target", seen["cmd"])
        self.assertEqual(seen["cmd"][seen["cmd"].index("--target") + 1],
                         target)
        self.assertIn("python-osc>=1.9", seen["cmd"])
        env = seen["kwargs"]["env"]
        self.assertTrue(all(not k.upper().startswith("PYTHON")
                            for k in env))
        self.assertEqual(env["PIP_NO_INPUT"], "1")

    def test_frozen_install_optional_group_uses_no_deps(self):
        base = tempfile.mkdtemp(prefix="dgstudio_embedpy_")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        _stage_runtime(base)
        seen, patched = self._capture_run()
        with unittest.mock.patch.object(sys, "frozen", True, create=True), \
                _frozen_exe(base), patched:
            ok, _out = pip_install(["!rapidocr-onnxruntime>=1.4.4"],
                                   target=os.path.join(base, "_deps"))
        self.assertTrue(ok)
        self.assertIn("--no-deps", seen["cmd"])
        # -E -s：内置 Python 不能串到宿主机的用户级 site-packages，否则 pip
        # 报「already satisfied」而跳过安装，换台干净机器就缺依赖
        self.assertIn("-E", seen["cmd"])
        self.assertIn("-s", seen["cmd"])

    def test_frozen_install_without_runtime_reports_error(self):
        base = tempfile.mkdtemp(prefix="dgstudio_embedpy_")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        with unittest.mock.patch.object(sys, "frozen", True, create=True), \
                _frozen_exe(base):
            ok, out = pip_install(["python-osc>=1.9"],
                                  target=os.path.join(base, "_deps"))
        self.assertFalse(ok)
        self.assertIn("内置 Python", out)

    def test_frozen_ensure_dependencies_recheck_uses_deps_dir(self):
        base = tempfile.mkdtemp(prefix="dgstudio_embedpy_")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        _stage_runtime(base)
        modules_root = tempfile.mkdtemp(prefix="dgstudio_deps_")
        self.addCleanup(shutil.rmtree, modules_root, ignore_errors=True)
        patcher = unittest.mock.patch("plugins.module_roots",
                                      return_value=[modules_root])
        patcher.start()
        self.addCleanup(patcher.stop)
        engine = _FakeEngine()
        manager = PluginManager(engine)
        engine.modules = manager
        module_dir = os.path.join(modules_root, "sample")
        os.makedirs(module_dir)
        with open(os.path.join(module_dir, "plugin.py"), "w",
                  encoding="utf-8") as f:
            f.write('META = {"id": "sample", "version": "1.0.0"}\n')
        with open(os.path.join(module_dir, "requirements.txt"), "w",
                  encoding="utf-8") as f:
            f.write("dgstudio-definitely-not-a-package>=1.0\n")
        manager.discover()

        def fake_pip(cmd, **_kwargs):
            target = cmd[cmd.index("--target") + 1]
            info = os.path.join(
                target, "dgstudio_definitely_not_a_package-1.0.dist-info")
            os.makedirs(info)
            with open(os.path.join(info, "METADATA"), "w",
                      encoding="utf-8") as f:
                f.write("Metadata-Version: 2.1\n"
                        "Name: dgstudio-definitely-not-a-package\n"
                        "Version: 1.0\n")
            return SimpleNamespace(returncode=0, stdout="ok", stderr="")

        with unittest.mock.patch.object(sys, "frozen", True, create=True), \
                _frozen_exe(base), \
                unittest.mock.patch.object(module_store.subprocess, "run",
                                           fake_pip):
            ok, still, _out = manager.store.ensure_dependencies("sample")
        self.assertTrue(ok)
        self.assertEqual(still, [])


class DepsAbiTests(unittest.TestCase):
    RUNNING = f"cp{sys.version_info.major}{sys.version_info.minor}"

    def test_deps_abi_detects_foreign_cp_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(deps_abi_ok(tmp))
            self.assertTrue(deps_abi_ok(os.path.join(tmp, "missing")))
            foreign = f"x.cp{99}-win_amd64.pyd"
            if foreign == f"x.{self.RUNNING}-win_amd64.pyd":
                foreign = f"x.cp312-win_amd64.pyd" if self.RUNNING != "cp312" \
                    else "x.cp311-win_amd64.pyd"
            with open(os.path.join(tmp, foreign), "w", encoding="utf-8") as f:
                f.write("")
            self.assertFalse(deps_abi_ok(tmp))

    def test_deps_abi_accepts_matching_and_untagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, f"m.{self.RUNNING}-win_amd64.pyd"), "w",
                      encoding="utf-8") as f:
                f.write("")
            with open(os.path.join(tmp, "plain.pyd"), "w",
                      encoding="utf-8") as f:
                f.write("")
            self.assertTrue(deps_abi_ok(tmp))

    def test_wheel_abi_rules(self):
        self.assertTrue(wheel_abi_ok("six-1.17.0-py2.py3-none-any.whl"))
        self.assertTrue(wheel_abi_ok(
            "opencv_python_headless-5.0.0.93-cp37-abi3-win_amd64.whl"))
        self.assertTrue(wheel_abi_ok(
            f"numpy-2.5.3-{self.RUNNING}-{self.RUNNING}-win_amd64.whl"))
        foreign = "cp312" if self.RUNNING != "cp312" else "cp311"
        self.assertFalse(wheel_abi_ok(
            f"numpy-2.5.3-{foreign}-{foreign}-win_amd64.whl"))

    def test_stale_wheels_lists_only_mismatched(self):
        tmp = tempfile.mkdtemp(prefix="dgstudio_stale_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        for name in (f"numpy-2.5.3-{self.RUNNING}-{self.RUNNING}-win_amd64.whl",
                     "six-1.17.0-py2.py3-none-any.whl",
                     "opencv_python_headless-5.0.0.93-cp37-abi3-win_amd64.whl",
                     "pyyaml-6.0.3-cp312-cp312-win_amd64.whl",
                     "notes.txt"):
            with open(os.path.join(tmp, name), "wb") as f:
                f.write(b"")
        expected = ([] if self.RUNNING == "cp312"
                    else ["pyyaml-6.0.3-cp312-cp312-win_amd64.whl"])
        self.assertEqual(stale_wheels(tmp), expected)
        self.assertEqual(stale_wheels(os.path.join(tmp, "missing")), [])

    def test_frozen_ensure_quarantines_stale_abi_deps(self):
        base = tempfile.mkdtemp(prefix="dgstudio_embedpy_")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        _stage_runtime(base)
        modules_root = tempfile.mkdtemp(prefix="dgstudio_deps_")
        self.addCleanup(shutil.rmtree, modules_root, ignore_errors=True)
        patcher = unittest.mock.patch("plugins.module_roots",
                                      return_value=[modules_root])
        patcher.start()
        self.addCleanup(patcher.stop)
        engine = _FakeEngine()
        manager = PluginManager(engine)
        engine.modules = manager
        module_dir = os.path.join(modules_root, "sample")
        os.makedirs(module_dir)
        with open(os.path.join(module_dir, "plugin.py"), "w",
                  encoding="utf-8") as f:
            f.write('META = {"id": "sample", "version": "1.0.0"}\n')
        with open(os.path.join(module_dir, "requirements.txt"), "w",
                  encoding="utf-8") as f:
            f.write("dgstudio-definitely-not-a-package>=1.0\n")
        manager.discover()
        deps = manager.store.deps_dir("sample")
        os.makedirs(deps)
        stale_name = ("x.cp312-win_amd64.pyd" if self.RUNNING != "cp312"
                      else "x.cp311-win_amd64.pyd")
        with open(os.path.join(deps, stale_name), "w", encoding="utf-8") as f:
            f.write("stale")

        def fake_pip(cmd, **_kwargs):
            target = cmd[cmd.index("--target") + 1]
            info = os.path.join(
                target, "dgstudio_definitely_not_a_package-1.0.dist-info")
            os.makedirs(info)
            with open(os.path.join(info, "METADATA"), "w",
                      encoding="utf-8") as f:
                f.write("Metadata-Version: 2.1\n"
                        "Name: dgstudio-definitely-not-a-package\n"
                        "Version: 1.0\n")
            return SimpleNamespace(returncode=0, stdout="ok", stderr="")

        logs: list[str] = []
        with unittest.mock.patch.object(sys, "frozen", True, create=True), \
                _frozen_exe(base), \
                unittest.mock.patch.object(module_store.subprocess, "run",
                                           fake_pip):
            ok, still, _out = manager.store.ensure_dependencies(
                "sample", log=logs.append)
        self.assertTrue(ok)
        self.assertEqual(still, [])
        self.assertTrue(any("ABI" in line for line in logs))
        self.assertTrue(os.path.isfile(os.path.join(deps + ".old", stale_name)))
        self.assertTrue(os.path.isfile(
            os.path.join(deps, "dgstudio_definitely_not_a_package-1.0.dist-info",
                         "METADATA")))

    def test_missing_dependencies_reports_abi_mismatch(self):
        base = tempfile.mkdtemp(prefix="dgstudio_embedpy_")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        _stage_runtime(base)
        modules_root = tempfile.mkdtemp(prefix="dgstudio_deps_")
        self.addCleanup(shutil.rmtree, modules_root, ignore_errors=True)
        patcher = unittest.mock.patch("plugins.module_roots",
                                      return_value=[modules_root])
        patcher.start()
        self.addCleanup(patcher.stop)
        engine = _FakeEngine()
        manager = PluginManager(engine)
        engine.modules = manager
        module_dir = os.path.join(modules_root, "sample")
        os.makedirs(module_dir)
        with open(os.path.join(module_dir, "plugin.py"), "w",
                  encoding="utf-8") as f:
            f.write('META = {"id": "sample", "version": "1.0.0"}\n')
        with open(os.path.join(module_dir, "requirements.txt"), "w",
                  encoding="utf-8") as f:
            f.write("dgstudio-definitely-not-a-package>=1.0\n")
        manager.discover()
        deps = manager.store.deps_dir("sample")
        os.makedirs(deps)
        stale_name = ("x.cp312-win_amd64.pyd" if self.RUNNING != "cp312"
                      else "x.cp311-win_amd64.pyd")
        with open(os.path.join(deps, stale_name), "w", encoding="utf-8") as f:
            f.write("stale")
        self.assertEqual(manager.store.missing_dependencies("sample"),
                         ["dgstudio-definitely-not-a-package>=1.0"])


class MarketParseTests(unittest.TestCase):
    def test_parse_market_payload(self):
        data = (
            "schema: 1\n"
            "modules:\n"
            "  - id: osc_bridge\n"
            "    repo: dgstudio-modules-osc_bridge\n"
            "    path: modules/osc_bridge\n"
            "    branch: main\n"
            "    author: KelierAndes\n"
            '    name: "VRChat OSC 联动"\n'
            "    version: 1.5.0\n"
            '    description: "d"\n'
            "    default_enabled: false\n"
            "    requirements: [python-osc>=1.9]\n"
            "    files: [modules/osc_bridge/plugin.py]\n"
        ).encode("utf-8")
        entries = ModuleStore._parse_market(data)
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["id"], "osc_bridge")
        self.assertEqual(entry["repo"], "dgstudio-modules-osc_bridge")
        self.assertEqual(entry["path"], "modules/osc_bridge")
        self.assertEqual(entry["requirements"], ["python-osc>=1.9"])
        self.assertEqual(entry["name"], "VRChat OSC 联动")

    def test_parse_rejects_bad_payload(self):
        with self.assertRaises(ValueError):
            ModuleStore._parse_market(b"schema: 1\n")
        with self.assertRaises(ValueError):
            ModuleStore._parse_market(b"modules: []\n")


class MarketSettingsTests(unittest.TestCase):

    def setUp(self):
        self.modules_root = tempfile.mkdtemp(prefix="dgstudio_mods_")
        self.addCleanup(shutil.rmtree, self.modules_root, ignore_errors=True)
        patcher = unittest.mock.patch(
            "plugins.module_roots", return_value=[self.modules_root])
        patcher.start()
        self.addCleanup(patcher.stop)

        self.engine = _FakeEngine()
        self.manager = PluginManager(self.engine)
        self.engine.modules = self.manager
        self.store = self.manager.store
        self.market = self.engine.config.setdefault("modules_market", {})
        self.addCleanup(self.engine.config.pop, "modules_market", None)

    def test_repo_override_formats(self):
        self.assertEqual(self.store.repo["owner"], "KelierAndes")
        self.market["repo"] = "someone/mirror@dev"
        self.assertEqual(self.store.repo,
                         {"owner": "someone", "name": "mirror",
                          "branch": "dev"})
        self.market["repo"] = "not-a-repo"
        self.assertEqual(self.store.repo["owner"], "KelierAndes")

    def test_mirror_normalization(self):
        self.assertEqual(self.store.mirror, "")
        for raw, expect in (("ghfast.top", "https://ghfast.top/"),
                            ("https://gh-proxy.com",
                             "https://gh-proxy.com/")):
            self.market["mirror"] = raw
            self.assertEqual(self.store.mirror, expect)
        self.market["mirror"] = "直连"
        self.assertEqual(self.store.mirror, "")

    def test_market_urls_put_mirrored_raw_first(self):
        self.market["mirror"] = "ghfast.top"
        urls = self.store._market_urls()
        self.assertTrue(urls[0].startswith(
            "https://ghfast.top/https://raw.githubusercontent.com/"))
        self.assertIn("/dgstudio-modules-market/main/market.yaml", urls[0])
        self.assertTrue(urls[1].startswith("https://raw.githubusercontent.com/"))
        self.assertTrue(urls[2].startswith("https://cdn.jsdelivr.net/"))
        self.assertTrue(urls[3].startswith("https://api.github.com/"))

    def test_zip_url_uses_archive_when_mirror(self):
        self.manager.discover()
        entry = {"id": "x", "repo": "dgstudio-modules-x", "branch": "main",
                 "path": "modules/x"}
        self.market["mirror"] = "ghfast.top"
        url = self.store._zip_url(entry)
        self.assertTrue(url.startswith("https://ghfast.top/https://github.com/"))
        self.assertIn("/archive/refs/heads/main.zip", url)
        self.market["mirror"] = ""
        self.assertTrue(self.store._zip_url(entry).startswith(
            "https://codeload.github.com/"))

    def test_proxy_opener_built_from_settings(self):
        self.assertIsNone(self.store._network_opener())
        self.market["proxy"] = "http://127.0.0.1:7890"
        opener = self.store._network_opener()
        from urllib.request import ProxyHandler

        self.assertTrue(any(isinstance(h, ProxyHandler)
                            for h in opener.handlers))
        self.market["proxy"] = ""
        self.market["no_proxy"] = True
        opener = self.store._network_opener()
        direct = next(h for h in opener.handlers
                      if isinstance(h, ProxyHandler))
        self.assertEqual(direct.proxies, {"http": None, "https": None})


class OnlineSectionStateTests(unittest.TestCase):

    def test_idle_before_any_fetch(self):
        store = SimpleNamespace(entries=[], fetched_at=0.0, last_error="")
        self.assertEqual(online_section_state(store), ("idle", []))

    def test_ready_after_successful_fetch(self):
        store = SimpleNamespace(
            entries=[{"id": "osc_bridge", "version": "1.5.0"}],
            fetched_at=1.0, last_error="")
        state, entries = online_section_state(store)
        self.assertEqual(state, "ready")
        self.assertEqual([e["id"] for e in entries], ["osc_bridge"])

    def test_ready_after_cache_fallback(self):
        store = SimpleNamespace(entries=[{"id": "x"}], fetched_at=0.0,
                                last_error="raw: timeout")
        self.assertEqual(online_section_state(store)[0], "ready")

    def test_empty_after_successful_but_empty_market(self):
        store = SimpleNamespace(entries=[], fetched_at=1.0, last_error="")
        self.assertEqual(online_section_state(store), ("empty", []))


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.modules_root = tempfile.mkdtemp(prefix="dgstudio_mods_")
        self.addCleanup(shutil.rmtree, self.modules_root, ignore_errors=True)
        patcher = unittest.mock.patch(
            "plugins.module_roots", return_value=[self.modules_root])
        patcher.start()
        self.addCleanup(patcher.stop)

        self.engine = _FakeEngine()
        self.manager = PluginManager(self.engine)
        self.engine.modules = self.manager
        self.store = self.manager.store

        self.tmp = tempfile.mkdtemp(prefix="dgstudio_repofake_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _fake_market(self, module_id: str, version: str,
                     *, zip_ok: bool = True, requirements: str = "",
                     extra_declared: tuple[str, ...] = ()) -> None:
        repo = f"dgstudio-modules-{module_id}"
        market_path = os.path.join(self.tmp, "market.yaml")
        with open(market_path, "wb") as f:
            f.write(_market_yaml(repo, module_id, version, extra_declared))
        if zip_ok:
            zip_path = os.path.join(self.tmp, f"{repo}.zip")
            _make_sub_repo_zip(zip_path, repo, module_id, version, requirements)
            zip_url = _file_url(zip_path)
        else:
            zip_url = _file_url(os.path.join(self.tmp, "missing.zip"))

        files_dir = os.path.join(self.tmp, "files")
        mod_files = os.path.join(files_dir, "modules", module_id)
        os.makedirs(mod_files, exist_ok=True)
        self.repo_files = files_dir
        with open(os.path.join(mod_files, "plugin.py"), "w",
                  encoding="utf-8") as f:
            f.write('META = {"id": "%s", "version": "%s"}\n'
                    % (module_id, version))
        with open(os.path.join(files_dir, "README.md"), "w",
                  encoding="utf-8") as f:
            f.write("# demo\n")
        for rel in extra_declared:
            target = os.path.join(mod_files, *rel.split("/"))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w", encoding="utf-8") as f:
                f.write("payload\n")

        unittest.mock.patch.object(type(self.store), "_market_urls",
                                   return_value=[_file_url(market_path)]
                                   ).start()
        unittest.mock.patch.object(type(self.store), "_zip_url",
                                   side_effect=lambda entry: zip_url).start()
        unittest.mock.patch.object(
            type(self.store), "_file_urls",
            side_effect=lambda entry, rel: [_file_url(
                os.path.join(files_dir, *rel.split("/")))]).start()
        self.addCleanup(unittest.mock.patch.stopall)

    def test_download_via_zip_extracts_module_dir(self):
        self._fake_market("sample", "1.0.0")
        entries = self.store.fetch_market()
        self.assertEqual([e["id"] for e in entries], ["sample"])
        dest = self.store.download("sample")
        self.assertEqual(dest, os.path.join(self.modules_root, "sample"))
        self.assertTrue(os.path.isfile(os.path.join(dest, "plugin.py")))
        self.assertTrue(os.path.isfile(os.path.join(dest, "extra", "inner.py")))
        self.assertFalse(os.path.exists(dest + ".downloading"))
        self.store.download("sample")
        self.assertTrue(os.path.isfile(os.path.join(dest, "plugin.py")))

    def test_download_falls_back_to_per_file(self):
        self._fake_market("sample", "1.0.0", zip_ok=False)
        self.store.fetch_market()
        dest = self.store.download("sample")
        self.assertTrue(os.path.isfile(os.path.join(dest, "plugin.py")))

    def test_download_refetches_files_missing_from_zip(self):
        """仓库快照少了清单声明的文件时逐个补取，bin/ 里的 dll 不能静默丢失。"""
        self._fake_market("sample", "1.0.0", extra_declared=("bin/payload.dll",))
        self.store.fetch_market()
        dest = self.store.download("sample")
        self.assertTrue(os.path.isfile(os.path.join(dest, "plugin.py")))
        self.assertTrue(os.path.isfile(os.path.join(dest, "bin", "payload.dll")))

    def test_download_keeps_going_when_a_file_is_unreachable(self):
        """仓库里也取不到的文件只报告警，不该把整个模块的安装挡掉。"""
        self._fake_market("sample", "1.0.0", extra_declared=("bin/gone.dll",))
        os.remove(os.path.join(self.repo_files, "modules", "sample", "bin",
                               "gone.dll"))
        self.store.fetch_market()
        dest = self.store.download("sample")
        self.assertTrue(os.path.isfile(os.path.join(dest, "plugin.py")))
        self.assertFalse(os.path.exists(os.path.join(dest, "bin", "gone.dll")))
        self.assertTrue(any("取不到" in msg for msg in self.engine._logs))

    def test_download_unknown_module_raises(self):
        self._fake_market("sample", "1.0.0")
        self.store.fetch_market()
        with self.assertRaises(RuntimeError):
            self.store.download("ghost")

    def test_zip_fetch_retries_before_falling_back(self):
        """整仓快照断流时先重连，几次都不行才回退逐文件。"""
        self._fake_market("sample", "1.0.0", zip_ok=False)
        self.store.fetch_market()
        attempts: list[str] = []

        def flaky(url, *, timeout, opener=None):
            attempts.append(url)
            raise OSError("IncompleteRead")

        entry = self.store.entry("sample")
        with unittest.mock.patch.object(module_store, "_open_url", flaky):
            with self.assertRaises(RuntimeError):
                self.store._fetch_zip(entry)
        self.assertEqual(len(attempts), module_store._ZIP_RETRIES)
        dest = self.store.download("sample")
        self.assertTrue(os.path.isfile(os.path.join(dest, "plugin.py")))

    def test_module_files_get_the_long_timeout(self):
        """wheel 几十 MB，逐文件下载不能用清单那档 10 秒超时。"""
        self._fake_market("sample", "1.0.0")
        self.store.fetch_market()
        seen: list[float] = []

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _n):
                return b""

        def fake_open(url, *, timeout, opener=None):
            seen.append(timeout)
            return Resp()

        entry = self.store.entry("sample")
        with unittest.mock.patch.object(module_store, "_open_url", fake_open):
            err = self.store._fetch_one(
                entry, "modules/sample/plugin.py",
                os.path.join(self.tmp, "out", "plugin.py"))
        self.assertEqual(err, "")
        self.assertEqual(seen, [module_store._FILE_TIMEOUT])
        self.assertGreater(module_store._FILE_TIMEOUT,
                           module_store._HTTP_TIMEOUT)

    def test_requirements_from_txt_beats_meta(self):
        self._fake_market("sample", "1.0.0", requirements="python-osc>=1.9\n")
        self.store.fetch_market()
        self.store.download("sample")
        self.manager.discover()
        reqs, source = self.store.requirements_of("sample")
        self.assertEqual(source, "requirements.txt")
        self.assertEqual(reqs, ["python-osc>=1.9"])
        missing = self.store.missing_dependencies("sample")
        self.assertEqual(missing, [])
        path = self.store.requirements_path("sample")
        with open(path, "w", encoding="utf-8") as f:
            f.write("dgstudio-definitely-not-a-package>=1.0\n")
        self.assertEqual(self.store.missing_dependencies("sample"),
                         ["dgstudio-definitely-not-a-package>=1.0"])

    def test_requirements_falls_back_to_meta(self):
        self._fake_market("sample", "1.0.0")
        self.store.fetch_market()
        self.store.download("sample")
        self.manager.discover()
        reqs, source = self.store.requirements_of("sample")
        self.assertEqual(source, "META")
        self.assertEqual(reqs, [])

    def test_update_with_locked_old_files_defers_cleanup(self):
        self._fake_market("sample", "1.0.0")
        self.store.fetch_market()
        self.store.download("sample")
        dest = os.path.join(self.modules_root, "sample")
        dep_dir = os.path.join(dest, "_deps")
        os.makedirs(dep_dir)
        lib = _mapped_pyd(os.path.join(dep_dir, "cv2.pyd"))
        self._fake_market("sample", "2.0.0")
        self.store.fetch_market(force=True)
        self.store.download("sample")
        self.assertTrue(os.path.isfile(os.path.join(dest, "plugin.py")))
        with open(os.path.join(dest, "plugin.py"), encoding="utf-8") as f:
            self.assertIn("2.0.0", f.read())
        _unmap(lib)
        self.manager.discover()
        self.assertEqual([n for n in os.listdir(self.modules_root)
                          if n.startswith("sample")], ["sample"])


class ModuleDeleteTests(unittest.TestCase):

    def setUp(self):
        self.modules_root = tempfile.mkdtemp(prefix="dgstudio_del_")
        self.addCleanup(shutil.rmtree, self.modules_root, ignore_errors=True)
        patcher = unittest.mock.patch(
            "plugins.module_roots", return_value=[self.modules_root])
        patcher.start()
        self.addCleanup(patcher.stop)
        self.engine = _FakeEngine()
        self.manager = PluginManager(self.engine)
        self.engine.modules = self.manager

    def _make_module(self, module_id: str) -> str:
        module_dir = os.path.join(self.modules_root, module_id)
        os.makedirs(module_dir, exist_ok=True)
        with open(os.path.join(module_dir, "plugin.py"), "w",
                  encoding="utf-8") as f:
            f.write('META = {"id": "%s", "version": "1.0.0"}\n' % module_id)
        self.manager.discover()
        return module_dir

    def test_delete_plain_removes_dir(self):
        module_dir = self._make_module("plain")
        self.manager.delete_module("plain")
        self.assertFalse(os.path.exists(module_dir))
        self.assertNotIn("plain", self.manager._paths)

    def test_delete_locked_defers_to_next_startup(self):
        module_dir = self._make_module("locked")
        dep_dir = os.path.join(module_dir, "_deps", "cv2")
        os.makedirs(dep_dir)
        ext = os.path.join(dep_dir, "cv2.pyd")
        lib = _mapped_pyd(ext)
        with self.assertRaises(OSError):
            os.remove(ext)
        self.manager.delete_module("locked")
        self.assertFalse(os.path.exists(module_dir))
        pending = module_dir + ".pending_delete"
        self.assertTrue(os.path.isfile(
            os.path.join(pending, "_deps", "cv2", "cv2.pyd")))
        self.assertNotIn("locked", self.manager._paths)
        _unmap(lib)
        self.manager.discover()
        self.assertFalse(os.path.exists(module_dir + ".pending_delete"))

    def test_delete_plain_open_handle_marks_cleanup(self):
        module_dir = self._make_module("held")
        extra = os.path.join(module_dir, "_deps", "x")
        os.makedirs(extra)
        data = os.path.join(extra, "blob.bin")
        with open(data, "wb") as f:
            f.write(b"payload")
        fh = open(data, "rb")
        try:
            with unittest.mock.patch("plugins.time.sleep"):
                self.manager.delete_module("held")
        finally:
            fh.close()
        self.assertTrue(os.path.isdir(module_dir))
        self.assertTrue(os.path.isfile(
            os.path.join(module_dir, "_deps", "x", "blob.bin")))
        self.assertTrue(os.path.isfile(
            os.path.join(module_dir, _CLEANUP_MARKER)))
        self.assertNotIn("held", self.manager._paths)
        self.manager.discover()
        self.assertFalse(os.path.exists(module_dir))

    def test_delete_transient_handle_retries_then_succeeds(self):
        module_dir = self._make_module("transient")
        extra = os.path.join(module_dir, "_deps", "x")
        os.makedirs(extra)
        data = os.path.join(extra, "blob.bin")
        with open(data, "wb") as f:
            f.write(b"payload")
        fh = open(data, "rb")

        def release(_seconds):
            fh.close()

        try:
            with unittest.mock.patch("plugins.time.sleep",
                                     side_effect=release):
                self.manager.delete_module("transient")
        finally:
            fh.close()
        self.assertFalse(os.path.exists(module_dir))
        self.assertFalse(os.path.exists(module_dir + ".pending_delete"))
        self.assertNotIn("transient", self.manager._paths)

    def test_discover_sweeps_pending_delete_leftovers(self):
        junk = os.path.join(self.modules_root, "ghost.pending_delete")
        os.makedirs(os.path.join(junk, "_deps"))
        with open(os.path.join(junk, "plugin.py"), "w", encoding="utf-8") as f:
            f.write("# leftover\n")
        self.manager.discover()
        self.assertFalse(os.path.exists(junk))


class BundledWheelsTests(unittest.TestCase):

    def setUp(self):
        self.modules_root = tempfile.mkdtemp(prefix="dgstudio_whl_")
        self.addCleanup(shutil.rmtree, self.modules_root, ignore_errors=True)
        patcher = unittest.mock.patch(
            "plugins.module_roots", return_value=[self.modules_root])
        patcher.start()
        self.addCleanup(patcher.stop)
        self.engine = _FakeEngine()
        self.manager = PluginManager(self.engine)
        self.engine.modules = self.manager
        self.module_dir = os.path.join(self.modules_root, "sample")
        os.makedirs(self.module_dir)
        with open(os.path.join(self.module_dir, "plugin.py"), "w",
                  encoding="utf-8") as f:
            f.write('META = {"id": "sample", "version": "1.0.0"}\n')
        with open(os.path.join(self.module_dir, "requirements.txt"), "w",
                  encoding="utf-8") as f:
            f.write("dgstudio-fake-dep>=1.0\n")
        self.manager.discover()

    @staticmethod
    def _make_wheel(path: str, dist_name: str, version: str,
                    *, evil: bool = False) -> None:
        pkg = dist_name.replace("-", "_")
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr(f"{pkg}/__init__.py", "__version__ = %r\n" % version)
            zf.writestr(f"{pkg}-{version}.dist-info/METADATA",
                        "Metadata-Version: 2.1\nName: %s\nVersion: %s\n"
                        % (dist_name, version))
            zf.writestr(f"{pkg}-{version}.dist-info/WHEEL",
                        "Wheel-Version: 1.0\n")
            if evil:
                zf.writestr("../evil.txt", "boom")

    def test_merge_satisfies_requirements_without_pip(self):
        base = tempfile.mkdtemp(prefix="dgstudio_whlbase_")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        _stage_runtime(base)
        wheels = os.path.join(self.module_dir, "wheels")
        os.makedirs(wheels)
        self._make_wheel(
            os.path.join(wheels, "dgstudio_fake_dep-1.0-py3-none-any.whl"),
            "dgstudio-fake-dep", "1.0")

        def _fail_run(*_args, **_kwargs):
            raise AssertionError("pip 不应被调用")

        with unittest.mock.patch.object(sys, "frozen", True, create=True), \
                _frozen_exe(base), \
                unittest.mock.patch.object(module_store.subprocess, "run",
                                           _fail_run):
            ok, still, _out = self.manager.store.ensure_dependencies("sample")
        self.assertTrue(ok)
        self.assertEqual(still, [])
        deps = os.path.join(self.module_dir, "_deps")
        self.assertTrue(os.path.isfile(
            os.path.join(deps, "dgstudio_fake_dep", "__init__.py")))
        self.assertTrue(os.path.isdir(
            os.path.join(deps, "dgstudio_fake_dep-1.0.dist-info")))

    def test_merge_is_idempotent(self):
        base = tempfile.mkdtemp(prefix="dgstudio_whlidem_")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        _stage_runtime(base)
        wheels = os.path.join(self.module_dir, "wheels")
        os.makedirs(wheels)
        self._make_wheel(
            os.path.join(wheels, "dgstudio_fake_dep-1.0-py3-none-any.whl"),
            "dgstudio-fake-dep", "1.0")
        deps = os.path.join(self.module_dir, "_deps")
        frozen = unittest.mock.patch.object(sys, "frozen", True, create=True)
        with frozen, _frozen_exe(base):
            self.manager.store.ensure_dependencies("sample")
        init_py = os.path.join(deps, "dgstudio_fake_dep", "__init__.py")
        with open(init_py, "w", encoding="utf-8") as f:
            f.write("# sentinel\n")
        with frozen, _frozen_exe(base):
            ok, _still, _out = self.manager.store.ensure_dependencies("sample")
        self.assertTrue(ok)
        with open(init_py, encoding="utf-8") as f:
            self.assertEqual(f.read(), "# sentinel\n")

    def test_merge_rejects_unsafe_wheel(self):
        base = tempfile.mkdtemp(prefix="dgstudio_whlev_")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        target = os.path.join(base, "_deps")
        os.makedirs(target)
        wheel = os.path.join(base, "evil-1.0-py3-none-any.whl")
        self._make_wheel(wheel, "evil", "1.0", evil=True)
        with self.assertRaises(RuntimeError):
            self.manager.store._merge_wheel(wheel, target)
        self.assertFalse(os.path.exists(os.path.join(base, "evil.txt")))
        self.assertEqual(os.listdir(target), [])

    def test_one_stale_wheel_does_not_void_the_rest(self):
        """自带 wheel 里有一个 ABI 不符，其余可用 wheel 仍要装进 _deps。

        内置 Python 一升级（cp312→cp314）时，整目录作废会让已经适配的 wheel
        一起不用，模块只能联网安装——网络不通就是「依赖安装失败」。
        """
        base = tempfile.mkdtemp(prefix="dgstudio_whlmix_")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        _stage_runtime(base)
        wheels = os.path.join(self.module_dir, "wheels")
        os.makedirs(wheels)
        self._make_wheel(
            os.path.join(wheels, "dgstudio_fake_dep-1.0-py3-none-any.whl"),
            "dgstudio-fake-dep", "1.0")
        self._make_wheel(
            os.path.join(wheels,
                         "dgstudio_other-1.0-cp99-cp99-win_amd64.whl"),
            "dgstudio-other", "1.0")

        def _fail_run(*_args, **_kwargs):
            raise AssertionError("ABI 相符的 wheel 已覆盖需求，pip 不应被调用")

        logs: list[str] = []
        with unittest.mock.patch.object(sys, "frozen", True, create=True), \
                _frozen_exe(base), \
                unittest.mock.patch.object(module_store.subprocess, "run",
                                           _fail_run):
            ok, still, _out = self.manager.store.ensure_dependencies(
                "sample", log=logs.append)
        self.assertTrue(ok)
        self.assertEqual(still, [])
        deps = os.path.join(self.module_dir, "_deps")
        self.assertTrue(os.path.isfile(
            os.path.join(deps, "dgstudio_fake_dep", "__init__.py")))
        self.assertFalse(os.path.isdir(os.path.join(deps, "dgstudio_other")))
        self.assertTrue(any("ABI 不符" in line and "cp99" in line
                            for line in logs))


if __name__ == "__main__":
    unittest.main()

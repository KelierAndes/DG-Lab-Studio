from __future__ import annotations

import functools
import importlib.metadata
import importlib.util
import base64
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
import urllib.request

DEFAULT_REPO = {"owner": "KelierAndes", "name": "dgstudio-modules-market",
                "branch": "main"}
_UA = "DGStudio-ModuleStore/1.0"
_MARKET_NAME = "market.yaml"
_HTTP_TIMEOUT = 10.0
_ZIP_TIMEOUT = 120.0

MIRROR_PRESETS = ("https://ghfast.top/", "https://gh-proxy.com/",
                  "https://ghproxy.net/")
PROXY_PRESETS = ("http://127.0.0.1:7890", "http://127.0.0.1:7897",
                 "http://127.0.0.1:10809")


def _http_get(url: str, *, timeout: float = _HTTP_TIMEOUT, opener=None) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": _UA})
    open_url = opener.open if opener is not None else urllib.request.urlopen
    with open_url(request, timeout=timeout) as resp:
        return resp.read()


def _host(url: str) -> str:
    return re.sub(r"^https?://", "", url).split("/", 1)[0]


def parse_requirements_text(text: str) -> list[str]:
    out: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


_REQ_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def requirement_name(req: str) -> str:
    match = _REQ_NAME.match(req.lstrip("!").strip())
    return match.group(0).lower() if match else ""


@functools.lru_cache(maxsize=1)
def _import_map() -> dict:
    try:
        return importlib.metadata.packages_distributions()
    except Exception:
        return {}


@functools.lru_cache(maxsize=256)
def _import_candidates(dist_name: str) -> tuple[str, ...]:
    key = dist_name.lower().replace("_", "-")
    out: list[str] = []
    for imp, dists in _import_map().items():
        if any(d.lower().replace("_", "-") == key for d in dists):
            out.append(imp)
    fallback = dist_name.replace("-", "_")
    if fallback not in out:
        out.append(fallback)
    return tuple(out)


def requirement_satisfied(req: str, extra_dirs: tuple[str, ...] = ()) -> bool:
    name = requirement_name(req)
    if not name:
        return True
    try:
        importlib.metadata.version(name)
        return True
    except importlib.metadata.PackageNotFoundError:
        pass
    for path in extra_dirs:
        try:
            for dist in importlib.metadata.distributions(path=[path]):
                if (dist.metadata["Name"] or "").lower().replace("_", "-") == name:
                    return True
        except Exception:
            continue
    for imp in _import_candidates(name):
        try:
            if importlib.util.find_spec(imp) is not None:
                return True
        except (ImportError, ValueError, AttributeError):
            continue
    return False


def _embedded_python_dir() -> str:
    if not getattr(sys, "frozen", False):
        return ""
    runtime = os.path.join(os.path.dirname(os.path.abspath(sys.executable)),
                           "_python")
    return runtime if os.path.isfile(os.path.join(runtime, "python.exe")) else ""


def _run_pip(cmd: list[str], env: dict) -> tuple[bool, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", env=env,
                              timeout=1800)
        return proc.returncode == 0, (proc.stdout or "") + (proc.stderr or "")
    except OSError as exc:
        return False, str(exc)
    except subprocess.TimeoutExpired as exc:
        return False, f"pip 超时: {exc}"


def pip_install(requirements: list[str], target: str | None = None,
                *, log=None) -> tuple[bool, str]:
    groups = [([r for r in requirements if not r.startswith("!")], []),
              ([r[1:].strip() for r in requirements if r.startswith("!")],
               ["--no-deps"])]
    outputs: list[str] = []
    ok_all = True
    for reqs, flags in groups:
        if not reqs:
            continue
        args = ["install", "--disable-pip-version-check", "--no-input",
                "--upgrade", *flags]
        if target:
            args += ["--target", target]
        args += reqs
        env = {key: value for key, value in os.environ.items()
               if not key.upper().startswith("PYTHON")}
        env.update(PIP_NO_INPUT="1", PIP_DISABLE_PIP_VERSION_CHECK="1")
        if getattr(sys, "frozen", False):
            runtime = _embedded_python_dir()
            if not runtime:
                ok, out = False, ("内置 Python 运行时缺失（exe 旁 _python/），"
                                  "无法安装依赖；请用 build_exe.py 重新打包")
            else:
                ok, out = _run_pip(
                    [os.path.join(runtime, "python.exe"), "-X", "utf8",
                     "-m", "pip", *args], env)
        else:
            ok, out = _run_pip([sys.executable, "-m", "pip", *args], env)
        if log is not None:
            for line in out.strip().splitlines()[-5:]:
                log(line)
        outputs.append(out)
        ok_all = ok_all and ok
    return ok_all, "\n".join(outputs)


def version_key(version: str) -> tuple:
    parts: list[int] = []
    for part in str(version or "").strip().split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


class ModuleStore:

    def __init__(self, manager):
        self.manager = manager
        self.entries: list[dict] = []
        self.fetched_at: float = 0.0
        self.last_error: str = ""


    def _market_settings(self) -> dict:
        try:
            cfg = self.manager.engine.config.get("modules_market")
        except Exception:
            cfg = None
        return cfg if isinstance(cfg, dict) else {}

    @property
    def repo(self) -> dict:
        repo = dict(DEFAULT_REPO)
        spec = str(self._market_settings().get("repo") or "").strip().strip("/")
        branch = ""
        if "@" in spec:
            spec, branch = (part.strip() for part in spec.split("@", 1))
        parts = spec.split("/")
        if len(parts) >= 2 and parts[0] and parts[1]:
            repo.update(owner=parts[0], name=parts[1],
                        branch=branch or repo["branch"])
            return repo
        try:
            legacy = self.manager.engine.config.get("modules_repo")
        except Exception:
            legacy = None
        if isinstance(legacy, dict):
            for key in repo:
                if legacy.get(key):
                    repo[key] = str(legacy[key])
        return repo

    @property
    def mirror(self) -> str:
        value = str(self._market_settings().get("mirror") or "").strip()
        if not value or value in {"直连", "无"}:
            return ""
        if not value.startswith(("http://", "https://")):
            value = "https://" + value
        return value if value.endswith("/") else value + "/"

    @property
    def proxy(self) -> str:
        return str(self._market_settings().get("proxy") or "").strip()

    @property
    def no_proxy(self) -> bool:
        return bool(self._market_settings().get("no_proxy", False))

    def _network_opener(self):
        handlers = []
        if self.proxy:
            handlers.append(urllib.request.ProxyHandler(
                {"http": self.proxy, "https": self.proxy}))
        elif self.no_proxy:
            handlers.append(urllib.request.ProxyHandler(
                {"http": None, "https": None}))
        return urllib.request.build_opener(*handlers) if handlers else None

    def _log(self, message: str) -> None:
        try:
            self.manager.engine._log(message)
        except Exception:
            pass

    @staticmethod
    def _with_mirror(mirror: str, url: str) -> str:
        if mirror and url.startswith(("https://github.com/",
                                      "https://raw.githubusercontent.com/",
                                      "https://codeload.github.com/",
                                      "https://api.github.com/")):
            return mirror + url
        return url

    def _market_urls(self) -> list[str]:
        r = self.repo
        owner, name, branch = r["owner"], r["name"], r["branch"]
        raw = (f"https://raw.githubusercontent.com/{owner}/{name}/"
               f"{branch}/{_MARKET_NAME}")
        jsd = (f"https://cdn.jsdelivr.net/gh/{owner}/{name}@{branch}/"
               f"{_MARKET_NAME}")
        api = (f"https://api.github.com/repos/{owner}/{name}/contents/"
               f"{_MARKET_NAME}?ref={branch}")
        mirror = self.mirror
        urls = [self._with_mirror(mirror, raw)] if mirror else []
        urls += [raw, jsd, api]
        return urls

    def _cache_path(self) -> str:
        return os.path.join(self.manager.config_dir, "market_cache.json")


    def fetch_market(self, *, force: bool = False) -> list[dict]:
        if self.entries and not force:
            return self.entries
        opener = self._network_opener()
        errors: list[str] = []
        for url in self._market_urls():
            try:
                data = _http_get(url, timeout=_HTTP_TIMEOUT, opener=opener)
                if _host(url).startswith("api.github."):
                    payload = json.loads(data.decode("utf-8"))
                    data = base64.b64decode(payload.get("content") or "")
                entries = self._parse_market(data)
                self.entries = entries
                self.fetched_at = time.time()
                self.last_error = ""
                self._save_cache(entries)
                self._log(f"已从 {_host(url)} 获取市场清单"
                          f"（{len(entries)} 个模块）")
                return entries
            except Exception as exc:
                errors.append(f"{_host(url)}: {exc}")
                self._log(f"市场源 {_host(url)} 不可用: {exc}")
        self.last_error = "；".join(errors[-2:]) if errors else ""
        cached = self._load_cache()
        if cached:
            self.entries = cached
            self._log("全部市场源不可用，已回退到离线缓存的清单")
            return self.entries
        raise RuntimeError("获取模块市场清单失败：" + (self.last_error or "未知错误"))

    @staticmethod
    def _parse_market(data: bytes) -> list[dict]:
        import yaml

        payload = yaml.safe_load(data.decode("utf-8"))
        modules = payload.get("modules") if isinstance(payload, dict) else None
        if not isinstance(modules, list):
            raise ValueError("市场清单格式不正确")
        entries: list[dict] = []
        for item in modules:
            if not isinstance(item, dict) or not str(item.get("id") or ""):
                continue
            entries.append({
                "id": str(item["id"]),
                "repo": str(item.get("repo") or f"dgstudio-modules-{item['id']}"),
                "path": str(item.get("path") or ""),
                "branch": str(item.get("branch") or "main"),
                "author": str(item.get("author") or ""),
                "name": str(item.get("name") or item["id"]),
                "version": str(item.get("version") or "0.0.0"),
                "description": str(item.get("description") or ""),
                "default_enabled": bool(item.get("default_enabled", False)),
                "requirements": [str(r) for r in (item.get("requirements") or [])],
                "files": [str(f) for f in (item.get("files") or [])],
            })
        if not entries:
            raise ValueError("市场清单不含任何模块")
        return entries

    def _save_cache(self, entries: list[dict]) -> None:
        try:
            os.makedirs(os.path.dirname(self._cache_path()), exist_ok=True)
            with open(self._cache_path(), "w", encoding="utf-8") as f:
                json.dump({"fetched_at": time.time(), "modules": entries},
                          f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def _load_cache(self) -> list[dict]:
        try:
            with open(self._cache_path(), "r", encoding="utf-8") as f:
                data = json.load(f)
            entries = data.get("modules")
            return entries if isinstance(entries, list) and entries else []
        except (OSError, ValueError):
            return []

    def entry(self, module_id: str) -> dict | None:
        for item in self.entries:
            if item["id"] == module_id:
                return item
        return None


    def _zip_url(self, entry: dict) -> str:
        r = self.repo
        coords = self._repo_coords(entry)
        if self.mirror:
            return self._with_mirror(self.mirror, (
                f"https://github.com/{r['owner']}/{coords['repo']}"
                f"/archive/refs/heads/{coords['branch']}.zip"))
        return (f"https://codeload.github.com/{r['owner']}/{coords['repo']}"
                f"/zip/refs/heads/{coords['branch']}")

    def _file_urls(self, entry: dict, rel_path: str) -> list[str]:
        r = self.repo
        owner, branch = r["owner"], (entry.get("branch") or "main")
        raw = (f"https://raw.githubusercontent.com/{owner}/{entry['repo']}/"
               f"{branch}/{rel_path}")
        jsd = (f"https://cdn.jsdelivr.net/gh/{owner}/{entry['repo']}"
               f"@{branch}/{rel_path}")
        mirror = self.mirror
        urls = [self._with_mirror(mirror, raw)] if mirror else []
        urls += [raw, jsd]
        return urls

    def download(self, module_id: str, entry: dict | None = None) -> str:
        entry = entry or self.entry(module_id)
        if entry is None:
            raise RuntimeError(f"在线清单中没有模块 {module_id}")
        dest = os.path.join(self.manager.base_dir, module_id)
        tmp = dest + ".downloading"
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(tmp, exist_ok=True)
        opener = self._network_opener()
        got_zip = False
        try:
            got_zip = self._extract_from_zip(entry, tmp, opener)
        except Exception:
            got_zip = False
        if not got_zip:
            self._fetch_files(entry, tmp, opener)
        if not os.path.isfile(os.path.join(tmp, "plugin.py")):
            raise RuntimeError(f"模块 {module_id} 下载不完整（缺 plugin.py）")
        old = dest + ".old"
        shutil.rmtree(old, ignore_errors=True)
        if os.path.isdir(dest):
            try:
                os.rename(dest, old)
            except OSError as exc:
                raise RuntimeError(
                    "模块文件被运行中的应用占用（扩展 .pyd 载入后保留到进程"
                    "退出），请重启应用后再更新") from exc
        os.rename(tmp, dest)
        shutil.rmtree(old, ignore_errors=True)
        if os.path.isdir(old):
            shutil.rmtree(old + ".pending_delete", ignore_errors=True)
            try:
                os.rename(old, old + ".pending_delete")
            except OSError:
                pass
        return dest

    @staticmethod
    def _repo_coords(entry: dict) -> dict:
        return {"repo": entry["repo"], "branch": entry.get("branch") or "main"}

    def _extract_from_zip(self, entry: dict, tmp: str, opener=None) -> bool:
        r = self._repo_coords(entry)
        archive = zipfile.ZipFile(io.BytesIO(_http_get(
            self._zip_url(entry), timeout=_ZIP_TIMEOUT, opener=opener)))
        with archive:
            prefix = f"{r['repo']}-{r['branch']}/" + (f"{entry['path']}/"
                                                     if entry.get("path") else "")
            names = [name for name in archive.namelist()
                     if name.startswith(prefix) and not name.endswith("/")]
            if not names:
                return False
            for name in names:
                rel = name[len(prefix):]
                target = os.path.join(tmp, *rel.split("/"))
                parent = os.path.dirname(target)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with archive.open(name) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        return True

    def _fetch_files(self, entry: dict, tmp: str, opener=None) -> None:
        path_prefix = f"{entry['path']}/" if entry.get("path") else ""
        files = [str(f) for f in (entry.get("files") or [])
                 if not str(f).startswith("_deps/")]
        for name in files:
            if path_prefix:
                if not name.startswith(path_prefix):
                    continue
                rel_in_module = name[len(path_prefix):]
            else:
                rel_in_module = name
            for url in self._file_urls(entry, name):
                try:
                    data = _http_get(url, timeout=_HTTP_TIMEOUT, opener=opener)
                except Exception:
                    continue
                target = os.path.join(tmp, *rel_in_module.split("/"))
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with open(target, "wb") as f:
                    f.write(data)
                break
            else:
                raise RuntimeError(f"文件下载失败: {name}")


    def deps_dir(self, module_id: str) -> str:
        module_dir = self.manager.module_dir(module_id)
        return os.path.join(module_dir, "_deps") if module_dir else ""

    def requirements_path(self, module_id: str) -> str:
        module_dir = self.manager.module_dir(module_id)
        path = os.path.join(module_dir, "requirements.txt") if module_dir else ""
        return path if path and os.path.isfile(path) else ""

    def requirements_of(self, module_id: str) -> tuple[list[str], str]:
        path = self.requirements_path(module_id)
        if path:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    reqs = parse_requirements_text(f.read())
            except OSError:
                reqs = []
            if reqs:
                return reqs, "requirements.txt"
        meta = self.manager.meta(module_id) or {}
        return ([str(r) for r in (meta.get("dependencies") or [])
                 if str(r).strip()], "META")

    def missing_dependencies(self, module_id: str) -> list[str]:
        requirements, _source = self.requirements_of(module_id)
        frozen = getattr(sys, "frozen", False)
        deps = self.deps_dir(module_id)
        extra = (deps,) if frozen and deps and os.path.isdir(deps) else ()
        return [req for req in requirements if not req.startswith("!")
                and not requirement_satisfied(req, extra)]

    def wheels_dir(self, module_id: str) -> str:
        module_dir = self.manager.module_dir(module_id)
        path = os.path.join(module_dir, "wheels") if module_dir else ""
        return path if path and os.path.isdir(path) else ""

    @staticmethod
    def _merge_wheel(wheel_path: str, target: str) -> bool:
        stem = os.path.basename(wheel_path)[:-4]
        dist_info = "-".join(stem.split("-")[:2]) + ".dist-info"
        if os.path.isdir(os.path.join(target, dist_info)):
            return False
        with zipfile.ZipFile(wheel_path) as zf:
            for member in zf.infolist():
                rel = member.filename
                if rel.startswith("/") or ".." in rel.split("/"):
                    raise RuntimeError(f"wheel 内含不安全路径: {rel}")
            zf.extractall(target)
        return True

    def _merge_bundled_wheels(self, module_id: str, deps: str,
                              *, log=None) -> None:
        wheels = self.wheels_dir(module_id)
        if not wheels:
            return
        merged = 0
        for name in sorted(os.listdir(wheels)):
            if not name.endswith(".whl"):
                continue
            try:
                merged += bool(self._merge_wheel(os.path.join(wheels, name),
                                                 deps))
            except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
                if log is not None:
                    log(f"[deps] wheel 合并失败 {name}: {exc}")
        if merged and log is not None:
            log(f"[deps] 已合并模块自带依赖 {merged} 个 wheel")

    def ensure_dependencies(self, module_id: str,
                            *, log=None) -> tuple[bool, list[str], str]:
        requirements, _source = self.requirements_of(module_id)
        if not requirements:
            return True, [], ""
        frozen = getattr(sys, "frozen", False)
        deps = self.deps_dir(module_id)
        extra: tuple[str, ...] = ()
        if frozen and deps:
            os.makedirs(deps, exist_ok=True)
            self._merge_bundled_wheels(module_id, deps, log=log)
            extra = (deps,)
        missing = [req for req in requirements
                   if not requirement_satisfied(req, extra)]
        if not missing:
            return True, [], ""
        if frozen:
            extra = (deps,)
        ok, out = pip_install(missing, deps if frozen else None, log=log)
        still = [req for req in missing if not req.startswith("!") and
                 not requirement_satisfied(req, extra)]
        return (ok and not still), still, out

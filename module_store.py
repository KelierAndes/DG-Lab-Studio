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
import tempfile
import time
import zipfile
import urllib.request

DEFAULT_REPO = {"owner": "KelierAndes", "name": "dgstudio-modules-market",
                "branch": "main"}
_UA = "DGStudio-ModuleStore/1.0"
_MARKET_NAME = "market.yaml"
_HTTP_TIMEOUT = 10.0
# 模块自带的 wheel 动辄几十 MB（opencv / onnxruntime），10 秒的清单级超时不够；
# 这里的 timeout 是「单次 socket 读」的上限，不是整个传输的时限。
_FILE_TIMEOUT = 120.0
_ZIP_TIMEOUT = 60.0
# 整包快照失败的常见原因是链路本身撑不住这个体积，重连第三次也一样失败，却要多
# 等一两分钟才回退逐文件（实测 vision_link 三次重试占了 5.5 分钟里的近 3 分钟）。
_ZIP_RETRIES = 2

MIRROR_PRESETS = ("https://ghfast.top/", "https://gh-proxy.com/",
                  "https://ghproxy.net/")
PROXY_PRESETS = ("http://127.0.0.1:7890", "http://127.0.0.1:7897",
                 "http://127.0.0.1:10809")


def _open_url(url: str, *, timeout: float, opener=None):
    request = urllib.request.Request(url, headers={"User-Agent": _UA})
    open_url = opener.open if opener is not None else urllib.request.urlopen
    return open_url(request, timeout=timeout)


def _stream_to(resp, dest: str) -> None:
    """流式落盘并按 Content-Length 校长度：半截 wheel 解开就是 BadZipFile。

    代理 / 加速前缀把响应提前掐断时不一定抛 IncompleteRead，实测 12.7 MB 的
    numpy wheel 会只剩 2.3 MB 而「下载成功」，装依赖时才炸。
    """
    expected = None
    try:
        header = resp.headers.get("Content-Length") if resp.headers else None
        expected = int(header) if header else None
    except (TypeError, ValueError):
        expected = None
    with open(dest, "wb") as out:
        shutil.copyfileobj(resp, out, 1 << 20)
    got = os.path.getsize(dest)
    if expected is not None and got != expected:
        raise OSError(f"下载不完整：{got}/{expected} 字节")


def _http_get(url: str, *, timeout: float = _HTTP_TIMEOUT, opener=None) -> bytes:
    with _open_url(url, timeout=timeout, opener=opener) as resp:
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


def _running_abi() -> str:
    return f"cp{sys.version_info.major}{sys.version_info.minor}"


def _ext_abi_tag(name: str) -> str:
    match = re.match(r".+\.(cp\d+)-win_amd64\.(?:pyd|lib)$", name)
    return match.group(1) if match else ""


def deps_abi_ok(deps_dir: str) -> bool:
    """_deps 里的二进制扩展必须与运行中解释器同 ABI：内置 Python 升级后必须整目录重装。"""
    want = _running_abi()
    if not want or not deps_dir or not os.path.isdir(deps_dir):
        return True
    for _root, _dirs, files in os.walk(deps_dir):
        for name in files:
            tag = _ext_abi_tag(name)
            if tag and tag != want:
                return False
    return True


def wheel_abi_ok(filename: str) -> bool:
    """随包 wheel 与当前解释器的 ABI 是否兼容（abi3 / 纯 Python 恒兼容）。"""
    parts = os.path.basename(filename)[:-4].split("-")
    if len(parts) < 3 or not filename.endswith(".whl"):
        return True
    py_tag, abi_tag = parts[-3], parts[-2]
    if abi_tag in ("none", "abi3"):
        return True
    running = _running_abi()
    return py_tag == running and abi_tag == running


def stale_wheels(wheels_dir: str) -> list[str]:
    """目录里与当前解释器 ABI 不符的 wheel 文件名（纯 Python / abi3 不计入）。"""
    if not wheels_dir or not os.path.isdir(wheels_dir):
        return []
    try:
        names = sorted(os.listdir(wheels_dir))
    except OSError:
        return []
    return [name for name in names if name.endswith(".whl")
            and not wheel_abi_ok(name)]


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
                *, log=None, optional: bool = False) -> tuple[bool, str]:
    if optional:
        groups = [(list(requirements), ["--no-deps"])]
    else:
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
                # -E -s：只认内置 Python 自己的 site-packages。开发机上用户级
                # %APPDATA%\Python\... 里的同名包会让 pip 说「already satisfied」
                # 而跳过安装，换一台干净机器就少依赖。
                ok, out = _run_pip(
                    [os.path.join(runtime, "python.exe"), "-X", "utf8",
                     "-E", "-s", "-m", "pip", *args], env)
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
        else:
            self._refetch_missing(entry, tmp, opener)
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

    def _fetch_zip(self, entry: dict, opener=None) -> str:
        """整仓快照流式落到临时文件，返回路径（调用方负责删）。

        带上 wheel 的仓库快照有 100 MB 量级：一次性 read() 成常见断流，代理提前
        掐断连接时也不一定抛错，所以落到临时文件 + 按 Content-Length 校长度 +
        按次重连（每次都是新连接，比把半截数据留在内存里更可能读完）。
        """
        url = self._zip_url(entry)
        errors: list[str] = []
        for _attempt in range(_ZIP_RETRIES):
            handle, path = tempfile.mkstemp(suffix=".zip", prefix="dgstudio_")
            os.close(handle)
            try:
                with _open_url(url, timeout=_ZIP_TIMEOUT, opener=opener) as resp:
                    _stream_to(resp, path)
                return path
            except Exception as exc:
                errors.append(f"{_host(url)} {type(exc).__name__}: {exc}")
                try:
                    os.remove(path)
                except OSError:
                    pass
        raise RuntimeError("仓库快照下载失败: " + (errors[-1] if errors else ""))

    def _extract_from_zip(self, entry: dict, tmp: str, opener=None) -> bool:
        r = self._repo_coords(entry)
        path = self._fetch_zip(entry, opener)
        try:
            self._unpack_zip(path, entry, r, tmp)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
        return True

    @staticmethod
    def _unpack_zip(path: str, entry: dict, r: dict, tmp: str) -> None:
        with zipfile.ZipFile(path) as archive:
            prefix = f"{r['repo']}-{r['branch']}/" + (f"{entry['path']}/"
                                                     if entry.get("path") else "")
            names = [name for name in archive.namelist()
                     if name.startswith(prefix) and not name.endswith("/")]
            if not names:
                raise RuntimeError("仓库快照里没有本模块目录")
            for name in names:
                rel = name[len(prefix):]
                target = os.path.join(tmp, *rel.split("/"))
                parent = os.path.dirname(target)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with archive.open(name) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)

    def _fetch_files(self, entry: dict, tmp: str, opener=None) -> None:
        failed = []
        for name, rel in self._module_files(entry):
            err = self._fetch_one(entry, name, os.path.join(
                tmp, *rel.split("/")), opener)
            if err:
                failed.append(f"{rel}（{err}）")
        if failed:
            raise RuntimeError("文件下载失败: " + "、".join(failed[:4])
                               + (f" …另有 {len(failed) - 4} 个"
                                  if len(failed) > 4 else ""))

    @staticmethod
    def _module_files(entry: dict) -> list[tuple[str, str]]:
        """市场清单里属于本模块目录的文件：(仓库内路径, 模块内相对路径)。"""
        prefix = f"{entry['path']}/" if entry.get("path") else ""
        pairs: list[tuple[str, str]] = []
        for item in entry.get("files") or []:
            name = str(item)
            if name.startswith("_deps/") or "__pycache__" in name:
                continue
            if prefix:
                if not name.startswith(prefix):
                    continue
                pairs.append((name, name[len(prefix):]))
            else:
                pairs.append((name, name))
        return pairs

    def _fetch_one(self, entry: dict, repo_path: str, target: str,
                   opener=None) -> str:
        """取一个文件：成功返回空串，失败返回最后一个错误。

        wheel 动辄几十 MB，超时按「单次 socket 读」给足，并流式写盘而不是
        整包读进内存。
        """
        last = ""
        for url in self._file_urls(entry, repo_path):
            try:
                with _open_url(url, timeout=_FILE_TIMEOUT, opener=opener) as resp:
                    parent = os.path.dirname(target)
                    if parent:
                        os.makedirs(parent, exist_ok=True)
                    _stream_to(resp, target)
                return ""
            except Exception as exc:
                last = f"{_host(url)} {type(exc).__name__}: {exc}"
        return last or "无可用下载源"

    def _refetch_missing(self, entry: dict, tmp: str, opener=None) -> None:
        """整仓 zip 里缺清单声明的文件时逐个补取。

        分支快照与清单生成之间有时间差（也挡不住个别平台不落子目录），少了
        bin/ 里的 dll 这类文件不会报错，只会在运行时降级，所以按清单核对一次。
        """
        missing = [(name, rel) for name, rel in self._module_files(entry)
                   if not os.path.isfile(os.path.join(tmp, *rel.split("/")))]
        if not missing:
            return
        self._log(f"模块 {entry['id']} 的仓库快照缺 "
                  f"{len(missing)} 个清单文件，逐个补取："
                  + "、".join(rel for _name, rel in missing))
        failed = []
        for name, rel in missing:
            if self._fetch_one(entry, name, os.path.join(tmp, *rel.split("/")),
                               opener):
                failed.append(rel)
        if failed:
            self._log(f"模块 {entry['id']} 仍有文件取不到（"
                      + "、".join(failed) + "），该模块功能可能不完整")


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
        if frozen and deps and os.path.isdir(deps) and not deps_abi_ok(deps):
            return [req for req in requirements if not req.startswith("!")]
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
        """把随包 wheel 逐个解开到 _deps：只跳过 ABI 不符的那几个。

        模块自带 wheel 是「无网络也能装依赖」的唯一保障。整目录作废会让已经
        适配的 wheel（abi3 / 纯 Python 那些）也一起不用，模块只能联网安装；
        内置 Python 一升级就把所有已装模块打成「装不上」。
        """
        wheels = self.wheels_dir(module_id)
        if not wheels:
            return
        skipped = stale_wheels(wheels)
        if skipped and log is not None:
            log(f"[deps] 自带 wheel 中有 {len(skipped)} 个与当前内置 Python"
                f"（{_running_abi()}）ABI 不符，本次跳过（涉及："
                + "、".join(skipped) + "）；模块若导入失败请更新模块版本")
        merged = 0
        for name in sorted(os.listdir(wheels)):
            if not name.endswith(".whl") or not wheel_abi_ok(name):
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
            if os.path.isdir(deps) and not deps_abi_ok(deps):
                stale = deps + ".old"
                try:
                    if os.path.isdir(stale):
                        shutil.rmtree(stale, ignore_errors=True)
                    os.rename(deps, stale)
                except OSError as exc:
                    if log is not None:
                        log(f"[deps] 旧依赖隔离失败（模块可能正在运行，"
                            f"请先停止再试）: {exc}")
                    still = [req for req in requirements
                             if not req.startswith("!")]
                    return False, still, f"deps ABI mismatch: {exc}"
                if log is not None:
                    log(f"[deps] 依赖二进制与当前内置 Python（{_running_abi()}）"
                        "ABI 不符，已隔离旧依赖并重新安装")
            os.makedirs(deps, exist_ok=True)
            self._merge_bundled_wheels(module_id, deps, log=log)
            extra = (deps,)
        missing = [req for req in requirements
                   if not requirement_satisfied(req, extra)]
        if not missing:
            return True, [], ""
        target = deps if frozen else None
        required_missing = [req for req in missing if not req.startswith("!")]
        optional_missing = [req[1:].strip() for req in missing
                            if req.startswith("!")]
        ok, out = pip_install(required_missing, target, log=log)
        if optional_missing:
            ok_opt, out_opt = pip_install(optional_missing, target, log=log,
                                          optional=True)
            out = "\n".join(part for part in (out, out_opt) if part)
            if not ok_opt and log is not None:
                log("[deps] 可选依赖安装失败（不影响模块运行，仅对应增强功能不可用）")
        if frozen:
            extra = (deps,)
        still = [req for req in required_missing
                 if not requirement_satisfied(req, extra)]
        return (ok and not still), still, out

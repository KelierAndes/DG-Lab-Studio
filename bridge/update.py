"""软件自更新：查 GitHub 最新 release、下载安装包、交给外部脚本替换并重启。

界面只负责点按钮和显示进度；版本比较、下载地址与替换脚本都在这里，
免得前端各自猜「哪个 zip 才是 Windows 安装包」。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

from dglab.version import APP_VERSION
from module_store import version_key

REPO = "KelierAndes/DG-Lab-Studio"
_API = f"https://api.github.com/repos/{REPO}/releases/latest"
_ASSET_SUFFIX = "_win64.zip"
_HTTP_TIMEOUT = 20.0
_DOWNLOAD_TIMEOUT = 120.0
_USER_AGENT = f"DGStudio/{APP_VERSION}"

#: 最近一次检查的结果。下载与安装只认它（安装包地址不从界面回传，免得被拼任意路径）。
_LAST: dict[str, Any] = {}


def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _update_dir() -> Path:
    path = Path(tempfile.gettempdir()) / "dgstudio_update"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _opener(engine: Any):
    store = engine.modules.store
    opener = store._network_opener()
    return opener or urllib.request.build_opener()


def _mirror(engine: Any, url: str) -> str:
    try:
        mirror = engine.modules.store.mirror
    except Exception:
        mirror = ""
    return engine.modules.store._with_mirror(mirror, url)


def _fetch_json(url: str, engine: Any) -> dict:
    request = urllib.request.Request(_mirror(engine, url), headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": _USER_AGENT,
    })
    with _opener(engine).open(request, timeout=_HTTP_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def check(engine: Any, params: dict) -> dict:
    """查最新 release；任何失败都收成 error 文案，界面不用分辨异常类型。"""
    try:
        release = _fetch_json(_API, engine)
    except Exception as exc:
        return {"ok": False, "current": APP_VERSION,
                "error": f"检查更新失败：{exc}"}
    tag = str(release.get("tag_name") or "").lstrip("vV")
    assets = [a for a in (release.get("assets") or []) if isinstance(a, dict)]
    asset = next((a for a in assets
                  if str(a.get("name") or "").endswith(_ASSET_SUFFIX)), None)
    info = {
        "ok": True,
        "current": APP_VERSION,
        "latest": tag,
        # 只认更新：装的比线上新（开发版）不算「有更新」。
        "has_update": bool(tag) and version_key(tag) > version_key(APP_VERSION),
        "name": str(release.get("name") or tag),
        "notes": str(release.get("body") or "")[:20000],
        "page": str(release.get("html_url") or ""),
        "checked_at": time.time(),
        "asset": None if asset is None else {
            "name": str(asset.get("name") or ""),
            "size": int(asset.get("size") or 0),
            "url": str(asset.get("browser_download_url") or ""),
        },
    }
    _LAST.clear()
    _LAST.update(info)
    return info


def download(engine: Any, params: dict) -> dict:
    """把检查结果里的安装包下到临时目录；进度靠界面那颗「下载中」转圈表示。"""
    info = _LAST
    asset = info.get("asset") or {}
    url = str(asset.get("url") or "")
    name = str(asset.get("name") or "")
    if not info.get("has_update"):
        return {"ok": False, "error": "当前已是最新版本"}
    if not url or not name:
        return {"ok": False, "error": "最新版本没有 Windows 安装包"}
    target = _update_dir() / Path(name).name
    partial = target.with_suffix(target.suffix + ".part")
    try:
        request = urllib.request.Request(_mirror(engine, url), headers={
            "User-Agent": _USER_AGENT,
        })
        with _opener(engine).open(request,
                                  timeout=_DOWNLOAD_TIMEOUT) as resp, \
                open(partial, "wb") as out:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
        size = partial.stat().st_size
        expected = int(asset.get("size") or 0)
        if expected and size != expected:
            partial.unlink(missing_ok=True)
            return {"ok": False,
                    "error": f"安装包不完整（{size}/{expected} 字节），请重试"}
        partial.replace(target)
    except Exception as exc:
        partial.unlink(missing_ok=True)
        return {"ok": False, "error": f"下载失败：{exc}"}
    return {"ok": True, "path": str(target), "size": size}


def _script(app_dir: Path, zip_path: Path, pid: int) -> str:
    def q(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    app = q(str(app_dir))
    zip_ = q(str(zip_path))
    tmp = q(str(app_dir / ".update_tmp"))
    exe = q(str(app_dir / "DGStudio.exe"))
    return (
        "$ErrorActionPreference = 'Stop';"
        f"$app = {app}; $zip = {zip_}; $tmp = {tmp};"
        f"while (Get-Process -Id {pid} -ErrorAction SilentlyContinue) "
        "{ Start-Sleep -Milliseconds 400 };"
        "if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force };"
        "try {"
        "  Expand-Archive -LiteralPath $zip -DestinationPath $tmp -Force;"
        "  Copy-Item -Path (Join-Path $tmp '*') -Destination $app "
        "    -Recurse -Force;"
        "} catch {"
        f"  Start-Process {exe} -ErrorAction SilentlyContinue;"
        "  exit 1"
        "}"
        "Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue;"
        f"Start-Process {exe}"
    )


def apply(engine: Any, params: dict) -> dict:
    """起一个和本进程无关的 PowerShell：等外壳退出 → 解压覆盖 → 重新启动。

    只替换文件、不删目录：config.json / config/ / modules/ 是用户数据，
    解压包里本来也不含它们。
    """
    if not getattr(sys, "frozen", False):
        return {"ok": False, "error": "源码运行不支持自动更新，请 git pull 后重启"}
    app_dir = _app_dir()
    exe = app_dir / "DGStudio.exe"
    if not exe.is_file():
        return {"ok": False, "error": f"找不到 {exe}"}
    raw = str(params.get("path") or "")
    zip_path = Path(raw).resolve() if raw else None
    try:
        inside = zip_path is not None and \
            zip_path.parent.resolve() == _update_dir().resolve()
    except OSError:
        inside = False
    if not inside or not zip_path.is_file() or zip_path.suffix != ".zip":
        return {"ok": False, "error": "安装包路径不对，请重新下载"}
    pid = os.getpid()
    try:
        parent = os.getppid()
    except Exception:
        parent = 0
    if parent and parent != pid:
        pid = parent
    script = _script(app_dir, zip_path, pid)
    flags = 0
    if sys.platform == "win32":
        flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
                 | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-Command", script],
            close_fds=True, creationflags=flags,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:
        return {"ok": False, "error": f"启动更新脚本失败：{exc}"}
    return {"ok": True, "restart": True, "path": str(zip_path)}


UPDATE_METHODS = {
    "update.check": check,
    "update.download": download,
    "update.apply": apply,
}

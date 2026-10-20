"""软件自更新：查 GitHub 最新 release、校验并下载安装包、交给外部脚本替换并重启。

几条硬约束：

* 安装包必须同时通过 **哈希**（release 里的 `.sha256`）与 **签名**
  （`.sha256` 的 RSA 签名，公钥内置在 `dglab/update_key.py`）才会被安装；
  签名文件缺失或不匹配一律拒绝，而不是「跳过校验继续装」。
* 取包顺序照用户要求：**直连 → 常用镜像 → 本地端口代理**，每一跳都换了
  代理/地址重新试，任何一跳成功就停。
* 下载与校验过程通过 `set_publisher` 推给界面，界面据此显示大小与进度。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable

from dglab import update_key
from dglab.version import APP_VERSION
from module_store import MIRROR_PRESETS, PROXY_PRESETS, version_key

REPO = "KelierAndes/DG-Lab-Studio"
_API = f"https://api.github.com/repos/{REPO}/releases/latest"
_ASSET_SUFFIX = "_win64.zip"
_CHECK_TIMEOUT = 12.0
_FILE_TIMEOUT = 30.0
_DOWNLOAD_TIMEOUT = 60.0
_USER_AGENT = f"DGStudio/{APP_VERSION}"
_PROGRESS_STEP = 1 << 20
_PROGRESS_GAP = 0.15

#: 最近一次检查的结果。下载与安装只认它（安装包地址不从界面回传，免得被拼任意路径）。
_LAST: dict[str, Any] = {}
_publisher: Callable[[str, Any], None] | None = None


def set_publisher(publish: Callable[[str, Any], None] | None) -> None:
    global _publisher
    _publisher = publish


def _emit(payload: dict) -> None:
    if _publisher is None:
        return
    try:
        _publisher("update", payload)
    except Exception:
        pass


def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _update_dir() -> Path:
    path = Path(tempfile.gettempdir()) / "dgstudio_update"
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------- 取包线路


def _direct():
    # 直连就是不走系统代理：模块市场里配的代理不一定适合 GitHub release。
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": None, "https": None}))


def _proxied(url: str):
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": url, "https": url}))


def _proxy_urls(engine: Any) -> list[str]:
    out: list[str] = []
    try:
        configured = str(engine.config.get("modules_market", {}).get("proxy") or "")
    except Exception:
        configured = ""
    if configured.strip():
        out.append(configured.strip())
    for preset in PROXY_PRESETS:
        if preset not in out:
            out.append(preset)
    return out


def _sources(url: str, engine: Any):
    """直连 → 常用镜像 → 本地端口代理，按这个顺序给候选。"""
    yield "直连", _direct(), url
    for mirror in MIRROR_PRESETS:
        yield mirror.rstrip("/"), _direct(), mirror + url
    for proxy in _proxy_urls(engine):
        yield f"代理 {proxy}", _proxied(proxy), url


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})


def _read(url: str, engine: Any, *,
          timeout: float = _FILE_TIMEOUT) -> tuple[bytes, str]:
    """小额文本（release JSON / .sha256 / .sig）：逐条线路试，返回内容与线路。"""
    errors: list[str] = []
    for label, opener, target in _sources(url, engine):
        try:
            with opener.open(_request(target), timeout=timeout) as resp:
                return resp.read(), label
        except Exception as exc:
            errors.append(f"{label}: {exc}")
    raise RuntimeError("；".join(errors) or "没有可用的下载线路")


# ---------------------------------------------------------------- 校验


def verify_sha256(path: Path, manifest: bytes) -> str:
    """manifest 第一列是十六进制 sha256；对不上就抛错，返回实际摘要。"""
    expected = manifest.decode("utf-8", "replace").split(None, 1)
    want = (expected[0] if expected else "").strip().lower()
    if len(want) != 64:
        raise ValueError("哈希文件格式不对")
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    got = digest.hexdigest()
    if not hmac.compare_digest(got, want):
        raise ValueError(f"哈希不匹配（期望 {want[:16]}…，实际 {got[:16]}…）")
    return got


#: PKCS#1 v1.5 + SHA-256 的 DigestInfo 前缀。
_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")


def verify_signature(manifest: bytes, signature: bytes) -> None:
    """用内置公钥验 `.sha256` 文件的签名；任何不合规都抛 ValueError。"""
    n = update_key.MODULUS
    e = update_key.EXPONENT
    size = (n.bit_length() + 7) // 8
    if len(signature) != size:
        raise ValueError("签名长度不对")
    block = pow(int.from_bytes(signature, "big"), e, n).to_bytes(size, "big")
    digest = hashlib.sha256(manifest).digest()
    padding = size - 3 - len(_DIGEST_INFO) - len(digest)
    if padding < 8:
        raise ValueError("公钥太短")
    expected = (b"\x00\x01" + b"\xff" * padding + b"\x00"
                + _DIGEST_INFO + digest)
    if not hmac.compare_digest(block, expected):
        raise ValueError("签名与哈希文件不匹配")


def _verify_download(zip_path: Path, engine: Any) -> tuple[str, str]:
    """下载后立刻验哈希 + 签名；返回 (sha256, 哈希文件线路)。"""
    _emit({"stage": "verify", "received": 1, "total": 1})
    base = str((_LAST.get("asset") or {}).get("url") or "")
    if not base:
        raise ValueError("没有下载地址")
    try:
        manifest, source = _read(base + ".sha256", engine)
    except Exception as exc:
        raise ValueError(f"发布缺少哈希文件：{exc}") from exc
    digest = verify_sha256(zip_path, manifest)
    try:
        signature, _ = _read(base + ".sig", engine)
    except Exception as exc:
        raise ValueError(f"发布缺少签名文件：{exc}") from exc
    verify_signature(manifest, signature)
    return digest, source


# ---------------------------------------------------------------- 检查


def _fetch_json(url: str, engine: Any) -> dict:
    data, _source = _read(url, engine, timeout=_CHECK_TIMEOUT)
    return json.loads(data.decode("utf-8"))


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
            # 新版本 release API 会给 sha256，留着做第二道对照。
            "digest": str(asset.get("digest") or ""),
        },
    }
    _LAST.clear()
    _LAST.update(info)
    return info


# ---------------------------------------------------------------- 下载


def _stream(url: str, engine: Any, target: Path, total: int) -> str:
    errors: list[str] = []
    for label, opener, source_url in _sources(url, engine):
        received = 0
        last_emit, last_size = 0.0, 0
        try:
            with opener.open(_request(source_url),
                             timeout=_DOWNLOAD_TIMEOUT) as resp, \
                    open(target, "wb") as out:
                _emit({"stage": "download", "received": 0, "total": total,
                       "source": label})
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    out.write(chunk)
                    received += len(chunk)
                    now = time.monotonic()
                    if (received - last_size >= _PROGRESS_STEP
                            and now - last_emit >= _PROGRESS_GAP):
                        last_emit, last_size = now, received
                        _emit({"stage": "download", "received": received,
                               "total": total, "source": label})
            return label
        except Exception as exc:
            errors.append(f"{label}: {exc}")
            target.unlink(missing_ok=True)
    raise RuntimeError("；".join(errors) or "没有可用的下载线路")


def download(engine: Any, params: dict) -> dict:
    """下载并校验安装包；进度走 `update` 事件，界面照 received/total 画。"""
    asset = (_LAST.get("asset") or {})
    url = str(asset.get("url") or "")
    name = str(asset.get("name") or "")
    total = int(asset.get("size") or 0)
    if not _LAST.get("has_update"):
        return {"ok": False, "error": "当前已是最新版本"}
    if not url or not name:
        return {"ok": False, "error": "最新版本没有 Windows 安装包"}
    target = _update_dir() / Path(name).name
    partial = target.with_suffix(target.suffix + ".part")
    partial.unlink(missing_ok=True)
    try:
        source = _stream(url, engine, partial, total)
    except Exception as exc:
        _emit({"stage": "error", "error": str(exc)})
        return {"ok": False, "error": f"下载失败：{exc}"}
    size = partial.stat().st_size
    if total and size != total:
        partial.unlink(missing_ok=True)
        _emit({"stage": "error", "error": "安装包不完整"})
        return {"ok": False,
                "error": f"安装包不完整（{size}/{total} 字节），请重试"}
    try:
        digest, sha_source = _verify_download(partial, engine)
    except Exception as exc:
        partial.unlink(missing_ok=True)
        _emit({"stage": "error", "error": str(exc)})
        return {"ok": False, "error": str(exc)}
    partial.replace(target)
    _emit({"stage": "done", "received": size, "total": size,
           "source": source, "sha256": digest})
    return {"ok": True, "path": str(target), "size": size,
            "sha256": digest, "source": source, "verified": True,
            "manifest_source": sha_source}


# ---------------------------------------------------------------- 安装


def _script(app_dir: Path, zip_path: Path, pid: int, log_path: Path) -> str:
    def q(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    app = q(str(app_dir))
    zip_ = q(str(zip_path))
    tmp = q(str(app_dir / ".update_tmp"))
    exe = q(str(app_dir / "DGStudio.exe"))
    log = q(str(log_path))
    return (
        "$ErrorActionPreference = 'Stop';"
        f"$app = {app}; $zip = {zip_}; $tmp = {tmp}; $log = {log};"
        "function Note($text) { Add-Content -LiteralPath $log -Value $text };"
        'Note ("start " + (Get-Date -Format o));'
        f"while (Get-Process -Id {pid} -ErrorAction SilentlyContinue) "
        "{ Start-Sleep -Milliseconds 400 };"
        # 侧车是外壳的子进程，外壳退出后它还要收尾；两个都退干净再动文件。
        "while (Get-Process -Name 'DGStudio-bridge' -ErrorAction SilentlyContinue) "
        "{ Start-Sleep -Milliseconds 300 };"
        "Start-Sleep -Milliseconds 400;"
        "try {"
        "  if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force };"
        "  Expand-Archive -LiteralPath $zip -DestinationPath $tmp -Force;"
        # 发布 zip 的根目录是 DGStudio/，直接拷 $tmp 会套出一层 DGStudio\DGStudio。
        "  $src = Join-Path $tmp 'DGStudio';"
        "  if (-not (Test-Path $src)) { $src = $tmp };"
        "  Copy-Item -Path (Join-Path $src '*') -Destination $app "
        "    -Recurse -Force;"
        "  Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue;"
        '  Note "copied";'
        "} catch {"
        '  Note ("failed: " + $_.Exception.Message);'
        f"  Start-Process {exe} -ErrorAction SilentlyContinue;"
        "  exit 1"
        "}"
        f"Start-Process {exe};"
        'Note "restarted";'
    )


def _write_script(text: str, path: Path) -> None:
    # PowerShell 5.1 按当前 ANSI 读 .ps1；带 BOM 的 UTF-8 才能保住中文路径。
    path.write_text("\ufeff" + text, encoding="utf-8")


def apply(engine: Any, params: dict) -> dict:
    """起一个和本进程无关的 PowerShell：等外壳退出 → 解压覆盖 → 重新启动。

    只替换文件、不删目录：config.json / config/ / modules/ 是用户数据，
    解压包里本来也不含它们。动手前再验一次哈希与签名，防临时目录被掉包。
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
    try:
        digest, _ = _verify_download(zip_path, engine)
    except Exception as exc:
        return {"ok": False, "error": f"安装前校验失败：{exc}"}
    pid = os.getpid()
    try:
        parent = os.getppid()
    except Exception:
        parent = 0
    if parent and parent != pid:
        pid = parent
    work = _update_dir()
    log_path = work / "update.log"
    log_path.unlink(missing_ok=True)
    script_path = work / "update.ps1"
    try:
        _write_script(_script(app_dir, zip_path, pid, log_path), script_path)
    except Exception as exc:
        return {"ok": False, "error": f"写入更新脚本失败：{exc}"}
    flags = 0
    if sys.platform == "win32":
        # 只隐藏窗口。DETACHED_PROCESS 会让 powershell.exe 起不来（实测：进程
        # 建出来立刻退，脚本一行都不执行），CREATE_NO_WINDOW 才能正常跑。
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-WindowStyle", "Hidden", "-File", str(script_path)],
            close_fds=True, creationflags=flags,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:
        return {"ok": False, "error": f"启动更新脚本失败：{exc}"}
    return {"ok": True, "restart": True, "path": str(zip_path),
            "sha256": digest}


UPDATE_METHODS = {
    "update.check": check,
    "update.download": download,
    "update.apply": apply,
}

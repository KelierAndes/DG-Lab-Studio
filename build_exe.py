from __future__ import annotations

import shutil
import subprocess
import sys
import sysconfig
import urllib.request
import zipfile
from pathlib import Path

from module_store import stale_wheels

from dglab.version import APP_VERSION

ROOT = Path(__file__).resolve().parent
APP_DIR = ROOT / "dist" / "DGStudio"
RUNTIME_FILES = ("config.json", "dgstudio.log")
RUNTIME_DIRS = ("config",)

RELEASE_KEEP_TOP = {"DGStudio.exe", "_internal", "_python", "modules"}
RELEASE_KEEP_MODULES = {"__init__.py", "config_init"}


def main() -> int:
    keep_log = "--drop-log" not in sys.argv
    version = APP_VERSION
    if "--version" in sys.argv:
        version = sys.argv[sys.argv.index("--version") + 1]
    if not check_bundled_wheels(ROOT / "modules",
                                allow_stale="--allow-stale-wheels" in sys.argv):
        return 2
    backup_dir = ROOT / "_research" / "_runtime_backup"
    backup_dir.mkdir(parents=True, exist_ok=True)

    saved: dict[str, Path] = {}
    for name in RUNTIME_FILES:
        if name == "dgstudio.log" and not keep_log:
            continue
        src = APP_DIR / name
        if src.is_file():
            dst = backup_dir / name
            shutil.copyfile(src, dst)
            saved[name] = dst
            print(f"backed up {name}")
    for name in RUNTIME_DIRS:
        src = APP_DIR / name
        if src.is_dir():
            dst = backup_dir / name
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
            saved[name] = dst
            print(f"backed up {name}/")
    stash_modules(APP_DIR / "modules", backup_dir / "modules_backup")

    try:
        cmd = [sys.executable, "-m", "PyInstaller", "DGStudio.spec",
               "--noconfirm"]
        code = subprocess.call(cmd, cwd=ROOT)
        if code != 0:
            print(f"build failed (exit {code})")
            return code

        copy_modules(ROOT / "modules", APP_DIR / "modules")
        merge_downloaded(APP_DIR / "modules", ROOT / "modules",
                         backup_dir / "modules_backup")
        ensure_python_payload(APP_DIR)
        make_release_zip(APP_DIR, version)
    finally:
        # 无论构建在哪一步失败，都要把运行时文件放回去——否则一次
        # 「备份完成、还原未执行」的失败构建会把 dist 的用户数据清空
        for name, dst in saved.items():
            if dst.is_dir():
                shutil.copytree(dst, APP_DIR / name, dirs_exist_ok=True)
            else:
                shutil.copyfile(dst, APP_DIR / name)
            print(f"restored {name}")
        try:
            # PyInstaller 可能在删 dist 的中途就失败（比如 exe 还被占用），
            # 这时 modules/ 已经被清空：下载来的模块与 _deps 也要放回去。
            merge_downloaded(APP_DIR / "modules", ROOT / "modules",
                             backup_dir / "modules_backup")
        except Exception as exc:
            print(f"restore modules failed: {exc!r}")
    if not saved:
        print("no runtime files existed to restore")
    print(f"build ok: {APP_DIR / 'DGStudio.exe'}")
    return 0


def stash_modules(live: Path, backup: Path) -> None:
    """PyInstaller 整目录重建 dist/：先把已安装模块（含 _deps）备份出去。

    只带仓库 modules/ 的那几份会被重新拷回，用户在模块页下载安装的模块
    与它们装在 _deps 里的依赖原本会随 dist 一起被清掉。
    """
    if not live.is_dir():
        return
    if backup.exists():
        shutil.rmtree(backup)
    shutil.copytree(live, backup, ignore=shutil.ignore_patterns("__pycache__"))
    print(f"backed up modules/ -> {backup.name}")


def merge_downloaded(live: Path, bundled_dir: Path, backup: Path) -> None:
    """把备份里的下载模块放回 dist：bundled 的只补 _deps，代码用本次构建的新版。"""
    if not backup.is_dir():
        return
    live.mkdir(parents=True, exist_ok=True)
    bundled = {p.name for p in bundled_dir.iterdir() if p.is_dir()} \
        if bundled_dir.is_dir() else set()
    restored = 0
    for entry in sorted(backup.iterdir()):
        if not entry.is_dir():
            continue
        target = live / entry.name
        if entry.name not in bundled:
            shutil.copytree(entry, target, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__"))
            restored += 1
            continue
        deps = entry / "_deps"
        if deps.is_dir() and not (target / "_deps").exists():
            shutil.copytree(deps, target / "_deps")
            restored += 1
    if restored:
        print(f"restored {restored} downloaded module(s) into modules/")


def copy_modules(src: Path, dst: Path) -> None:
    if not src.is_dir():
        print(f"modules/ missing, skip copy: {src}")
        return
    dst.mkdir(parents=True, exist_ok=True)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo",
                                    "_deps", "*.downloading", "*.old")
    for entry in sorted(src.iterdir()):
        if entry.name == "__pycache__":
            continue
        target = dst / entry.name
        if entry.is_dir():
            if not (entry / "plugin.py").is_file():
                # 残缺的模块目录（比如只剩 flow/ 的手工副本）会顶掉 dist 里
                # 完整的已下载模块：缺 plugin.py 的不随包，留给出处重建
                print(f"warning: skip incomplete module dir (no plugin.py): "
                      f"{entry}")
                continue
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(entry, target, ignore=ignore)
        else:
            shutil.copy2(entry, target)
    print(f"merged modules/ -> {dst} (existing downloaded modules kept)")


def make_release_zip(app_dir: Path, version: str) -> Path:
    zip_path = app_dir.parent / f"DGStudio_v{version}_win64.zip"
    zip_path.unlink(missing_ok=True)
    count = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED,
                         compresslevel=9) as zf:
        for path in sorted(app_dir.rglob("*")):
            if path.is_dir() or "__pycache__" in path.parts:
                continue
            parts = path.relative_to(app_dir.parent).parts
            if parts[1] not in RELEASE_KEEP_TOP:
                continue
            if (parts[1] == "modules"
                    and (len(parts) < 3
                         or parts[2] not in RELEASE_KEEP_MODULES)):
                continue
            zf.write(path, path.relative_to(app_dir.parent))
            count += 1
    print(f"release zip ({count} files): {zip_path}")
    return zip_path


def ensure_python_payload(app_dir: Path) -> None:
    major, minor, micro = sys.version_info[:3]
    ver = f"{major}.{minor}.{micro}"
    cache = ROOT / "build" / "_python_payload"
    cache.mkdir(parents=True, exist_ok=True)
    zip_name = f"python-{ver}-embed-amd64.zip"
    zip_path = cache / zip_name
    if not zip_path.is_file():
        url = f"https://www.python.org/ftp/python/{ver}/{zip_name}"
        print(f"downloading python embeddable: {url}")
        with urllib.request.urlopen(url, timeout=120) as resp, \
                open(zip_path, "wb") as f:
            shutil.copyfileobj(resp, f)
    stage = cache / zip_name[:-4]
    if not stage.is_dir():
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(stage)
    payload = app_dir / "_python"
    if payload.exists():
        shutil.rmtree(payload)
    shutil.copytree(stage, payload)
    pip_src = Path(sysconfig.get_paths()["purelib"]) / "pip"
    shutil.copytree(pip_src, payload / "pip",
                    ignore=shutil.ignore_patterns("__pycache__"))
    pth = payload / f"python{major}{minor}._pth"
    pth.write_text(f"python{major}{minor}.zip\n.\npip\n\nimport site\n",
                   encoding="utf-8")
    internal = app_dir / "_internal"
    if internal.is_dir():
        shutil.copy2(payload / "python3.dll", internal / "python3.dll")
    print(f"python payload (embeddable {zip_name[:-4]} + pip) -> {payload}")


def _payload_version() -> str:
    return ".".join(str(part) for part in sys.version_info[:3])


def check_bundled_wheels(modules_dir: Path, *, allow_stale: bool) -> bool:
    """随包 wheel 必须适配内置 Python：wheels/ 里 ABI 不符就别发布。

    wheels/ 会被宿主解进模块的 _deps 当离线依赖，一旦与内置 Python 不符就只能
    联网 pip，网络侧出任何问题（镜像不通、证书过期、离线机器）都是「依赖安装
    失败」；ocr_wheels/ 只喂内置解释器，不适配不过是识别回退模板法，提醒即可。
    """
    running = f"cp{sys.version_info.major}{sys.version_info.minor}"
    fatal: list[str] = []
    for sub in ("wheels", "ocr_wheels"):
        for folder in sorted(p for p in modules_dir.glob(f"*/{sub}")
                             if p.is_dir()):
            stale = stale_wheels(str(folder))
            if not stale:
                continue
            label = f"{folder.parent.name}/{sub}"
            line = (f"{label} 有 {len(stale)} 个 wheel 与内置 Python "
                    f"{_payload_version()}（{running}）ABI 不符："
                    + "、".join(stale))
            if sub == "wheels":
                fatal.append(line)
            else:
                print(f"warning: {line}")
    if not fatal:
        return True
    for line in fatal:
        print(f"error: {line}")
    if allow_stale:
        print("已指定 --allow-stale-wheels：继续构建（打包版装这些模块要联网）")
        return True
    print("请把这些 wheel 换成内置 Python 对应的版本，例如：\n"
          f"  pip download --only-binary=:all: --python-version "
          f"{sys.version_info.major}{sys.version_info.minor} --abi {running} "
          "--platform win_amd64 -d <模块>/wheels <包名>\n"
          "或临时用 --allow-stale-wheels 跳过本检查")
    return False


if __name__ == "__main__":
    raise SystemExit(main())

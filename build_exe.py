from __future__ import annotations

import shutil
import subprocess
import sys
import sysconfig
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP_DIR = ROOT / "dist" / "DGStudio"
RUNTIME_FILES = ("config.json", "dgstudio.log")
RUNTIME_DIRS = ("config",)

APP_VERSION = "0.2.0"

RELEASE_KEEP_TOP = {"DGStudio.exe", "_internal", "_python", "modules"}
RELEASE_KEEP_MODULES = {"__init__.py", "config_init"}


def main() -> int:
    keep_log = "--drop-log" not in sys.argv
    version = APP_VERSION
    if "--version" in sys.argv:
        version = sys.argv[sys.argv.index("--version") + 1]
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

    cmd = [sys.executable, "-m", "PyInstaller", "DGStudio.spec", "--noconfirm"]
    code = subprocess.call(cmd, cwd=ROOT)
    if code != 0:
        print(f"build failed (exit {code})")
        return code

    copy_modules(ROOT / "modules", APP_DIR / "modules")
    ensure_python_payload(APP_DIR)
    make_release_zip(APP_DIR, version)

    for name, dst in saved.items():
        if dst.is_dir():
            shutil.copytree(dst, APP_DIR / name, dirs_exist_ok=True)
        else:
            shutil.copyfile(dst, APP_DIR / name)
        print(f"restored {name}")
    if not saved:
        print("no runtime files existed to restore")
    print(f"build ok: {APP_DIR / 'DGStudio.exe'}")
    return 0


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


if __name__ == "__main__":
    raise SystemExit(main())

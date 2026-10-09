# PyInstaller spec for DGStudio (DG-Lab × VRChat OSC console).
# Build:  .venv/Scripts/python -m PyInstaller DGStudio.spec --noconfirm
#
# Notes:
# - win32more is pure Python and resolves runtime classes dynamically via
#   importlib (GetRuntimeClassName -> module), so every Microsoft.* / UI
#   namespace that can materialize at runtime must be bundled.
# - win32more/dll/x64/Microsoft.WindowsAppRuntime.Bootstrap.dll and
#   win32more/winui3/app.xaml must ship inside the bundle at their original
#   package-relative paths.
# - 联动模块已外置（dgstudio-modules-market 仓库按需下载）：模块依赖（python-osc、
#   cv2、rapidocr/onnxruntime 等）不再随包内置，由模块 requirements.txt
#   声明、安装时经随包内置的真实 Python（exe 旁 _python/，build_exe.py 释放）
#   子进程 pip 补装到 modules/<id>/_deps/。不在冻结进程内调 pip：PyInstaller
#   的导入系统 hook 会让 pip 内置 distlib 崩溃（pip#12841，装任何 wheel 都
#   命中），子进程方式整类规避。
# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_submodules, collect_data_files, collect_dynamic_libs

import os
import sys

# 模块依赖（_deps）在运行期动态导入，所需 stdlib 无法静态枚举：收全标准库
# （跳过 tkinter 系 GUI 桩），否则 osc_bridge 的 python-osc 载入即报缺
# socketserver 这类主程序未引用的标准库模块
_STDLIB_SKIP = {"tkinter", "turtle", "turtledemo", "idlelib", "antigravity",
                "this", "lib2to3"}

hiddenimports = (
    collect_submodules("win32more.Microsoft")
    + collect_submodules("win32more.Microsoft.Graphics.Canvas")
    + collect_submodules("win32more.Microsoft.Graphics.DirectX")
    + collect_submodules("win32more.Microsoft.Graphics.Imaging")
    + collect_submodules("win32more.Microsoft.Web.WebView2")
    + collect_submodules("win32more.Windows.Foundation")
    + collect_submodules("win32more.Windows.Graphics")
    + collect_submodules("win32more.Windows.UI")
    + collect_submodules("winrt")
    + collect_submodules("bleak")
    + collect_submodules("websockets")
    + collect_submodules("qrcode")
    # 模块市场清单 market.yaml 的解析（模块页在线列表）
    + collect_submodules("yaml")
    # 模块运行期（exe 旁 modules/ 的 plugin.py）导入的引擎子模块不在主程序
    # 静态导入图里，需显式收集（dglab.mapping / dglab.params 等）；
    # PIL.ImageGrab 供核心截图框选（ui.region_pick）与模块共用。
    + collect_submodules("dglab")
    + ["PIL.ImageGrab"]
    + list(name for name in sys.stdlib_module_names
           if name not in _STDLIB_SKIP)
)

datas = (collect_data_files("win32more")
         + [
    (os.path.join(SPECPATH, "xaml", name), "xaml")
    for name in sorted(os.listdir(os.path.join(SPECPATH, "xaml")))
    if name.endswith(".xaml")
])

binaries = collect_dynamic_libs("win32more")

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # excludes：modules 外置；numpy/setuptools 系核心运行不需要（setuptools 的
    # distutils compat 会条件式拖入 numpy；模块依赖自带的 numpy 随 _deps 安装）
    excludes=["modules", "numpy", "setuptools"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DGStudio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="DGStudio",
)

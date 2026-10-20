# PyInstaller spec for the DGStudio engine sidecar (界面桥 + 引擎，无界面)。
# Build:  python -m PyInstaller DGStudio-bridge.spec --noconfirm
#
# 外壳是 Flutter 的 DGStudio.exe，引擎打成同目录的 DGStudio-bridge.exe：
# 它按 sys.executable 所在目录找 config.json / modules/ / _python/，
# 所以两者必须摆在一起（build_exe.py 负责合并 dist）。
#
# - 不收任何界面框架：外壳是 Flutter，桥里没有可导入的 UI 代码。
# - 模块依赖（_deps）在运行期动态导入，所需 stdlib 无法静态枚举：收全标准库
#   （跳过 tkinter 系 GUI 桩），否则 osc_bridge 的 python-osc 载入即报缺
#   socketserver 这类主程序未引用的标准库模块。
# - console=False：侧车不该弹一个黑框；握手因此走 --handshake-file 文件通道。
# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_submodules

import sys

_STDLIB_SKIP = {"tkinter", "turtle", "turtledemo", "idlelib", "antigravity",
                "this", "lib2to3"}

hiddenimports = (
    collect_submodules("dglab")
    + collect_submodules("bridge")
    + collect_submodules("bleak")
    + collect_submodules("websockets")
    + collect_submodules("qrcode")
    # 模块市场清单 market.yaml 的解析（模块页在线列表）
    + collect_submodules("yaml")
    # 顶层的引擎模块：运行期由 exe 旁 modules/ 的 plugin.py 反向导入
    + ["app", "plugins", "module_store"]
    + list(name for name in sys.stdlib_module_names
           if name not in _STDLIB_SKIP)
)

a = Analysis(
    ["bridge_app.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # excludes：modules 外置；核心运行不需要的重依赖整类排除
    # （numpy/setuptools 系核心不需要，模块自带的 numpy 随 _deps 安装）。
    # PIL 不能排：画面识别模块在进程内 import PIL.ImageGrab，识别参数的「截区域」也用它。
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
    name="DGStudio-bridge",
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

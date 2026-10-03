"""冻结态依赖安装探针：验证打包版经随包内置 Python 子进程装依赖的全链路。

背景：打包版在冻结进程内直接调 pip 会撞 PyInstaller 导入系统 hook 的
distlib 已知问题（pip#12841），依赖安装改为子进程执行真实 Python
（module_store.pip_install 冻结分支）。本探针以最小冻结包复现同一条链路。

构建（仓库根）：
    .venv/Scripts/python -m PyInstaller _tools/frozen_pip_probe.py \
        --onefile --console --noconfirm \
        --distpath build/_probe/dist --workpath build/_probe/work \
        --specpath build/_probe
    .venv/Scripts/python -c "from pathlib import Path; \
        from build_exe import ensure_python_payload; \
        ensure_python_payload(Path('build/_probe/dist'))"

运行（退出码 0 = 安装并导入成功）：
    build/_probe/dist/frozen_pip_probe.exe <wheel 文件> <目标目录>

仅本地验证工具，不随应用分发。
"""
from __future__ import annotations

import os
import sys

if not getattr(sys, "frozen", False):
    sys.path.insert(0, os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))

from module_store import _embedded_python_dir, pip_install


def main() -> int:
    wheel, target = sys.argv[1], sys.argv[2]
    print("frozen:", bool(getattr(sys, "frozen", False)))
    print("embedded python:", _embedded_python_dir() or "(缺失)")
    ok, out = pip_install([wheel], target=target)
    print("pip_install ok:", ok)
    print("\n".join(out.strip().splitlines()[-6:]))
    if not ok:
        return 1
    sys.path.insert(0, target)
    import six

    print("import ok: six", six.__version__)
    return 0


if __name__ == "__main__":
    sys.exit(main())

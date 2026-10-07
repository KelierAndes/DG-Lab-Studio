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

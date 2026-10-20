"""冻结侧车的入口：等价于 `python -m bridge.server`。

打包版把引擎做成外壳旁边的 `DGStudio-bridge.exe`；开发期直接跑
`python -m bridge.server`，两条路共用 `bridge.server.main()` 一套参数。
"""

from bridge.server import main

if __name__ == "__main__":
    raise SystemExit(main())

"""模块详情页的两条附加行：第三方依赖与游戏模组。

依赖是否齐、ABI 对不对都由 `module_store` 判；模组目录与安装归 `plugins`。
这里只把这些判据序列化给界面，并把「装依赖 / 扫游戏目录 / 装模组」三件慢活
挪到线程里跑——pip 与目录扫描都是分钟级，不能卡住桥的事件循环。
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from module_store import deps_abi_ok, requirement_satisfied


def _deps_payload(engine: Any, module_id: str) -> dict:
    # 依赖清单走 store 那一条判据：`requirements.txt` 优先、META["dependencies"] 兜底。
    # 以前只看 META，而模块实际都是把包写在 requirements.txt 里，于是详情页那条
    # 「额外 pip 包」永远是空的——装依赖明明在跑，界面上却什么都看不见。
    requirements, source = engine.modules.store.requirements_of(module_id)
    deps = engine.modules.store.deps_dir(module_id) if requirements else ""
    extra = (deps,) if deps and os.path.isdir(deps) else ()
    rows = []
    for req in requirements:
        optional = req.startswith("!")
        name = req[1:] if optional else req
        rows.append({"req": name, "optional": optional,
                     "ok": bool(requirement_satisfied(name, extra))})
    abi_ok = deps_abi_ok(deps) if deps else True
    missing = [r["req"] for r in rows
               if not r["optional"] and not r["ok"]]
    if deps and not abi_ok:
        missing = [r["req"] for r in rows if not r["optional"]]
    return {"dir": deps.replace("\\", "/") if deps else "",
            "abi_ok": abi_ok, "rows": rows, "missing": missing,
            "source": source if rows else ""}


def _mods_payload(engine: Any, module_id: str) -> dict:
    mods_cfg = dict((engine.modules.meta(module_id) or {}).get("mods") or {})
    mods_dir = engine.modules.module_mods_dir(module_id) or ""
    if not mods_dir:
        return {"declared": False}
    cfg = engine.modules.settings_for(module_id)
    return {
        "declared": True,
        "dir": mods_dir.replace("\\", "/"),
        "marker": str(mods_cfg.get("marker") or ""),
        "dest": str(mods_cfg.get("dest") or ""),
        "root": str(cfg.get("mods_root") or ""),
    }


def state(engine: Any, params: dict) -> dict:
    module_id = str(params.get("module_id") or "")
    return {"module_id": module_id,
            "deps": _deps_payload(engine, module_id),
            "mods": _mods_payload(engine, module_id)}


async def deps_install(engine: Any, params: dict) -> dict:
    module_id = str(params.get("module_id") or "")

    def log(line: str) -> None:
        engine._log(f"[pip] {line}")

    try:
        _ok, still, _detail = await asyncio.to_thread(
            engine.modules.store.ensure_dependencies, module_id, log=log)
    except Exception as exc:
        engine._log(f"模块 {module_id} 依赖安装失败：{exc}")
        return state(engine, {"module_id": module_id}) | {"error": f"{exc}"}
    if still:
        engine._log(f"模块 {module_id} 依赖仍缺：{'、'.join(still)}")
    else:
        engine._log(f"模块 {module_id} 依赖就绪")
    return state(engine, {"module_id": module_id}) | {"error": ""}


async def mods_scan(engine: Any, params: dict) -> dict:
    module_id = str(params.get("module_id") or "")
    marker = str(((engine.modules.meta(module_id) or {}).get("mods") or {})
                 .get("marker") or "")
    if not marker:
        return state(engine, {"module_id": module_id}) | {"found": [], "error": "模块未声明目录标记"}

    def worker() -> list[str]:
        # 扫盘是磁盘顺序遍历，放到线程里，界面靠 busy 提示等着。
        return list(engine.modules.scan_game_roots(marker))

    found = await asyncio.to_thread(worker)
    return state(engine, {"module_id": module_id}) | {"found": found, "error": ""}


async def mods_install(engine: Any, params: dict) -> dict:
    module_id = str(params.get("module_id") or "")
    root = str(params.get("root") or "").strip()
    if not root:
        return state(engine, {"module_id": module_id}) | {"error": "先填游戏根目录或点「自动扫描」"}

    def worker() -> int:
        return engine.modules.install_game_mod(module_id, root)

    try:
        count = await asyncio.to_thread(worker)
    except Exception as exc:
        engine._log(f"游戏模组安装失败：{exc}")
        return state(engine, {"module_id": module_id}) | {"error": f"{exc}"}
    cfg = engine.modules.settings_for(module_id)
    cfg["mods_root"] = root
    cfg.save()
    dest = str(((engine.modules.meta(module_id) or {}).get("mods") or {}).get("dest") or "")
    engine._log(f"游戏模组已安装（{count} 个文件）→ {root}\\{dest}")
    return state(engine, {"module_id": module_id}) | {"count": count, "error": ""}


#: README 只认这几个名字，而且必须就在模块目录里。
_README_NAMES = ("README.md", "README.rst", "README.txt", "readme.md")
#: 预览上限：模块文档一般几 KB，会把桥撑爆的从来不是正常 README。
_README_LIMIT = 200_000


def readme(engine: Any, params: dict) -> dict:
    """读模块自带的 README 给详情页预览。

    `module_id` 来自界面，所以拼完路径还要确认 realpath 仍落在模块目录内，别顺着
    `..` 去读盘上任意文件。模块没随包带文档就回 `missing`，由界面说明清楚。
    """
    module_id = str(params.get("module_id") or "")
    out: dict[str, Any] = {"module_id": module_id, "path": "", "text": "",
                           "size": 0, "missing": True, "truncated": False}
    root = engine.modules.module_dir(module_id)
    if not root:
        return out
    base = os.path.normcase(os.path.realpath(root)) + os.sep
    for name in _README_NAMES:
        target = os.path.join(root, name)
        real = os.path.normcase(os.path.realpath(target))
        if not real.startswith(base) or not os.path.isfile(target):
            continue
        try:
            with open(target, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read(_README_LIMIT + 1)
        except OSError:
            continue
        out.update({"path": f"{module_id}/{name}",
                    "text": text[:_README_LIMIT],
                    "size": len(text),
                    "missing": False,
                    "truncated": len(text) > _README_LIMIT})
        return out
    return out


MODULE_DEP_METHODS: dict[str, Any] = {
    "modules.deps": state,
    "modules.deps_install": deps_install,
    "modules.mods_scan": mods_scan,
    "modules.mods_install": mods_install,
    "modules.readme": readme,
}

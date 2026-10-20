"""事件流的界面接口：卡片目录、画布读写与配置切换。

画布本身仍归引擎所有（`engine.flow.runtime`），这里只做序列化与变更转发；
布局、连线规则、求值语义全部沿用引擎那套，界面不复制一份判断。
"""

from __future__ import annotations

from typing import Any

from dglab import event_flow as ef

PAGES = ef.PAGES


def _def_brief(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": item["key"],
        "title": item["title"],
        # 整理布局按语义 op 分列（事件 / 源 / 运算 / 分支 / 汇），界面要拿得到。
        "op": str(item.get("op") or ""),
        "en": item.get("en", ""),
        "cat": item.get("cat", ""),
        "tip": item.get("tip", ""),
        "page": item.get("page", "both"),
        "palette": bool(item.get("palette", True)),
        "exec_in": bool(item.get("exec_in")),
        "entry": bool(item.get("entry")),
        "sink": bool(item.get("sink")),
        "inputs": [
            {"name": p["name"], "type": p["type"], "default": p.get("default")}
            for p in (item.get("inputs") or [])
        ],
        "outputs": [
            {"name": p["name"], "type": p["type"], "default": p.get("default")}
            for p in (item.get("outputs") or [])
        ],
        "fields": [
            {
                "id": f["id"],
                "label": f["label"],
                "type": f["type"],
                "default": f.get("default"),
                "choices": list(f.get("choices") or []),
                "labels": list(f.get("labels") or []),
                "min": f.get("min"),
                "max": f.get("max"),
                "pool": f.get("pool", ""),
            }
            for f in (item.get("fields") or [])
        ],
    }


def _graph_payload(runtime: Any, page: str) -> dict[str, Any]:
    graph = runtime.graph(page if page in PAGES else ef.PAGE_INPUT)
    return {
        "page": page,
        # live 是这一拍算出来的值，只给界面看不进存档，所以在这里补，
        # 不动 FlowNode.to_dict（那是写盘格式）。文本也统一由引擎格式化。
        "nodes": [dict(n.to_dict(), live=[ef.value_to_text(v) for v in n.live]) for n in graph.nodes],
        "wires": [w.to_dict() for w in graph.wires],
    }


def _state(engine: Any, params: dict) -> dict[str, Any]:
    host = engine.flow
    runtime = host.runtime
    catalog = host.catalog
    page = _page(params)
    return {
        "profiles": {"active": runtime.active, "order": list(runtime.profiles)},
        "page": page,
        "graph": _graph_payload(runtime, page),
        "catalog": [_def_brief(d) for d in catalog.defs.values()],
        "types": {"labels": ef.TYPE_LABELS, "colors": ef.TYPE_COLORS},
        "vars": _var_lists(runtime, page),
        "errors": dict(runtime.errors),
        # idle：执行流走不到、界面要灰显的那几张。
        "idle": list(runtime.idle),
        # 目录换了一版没有：界面靠它决定要不要重取整份状态（见 flow.live）。
        "catalog_rev": catalog.revision,
        "missing_modules": host.missing_modules(),
    }


def _page(params: dict) -> str:
    page = str(params.get("page") or ef.PAGE_INPUT)
    return page if page in PAGES else ef.PAGE_INPUT


def _node(runtime: Any, page: str, node_id: str):
    return runtime.graph(page).find(str(node_id or ""))


def _result(engine: Any, page: str, error: str = "") -> dict[str, Any]:
    runtime = engine.flow.runtime
    return {
        "error": error,
        "graph": _graph_payload(runtime, page),
        "errors": dict(runtime.errors),
        "idle": list(runtime.idle),
    }


# ---------------------------------------------------------------- 变更


def node_add(engine: Any, params: dict) -> dict:
    page = _page(params)
    graph = engine.flow.runtime.graph(page)
    key = str(params.get("def") or "")
    if not engine.flow.catalog.known(key):
        # 变量表里刚登记的变量，读数 / 写入卡要下一拍才进目录；这里补一次
        # 刷新，免得用户点一下只看到「未知卡片类型」。
        engine.flow.refresh()
    if not engine.flow.catalog.known(key):
        return _result(engine, page, f"未知卡片类型：{key}")
    node = graph.add_node(engine.flow.catalog, key,
                          float(params.get("x") or 0.0), float(params.get("y") or 0.0),
                          dict(params.get("params") or {}))
    return _result(engine, page) | {"id": node.id}


def node_move(engine: Any, params: dict) -> dict:
    page = _page(params)
    node = _node(engine.flow.runtime, page, params.get("id"))
    if node is None:
        return _result(engine, page, "卡片已不存在")
    node.x = float(params.get("x") or node.x)
    node.y = float(params.get("y") or node.y)
    return _result(engine, page)


def node_set(engine: Any, params: dict) -> dict:
    page = _page(params)
    node = _node(engine.flow.runtime, page, params.get("id"))
    if node is None:
        return _result(engine, page, "卡片已不存在")
    if isinstance(params.get("params"), dict):
        node.params.update({str(k): v for k, v in params["params"].items()})
    if isinstance(params.get("alias"), str):
        node.alias = params["alias"].strip()
    if isinstance(params.get("overrides"), dict):
        for index, value in params["overrides"].items():
            try:
                node.overrides[int(index)] = value
            except (TypeError, ValueError):
                continue
    return _result(engine, page)


def node_remove(engine: Any, params: dict) -> dict:
    page = _page(params)
    graph = engine.flow.runtime.graph(page)
    node = _node(engine.flow.runtime, page, params.get("id"))
    if node is not None:
        graph.remove_node(node)
    return _result(engine, page)


def wire_add(engine: Any, params: dict) -> dict:
    page = _page(params)
    runtime = engine.flow.runtime
    graph = runtime.graph(page)
    src = _node(runtime, page, (params.get("src") or [None])[0])
    dst = _node(runtime, page, (params.get("dst") or [None])[0])
    if src is None or dst is None:
        return _result(engine, page, "连线两端必须有卡片")
    wire, why = graph.connect(engine.flow.catalog, src, int(params["src"][1]),
                              dst, int(params["dst"][1]))
    return _result(engine, page, "" if wire else why)


def wire_remove(engine: Any, params: dict) -> dict:
    page = _page(params)
    graph = engine.flow.runtime.graph(page)
    graph.remove_wire(str(params.get("id") or ""))
    return _result(engine, page)


def graph_clear(engine: Any, params: dict) -> dict:
    page = _page(params)
    engine.flow.runtime.graph(page).clear()
    return _result(engine, page)


def graph_paste(engine: Any, params: dict) -> dict:
    """把界面剪贴板里的一批卡片照抄进当前页（Ctrl+V / Ctrl+X 之后再来贴）。

    副本的 id、参数默认值与连线合法性都由引擎定：只有目录知道每张卡该有什么
    参数，而剪贴板那份快照可能来自另一张画布、甚至已经被删掉了。
    """
    page = _page(params)
    runtime = engine.flow.runtime
    graph = runtime.graph(page)
    catalog = engine.flow.catalog
    items = params.get("nodes") or []
    if not items:
        return _result(engine, page, "剪贴板是空的")
    dx = float(params.get("dx") or 34.0)
    dy = float(params.get("dy") or 34.0)
    made: list[Any] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        key = str(raw.get("def") or "")
        if not catalog.known(key):
            continue
        node = graph.add_node(catalog, key,
                              float(raw.get("x") or 0.0) + dx,
                              float(raw.get("y") or 0.0) + dy)
        node.params.update(dict(raw.get("params") or {}))
        node.alias = str(raw.get("alias") or "")
        for index, value in (raw.get("overrides") or {}).items():
            try:
                node.overrides[int(index)] = value
            except (TypeError, ValueError):
                continue
        made.append(node)
    if not made:
        return _result(engine, page, "剪贴板里的卡片在当前目录里已经没有了")
    for row in (params.get("wires") or []):
        if not isinstance(row, (list, tuple)) or len(row) != 4:
            continue
        try:
            src = made[int(row[0])]
            dst = made[int(row[2])]
        except (ValueError, IndexError):
            continue
        graph.connect(catalog, src, int(row[1]), dst, int(row[3]))
    return _result(engine, page)


# ---------------------------------------------------------------- 配置与变量


def profile_switch(engine: Any, params: dict) -> dict:
    name = str(params.get("name") or "")
    if not engine.flow.switch_profile(name):
        if name not in engine.flow.runtime.profiles:
            return {"error": f"没有配置「{name}」"}
    return _state(engine, params)


def profile_new(engine: Any, params: dict) -> dict:
    name = engine.flow.runtime.new_profile(str(params.get("name") or ""))
    engine.flow.runtime.save()
    return _state(engine, {"page": _page(params)}) | {"created": name}


def profile_rename(engine: Any, params: dict) -> dict:
    why = engine.flow.runtime.rename_profile(str(params.get("old") or ""), str(params.get("new") or ""))
    if not why:
        engine.flow.runtime.save()
    return _state(engine, {"page": _page(params)}) | {"error": why}


def profile_remove(engine: Any, params: dict) -> dict:
    runtime = engine.flow.runtime
    name = str(params.get("name") or "")
    if (name in runtime.profiles and len(runtime.profiles) > 1
            and runtime.active == name):
        # 界面上「删除」就挂在当前这份配置上：删的是正在用的这份就自己切走。
        # 叫用户先去别的配置再回来点删除，看着就像这颗按钮没反应。
        engine.flow.switch_profile(next(k for k in runtime.profiles if k != name))
    why = runtime.remove_profile(name)
    return _state(engine, {"page": _page(params)}) | {"error": why}


def save(engine: Any, params: dict) -> dict:
    engine.flow.runtime.save()
    return {"saved": True}


def var_add(engine: Any, params: dict) -> dict:
    runtime = engine.flow.runtime
    name = str(params.get("name") or "").strip()
    if not name:
        return {"error": "变量名不能为空"}
    why = runtime.add_user_var(name, str(params.get("note") or ""),
                               str(params.get("dir") or "inout"))
    if why:
        return {"error": why}
    return _var_lists(runtime, _page(params)) | {"error": ""}


def var_remove(engine: Any, params: dict) -> dict:
    runtime = engine.flow.runtime
    runtime.remove_user_var(str(params.get("name") or ""))
    runtime.save()
    return _var_lists(runtime, _page(params))


def _var_row(item: dict[str, Any], page: str) -> dict[str, Any]:
    """给每行变量带上「点一下就建卡」的 def key，卡片归属仍由引擎判断。"""
    return dict(item, card=ef.var_card_key(
        str(item.get("name") or ""), str(item.get("dir") or "in"),
        page, str(item.get("mid") or "")))


def _var_lists(runtime: Any, page: str = "") -> dict[str, Any]:
    declared, renamable = runtime.var_table()
    page = page if page in PAGES else ef.PAGE_INPUT
    return {"declared": [_var_row(r, page) for r in declared],
            "renamable": [_var_row(r, page) for r in renamable]}


def var_rename(engine: Any, params: dict) -> dict:
    runtime = engine.flow.runtime
    page = _page(params)
    name = str(params.get("name") or "")
    rows = _var_lists(runtime, page)
    row = next((r for r in (rows["declared"] + rows["renamable"])
                if r.get("name") == name), None)
    if row is None:
        return rows | {"error": "找不到这个变量"}
    # 模块登记的可改名行要走模块自己的改名，rename_var_row 内部按 mid 分流。
    why = runtime.rename_var_row(row, str(params.get("new") or ""))
    return _var_lists(runtime, page) | {"error": why}


def var_dir(engine: Any, params: dict) -> dict:
    runtime = engine.flow.runtime
    why = runtime.set_var_dir(str(params.get("name") or ""), str(params.get("dir") or "inout"))
    return _var_lists(runtime, _page(params)) | {"error": why}


def vars_only(engine: Any, params: dict) -> dict:
    """只取变量表：面板按秒刷新实时值，不该把整张图和目录再拉一遍。"""
    return _var_lists(engine.flow.runtime, _page(params))


def live_values(engine: Any, params: dict) -> dict:
    """这一拍每张卡片的实时值（文本，格式化仍由引擎负责）。

    红字与灰显跟着一起给：接线一变，引擎就不再把那张卡算成失效，可界面只按
    flow.state 那份快照画注释，注会一直停在卡上，看着像判断根本没改过来。
    """
    runtime = engine.flow.runtime
    graph = runtime.graph(_page(params))
    return {
        "values": {n.id: [ef.value_to_text(v) for v in n.live] for n in graph.nodes},
        "errors": dict(runtime.errors),
        "idle": list(runtime.idle),
        # 卡片目录的版本：设备连上 / 模块启停会让目录多出或少掉一批卡片定义，
        # 而实时值这一轮只有值、红字和灰显。不把版本一起给的话，画布上的模块
        # 读数卡会一直停在「只有 key 的空壳」——变量表里变量已经出现了也没用。
        "catalog_rev": engine.flow.catalog.revision,
    }


def data_flow(engine: Any, params: dict) -> dict:
    """概览页的「数据流探测」：只要模块 ↔ 核心之间那几条已经连好的线路。

    线路由引擎判（它知道每张卡真正写的是哪个参数、中间过了哪些转换卡），
    界面只负责把 `郊狼通道 A 强度[CORE] ← OSC 强度[OSC]` 排出来。
    """
    return {"lines": engine.flow.runtime.data_flow()}


def nodes_move(engine: Any, params: dict) -> dict:
    """整理布局一次落一批坐标：逐条 node_move 会让界面看到半排好的中间态。

    这里当场落盘，不等界面的延时保存：模块装载那条路会重新读一遍配置文件，
    晚一步整理好的坐标就被旧的那份盖回去——用户看到的就是「整理了，一刷新又乱了」。
    """
    graph = engine.flow.runtime.graph(_page(params))
    moves = params.get("moves") or {}
    if isinstance(moves, list):
        moves = {str(m.get("id")): m.get("pos") for m in moves if isinstance(m, dict)}
    for node in graph.nodes:
        pos = moves.get(str(node.id))
        if not isinstance(pos, (list, tuple)) or len(pos) != 2:
            continue
        node.x = float(pos[0])
        node.y = float(pos[1])
    engine.flow.runtime.save()
    return _state(engine, params)


FLOW_METHODS: dict[str, Any] = {
    "flow.state": _state,
    "flow.node_add": node_add,
    "flow.node_move": node_move,
    "flow.nodes_move": nodes_move,
    "flow.node_set": node_set,
    "flow.node_remove": node_remove,
    "flow.wire_add": wire_add,
    "flow.wire_remove": wire_remove,
    "flow.clear": graph_clear,
    "flow.paste": graph_paste,
    "flow.profile_switch": profile_switch,
    "flow.profile_new": profile_new,
    "flow.profile_rename": profile_rename,
    "flow.profile_remove": profile_remove,
    "flow.save": save,
    "flow.var_add": var_add,
    "flow.var_remove": var_remove,
    "flow.var_rename": var_rename,
    "flow.var_dir": var_dir,
    "flow.vars": vars_only,
    "flow.live": live_values,
    "flow.dataflow": data_flow,
}

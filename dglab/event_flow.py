from __future__ import annotations

import json
import math
import os
import re
import time
from typing import Any, Callable

from dglab import expr
from dglab.parsing import parse_name
from dglab.params import (core_alias_values, core_inputs, input_limit_signal,
                          input_spec, output_specs)

__all__ = [
    "EXEC", "FLOAT", "INT", "BOOL", "STR", "ANY",
    "TYPE_LABELS", "TYPE_COLORS",
    "Catalog", "FlowGraph", "FlowNode", "FlowWire", "FlowRuntime",
    "load_graphs", "save_graphs", "graph_path", "migrate_legacy",
    "vars_path", "load_vars", "save_vars", "valid_var_name",
    "DEFAULT_PROFILE", "clone_graphs", "load_profiles", "save_profiles",
    "profile_modules",
]

EXEC = "exec"
FLOAT = "float"
INT = "int"
BOOL = "bool"
STR = "str"
ANY = "any"

TYPE_LABELS = {EXEC: "执行", FLOAT: "数值", INT: "整数", BOOL: "布尔",
               STR: "文本", ANY: "通用"}

TYPE_COLORS = {
    EXEC: "#F1F2F4",
    FLOAT: "#7FD08C",
    INT: "#E0CC73",
    BOOL: "#E07A7A",
    STR: "#CC73B0",
    ANY: "#B0B4BA",
}

CATEGORY_COLORS = {
    "核心写入": "#2563EB",
    "核心读出": "#0D9488",
    "模块事件": "#D97706",
    "模块参数": "#B45309",
    "流程控制": "#DC2626",
    "数学": "#7C3AED",
    "范围": "#8B5CF6",
    "逻辑": "#E11D48",
    "常数": "#059669",
    "变量": "#0EA5A0",
    "表达式": "#4F46E5",
    "实用": "#64748B",
    "失效": "#71717A",
}

_NUMERIC = {FLOAT, INT}

TICK_INTERVAL = 0.05
MIN_PERIOD_MS = 50.0
PAGE_INPUT = "input"
PAGE_OUTPUT = "output"
PAGES = (PAGE_INPUT, PAGE_OUTPUT)
ID_PREFIX = {PAGE_INPUT: "i", PAGE_OUTPUT: "o"}
DEFAULT_PROFILE = "默认"
_MOD_KINDS = ("read", "write")


def compatible(src: str, dst: str) -> bool:
    if src == dst or src == ANY or dst == ANY:
        return True
    return src in _NUMERIC and dst in _NUMERIC


def coerce(value: Any, dst: str) -> Any:
    if value is None or dst == ANY or dst in _NUMERIC:
        return value
    if dst == BOOL:
        return as_float(value) > 0.5
    if dst == STR:
        return value_to_text(value)
    return value


def as_float(value: Any) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0


def value_to_text(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "真" if value else "假"
    if isinstance(value, float):
        if abs(value - round(value)) < 1e-6 and abs(value) < 1e12:
            return str(int(round(value)))
        return f"{value:.3f}".rstrip("0").rstrip(".")
    return str(value)


def pin(name: str, ptype: str = FLOAT, default: Any = None) -> dict[str, Any]:
    return {"name": name, "type": ptype, "default": default}


_SPEC_TYPES = {"int": INT, "integer": INT, "float": FLOAT, "number": FLOAT,
               "bool": BOOL, "boolean": BOOL, "string": STR, "str": STR}


def spec_type(raw: Any) -> str:
    return _SPEC_TYPES.get(str(raw or "").strip().lower(), FLOAT)


def field(fid: str, label: str, ftype: str = "float", *, default: Any = 0.0,
          choices: tuple[str, ...] = (), labels: tuple[str, ...] = (),
          minimum: float | None = None, maximum: float | None = None,
          pool: str = "") -> dict[str, Any]:
    return {"id": fid, "label": label, "type": ftype, "default": default,
            "choices": list(choices), "labels": list(labels),
            "min": minimum, "max": maximum, "pool": pool}


def defn(key: str, title: str, en: str, cat: str, op: str, *, keys: str = "",
         tip: str = "", inputs: tuple = (), outputs: tuple = (),
         fields: tuple = (), page: str = "both", exec_in: bool = False,
         entry: bool = False, sink: bool = False,
         palette: bool = True) -> dict[str, Any]:
    """palette=False：卡片存在（画布上能用、能由变量表拖入）但不进卡片面板列表。

    """
    return {"key": key, "title": title, "en": en, "cat": cat, "op": op,
            "keys": keys, "tip": tip, "palette": palette,
            "inputs": [p if isinstance(p, dict) else dict(p) for p in inputs],
            "outputs": [p if isinstance(p, dict) else dict(p) for p in outputs],
            "fields": [f for f in fields], "page": page,
            "exec_in": exec_in, "entry": entry, "sink": sink}


def _math_def(name, zh, en, inputs, keys="", tip="", **kw):
    return defn(f"math.{name}", zh, en, "数学", name,
                inputs=tuple(inputs), outputs=(pin("结果"),),
                keys=keys, tip=tip, **kw)


def _compare_def(name, zh, en, keys, tip=""):
    return defn(f"logic.{name}", zh, en, "逻辑", name,
                inputs=(pin("A"), pin("B")), outputs=(pin("结果", BOOL),),
                keys=keys, tip=tip or "A 与 B 比较成立时输出真")


STATIC: list[dict[str, Any]] = [
    # ---- 常数 ----
    defn("var.const_float", "数值", "Value (Float)", "常数", "const",
         outputs=(pin("数值", FLOAT, 1.0),),
         fields=(field("v", "数值", "float", default=1.0),),
         keys="shuzhi sz value float changliang cl 常量", tip="左右拖动可直接改值，双击输入精确值"),
    defn("var.const_int", "整数", "Value (Int)", "常数", "const",
         outputs=(pin("整数", INT, 1),),
         fields=(field("v", "整数", "int", default=1),),
         keys="zhengshu zs int value 整数常量"),
    defn("var.const_bool", "布尔", "Value (Bool)", "常数", "const",
         outputs=(pin("布尔", BOOL, True),),
         fields=(field("v", "布尔", "bool", default=True),),
         keys="boer bool 真 假 开关 kg"),
    defn("var.const_str", "文本", "Value (String)", "常数", "const",
         outputs=(pin("文本", STR, ""),),
         fields=(field("v", "文本", "str", default=""),),
         keys="wenben wb str string 文本"),

    # ---- 数学 ----
    _math_def("add", "相加", "Add (+)", (pin("A"), pin("B")), keys="xiangjia xj add jia 求和 sum +"),
    _math_def("sub", "相减", "Subtract (-)", (pin("A"), pin("B")), keys="xiangjian xj sub jian -"),
    _math_def("mul", "相乘", "Multiply (*)",
              (pin("A", FLOAT, 1.0), pin("B", FLOAT, 1.0)), keys="xiangcheng xc mul cheng *"),
    _math_def("div", "相除", "Divide (/)",
              (pin("A", FLOAT, 1.0), pin("B", FLOAT, 1.0)), keys="xiangchu xg div chu /"),
    _math_def("mod", "取余", "Modulo (%)", (pin("A"), pin("B")), keys="quyu qy mod %"),
    _math_def("pow", "幂", "Power", (pin("底", FLOAT, 2.0), pin("指数", FLOAT, 2.0)),
              keys="mi pow 次方 指数"),
    _math_def("min", "较小值", "Min", (pin("A"), pin("B")), keys="zuixiao zx min 最小"),
    _math_def("max", "较大值", "Max", (pin("A"), pin("B")), keys="zuixiao zx max 最大"),
    _math_def("lerp", "插值混合", "Mix (Lerp)",
              (pin("A"), pin("B", FLOAT, 1.0), pin("Alpha", FLOAT, 0.5)),
              keys="chazhi cz hunhe hh mix lerp"),
    defn("math.abs", "绝对值", "Abs", "数学", "abs", inputs=(pin("输入"),),
         outputs=(pin("结果"),), keys="jueduizhi jd abs"),
    defn("math.neg", "取反", "Negate", "数学", "neg", inputs=(pin("输入"),),
         outputs=(pin("结果"),), keys="qufan qf neg 相反数"),
    defn("math.sqrt", "平方根", "Square Root", "数学", "sqrt", inputs=(pin("输入"),),
         outputs=(pin("结果"),), keys="pingfanggen png sqrt 根号"),
    defn("math.sin", "正弦", "Sin", "数学", "sin", inputs=(pin("弧度"),),
         outputs=(pin("结果"),), keys="zhengxian zx sin"),
    defn("math.cos", "余弦", "Cos", "数学", "cos", inputs=(pin("弧度"),),
         outputs=(pin("结果"),), keys="yuxian yx cos"),

    # ---- 范围 ----
    defn("range.clamp", "钳制", "Clamp", "范围", "clamp",
         inputs=(pin("值"), pin("最小", FLOAT, 0.0), pin("最大", FLOAT, 200.0)),
         outputs=(pin("结果"),), keys="qianzhi qz clamp limit 限制"),
    defn("range.map", "范围映射", "Map Range", "范围", "map_range",
         inputs=(pin("值"), pin("源最小"), pin("源最大", FLOAT, 100.0),
                 pin("目标最小"), pin("目标最大", FLOAT, 200.0)),
         outputs=(pin("结果"),), keys="fanwei yingshe fw ys map range remap"),
    defn("range.round", "取整", "Round", "范围", "round", inputs=(pin("输入"),),
         outputs=(pin("结果", INT),),
         fields=(field("mode", "方式", "enum", default="round",
                       choices=("round", "floor", "ceil"),
                       labels=("四舍五入", "向下", "向上")),),
         keys="quzheng qz round floor ceil 取整"),

    # ---- 逻辑 ----
    defn("logic.compare", "比较", "Compare", "逻辑", "compare",
         inputs=(pin("A"), pin("B")),
         outputs=(pin("大于", BOOL), pin("等于", BOOL), pin("小于", BOOL)),
         keys="bijiao bj compare greater less equal 大于 小于"),
    _compare_def("eq", "等于", "Equal (A = B)", "dengyu dy equal eq == 等于"),
    _compare_def("neq", "不等于", "Not Equal (A ≠ B)",
                 "budengyu bdy neq != 不等于"),
    _compare_def("gt", "大于", "Greater (A > B)", "dayu dg greater gt > 大于"),
    _compare_def("gte", "大于等于", "Greater Or Equal (A ≥ B)",
                 "dayudengyu ddy gte ge >= 大于等于 不少于"),
    _compare_def("lt", "小于", "Less (A < B)", "xiaoyu xy less lt < 小于"),
    _compare_def("lte", "小于等于", "Less Or Equal (A ≤ B)",
                 "xiaoyudengyu xdy lte le <= 小于等于 不超过"),
    defn("logic.and", "与", "AND", "逻辑", "and",
         inputs=(pin("A", ANY, True), pin("B", ANY, True)), outputs=(pin("结果", BOOL),),
         keys="yu and 并且 且"),
    defn("logic.or", "或", "OR", "逻辑", "or",
         inputs=(pin("A", ANY), pin("B", ANY)), outputs=(pin("结果", BOOL),),
         keys="huo or 或者"),
    defn("logic.not", "非", "NOT", "逻辑", "not", inputs=(pin("值", ANY, True),),
         outputs=(pin("结果", BOOL),), keys="fei not 取反"),
    defn("logic.select", "选择", "Select", "逻辑", "select",
         inputs=(pin("条件", ANY, True), pin("为真时", FLOAT, 1.0), pin("为假时")),
         outputs=(pin("结果"),), keys="xuanze xz select ternary 三元"),

    # ---- 流程 ----
    defn("flow.branch", "If-Else 分支", "Branch", "流程控制", "branch", exec_in=True,
         inputs=(pin("条件", ANY, True), pin("真值", FLOAT, 1.0), pin("假值")),
         outputs=(pin("满足", EXEC), pin("不满足", EXEC), pin("输出值")),
         keys="fenzhi fz branch if else 判断 条件 tiaojian",
         tip="事件流里的一个节点块：执行流按条件分流，数值按条件选值"),
    defn("flow.gate", "门控", "Gate", "流程控制", "gate", exec_in=True,
         inputs=(pin("打开", ANY, True),), outputs=(pin("通过", EXEC),),
         keys="menkong gk gate 拦截 lanjie"),
    defn("flow.sequence", "序列", "Sequence", "流程控制", "sequence", exec_in=True,
         outputs=(pin("第一", EXEC), pin("第二", EXEC), pin("第三", EXEC)),
         keys="xulie xl sequence 顺序 并行"),

    # ---- 变量 ----
    # 变量卡由右侧变量表拖出（var.read.<名> / var.write.<名>）：卡片本身就是即时
    # 读 / 即时写端。下面两张通用卡只保留给旧配置迁移出来的画布，不再进卡片面板。
    defn("var.temp_read", "读取变量", "Get Variable", "变量", "temp_read",
         palette=False, outputs=(pin("值"),),
         fields=(field("name", "变量", "text", default="", pool="var"),),
         keys="duqu bianliang bl read get 临时变量 lsb lingshi",
         tip="读取临时变量 / 模块回传变量"),
    defn("var.temp_write", "写入变量", "Set Variable", "变量", "temp_write",
         exec_in=True, sink=True, palette=False, inputs=(pin("值"),),
         outputs=(pin("写入值"),),
         fields=(field("name", "变量", "text", default="", pool="var"),),
         keys="xieji bianliang bl write set 临时变量 赋值",
         tip="不接执行流时每拍写入；接执行流则只在触发时写入"),

    # ---- 表达式 ----
    defn("expr.formula", "公式", "Formula (A, B)", "表达式", "formula",
         inputs=(pin("A"), pin("B")), outputs=(pin("结果"),),
         fields=(field("expr", "公式", "expr", default="{A} + {B}"),),
         keys="gongshi gs formula expr 表达式 bda",
         tip="公式里用 {A} {B} 引用两个输入引脚"),
    defn("expr.free", "自由公式", "Free Expression", "表达式", "free_expr",
         outputs=(pin("结果"),),
         fields=(field("expr", "表达式", "expr", default="", pool="var"),),
         keys="ziyou gs formula free 表达式 全变量",
         tip="可直接引用全部变量：核心读出参数、模块参数、临时变量"),

    # ---- 实用 ----
    defn("util.reroute", "中继", "Reroute", "实用", "reroute",
         inputs=(pin("输入", ANY),), outputs=(pin("输出", ANY),),
         keys="zhongji zj reroute 转发 跳线"),
    defn("util.to_int", "转整数", "To Int", "实用", "to_int",
         inputs=(pin("输入", ANY),), outputs=(pin("整数", INT),),
         keys="zhuan zhengshu convert int 数值化"),
    defn("util.to_bool", "转布尔", "To Bool", "实用", "to_bool",
         inputs=(pin("输入", ANY),), outputs=(pin("布尔", BOOL),),
         keys="zhuan boer convert bool"),
    defn("util.to_str", "转文本", "To String", "实用", "to_str",
         inputs=(pin("输入", ANY),), outputs=(pin("文本", STR),),
         keys="zhuan wenben convert str 文本化"),
]

PINYIN = {
    "核心写入": "hexiexie ru hx xr write",
    "核心读出": "hexi du hx qc read",
    "模块事件": "mokuai shijian mk sj driver",
    "模块参数": "mokuai canshu mk cs",
    "流程控制": "liucheng kongzhi lc kz flow",
    "数学": "shuxue sx math",
    "范围": "fanwei fw range",
    "逻辑": "luoji lj logic",
    "常数": "changshu cs const",
    "变量": "bianliang bl var",
    "表达式": "biaodashi ds expr",
    "实用": "shiyong sy util",
}

_MISSING = defn("__missing__", "失效卡片", "Missing", "失效", "reroute",
                inputs=(pin("输入", ANY),), outputs=(pin("输出", ANY),),
                tip="引用的参数或模块已下线")


def _core_write_defs() -> list[dict[str, Any]]:
    out = []
    for spec in core_inputs():
        key = spec["key"]
        ptype = BOOL if str(spec.get("type")) == "Bool" else INT
        out.append(defn(
            f"core.write.{key}", spec["label"], f"Write {key}", "核心写入",
            "core_write", page=PAGE_INPUT, exec_in=True, sink=True,
            inputs=(pin("值", ptype),), outputs=(pin("写入值", INT),),
            fields=(field("key", "参数", "text", default=key, pool="core_in"),),
            keys=f"{key} {spec.get('group','')} xieru",
            tip=f"写入 {spec['label']}：{spec.get('desc') or ''}"))
    return out


def _core_read_defs(device_count: int) -> list[dict[str, Any]]:
    out = []
    seen: set[str] = set()
    for family in ("COYOTE", "OVC", "BMTR"):
        for index in range(1, max(1, device_count) + 1):
            for spec in output_specs(family, index):
                key = str(spec["key"])
                if key in seen:
                    continue
                seen.add(key)
                out.append(defn(
                    f"core.read.{key}", spec["label"], f"Read {key}", "核心读出",
                    "core_read", page=PAGE_OUTPUT,
                    outputs=(pin("值", spec_type(spec.get("type"))),),
                    fields=(field("key", "参数", "text", default=key,
                                  pool="core_out"),),
                    keys=f"{key} {family.lower()} {spec.get('desc', '')} duqu dr",
                    tip=f"读出 {spec['label']}"))
    out.append(defn("core.read.Action", "App 按键反馈", "Read Action", "核心读出",
                    "core_read", page=PAGE_OUTPUT,
                    outputs=(pin("按键编号", INT),),
                    fields=(field("key", "参数", "text", default="Action"),),
                    keys="Action anjian aj 按键", tip="读出 App 按键反馈 0-9"))
    return out


def _event_defs() -> list[dict[str, Any]]:
    return [
        defn("mod.period", "周期更新", "Tick", "模块事件",
             "driver_period", entry=True, page="both",
             outputs=(pin("触发", EXEC), pin("值")),
             fields=(field("period_ms", "周期", "int", default=100,
                           minimum=MIN_PERIOD_MS, maximum=3600000),
                     field("var", "取值变量", "text", default="", pool="var")),
             keys="zhouqi zq period tick 毫秒 haomiao 周期更新 模块事件 shijian sj",
             tip="按毫秒周期发一次执行流，「值」取该变量的当前值（留空为 1）"),
        defn("mod.change", "值变动时", "On Change", "模块事件",
             "driver_change", entry=True, page="both",
             inputs=(pin("监控变量"),),
             outputs=(pin("触发", EXEC), pin("新值")),
             keys="zhibandong sbd change jiankong 监控 值变动 模块事件 shijian sj",
             tip="监控变量（连线接入）数值一变即发执行流，「新值」为变动后的值"),
    ]


def _module_defs(modules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for mod in modules:
        mid = str(mod.get("id") or "")
        if not mid:
            continue
        mname = str(mod.get("name") or mid)
        short = mname.replace("联动", "").replace("模块", "").strip() or mid
        base_keys = f"{mid} {mname} {short} mokuai mk"
        for row in _mod_pool(mod):
            name = row["name"]
            label = row.get("label") or name
            direction = row.get("dir") or "in"
            ptype = spec_type(row.get("type"))
            path = "/" in name
            cat = "临时变量" if path else "模块参数"
            leaf = name.rsplit("/", 1)[-1] if path else name
            keys = f"{base_keys} {name} {leaf} {label} duqu dr 模块读数"
            title = f"{short} · {leaf}" if path else f"{short} · {label}"
            if direction in ("in", "inout"):
                out.append(defn(
                    f"mod.read.{mid}.{name}", title, f"Read {leaf}",
                    cat, "mod_read", page=PAGE_INPUT, palette=False,
                    outputs=(pin("值", ptype),),
                    fields=(field("module", "模块", "enum", default=mid,
                                  pool="module"),
                            field("name", "参数", "text", default=name,
                                  pool="mod_param")),
                    keys=keys,
                    tip=f"读取 {mname} 的{('变量 ' + name) if path else '参数 ' + name}"))
            if direction in ("out", "inout"):
                out.append(defn(
                    f"mod.write.{mid}.{name}", title, f"Write {leaf}",
                    cat, "mod_write", page=PAGE_OUTPUT, exec_in=True,
                    sink=True, palette=False,
                    inputs=(pin("值", ptype),), outputs=(pin("写入值", ptype),),
                    fields=(field("module", "模块", "enum", default=mid,
                                  pool="module"),
                            field("name", "参数", "text", default=name,
                                  pool="mod_param")),
                    keys=f"{keys} xieru xw 模块回传 赋值",
                    tip=f"写入 {mname} 的{('变量 ' + name) if path else '参数 ' + name}"
                        "（模块对外回传时读取）"))
    return out


def _mod_pool(mod: dict[str, Any]) -> list[dict[str, Any]]:
    """汇总模块可收发的参数：名字、标签、方向、值类型与是否可改名。

    方向必须由模块写明（in=宿主可读 / out=宿主可写 / inout=双向）；
    没写方向的按「模块产出的读数」处理，只有 in——读数才是默认语义，
    可写参数要显式声明，否则变量表会满屏读写、连线也接错方向。
    """
    pool: dict[str, dict[str, Any]] = {}
    for entry in (mod.get("params") or []):
        name = label = direction = vtype = ""
        renamable = False
        if isinstance(entry, (list, tuple)) and len(entry) >= 1:
            name = str(entry[0])
            label = str(entry[1]) if len(entry) > 1 else name
            vtype = str(entry[2]) if len(entry) > 2 else ""
        elif isinstance(entry, dict):
            name = str(entry.get("name") or entry.get("key") or "")
            label = str(entry.get("label") or "")
            direction = str(entry.get("dir") or "").strip().lower()
            vtype = str(entry.get("type") or "")
            renamable = bool(entry.get("renamable"))
        if not name:
            continue
        row = pool.setdefault(name, {"label": label, "dirs": set(),
                                     "type": vtype, "renamable": False})
        if label and not row["label"]:
            row["label"] = label
        if direction in ("in", "out", "inout"):
            row["dirs"].add(direction)
        if vtype:
            row["type"] = vtype
        row["renamable"] = row["renamable"] or renamable
    return [{"name": name, "label": row["label"], "dir": _pool_dir(row["dirs"]),
             "type": row["type"], "renamable": row["renamable"]}
            for name, row in sorted(pool.items())]


def _pool_dir(dirs: set[str]) -> str:
    """同一参数被多处登记时方向取并集：只有一边写明可写才算可写。

    后写的行不再静默覆盖先写的行——上一轮 OSC 的 link_params 标可写、
    temp_specs 标可读，覆盖语义让头像参数整列翻成了反的。
    """
    if "inout" in dirs or ("in" in dirs and "out" in dirs):
        return "inout"
    if dirs == {"out"}:
        return "out"
    return "in"


def module_pool(mod: dict[str, Any]) -> list[dict[str, str]]:
    """对外接口：某模块可收发的参数（name / label / dir / type）。"""
    return _mod_pool(mod)


def var_card_key(name: str, direction: str, page: str = "",
                 module_id: str = "") -> str:
    """变量 → 卡片 def key：拖出来的就是这张变量的即时读数卡 / 即时写入卡。

    读数卡出数值、写入卡收数值；只有单一方向的变量在两页都能建。
    """
    low = str(direction or "").strip().lower()
    readable = low != "out"
    writable = low != "in"
    if page == PAGE_INPUT and readable:
        kind = "read"
    elif writable:
        kind = "write"
    else:
        kind = "read"
    return (f"mod.{kind}.{module_id}.{name}" if module_id
            else f"var.{kind}.{name}")


def _var_defs(user_vars: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """变量表里每个用户变量各一张即时读数卡 / 即时回传卡（由变量表拖入，不进面板）。"""
    out: list[dict[str, Any]] = []
    for row in user_vars or []:
        name = valid_var_name(row.get("name"))
        if not name:
            continue
        note = str(row.get("note") or "")
        leaf = name.rsplit("/", 1)[-1]
        ptype = spec_type(row.get("type"))
        keys = f"{name} {leaf} {note} bianliang bl duqu dr xieru xw 临时变量 lsb"
        out.append(defn(
            f"var.read.{name}", f"读数 · {leaf}", f"Read {leaf}",
            "临时变量", "temp_read", palette=False,
            outputs=(pin("值", ptype),),
            fields=(field("name", "变量", "text", default=name, pool="var"),),
            keys=keys, tip=f"读取变量 {name}"))
        out.append(defn(
            f"var.write.{name}", f"写入 · {leaf}", f"Write {leaf}",
            "临时变量", "temp_write", exec_in=True, sink=True, palette=False,
            inputs=(pin("值", ptype),), outputs=(pin("写入值", ptype),),
            fields=(field("name", "变量", "text", default=name, pool="var"),),
            keys=keys, tip=f"写入变量 {name}"))
    return out


class Catalog:

    def __init__(self):
        self._defs: dict[str, dict[str, Any]] = {}
        self._static: dict[str, dict[str, Any]] = {}
        for item in STATIC:
            self._static[item["key"]] = item
        self.refresh()

    def refresh(self, modules: list[dict[str, Any]] | None = None,
                device_count: int = 1,
                user_vars: list[dict[str, Any]] | None = None) -> None:
        self._defs = dict(self._static)
        for item in _core_write_defs():
            self._defs[item["key"]] = item
        for item in _core_read_defs(device_count):
            self._defs[item["key"]] = item
        for item in _event_defs():
            self._defs[item["key"]] = item
        for item in _module_defs(modules or []):
            self._defs[item["key"]] = item
        for item in _var_defs(user_vars or []):
            self._defs[item["key"]] = item

    @property
    def defs(self) -> dict[str, dict[str, Any]]:
        return self._defs

    def definition(self, key: str) -> dict[str, Any]:
        return self._defs.get(str(key or "")) or _MISSING

    def known(self, key: str) -> bool:
        return str(key or "") in self._defs

    def templates(self, page: str) -> list[dict[str, Any]]:
        """卡片面板可见的卡片：变量类卡片（palette=False）只能从变量表拖入。"""
        return [d for d in self._defs.values()
                if (d["page"] == "both" or d["page"] == page)
                and d.get("palette", True)]

    def grouped(self, page: str) -> list[tuple[str, list[dict[str, Any]]]]:
        order: list[str] = []
        buckets: dict[str, list[dict[str, Any]]] = {}
        for item in self.templates(page):
            cat = item["cat"]
            if cat not in buckets:
                buckets[cat] = []
                order.append(cat)
            buckets[cat].append(item)
        order.sort(key=lambda c: (c not in ("模块事件", "模块参数", "临时变量",
                                            "核心写入", "核心读出"), c))
        return [(cat, buckets[cat]) for cat in order]

    def field_spec(self, def_key: str, fid: str) -> dict[str, Any] | None:
        for item in self.definition(def_key).get("fields") or []:
            if item["id"] == fid:
                return item
        return None

    def default_params(self, def_key: str) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for item in self.definition(def_key).get("fields") or []:
            if item["type"] == "expr":
                out[item["id"]] = ""
            else:
                out[item["id"]] = item["default"]
        return out

    def search(self, page: str, query: str, limit: int = 60) -> list[dict[str, Any]]:
        tokens = [t for t in str(query or "").strip().lower().split() if t]
        rows = []
        for item in self.templates(page):
            score, hl = 100, (-1, 0)
            for i, tok in enumerate(tokens):
                s, start, ln = _match_token(tok, item)
                if s == 0:
                    score = 0
                    break
                score = min(score, s)
                if i == 0 and start >= 0:
                    hl = (start, ln)
            if score == 0 and tokens:
                continue
            rows.append({"def": item, "score": score, "hl": hl})
        rows.sort(key=lambda r: (-r["score"], r["def"]["cat"], r["def"]["title"]))
        return rows[:limit]


def _match_token(tok: str, item: dict[str, Any]) -> tuple[int, int, int]:
    title = item["title"].lower()
    if title.startswith(tok):
        return 90, title.index(tok), len(tok)
    if tok in title:
        return 60, title.index(tok), len(tok)
    en = item["en"].lower()
    if en.startswith(tok):
        return 80, -1, 0
    if tok in en:
        return 50, -1, 0
    key = item["key"].lower()
    if tok in key:
        return 55, -1, 0
    for token in str(item.get("keys") or "").split():
        if token == tok:
            return 75, -1, 0
        if token.startswith(tok):
            return 45, -1, 0
        if tok in token:
            return 25, -1, 0
    cat = item["cat"].lower()
    if cat.startswith(tok) or tok in cat:
        return 30, -1, 0
    for token in PINYIN.get(item["cat"], "").split():
        if token.startswith(tok):
            return 28 - len(tok) * 0.1, -1, 0
        if tok in token:
            return 18, -1, 0
    return 0, -1, 0


class FlowNode:

    def __init__(self, node_id: str, def_key: str, x: float = 0.0, y: float = 0.0,
                 params: dict[str, Any] | None = None, alias: str = "",
                 overrides: dict[int, Any] | None = None):
        self.id = node_id
        self.def_key = def_key
        self.x = float(x)
        self.y = float(y)
        self.params: dict[str, Any] = dict(params or {})
        self.alias = alias
        self.overrides: dict[int, Any] = {}
        for index, value in (overrides or {}).items():
            try:
                self.overrides[int(index)] = value
            except (TypeError, ValueError):
                continue
        self.selected = False
        self.live: list[Any] = []
        self.error: str = ""

    def title(self, catalog: Catalog) -> str:
        return self.alias or catalog.definition(self.def_key)["title"]

    def param(self, catalog: Catalog, fid: str, default: Any = None) -> Any:
        item = catalog.definition(self.def_key)
        for spec in item.get("fields") or []:
            if spec["id"] == fid:
                return self.params.get(fid, spec["default"])
        return self.params.get(fid, default)

    def inputs(self, catalog: Catalog) -> list[dict[str, Any]]:
        return catalog.definition(self.def_key)["inputs"]

    def outputs(self, catalog: Catalog) -> list[dict[str, Any]]:
        return catalog.definition(self.def_key)["outputs"]

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "def": self.def_key, "x": self.x, "y": self.y,
                "params": self.params, "alias": self.alias,
                "overrides": {str(k): v for k, v in self.overrides.items()}}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FlowNode":
        return cls(str(data.get("id") or ""), str(data.get("def") or ""),
                   float(data.get("x") or 0.0), float(data.get("y") or 0.0),
                   dict(data.get("params") or {}), str(data.get("alias") or ""),
                   dict(data.get("overrides") or {}))


class FlowWire:

    def __init__(self, wire_id: str, src: tuple[str, int], dst: tuple[str, int],
                 ptype: str = FLOAT):
        self.id = wire_id
        self.src = (str(src[0]), int(src[1]))
        self.dst = (str(dst[0]), int(dst[1]))
        self.type = ptype
        self.selected = False
        self.value: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "src": [self.src[0], self.src[1]],
                "dst": [self.dst[0], self.dst[1]], "type": self.type}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FlowWire":
        src = list(data.get("src") or ["", 0])
        dst = list(data.get("dst") or ["", 0])
        return cls(str(data.get("id") or ""), (str(src[0]), int(src[1])),
                   (str(dst[0]), int(dst[1])), str(data.get("type") or FLOAT))


class FlowGraph:

    def __init__(self, page: str = PAGE_INPUT):
        self.page = page
        self.prefix = ID_PREFIX.get(page, "n")
        self.nodes: list[FlowNode] = []
        self.wires: list[FlowWire] = []
        self._next = 1
        self.selected: set[str] = set()
        self.selected_wires: set[str] = set()

    def new_id(self, tag: str = "") -> str:
        while True:
            candidate = f"{self.prefix}{tag}{self._next}"
            self._next += 1
            if not self.find(candidate) and not self.wire(candidate):
                return candidate

    def find(self, node_id: str) -> FlowNode | None:
        return next((n for n in self.nodes if n.id == node_id), None)

    def wire(self, wire_id: str) -> FlowWire | None:
        return next((w for w in self.wires if w.id == wire_id), None)

    def add_node(self, catalog: Catalog, def_key: str, x: float, y: float,
                 params: dict[str, Any] | None = None) -> FlowNode:
        node = FlowNode(self.new_id(), def_key, x, y)
        node.params = catalog.default_params(def_key)
        node.params.update(params or {})
        self.nodes.append(node)
        return node

    def remove_node(self, node: FlowNode | str) -> None:
        node_id = node if isinstance(node, str) else getattr(node, "id", node)
        self.nodes = [n for n in self.nodes if n.id != node_id]
        self.wires = [w for w in self.wires
                      if w.src[0] != node_id and w.dst[0] != node_id]
        self.selected.discard(node_id)

    def remove_wire(self, wire: FlowWire | str) -> None:
        wire_id = wire if isinstance(wire, str) else getattr(wire, "id", wire)
        self.wires = [w for w in self.wires if w.id != wire_id]
        self.selected_wires.discard(wire_id)

    def clear(self) -> None:
        self.nodes = []
        self.wires = []
        self.selected.clear()
        self.selected_wires.clear()

    def wires_into(self, node_id: str, index: int) -> list[FlowWire]:
        return [w for w in self.wires if w.dst == (node_id, index)]

    def wire_into(self, node_id: str, index: int) -> FlowWire | None:
        return next((w for w in self.wires if w.dst == (node_id, index)), None)

    def wires_from(self, node_id: str, index: int) -> list[FlowWire]:
        return [w for w in self.wires if w.src == (node_id, index)]

    def in_type(self, catalog: Catalog, node: FlowNode, index: int) -> str:
        if index < 0:
            return EXEC
        return node.inputs(catalog)[index]["type"]

    def out_type(self, catalog: Catalog, node: FlowNode, index: int) -> str:
        return node.outputs(catalog)[index]["type"]

    def can_connect(self, catalog: Catalog, src: FlowNode, src_index: int,
                    dst: FlowNode, dst_index: int) -> tuple[bool, str]:
        if src is dst:
            return False, "不能连接到自身"
        if src not in self.nodes or dst not in self.nodes:
            return False, "两张画布的卡片不能直接互连，请用「写入变量 / 读取变量」衔接"
        item = catalog.definition(dst.def_key)
        if src_index < 0 or src_index >= len(src.outputs(catalog)):
            return False, "输出引脚不存在"
        if dst_index >= len(dst.inputs(catalog)):
            return False, "该节点没有可用的输入引脚"
        st = self.out_type(catalog, src, src_index)
        dt = self.in_type(catalog, dst, dst_index)
        if st == EXEC or dt == EXEC:
            if st != EXEC or dt != EXEC:
                return False, "执行流只能接到带三角入口的卡片"
            if not item["exec_in"]:
                return False, "该卡片没有执行入口"
            if self._exec_reaches(catalog, dst, src):
                return False, "会形成执行环路"
            return True, ""
        if not compatible(st, dt):
            return False, f"类型不匹配：{TYPE_LABELS.get(st)} → {TYPE_LABELS.get(dt)}"
        if self._depends_on(catalog, src, dst):
            return False, "会形成数据环路"
        return True, ""

    def connect(self, catalog: Catalog, src: FlowNode, src_index: int,
                dst: FlowNode, dst_index: int) -> tuple[FlowWire | None, str]:
        ok, why = self.can_connect(catalog, src, src_index, dst, dst_index)
        if not ok:
            return None, why
        st = self.out_type(catalog, src, src_index)
        old = self.wire_into(dst.id, dst_index)
        if old:
            self.remove_wire(old)
        wire = FlowWire(self.new_id("w"), (src.id, src_index),
                        (dst.id, dst_index), st)
        self.wires.append(wire)
        return wire, ""

    def _depends_on(self, catalog: Catalog, node: FlowNode, target: FlowNode) -> bool:
        seen, stack = set(), [node.id]
        while stack:
            nid = stack.pop()
            if nid == target.id:
                return True
            if nid in seen:
                continue
            seen.add(nid)
            current = self.find(nid)
            if current is None:
                continue
            for i in range(len(current.inputs(catalog))):
                w = self.wire_into(nid, i)
                if w:
                    stack.append(w.src[0])
        return False

    def _exec_reaches(self, catalog: Catalog, node: FlowNode, target: FlowNode) -> bool:
        seen, stack = set(), [node.id]
        while stack:
            nid = stack.pop()
            if nid == target.id:
                return True
            if nid in seen:
                continue
            seen.add(nid)
            current = self.find(nid)
            if current is None:
                continue
            for i in range(len(current.outputs(catalog))):
                for w in self.wires_from(nid, i):
                    if self.out_type(catalog, current, i) == EXEC:
                        stack.append(w.dst[0])
        return False

    def to_dict(self) -> dict[str, Any]:
        return {"page": self.page,
                "nodes": [n.to_dict() for n in self.nodes],
                "wires": [w.to_dict() for w in self.wires]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FlowGraph":
        graph = cls(str(data.get("page") or PAGE_INPUT))
        for row in data.get("nodes") or []:
            if isinstance(row, dict):
                graph.nodes.append(FlowNode.from_dict(row))
        for row in data.get("wires") or []:
            if isinstance(row, dict):
                graph.wires.append(FlowWire.from_dict(row))
        highest = 0
        for item in [n.id for n in graph.nodes] + [w.id for w in graph.wires]:
            digits = "".join(ch for ch in str(item) if ch.isdigit())
            if digits:
                highest = max(highest, int(digits))
        graph._next = highest + 1
        return graph

    def summary(self) -> str:
        return f"{len(self.nodes)} 卡片 · {len(self.wires)} 连接"


def graph_path(base_dir: str | None = None) -> str:
    """旧版单文件事件流配置（v3 及更早）的路径，只用于迁移检测。"""
    root = base_dir or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "config", "event_flow.json")


def profiles_dir(base_dir: str | None = None) -> str:
    """事件流配置文件夹：每份配置一个 JSON，active 记在 index.json 里。"""
    root = base_dir or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "config", "event_flow")


def vars_path(base_dir: str | None = None) -> str:
    """用户自建临时变量的全局清单（跨配置共用，与临时变量命名空间一致）。"""
    root = base_dir or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "config", "flow_vars.json")


_PROFILE_INDEX = "index.json"
_FILENAME_BAD = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def _profile_filename(name: str) -> str:
    """配置名 → 文件名：非法字符换成下划线；两个名字撞文件名时后者覆盖前者。"""
    stem = _FILENAME_BAD.sub("_", str(name or "").strip()).strip(" .")
    return (stem or "profile") + ".json"


def _read_json(target: str) -> Any:
    try:
        with open(target, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return None


def _write_json(target: str, payload: Any) -> bool:
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = target + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, target)
        return True
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


def _profile_payload(name: str, graphs: dict[str, FlowGraph]) -> dict[str, Any]:
    return {"version": 3, "name": str(name),
            "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "graphs": {page: graphs.get(page, FlowGraph(page)).to_dict()
                       for page in PAGES}}


def valid_var_name(name) -> str:
    """变量名允许纯名称或「/」分段路径（变量名即 OSC 路径）；非法返回空串。"""
    raw = str(name or "").strip()
    if not raw or raw.startswith("/") or raw.endswith("/") or "//" in raw:
        return ""
    parts = raw.split("/")
    try:
        return "/".join(parse_name(part) for part in parts)
    except ValueError:
        return ""


def load_vars(path: str | None = None) -> list[dict[str, str]]:
    target = path or vars_path()
    try:
        with open(target, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except Exception:
        return []
    rows = raw.get("vars") if isinstance(raw, dict) else raw
    out: list[dict[str, str]] = []
    for row in rows or []:
        if isinstance(row, str):
            row = {"name": row}
        if not isinstance(row, dict):
            continue
        name = valid_var_name(row.get("name"))
        if name and all(item["name"] != name for item in out):
            out.append({"name": name, "note": str(row.get("note") or ""),
                        "dir": valid_var_dir(row.get("dir"))})
    return out


def valid_var_dir(raw) -> str:
    """变量方向：in=只读 / out=只写 / inout=读写，其它一律按读写处理。"""
    text = str(raw or "").strip().lower()
    return text if text in ("in", "out", "inout") else "inout"


def save_vars(rows: list[dict[str, str]], path: str | None = None) -> bool:
    target = path or vars_path()
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = target + ".tmp"
    payload = {"version": 1,
               "vars": [{"name": str(row.get("name") or ""),
                      "note": str(row.get("note") or ""),
                      "dir": valid_var_dir(row.get("dir"))}
                     for row in rows]}
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, target)
        return True
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


def _empty_graphs() -> dict[str, FlowGraph]:
    return {page: FlowGraph(page) for page in PAGES}


def _graphs_of(raw: Any) -> dict[str, FlowGraph]:
    graphs = _empty_graphs()
    if isinstance(raw, dict):
        for page in PAGES:
            row = raw.get(page)
            if isinstance(row, dict):
                graphs[page] = FlowGraph.from_dict({**row, "page": page})
                _upgrade_drivers(graphs[page])
                _upgrade_expr_nodes(graphs[page])
    return graphs


_LEGACY_DRIVERS = {"period": "mod.period", "change": "mod.change"}


def _upgrade_expr_nodes(graph: FlowGraph) -> None:
    """变量表达式卡片已取消：改写成「自由公式 → 写入变量」链，原消费连线改接公式输出。"""
    for node in list(graph.nodes):
        if node.def_key != "var.temp_expr":
            continue
        name = str(node.params.get("name") or "").strip()
        text = str(node.params.get("expr") or "")
        node.def_key = "expr.free"
        node.params = {"expr": text}
        if not name:
            continue
        writer = FlowNode(graph.new_id(), "var.temp_write", node.x, node.y + 96.0)
        writer.params = {"name": name}
        graph.nodes.append(writer)
        graph.wires.append(FlowWire(graph.new_id("w"), (node.id, 0),
                                    (writer.id, 0), FLOAT))


def _upgrade_drivers(graph: FlowGraph) -> None:
    """早期「每模块一张」的周期/值变动卡片在载入时改写成通用事件卡片。"""
    for node in list(graph.nodes):
        parts = str(node.def_key or "").split(".")
        if len(parts) != 3 or parts[0] != "mod" or parts[1] not in _LEGACY_DRIVERS:
            continue
        kind = parts[1]
        node.params.pop("module", None)
        node.def_key = _LEGACY_DRIVERS[kind]
        name = str(node.params.pop("var", "") or "").strip()
        if kind != "change" or not name:
            continue
        guard = FlowNode(graph.new_id(), "var.temp_read", node.x - 210.0, node.y)
        guard.params = {"name": name}
        graph.nodes.append(guard)
        graph.wires.append(FlowWire(graph.new_id("w"), (guard.id, 0),
                                    (node.id, 0), FLOAT))


def clone_graphs(graphs: dict[str, FlowGraph]) -> dict[str, FlowGraph]:
    out: dict[str, FlowGraph] = {}
    for page in PAGES:
        src = graphs.get(page) or FlowGraph(page)
        out[page] = FlowGraph.from_dict({**src.to_dict(), "page": page})
    return out


def load_profiles(directory: str | None = None
                  ) -> tuple[str, dict[str, dict[str, FlowGraph]]]:
    """读取配置文件夹，返回 (当前配置名, {配置名: 两张画布})。

    每份配置一个 JSON（文件内记显示名，改名即换文件），当前配置记在
    index.json。旧版单文件 event_flow.json（v3 及更早）首次读到时拆分成
    独立文件并改名为 .migrated，之后只读文件夹。
    """
    directory = directory or profiles_dir()
    profiles: dict[str, dict[str, FlowGraph]] = {}
    if os.path.isdir(directory):
        for entry in sorted(os.listdir(directory)):
            if not entry.endswith(".json") or entry == _PROFILE_INDEX:
                continue
            data = _read_json(os.path.join(directory, entry))
            if not isinstance(data, dict):
                continue
            name = str(data.get("name") or entry[:-len(".json")]).strip()
            if name:
                profiles[name] = _graphs_of(data.get("graphs"))
    if not profiles:
        # 文件夹还没有任何配置：看一眼旁边的旧版单文件（config/event_flow.json），
        # 有就拆成独立文件并改名为 .migrated，之后只读文件夹
        legacy = directory + ".json"
        if os.path.isfile(legacy):
            profiles = _legacy_profiles(legacy)
            if profiles:
                for name, graphs in profiles.items():
                    _write_json(os.path.join(directory, _profile_filename(name)),
                                _profile_payload(name, graphs))
                _write_json(os.path.join(directory, _PROFILE_INDEX),
                            {"version": 1, "active": next(iter(profiles))})
                try:
                    os.replace(legacy, legacy + ".migrated")
                except OSError:
                    pass
    if not profiles:
        profiles[DEFAULT_PROFILE] = _empty_graphs()
    index = _read_json(os.path.join(directory, _PROFILE_INDEX))
    if isinstance(index, dict):
        # index 里记着上次保存的配置顺序：下拉列表不因文件名排序而跳动
        order = [str(n) for n in index.get("order") or []]
        ordered = {name: profiles.pop(name) for name in order if name in profiles}
        profiles = {**ordered, **profiles}
    active = str(index.get("active") or "") if isinstance(index, dict) else ""
    if active not in profiles:
        active = next(iter(profiles))
    return active, profiles


def _legacy_profiles(legacy: str) -> dict[str, dict[str, FlowGraph]]:
    """旧版单文件的解析：v3 的 profiles 大包、v2 及更早的单配置并入「默认」。"""
    data = _read_json(legacy)
    if not isinstance(data, dict):
        return {}
    profiles: dict[str, dict[str, FlowGraph]] = {}
    raw = data.get("profiles")
    if isinstance(raw, dict):
        for name, entry in raw.items():
            graphs = entry.get("graphs") if isinstance(entry, dict) else entry
            if name:
                profiles[str(name)] = _graphs_of(graphs)
    elif data.get("graphs"):
        profiles[DEFAULT_PROFILE] = _graphs_of(data.get("graphs"))
    return profiles


def save_profiles(active: str, profiles: dict[str, dict[str, FlowGraph]],
                  directory: str | None = None) -> bool:
    """整包落盘：每份配置一个文件，删掉已不存在的配置文件（改名 / 删除配置）。"""
    directory = directory or profiles_dir()
    os.makedirs(directory, exist_ok=True)
    ok = True
    kept: set[str] = set()
    for name, graphs in profiles.items():
        target = os.path.join(directory, _profile_filename(name))
        kept.add(os.path.normcase(os.path.basename(target)))
        ok = _write_json(target, _profile_payload(name, graphs)) and ok
    for entry in os.listdir(directory):
        if (entry.endswith(".json") and entry != _PROFILE_INDEX
                and os.path.normcase(entry) not in kept):
            try:
                os.remove(os.path.join(directory, entry))
            except OSError:
                ok = False
    if active not in profiles:
        active = next(iter(profiles), DEFAULT_PROFILE)
    ok = _write_json(os.path.join(directory, _PROFILE_INDEX),
                     {"version": 1, "active": active,
                      "order": list(profiles)}) and ok
    return ok


def load_graphs(path: str | None = None) -> dict[str, FlowGraph]:
    """读一份单配置文件（模块自带的默认事件流就是这种格式）。"""
    data = _read_json(path or graph_path())
    if isinstance(data, dict) and isinstance(data.get("profiles"), dict):
        raw = data.get("profiles")
        entry = raw.get(str(data.get("active") or "")) or next(iter(raw.values()), None)
        graphs = entry.get("graphs") if isinstance(entry, dict) else entry
        return _graphs_of(graphs)
    return _graphs_of(data.get("graphs") if isinstance(data, dict) else None)


def save_graphs(graphs: dict[str, FlowGraph], path: str | None = None) -> bool:
    return _write_json(path or graph_path(),
                       {"version": 3,
                        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "graphs": {page: graphs.get(page, FlowGraph(page)).to_dict()
                                   for page in PAGES}})


def profile_modules(graphs: dict[str, FlowGraph]) -> list[str]:
    """这张配置引用到的联动模块 id（按卡片 def_key 解析）。"""
    out: list[str] = []
    for graph in (graphs or {}).values():
        for node in getattr(graph, "nodes", []) or []:
            parts = str(node.def_key or "").split(".")
            if len(parts) < 3 or parts[0] != "mod" or parts[1] not in _MOD_KINDS:
                continue
            mid = parts[2]
            if mid and mid not in out:
                out.append(mid)
    return out


class _Tick:

    def __init__(self, runtime: "FlowRuntime", values: dict[str, float], now: float):
        self.rt = runtime
        self.catalog = runtime.catalog
        self.values = values
        self.now = now
        self.memo: dict[tuple[str, int], Any] = {}
        self.active: set[str] = set()
        self.done: set[str] = set()
        self.exec_seen: set[str] = set()
        self.chain: list[str] = []
        self.steps = 0
        self.errors: dict[str, str] = {}
        self.owner: dict[str, FlowGraph] = {}
        self.written: list[str] = []


class FlowRuntime:

    def __init__(self, catalog: Catalog | None = None,
                 graphs: dict[str, FlowGraph] | None = None, *,
                 read_core: Callable[[], dict[str, float]] | None = None,
                 read_modules: Callable[[], dict[str, dict[str, float]]] | None = None,
                 write_core: Callable[[str, int], None] | None = None,
                 write_module: Callable[[str, str, Any], None] | None = None,
                 rename_module: Callable[[str, str, str], str] | None = None,
                 temps: dict[str, float] | None = None,
                 base_dir: str | None = None,
                 log: Callable[[str], None] | None = None):
        self.catalog = catalog or Catalog()
        self.graphs = graphs or {page: FlowGraph(page) for page in PAGES}
        self.profiles: dict[str, dict[str, FlowGraph]] = {DEFAULT_PROFILE: self.graphs}
        self.active = DEFAULT_PROFILE
        self._read_core = read_core or (lambda: {})
        self._read_modules = read_modules or (lambda: {})
        self._emit_core = write_core or (lambda key, value: None)
        self._emit_module = write_module or (lambda mid, name, value: None)
        self._rename_module = rename_module or (lambda mid, old, new: "")
        self.temps = temps if temps is not None else {}
        self.base_dir = base_dir
        self._log = log
        self.flow_dir = profiles_dir(base_dir)
        self.vars_file = vars_path(base_dir)
        self.user_vars: list[dict[str, str]] = []
        self.declared_vars: list[dict[str, str]] = []
        self._last_core: dict[str, int] = {}
        self._last_module: dict[tuple[str, str], Any] = {}
        self._periodic = {spec["key"] for spec in core_inputs()
                          if str(spec.get("action")) == "pulse"}
        self._driver_state: dict[str, Any] = {}
        self.stats: dict[str, Any] = {"nodes": 0, "wires": 0, "steps": 0,
                                      "fired": 0, "written": 0}
        self.chain: list[str] = []
        self.errors: dict[str, str] = {}
        self.armed = True

    # ------------------------------------------------------------------ 配置
    def load(self) -> None:
        self.active, self.profiles = load_profiles(self.flow_dir)
        self.graphs = self.profiles[self.active]
        self.user_vars = load_vars(self.vars_file)
        self._declare_user_vars()

    def save(self) -> bool:
        return save_profiles(self.active, self.profiles, self.flow_dir)

    # -------------------------------------------------------------- 临时变量表
    def _declare_user_vars(self) -> None:
        """用户登记的变量先占位，卡片与表达式才认得名（值由写入卡 / 模块回填）。"""
        for row in self.user_vars:
            self.temps.setdefault(row["name"], 0.0)

    def save_user_vars(self) -> bool:
        return save_vars(self.user_vars, self.vars_file)

    def add_user_var(self, name, note: str = "",
                     direction: str = "inout") -> str:
        clean = valid_var_name(name)
        if not clean:
            return "变量名需字母开头，可用字母/数字/下划线，或 a/b 形式路径"
        if any(row["name"] == clean for row in self.user_vars):
            return "已有同名变量"
        if self.var_clash(clean):
            return f"「{clean}」已被系统或模块登记的参数占用，换一个名字"
        self.user_vars.append({"name": clean, "note": str(note or ""),
                               "dir": valid_var_dir(direction)})
        self.temps.setdefault(clean, 0.0)
        self.save_user_vars()
        return ""

    def rename_user_var(self, old: str, new) -> str:
        clean = valid_var_name(new)
        if not clean:
            return "变量名需字母开头，可用字母/数字/下划线，或 a/b 形式路径"
        if clean == str(old or ""):
            return ""
        row = next((r for r in self.user_vars if r["name"] == old), None)
        if row is None:
            return "该变量由模块登记，不能在这里改名"
        if any(r["name"] == clean for r in self.user_vars):
            return "已有同名变量"
        if self.var_clash(clean):
            return f"「{clean}」已被系统或模块登记的参数占用，换一个名字"
        row["name"] = clean
        if old in self.temps:
            self.temps[clean] = self.temps.pop(old)
        else:
            self.temps.setdefault(clean, 0.0)
        self._retarget_var(old, clean)
        self.save_user_vars()
        return ""

    def set_var_dir(self, name: str, direction: str) -> str:
        """切换临时变量的读 / 写属性：拖出来的卡片方向与变量表标记跟着变。"""
        row = next((r for r in self.user_vars if r["name"] == name), None)
        if row is None:
            return "该变量由模块登记，读写属性由模块决定"
        row["dir"] = valid_var_dir(direction)
        self.save_user_vars()
        return ""

    def user_var_dir(self, name: str) -> str:
        row = next((r for r in self.user_vars if r["name"] == name), None)
        return valid_var_dir((row or {}).get("dir"))

    def rename_var_row(self, row: dict[str, Any], new: str) -> str:
        """改名：用户变量走变量表，模块登记的可改名行转交模块改它自己的地址。"""
        name = str(row.get("name") or "")
        mid = str(row.get("mid") or "")
        if mid:
            clean = valid_var_name(new)
            if not clean:
                return "变量名需字母开头，可用字母/数字/下划线，或 a/b 形式路径"
            if clean == name:
                return ""
            if any(r["name"] == clean for r in self.user_vars):
                return "已有同名变量"
            note = self._rename_module(mid, name, clean)
            if note:
                return str(note)
            self._retarget_var(name, clean)
            self.save()
            return ""
        return self.rename_user_var(name, new)

    def remove_user_var(self, name: str) -> bool:
        rows = [r for r in self.user_vars if r["name"] != name]
        if len(rows) == len(self.user_vars):
            return False
        self.user_vars = rows
        self.temps.pop(name, None)
        self.save_user_vars()
        return True

    def var_table(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """变量表两栏：(系统登记参数·不可改名, 可改名参数·含临时变量)。

        两栏都只认模块登记（link_params / temp_specs）：变量表是「模块 / 用户
        登记的变量」清单，核心自己的设备读数（COYOTE.StrengthA、Action 等）
        不算登记——它们在画布上是核心读数卡片，不进变量表占一行。
        设备一连上参数就出现，不必等第一条数据到达；模块下线后它的登记行立刻
        消失——模块写进共享值空间的残值不算登记，不回填进表里。
        模块登记时标了 renamable 的行（如 OSC 头像参数）归入右栏，可改名。
        """
        values = self.value_space()
        user_names = {row["name"] for row in self.user_vars}
        rows: dict[str, dict[str, Any]] = {}
        for item in self.declared_vars:
            name = str(item.get("name") or "")
            if not name or name in user_names:
                continue
            rows[name] = {"name": name, "label": str(item.get("label") or ""),
                          "dir": str(item.get("dir") or "in"),
                          "type": str(item.get("type") or ""),
                          "mid": str(item.get("mid") or ""),
                          "source": str(item.get("source") or ""),
                          "renamable": bool(item.get("renamable")),
                          "value": values.get(name)}
        system = sorted((row for row in rows.values() if not row["renamable"]),
                        key=lambda r: r["name"])
        module_rows = sorted((row for row in rows.values() if row["renamable"]),
                             key=lambda r: r["name"])
        user = [{"name": row["name"], "label": str(row.get("note") or ""),
                 "dir": valid_var_dir(row.get("dir")), "type": "", "mid": "",
                 "source": "临时变量", "renamable": True,
                 "value": self.temps.get(row["name"])}
                for row in self.user_vars]
        return system, user + module_rows

    def var_clash(self, name: str) -> bool:
        """与系统登记参数或模块已写入的变量重名时算冲突。"""
        if name in self.system_var_names():
            return True
        if name in self.temps and all(row["name"] != name for row in self.user_vars):
            return True
        return False

    def system_var_names(self) -> set[str]:
        """模块登记 + 模块已写入 + 核心读出的参数名：用户变量不能与它们重名。"""
        names: set[str] = set()
        for item in self.declared_vars:
            name = str(item.get("name") or "")
            if name:
                names.add(name)
        for signals in self.module_signals().values():
            names.update(str(key) for key in signals)
        for key in self.read_core_values():
            full = str(key)
            names.add(full)
            names.add(full.rsplit(".", 1)[-1])
        return names

    def _retarget_var(self, old: str, new: str) -> None:
        """改名后把引用旧名的变量卡片一并改接，避免画布指向空变量。

        模块登记的卡片 def key 里带着变量名（mod.read.<模块>.<名>），改名要连
        key 一起换，否则目录里查不到就整张卡变「失效卡片」。
        """
        for graphs in self.profiles.values():
            for graph in graphs.values():
                for node in graph.nodes:
                    suffix = "." + str(old or "")
                    key = str(node.def_key or "")
                    for prefix in ("mod.read.", "mod.write."):
                        if key.startswith(prefix) and key.endswith(suffix):
                            node.def_key = key[:-len(suffix)] + "." + new
                            break
                    item = self.catalog.definition(node.def_key)
                    if item["op"] not in ("temp_read", "temp_write"):
                        continue
                    if str(node.params.get("name") or "") == old:
                        node.params["name"] = new

    @property
    def profile_names(self) -> list[str]:
        return list(self.profiles)

    def switch_profile(self, name: str) -> bool:
        if name not in self.profiles or name == self.active:
            return False
        self.active = name
        self.graphs = self.profiles[name]
        self._last_core.clear()
        self._last_module.clear()
        self._driver_state.clear()
        return True

    def new_profile(self, name: str = "") -> str:
        index = 1
        target = (name or "").strip()
        while not target or target in self.profiles:
            target = f"配置{index}"
            index += 1
        self.profiles[target] = clone_graphs(self.graphs)
        self.active = target
        self.graphs = self.profiles[target]
        return target

    def rename_profile(self, old: str, new: str) -> str:
        """改名成功返回新名，失败返回中文错误说明。"""
        new = (new or "").strip()
        if old not in self.profiles:
            return "找不到要改名的配置"
        if not new:
            return "名称不能为空"
        if new == old:
            return ""
        if new in self.profiles:
            return f"已存在同名配置「{new}」"
        self.profiles = {new if key == old else key: graphs
                         for key, graphs in self.profiles.items()}
        if self.active == old:
            self.active = new
        return ""

    def graph(self, page: str) -> FlowGraph:
        return self.graphs.get(page) or self.graphs[PAGE_INPUT]

    def log(self, text: str) -> None:
        if self._log is not None:
            try:
                self._log(text)
            except Exception:
                pass

    # ---------------------------------------------------------------- 值空间
    def read_core_values(self) -> dict[str, float]:
        try:
            values = dict(self._read_core() or {})
        except Exception:
            values = {}
        values.update(core_alias_values(values))
        return values

    def module_signals(self) -> dict[str, dict[str, float]]:
        try:
            return dict(self._read_modules() or {})
        except Exception:
            return {}

    def value_space(self) -> dict[str, float]:
        values = self.read_core_values()
        for signals in self.module_signals().values():
            for name, value in signals.items():
                values.setdefault(name, value)
        for mid, signals in self.module_signals().items():
            for name, value in signals.items():
                values[f"{mid}.{name}"] = value
        for name, value in self.temps.items():
            values[name] = value
        return values

    def var_pool(self) -> list[tuple[str, str]]:
        pool: dict[str, str] = {}
        for name in self.temps:
            pool[name] = "临时变量"
        for mid, signals in self.module_signals().items():
            for name in signals:
                pool.setdefault(name, f"模块信号 · {mid}")
        for key, value in self.read_core_values().items():
            if "." in str(key) or str(key) == "Action":
                pool.setdefault(key, "核心读出")
        return sorted(pool.items())

    def module_ids(self) -> list[str]:
        return sorted(self.module_signals())

    # ------------------------------------------------------------------ 求值
    def tick(self, now: float | None = None) -> dict[str, Any]:
        now = time.monotonic() if now is None else now
        ctx = _Tick(self, self.value_space(), now)
        for page in PAGES:
            graph = self.graphs.get(page) or FlowGraph(page)
            for node in graph.nodes:
                ctx.owner[node.id] = graph
        self._prime_reads(ctx)
        self._continuous_sinks(ctx, PAGE_OUTPUT)
        fired = self._eval_drivers(ctx)
        self._walk_exec(ctx, fired)
        self._continuous_sinks(ctx, PAGE_INPUT)
        self._collect(ctx)
        return self.stats

    def _prime_reads(self, ctx: _Tick) -> None:
        """读数卡片每拍都算一遍：卡片上要直接显示实时值，与是否接线无关。
        「读取变量」不在这里算——同拍写入的值会被 memo 缓存成上一拍，交给界面直读变量表。"""
        for page in PAGES:
            for node in self.graphs[page].nodes:
                if ctx.catalog.definition(node.def_key)["op"] in ("mod_read", "core_read"):
                    self._evaluate(ctx, node)

    def _ordered(self, ctx: _Tick, op: str) -> list[FlowNode]:
        rows: list[tuple[float, float, FlowNode]] = []
        for page in PAGES:
            for node in self.graphs[page].nodes:
                if ctx.catalog.definition(node.def_key)["op"] == op:
                    rows.append((node.y, node.x, node))
        rows.sort(key=lambda r: (r[0], r[1]))
        return [r[2] for r in rows]

    def _eval_drivers(self, ctx: _Tick) -> list[FlowNode]:
        fired: list[FlowNode] = []
        for node in self._ordered(ctx, "driver_period"):
            period = max(MIN_PERIOD_MS, as_float(node.param(ctx.catalog, "period_ms"))) / 1000.0
            last = self._driver_state.get(node.id)
            fire = last is None or (ctx.now - last) >= period
            if fire:
                self._driver_state[node.id] = ctx.now
            value = self._driver_value(ctx, node)
            ctx.memo[(node.id, 0)] = 1.0 if fire else 0.0
            ctx.memo[(node.id, 1)] = value
            ctx.done.add(node.id)
            node.live = [1.0 if fire else 0.0, value]
            if fire:
                fired.append(node)
        for node in self._ordered(ctx, "driver_change"):
            if not self._change_armed(ctx, node):
                ctx.memo[(node.id, 0)] = 0.0
                ctx.memo[(node.id, 1)] = 0.0
                ctx.done.add(node.id)
                node.live = [0.0, 0.0]
                continue
            current = as_float(self._pin(ctx, node, 0))
            seen = self._driver_state.get(f"{node.id}:v")
            self._driver_state[f"{node.id}:v"] = current
            fire = seen is not None and abs(current - seen) > 1e-9
            ctx.memo[(node.id, 0)] = 1.0 if fire else 0.0
            ctx.memo[(node.id, 1)] = current
            ctx.done.add(node.id)
            node.live = [1.0 if fire else 0.0, current]
            if fire:
                fired.append(node)
        return fired

    def _change_armed(self, ctx: _Tick, node: FlowNode) -> bool:
        """值变动卡片必须接线监控变量；未接线时待机并清掉基准值。"""
        graph = ctx.owner.get(node.id)
        if graph is not None and graph.wire_into(node.id, 0):
            return True
        self._driver_state.pop(f"{node.id}:v", None)
        return False

    def _driver_value(self, ctx: _Tick, node: FlowNode) -> float:
        name = str(node.param(ctx.catalog, "var") or "").strip()
        if not name:
            return 1.0
        return as_float(ctx.values.get(name, 0.0))

    # ------------------------------------------------------------ 数据求值
    def out(self, ctx: _Tick, node: FlowNode, index: int) -> Any:
        key = (node.id, index)
        if key in ctx.memo:
            return ctx.memo[key]
        outs = self._evaluate(ctx, node)
        return outs[index] if index < len(outs) else None

    def _evaluate(self, ctx: _Tick, node: FlowNode) -> list[Any]:
        memo_key = (node.id, 0)
        if memo_key in ctx.memo:
            return [ctx.memo.get((node.id, i))
                    for i in range(len(node.outputs(ctx.catalog)))]
        if node.id in ctx.active:
            return [None] * len(node.outputs(ctx.catalog))
        ctx.active.add(node.id)
        outs = self._compute(ctx, node)
        pins = node.outputs(ctx.catalog)
        while len(outs) < len(pins):
            outs.append(None)
        for i, value in enumerate(outs[:len(pins)]):
            ctx.memo[(node.id, i)] = value
        node.live = list(outs[:len(pins)])
        ctx.active.discard(node.id)
        ctx.done.add(node.id)
        ctx.steps += 1
        return outs

    def _pin(self, ctx: _Tick, node: FlowNode, index: int) -> Any:
        graph = ctx.owner.get(node.id)
        if graph is None:
            return None
        wire = graph.wire_into(node.id, index)
        pins = node.inputs(ctx.catalog)
        if wire:
            src = graph.find(wire.src[0])
            if src is not None:
                value = self.out(ctx, src, wire.src[1])
                if value is not None:
                    return coerce(value, pins[index]["type"])
            return None
        if index in node.overrides:
            return node.overrides[index]
        return pins[index]["default"]

    def _compute(self, ctx: _Tick, node: FlowNode) -> list[Any]:
        item = ctx.catalog.definition(node.def_key)
        op = item["op"]
        cat = item["cat"]
        if cat == "失效":
            return [None]
        try:
            if op == "const":
                value = node.param(ctx.catalog, "v")
                out_pin = node.outputs(ctx.catalog)[0]
                if out_pin["type"] == INT:
                    return [int(round(as_float(value)))]
                if out_pin["type"] == BOOL:
                    return [bool(value) if not isinstance(value, str)
                            else str(value).lower() in ("1", "true", "真", "on")]
                if out_pin["type"] == STR:
                    return [str(value or "")]
                return [as_float(value)]
            if op == "core_read":
                key = str(node.param(ctx.catalog, "key") or "")
                return [ctx.values.get(key)]
            if op == "mod_read":
                mid = str(node.param(ctx.catalog, "module") or "")
                name = str(node.param(ctx.catalog, "name") or "")
                signals = self.module_signals().get(mid, {})
                return [signals.get(name, ctx.values.get(name))]
            if op == "temp_read":
                name = str(node.param(ctx.catalog, "name") or "")
                return [self.temps.get(name, ctx.values.get(name))]
            if op == "reroute":
                return [self._pin(ctx, node, 0)]
            if op == "to_int":
                return [int(round(as_float(self._pin(ctx, node, 0))))]
            if op == "to_bool":
                return [as_float(self._pin(ctx, node, 0)) > 0.5]
            if op == "to_str":
                return [value_to_text(self._pin(ctx, node, 0))]
            if op in ("add", "sub", "mul", "div", "mod", "pow", "min", "max"):
                return [self._binary(ctx, node, op)]
            if op == "abs":
                return [abs(as_float(self._pin(ctx, node, 0)))]
            if op == "neg":
                return [-as_float(self._pin(ctx, node, 0))]
            if op == "sqrt":
                value = as_float(self._pin(ctx, node, 0))
                return [math.sqrt(value) if value >= 0 else 0.0]
            if op == "sin":
                return [math.sin(as_float(self._pin(ctx, node, 0)))]
            if op == "cos":
                return [math.cos(as_float(self._pin(ctx, node, 0)))]
            if op == "lerp":
                a = as_float(self._pin(ctx, node, 0))
                b = as_float(self._pin(ctx, node, 1))
                t = as_float(self._pin(ctx, node, 2))
                return [a + (b - a) * t]
            if op == "clamp":
                value = as_float(self._pin(ctx, node, 0))
                low = as_float(self._pin(ctx, node, 1))
                high = as_float(self._pin(ctx, node, 2))
                return [max(low, min(high, value))]
            if op == "map_range":
                value = as_float(self._pin(ctx, node, 0))
                in0 = as_float(self._pin(ctx, node, 1))
                in1 = as_float(self._pin(ctx, node, 2))
                out0 = as_float(self._pin(ctx, node, 3))
                out1 = as_float(self._pin(ctx, node, 4))
                span = in1 - in0
                ratio = 0.0 if abs(span) < 1e-9 else (value - in0) / span
                return [out0 + (out1 - out0) * ratio]
            if op == "round":
                value = as_float(self._pin(ctx, node, 0))
                mode = str(node.param(ctx.catalog, "mode") or "round")
                res = round(value) if mode == "round" else (
                    math.floor(value) if mode == "floor" else math.ceil(value))
                return [int(res)]
            if op == "compare":
                a = as_float(self._pin(ctx, node, 0))
                b = as_float(self._pin(ctx, node, 1))
                return [a > b, abs(a - b) < 1e-6, a < b]
            if op in ("eq", "neq", "gt", "gte", "lt", "lte"):
                a = as_float(self._pin(ctx, node, 0))
                b = as_float(self._pin(ctx, node, 1))
                near = abs(a - b) < 1e-6
                truth = {"eq": near, "neq": not near, "gt": a > b and not near,
                         "gte": a > b or near, "lt": a < b and not near,
                         "lte": a < b or near}[op]
                return [truth]
            if op == "and":
                return [self._truth(ctx, node, 0) and self._truth(ctx, node, 1)]
            if op == "or":
                return [self._truth(ctx, node, 0) or self._truth(ctx, node, 1)]
            if op == "not":
                return [not self._truth(ctx, node, 0)]
            if op == "select":
                return [self._pin(ctx, node, 1) if self._truth(ctx, node, 0)
                        else self._pin(ctx, node, 2)]
            if op == "branch":
                truth = self._truth(ctx, node, 0)
                value = self._pin(ctx, node, 1) if truth else self._pin(ctx, node, 2)
                return [None, None, value]
            if op in ("gate", "sequence"):
                return [None, None, None][:len(node.outputs(ctx.catalog))]
            if op == "formula":
                values = {"A": as_float(self._pin(ctx, node, 0)),
                          "B": as_float(self._pin(ctx, node, 1))}
                values["a"] = values["A"]
                values["b"] = values["B"]
                text = expr.normalize(str(node.param(ctx.catalog, "expr") or ""))
                return [expr.evaluate(text, values) if text else values["A"]]
            if op == "free_expr":
                text = expr.normalize(str(node.param(ctx.catalog, "expr") or ""))
                if not text:
                    return [0.0]
                values = dict(ctx.values)
                values.update(self.temps)
                return [expr.evaluate(text, values)]
            if op in ("temp_write", "core_write", "mod_write"):
                value = self._pin(ctx, node, 0)
                if op == "core_write":
                    return [self._core_value(ctx, node, value)]
                if op == "mod_write":
                    return [value]
                return [as_float(value)]
        except expr.ExprError as exc:
            node.error = str(exc)
            ctx.errors[node.id] = str(exc)
            return [None]
        except Exception as exc:
            node.error = repr(exc)
            ctx.errors[node.id] = repr(exc)
            return [None]
        node.error = ""
        return [None]

    def _binary(self, ctx: _Tick, node: FlowNode, op: str) -> float:
        a = as_float(self._pin(ctx, node, 0))
        b = as_float(self._pin(ctx, node, 1))
        if op == "add":
            return a + b
        if op == "sub":
            return a - b
        if op == "mul":
            return a * b
        if op == "div":
            return a / b if abs(b) > 1e-9 else 0.0
        if op == "mod":
            return a % b if abs(b) > 1e-9 else 0.0
        if op == "pow":
            try:
                return a ** b
            except (OverflowError, ValueError):
                return 0.0
        if op == "min":
            return min(a, b)
        return max(a, b)

    def _truth(self, ctx: _Tick, node: FlowNode, index: int) -> bool:
        value = self._pin(ctx, node, index)
        if isinstance(value, bool):
            return value
        return as_float(value) > 0.5

    # ------------------------------------------------------------ 写入动作
    def _core_value(self, ctx: _Tick, node: FlowNode, raw: Any) -> int:
        key = str(node.param(ctx.catalog, "key") or "")
        spec = input_spec(key) or {}
        low, high = spec.get("range") or (0, 200)
        value = as_float(raw)
        if str(spec.get("type")) == "Bool":
            return 1 if value > 1e-9 else 0
        limit_signal = input_limit_signal(key)
        if limit_signal:
            channel = limit_signal[-1]
            for name in (limit_signal, f"Limit{channel}"):
                cap = ctx.values.get(name)
                if cap is not None and as_float(cap) > 0:
                    high = min(high, int(as_float(cap)))
                    break
        return expr.clamp_int(value, int(low), int(high))

    def _write_core(self, ctx: _Tick, node: FlowNode, raw: Any) -> None:
        key = str(node.param(ctx.catalog, "key") or "")
        if not key:
            node.error = "未选择核心参数"
            return
        value = self._core_value(ctx, node, raw)
        node.live = [value]
        ctx.memo[(node.id, 0)] = value
        if key not in self._periodic and self._last_core.get(key) == value:
            return
        self._last_core[key] = value
        if not self.armed:
            return
        try:
            self._emit_core(key, value)
            ctx.written.append(key)
        except Exception as exc:
            ctx.errors[node.id] = f"派发失败: {exc!r}"

    def _write_module(self, ctx: _Tick, node: FlowNode, raw: Any) -> None:
        mid = str(node.param(ctx.catalog, "module") or "")
        name = str(node.param(ctx.catalog, "name") or "")
        if not mid or not name:
            node.error = "未选择模块参数"
            return
        value = as_float(raw)
        node.live = [value]
        ctx.memo[(node.id, 0)] = value
        if self._last_module.get((mid, name)) == value:
            return
        self._last_module[(mid, name)] = value
        if not self.armed:
            return
        try:
            self._emit_module(mid, name, value)
            ctx.written.append(f"{mid}.{name}")
        except Exception as exc:
            ctx.errors[node.id] = f"写入失败: {exc!r}"

    def _write_temp(self, ctx: _Tick, node: FlowNode, raw: Any) -> None:
        name = str(node.param(ctx.catalog, "name") or "").strip()
        value = as_float(raw)
        node.live = [value]
        ctx.memo[(node.id, 0)] = value
        if not name:
            node.error = "未命名"
            return
        node.error = ""
        self.temps[name] = value
        ctx.values[name] = value
        ctx.written.append(f"变量 {name}")

    def _sink_write(self, ctx: _Tick, node: FlowNode) -> None:
        item = ctx.catalog.definition(node.def_key)
        value = self._pin(ctx, node, 0)
        op = item["op"]
        if op == "core_write":
            self._write_core(ctx, node, value)
        elif op == "mod_write":
            self._write_module(ctx, node, value)
        elif op == "temp_write":
            self._write_temp(ctx, node, value)

    # ---------------------------------------------------------------- 执行流
    def _walk_exec(self, ctx: _Tick, fired: list[FlowNode]) -> None:
        for node in fired:
            graph = ctx.owner.get(node.id)
            if graph is None:
                continue
            for wire in graph.wires_from(node.id, 0):
                target = graph.find(wire.dst[0])
                if target is not None:
                    self._run(ctx, target)

    def _run(self, ctx: _Tick, node: FlowNode) -> None:
        if node.id in ctx.exec_seen:
            return
        ctx.exec_seen.add(node.id)
        graph = ctx.owner.get(node.id)
        if graph is None:
            return
        item = ctx.catalog.definition(node.def_key)
        op = item["op"]
        ctx.chain.append(node.title(ctx.catalog))
        if op in ("core_write", "mod_write", "temp_write"):
            self._sink_write(ctx, node)
            return
        if op == "branch":
            truth = self._truth(ctx, node, 0)
            self._evaluate(ctx, node)
            ctx.memo[(node.id, 0 if truth else 1)] = 1.0
            self._route(ctx, graph, node, 0 if truth else 1)
            return
        if op == "gate":
            if self._truth(ctx, node, 0):
                self._route(ctx, graph, node, 0)
            return
        if op == "sequence":
            for index in range(len(node.outputs(ctx.catalog))):
                self._route(ctx, graph, node, index)
            return
        self._evaluate(ctx, node)
        for index, out_pin in enumerate(node.outputs(ctx.catalog)):
            if out_pin["type"] == EXEC:
                self._route(ctx, graph, node, index)

    def _route(self, ctx: _Tick, graph: FlowGraph, node: FlowNode, index: int) -> None:
        for wire in graph.wires_from(node.id, index):
            target = graph.find(wire.dst[0])
            if target is not None:
                self._run(ctx, target)

    def _continuous_sinks(self, ctx: _Tick, page: str) -> None:
        graph = self.graphs.get(page)
        if graph is None:
            return
        for node in graph.nodes:
            item = ctx.catalog.definition(node.def_key)
            if not item["sink"] or not item["exec_in"]:
                continue
            if graph.wires_into(node.id, -1):
                continue
            if node.id in ctx.exec_seen:
                continue
            self._sink_write(ctx, node)

    # ---------------------------------------------------------------- 快照
    def _collect(self, ctx: _Tick) -> None:
        self.errors = dict(ctx.errors)
        self.chain = ctx.chain[:24]
        for page in PAGES:
            graph = self.graphs[page]
            for wire in graph.wires:
                wire.value = ctx.memo.get((wire.src[0], wire.src[1]))
        total_nodes = sum(len(g.nodes) for g in self.graphs.values())
        total_wires = sum(len(g.wires) for g in self.graphs.values())
        self.stats = {"nodes": total_nodes, "wires": total_wires,
                      "steps": ctx.steps, "fired": len(ctx.exec_seen),
                      "written": len(ctx.written)}

    def reset(self) -> None:
        self._last_core.clear()
        self._last_module.clear()
        self._driver_state.clear()
        self.armed = True


def migrate_legacy(catalog: Catalog, graphs: dict[str, FlowGraph],
                   module_configs: list[tuple[str, list, list]],
                   *, base_y: float = 40.0) -> int:
    """把联动页遗留的每模块 events / temps 配置翻译成事件流卡片。"""
    total = 0
    for mid, events, temps in module_configs:
        offset = 0.0
        for row in temps or []:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "").strip()
            text = expr.normalize(str(row.get("expr") or ""))
            if not name or not str(row.get("expr") or "").strip():
                continue    # 只登记名字的行交给共享变量表，不建公式卡
            graph = graphs[PAGE_INPUT]
            node = graph.add_node(catalog, "expr.free", 40.0, base_y + offset)
            node.params["expr"] = text
            node.alias = f"变量 {name}"
            writer = graph.add_node(catalog, "var.temp_write", 40.0,
                                    base_y + offset + 110.0)
            writer.params["name"] = name
            graph.connect(catalog, node, 0, writer, 0)
            offset += 250.0
            total += 1
        for card in events or []:
            if not isinstance(card, dict):
                continue
            total += _migrate_card(catalog, graphs, mid, card, base_y + offset)
            offset += 210.0
    return total


def _migrate_card(catalog: Catalog, graphs: dict[str, FlowGraph], mid: str,
                  card: dict[str, Any], top: float) -> int:
    trigger = str(card.get("trigger") or "").strip()
    arg = card.get("arg")
    name = str(card.get("name") or "").strip()
    input_graph = graphs[PAGE_INPUT]
    output_graph = graphs[PAGE_OUTPUT]
    created = 0

    if trigger == "if":
        driver = input_graph.add_node(catalog, "mod.period", 40.0, top)
        driver.params["period_ms"] = MIN_PERIOD_MS
        cond = input_graph.add_node(catalog, "expr.free", 300.0, top)
        cond.params["expr"] = expr.normalize(str(arg or ""))
        cond.alias = f"判断体 {name}" if name else "判断体"
        branch = input_graph.add_node(catalog, "flow.branch", 560.0, top)
        _force_connect(input_graph, catalog, driver, 0, branch, -1)
        _force_connect(input_graph, catalog, cond, 0, branch, 0)
        exec_src, exec_pin = branch, 0
        value_src = branch
        created += 3
    elif trigger in ("period", "change"):
        def_key = f"mod.{trigger}"
        if not catalog.known(def_key):
            return 0
        driver = input_graph.add_node(catalog, def_key, 40.0, top)
        if trigger == "period":
            driver.params["period_ms"] = max(MIN_PERIOD_MS, as_float(arg))
        else:
            guard = input_graph.add_node(catalog, "var.temp_read", 40.0, top)
            guard.params["name"] = str(arg or "")
            _force_connect(input_graph, catalog, guard, 0, driver, 0)
            created += 1
        if name:
            driver.alias = name
        exec_src, exec_pin = driver, 0
        value_src = driver
        created += 1
    else:
        return 0

    for index, act in enumerate(card.get("actions") or []):
        if not isinstance(act, dict):
            continue
        direction = str(act.get("dir") or "").strip()
        param = str(act.get("param") or "").strip()
        var = str(act.get("var") or "").strip()
        row_y = top + index * 96.0
        if direction == "in":
            write_key = f"core.write.{param}"
            if not param or not catalog.known(write_key):
                continue
            sink = input_graph.add_node(catalog, write_key, 860.0, row_y)
            sink.params["key"] = param
            reader = input_graph.add_node(catalog, "var.temp_read", 600.0, row_y)
            reader.params["name"] = var
            _force_connect(input_graph, catalog, exec_src, exec_pin, sink, -1)
            _force_connect(input_graph, catalog, reader, 0, sink, 0)
            created += 2
        elif direction == "out":
            read_key = f"core.read.{param}"
            if not param or not catalog.known(read_key):
                continue
            reader = output_graph.add_node(catalog, read_key, 40.0, row_y)
            reader.params["key"] = param
            writer = output_graph.add_node(catalog, "var.temp_write", 340.0, row_y)
            writer.params["name"] = var
            _force_connect(output_graph, catalog, reader, 0, writer, 0)
            out_name = str(act.get("name") or "").strip()
            if out_name:
                write_key = f"mod.write.{mid}.{out_name}"
                if catalog.known(write_key):
                    mod_write = output_graph.add_node(catalog, write_key, 660.0, row_y)
                    mod_write.params["module"] = mid
                    mod_write.params["name"] = out_name
                    _force_connect(output_graph, catalog, writer, 0, mod_write, 0)
                    created += 1
            created += 2
    return created


def _force_connect(graph: FlowGraph, catalog: Catalog, src: FlowNode, src_index: int,
                   dst: FlowNode, dst_index: int) -> FlowWire | None:
    wire, _why = graph.connect(catalog, src, src_index, dst, dst_index)
    if wire is not None:
        return wire
    st = graph.out_type(catalog, src, src_index) if src_index >= 0 else EXEC
    if src_index < 0 or dst_index < 0:
        wire = FlowWire(graph.new_id("w"), (src.id, src_index),
                        (dst.id, dst_index), EXEC)
        graph.wires.append(wire)
        return wire
    if st == ANY or compatible(st, graph.in_type(catalog, dst, dst_index)):
        wire = FlowWire(graph.new_id("w"), (src.id, src_index),
                        (dst.id, dst_index), st)
        graph.wires.append(wire)
        return wire
    return None

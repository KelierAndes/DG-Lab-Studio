
from __future__ import annotations

import threading
import time
from collections import deque

from dglab import expr
from dglab.official_waveforms import COYOTE_WAVEFORMS, CoyoteWaveform
from dglab.official_waveforms_ovc import OVC_WAVEFORMS, OvcWaveform
from dglab.state import EngineState, family_of
from dglab.waves import CONTINUOUS, PULSE_STREAM, SILENT

FAMILIES = ("COYOTE", "OVC", "BMTR")
FAMILY_LABELS = {"COYOTE": "郊狼 (电刺激)", "OVC": "负鼠 (振动)", "BMTR": "灵猫 (气压)"}
FAMILY_SYMBOLS = {"COYOTE": "Remote", "OVC": "CellPhone", "BMTR": "Target"}

OVC_BUTTON_BITS = [
    (0, "SEL_1"), (1, "SEL_2"), (2, "HOME"),
    (8, "Up"), (9, "Down"), (10, "Left"), (11, "Right"),
    (12, "B"), (13, "A"), (14, "G"), (15, "D"),
]
# 内置动作（与 Engine._OVC_BUTTON_ACTIONS 一一对应）；模块动作（如 OSC）由
# button_actions(engine) 动态追加，随模块装卸出现与消失。
BUTTON_ACTIONS = [
    ("none", "无"),
    ("a_strength_up", "A 通道强度 +10"), ("a_strength_down", "A 通道强度 -10"),
    ("a_strength_zero", "A 通道强度 归0"),
    ("a_wave_up", "A 切换上一个波形"), ("a_wave_down", "A 切换下一个波形"),
    ("b_strength_up", "B 通道强度 +10"), ("b_strength_down", "B 通道强度 -10"),
    ("b_strength_zero", "B 通道强度 归0"),
    ("b_wave_up", "B 切换上一个波形"), ("b_wave_down", "B 切换下一个波形"),
    ("fire", "持续开火 (按住，双通道)"),
    ("fire_a", "持续开火 A (按住，仅 A 通道)"),
    ("fire_b", "持续开火 B (按住，仅 B 通道)"),
    ("estop", "急停"),
]
KEY_BINDING_ACTION = ("key", "模拟键盘按键…")
BUTTON_ACTION_LABELS = dict(BUTTON_ACTIONS)


def button_actions(engine) -> list[tuple[str, str]]:
    """绑定选择框的全部动作项：内置 + 键盘注入 + 已加载模块注册的动作。"""
    items = list(BUTTON_ACTIONS)
    items.append(KEY_BINDING_ACTION)
    try:
        for action in engine.modules.button_actions():
            items.append((action.key, action.label))
    except Exception:
        pass
    return items


def button_action_labels(engine) -> dict[str, str]:
    return dict(button_actions(engine))

BACKEND_LABELS = {
    "none": "未连接",
    "v4": "Socket V4",
    "v3": "Socket V3",
    "ble": "蓝牙直连",
}

EDGE_STATES = {0: "停止", 1: "刺激", 2: "冷静计时", 3: "冷静判定", 4: "允许高潮"}

PRESSURE_MIN_KPA = 0.0
PRESSURE_MAX_KPA = 60.0
PRESSURE_WINDOW_S = 60.0
PRESSURE_COLORS = ("#3b82d0", "#d64541", "#48aa60", "#a05ac8")

LOG_LEVELS = ("全部", "调试", "信息", "警告", "错误")
LEVEL_LABELS = {"debug": "调试", "info": "信息", "warn": "警告", "error": "错误"}

def wave_items(family: str = "COYOTE") -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = [("静默 (无输出)", SILENT)]
    if family == "OVC":
        table, enum_cls = OVC_WAVEFORMS, OvcWaveform
    else:
        table, enum_cls = COYOTE_WAVEFORMS, CoyoteWaveform
    for wave in enum_cls:
        label = table[wave].get("label", {})
        cn = label.get("cn") or wave.value
        items.append((f"{cn} ({wave.value})", wave.value))
    items.append(("持续 (Continuous)", CONTINUOUS))
    items.append(("外部脉冲流 (PULSE_STREAM)", PULSE_STREAM))
    return items

def wave_label(value: str, family: str = "COYOTE") -> str:
    for label, item in wave_items(family):
        if item == value:
            return label
    return value or SILENT

def classify_log(msg: str) -> str:
    if any(k in msg for k in ("失败", "错误", "异常", "Traceback")):
        return "error"
    if any(k in msg for k in ("警告", "重试", "超时", "丢包", "限幅")):
        return "warn"
    if msg.startswith(("<", ">>", "{")) or " frame" in msg.lower():
        return "debug"
    return "info"

class LogBuffer:

    def __init__(self, maxlen: int = 800):
        self._items: deque[tuple[float, str, str]] = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self.version = 0
        self.mirror = None

    def append(self, msg: str, *, from_engine: bool = False) -> None:
        with self._lock:
            self._items.append((time.time(), classify_log(msg), msg))
            self.version += 1
        if not from_engine and self.mirror is not None:
            try:
                self.mirror(msg)
            except Exception:
                pass

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self.version += 1

    def snapshot(self) -> list[tuple[float, str, str]]:
        with self._lock:
            return list(self._items)

    def filtered(self, level: str = "全部", keyword: str = "", limit: int = 300):
        lines = self.snapshot()
        if level != "全部":
            key = {v: k for k, v in LEVEL_LABELS.items()}.get(level, "")
            lines = [line for line in lines if line[1] == key]
        kw = keyword.strip().lower()
        if kw:
            lines = [line for line in lines if kw in line[2].lower()]
        return list(reversed(lines[-limit:]))

LED_OPTIONS = (
    (0x00, "熄灭", "#9AA0A6"),
    (0x01, "黄色", "#F2C94C"),
    (0x02, "红色", "#EB5757"),
    (0x03, "紫色", "#9B51E0"),
    (0x04, "蓝色", "#2F80ED"),
    (0x05, "青色", "#27C4D3"),
    (0x06, "绿色", "#27AE60"),
)


def round_step10(value: int) -> int:
    value = int(value)
    return max(10, (value + 5) // 10 * 10)


def clamp_ovc_strength(value: int, *, allow_zero: bool = False) -> int:
    value = int(value)
    if allow_zero and value <= 0:
        return 0
    return min(200, round_step10(value))


def new_history() -> deque:
    return deque(maxlen=900)

def slot_status(slot) -> str:
    return "online"

def battery_text(slot) -> str:
    return f"{slot.battery}%" if slot.battery is not None else "—"

def output_row(slot, channel: str) -> dict:
    limit = slot.strength_limit.get(channel, 200) or 200
    value = slot.strength.get(channel, 0)
    return {
        "channel": channel,
        "value": value,
        "limit": limit,
        "percent": max(0, min(100, value / limit * 100)),
    }

def osc_value_rows(state: EngineState) -> list[dict]:
    rows: list[dict] = []
    for sid in sorted(state.slots):
        slot = state.slots[sid]
        family = family_of(slot.type)
        if family == "BMTR":
            pressure = slot.pressure if slot.pressure is not None else 0.0
            rows.append({
                "address": f"…{family}Pressure", "vtype": "float",
                "value": f"{pressure:.2f} kPa",
                "percent": max(0, min(100, pressure / PRESSURE_MAX_KPA * 100)),
            })
            edge = slot.edge_state if slot.edge_state is not None else 0
            rows.append({
                "address": f"…{family}EdgeState", "vtype": "int32",
                "value": EDGE_STATES.get(edge, str(edge)),
                "percent": int(edge / 4 * 100),
            })
        else:
            for ch in ("A", "B"):
                limit = slot.strength_limit.get(ch, 200) or 200
                value = slot.strength.get(ch, 0)
                rows.append({
                    "address": f"…{family}Strength{ch}", "vtype": "float",
                    "value": f"{value}/{limit}",
                    "percent": max(0, min(100, value / limit * 100)),
                })
        if slot.battery is not None:
            rows.append({
                "address": f"…Battery", "vtype": "int32",
                "value": f"{slot.battery}%", "percent": slot.battery,
            })
    return rows

def osc_probe_card(engine) -> dict:
    osc = engine.osc
    running = osc is not None and getattr(osc, "_running", False)
    label = "OSC 探测"
    if not running:
        return {"value": "已停止", "unit": "", "label": label, "symbol": "Sync",
                "accent": False, "detail": ["桥接未运行", "联动页可开启"]}
    last = getattr(osc, "last_rx", None)
    in_port = 9001
    try:
        in_port = int(engine.modules.settings_for("osc_bridge").get("in_port", 9001))
    except Exception:
        pass
    if last is not None and time.monotonic() - last <= 30.0:
        age = max(0, time.monotonic() - last)
        count = getattr(osc, "rx_count", 0) or 0
        return {"value": "已连接", "unit": "", "label": label, "symbol": "Contact",
                "accent": True,
                "detail": [f"收到 {count} 包", f"{age:.0f} 秒前有数据"]}
    return {"value": "无数据", "unit": "", "label": label, "symbol": "Contact",
            "accent": False,
            "detail": [f"监听 :{in_port}", "未收到数据"]}


def _osc_module_active(engine) -> bool:
    """OSC 桥是否以模块通道形式在列（运行中且有映射行）——此时老的
    「VRChat OSC 输入/输出」基础条目与之是同一条通道，不再单列。"""
    try:
        for module_id, _name, eng, runtime in module_engines(engine):
            if module_id == "osc_bridge" \
                    and getattr(runtime, "_running", False) \
                    and (eng.mappings or eng.outputs):
                return True
    except Exception:
        pass
    return False


def _osc_bridge_running(engine) -> bool:
    try:
        for module_id, _name, _eng, runtime in module_engines(engine):
            if module_id == "osc_bridge" \
                    and getattr(runtime, "_running", False):
                return True
    except Exception:
        pass
    return False


def _osc_module_installed(engine) -> bool:
    """OSC 模块是否已安装（启用）——卸载后概览不再显示其基础条目。"""
    try:
        return any(m["id"] == "osc_bridge" and m.get("enabled")
                   for m in engine.modules.list_modules())
    except Exception:
        return False


def link_counts(engine, state: EngineState) -> dict:
    """输入/输出链路计数与明细。

    输入链路：控制与遥测进入应用的路径（OSC 输入、负鼠按键绑定、灵猫传感器、
    各联动模块的「模块 → 核心」映射通道）。
    输出链路：应用向外下发数据的路径（OSC 输出、每台已接入输出设备、
    各联动模块的「核心 → 模块」映射通道）。
    """
    osc_module = _osc_module_active(engine)
    osc_on = _osc_bridge_running(engine) and not osc_module
    inputs: list[tuple[str, str]] = []
    outputs: list[tuple[str, str]] = []
    if osc_on:
        inputs.append(("VRChat OSC 输入", "头像参数 → 设备控制"))
        outputs.append(("VRChat OSC 输出", "设备数值 → 头像参数"))
    try:
        bindings = engine.ovc_bindings()
        profile = engine.config.get("ble", {}).get("ovc_profile", "")
    except Exception:
        bindings, profile = {}, ""
    bound = sum(1 for v in bindings.values() if v and v != "none")
    if bound:
        inputs.append(("负鼠物理按键", f"配置 {profile or '默认'} · {bound} 个绑定"))
    for sid in sorted(state.slots):
        slot = state.slots[sid]
        name = slot.name or slot.type or sid
        if family_of(slot.type) == "BMTR":
            inputs.append((f"{name} 传感", "气压 / 边缘状态遥测"))
        elif slot.is_output_device:
            outputs.append((name, "强度 / 波形下发"))
    for _module_id, module_name, eng, _rt in module_engines(engine):
        if eng.mappings:
            inputs.append((f"{module_name} · 模块→核心",
                           f"输入映射 {len(eng.mappings)} 条"))
        if eng.outputs:
            outputs.append((f"{module_name} · 核心→模块",
                            f"输出映射 {len(eng.outputs)} 条"))
    return {"input": inputs, "output": outputs}


def module_engines(engine) -> list[tuple[str, str, object, object]]:
    """运行中联动模块的映射引擎 ``(module_id, 显示名, MappingEngine, runtime)``。

    模块实例以 bridge（OSC）或 server（游戏数据类）属性承载共享映射引擎；
    runtime 用于探活（其 last_rx / _last_rx 记录对端最近通信时间）。
    """
    out: list[tuple[str, str, object, object]] = []
    try:
        metas = engine.modules.list_modules()
    except Exception:
        return out
    for meta in metas:
        try:
            inst = engine.modules.instance(meta["id"])
        except Exception:
            inst = None
        if inst is None:
            continue
        runtime = getattr(inst, "bridge", None) or getattr(inst, "server", None)
        eng = getattr(runtime, "engine", None) if runtime is not None else None
        if eng is not None:
            out.append((str(meta["id"]), str(meta["name"]), eng, runtime))
    return out


def _runtime_fresh(runtime) -> bool:
    """对端 5 秒内有过通信（OSC 收包 / 游戏 POST）视为数据流动。"""
    last = getattr(runtime, "last_rx", None)
    if last is None:
        last = getattr(runtime, "_last_rx", None)
    return last is not None and (time.monotonic() - last) < 5.0


def module_channel_rows(engine) -> list[dict]:
    """联动模块双向通道行：模块→核心（输入）与 核心→模块（输出）各一条。

    模块→核心行随模块运行而存在（0 条映射=「未配置映射」）；OSC 例外——
    无映射行时由「VRChat OSC 输入」基础条目表达，不重复列。核心→模块行
    仅在配置了输出行时出现。探活以**对端实际通信**为准：表达式错误 → 异常；
    对端 5 秒内有收发 → 数据流动中 / 回传中；否则（对端未启动 / 未发数据）
    → 等待，即使引擎已按缺省值算出输出也算未建立回传。
    """
    rows: list[dict] = []
    for module_id, module_name, eng, runtime in module_engines(engine):
        errors = getattr(eng, "errors", {}) or {}
        out_errors = getattr(eng, "out_errors", {}) or {}
        fresh = _runtime_fresh(runtime)
        mappings = len(getattr(eng, "mappings", {}) or {})
        outs = len(getattr(eng, "outputs", {}) or {})
        if module_id != "osc_bridge" or mappings:
            detail = next(iter(errors.values()), "")
            rows.append({"module": module_name, "direction": "模块→核心",
                         "count": mappings,
                         "probe": "异常" if errors else
                                  ("数据流动中" if fresh else
                                   ("未配置映射" if not mappings
                                    else "等待数据")),
                         "probe_detail": detail,
                         "probe_ok": False if errors else
                                     (True if fresh else None)})
        if outs:
            detail = next(iter(out_errors.values()), "")
            rows.append({"module": module_name, "direction": "核心→模块",
                         "count": outs,
                         "probe": "异常" if out_errors else
                                  ("回传中" if fresh else "等待回传"),
                         "probe_detail": detail,
                         "probe_ok": False if out_errors else
                                     (True if fresh else None)})
    return rows


def _stat_card(value: str, unit: str, label: str, *, symbol: str, accent: bool,
               trend: str = "", detail: list[str] | None = None) -> dict:
    card = {"value": value, "unit": unit, "label": label, "symbol": symbol,
            "accent": accent, "trend": trend}
    if detail:
        card["detail"] = detail
    return card


def stats(engine, log_buffer: LogBuffer) -> list[dict]:
    state = engine.get_state()
    devices = state.slots
    outputs = sum(1 for s in devices.values() if s.is_output_device)

    in_rows = input_channel_rows(engine, state)
    module_rows = module_channel_rows(engine)
    mod_in = sum(1 for r in module_rows if r["direction"] == "模块→核心")
    mod_out = sum(1 for r in module_rows if r["direction"] == "核心→模块")
    in_on = sum(1 for r in in_rows if r.get("enabled")) + mod_in
    in_total = len(in_rows) + mod_in
    out_on = len(output_channel_rows(state)) + mod_out
    out_total = out_on

    return [
        _stat_card(str(len(devices)), "台", "已连接设备", symbol="CellPhone",
                   accent=True,
                   trend=BACKEND_LABELS.get(engine.backend_kind, engine.backend_kind)),
        _stat_card(str(outputs), f"/ {max(len(devices), outputs)}", "输出设备",
                   symbol="Remote", accent=False,
                   trend=f"{len(devices) - outputs} 台传感器"),
        _stat_card(str(in_on), "条", "已启用输入链路",
                   symbol="Download", accent=bool(in_on),
                   trend=f"共 {in_total} 条"),
        _stat_card(str(out_on), "条", "已启用输出链路",
                   symbol="Upload", accent=bool(out_on),
                   trend=f"共 {out_total} 条"),
    ]


def module_data_sig(engine) -> tuple:
    """联动模块结构签名（生命周期 + signals/out_values 键集合）。

    供仪表盘 tick 判断是否需要整页重建：模块装卸启停、或收到过的新参数
    名/回传字段名集合变化才触发。**不含数值**——数值变化由数据值卡的
    live 行更新覆盖，否则周期事件流（50ms 写 out_values）会造成高频
    全页重建。start/stop 不发 modules_changed 事件，靠本签名感知。
    """
    sig: list[tuple] = []
    try:
        for meta in engine.modules.list_modules():
            sig.append(("m", str(meta["id"]), bool(meta.get("enabled")),
                        bool(meta.get("loaded")), bool(meta.get("running"))))
    except Exception:
        pass
    for module_id, _name, eng, _rt in module_engines(engine):
        try:
            sig.append(("s", tuple(sorted(getattr(eng, "signals", {}) or {}))))
            sig.append(("o", tuple(sorted(str(k)
                                          for k in (getattr(eng, "out_values",
                                                              {}) or {})))))
        except Exception:
            continue
    return tuple(sig)


def input_channel_rows(engine, state: EngineState) -> list[dict]:
    """输入通道清单（含未启用的，界面据 enabled 显示状态胶囊）。"""
    # OSC 桥运行且有映射行时由模块通道行统一表达（同一条通道，不重复列）；
    # 模块已安装但未运行时显示「未启用」提示行；卸载后条目整体消失。
    show_osc_entry = _osc_module_installed(engine) and not _osc_module_active(engine)
    osc_on = _osc_bridge_running(engine)
    rows = []
    if show_osc_entry:
        rows.append({
            "name": "VRChat OSC 输入",
            "detail": "头像参数 → 首个同类型设备（强度/波形/开火/急停）",
            "enabled": osc_on,
            "hint": "" if osc_on else "在模块页或联动页开启 VRChat OSC 联动",
        })
    try:
        bindings = engine.ovc_bindings()
        profile = engine.config.get("ble", {}).get("ovc_profile", "")
    except Exception:
        bindings, profile = {}, ""
    bound = sum(1 for v in bindings.values() if v and v != "none")
    rows.append({
        "name": "负鼠物理按键",
        "detail": (f"配置 {profile or '默认'} · {bound} 个绑定生效"
                   if bound else "当前配置无绑定"),
        "enabled": bound > 0,
        "hint": "" if bound else "在控制页负鼠卡片绑定按键动作",
    })
    for sid in sorted(state.slots):
        slot = state.slots[sid]
        if family_of(slot.type) == "BMTR":
            rows.append({
                "name": f"{slot.name or slot.type or sid} 传感器",
                "detail": "气压 / 边缘状态遥测（输入数据源）",
                "enabled": True, "hint": "",
            })
    running = {module_id for module_id, _n, _e, _rt in module_engines(engine)}
    for meta in _linkage_modules(engine):
        # OSC 由上方专属条目表达；运行中模块由模块通道行表达；未安装不显示
        if (meta["id"] == "osc_bridge" or meta["id"] in running
                or not meta.get("enabled")):
            continue
        rows.append({
            "name": str(meta["name"]),
            "detail": "联动模块输入通道（模块 → 核心）",
            "enabled": False,
            "hint": "在模块页或联动页启动该模块",
        })
    return rows


def _linkage_modules(engine) -> list[dict]:
    """声明了配置的联动模块（与联动页卡片同口径；config_init 无 config
    声明自然排除，strength_logger 等非联动模块同样排除）。"""
    try:
        return [m for m in engine.modules.list_modules() if m.get("config")]
    except Exception:
        return []


def channel_alive_text(status: int) -> str:
    if status in (0, 2):
        return "正常"
    if status == 1:
        return "异常"
    return f"状态 {status}"


def output_channel_rows(state: EngineState) -> list[dict]:
    """输出通道行：每台输出设备每通道一条，含探活（channel_status）。"""
    rows: list[dict] = []
    for sid in sorted(state.slots):
        slot = state.slots[sid]
        if not slot.is_output_device:
            continue
        name = slot.name or slot.type or sid
        for ch in ("A", "B"):
            out = output_row(slot, ch)
            status = int(slot.channel_status.get(ch, 0))
            rows.append({
                "device": name, "channel": ch,
                "value": out["value"], "limit": out["limit"],
                "percent": out["percent"],
                "alive": status in (0, 2),
                "alive_text": channel_alive_text(status),
            })
    return rows


def _input_value_text(value) -> str:
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, float):
        return f"{value:.2f}".rstrip("0").rstrip(".")
    return str(value)


def _used_stream_vars(engine, module_id: str) -> set[str]:
    """事件流已建立引用的变量集：动作 var/param、变更触发变量、
    if 判断体与临时变量表达式中的 {变量}、临时变量名本身。

    主页参数监控据此过滤——模块收到的原始参数只有被数据流引用才上表，
    未建立流的无名参数不再出现。
    """
    used: set[str] = set()
    try:
        cfg = engine.modules.settings_for(module_id)
    except Exception:
        return used
    for card in (cfg.get("events") or []):
        if not isinstance(card, dict):
            continue
        for act in (card.get("actions") or []):
            if isinstance(act, dict):
                if act.get("var"):
                    used.add(str(act["var"]))
                if act.get("param"):
                    used.add(str(act["param"]))
        trigger = str(card.get("trigger") or "")
        arg = card.get("arg")
        if trigger == "change" and arg:
            used.add(str(arg))
        if trigger == "if" and arg:
            used |= set(expr.variables(str(arg)))
    for row in (cfg.get("temps") or []):
        if isinstance(row, dict):
            if row.get("name"):
                used.add(str(row["name"]))
            used |= set(expr.variables(str(row.get("expr") or "")))
    return used


def input_value_rows(engine, state: EngineState) -> list[dict]:
    """输入数据值：运行中模块被数据流引用的输入信号 + 临时变量 + 灵猫
    传感器遥测。

    模块收到的原始参数只有被事件流/临时变量引用才上表（未建立流的参数
    不出现）；临时变量本身即已建立的流，全量显示。
    """
    rows: list[dict] = []
    for module_id, module_name, eng, _rt in module_engines(engine):
        used = _used_stream_vars(engine, module_id)
        for name in sorted(getattr(eng, "signals", {}) or {}):
            if name not in used:
                continue
            rows.append({"name": name, "kind": module_name,
                         "value": _input_value_text(eng.signals[name]),
                         "age": "实时"})
        for name in sorted(getattr(eng, "temps", {}) or {}):
            rows.append({"name": name, "kind": f"{module_name} · 临时变量",
                         "value": _input_value_text(eng.temps[name]),
                         "age": "实时"})
    for sid in sorted(getattr(state, "slots", {}) or {}):
        slot = state.slots[sid]
        if family_of(slot.type) != "BMTR":
            continue
        pressure = slot.pressure if slot.pressure is not None else 0.0
        rows.append({"name": f"{slot.name or slot.type or sid} Pressure",
                     "kind": "传感器", "value": f"{pressure:.2f} kPa", "age": "实时"})
        edge = slot.edge_state if slot.edge_state is not None else 0
        rows.append({"name": f"{slot.name or slot.type or sid} EdgeState",
                     "kind": "传感器", "value": EDGE_STATES.get(edge, str(edge)),
                     "age": "实时"})
    return rows


def module_output_value_rows(engine) -> list[dict]:
    """输出数据值的模块段：各运行中模块回传字段（核心 → 模块）的实时值。"""
    rows: list[dict] = []
    for _module_id, module_name, eng, _rt in module_engines(engine):
        for name in sorted(getattr(eng, "out_values", {}) or {}):
            spec = next((o for o in (eng.outputs or [])
                         if str(o.get("name")) == name), None)
            rows.append({"name": name, "kind": module_name,
                         "value": _input_value_text(eng.out_values[name]),
                         "age": str((spec or {}).get("type") or "")})
    return rows


def output_value_rows(engine, state: EngineState) -> list[dict]:
    """输出数据值：每台输出设备各通道的当前强度与波形。"""
    try:
        waves = engine.wave_selection()
    except Exception:
        waves = {"A": SILENT, "B": SILENT}
    rows: list[dict] = []
    for sid in sorted(state.slots):
        slot = state.slots[sid]
        if not slot.is_output_device:
            continue
        name = slot.name or slot.type or sid
        for ch in ("A", "B"):
            out = output_row(slot, ch)
            rows.append({
                "name": f"{name} · {ch}",
                "value": f"{out['value']}/{out['limit']}",
                "percent": out["percent"],
                "wave": wave_label(waves.get(ch, ""), family_of(slot.type)),
            })
    return rows

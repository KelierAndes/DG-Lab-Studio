"""核心可操作参数目录：联动可读写的一切参数都由核心定义，参数名固定不可改。

* 输入参数（模块 → 核心 → 设备）：郊狼 / 负鼠的通道强度、波形选择、波形步进、
  瞬时脉冲、开火以及全局急停，参数 id 即 ``in_*`` / ``in_ovc_*``；
* 输出参数（核心 → 模块）：设备实时状态信号，参数 id 为 ``家族.信号`` 或
  ``家族.设备序号.信号``（如 ``COYOTE.StrengthA``、``COYOTE.2.Battery``），
  外加全局 ``Action``（App 按键反馈）。

联动模块的映射表以这些参数 id 为锚点：核心参数名不可更改，模块一侧用于
接收的字段名（头像参数名、游戏侧字段名）可由用户自由重命名。
派发执行器由本模块统一构造，OSC 与游戏数据类模块共用同一套语义。
"""

from __future__ import annotations

import time
from typing import Any, Callable

from dglab.waves import wave_order

# 脉冲流推帧节流下限（秒）：设备按 100ms/帧消费，事件流周期再短也最多
# 10 帧/秒，避免推入快于消费造成播放队列积压（延迟累积）
PULSE_PUSH_MIN_INTERVAL_S = 0.1

__all__ = [
    "core_inputs", "input_spec", "input_specs", "input_ranges",
    "input_limit_signal",
    "output_signals", "output_specs", "output_spec", "output_key",
    "label_of", "family_label", "build_dispatchers",
    "device_state_values", "core_aliases", "core_alias_values",
    "OUTPUT_SIGNALS", "ACTION_OUTPUT", "PULSE_PUSH_MIN_INTERVAL_S",
]

_FAMILY_LABELS = {"COYOTE": "郊狼", "OVC": "负鼠", "BMTR": "灵猫"}

# 输入参数按 (家族, id 前缀) 展开；BMTR 暂无输入参数
_INPUT_FAMILIES = (("COYOTE", "in_"), ("OVC", "in_ovc_"))


def family_label(family: str) -> str:
    return _FAMILY_LABELS.get(str(family or "").upper(),
                              str(family or "设备"))


def core_inputs() -> list[dict[str, Any]]:
    """核心输入参数表（顺序即界面下拉顺序）。

    项字段：``key`` 参数 id、``label`` 固定名称、``type`` Int/Bool、
    ``range`` 表达式结果钳制范围、``family`` / ``channel``、``action`` 派发类型。
    """
    specs: list[dict[str, Any]] = []
    for family, prefix in _INPUT_FAMILIES:
        zh = family_label(family)
        last_wave = max(1, len(wave_order(family)) - 1)
        for ch in ("A", "B"):
            low = ch.lower()
            base = {"family": family, "channel": ch,
                    "group": f"{zh}通道"}
            specs.append({**base, "key": f"{prefix}strength_{low}",
                          "label": f"{zh}通道 {ch} 强度", "type": "Int",
                          "range": (0, 200), "action": "strength",
                          "desc": "0-200 自动钳制"})
            specs.append({**base, "key": f"{prefix}wave_{low}",
                          "label": f"{zh}通道 {ch} 波形选择", "type": "Int",
                          "range": (0, last_wave), "action": "wave",
                          "desc": "按波形序号选择"})
            specs.append({**base, "key": f"{prefix}wave_step_{low}",
                          "label": f"{zh}通道 {ch} 波形步进", "type": "Int",
                          "range": (-1, 1), "action": "wave_step",
                          "desc": "非零触发 ±1 切换"})
        specs.append({"family": family, "channel": "", "group": f"{zh}通道",
                      "key": f"{prefix}fire", "label": f"{zh}开火",
                      "type": "Bool", "range": (0, 1), "action": "fire",
                      "desc": "非零起爆 / 归零停止并恢复（双通道）"})
        for ch in ("A", "B"):
            base_ch = {"family": family, "channel": ch, "group": f"{zh}通道"}
            specs.append({**base_ch, "key": f"{prefix}fire_{ch.lower()}",
                          "label": f"{zh}开火 {ch}",
                          "type": "Bool", "range": (0, 1), "action": "fire",
                          "desc": "仅本通道起爆 / 归零停止并恢复"})
            specs.append({**base_ch, "key": f"{prefix}pulse_{ch.lower()}",
                          "label": f"{zh}通道 {ch} 脉冲流频率", "type": "Int",
                          "range": (0, 1000), "action": "pulse",
                          "desc": "数值推入：0=静音帧，10-1000=脉冲频率"
                                  "（波形需选「外部脉冲流」，周期事件每拍推帧）"})
    specs.append({"family": "", "channel": "", "group": "全局",
                  "key": "in_emergency", "label": "急停（全部设备）",
                  "type": "Bool", "range": (0, 1), "action": "emergency",
                  "desc": "非零触发全部设备急停"})
    return specs


def input_specs() -> dict[str, dict[str, Any]]:
    """参数 id → 输入参数定义。"""
    return {spec["key"]: spec for spec in core_inputs()}


def input_spec(key: str) -> dict[str, Any] | None:
    return input_specs().get(str(key or ""))


def input_ranges() -> dict[str, tuple[int, int]]:
    return {spec["key"]: spec["range"] for spec in core_inputs()}


_LIMIT_CACHE: dict[str, str | None] = {}


def input_limit_signal(param_id: str) -> str | None:
    """强度输入参数 id → 目标设备通道上限信号的输出参数 id（其余返回 None）。

    ``in_coyote_strength_a`` → ``COYOTE.LimitA``：映射引擎据此把表达式结果
    按当前通道上限（App 滑杆上限 / 设备上报 intensityMax）动态钳制；
    上限信号取家族 1 号设备（派发器解析的正是该设备），非强度参数无上限语义。
    """
    key = str(param_id or "")
    if key in _LIMIT_CACHE:
        return _LIMIT_CACHE[key]
    spec = input_spec(key)
    out: str | None = None
    if spec is not None and str(spec.get("action")) == "strength":
        out = output_key(str(spec["family"]), 1, f"Limit{spec['channel']}")
    _LIMIT_CACHE[key] = out
    return out


# ---- 核心输出参数（设备实时状态） -------------------------------------------

# 家族 → 信号定义：signal 信号名、type 值类型、desc 说明、getter 从 Slot 取值
OUTPUT_SIGNALS: dict[str, tuple[dict, ...]] = {
    "COYOTE": (
        {"signal": "StrengthA", "type": "Int", "desc": "通道 A 当前强度",
         "getter": lambda slot: slot.strength.get("A", 0)},
        {"signal": "StrengthB", "type": "Int", "desc": "通道 B 当前强度",
         "getter": lambda slot: slot.strength.get("B", 0)},
        {"signal": "LimitA", "type": "Int", "desc": "通道 A 强度上限",
         "getter": lambda slot: slot.strength_limit.get("A", 200)},
        {"signal": "LimitB", "type": "Int", "desc": "通道 B 强度上限",
         "getter": lambda slot: slot.strength_limit.get("B", 200)},
        {"signal": "ChannelOK_A", "type": "Bool", "desc": "通道 A 探活",
         "getter": lambda slot: slot.channel_status.get("A", 0) in (0, 2)},
        {"signal": "ChannelOK_B", "type": "Bool", "desc": "通道 B 探活",
         "getter": lambda slot: slot.channel_status.get("B", 0) in (0, 2)},
        {"signal": "Battery", "type": "Int", "desc": "电量百分比",
         "getter": lambda slot: slot.battery or 0},
        {"signal": "Connected", "type": "Bool", "desc": "连接状态",
         "getter": None},
    ),
    "BMTR": (
        {"signal": "Pressure", "type": "Float", "desc": "气压 (kPa)",
         "getter": lambda slot: slot.pressure or 0.0},
        {"signal": "EdgeState", "type": "Int", "desc": "边缘状态 (0-4)",
         "getter": lambda slot: slot.edge_state or 0},
        {"signal": "Battery", "type": "Int", "desc": "电量百分比",
         "getter": lambda slot: slot.battery or 0},
        {"signal": "Connected", "type": "Bool", "desc": "连接状态",
         "getter": None},
    ),
}
OUTPUT_SIGNALS["OVC"] = OUTPUT_SIGNALS["COYOTE"]

# 全局输出参数：App 按键反馈（不依附具体设备）
ACTION_OUTPUT = {"key": "Action", "signal": "Action", "type": "Int",
                 "family": "", "index": 0, "desc": "App 按键反馈 0-9",
                 "label": "App 按键反馈", "getter": None}


def output_key(family: str, index: int, signal: str) -> str:
    """输出参数 id：1 号设备用 ``家族.信号``，其余带设备序号。"""
    return (f"{family}.{signal}" if index <= 1
            else f"{family}.{index}.{signal}")


def output_signals(family: str) -> tuple[dict, ...]:
    return OUTPUT_SIGNALS.get(str(family or "").upper(), ())


def output_specs(family: str, index: int = 1) -> list[dict[str, Any]]:
    """某家族某序号设备的输出参数定义（带 id 与固定名称）。"""
    zh = family_label(family)
    serial = "" if index <= 1 else f" {index} 号"
    out: list[dict[str, Any]] = []
    for item in output_signals(family):
        out.append({**item,
                    "key": output_key(family, index, item["signal"]),
                    "family": str(family).upper(), "index": index,
                    "label": f"{zh}{serial}{item['desc']}"})
    return out


_OUTPUT_CACHE: dict[str, dict[str, Any]] = {}


def output_spec(param_id: str) -> dict[str, Any] | None:
    """按输出参数 id 解析定义（未接入设备时也能取到类型与说明）。"""
    key = str(param_id or "")
    if key in _OUTPUT_CACHE:
        return _OUTPUT_CACHE[key]
    if key == "Action":
        _OUTPUT_CACHE[key] = ACTION_OUTPUT
        return ACTION_OUTPUT
    parts = key.split(".")
    if len(parts) < 2:
        return None
    family = parts[0].upper()
    index = int(parts[1]) if len(parts) > 2 and parts[1].isdigit() else 1
    signal = parts[-1]
    for spec in output_specs(family, index):
        if spec["signal"] == signal:
            _OUTPUT_CACHE[key] = spec
            return spec
    return None


def label_of(param_id: str) -> str:
    """核心参数 id → 固定名称（输入或输出命名空间）。"""
    spec = input_spec(param_id)
    if spec is not None:
        return str(spec["label"])
    spec = output_spec(param_id)
    if spec is not None:
        return str(spec.get("label") or spec["desc"])
    return str(param_id or "")


def device_state_values(state) -> dict[str, float]:
    """EngineState → 全部核心输出参数实时值（``家族.信号`` 为键）。"""
    vals: dict[str, float] = {}
    if state is None:
        return vals
    from dglab.state import family_of

    counters: dict[str, int] = {}
    paired = 1.0 if (getattr(state, "paired", False)
                     and getattr(state, "connected", False)) else 0.0
    for sid in sorted(getattr(state, "slots", {}) or {}):
        slot = state.slots[sid]
        family = str(family_of(slot.type))
        counters[family] = counters.get(family, 0) + 1
        for spec in output_signals(family):
            getter = spec.get("getter")
            value = paired if getter is None else _as_float(getter(slot))
            vals[output_key(family, counters[family], spec["signal"])] = value
    return vals


def core_aliases(values: dict[str, float], family: str) -> dict[str, float]:
    """``家族.信号`` 值表 → 1 号设备的短名别名（``Strength`` / ``max`` 等）。"""
    prefix = f"{str(family).upper()}."
    first = {key[len(prefix):]: value for key, value in values.items()
             if key.startswith(prefix) and key.count(".") == 1}
    out: dict[str, float] = {}
    if "StrengthA" in first:
        out.setdefault("Strength", first["StrengthA"])
    if "LimitA" in first:
        out.setdefault("Limit", first["LimitA"])
        out.setdefault("max", first["LimitA"])
    for signal in ("Battery", "Connected", "Pressure"):
        if signal in first:
            out.setdefault(signal, first[signal])
    return out


def core_alias_values(values: dict[str, float]) -> dict[str, float]:
    """全部家族的短名别名：``家族+短名``（如 COYOTEmax）全量给出，
    裸短名（``max`` / ``Strength`` / ``Pressure``…）按 郊狼 → 负鼠 → 灵猫
    顺序取首个有该信号的家族，供映射表达式跨设备直接引用。"""
    out: dict[str, float] = {}
    for family in ("COYOTE", "OVC", "BMTR"):
        for name, value in core_aliases(values, family).items():
            out.setdefault(f"{family}{name}", value)
            out.setdefault(name, value)
    return out


def _as_float(value) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


# ---- 统一派发 ---------------------------------------------------------------

def build_dispatchers(api, specs: list[dict] | None = None
                      ) -> dict[str, Callable[[int], None]]:
    """核心输入参数 → 执行器 ``fn(value:int)``。

    ``api`` 由模块适配，需提供：``run(coro)``、``resolve_slot(family)``、
    ``set_strength`` / ``set_wave`` / ``fire_start`` / ``fire_stop`` /
    ``push_pulse`` / ``emergency_stop`` / ``wave_order(family)`` /
    ``wave_selection()``。
    """
    out: dict[str, Callable[[int], None]] = {}
    for spec in (core_inputs() if specs is None else specs):
        out[spec["key"]] = _dispatcher(spec, api)
    return out


# 家族限定参数的派发目标哨兵：目标家族不在场时跳过本轮派发（不落到
# 其他设备——适配层的跨家族兜底对显式家族目标不生效）
_NO_TARGET = object()


def _dispatcher(spec: dict[str, Any], api) -> Callable[[int], None]:
    action = str(spec.get("action") or "")
    channel = str(spec.get("channel") or "A")
    family = str(spec.get("family") or "")
    edge = {"last": None}

    def slot():
        """解析目标设备；家族限定参数做**严格家族校验**。

        适配层可能带跨家族兜底（OSC 头像参数等场景）；核心参数 id 明确
        带家族时以家族为准——兜底解析到其他家族视为未命中，跳过派发
        （郊狼目标不再误触在场负鼠）。适配层未实现 slot_family 钩子时
        维持旧行为（无法校验则接受解析结果）。
        """
        try:
            sid = api.resolve_slot(family)
        except Exception:
            return None
        if sid is not None and family:
            fam_fn = getattr(api, "slot_family", None)
            if callable(fam_fn):
                try:
                    if str(fam_fn(sid) or "").upper() != family.upper():
                        return None
                except Exception:
                    pass
        return sid

    def target() -> str | None:
        """家族限定参数的派发目标：家族不在场返回哨兵跳过本轮派发。"""
        sid = slot()
        if family and sid is None:
            return _NO_TARGET
        return sid

    def changed(value: int) -> bool:
        """0↔非零边沿判定：引擎重载 / 首轮求值的重复派发不再重复动作。"""
        flag = _truthy(value)
        if edge["last"] is flag:
            return False
        edge["last"] = flag
        return True

    if action == "strength":
        def run(value: int) -> None:
            sid = target()
            if sid is _NO_TARGET:
                return
            api.run(api.set_strength(channel, _clamp(value, 0, 200),
                                     slot_id=sid))
        return run

    if action == "wave":
        def run(value: int) -> None:
            sid = target()
            if sid is _NO_TARGET:
                return
            order = api.wave_order(family)
            idx = _clamp(value, 0, max(0, len(order) - 1))
            api.run(api.set_wave(channel, order[idx], slot_id=sid))
        return run

    if action == "wave_step":
        def run(value: int) -> None:
            sid = target()
            if sid is _NO_TARGET:
                return
            step = _clamp(value, -1, 1)
            if step == 0:
                return
            order = api.wave_order(family)
            current = str((api.wave_selection() or {}).get(channel)
                          or order[0])
            idx = order.index(current) if current in order else 0
            name = order[(idx + (1 if step > 0 else -1)) % len(order)]
            api.run(api.set_wave(channel, name, slot_id=sid))
        return run

    if action == "fire":
        # 通道分离：带通道的 fire 参数只动本通道，家族级 fire 仍双通道
        fire_channel = str(spec.get("channel") or "").upper() or None

        def run(value: int) -> None:
            if not changed(value):
                return
            sid = target()
            if sid is _NO_TARGET:
                return
            if _truthy(value):
                api.run(api.fire_start(slot_id=sid, channel=fire_channel))
            else:
                api.run(api.fire_stop(slot_id=sid, channel=fire_channel))
        return run

    if action == "pulse":
        # 脉冲流数值推入：每次派发推一帧（周期事件每拍触发，非边沿动作），
        # 派发侧按 0.1s 节流防止周期短于帧时长造成队列积压；
        # 0 = 静音帧（电平 0，保留波形成形），10-1000 = 脉冲频率。
        # 电平默认 100；模块适配层提供 pulse_level(channel) 时跟随响度
        # （设备振动/波形包络随音频起伏，波形图出现高低变化）
        last_push = {"t": 0.0}

        def run(value: int) -> None:
            now = time.monotonic()
            if now - last_push["t"] < PULSE_PUSH_MIN_INTERVAL_S:
                return
            last_push["t"] = now
            sid = target()
            if sid is _NO_TARGET:
                return
            freq = _clamp(value, 0, 1000)
            if freq <= 0:
                api.run(api.push_pulse(channel, 10, level=0, slot_id=sid))
            else:
                level_fn = getattr(api, "pulse_level", None)
                level = 100
                if callable(level_fn):
                    try:
                        level = max(0, min(100, int(level_fn(channel))))
                    except Exception:
                        level = 100
                api.run(api.push_pulse(channel, max(10, freq), level=level,
                                       slot_id=sid))
        return run

    if action == "emergency":
        def run(value: int) -> None:
            if _truthy(value) and changed(value):
                api.run(api.emergency_stop())
        return run

    return lambda value: None


def _clamp(value, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return low


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    return False

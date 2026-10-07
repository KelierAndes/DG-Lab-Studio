"""共享映射引擎：模块参数信号空间 + 核心参数双向映射表 + 事件流。

OSC 与 Alice in Cradle 等联动模块共用 :class:`MappingEngine`。联动数据面
由三部分组成：

* 输入/输出映射表（存量兼容）：``核心输入参数 → 表达式`` 连续求值派发、
  ``模块侧参数名 → 表达式`` 供模块回传——联动页已改用事件流编辑，
  映射表仅保留引擎侧装载接口（外部模块代码仍会调用）；
* 临时变量表 ``名字 → 表达式``：每次 pump 按序求值（可引用信号、设备
  变量与先前的临时变量，自引用即累加器），结果并入值空间——**全部
  运算只发生在这里**；模块代码经 ``ctx.set_temp/get_temp`` 读写同一
  空间（宿主持有共享 dict，``attach_temps`` 注入引擎）；
* 事件流（事件小卡片）：``驱动事件 + 动作直列``，宿主节拍循环每
  50ms 调一次 :meth:`tick_event_cards` 驱动——周期更新（arg = 毫秒）、
  变量变更时（arg = 变量名，值变化即触发）、if 判断（arg = 判断体，
  条件表达式或 bool 变量名，上升沿触发一次）。动作不做运算：输入动作
  把变量当前值经钳制/Bool 归一后派发核心输入参数（同值不去重，触发即
  生效），输出动作把核心输出信号实时值写入临时变量。

值空间 = 设备状态变量（``device_vars`` 回调，弱引用实时取）∪ 输入信号
（同名时信号优先）∪ 临时变量（最低优先，不覆盖前两者）。未出现的变量按 0
处理。
"""

from __future__ import annotations

import time as _time
from typing import Any, Callable

from dglab import expr
from dglab.params import input_limit_signal, input_ranges, input_specs

__all__ = ["MappingEngine", "as_number", "bool_value", "signal_specs",
           "rows_to_map", "output_rows", "temp_rows", "event_cards"]

_BOOL_EPS = 1e-9
_TICK_INTERVAL = 0.05     # 事件流节拍（秒）：触发判定的最小粒度
_MIN_PERIOD_S = 0.05      # 周期事件 arg 下限（存储为毫秒，50ms）


def bool_value(raw: float) -> int:
    """Bool 归一：正值归 1（大于 1 的钳制为 1 → true），
    小于等于 0（含负值）归 0 → false；浮点噪声按 0 处理。"""
    return 1 if raw > _BOOL_EPS else 0


def signal_specs() -> dict[str, tuple[int, int]]:
    """核心输入参数 id → 表达式结果钳制范围。"""
    return input_ranges()


def rows_to_map(rows: Any) -> dict[str, str]:
    """输入映射表 → ``{核心输入参数 id: 表达式}``。

    兼容两种书写：``[{"param"/"target": id, "expr": 表达式}]`` 与
    ``{"id": 表达式}``；表达式为空即同名直传。
    """
    out: dict[str, str] = {}
    if isinstance(rows, dict):
        items = list(rows.items())
        pairs = [(str(key), str(value or "")) for key, value in items]
    else:
        pairs = []
        for entry in (rows or []):
            if not isinstance(entry, dict):
                continue
            key = str(entry.get("param") or entry.get("target") or "").strip()
            pairs.append((key, str(entry.get("expr") or "")))
    for key, text in pairs:
        if not key:
            continue
        src = expr.normalize(text)
        out[key] = src or _direct(key)
    return out


def _direct(param_id: str) -> str:
    """空表达式的直传形式：引用同名模块参数。"""
    return "{" + str(param_id).split(".")[-1] + "}"


def _channel_limit(target: str, vals: dict[str, float]) -> int | None:
    """强度参数的当前通道上限（实时值）；取不到返回 None，沿用静态范围。

    值空间优先取 ``家族.LimitX``（目标家族 1 号设备，与派发器解析的设备
    一致）；家族键缺失（跨家族兜底派发）时退回裸别名 ``LimitX``；
    上限 ≤0 视为未上报。上限随 App 滑杆 / 设备上报实时变化，模块按周期
    pump 重算即可跟随放开与收紧。
    """
    limit_key = input_limit_signal(target)
    if limit_key is None:
        return None
    channel = limit_key[-1]            # 上限信号 id 尾字符即通道（…LimitA → A）
    for key in (limit_key, f"Limit{channel}"):
        raw = vals.get(key)
        if raw is not None and raw > 0:
            return int(raw)
    return None


def output_rows(rows: Any) -> list[dict[str, Any]]:
    """输出映射表 → ``[{"name","expr","type"}]``（丢弃无名/无表达式项）。

    兼容 ``{"核心参数 id": 模块侧名}`` 的旧重命名写法（表达式取同名信号）。
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    entries: list[dict[str, Any]] = []
    if isinstance(rows, dict):
        for key, name in rows.items():
            entries.append({"param": str(key),
                            "name": str(name or "").strip(),
                            "expr": _direct(str(key))})
    else:
        for entry in (rows or []):
            if isinstance(entry, dict):
                entries.append(entry)
    for entry in entries:
        name = str(entry.get("name") or "").strip()
        if not name:
            name = str(entry.get("param") or entry.get("key") or "").strip()
        if not name:
            continue
        source = str(entry.get("param") or entry.get("key") or "").strip()
        text = expr.normalize(entry.get("expr") or "")
        if not text:
            text = _direct(source or name)
        if name in seen:
            continue
        seen.add(name)
        out.append({"name": name, "expr": text, "param": source,
                    "type": str(entry.get("type") or "Int")})
    return out


def temp_rows(rows: Any) -> list[dict[str, str]]:
    """临时变量表 → ``[{"name","expr"}]``（丢弃无名/无表达式项，归一化表达式）。"""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for entry in (rows or []):
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        text = expr.normalize(entry.get("expr") or "")
        if not name or not text or name in seen:
            continue
        seen.add(name)
        out.append({"name": name, "expr": text})
    return out


def event_cards(rows: Any) -> list[dict[str, Any]]:
    """事件流配置 → 规整事件小卡片列表（联动页存储形式直译）。

    卡片 = ``{"name","trigger","arg","actions"}``：
    * ``trigger``: ``"period"``（arg = 周期毫秒）｜``"change"``（arg = 变量名）｜
      ``"if"``（arg = 判断体：条件表达式或 bool 变量名）；
    * ``actions``: ``{"dir": "in"|"out", "param", "var"}``——输入把 ``var``
      当前值派发给核心输入参数 ``param``，输出把核心输出信号 ``param``
      的实时值写入临时变量 ``var``。动作内不做运算（运算只属于临时变量表）。
    """
    out: list[dict[str, Any]] = []
    for index, entry in enumerate(rows or []):
        if not isinstance(entry, dict):
            continue
        trigger = str(entry.get("trigger") or "").strip()
        if trigger not in ("period", "change", "if"):
            continue
        actions: list[dict[str, str]] = []
        for act in (entry.get("actions") or []):
            if not isinstance(act, dict):
                continue
            direction = str(act.get("dir") or "").strip()
            param = str(act.get("param") or "").strip()
            var = str(act.get("var") or "").strip()
            if direction not in ("in", "out") or not param or not var:
                continue
            action = {"dir": direction, "param": param, "var": var}
            if direction == "out":
                # 模块侧回传字段（写入引擎 out_values 供模块 API 读取）
                action["name"] = str(act.get("name") or "").strip()
                action["type"] = str(act.get("type") or "Int")
            actions.append(action)
        name = str(entry.get("name") or "").strip() or f"事件{index + 1}"
        arg = entry.get("arg")
        out.append({"name": name, "trigger": trigger,
                    "arg": arg, "actions": actions})
    return out


def as_number(value: Any) -> float | None:
    """OSC/JSON 值 → float；bool→1/0，非数值返回 None。"""
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


class MappingEngine:
    """核心参数双向映射的求值与去重派发。

    :param dispatch: ``fn(target_key, value:int)``，值变化时被调用。
    :param device_vars: ``fn() -> dict[str, float]`` 提供设备实时状态变量。
    :param ranges: 核心输入参数 id → (low, high)，结果取整后钳制。
    :param default_range: 未登记参数的钳制范围。
    """

    def __init__(self, dispatch: Callable[[str, int], None], *,
                 device_vars: Callable[[], dict[str, float]] | None = None,
                 ranges: dict[str, tuple[int, int]] | None = None,
                 default_range: tuple[int, int] = (0, 200)):
        self._dispatch = dispatch
        self._device_vars = device_vars or (lambda: {})
        self._ranges = dict(ranges or {})
        self._default_range = default_range
        self._bool_keys = {key for key, spec in input_specs().items()
                           if str(spec.get("type")) == "Bool"}
        # 脉冲流等周期参数：不做同值去重——每拍派发持续成流（设备按帧消费，
        # 恒定值也要持续推帧保持播放队列新鲜），而非边沿动作
        self._periodic_keys = {key for key, spec in input_specs().items()
                               if str(spec.get("action")) == "pulse"}
        self.signals: dict[str, float] = {}
        self.mappings: dict[str, str] = {}
        self.errors: dict[str, str] = {}
        self.last_values: dict[str, int] = {}
        self.outputs: list[dict[str, Any]] = []
        self.out_values: dict[str, Any] = {}
        self.out_errors: dict[str, str] = {}
        # 临时变量：默认私有空间，宿主可经 attach_temps 注入共享 dict
        # （模块 ctx.set_temp/get_temp 与配置表达式读写同一份）
        self.temps: dict[str, float] = {}
        self._temp_table: list[dict[str, str]] = []
        self._cards: list[dict[str, Any]] = []
        self._card_state: dict[int, dict[str, Any]] = {}
        # False = 只求值不派发（装载映射表首轮，避免启动即把设备写成 0）
        self.armed = True

    # ------------------------------------------------------------- 配置面

    def set_mappings(self, mappings: Any) -> None:
        """装载输入映射表：``{参数 id: 表达式}`` 字典或 ``[{"param","expr"}]`` 行。

        字典中空表达式条目忽略；行形式空表达式按「同名直传」补齐。
        """
        if isinstance(mappings, dict):
            self.mappings = {str(key): src
                             for key, src in ((str(k), expr.normalize(v))
                                              for k, v in (mappings or {}).items())
                             if src}
        else:
            self.mappings = rows_to_map(mappings)
        self.errors.clear()
        self.pump()

    def set_outputs(self, rows: Any) -> None:
        """装载输出映射表（``[{"name","expr","type"}]`` 或旧重命名字典）。"""
        self.outputs = output_rows(rows)
        self.out_errors.clear()
        self.pump()

    def attach_temps(self, shared: dict[str, float]) -> None:
        """注入宿主持有的临时变量共享空间（模块 ctx 读写与引擎求值同源）。"""
        if shared is not self.temps:
            shared.update(self.temps)
            self.temps = shared

    def set_temp_rows(self, rows: Any) -> None:
        """装载临时变量表（``[{"name","expr"}]``），装载即重算一轮。"""
        self._temp_table = temp_rows(rows)
        self.pump()

    def set_event_cards(self, rows: Any) -> None:
        """装载事件流卡片（``[{"name","trigger","arg","actions"}]``）。

        装载即重置触发状态（周期首拍即到期、change/if 重新采基线）。
        """
        self._cards = event_cards(rows)
        self._card_state = {}

    def has_events(self) -> bool:
        return any(card["actions"] for card in self._cards)

    def set_ranges(self, ranges: dict[str, tuple[int, int]]) -> None:
        self._ranges = dict(ranges or {})

    # ------------------------------------------------------------- 信号面

    def signal(self, name: str, value: Any) -> None:
        """记录一个模块参数信号（命名数值）并触发重算。"""
        num = as_number(value)
        if num is None:
            return
        if self.signals.get(name) == num:
            return
        self.signals[name] = num
        self.pump()

    def values(self) -> dict[str, float]:
        merged = dict(self._device_vars())
        merged.update(self.signals)
        # 临时变量优先级最低：不覆盖设备变量与模块信号
        for key, value in self.temps.items():
            merged.setdefault(key, value)
        return merged

    def _eval_temps(self, vals: dict[str, float]) -> None:
        """按序求值临时变量表：结果写回共享空间并并入本轮值空间。

        表达式基于「已并入先前临时变量的 vals」求值——自引用
        （``{count} + 1``）取上一轮值，天然构成累加器。
        """
        for row in self._temp_table:
            name = row["name"]
            try:
                value = expr.evaluate(row["expr"], vals)
            except expr.ExprError as exc:
                self.errors[f"temp:{name}"] = str(exc)
                continue
            self.errors.pop(f"temp:{name}", None)
            self.temps[name] = value
            vals[name] = value

    def pump(self) -> None:
        """重算两张映射表：临时变量先行（供后续表达式引用），输入按整数
        变化派发（强度按当前通道上限钳制），输出刷新实时值。"""
        if not self.mappings and not self.outputs and not self._temp_table:
            return
        vals = self.values()
        self._eval_temps(vals)
        for target, text in self.mappings.items():
            low, high = self._ranges.get(target, self._default_range)
            limit = _channel_limit(target, vals)
            if limit is not None:
                high = min(high, limit)
            try:
                if target in self._bool_keys:
                    # Bool 参数：正值归 1（>1 钳制），<=0（含负值）归 0，
                    # 不做四舍五入
                    value = bool_value(expr.evaluate(text, vals))
                else:
                    value = expr.eval_int(text, vals, low, high)
            except expr.ExprError as exc:
                self.errors[target] = str(exc)
                continue
            self.errors.pop(target, None)
            if target not in self._periodic_keys \
                    and self.last_values.get(target) == value:
                continue
            self.last_values[target] = value
            if not self.armed:
                continue
            try:
                self._dispatch(target, value)
            except Exception:      # 派发失败不拖垮引擎（模块自行记录）
                self.errors[target] = "派发失败"
        for row in self.outputs:
            name = row["name"]
            try:
                raw = expr.evaluate(row["expr"], vals)
            except expr.ExprError as exc:
                self.out_errors[name] = str(exc)
                continue
            self.out_errors.pop(name, None)
            self.out_values[name] = _typed(raw, row.get("type") or "Int")

    def fire_card(self, card: dict[str, Any]) -> int:
        """执行事件卡片的动作直列，返回实际派发/写入的动作数。

        输入动作：``var`` 当前值经钳制/Bool 归一后派发核心输入参数，
        **与派发目标上次值相同则跳过**——周期/变更触发的重复派发不再
        把设备钉在变量值上，App 按钮等手动控制的变化得以保留（与映射表
        pump 的同值去重语义一致）；脉冲流等周期参数除外：每拍持续推帧
        成流。输出动作：核心输出信号实时值写入临时变量。求值/派发失败
        记入 errors，不拖垮其余动作。
        """
        vals = self.values()
        fired = 0
        for index, action in enumerate(card["actions"]):
            tag = f"event:{card['name']}#{index}"
            try:
                if action["dir"] == "in":
                    value = self._clamp_param(action["param"],
                                              vals.get(action["var"], 0.0),
                                              vals)
                    if action["param"] not in self._periodic_keys \
                            and self.last_values.get(action["param"]) == value:
                        continue      # 同值去重（脉冲流等周期参数除外：每拍持续成流）
                    self.last_values[action["param"]] = value
                    if self.armed:
                        self._dispatch(action["param"], value)
                    fired += 1
                else:
                    raw = float(vals.get(action["param"], 0.0))
                    self.temps[action["var"]] = raw
                    name = str(action.get("name") or "")
                    if name:
                        # 模块回传字段：类型归一后写入 out_values，
                        # 模块 API（如 GET /data）与映射表时代同源
                        self.out_values[name] = _typed(
                            raw, str(action.get("type") or "Int"))
                    fired += 1
            except expr.ExprError as exc:
                self.errors[tag] = str(exc)
            except Exception:
                self.errors[tag] = "派发失败"
        return fired

    def _clamp_param(self, target: str, raw: float,
                     vals: dict[str, float]) -> int:
        """事件输入动作的取值归一：与映射表同语义（范围钳制 + 通道上限
        收紧 + Bool 参数归一）。"""
        low, high = self._ranges.get(target, self._default_range)
        limit = _channel_limit(target, vals)
        if limit is not None:
            high = min(high, limit)
        if target in self._bool_keys:
            return bool_value(raw)
        return expr.clamp_int(raw, low, high)

    def tick_event_cards(self, now: float | None = None) -> int:
        """事件流节拍：宿主循环按固定间隔调用，依驱动事件触发卡片。

        返回本轮触发执行的卡片数。触发状态按卡片下标记录，装载即重置。
        """
        now = _time.monotonic() if now is None else now
        if not self._cards:
            return 0
        fired = 0
        for index, card in enumerate(self._cards):
            state = self._card_state.setdefault(index, {})
            trigger = card["trigger"]
            if trigger == "period":
                period = max(_MIN_PERIOD_S, as_number(card["arg"]) / 1000.0
                             if as_number(card["arg"]) is not None
                             else _MIN_PERIOD_S)
                last = state.get("last_fire")
                if last is not None and now - last < period:
                    continue
                state["last_fire"] = now      # 装载后首拍立即到期
            elif trigger == "change":
                current = self.values().get(str(card["arg"] or ""), 0.0)
                if "last_val" not in state:
                    state["last_val"] = current   # 首拍采基线，不算变更
                    continue
                if state["last_val"] == current:
                    continue
                state["last_val"] = current
            else:                                  # if 判断：上升沿触发
                try:
                    truth = bool_value(expr.evaluate(
                        expr.normalize(card["arg"] or ""), self.values()))
                except expr.ExprError:
                    continue                       # 判断体未就绪（变量未到）
                if truth:
                    if state.get("last_bool") == 1:
                        continue
                    state["last_bool"] = 1
                else:
                    state["last_bool"] = 0
                    continue
            if card["actions"]:
                self.fire_card(card)
                fired += 1
        return fired

    def reset(self) -> None:
        self.signals.clear()
        self.last_values.clear()
        self.errors.clear()


def _typed(value: float, value_type: str):
    """输出值按声明类型归一：Int 取整、Bool 正值归真（>1 钳制，<=0 归假）、
    Float 保留三位。"""
    kind = str(value_type or "Int").upper()
    if kind == "BOOL":
        return bool(bool_value(value))
    if kind == "FLOAT":
        return round(float(value), 3)
    return int(round(float(value)))

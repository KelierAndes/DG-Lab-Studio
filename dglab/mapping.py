"""共享映射引擎：模块参数信号空间 + 核心参数双向映射表。

OSC 与 Alice in Cradle 等联动模块共用 :class:`MappingEngine`：数据源（OSC 收参 /
游戏 POST 上报）把命名数值喂进信号空间，引擎在每次信号或设备状态变化时求值
两张映射表：

* 输入表 ``核心输入参数 → 表达式``，结果取整钳制后派发设备动作；强度参数
  的钳制上限按当前通道上限（``家族.LimitX`` 上限信号）实时收紧；
* 输出表 ``模块侧参数名 → 表达式``，按 ``type`` 归一后供模块回传
  （OSC 发头像参数、游戏侧 GET /data）。

值空间 = 设备状态变量（``device_vars`` 回调，弱引用实时取）∪ 输入信号
（同名时信号优先）。未出现的变量按 0 处理。
"""

from __future__ import annotations

from typing import Any, Callable

from dglab import expr
from dglab.params import input_limit_signal, input_ranges, input_specs

__all__ = ["MappingEngine", "as_number", "bool_value", "signal_specs",
           "rows_to_map", "output_rows"]

_BOOL_EPS = 1e-9


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
        self.signals: dict[str, float] = {}
        self.mappings: dict[str, str] = {}
        self.errors: dict[str, str] = {}
        self.last_values: dict[str, int] = {}
        self.outputs: list[dict[str, Any]] = []
        self.out_values: dict[str, Any] = {}
        self.out_errors: dict[str, str] = {}
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
        return merged

    def pump(self) -> None:
        """重算两张映射表：输入按整数变化派发（强度按当前通道上限钳制），
        输出刷新实时值。"""
        if not self.mappings and not self.outputs:
            return
        vals = self.values()
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
            if self.last_values.get(target) == value:
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

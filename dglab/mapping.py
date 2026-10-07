
from __future__ import annotations

import time as _time
from typing import Any, Callable

from dglab import expr
from dglab.params import input_limit_signal, input_ranges, input_specs

__all__ = ["MappingEngine", "as_number", "bool_value", "signal_specs",
           "rows_to_map", "output_rows", "temp_rows", "event_cards"]

_BOOL_EPS = 1e-9
_TICK_INTERVAL = 0.05
_MIN_PERIOD_S = 0.05


def bool_value(raw: float) -> int:
    return 1 if raw > _BOOL_EPS else 0


def signal_specs() -> dict[str, tuple[int, int]]:
    return input_ranges()


def rows_to_map(rows: Any) -> dict[str, str]:
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
    return "{" + str(param_id).split(".")[-1] + "}"


def _channel_limit(target: str, vals: dict[str, float]) -> int | None:
    limit_key = input_limit_signal(target)
    if limit_key is None:
        return None
    channel = limit_key[-1]
    for key in (limit_key, f"Limit{channel}"):
        raw = vals.get(key)
        if raw is not None and raw > 0:
            return int(raw)
    return None


def output_rows(rows: Any) -> list[dict[str, Any]]:
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
                action["name"] = str(act.get("name") or "").strip()
                action["type"] = str(act.get("type") or "Int")
            actions.append(action)
        name = str(entry.get("name") or "").strip() or f"事件{index + 1}"
        arg = entry.get("arg")
        out.append({"name": name, "trigger": trigger,
                    "arg": arg, "actions": actions})
    return out


def as_number(value: Any) -> float | None:
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
        self._periodic_keys = {key for key, spec in input_specs().items()
                               if str(spec.get("action")) == "pulse"}
        self.signals: dict[str, float] = {}
        self.mappings: dict[str, str] = {}
        self.errors: dict[str, str] = {}
        self.last_values: dict[str, int] = {}
        self.outputs: list[dict[str, Any]] = []
        self.out_values: dict[str, Any] = {}
        self.out_errors: dict[str, str] = {}
        self.temps: dict[str, float] = {}
        self._temp_table: list[dict[str, str]] = []
        self._cards: list[dict[str, Any]] = []
        self._card_state: dict[int, dict[str, Any]] = {}
        self.armed = True


    def set_mappings(self, mappings: Any) -> None:
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
        self.outputs = output_rows(rows)
        self.out_errors.clear()
        self.pump()

    def attach_temps(self, shared: dict[str, float]) -> None:
        if shared is not self.temps:
            shared.update(self.temps)
            self.temps = shared

    def set_temp_rows(self, rows: Any) -> None:
        self._temp_table = temp_rows(rows)
        self.pump()

    def set_event_cards(self, rows: Any) -> None:
        self._cards = event_cards(rows)
        self._card_state = {}

    def has_events(self) -> bool:
        return any(card["actions"] for card in self._cards)

    def set_ranges(self, ranges: dict[str, tuple[int, int]]) -> None:
        self._ranges = dict(ranges or {})


    def signal(self, name: str, value: Any) -> None:
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
        for key, value in self.temps.items():
            merged.setdefault(key, value)
        return merged

    def _eval_temps(self, vals: dict[str, float]) -> None:
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
            except Exception:
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
                        continue
                    self.last_values[action["param"]] = value
                    if self.armed:
                        self._dispatch(action["param"], value)
                    fired += 1
                else:
                    raw = float(vals.get(action["param"], 0.0))
                    self.temps[action["var"]] = raw
                    name = str(action.get("name") or "")
                    if name:
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
        low, high = self._ranges.get(target, self._default_range)
        limit = _channel_limit(target, vals)
        if limit is not None:
            high = min(high, limit)
        if target in self._bool_keys:
            return bool_value(raw)
        return expr.clamp_int(raw, low, high)

    def tick_event_cards(self, now: float | None = None) -> int:
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
                state["last_fire"] = now
            elif trigger == "change":
                current = self.values().get(str(card["arg"] or ""), 0.0)
                if "last_val" not in state:
                    state["last_val"] = current
                    continue
                if state["last_val"] == current:
                    continue
                state["last_val"] = current
            else:
                try:
                    truth = bool_value(expr.evaluate(
                        expr.normalize(card["arg"] or ""), self.values()))
                except expr.ExprError:
                    continue
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
    kind = str(value_type or "Int").upper()
    if kind == "BOOL":
        return bool(bool_value(value))
    if kind == "FLOAT":
        return round(float(value), 3)
    return int(round(float(value)))

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dglab import expr
from dglab.mapping import (MappingEngine, as_number, event_cards,
                           temp_rows)


class ExprTests(unittest.TestCase):
    def test_basic_arith(self):
        vals = {"HP": 30.0, "HPmax": 100.0}
        self.assertAlmostEqual(expr.evaluate("{HP}/{HPmax}*200", vals), 60.0)
        self.assertAlmostEqual(expr.evaluate("({HP}+{HPmax})/2", vals), 65.0)
        self.assertAlmostEqual(expr.evaluate("-{HP}+10", vals), -20.0)
        self.assertAlmostEqual(expr.evaluate("2**3+{HP}%7", vals), 10.0)

    def test_user_example(self):
        vals = {"Strength": 120.0, "max": 200.0, "HP": 60.0,
                "Hurt": 12.0, "HPmax": 100.0}
        out = expr.eval_int("{Strength-max}*({HP}+{Hurt}/{HPmax})", vals, 0, 200)
        self.assertEqual(out, 0)
        vals["Strength"] = 300.0
        self.assertEqual(expr.eval_int("{Strength-max}*({HP}+{Hurt}/{HPmax})",
                                       vals, 0, 200), 200)

    def test_brace_subexpr(self):
        vals = {"a": 4.0, "b": 2.0}
        self.assertAlmostEqual(expr.evaluate("{a*b}+{a+b}", vals), 14.0)

    def test_unknown_var_is_zero(self):
        self.assertAlmostEqual(expr.evaluate("{nope}*5+2", {}), 2.0)

    def test_unknown_dotted_param_is_zero(self):
        self.assertAlmostEqual(expr.evaluate("{COYOTE.StrengthA}+1", {}), 1.0)
        self.assertAlmostEqual(expr.evaluate("{COYOTE.2.Battery}", {}), 0.0)

    def test_fullwidth_normalize(self):
        self.assertAlmostEqual(
            expr.evaluate("（{a}＋{b}）／２", {"a": 6, "b": 4}), 5.0)

    def test_funcs(self):
        vals = {"a": 7.5, "b": 2.0}
        self.assertAlmostEqual(expr.evaluate("round({a})+max({a},{b})", vals), 15.5)
        self.assertAlmostEqual(expr.evaluate("abs(0-{a})+min(1,{b})", vals), 8.5)

    def test_errors(self):
        with self.assertRaises(expr.ExprError):
            expr.evaluate("{a}/0", {"a": 1})
        with self.assertRaises(expr.ExprError):
            expr.evaluate("", {})
        with self.assertRaises(expr.ExprError):
            expr.evaluate("{a", {"a": 1})
        with self.assertRaises(expr.ExprError):
            expr.evaluate("{a}+__import__('os')", {"a": 1})
        with self.assertRaises(expr.ExprError):
            expr.evaluate("9**99", {})

    def test_variables(self):
        self.assertEqual(expr.variables("{HP}/{HPmax}*200+{a}"),
                         {"HP", "HPmax", "a"})
        self.assertEqual(expr.variables("max(1,{x})"), {"x"})


class MappingEngineTests(unittest.TestCase):
    def setUp(self):
        self.sent: list[tuple[str, int]] = []
        self.engine = MappingEngine(
            lambda key, value: self.sent.append((key, value)),
            device_vars=lambda: {"StrengthA": 50, "LimitA": 200},
            ranges={"in_strength_a": (0, 200), "in_fire": (0, 1)},
            default_range=(0, 200))

    def test_signal_triggers_dispatch_on_change(self):
        self.engine.set_mappings({"in_strength_a": "{HP}/{HPmax}*200"})
        self.sent = []
        self.engine.signal("HP", 60)
        self.engine.signal("HPmax", 100)
        self.assertEqual(self.sent, [("in_strength_a", 120)])
        self.engine.signal("HP", 60)
        self.assertEqual(len(self.sent), 1)
        self.engine.signal("HP", 30)
        self.assertEqual(self.sent[-1], ("in_strength_a", 60))

    def test_device_vars_visible(self):
        self.engine.set_mappings({"in_strength_a": "{StrengthA}+{LimitA}"})
        self.engine.pump()
        self.assertEqual(self.sent, [("in_strength_a", 200)])

    def test_signal_overrides_device_var(self):
        self.engine.set_mappings({"in_strength_a": "{StrengthA}"})
        self.sent = []
        self.engine.signal("StrengthA", 9)
        self.assertEqual(self.sent, [("in_strength_a", 9)])

    def test_error_recorded_and_skips(self):
        self.engine.set_mappings({"in_strength_a": "{oops}/0"})
        self.engine.signal("oops", 5)
        self.assertEqual(self.sent, [])
        self.assertIn("in_strength_a", self.engine.errors)

    def test_empty_mapping_ignored(self):
        self.engine.set_mappings({"in_strength_a": "  ", "in_fire": "{x}"})
        self.assertEqual(list(self.engine.mappings), ["in_fire"])

    def test_bool_target_truthy_clamp(self):
        self.engine.set_mappings({"in_fire": "{flag}"})
        self.sent = []
        self.engine.signal("flag", 0.4)
        self.assertEqual(self.sent, [("in_fire", 1)])
        self.engine.signal("flag", 0)
        self.assertEqual(self.sent[-1], ("in_fire", 0))
        self.engine.signal("flag", 0.5)
        self.assertEqual(self.sent[-1], ("in_fire", 1))
        self.engine.signal("flag", -0.5)
        self.assertEqual(self.sent[-1], ("in_fire", 0))
        self.engine.signal("flag", 2.5)
        self.assertEqual(self.sent[-1], ("in_fire", 1))

    def test_reset(self):
        self.engine.set_mappings({"in_fire": "{b}"})
        self.sent = []
        self.engine.signal("b", 1)
        self.assertEqual(self.sent, [("in_fire", 1)])
        self.engine.reset()
        self.assertEqual(self.engine.signals, {})
        self.engine.signal("b", 1)
        self.assertEqual(self.sent[-1], ("in_fire", 1))

    def test_as_number(self):
        self.assertEqual(as_number(True), 1.0)
        self.assertEqual(as_number("2.5"), 2.5)
        self.assertIsNone(as_number("abc"))
        self.assertIsNone(as_number(None))

    def test_bool_value(self):
        from dglab.mapping import bool_value
        self.assertEqual(bool_value(0.4), 1)
        self.assertEqual(bool_value(1.0), 1)
        self.assertEqual(bool_value(2.5), 1)
        self.assertEqual(bool_value(0.0), 0)
        self.assertEqual(bool_value(-0.5), 0)
        self.assertEqual(bool_value(-7), 0)
        self.assertEqual(bool_value(1e-12), 0)

    def test_typed_bool_output(self):
        from dglab.mapping import _typed
        self.assertTrue(_typed(0.4, "Bool"))
        self.assertTrue(_typed(3.7, "Bool"))
        self.assertFalse(_typed(0.0, "Bool"))
        self.assertFalse(_typed(-0.5, "Bool"))
        self.assertFalse(_typed(-2, "Bool"))
        self.assertEqual(_typed(1.6, "Int"), 2)
        self.assertEqual(_typed(1.23456, "Float"), 1.235)


class ChannelLimitClampTests(unittest.TestCase):

    def test_input_limit_signal_mapping(self):
        from dglab.params import input_limit_signal
        self.assertEqual(input_limit_signal("in_strength_a"), "COYOTE.LimitA")
        self.assertEqual(input_limit_signal("in_strength_b"), "COYOTE.LimitB")
        self.assertEqual(input_limit_signal("in_ovc_strength_a"), "OVC.LimitA")
        self.assertIsNone(input_limit_signal("in_wave_a"))
        self.assertIsNone(input_limit_signal("in_fire_a"))
        self.assertIsNone(input_limit_signal("in_fire"))
        self.assertIsNone(input_limit_signal(""))

    def _engine(self, vars):
        from dglab.params import input_ranges
        sent = []
        engine = MappingEngine(
            lambda key, value: sent.append((key, value)),
            device_vars=lambda: dict(vars),
            ranges=input_ranges(), default_range=(0, 200))
        return engine, sent

    def test_strength_result_clamped_to_channel_limit(self):
        vars = {"COYOTE.LimitA": 60.0}
        engine, sent = self._engine(vars)
        engine.set_mappings({"in_strength_a": "{v}*2"})
        sent.clear()
        engine.signal("v", 50)
        self.assertEqual(sent, [("in_strength_a", 60)])
        vars["COYOTE.LimitA"] = 200.0
        engine.pump()
        self.assertEqual(sent[-1], ("in_strength_a", 100))
        vars["COYOTE.LimitA"] = 30.0
        engine.pump()
        self.assertEqual(sent[-1], ("in_strength_a", 30))

    def test_channel_limit_bare_alias_fallback(self):
        engine, sent = self._engine({"LimitA": 45.0})
        engine.set_mappings({"in_ovc_strength_a": "{v}"})
        sent.clear()
        engine.signal("v", 80)
        self.assertEqual(sent, [("in_ovc_strength_a", 45)])

    def test_channel_limit_absent_keeps_static_range(self):
        engine, sent = self._engine({})
        engine.set_mappings({"in_strength_a": "{v}"})
        sent.clear()
        engine.signal("v", 150)
        self.assertEqual(sent, [("in_strength_a", 150)])

    def test_channel_limit_zero_ignored(self):
        engine, sent = self._engine({"COYOTE.LimitA": 0.0})
        engine.set_mappings({"in_strength_a": "{v}"})
        sent.clear()
        engine.signal("v", 40)
        self.assertEqual(sent, [("in_strength_a", 40)])

    def test_bool_targets_unaffected(self):
        engine, sent = self._engine({"COYOTE.LimitA": 0.0})
        engine.set_mappings({"in_fire": "{v}"})
        sent.clear()
        engine.signal("v", 1)
        self.assertEqual(sent, [("in_fire", 1)])


class FireNamingTests(unittest.TestCase):

    CONFIG = {"prefix": "DGLab",
              "device_prefixes": {"COYOTE": "DGLab", "OVC": "DGLabOvc"}}

    def test_fire_names_have_channel_suffix(self):
        from dglab.naming import default_input_name

        self.assertEqual(default_input_name(self.CONFIG, "in_fire"),
                         "DGLabFire")
        self.assertEqual(default_input_name(self.CONFIG, "in_fire_a"),
                         "DGLabFireA")
        self.assertEqual(default_input_name(self.CONFIG, "in_fire_b"),
                         "DGLabFireB")
        self.assertEqual(default_input_name(self.CONFIG, "in_ovc_fire"),
                         "DGLabOvcInFire")
        self.assertEqual(default_input_name(self.CONFIG, "in_ovc_fire_a"),
                         "DGLabOvcInFireA")

    def test_fire_params_are_per_channel(self):
        from dglab.params import input_spec, input_specs

        specs = input_specs()
        for prefix in ("in_", "in_ovc_"):
            for ch in ("a", "b"):
                spec = input_spec(f"{prefix}fire_{ch}")
                self.assertIsNotNone(spec)
                self.assertEqual(spec["channel"], ch.upper())
                self.assertEqual(spec["action"], "fire")
        self.assertEqual(input_spec("in_fire_a")["channel"], "A")
        self.assertEqual(input_spec("in_fire")["channel"], "")


class DispatcherEdgeTests(unittest.TestCase):

    class _Api:
        def __init__(self):
            self.calls = []

        def run(self, coro):
            coro.close()

        def resolve_slot(self, family=""):
            return "s1"

        def fire_start(self, slot_id=None, channel=None):
            self.calls.append(("fire", "start", channel))
            return _noop_coro()

        def fire_stop(self, slot_id=None, channel=None):
            self.calls.append(("fire", "stop", channel))
            return _noop_coro()

        def zap(self, channel, seconds=1.0, slot_id=None):
            self.calls.append(("zap", channel))
            return _noop_coro()

        def emergency_stop(self):
            self.calls.append(("emergency",))
            return _noop_coro()

    def _actions(self):
        from dglab.params import build_dispatchers

        api = self._Api()
        return api, build_dispatchers(api)

    def test_fire_only_on_edges(self):
        api, actions = self._actions()
        actions["in_fire"](1)
        actions["in_fire"](1)
        actions["in_fire"](0)
        actions["in_fire"](0)
        self.assertEqual(api.calls,
                         [("fire", "start", None), ("fire", "stop", None)])

    def test_fire_channel_dispatch(self):
        api, actions = self._actions()
        actions["in_fire_a"](1)
        actions["in_fire_a"](1)
        actions["in_fire_a"](0)
        actions["in_fire_b"](1)
        actions["in_fire_b"](0)
        actions["in_ovc_fire_a"](1)
        self.assertEqual(api.calls, [
            ("fire", "start", "A"), ("fire", "stop", "A"),
            ("fire", "start", "B"), ("fire", "stop", "B"),
            ("fire", "start", "A"),
        ])

    def test_emergency_dedupe(self):
        api, actions = self._actions()
        actions["in_emergency"](1)
        actions["in_emergency"](1)
        self.assertEqual(api.calls, [("emergency",)])


async def _noop_coro():
    pass


class TempVarTests(unittest.TestCase):

    def setUp(self):
        self.sent: list[tuple[str, int]] = []
        self.engine = MappingEngine(
            lambda key, value: self.sent.append((key, value)),
            device_vars=lambda: {"StrengthA": 50},
            ranges={"in_strength_a": (0, 200), "in_fire": (0, 1)},
            default_range=(0, 200))

    def test_temp_rows_filters_invalid(self):
        self.assertEqual(temp_rows([
            {"name": "a", "expr": "{x}+1"},
            {"name": "", "expr": "1"},
            {"name": "b", "expr": ""},
            {"name": "a", "expr": "2"},
        ]), [{"name": "a", "expr": "{x}+1"}])

    def test_temp_visible_to_mapping_and_value_space(self):
        self.engine.set_temp_rows([{"name": "half", "expr": "{StrengthA}/2"}])
        self.sent = []
        self.engine.set_mappings({"in_strength_a": "{half}*2"})
        self.assertEqual(self.sent, [("in_strength_a", 50)])
        self.assertEqual(self.engine.values()["half"], 25.0)

    def test_temp_self_reference_accumulates(self):
        self.engine.set_temp_rows([{"name": "count", "expr": "{count}+1"}])
        self.assertEqual(self.engine.temps["count"], 1.0)
        self.engine.pump()
        self.engine.pump()
        self.assertEqual(self.engine.temps["count"], 3.0)

    def test_signal_overrides_temp(self):
        self.engine.set_temp_rows([{"name": "x", "expr": "1"}])
        self.engine.pump()
        self.engine.signal("x", 9)
        self.assertEqual(self.engine.values()["x"], 9.0)

    def test_temp_error_recorded(self):
        self.engine.set_temp_rows([{"name": "bad", "expr": "1/0"}])
        self.engine.pump()
        self.assertIn("temp:bad", self.engine.errors)

    def test_shared_space_attached(self):
        shared: dict[str, float] = {}
        self.engine.attach_temps(shared)
        self.engine.set_temp_rows([{"name": "n", "expr": "7"}])
        self.engine.pump()
        self.assertIs(self.engine.temps, shared)
        self.assertEqual(shared["n"], 7.0)


class EventStreamTests(unittest.TestCase):

    def setUp(self):
        self.sent: list[tuple[str, int]] = []
        self.engine = MappingEngine(
            lambda key, value: self.sent.append((key, value)),
            device_vars=lambda: {"StrengthA": 50, "LimitA": 200,
                                 "OVC.Pressure": 12.5},
            ranges={"in_strength_a": (0, 200), "in_fire": (0, 1)},
            default_range=(0, 200))

    def test_event_cards_normalizes(self):
        cards = event_cards([
            {"name": "A", "trigger": "period", "arg": 100,
             "actions": [{"dir": "in", "param": "in_fire", "var": "x"},
                         {"dir": "bad", "param": "p", "var": "v"},
                         {"dir": "in", "param": "", "var": "v"}]},
            {"trigger": "change", "arg": "x"},
            {"trigger": "nope", "arg": 1},
        ])
        self.assertEqual([c["name"] for c in cards], ["A", "事件2"])
        self.assertEqual(cards[0]["actions"],
                         [{"dir": "in", "param": "in_fire", "var": "x"}])
        self.assertEqual(cards[1]["trigger"], "change")

    def test_period_trigger_fires_on_interval(self):
        self.engine.set_event_cards([
            {"name": "A", "trigger": "period", "arg": 100,
             "actions": [{"dir": "in", "param": "in_fire", "var": "x"}]}])
        self.engine.signal("x", 1)
        self.assertEqual(self.engine.tick_event_cards(0.0), 1)
        self.assertEqual(self.sent, [("in_fire", 1)])
        self.assertEqual(self.engine.tick_event_cards(0.05), 0)
        self.assertEqual(self.engine.tick_event_cards(0.1), 1)
        self.assertEqual(self.sent, [("in_fire", 1)])
        self.engine.signal("x", 0)
        self.engine.tick_event_cards(0.21)
        self.engine.signal("x", 1)
        self.assertEqual(self.engine.tick_event_cards(0.42), 1)
        self.assertEqual(self.sent, [("in_fire", 1), ("in_fire", 0),
                                     ("in_fire", 1)])

    def test_period_dispatch_does_not_overwrite_manual_change(self):
        self.engine.set_event_cards([
            {"name": "A", "trigger": "period", "arg": 50,
             "actions": [{"dir": "in", "param": "in_strength_a",
                          "var": "x"}]}])
        self.engine.signal("x", 30)
        self.engine.tick_event_cards(0.0)
        self.assertEqual(self.sent, [("in_strength_a", 30)])
        for t in (0.05, 0.10, 0.15):
            self.engine.tick_event_cards(t)
        self.assertEqual(self.sent, [("in_strength_a", 30)])
        self.assertEqual(self.engine.last_values.get("in_strength_a"), 30)

    def test_change_trigger(self):
        self.engine.set_event_cards([
            {"name": "A", "trigger": "change", "arg": "x",
             "actions": [{"dir": "in", "param": "in_strength_a",
                          "var": "x"}]}])
        self.engine.signal("x", 10)
        self.assertEqual(self.engine.tick_event_cards(0.0), 0)
        self.engine.signal("x", 20)
        self.assertEqual(self.engine.tick_event_cards(0.05), 1)
        self.assertEqual(self.sent, [("in_strength_a", 20)])
        self.assertEqual(self.engine.tick_event_cards(0.10), 0)

    def test_if_trigger_rising_edge(self):
        self.engine.set_event_cards([
            {"name": "A", "trigger": "if", "arg": "{x} > 10",
             "actions": [{"dir": "in", "param": "in_fire", "var": "x"}]}])
        self.engine.signal("x", 5)
        self.assertEqual(self.engine.tick_event_cards(0.0), 0)
        self.engine.signal("x", 15)
        self.assertEqual(self.engine.tick_event_cards(0.05), 1)
        self.assertEqual(self.engine.tick_event_cards(0.10), 0)
        self.engine.signal("x", 3)
        self.engine.tick_event_cards(0.15)
        self.engine.signal("x", 30)
        self.assertEqual(self.engine.tick_event_cards(0.20), 1)

    def test_if_trigger_bare_bool_var(self):
        self.engine.set_event_cards([
            {"name": "A", "trigger": "if", "arg": "flag",
             "actions": [{"dir": "in", "param": "in_fire", "var": "flag"}]}])
        self.engine.signal("flag", 1)
        self.assertEqual(self.engine.tick_event_cards(0.0), 1)

    def test_input_action_clamps_and_bool(self):
        self.engine.set_event_cards([
            {"name": "A", "trigger": "period", "arg": 50,
             "actions": [{"dir": "in", "param": "in_strength_a", "var": "v"},
                         {"dir": "in", "param": "in_fire", "var": "v"}]}])
        self.engine.signal("v", 999)
        self.engine.tick_event_cards(0.0)
        self.assertEqual(self.sent, [("in_strength_a", 200), ("in_fire", 1)])

    def test_output_action_captures_signal_to_temp(self):
        self.engine.set_event_cards([
            {"name": "A", "trigger": "period", "arg": 50,
             "actions": [{"dir": "out", "param": "OVC.Pressure",
                          "var": "edge"}]}])
        self.engine.tick_event_cards(0.0)
        self.assertEqual(self.engine.temps["edge"], 12.5)

    def test_missing_var_dispatches_zero(self):
        self.engine.set_event_cards([
            {"name": "A", "trigger": "period", "arg": 50,
             "actions": [{"dir": "in", "param": "in_strength_a",
                          "var": "ghost"}]}])
        self.engine.tick_event_cards(0.0)
        self.assertEqual(self.sent, [("in_strength_a", 0)])

    def test_card_without_actions_skipped(self):
        self.engine.set_event_cards([
            {"name": "A", "trigger": "period", "arg": 50, "actions": []}])
        self.assertEqual(self.engine.tick_event_cards(0.0), 0)
        self.assertEqual(self.sent, [])
        self.assertFalse(self.engine.has_events())

    def test_temps_join_event_actions(self):
        self.engine.set_temp_rows([{"name": "count", "expr": "{count}+1"}])
        self.engine.set_event_cards([
            {"name": "A", "trigger": "period", "arg": 50,
             "actions": [{"dir": "in", "param": "in_strength_a",
                          "var": "count"}]}])
        self.assertEqual(self.engine.temps["count"], 1.0)
        self.engine.tick_event_cards(0.0)
        self.assertEqual(self.sent, [("in_strength_a", 1)])
        self.engine.pump()
        self.engine.tick_event_cards(0.05)
        self.assertEqual(self.sent[-1], ("in_strength_a", 2))


if __name__ == "__main__":
    unittest.main()
    unittest.main()

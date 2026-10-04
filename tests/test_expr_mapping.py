"""表达式求值（dglab.expr）与共享映射引擎（dglab.mapping）回归测试。"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dglab import expr
from dglab.mapping import MappingEngine, as_number


class ExprTests(unittest.TestCase):
    def test_basic_arith(self):
        vals = {"HP": 30.0, "HPmax": 100.0}
        self.assertAlmostEqual(expr.evaluate("{HP}/{HPmax}*200", vals), 60.0)
        self.assertAlmostEqual(expr.evaluate("({HP}+{HPmax})/2", vals), 65.0)
        self.assertAlmostEqual(expr.evaluate("-{HP}+10", vals), -20.0)
        self.assertAlmostEqual(expr.evaluate("2**3+{HP}%7", vals), 10.0)

    def test_user_example(self):
        # （输入，in_strength_a <- {Strength-max}*({HP}+{Hurt}/{HPmax}) 取整）
        vals = {"Strength": 120.0, "max": 200.0, "HP": 60.0,
                "Hurt": 12.0, "HPmax": 100.0}
        out = expr.eval_int("{Strength-max}*({HP}+{Hurt}/{HPmax})", vals, 0, 200)
        self.assertEqual(out, 0)   # 负值钳到 0
        vals["Strength"] = 300.0
        self.assertEqual(expr.eval_int("{Strength-max}*({HP}+{Hurt}/{HPmax})",
                                       vals, 0, 200), 200)

    def test_brace_subexpr(self):
        vals = {"a": 4.0, "b": 2.0}
        self.assertAlmostEqual(expr.evaluate("{a*b}+{a+b}", vals), 14.0)

    def test_unknown_var_is_zero(self):
        self.assertAlmostEqual(expr.evaluate("{nope}*5+2", {}), 2.0)

    def test_unknown_dotted_param_is_zero(self):
        # 核心输出参数 id 带点号：设备未接入（不在值表）时按 0，不报语法节点错误
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
        self.assertEqual(expr.variables("max(1,{x})"), {"x"})   # 函数名不算变量


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
        self.sent = []                          # set_mappings 首轮 pump 不算
        self.engine.signal("HP", 60)
        self.engine.signal("HPmax", 100)      # 60/100*200=120
        self.assertEqual(self.sent, [("in_strength_a", 120)])
        self.engine.signal("HP", 60)          # 同值不重复派发
        self.assertEqual(len(self.sent), 1)
        self.engine.signal("HP", 30)          # 60
        self.assertEqual(self.sent[-1], ("in_strength_a", 60))

    def test_device_vars_visible(self):
        self.engine.set_mappings({"in_strength_a": "{StrengthA}+{LimitA}"})
        self.engine.pump()
        self.assertEqual(self.sent, [("in_strength_a", 200)])   # 250 钳到 200

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
        self.engine.signal("flag", 0.4)      # (0,1] 正值归真（round(0.4)=0 会导致不生效）
        self.assertEqual(self.sent, [("in_fire", 1)])
        self.engine.signal("flag", 0)
        self.assertEqual(self.sent[-1], ("in_fire", 0))
        self.engine.signal("flag", 0.5)      # round(0.5)=0，同样必须归一为 1
        self.assertEqual(self.sent[-1], ("in_fire", 1))
        self.engine.signal("flag", -0.5)     # 负值 ≤0 归 0，不再按“非零即真”当成真
        self.assertEqual(self.sent[-1], ("in_fire", 0))
        self.engine.signal("flag", 2.5)      # 大于 1 钳制为 1
        self.assertEqual(self.sent[-1], ("in_fire", 1))

    def test_reset(self):
        self.engine.set_mappings({"in_fire": "{b}"})
        self.sent = []
        self.engine.signal("b", 1)
        self.assertEqual(self.sent, [("in_fire", 1)])
        self.engine.reset()
        self.assertEqual(self.engine.signals, {})
        self.engine.signal("b", 1)            # 重置后重新派发
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
        self.assertEqual(bool_value(2.5), 1)     # 大于 1 钳制为 1
        self.assertEqual(bool_value(0.0), 0)
        self.assertEqual(bool_value(-0.5), 0)    # 小于等于 0 归 0
        self.assertEqual(bool_value(-7), 0)
        self.assertEqual(bool_value(1e-12), 0)   # 浮点噪声按 0

    def test_typed_bool_output(self):
        from dglab.mapping import _typed
        self.assertTrue(_typed(0.4, "Bool"))
        self.assertTrue(_typed(3.7, "Bool"))     # 大于 1 钳制为 true
        self.assertFalse(_typed(0.0, "Bool"))
        self.assertFalse(_typed(-0.5, "Bool"))   # 负值归 false（原 bool() 会给 True）
        self.assertFalse(_typed(-2, "Bool"))
        self.assertEqual(_typed(1.6, "Int"), 2)
        self.assertEqual(_typed(1.23456, "Float"), 1.235)


class ChannelLimitClampTests(unittest.TestCase):
    """强度映射结果按当前通道上限信号（LimitA/LimitB）动态钳制。"""

    def test_input_limit_signal_mapping(self):
        from dglab.params import input_limit_signal
        self.assertEqual(input_limit_signal("in_strength_a"), "COYOTE.LimitA")
        self.assertEqual(input_limit_signal("in_strength_b"), "COYOTE.LimitB")
        self.assertEqual(input_limit_signal("in_ovc_strength_a"), "OVC.LimitA")
        self.assertIsNone(input_limit_signal("in_wave_a"))
        self.assertIsNone(input_limit_signal("in_zap_a"))
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
        engine.signal("v", 50)               # 100 → 收紧到通道上限 60
        self.assertEqual(sent, [("in_strength_a", 60)])
        vars["COYOTE.LimitA"] = 200.0        # 上限放开 → 重算派发 100
        engine.pump()
        self.assertEqual(sent[-1], ("in_strength_a", 100))
        vars["COYOTE.LimitA"] = 30.0         # 上限收紧 → 重算派发 30
        engine.pump()
        self.assertEqual(sent[-1], ("in_strength_a", 30))

    def test_channel_limit_bare_alias_fallback(self):
        # 家族键缺失（跨家族兜底派发到别名指向的设备）时退回裸别名上限
        engine, sent = self._engine({"LimitA": 45.0})
        engine.set_mappings({"in_ovc_strength_a": "{v}"})
        sent.clear()
        engine.signal("v", 80)
        self.assertEqual(sent, [("in_ovc_strength_a", 45)])

    def test_channel_limit_absent_keeps_static_range(self):
        # 上限信号未上报（设备未接入）时沿用静态 0-200 钳制
        engine, sent = self._engine({})
        engine.set_mappings({"in_strength_a": "{v}"})
        sent.clear()
        engine.signal("v", 150)
        self.assertEqual(sent, [("in_strength_a", 150)])

    def test_channel_limit_zero_ignored(self):
        # 上限 ≤0 视为未上报，不把映射钉死在 0
        engine, sent = self._engine({"COYOTE.LimitA": 0.0})
        engine.set_mappings({"in_strength_a": "{v}"})
        sent.clear()
        engine.signal("v", 40)
        self.assertEqual(sent, [("in_strength_a", 40)])

    def test_bool_targets_unaffected(self):
        # 非强度参数（开火）不引入通道上限钳制
        engine, sent = self._engine({"COYOTE.LimitA": 0.0})
        engine.set_mappings({"in_fire": "{v}"})
        sent.clear()
        engine.signal("v", 1)
        self.assertEqual(sent, [("in_fire", 1)])


class FireNamingTests(unittest.TestCase):
    """通道开火的默认参数名：通道后缀区分 fire_a/b，家族级 fire 名称不变。"""

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
    """fire / zap / 急停派发器的 0↔非零边沿记忆：重复派发不再重复动作。"""

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
        """通道分离：fire_a / fire_b 只派发对应通道，与双通道 fire 互不混淆。"""
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

    def test_zap_and_emergency_dedupe(self):
        api, actions = self._actions()
        actions["in_zap_a"](1)
        actions["in_zap_a"](1)
        actions["in_emergency"](1)
        actions["in_emergency"](1)
        self.assertEqual(api.calls, [("zap", "A"), ("emergency",)])


async def _noop_coro():
    pass


if __name__ == "__main__":
    unittest.main()

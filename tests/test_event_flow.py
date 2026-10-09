from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace

from dglab import event_flow as EF, flow_host
from dglab.params import core_inputs
from dglab.state import EngineState, Slot, family_of
from dglab.waves import wave_order

MODULES = [{"id": "osc_bridge", "name": "OSC 桥接模块",
            "params": [{"name": "osc_strength", "label": "OSC 强度",
                        "dir": "inout"},
                       {"name": "osc_battery", "label": "OSC 电量"}]}]
STRENGTH = "in_strength_a"


def _catalog() -> EF.Catalog:
    catalog = EF.Catalog()
    catalog.refresh(MODULES, 1)
    return catalog


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


class CatalogTests(unittest.TestCase):

    def setUp(self):
        self.catalog = _catalog()

    def test_core_params_generate_cards(self):
        for spec in core_inputs():
            self.assertIn(f"core.write.{spec['key']}", self.catalog.defs)
        read = self.catalog.definition("core.read.COYOTE.Battery")
        self.assertEqual(read["op"], "core_read")
        self.assertEqual(read["page"], EF.PAGE_OUTPUT)

    def test_module_endpoints_follow_module_list(self):
        for key in ("mod.read.osc_bridge.osc_strength",
                    "mod.write.osc_bridge.osc_strength"):
            self.assertTrue(self.catalog.known(key), key)
        bare = EF.Catalog()
        self.assertFalse(bare.known("mod.read.osc_bridge.osc_strength"))

    def test_driver_cards_are_generic_not_per_module(self):
        for key in ("mod.period", "mod.change"):
            self.assertTrue(self.catalog.known(key), key)
        bare = EF.Catalog()
        bare.refresh([], 1)
        self.assertTrue(bare.known("mod.period"))
        self.assertTrue(bare.known("mod.change"))
        self.assertFalse(bare.known("mod.period.osc_bridge"))

    def test_registered_module_temps_become_cards(self):
        catalog = EF.Catalog()
        catalog.refresh([{"id": "osc_bridge", "name": "OSC 桥接模块",
                          "params": [
                              {"key": "avatar/parameters/DGLabStrengthA",
                               "label": "OSC 可读参数", "dir": "in"},
                              {"key": "DGLab/Action", "label": "OSC 可写参数",
                               "dir": "out"},
                              {"key": "MyVar", "dir": "inout"}]}], 1)
        read = catalog.definition("mod.read.osc_bridge.avatar/parameters/DGLabStrengthA")
        self.assertEqual(read["cat"], "临时变量")
        self.assertEqual(read["page"], EF.PAGE_INPUT)
        self.assertEqual(read["op"], "mod_read")
        self.assertEqual(read["fields"][1]["default"],
                         "avatar/parameters/DGLabStrengthA")
        self.assertEqual(catalog.definition("mod.read.osc_bridge.DGLab/Action"),
                         EF._MISSING)
        self.assertEqual(catalog.definition(
            "mod.write.osc_bridge.DGLab/Action")["page"], EF.PAGE_OUTPUT)
        for kind in ("read", "write"):
            self.assertTrue(catalog.known(f"mod.{kind}.osc_bridge.MyVar"), kind)

    def test_variable_cards_are_immediate_read_write(self):
        """拖出来的变量卡本身就是即时读 / 即时写端，没有对象层。"""
        catalog = EF.Catalog()
        catalog.refresh([{"id": "osc_bridge", "name": "OSC 桥接模块",
                          "params": [{"key": "avatar/parameters/X", "dir": "in"},
                                     {"key": "DGLab/Action", "dir": "out"}]}],
                        1, user_vars=[{"name": "MyVar", "dir": "inout"}])
        read = catalog.definition("mod.read.osc_bridge.avatar/parameters/X")
        write = catalog.definition("mod.write.osc_bridge.DGLab/Action")
        self.assertEqual((read["op"], read["page"]), ("mod_read", EF.PAGE_INPUT))
        self.assertEqual(read["outputs"][0]["type"], EF.FLOAT)
        self.assertEqual(write["op"], "mod_write")
        self.assertFalse(read["palette"])
        self.assertEqual(catalog.definition("var.read.MyVar")["op"], "temp_read")
        self.assertEqual(catalog.definition("var.write.MyVar")["inputs"][0]["type"],
                         EF.FLOAT)
        self.assertFalse(catalog.known("var.obj.MyVar"))
        self.assertEqual(
            EF.var_card_key("MyVar", "inout", EF.PAGE_INPUT), "var.read.MyVar")
        self.assertEqual(
            EF.var_card_key("MyVar", "inout", EF.PAGE_OUTPUT), "var.write.MyVar")
        self.assertEqual(
            EF.var_card_key("MyVar", "out", EF.PAGE_INPUT), "var.write.MyVar")
        self.assertEqual(
            EF.var_card_key("DGLab/Action", "out", EF.PAGE_OUTPUT, "osc_bridge"),
            "mod.write.osc_bridge.DGLab/Action")

    def test_undeclared_direction_defaults_to_read_only(self):
        """没写方向的登记参数按只读处理：变量表不再满屏「读写」。"""
        catalog = EF.Catalog()
        catalog.refresh([{"id": "osc_bridge", "name": "OSC 桥接模块",
                          "params": [("osc_hp", "HP")]}], 1)
        self.assertTrue(catalog.known("mod.read.osc_bridge.osc_hp"))
        self.assertFalse(catalog.known("mod.write.osc_bridge.osc_hp"))
        self.assertEqual(EF.module_pool(
            {"params": [("osc_hp", "HP")], "id": "m"})[0]["dir"], "in")

    def test_pages_split_drivers_from_sinks(self):
        input_keys = {d["key"] for d in self.catalog.templates(EF.PAGE_INPUT)}
        output_keys = {d["key"] for d in self.catalog.templates(EF.PAGE_OUTPUT)}
        self.assertIn(f"core.write.{STRENGTH}", input_keys)
        self.assertNotIn(f"core.write.{STRENGTH}", output_keys)
        self.assertIn("core.read.COYOTE.Battery", output_keys)
        self.assertNotIn("core.read.COYOTE.Battery", input_keys)

    def test_search_matches_chinese_and_pinyin(self):
        self.assertIn(f"core.write.{STRENGTH}",
                      [r["def"]["key"] for r in self.catalog.search(EF.PAGE_INPUT, "强度")])
        self.assertIn("math.sin",
                      [r["def"]["key"] for r in self.catalog.search(EF.PAGE_INPUT, "zhengxian")])
        self.assertIn("mod.period",
                      [r["def"]["key"] for r in self.catalog.search(EF.PAGE_INPUT, "zhouqi")])

    def test_variable_expression_card_is_gone(self):
        self.assertFalse(self.catalog.known("var.temp_expr"))
        keys = {d["key"] for d in self.catalog.templates(EF.PAGE_INPUT)}
        self.assertNotIn("var.temp_expr", keys)

    def test_individual_comparison_cards_exist(self):
        cases = {"logic.eq": ("等于", True), "logic.neq": ("不等于", False),
                 "logic.gt": ("大于", False), "logic.gte": ("大于等于", True),
                 "logic.lt": ("小于", False), "logic.lte": ("小于等于", True)}
        for key, (title, _) in cases.items():
            item = self.catalog.definition(key)
            self.assertEqual(item["title"], title)
            self.assertEqual(item["op"], key.split(".")[1])
            self.assertEqual([p["type"] for p in item["outputs"]], [EF.BOOL])
        for text, want in (("等于", "logic.eq"), ("小于等于", "logic.lte"),
                           ("xiaoyudengyu", "logic.lte"), ("gte", "logic.gte"),
                           ("budengyu", "logic.neq")):
            hits = [r["def"]["key"] for r in self.catalog.search(EF.PAGE_INPUT, text)]
            self.assertIn(want, hits, text)


class GraphConnectionTests(unittest.TestCase):

    def setUp(self):
        self.catalog = _catalog()
        self.graph = EF.FlowGraph(EF.PAGE_INPUT)

    def _const(self, value=50.0) -> EF.FlowNode:
        node = self.graph.add_node(self.catalog, "var.const_float", 0.0, 0.0)
        node.params["v"] = value
        return node

    def test_writable_param_card_keeps_one_data_wire(self):
        sink = self.graph.add_node(self.catalog, f"core.write.{STRENGTH}", 400.0, 0.0)
        self.graph.connect(self.catalog, self._const(40.0), 0, sink, 0)
        second = self._const(90.0)
        self.graph.connect(self.catalog, second, 0, sink, 0)
        wires = self.graph.wires_into(sink.id, 0)
        self.assertEqual([w.src[0] for w in wires], [second.id])

    def test_output_card_can_fan_out(self):
        graph = EF.FlowGraph(EF.PAGE_OUTPUT)
        source = graph.add_node(self.catalog, "core.read.COYOTE.Battery", 0.0, 0.0)
        first = graph.add_node(self.catalog, "var.temp_write", 300.0, 0.0)
        second = graph.add_node(self.catalog, "var.temp_write", 300.0, 120.0)
        graph.connect(self.catalog, source, 0, first, 0)
        wire, why = graph.connect(self.catalog, source, 0, second, 0)
        self.assertIsNotNone(wire, why)
        self.assertEqual(len(graph.wires_from(source.id, 0)), 2)

    def test_pages_do_not_cross_connect(self):
        sink = self.graph.add_node(self.catalog, f"core.write.{STRENGTH}", 400.0, 0.0)
        other = EF.FlowGraph(EF.PAGE_OUTPUT)
        foreign = other.add_node(self.catalog, "core.read.COYOTE.Battery", 0.0, 0.0)
        ok, why = self.graph.can_connect(self.catalog, foreign, 0, sink, 0)
        self.assertFalse(ok)
        self.assertIn("写入变量", why)

    def test_exec_wire_requires_exec_target_and_rejects_loops(self):
        driver = self.graph.add_node(self.catalog, "mod.period", 0.0, 0.0)
        sink = self.graph.add_node(self.catalog, f"core.write.{STRENGTH}", 300.0, 0.0)
        wire, why = self.graph.connect(self.catalog, driver, 0, sink, -1)
        self.assertIsNotNone(wire, why)
        self.assertEqual(wire.type, EF.EXEC)
        ok, why = self.graph.connect(self.catalog, sink, 0, driver, -1)
        self.assertFalse(ok)

    def test_data_loop_is_rejected(self):
        first = self.graph.add_node(self.catalog, "math.add", 0.0, 0.0)
        second = self.graph.add_node(self.catalog, "math.add", 300.0, 0.0)
        self.graph.connect(self.catalog, first, 0, second, 0)
        ok, why = self.graph.can_connect(self.catalog, second, 0, first, 0)
        self.assertFalse(ok)
        self.assertIn("环", why)


class RuntimeTests(unittest.TestCase):

    def setUp(self):
        self.catalog = _catalog()
        self.graphs = {page: EF.FlowGraph(page) for page in EF.PAGES}
        self.core: dict[str, float] = {"COYOTE.Battery": 72.0, "COYOTE.LimitA": 100.0}
        self.modules: dict[str, dict[str, float]] = {"osc_bridge": {"osc_strength": 10.0}}
        self.core_writes: list[tuple[str, int]] = []
        self.module_writes: list[tuple[str, str, float]] = []
        self.temps: dict[str, float] = {}
        self.runtime = EF.FlowRuntime(
            self.catalog, self.graphs,
            read_core=lambda: dict(self.core),
            read_modules=lambda: {m: dict(s) for m, s in self.modules.items()},
            write_core=lambda key, value: self.core_writes.append((key, value)),
            write_module=lambda mid, name, value: self.module_writes.append((mid, name, value)),
            temps=self.temps)

    def _graph(self, page: str) -> EF.FlowGraph:
        return self.graphs[page]

    def _input(self, def_key: str, x: float = 0.0, y: float = 0.0) -> EF.FlowNode:
        return self.graphs[EF.PAGE_INPUT].add_node(self.catalog, def_key, x, y)

    def test_variable_card_reads_and_writes_immediately(self):
        """拖出来的变量卡直接读写：读数卡出当前值，写入卡收数值。"""
        self.catalog.refresh(MODULES, 1, user_vars=[{"name": "MyVar"},
                                                    {"name": "Other"},
                                                    {"name": "Sink2"}])
        graph = self._graph(EF.PAGE_INPUT)
        self.temps["MyVar"] = 12.0
        read = graph.add_node(self.catalog, "var.read.MyVar", 0.0, 0.0)
        other = graph.add_node(self.catalog, "var.write.Other", 260.0, 0.0)
        wire, why = graph.connect(self.catalog, read, 0, other, 0)
        self.assertIsNotNone(wire, why)
        const = graph.add_node(self.catalog, "var.const_float", 0.0, 160.0)
        const.params["v"] = 66.0
        sink = graph.add_node(self.catalog, "var.write.Sink2", 260.0, 160.0)
        wire, why = graph.connect(self.catalog, const, 0, sink, 0)
        self.assertIsNotNone(wire, why)
        self.runtime.tick(now=0.0)
        self.assertEqual(self.temps["Other"], 12.0)
        self.assertEqual(self.temps["Sink2"], 66.0)

    def test_untriggered_sink_writes_every_beat_and_dedupes(self):
        graph = self._graph(EF.PAGE_INPUT)
        const = graph.add_node(self.catalog, "var.const_float", 0.0, 0.0)
        const.params["v"] = 55.0
        sink = graph.add_node(self.catalog, f"core.write.{STRENGTH}", 300.0, 0.0)
        graph.connect(self.catalog, const, 0, sink, 0)
        self.runtime.tick(now=0.0)
        self.runtime.tick(now=0.05)
        self.assertEqual(self.core_writes, [(STRENGTH, 55)])
        self.runtime.armed = False
        self.runtime.tick(now=0.10)
        self.assertEqual(self.core_writes, [(STRENGTH, 55)])

    def test_core_write_respects_channel_limit_and_range(self):
        graph = self._graph(EF.PAGE_INPUT)
        const = graph.add_node(self.catalog, "var.const_float", 0.0, 0.0)
        const.params["v"] = 180.0
        sink = graph.add_node(self.catalog, f"core.write.{STRENGTH}", 300.0, 0.0)
        graph.connect(self.catalog, const, 0, sink, 0)
        self.runtime.tick(now=0.0)
        self.assertEqual(self.core_writes, [(STRENGTH, 100)])

    def test_period_driver_fires_on_its_own_beat(self):
        graph = self._graph(EF.PAGE_INPUT)
        driver = graph.add_node(self.catalog, "mod.period", 0.0, 0.0)
        driver.params["period_ms"] = 100.0
        counter = graph.add_node(self.catalog, "var.temp_write", 300.0, 0.0)
        counter.params["name"] = "beats"
        graph.connect(self.catalog, driver, 0, counter, -1)
        read = graph.add_node(self.catalog, "var.temp_read", 300.0, 90.0)
        read.params["name"] = "beats"
        add = graph.add_node(self.catalog, "math.add", 560.0, 0.0)
        one = graph.add_node(self.catalog, "var.const_float", 400.0, 90.0)
        one.params["v"] = 1.0
        graph.connect(self.catalog, read, 0, add, 0)
        graph.connect(self.catalog, one, 0, add, 1)
        graph.connect(self.catalog, add, 0, counter, 0)
        self.runtime.tick(now=0.0)
        self.assertAlmostEqual(self.temps["beats"], 1.0)
        self.runtime.tick(now=0.05)
        self.assertAlmostEqual(self.temps["beats"], 1.0)
        self.runtime.tick(now=0.10)
        self.assertAlmostEqual(self.temps["beats"], 2.0)

    def test_change_driver_fires_only_on_moving_value(self):
        graph = self._graph(EF.PAGE_INPUT)
        driver = graph.add_node(self.catalog, "mod.change", 300.0, 0.0)
        watch = graph.add_node(self.catalog,
                               "mod.read.osc_bridge.osc_strength", 0.0, 0.0)
        graph.connect(self.catalog, watch, 0, driver, 0)
        sink = graph.add_node(self.catalog, "var.temp_write", 600.0, 0.0)
        sink.params["name"] = "seen"
        graph.connect(self.catalog, driver, 0, sink, -1)
        graph.connect(self.catalog, driver, 1, sink, 0)
        self.runtime.tick(now=0.0)
        self.assertNotIn("seen", self.temps)
        self.runtime.tick(now=0.05)
        self.assertNotIn("seen", self.temps)
        self.modules["osc_bridge"]["osc_strength"] = 44.0
        self.runtime.tick(now=0.10)
        self.assertAlmostEqual(self.temps["seen"], 44.0)

    def test_change_driver_stands_by_without_a_wire(self):
        graph = self._graph(EF.PAGE_INPUT)
        driver = graph.add_node(self.catalog, "mod.change", 0.0, 0.0)
        sink = graph.add_node(self.catalog, "var.temp_write", 300.0, 0.0)
        sink.params["name"] = "seen"
        graph.connect(self.catalog, driver, 0, sink, -1)
        graph.connect(self.catalog, driver, 1, sink, 0)
        self.runtime.tick(now=0.0)
        self.runtime.tick(now=0.05)
        self.assertNotIn("seen", self.temps)

    def test_branch_node_selects_value_and_exec_path(self):
        graph = self._graph(EF.PAGE_INPUT)
        driver = graph.add_node(self.catalog, "mod.period", 0.0, 0.0)
        driver.params["period_ms"] = 50.0
        branch = graph.add_node(self.catalog, "flow.branch", 260.0, 0.0)
        graph.connect(self.catalog, driver, 0, branch, -1)
        truth = graph.add_node(self.catalog, "var.const_bool", 40.0, 120.0)
        graph.connect(self.catalog, truth, 0, branch, 0)
        low = graph.add_node(self.catalog, "var.const_float", 260.0, 120.0)
        low.params["v"] = 11.0
        high = graph.add_node(self.catalog, "var.const_float", 260.0, 200.0)
        high.params["v"] = 99.0
        graph.connect(self.catalog, high, 0, branch, 1)
        graph.connect(self.catalog, low, 0, branch, 2)
        hit = graph.add_node(self.catalog, "var.temp_write", 560.0, 0.0)
        hit.params["name"] = "hit"
        miss = graph.add_node(self.catalog, "var.temp_write", 560.0, 90.0)
        miss.params["name"] = "miss"
        graph.connect(self.catalog, branch, 0, hit, -1)
        graph.connect(self.catalog, branch, 1, miss, -1)
        graph.connect(self.catalog, branch, 2, hit, 0)
        graph.connect(self.catalog, branch, 2, miss, 0)
        self.runtime.tick(now=0.0)
        self.assertEqual(self.temps.get("hit"), 99.0)
        self.assertNotIn("miss", self.temps)
        truth.params["v"] = False
        self.runtime.tick(now=0.05)
        self.assertEqual(self.temps.get("miss"), 11.0)
        self.assertEqual(self.temps.get("hit"), 99.0)

    def test_module_cards_read_and_write_module_side(self):
        input_graph = self._graph(EF.PAGE_INPUT)
        reader = input_graph.add_node(self.catalog,
                                      "mod.read.osc_bridge.osc_strength", 0.0, 0.0)
        sink = input_graph.add_node(self.catalog, f"core.write.{STRENGTH}", 300.0, 0.0)
        input_graph.connect(self.catalog, reader, 0, sink, 0)
        output_graph = self._graph(EF.PAGE_OUTPUT)
        source = output_graph.add_node(self.catalog, "core.read.COYOTE.Battery", 0.0, 0.0)
        writer = output_graph.add_node(self.catalog,
                                       "mod.write.osc_bridge.osc_strength", 300.0, 0.0)
        output_graph.connect(self.catalog, source, 0, writer, 0)
        self.runtime.tick(now=0.0)
        self.assertEqual(self.core_writes, [(STRENGTH, 10)])
        self.assertEqual(self.module_writes, [("osc_bridge", "osc_strength", 72.0)])

    def test_free_formula_chain_feeds_variable_consumers(self):
        graph = self._graph(EF.PAGE_INPUT)
        formula = graph.add_node(self.catalog, "expr.free", 0.0, 0.0)
        formula.params["expr"] = "{COYOTE.Battery} * 0.5"
        writer = graph.add_node(self.catalog, "var.temp_write", 0.0, 120.0)
        writer.params["name"] = "smooth"
        graph.connect(self.catalog, formula, 0, writer, 0)
        sink = graph.add_node(self.catalog, f"core.write.{STRENGTH}", 300.0, 240.0)
        read = graph.add_node(self.catalog, "var.temp_read", 0.0, 240.0)
        read.params["name"] = "smooth"
        graph.connect(self.catalog, read, 0, sink, 0)
        self.runtime.tick(now=0.0)
        self.assertAlmostEqual(self.temps["smooth"], 36.0)
        self.assertEqual(self.core_writes, [(STRENGTH, 36)])

    def test_free_expression_sees_core_module_and_temp_vars(self):
        graph = self._graph(EF.PAGE_INPUT)
        formula = graph.add_node(self.catalog, "expr.free", 0.0, 0.0)
        formula.params["expr"] = "{COYOTE.Battery} + {osc_strength}"
        sink = graph.add_node(self.catalog, "var.temp_write", 300.0, 0.0)
        sink.params["name"] = "sum"
        graph.connect(self.catalog, formula, 0, sink, 0)
        self.runtime.tick(now=0.0)
        self.assertAlmostEqual(self.temps["sum"], 82.0)

    def test_missing_card_is_reported_without_breaking_the_beat(self):
        graph = self._graph(EF.PAGE_INPUT)
        ghost = graph.add_node(self.catalog, "core.write.removed_param", 0.0, 0.0)
        self.runtime.tick(now=0.0)
        self.assertEqual(ghost.def_key, "core.write.removed_param")
        self.assertEqual(self.catalog.definition(ghost.def_key), EF._MISSING)
        self.assertEqual(self.runtime.stats["nodes"], 1)

    def test_comparison_ops_evaluate_with_tolerance(self):
        table = [("eq", 5.0, 5.0, True), ("eq", 5.0, 5.0000005, True),
                 ("eq", 5.0, 5.001, False), ("neq", 5.0, 4.0, True),
                 ("neq", 5.0, 5.0, False), ("gt", 5.0, 4.9999995, False),
                 ("gt", 6.0, 5.0, True), ("gte", 5.0, 5.0, True),
                 ("gte", 4.9, 5.0, False), ("lt", 4.9, 5.0, True),
                 ("lt", 5.0, 5.0, False), ("lte", 5.0, 5.0, True),
                 ("lte", 5.1, 5.0, False)]
        for op, a, b, expected in table:
            graph = self._graph(EF.PAGE_INPUT)
            node = graph.add_node(self.catalog, f"logic.{op}", 0.0, 0.0)
            node.overrides[0] = a
            node.overrides[1] = b
            middle = graph.add_node(self.catalog, "util.reroute", 150.0, 0.0)
            sink = graph.add_node(self.catalog, "var.temp_write", 300.0, 0.0)
            sink.params["name"] = "out"
            graph.connect(self.catalog, node, 0, middle, 0)
            graph.connect(self.catalog, middle, 0, sink, 0)
            self.runtime.tick(now=0.0)
            self.assertEqual(self.temps.get("out"), float(expected), (op, a, b))

    def test_legacy_variable_expression_upgrades_to_formula_chain(self):
        raw = {"graphs": {"input": {"nodes": [
            {"id": "i1", "def": "var.temp_expr", "x": 10.0, "y": 20.0,
             "params": {"name": "smooth", "expr": "{COYOTE.Battery} * 0.5"}}],
            "wires": []}, "output": {"nodes": [], "wires": []}}}
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "event_flow.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(raw, handle, ensure_ascii=False)
            _active, profiles = EF.load_profiles(path)
        graph = profiles[EF.DEFAULT_PROFILE][EF.PAGE_INPUT]
        ops = [self.catalog.definition(n.def_key)["op"] for n in graph.nodes]
        self.assertEqual(ops, ["free_expr", "temp_write"])
        names = [n.params.get("name") for n in graph.nodes]
        self.assertEqual(names, [None, "smooth"])
        self.assertTrue(any(w.src == ("i1", 0) and w.dst[0] != "i1"
                            for w in graph.wires))


class PersistenceTests(unittest.TestCase):

    def test_graphs_round_trip_through_config_file(self):
        catalog = _catalog()
        graphs = {page: EF.FlowGraph(page) for page in EF.PAGES}
        input_graph = graphs[EF.PAGE_INPUT]
        const = input_graph.add_node(catalog, "var.const_float", 12.0, 8.0)
        const.params["v"] = 66.0
        const.alias = "手动强度"
        sink = input_graph.add_node(catalog, f"core.write.{STRENGTH}", 320.0, 8.0)
        input_graph.connect(catalog, const, 0, sink, 0)
        output_graph = graphs[EF.PAGE_OUTPUT]
        source = output_graph.add_node(catalog, "core.read.COYOTE.Battery", 0.0, 0.0)
        writer = output_graph.add_node(catalog, "var.temp_write", 300.0, 0.0)
        writer.params["name"] = "bat"
        output_graph.connect(catalog, source, 0, writer, 0)

        handle, path = tempfile.mkstemp(suffix=".json")
        os.close(handle)
        try:
            self.assertTrue(EF.save_graphs(graphs, path))
            loaded = EF.load_graphs(path)
        finally:
            os.remove(path)
        replayed = loaded[EF.PAGE_INPUT]
        self.assertEqual(len(replayed.nodes), 2)
        self.assertEqual(replayed.nodes[0].alias, "手动强度")
        self.assertEqual(replayed.nodes[0].params["v"], 66.0)
        self.assertEqual(len(replayed.wires), 1)
        self.assertEqual(replayed.wires[0].dst, (replayed.nodes[1].id, 0))
        self.assertNotEqual(loaded[EF.PAGE_OUTPUT].nodes[0].id, replayed.nodes[0].id)

    def test_load_recovers_next_id_past_saved_numbers(self):
        catalog = _catalog()
        graph = EF.FlowGraph(EF.PAGE_INPUT)
        saved = {"page": EF.PAGE_INPUT,
                 "nodes": [{"id": "i9", "def": "var.const_float", "x": 0, "y": 0,
                            "params": {}, "alias": "", "overrides": {}}],
                 "wires": [{"id": "iw7", "src": ["i9", 0], "dst": ["i9", 1],
                            "type": EF.FLOAT}]}
        restored = EF.FlowGraph.from_dict(saved)
        self.assertEqual(restored.new_id(), "i10")
        self.assertEqual(restored.new_id("w"), "iw11")


class ProfileTests(unittest.TestCase):

    def setUp(self):
        self.catalog = _catalog()
        handle, self.path = tempfile.mkstemp(suffix=".json")
        os.close(handle)
        self.addCleanup(_remove, self.path)

    def _graphs(self, def_key: str, value: float):
        graphs = {page: EF.FlowGraph(page) for page in EF.PAGES}
        graph = graphs[EF.PAGE_INPUT]
        node = graph.add_node(self.catalog, def_key, 0.0, 0.0)
        node.params["v"] = value
        return graphs

    def test_profiles_round_trip_keeps_active_profile(self):
        profiles = {"默认": self._graphs("var.const_float", 11.0),
                    "演出": self._graphs("var.const_float", 22.0)}
        self.assertTrue(EF.save_profiles("演出", profiles, self.path))
        active, loaded = EF.load_profiles(self.path)
        self.assertEqual(active, "演出")
        self.assertEqual(sorted(loaded), ["演出", "默认"])
        self.assertAlmostEqual(
            loaded["演出"][EF.PAGE_INPUT].nodes[0].params["v"], 22.0)
        self.assertAlmostEqual(
            loaded["默认"][EF.PAGE_INPUT].nodes[0].params["v"], 11.0)

    def test_legacy_single_profile_file_becomes_default(self):
        graphs = self._graphs("var.const_float", 33.0)
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump({"version": 2,
                       "graphs": {page: g.to_dict() for page, g in graphs.items()}},
                      handle, ensure_ascii=False)
        active, profiles = EF.load_profiles(self.path)
        self.assertEqual(active, EF.DEFAULT_PROFILE)
        self.assertEqual(list(profiles), [EF.DEFAULT_PROFILE])
        self.assertAlmostEqual(
            EF.load_graphs(self.path)[EF.PAGE_INPUT].nodes[0].params["v"], 33.0)
        self.assertTrue(EF.save_graphs(graphs, self.path))
        with open(self.path, encoding="utf-8") as handle:
            payload = json.load(handle)
        self.assertEqual(payload["version"], 3)
        self.assertEqual(list(payload["profiles"]), [EF.DEFAULT_PROFILE])

    def test_missing_file_yields_one_empty_default(self):
        active, profiles = EF.load_profiles(self.path + ".ghost")
        self.assertEqual((active, list(profiles)), (EF.DEFAULT_PROFILE, ["默认"]))
        self.assertEqual(profiles["默认"][EF.PAGE_INPUT].nodes, [])

    def test_runtime_new_profile_clones_and_switches(self):
        runtime = EF.FlowRuntime(self.catalog, self._graphs("var.const_float", 5.0))
        made = runtime.new_profile()
        self.assertEqual((runtime.active, made), ("配置1", "配置1"))
        clone = runtime.graphs[EF.PAGE_INPUT].nodes[0]
        clone.params["v"] = 9.0
        self.assertAlmostEqual(
            runtime.profiles[EF.DEFAULT_PROFILE][EF.PAGE_INPUT].nodes[0].params["v"],
            5.0)
        self.assertEqual(runtime.new_profile(), "配置2")

    def test_runtime_switch_and_rename_profiles(self):
        runtime = EF.FlowRuntime(self.catalog, self._graphs("var.const_float", 5.0))
        runtime.new_profile("演出")
        self.assertTrue(runtime.switch_profile(EF.DEFAULT_PROFILE))
        self.assertEqual(runtime.graphs, runtime.profiles[EF.DEFAULT_PROFILE])
        self.assertFalse(runtime.switch_profile("不存在"))
        self.assertEqual(runtime.rename_profile("演出", "晚会"), "")
        self.assertIn("晚会", runtime.profile_names)
        self.assertNotIn("演出", runtime.profile_names)
        self.assertIn("晚会", runtime.rename_profile(EF.DEFAULT_PROFILE, "晚会"))
        self.assertIn("不能为空", runtime.rename_profile(EF.DEFAULT_PROFILE, "  "))
        runtime.active = EF.DEFAULT_PROFILE
        runtime.graphs = runtime.profiles[EF.DEFAULT_PROFILE]
        runtime.rename_profile(EF.DEFAULT_PROFILE, "主用")
        self.assertEqual((runtime.active, runtime.graphs),
                         ("主用", runtime.profiles["主用"]))

    def test_profile_modules_lists_referenced_module_ids(self):
        graphs = self._graphs("mod.read.osc_bridge.osc_strength", 1.0)
        graph = graphs[EF.PAGE_OUTPUT]
        graph.add_node(self.catalog, "mod.read.osc_bridge.osc_strength", 0.0, 0.0)
        graph.add_node(self.catalog, "mod.write.osc_bridge.osc_strength", 100.0, 0.0)
        graph.add_node(self.catalog, "var.const_float", 200.0, 0.0)
        self.assertEqual(EF.profile_modules(graphs), ["osc_bridge"])
        self.assertEqual(EF.profile_modules(self._graphs("var.const_float", 1.0)), [])


class MigrationTests(unittest.TestCase):

    def test_legacy_temp_table_and_cards_become_flow_nodes(self):
        catalog = _catalog()
        graphs = {page: EF.FlowGraph(page) for page in EF.PAGES}
        created = EF.migrate_legacy(catalog, graphs, [(
            "osc_bridge",
            [{"name": "按键判定", "trigger": "if", "arg": "{Action} == 3",
              "actions": [
                  {"dir": "in", "param": STRENGTH, "var": "COYOTE.LimitA"},
                  {"dir": "out", "param": "COYOTE.Battery", "var": "bat",
                   "name": "osc_strength"}]},
             {"name": "节拍", "trigger": "period", "arg": 200,
              "actions": [{"dir": "in", "param": STRENGTH, "var": "bat"}]}],
            [{"name": "smooth", "expr": "{COYOTE.StrengthA} * 0.5"}])])
        self.assertGreater(created, 0)
        input_graph = graphs[EF.PAGE_INPUT]
        output_graph = graphs[EF.PAGE_OUTPUT]
        ops = [catalog.definition(n.def_key)["op"] for n in input_graph.nodes]
        self.assertNotIn("temp_expr", ops)
        self.assertIn("free_expr", ops)
        self.assertIn("driver_period", ops)
        self.assertIn("branch", ops)
        self.assertEqual(catalog.definition(
            input_graph.nodes[0].def_key)["op"], "free_expr")
        self.assertIn("core_read", [catalog.definition(n.def_key)["op"]
                                    for n in output_graph.nodes])
        self.assertIn("temp_write", [catalog.definition(n.def_key)["op"]
                                     for n in output_graph.nodes])

        writes: list[tuple[str, int]] = []
        runtime = EF.FlowRuntime(catalog, graphs,
                                 read_core=lambda: {"COYOTE.LimitA": 80.0,
                                                    "COYOTE.StrengthA": 40.0,
                                                    "Action": 3.0},
                                 read_modules=lambda: {},
                                 write_core=lambda k, v: writes.append((k, v)),
                                 write_module=lambda *a: None,
                                 temps={})
        runtime.tick(now=0.0)
        self.assertAlmostEqual(runtime.temps["smooth"], 20.0)
        self.assertIn((STRENGTH, 80), writes)

    def test_unsupported_legacy_triggers_are_skipped(self):
        catalog = _catalog()
        graphs = {page: EF.FlowGraph(page) for page in EF.PAGES}
        created = EF.migrate_legacy(catalog, graphs, [
            ("osc_bridge", [{"trigger": "unknown", "actions": []}], [])])
        self.assertEqual(created, 0)
        self.assertEqual(graphs[EF.PAGE_INPUT].nodes, [])

    def test_legacy_per_module_driver_cards_upgrade_on_load(self):
        raw = {"graphs": {"input": {"nodes": [
            {"id": "i1", "def": "mod.change.osc_bridge", "x": 0.0, "y": 0.0,
             "params": {"module": "osc_bridge", "var": "osc_strength"}},
            {"id": "i2", "def": "mod.period.osc_bridge", "x": 0.0, "y": 220.0,
             "params": {"module": "osc_bridge", "period_ms": 200}},
        ], "wires": []}, "output": {"nodes": [], "wires": []}}}
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "event_flow.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(raw, handle, ensure_ascii=False)
            _active, profiles = EF.load_profiles(path)
        graph = profiles[EF.DEFAULT_PROFILE][EF.PAGE_INPUT]
        keys = sorted(node.def_key for node in graph.nodes)
        self.assertEqual(keys, ["mod.change", "mod.period", "var.temp_read"])
        change = next(n for n in graph.nodes if n.def_key == "mod.change")
        guard = next(n for n in graph.nodes if n.def_key == "var.temp_read")
        period = next(n for n in graph.nodes if n.def_key == "mod.period")
        self.assertEqual(guard.params["name"], "osc_strength")
        self.assertEqual(period.params.get("period_ms"), 200)
        self.assertNotIn("module", change.params)
        self.assertEqual([(w.src, w.dst) for w in graph.wires],
                         [((guard.id, 0), (change.id, 0))])


class _FakeCfg(dict):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.saved = 0

    def save(self) -> None:
        self.saved += 1


class _FakeModule:

    def __init__(self):
        self.renamed: list[tuple[str, str]] = []

    def link_params(self):
        return [{"name": "osc_strength", "label": "OSC 强度", "dir": "out"},
                {"name": "osc_hp", "label": "HP", "dir": "in"}]

    def read_params(self):
        return [{"name": "osc_fps", "label": "帧率", "dir": "in"}]

    def rename_var(self, old, new):
        self.renamed.append((str(old), str(new)))
        return ""


class _FakeModules:

    def __init__(self):
        self.temps: dict[str, float] = {}
        self.cfgs: dict[str, _FakeCfg] = {}
        self.metas = [{"id": "osc_bridge", "name": "OSC 桥接模块"}]
        self.inst = _FakeModule()
        self.specs: list[dict] = []
        self.enabled: set[str] | None = None

    def temps_space(self, module_id: str = "") -> dict[str, float]:
        return self.temps

    def list_modules(self):
        return [dict(m) for m in self.metas]

    def is_enabled(self, module_id: str) -> bool:
        return True if self.enabled is None else module_id in self.enabled

    def instance(self, module_id: str):
        return self.inst if module_id == "osc_bridge" else None

    def _mapping_engine(self, module_id: str):
        return None

    def temp_specs_for(self, module_id: str):
        return [dict(spec) for spec in self.specs]

    def set_temp(self, module_id: str, key: str, value) -> None:
        self.temps[str(key)] = float(value)

    def settings_for(self, module_id: str) -> dict:
        return self.cfgs.setdefault(module_id, _FakeCfg())


class _Future:

    def __init__(self, error=None):
        self._error = error

    def cancelled(self) -> bool:
        return False

    def exception(self):
        return self._error

    def add_done_callback(self, callback) -> None:
        callback(self)


class _FakeEngine:
    """只提供事件流宿主用到的那部分 Engine 表面。"""

    def __init__(self, base_dir: str):
        self.modules = _FakeModules()
        self.config = SimpleNamespace(
            path=os.path.join(base_dir, "config", "config.json"))
        self.loop = None
        self.backend = "socket_v4"
        self.logs: list[str] = []
        self.strength: list[tuple] = []
        self.waves: list[tuple] = []
        self.fires: list[tuple] = []
        self.pulses: list[tuple] = []
        self.emergency = 0
        self.state = EngineState(
            backend="socket_v4", connected=True, paired=True, last_action=3,
            slots={
                "1": Slot(slot_id="1", type="COYOTE", battery=72.0,
                          strength={"A": 30, "B": 0}),
                "2": Slot(slot_id="2", type="COYOTE", battery=55.0),
                "3": Slot(slot_id="3", type="BMTR", pressure=1.25),
            })

    @property
    def backend_kind(self) -> str:
        return self.backend

    def get_state(self) -> EngineState:
        return self.state

    def resolve_slot(self, slot_id=None, family=None, output_only=False):
        want = str(family or "COYOTE").upper()
        for sid in sorted(self.state.slots):
            if family_of(self.state.slots[sid].type) == want:
                return sid
        return None

    def wave_selection(self) -> dict:
        return {"A": "silent", "B": "silent"}

    async def set_strength(self, channel, value, slot_id=None):
        self.strength.append((channel, value, slot_id))

    async def set_wave(self, channel, name, slot_id=None):
        self.waves.append((channel, name, slot_id))

    async def fire_start(self, slot_id=None, channel=None):
        self.fires.append(("start", channel, slot_id))

    async def fire_stop(self, slot_id=None, channel=None):
        self.fires.append(("stop", channel, slot_id))

    async def push_pulse_stream(self, frequency, channel="A", level=100,
                                slot_id=None):
        self.pulses.append((channel, frequency, level, slot_id))

    async def emergency_stop(self):
        self.emergency += 1

    def submit(self, coro):
        try:
            asyncio.run(coro)
        except Exception as exc:      # noqa: BLE001 - 测试替身需要吞掉异常
            return _Future(exc)
        return _Future()

    def _log(self, text: str) -> None:
        self.logs.append(text)


class FlowHostTests(unittest.TestCase):

    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix="dgstudio_flow_host_")
        self.addCleanup(shutil.rmtree, self._dir, True)
        self.engine = _FakeEngine(self._dir)
        self.host = flow_host.FlowHost(self.engine)

    def _sink(self, page: str, def_key: str, source_def: str, value, **params):
        graph = self.host.runtime.graphs[page]
        source = graph.add_node(self.host.catalog, source_def, 0.0, 0.0)
        source.params["v"] = value
        sink = graph.add_node(self.host.catalog, def_key, 400.0, 0.0)
        for key, item in params.items():
            sink.params[key] = item
        graph.connect(self.host.catalog, source, 0, sink, 0)
        return source, sink

    def test_catalog_follows_enabled_modules_and_device_count(self):
        self.assertTrue(self.host.refresh(force=True))
        for key in ("mod.read.osc_bridge.osc_hp", "mod.read.osc_bridge.osc_fps",
                    "mod.write.osc_bridge.osc_strength", "mod.period"):
            self.assertTrue(self.host.catalog.known(key), key)
        self.assertTrue(self.host.catalog.known("core.read.COYOTE.2.Battery"))
        self.assertFalse(self.host.catalog.known("core.read.COYOTE.3.Battery"))
        self.assertFalse(self.host.refresh())

    def test_module_registered_temps_reach_catalog(self):
        self.host.refresh(force=True)
        # 方向按 OSC 路径：avatar/parameters/* 宿主可写，全局前缀参数宿主可读
        self.engine.modules.specs = [
            {"key": "avatar/parameters/DGLabStrengthA", "label": "OSC 可写",
             "dir": "out"},
            {"key": "DGLab/Action", "label": "OSC 可读", "dir": "in"}]
        self.assertTrue(self.host.refresh(), "新增临时变量应触发目录刷新")
        catalog = self.host.catalog
        read = catalog.definition("mod.read.osc_bridge.DGLab/Action")
        self.assertEqual((read["cat"], read["page"], read["op"]),
                         ("临时变量", EF.PAGE_INPUT, "mod_read"))
        self.assertTrue(catalog.known(
            "mod.write.osc_bridge.avatar/parameters/DGLabStrengthA"))
        self.assertFalse(catalog.known("mod.write.osc_bridge.DGLab/Action"))
        self.assertFalse(catalog.known(
            "mod.read.osc_bridge.avatar/parameters/DGLabStrengthA"))
        graph = self.host.runtime.graphs[EF.PAGE_INPUT]
        node = graph.add_node(catalog, read["key"], 0.0, 0.0)
        self.assertEqual(node.param(catalog, "name"), "DGLab/Action")
        self.engine.modules.set_temp("osc_bridge", node.param(catalog, "name"),
                                     42.0)
        self.host.runtime.tick(now=0.0)
        self.assertEqual(node.live, [42.0])

    def test_declared_params_inject_into_var_table(self):
        """模块登记即注入变量表：没有实时值也要出现，并带可读 / 可写方向。"""
        self.engine.modules.specs = [
            {"key": "avatar/parameters/DGLabStrengthA", "label": "OSC 可写",
             "dir": "out"},
            {"key": "DGLab/Action", "label": "OSC 可读", "dir": "in"}]
        self.host.refresh(force=True)
        system = {row["name"]: row for row in self.host.runtime.var_table()[0]}
        self.assertEqual(system["avatar/parameters/DGLabStrengthA"]["dir"], "out")
        self.assertIsNone(system["avatar/parameters/DGLabStrengthA"]["value"])
        self.assertEqual(system["DGLab/Action"]["dir"], "in")
        self.assertIn("osc_strength", system)

    def test_renamable_module_rows_never_enter_the_system_section(self):
        """OSC 那类标了 renamable 的行只进可改名栏，系统栏一行都不该有。"""
        self.engine.modules.specs = [
            {"key": "avatar/parameters/DGLabStrengthA", "label": "OSC 可写",
             "dir": "out", "renamable": True},
            {"key": "DGLab/Action", "label": "OSC 可读", "dir": "in",
             "renamable": True}]
        self.host.refresh(force=True)
        system, user = self.host.runtime.var_table()
        self.assertNotIn("avatar/parameters/DGLabStrengthA",
                         {row["name"] for row in system})
        self.assertNotIn("DGLab/Action", {row["name"] for row in system})
        by = {row["name"]: row for row in user}
        self.assertEqual(by["avatar/parameters/DGLabStrengthA"]["dir"], "out")
        self.assertEqual(by["DGLab/Action"]["dir"], "in")

    def test_renamable_row_wins_same_name_collision(self):
        """跨模块同名：可改名行不能被别模块的只读同名行顶进系统栏。"""
        rows = [{"id": "alice_cradle", "name": "Alice in Cradle 联动",
                 "params": [{"name": "HP", "dir": "in"}]},
                {"id": "osc_bridge", "name": "VRChat OSC 联动",
                 "params": [{"name": "HP", "dir": "out", "renamable": True}]}]
        declared = {row["name"]: row for row in flow_host._declared_vars(rows)}
        self.assertEqual(declared["HP"]["mid"], "osc_bridge")
        self.assertIs(declared["HP"]["renamable"], True)
        self.assertEqual(declared["HP"]["dir"], "out")
        flipped = list(reversed(rows))
        kept = {row["name"]: row for row in flow_host._declared_vars(flipped)}
        self.assertEqual(kept["HP"]["mid"], "osc_bridge")

    def test_var_table_excludes_core_device_readouts(self):
        """核心自己的设备读数（COYOTE.Battery / Action）不进变量表。"""
        self.host.refresh(force=True)
        system, user = self.host.runtime.var_table()
        names = {row["name"] for row in system} | {row["name"] for row in user}
        for key in ("COYOTE.Battery", "COYOTE.2.Battery", "Action"):
            self.assertNotIn(key, names)
            self.assertTrue(self.host.catalog.known(f"core.read.{key}"), key)

    def test_pool_dir_unions_entries_instead_of_overwriting(self):
        """同名参数多处登记时方向取并集：后登记的一行不能把先写的翻掉。"""
        rows = EF._mod_pool({"params": [
            {"name": "avatar/parameters/X", "dir": "out"},
            {"name": "avatar/parameters/X", "dir": "in"},
            {"name": "DGLab/Action", "dir": "in"},
            {"name": "DGLab/Action", "dir": "in"},
            {"name": "plain"}]})
        by = {row["name"]: row["dir"] for row in rows}
        self.assertEqual(by["avatar/parameters/X"], "inout")
        self.assertEqual(by["DGLab/Action"], "in")
        self.assertEqual(by["plain"], "in")

    def test_var_table_drops_stale_module_temps(self):
        """模块下线后残留在共享值空间里的名字不算登记，不能留在变量表。"""
        self.host.refresh(force=True)
        runtime = self.host.runtime
        runtime.temps["ghost_var"] = 5.0
        system, user = runtime.var_table()
        names = {row["name"] for row in system} | {row["name"] for row in user}
        self.assertNotIn("ghost_var", names)

    def test_renamable_rows_land_in_the_renamable_section(self):
        self.engine.modules.specs = [{"key": "avatar/parameters/X",
                                      "label": "OSC 可读", "dir": "in",
                                      "renamable": True}]
        self.host.refresh(force=True)
        system, user = self.host.runtime.var_table()
        self.assertNotIn("avatar/parameters/X", {row["name"] for row in system})
        self.assertIn("avatar/parameters/X", {row["name"] for row in user})

    def test_rename_renamable_row_goes_through_the_module(self):
        self.engine.modules.specs = [{"key": "avatar/parameters/X",
                                      "label": "OSC 可读", "dir": "in",
                                      "renamable": True}]
        self.host.refresh(force=True)
        runtime = self.host.runtime
        graph = runtime.graph(EF.PAGE_INPUT)
        card = graph.add_node(self.host.catalog,
                              "mod.read.osc_bridge.avatar/parameters/X",
                              0.0, 0.0)
        row = next(r for r in runtime.var_table()[1]
                   if r["name"] == "avatar/parameters/X")
        self.assertEqual(runtime.rename_var_row(row, "avatar/parameters/Y"), "")
        self.assertEqual(self.engine.modules.inst.renamed,
                         [("avatar/parameters/X", "avatar/parameters/Y")])
        self.assertEqual(card.def_key,
                         "mod.read.osc_bridge.avatar/parameters/Y")
        graph.remove_node(card)

    def test_rename_user_var_still_works(self):
        runtime = self.host.runtime
        self.assertEqual(runtime.add_user_var("Renamable"), "")
        self.assertEqual(runtime.rename_var_row({"name": "Renamable"}, "Renamed"),
                         "")
        self.assertIn("Renamed", {row["name"] for row in runtime.user_vars})
        self.assertTrue(runtime.remove_user_var("Renamed"))

    def test_user_vars_have_their_own_cards(self):
        catalog = EF.Catalog()
        catalog.refresh([], 1, user_vars=[{"name": "MyVar"},
                                          {"name": "avatar/parameters/X"}])
        read = catalog.definition("var.read.MyVar")
        self.assertEqual((read["op"], read["cat"]), ("temp_read", "临时变量"))
        self.assertEqual(read["fields"][0]["default"], "MyVar")
        write = catalog.definition("var.write.avatar/parameters/X")
        self.assertEqual(write["op"], "temp_write")
        self.assertTrue(write["sink"])
        self.assertEqual(catalog.definition("var.read.avatar/parameters/X")
                         ["title"], "读数 · X")

    def test_core_write_reaches_engine_backend_calls(self):
        self.host.refresh(force=True)
        self._sink(EF.PAGE_INPUT, f"core.write.{STRENGTH}",
                   "var.const_float", 60.0, key=STRENGTH)
        self.host.runtime.tick(now=0.0)
        self.assertIn(("A", 60, "1"), self.engine.strength)

    def test_core_write_is_skipped_without_backend(self):
        self.host.refresh(force=True)
        self._sink(EF.PAGE_INPUT, f"core.write.{STRENGTH}",
                   "var.const_float", 60.0, key=STRENGTH)
        self.engine.backend = "none"
        self.host.runtime.tick(now=0.0)
        self.assertEqual(self.engine.strength, [])

    def test_wave_fire_and_pulse_use_engine_methods(self):
        self.host.refresh(force=True)
        self._sink(EF.PAGE_INPUT, "core.write.in_wave_a",
                   "var.const_float", 1.0, key="in_wave_a")
        self._sink(EF.PAGE_INPUT, "core.write.in_pulse_a",
                   "var.const_float", 200.0, key="in_pulse_a")
        self.host.runtime.tick(now=0.0)
        order = wave_order("COYOTE")
        self.assertIn(("A", order[1], "1"), self.engine.waves)
        self.assertEqual(self.engine.pulses[0][:3], ("A", 200, 100))

    def test_module_write_lands_in_shared_temps(self):
        self.host.refresh(force=True)
        graph = self.host.runtime.graphs[EF.PAGE_OUTPUT]
        reader = graph.add_node(self.host.catalog, "core.read.COYOTE.Battery",
                                0.0, 0.0)
        writer = graph.add_node(self.host.catalog,
                                "mod.write.osc_bridge.osc_strength", 400.0, 0.0)
        graph.connect(self.host.catalog, reader, 0, writer, 0)
        self.host.runtime.tick(now=0.0)
        self.assertAlmostEqual(self.engine.modules.temps["osc_strength"], 72.0)
        self.assertIs(self.engine.modules.temps, self.host.runtime.temps)

    def test_legacy_module_settings_migrate_then_purge(self):
        cfg = self.host.engine.modules.settings_for("osc_bridge")
        cfg["temps"] = [{"name": "smooth", "expr": "{COYOTE.StrengthA} * 0.5"}]
        cfg["events"] = [{"name": "节拍", "trigger": "period", "arg": 200,
                          "actions": [{"dir": "in", "param": STRENGTH,
                                       "var": "smooth"}]}]
        created = flow_host.migrate_module_flows(self.host)
        self.assertGreater(created, 0)
        self.assertNotIn("events", cfg)
        self.assertNotIn("temps", cfg)
        self.assertGreaterEqual(cfg.saved, 1)
        self.assertTrue(os.path.isfile(self.host.runtime.path))
        ops = [self.host.catalog.definition(node.def_key)["op"]
               for node in self.host.runtime.graphs[EF.PAGE_INPUT].nodes]
        self.assertNotIn("temp_expr", ops)
        self.assertIn("free_expr", ops)
        self.assertIn("temp_write", ops)
        self.assertIn("driver_period", ops)

    def test_reset_clears_driver_edges(self):
        self.host.runtime._last_core["probe"] = 1
        self.host.reset()
        self.assertEqual(self.host.runtime._last_core, {})

    def test_missing_modules_flags_disabled_modules_only(self):
        graph = self.host.runtime.graphs[EF.PAGE_INPUT]
        graph.add_node(self.host.catalog,
                       "mod.read.osc_bridge.osc_strength", 0.0, 0.0)
        graph.add_node(self.host.catalog, "var.const_float", 200.0, 0.0)
        self.assertEqual(self.host.missing_modules(), [])
        self.engine.modules.enabled = set()
        self.assertEqual(self.host.missing_modules(), ["osc_bridge"])
        self.engine.modules.enabled = {"osc_bridge"}
        self.assertEqual(self.host.missing_modules(), [])

    def test_switch_profile_rebuilds_catalog_and_persists_active(self):
        graph = self.host.runtime.graphs[EF.PAGE_INPUT]
        graph.add_node(self.host.catalog,
                       "mod.read.osc_bridge.osc_strength", 0.0, 0.0)
        self.host.runtime.new_profile("演出")
        self.host.runtime.save()
        active, profiles = EF.load_profiles(self.host.runtime.path)
        self.assertEqual((active, sorted(profiles)), ("演出", ["演出", "默认"]))
        self.assertTrue(self.host.switch_profile(EF.DEFAULT_PROFILE))
        self.assertEqual(self.host.runtime.active, EF.DEFAULT_PROFILE)
        self.assertFalse(self.host.switch_profile("ghost"))
        active, _profiles = EF.load_profiles(self.host.runtime.path)
        self.assertEqual(active, EF.DEFAULT_PROFILE)
        self.engine.modules.enabled = set()
        self.host.refresh(force=True)
        self.assertTrue(self.host.catalog.known("mod.period"))
        self.assertFalse(self.host.catalog.known("mod.read.osc_bridge.osc_strength"))
        self.assertEqual(self.host.missing_modules(), ["osc_bridge"])


if __name__ == "__main__":
    unittest.main()

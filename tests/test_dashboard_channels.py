from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dglab import event_flow
from dglab.mapping import MappingEngine
from ui import live


def _flow_runtime(nodes=None, declared=None):
    catalog = event_flow.Catalog()
    graphs = {page: event_flow.FlowGraph(page) for page in event_flow.PAGES}
    graph = graphs[event_flow.PAGE_INPUT]
    for def_key, params in nodes or []:
        node = graph.add_node(catalog, def_key, 0.0, 0.0)
        node.params.update(params)
    return SimpleNamespace(catalog=catalog, graphs=graphs,
                           declared_vars=declared or [])


def _engine_with_module(signals=None, out_values=None, errors=None,
                        out_errors=None, mappings=1, outputs=1,
                        last_rx=None, cfg=None, temps=None, flow_nodes=None,
                        declared=None):
    eng = MappingEngine(lambda key, value: None)
    eng.signals.update(signals or {})
    eng.temps.update(temps or {})
    eng.out_values.update(out_values or {})
    eng.errors.update(errors or {})
    eng.out_errors.update(out_errors or {})
    eng.mappings = {f"in_{i}": "{x}" for i in range(mappings)}
    eng.outputs = [{"name": f"out{i}", "expr": "{x}", "type": "Int"}
                   for i in range(outputs)]
    server = SimpleNamespace(engine=eng, _last_rx=last_rx)
    inst = SimpleNamespace(server=server)
    host = SimpleNamespace(modules=SimpleNamespace(
        list_modules=lambda: [{"id": "alice_cradle",
                               "name": "Alice in Cradle 联动"}],
        instance=lambda mid: inst,
        settings_for=lambda mid: cfg or {}))
    return SimpleNamespace(modules=host.modules, osc=None,
                           flow=SimpleNamespace(
                               runtime=_flow_runtime(
                                   flow_nodes,
                                   declared or [
                                       {"name": f"out{i}", "mid": "alice_cradle",
                                        "dir": "out"}
                                       for i in range(outputs)]))), server


def _fresh():
    import time
    return time.monotonic()


class ModuleChannelRowTests(unittest.TestCase):
    def test_both_directions_probe_by_traffic(self):
        engine, _rt = _engine_with_module(signals={"HP": 5}, last_rx=_fresh())
        rows = live.module_channel_rows(engine)
        self.assertEqual([r["direction"] for r in rows],
                         ["模块→核心", "核心→模块"])
        self.assertEqual(rows[0]["probe"], "数据流动中")
        self.assertIs(rows[0]["probe_ok"], True)
        engine2, _rt2 = _engine_with_module(signals={"HP": 5},
                                          out_values={"f": 1},
                                          last_rx=None)
        rows2 = live.module_channel_rows(engine2)
        self.assertEqual(rows2[0]["probe"], "等待数据")
        self.assertIsNone(rows2[0]["probe_ok"])
        self.assertEqual(rows2[1]["probe"], "等待回传")

    def test_stale_traffic_waits(self):
        import time
        engine, _rt = _engine_with_module(signals={"HP": 5},
                                          last_rx=time.monotonic() - 30)
        rows = live.module_channel_rows(engine)
        self.assertEqual(rows[0]["probe"], "等待数据")

    def test_error_probe(self):
        engine, _rt = _engine_with_module(errors={"in_0": "除数为 0"})
        rows = live.module_channel_rows(engine)
        self.assertEqual(rows[0]["probe"], "异常")
        self.assertIs(rows[0]["probe_ok"], False)

    def test_no_engine_no_rows(self):
        engine = SimpleNamespace(modules=SimpleNamespace(
            list_modules=lambda: [], instance=lambda mid: None))
        self.assertEqual(live.module_channel_rows(engine), [])

    def test_running_module_without_mappings_shown(self):
        engine, _rt = _engine_with_module(mappings=0, outputs=0)
        rows = live.module_channel_rows(engine)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["direction"], "模块→核心")
        self.assertEqual(rows[0]["count"], 0)
        self.assertEqual(rows[0]["probe"], "未登记变量")

    def test_osc_without_mappings_still_listed_as_linkage(self):
        """OSC 是联动通道：没有映射也要出现在「联动模块（模块→核心）」里。"""
        eng = MappingEngine(lambda key, value: None)
        inst = SimpleNamespace(
            bridge=SimpleNamespace(engine=eng, _running=True,
                                   last_rx=None))
        engine = SimpleNamespace(osc=None, modules=SimpleNamespace(
            list_modules=lambda: [{"id": "osc_bridge",
                                   "name": "VRChat OSC 联动",
                                   "enabled": True}],
            instance=lambda mid: inst))
        rows = live.module_channel_rows(engine)
        self.assertEqual([(r["module"], r["direction"], r["probe"])
                          for r in rows],
                         [("VRChat OSC 联动", "模块→核心", "未登记变量")])

    def test_link_counts_include_module_channels(self):
        engine, _rt = _engine_with_module(signals={"HP": 5}, last_rx=_fresh())
        counts = live.link_counts(engine, SimpleNamespace(slots={}))
        self.assertIn(("Alice in Cradle 联动 · 模块→核心", "登记变量 1 个"),
                      counts["input"])
        self.assertIn(("Alice in Cradle 联动 · 核心→模块", "回传参数 1 个"),
                      counts["output"])


class OscDedupTests(unittest.TestCase):

    def _osc_engine(self, running=True, enabled=True):
        eng = MappingEngine(lambda key, value: None)
        eng.mappings = {"in_strength_a": "{x}"}
        eng.outputs = [{"name": "DGLabAction", "expr": "{Action}",
                        "type": "Int"}]
        inst = SimpleNamespace(
            bridge=SimpleNamespace(engine=eng, _running=running,
                                   last_rx=None))
        declared = [{"name": "DGLab/Action", "mid": "osc_bridge",
                     "dir": "out"}]
        return SimpleNamespace(
            osc=None,
            flow=SimpleNamespace(runtime=SimpleNamespace(
                declared_vars=declared)),
            modules=SimpleNamespace(
                list_modules=lambda: [
                    {"id": "alice_cradle", "name": "AIC"},
                    {"id": "osc_bridge", "name": "VRChat OSC 联动",
                     "enabled": enabled, "config": {"out_port": {}}}],
                instance=lambda mid: inst if mid == "osc_bridge" else None))

    def test_osc_never_listed_as_plain_input_channel(self):
        """OSC 不再冒充设备 / 服务链路：三种状态下都不出现在输入通道列表。"""
        for running, enabled in ((True, True), (False, True), (False, False)):
            rows = live.input_channel_rows(self._osc_engine(running=running,
                                                            enabled=enabled),
                                           SimpleNamespace(slots={}))
            self.assertNotIn("VRChat OSC 输入", [r["name"] for r in rows])

    def test_osc_unloaded_module_shows_start_hint(self):
        """装了没启动：和别的联动模块一样给「去模块页启动」提示行。"""
        engine = SimpleNamespace(osc=None, modules=SimpleNamespace(
            list_modules=lambda: [{"id": "osc_bridge",
                                   "name": "VRChat OSC 联动",
                                   "enabled": True, "config": {"out_port": {}}}],
            instance=lambda mid: None))
        rows = live.input_channel_rows(engine, SimpleNamespace(slots={}))
        entry = next((r for r in rows if r["name"] == "VRChat OSC 联动"), None)
        self.assertIsNotNone(entry)
        self.assertFalse(entry["enabled"])
        self.assertIn("启动", entry["hint"])

    def test_link_counts_do_not_double_count_osc(self):
        engine = self._osc_engine(running=True)
        counts = live.link_counts(engine, SimpleNamespace(slots={}))
        self.assertEqual([name for name, _d in counts["input"]
                          if "OSC" in name],
                         ["VRChat OSC 联动 · 模块→核心"])
        self.assertEqual([name for name, _d in counts["output"]
                          if "OSC" in name],
                         ["VRChat OSC 联动 · 核心→模块"])


class ModuleStateRowTests(unittest.TestCase):

    def _engine(self, metas, instances):
        return SimpleNamespace(modules=SimpleNamespace(
            list_modules=lambda: metas, instance=lambda mid: instances.get(mid)))

    def test_enabled_stopped_module_shows_hint_row(self):
        engine = self._engine(
            [{"id": "vision_link", "name": "画面识别联动",
              "config": {"interval": {}}, "enabled": True},
             {"id": "alice_cradle", "name": "AIC",
              "config": {"port": {}}, "enabled": False}],
            {})
        rows = live.input_channel_rows(engine, SimpleNamespace(slots={}))
        names = [r["name"] for r in rows]
        self.assertIn("画面识别联动", names)
        self.assertNotIn("AIC", names)
        row = next(r for r in rows if r["name"] == "画面识别联动")
        self.assertFalse(row["enabled"])
        self.assertIn("启动", row["hint"])

    def test_running_module_not_duplicated(self):
        eng = MappingEngine(lambda key, value: None)
        inst = SimpleNamespace(server=SimpleNamespace(engine=eng,
                                                      _last_rx=None))
        engine = self._engine(
            [{"id": "vision_link", "name": "画面识别联动",
              "config": {"interval": {}}, "enabled": True}],
            {"vision_link": inst})
        names = [r["name"] for r in
                 live.input_channel_rows(engine, SimpleNamespace(slots={}))]
        self.assertNotIn("画面识别联动", names)

    def test_non_linkage_module_ignored(self):
        engine = self._engine(
            [{"id": "strength_logger", "name": "强度日志示例",
              "config": {}, "enabled": True}],
            {})
        rows = live.input_channel_rows(engine, SimpleNamespace(slots={}))
        self.assertNotIn("强度日志示例", [r["name"] for r in rows])

    def test_sig_reflects_module_lifecycle(self):
        metas = [{"id": "vision_link", "name": "画面识别联动",
                  "enabled": False, "loaded": False, "running": False}]
        eng = MappingEngine(lambda key, value: None)
        inst = SimpleNamespace(server=SimpleNamespace(engine=eng,
                                                      _last_rx=None))
        engine = self._engine(metas, {"vision_link": inst})
        sig1 = live.module_data_sig(engine)
        metas[0]["running"] = True
        self.assertNotEqual(live.module_data_sig(engine), sig1)


if __name__ == "__main__":
    unittest.main()

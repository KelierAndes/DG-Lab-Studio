from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dglab.mapping import MappingEngine
from ui import live


def _engine_with_module(signals=None, out_values=None, errors=None,
                        out_errors=None, mappings=1, outputs=1,
                        last_rx=None, cfg=None, temps=None):
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
    return SimpleNamespace(modules=host.modules, osc=None), server


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
        engine2, _rt2 = _engine_with_module(out_values={"f": 1}, last_rx=None)
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
        self.assertEqual(rows[0]["probe"], "未配置映射")

    def test_osc_without_mappings_not_duplicated(self):
        eng = MappingEngine(lambda key, value: None)
        inst = SimpleNamespace(
            bridge=SimpleNamespace(engine=eng, _running=True,
                                   last_rx=None))
        engine = SimpleNamespace(osc=None, modules=SimpleNamespace(
            list_modules=lambda: [{"id": "osc_bridge",
                                   "name": "VRChat OSC 联动",
                                   "enabled": True}],
            instance=lambda mid: inst))
        self.assertEqual(live.module_channel_rows(engine), [])

    def test_link_counts_include_module_channels(self):
        engine, _rt = _engine_with_module(signals={"HP": 5}, last_rx=_fresh())
        counts = live.link_counts(engine, SimpleNamespace(slots={}))
        self.assertIn(("Alice in Cradle 联动 · 模块→核心", "输入映射 1 条"),
                      counts["input"])
        self.assertIn(("Alice in Cradle 联动 · 核心→模块", "输出映射 1 条"),
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
        return SimpleNamespace(osc=None, modules=SimpleNamespace(
            list_modules=lambda: [
                {"id": "alice_cradle", "name": "AIC"},
                {"id": "osc_bridge", "name": "VRChat OSC 联动",
                 "enabled": enabled}],
            instance=lambda mid: inst if mid == "osc_bridge" else None))

    def test_osc_base_entry_suppressed_when_module_active(self):
        engine = self._osc_engine(running=True)
        rows = live.input_channel_rows(engine, SimpleNamespace(slots={}))
        self.assertNotIn("VRChat OSC 输入", [r["name"] for r in rows])

    def test_osc_base_entry_shown_when_module_down(self):
        engine = self._osc_engine(running=False)
        rows = live.input_channel_rows(engine, SimpleNamespace(slots={}))
        self.assertIn("VRChat OSC 输入", [r["name"] for r in rows])

    def test_osc_base_entry_gone_after_uninstall(self):
        engine = self._osc_engine(running=False, enabled=False)
        rows = live.input_channel_rows(engine, SimpleNamespace(slots={}))
        self.assertNotIn("VRChat OSC 输入", [r["name"] for r in rows])


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


class ModuleValueRowTests(unittest.TestCase):
    def test_input_values_from_all_modules(self):
        cfg = {"temps": [{"name": "HLost", "expr": "{HPmax} - {HP}"}],
               "events": [{"name": "拍", "trigger": "if",
                           "arg": "Orgasming", "actions": []}]}
        engine, _rt = _engine_with_module(
            signals={"HP": 60.5, "Heal": 2.0, "Orgasming": 1.0},
            temps={"HLost": 40.0}, cfg=cfg)
        rows = live.input_value_rows(engine, None)
        by_name = {r["name"]: r for r in rows}
        self.assertEqual(by_name["HP"]["kind"], "Alice in Cradle 联动")
        self.assertEqual(by_name["HP"]["value"], "60.5")
        self.assertEqual(by_name["Orgasming"]["value"], "1")
        self.assertNotIn("Heal", by_name)
        self.assertEqual(by_name["HLost"]["kind"], "Alice in Cradle 联动 · 临时变量")

    def test_module_output_values(self):
        engine, _rt = _engine_with_module(out_values={"out0": 12, "b": True})
        rows = live.module_output_value_rows(engine)
        by_name = {r["name"]: r for r in rows}
        self.assertEqual(by_name["out0"]["value"], "12")
        self.assertEqual(by_name["out0"]["age"], "Int")
        self.assertEqual(by_name["b"]["value"], "True")


if __name__ == "__main__":
    unittest.main()

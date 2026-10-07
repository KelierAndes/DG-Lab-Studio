
from __future__ import annotations

import time

from win32more.Microsoft.UI.Xaml import Thickness
from win32more.Microsoft.UI.Xaml.Controls import Page
from win32more.winui3 import XamlClass

from dglab.state import family_of
from ui import live, nav, theme, widgets as W
from ui.paths import xaml

class DashboardPage(XamlClass, Page):
    def __init__(self, shell):
        super().__init__()
        self.shell = shell
        self.LoadComponentFromFile(xaml("DashboardPage.xaml"), encoding="utf-8")
        self._state_seen: tuple | None = None
        self._mod_sig: tuple = ()
        self._last = 0.0
        self.rebuild()

    def tick(self) -> None:
        shell = self.shell
        now = time.monotonic()
        if now - self._last < 0.4:
            return
        state_sig = self._state_sig()
        mod_sig = live.module_data_sig(shell.engine)
        if state_sig == self._state_seen and mod_sig == self._mod_sig:
            self._refresh_live_rows()
            return
        self.rebuild()

    def _state_sig(self) -> tuple:
        st = self.shell.state
        return (st.backend,
                tuple(sorted((sid, s.type) for sid, s in st.slots.items())))

    def _refresh_live_rows(self) -> None:
        engine, state = self.shell.engine, self.shell.state
        lines = live.input_value_rows(engine, state)
        if [(l["kind"], l["name"]) for l in lines] \
                != list(self._input_value_tbs):
            self.rebuild()
            return
        for line in lines:
            tb = self._input_value_tbs[(line["kind"], line["name"])]
            if tb.Text != line["value"]:
                tb.Text = line["value"]

        out_lines = live.output_value_rows(engine, state)
        if [l["name"] for l in out_lines] != list(self._output_value_tbs):
            self.rebuild()
            return
        for line in out_lines:
            refs = self._output_value_tbs[line["name"]]
            if refs["value"].Text != line["value"]:
                refs["value"].Text = line["value"]
            self._set_meter(refs["meter"], line["percent"], width=110)
            if refs["wave"].Text != line["wave"]:
                refs["wave"].Text = line["wave"]

        m_lines = live.module_output_value_rows(engine)
        if [(l["kind"], l["name"]) for l in m_lines] \
                != list(self._module_value_tbs):
            self.rebuild()
            return
        for line in m_lines:
            tb = self._module_value_tbs[(line["kind"], line["name"])]
            if tb.Text != line["value"]:
                tb.Text = line["value"]

        for sid, cells in self._device_cells.items():
            slot = state.slots.get(sid)
            if slot is None:
                continue
            if slot.is_output_device:
                for ch in ("A", "B"):
                    out = live.output_row(slot, ch)
                    tb = cells.get(f"strength_{ch}")
                    if tb is not None and tb.Text != f"{out['value']}/{out['limit']}":
                        tb.Text = f"{out['value']}/{out['limit']}"
                    meter = cells.get(f"strength_{ch}_meter")
                    if meter is not None:
                        self._set_meter(meter, out["percent"], width=54)
            else:
                pressure = slot.pressure
                text = f"{pressure:.2f} kPa" if pressure is not None else "—"
                tb = cells.get("pressure")
                if tb is not None and tb.Text != text:
                    tb.Text = text
                meter = cells.get("pressure_meter")
                if meter is not None:
                    percent = max(0.0, min(1.0, (pressure or 0.0)
                                           / live.PRESSURE_MAX_KPA)) * 100
                    self._set_meter(meter, percent, width=54)
            bat = slot.battery or 0
            tb = cells.get("battery")
            if tb is not None and tb.Text != live.battery_text(slot):
                tb.Text = live.battery_text(slot)
            meter = cells.get("battery_meter")
            if meter is not None:
                self._set_meter(meter, bat, width=54)

    @staticmethod
    def _set_meter(meter, percent: float, *, width: float = 54) -> None:
        ratio = max(0.0, min(1.0, percent / 100.0))
        try:
            fill = list(meter.Children)[1]
            fill.Width = max(width * ratio, 6.0)
        except Exception:
            pass

    def on_notify(self) -> None:
        self.rebuild()

    def _disconnect_all(self) -> None:
        async def _do():
            if self.shell.engine._backend is not None:
                await self.shell.engine._disconnect_backend()

        self.shell.submit(_do())
        self.shell.logs.append("已请求断开全部连接")

    def rebuild(self) -> None:
        shell = self.shell
        self._state_seen = self._state_sig()
        self._mod_sig = live.module_data_sig(shell.engine)
        self._last = time.monotonic()
        self._input_value_tbs: dict = {}
        self._output_value_tbs: dict = {}
        self._module_value_tbs: dict = {}
        self._device_cells: dict = {}

        W.page_head(
            self.HeadHost,
            {"title": "概览", "subtitle": "设备统计、输入 / 输出链路与通道实时数据",
             "breadcrumb": ["控制台", "概览"]},
            actions=[
                W.text_button("刷新", symbol="Refresh", on_click=lambda s, e: self.rebuild()),
                W.text_button("全部断开", symbol="DisconnectDrive",
                              on_click=lambda s, e: self._disconnect_all()),
            ],
        )
        self._fill_stats()
        self.InputChannelsHost.Content = self._input_channels_card()
        self.OutputChannelsHost.Content = self._output_channels_card()
        self.InputValuesHost.Content = self._input_values_card()
        self.OutputValuesHost.Content = self._output_values_card()

        host = self.DevicesHost
        host.Children.Clear()
        state = shell.state
        for sid in sorted(state.slots):
            host.Children.Append(self._device_card(sid, state.slots[sid]))
        if not state.slots:
            host.Children.Append(self._empty_note())
    def _fill_stats(self) -> None:
        host = self.StatsHost
        host.Children.Clear()
        host.ColumnDefinitions.Clear()
        for i, stat in enumerate(live.stats(self.shell.engine, self.shell.logs)):
            host.ColumnDefinitions.Append(W.column(stars=1))
            host.Children.Append(W.put(W.card(self._stat_body(stat)), i))

    def _stat_body(self, stat: dict):
        g = W.grid(W.fixed(46), W.star(1), W.auto())
        ic = W.icon(symbol=stat["symbol"], size=16,
                    color="accent" if stat["accent"] else "text3")
        tile = W.box(
            width=38, height=38, corner=8,
            background=theme.brush("accent_soft" if stat["accent"] else "track"),
            child=ic,
        )
        g.Children.Append(W.put(tile, 0))

        info = W.stack(spacing=2, v="center")
        numbers = W.stack(horizontal=True, spacing=3, v="bottom")
        numbers.Children.Append(W.text(stat["value"], size=22, bold=W.SEMIBOLD,
                                       trimming=True))
        if stat["unit"]:
            numbers.Children.Append(W.text(stat["unit"], size=12, color="text3", v="bottom"))
        info.Children.Append(numbers)
        info.Children.Append(W.text(stat["label"], size=12, color="text3", trimming=True))
        g.Children.Append(W.put(info, 1))

        detail = stat.get("detail")
        if detail:
            right = W.stack(spacing=1, v="center", h="right")
            for i, line in enumerate(detail):
                right.Children.Append(W.text(line, size=10,
                                             color="text2" if i == 0 else "text3",
                                             trimming=True, h="right"))
            g.Children.Append(W.put(right, 2))
        else:
            fg, bg = ("success", "success_soft") if stat["accent"] else ("text3", "track")
            g.Children.Append(W.put(W.pill(stat["trend"], fg, bg), 2))
        return g

    def _device_card(self, sid: str, slot):
        family = family_of(slot.type)
        fg, bg = ("success", "success_soft")

        tile = W.box(
            width=30, height=30, corner=7,
            background=theme.brush("accent_soft"),
            child=W.icon(symbol=live.FAMILY_SYMBOLS[family], size=14, color="accent_text"),
            v="center",
        )
        title = W.stack(horizontal=True, spacing=8, v="center")
        title.Children.Append(W.text(slot.name or slot.type or sid, size=14, bold=W.SEMIBOLD, trimming=True))
        title.Children.Append(W.text(f"{live.FAMILY_LABELS[family]} · {slot.type}", size=11, color="text3", v="center"))
        badge = W.pill("在线", fg, bg, dot_color=fg)

        head = W.grid(W.fixed(38), W.star(1), W.auto())
        head.Children.Append(W.put(tile, 0))
        head.Children.Append(W.put(title, 1))
        head.Children.Append(W.put(badge, 2))

        links = W.stack(horizontal=True, spacing=12, v="center")
        links.Children.Append(nav.link("连接", "connect"))
        links.Children.Append(nav.link("控制", "control"))

        if not slot.is_output_device:
            row, refs = self._sensor_row(slot, links)
        else:
            row, refs = self._output_row(slot, links)
        self._device_cells[sid] = refs

        inner = W.stack(spacing=8, h="stretch")
        inner.Children.Append(head)
        inner.Children.Append(row)
        return W.card(inner, padding=12)

    def _sensor_row(self, slot, links):
        pressure = slot.pressure
        value = f"{pressure:.2f} kPa" if pressure is not None else "—"
        percent = max(0.0, min(1.0, (pressure or 0.0) / live.PRESSURE_MAX_KPA)) * 100
        row = W.grid(W.star(1.4), W.star(1), W.auto())
        row.ColumnSpacing = 16
        cell, refs = self._quick_cell("气压", value, percent, "accent", "pressure")
        row.Children.Append(W.put(cell, 0))
        bat = slot.battery or 0
        bat_cell, bat_refs = self._quick_cell(
            "电量", live.battery_text(slot), bat,
            "success" if bat > 60 else "warning", "battery")
        row.Children.Append(W.put(bat_cell, 1))
        refs.update(bat_refs)
        row.Children.Append(W.put(_gap(links, 10), 2))
        return row, refs

    def _output_row(self, slot, links):
        row = W.grid(W.star(1), W.star(1), W.star(1), W.star(1.7), W.auto())
        row.ColumnSpacing = 16
        refs: dict = {}
        for i, ch in enumerate(("A", "B")):
            out = live.output_row(slot, ch)
            cell, cell_refs = self._quick_cell(
                f"{ch} 强度", f"{out['value']}/{out['limit']}",
                out["percent"], "accent", f"strength_{ch}")
            row.Children.Append(W.put(cell, i))
            refs.update(cell_refs)
        bat = slot.battery or 0
        bat_cell, bat_refs = self._quick_cell(
            "电量", live.battery_text(slot), bat,
            "success" if bat > 60 else "warning", "battery")
        row.Children.Append(W.put(bat_cell, 2))
        refs.update(bat_refs)
        waves = self.shell.engine.wave_selection()
        wave_a = live.wave_label(str(waves.get("A", "")))
        wave_b = live.wave_label(str(waves.get("B", "")))
        wave_cell, wave_refs = self._wave_cell(f"A {wave_a} · B {wave_b}")
        row.Children.Append(W.put(wave_cell, 3))
        refs.update(wave_refs)
        row.Children.Append(W.put(_gap(links, 10), 4))
        return row, refs

    def _quick_cell(self, label: str, value: str, percent: float, tone: str,
                    key: str = ""):
        cell = W.stack(horizontal=True, spacing=8, v="center")
        cell.Children.Append(W.text(label, size=11, color="text3"))
        value_tb = W.text(value, size=13, bold=W.SEMIBOLD)
        cell.Children.Append(value_tb)
        meter = W.meter(percent, width=54, fg=tone)
        cell.Children.Append(meter)
        refs = {}
        if key:
            refs = {key: value_tb, f"{key}_meter": meter}
        return cell, refs

    def _wave_cell(self, value: str):
        cell = W.stack(horizontal=True, spacing=8, v="center")
        cell.Children.Append(W.text("波形", size=11, color="text3"))
        wave_tb = W.text(value, size=12, color="text2", trimming=True)
        cell.Children.Append(wave_tb)
        return cell, {"wave": wave_tb}

    def _empty_note(self):
        row = W.grid(W.star(1), W.auto())
        row.Children.Append(W.put(
            W.text("尚未接入设备：在连接页使用 Socket V3 / V4 中继或蓝牙扫描接入",
                   size=12, color="text3", v="center"), 0))
        row.Children.Append(W.put(nav.link("前往连接页", "connect"), 1))
        return W.box(
            corner=6, padding=Thickness(12, 9, 12, 9),
            background=theme.brush("section"), border=theme.brush("stroke"),
            child=row, h="stretch",
        )

    def _card_frame(self, title: str, subtitle: str, *, symbol: str,
                    trailing=None, accent: bool = False) -> object:
        head = W.card_head(title, subtitle=subtitle, symbol=symbol, accent=accent,
                           trailing=trailing)
        inner = W.stack(spacing=8)
        inner.Children.Append(head)
        inner.Children.Append(W.divider(margin=Thickness(0, 2, 0, 0)))
        return inner

    def _input_channels_card(self) -> object:
        inner = self._card_frame(
            "输入通道",
            "控制输入与遥测进入应用的链路（设备 / 服务 / 模块）",
            symbol="Download",
            trailing=nav.link("联动设置", "link"),
        )
        body = W.stack(spacing=0)
        rows = live.input_channel_rows(self.shell.engine, self.shell.state)
        for i, entry in enumerate(rows):
            if i:
                body.Children.Append(W.divider())
            body.Children.Append(self._input_channel_row(entry))
        module_rows = [r for r in live.module_channel_rows(self.shell.engine)
                       if r["direction"] == "模块→核心"]
        if module_rows:
            body.Children.Append(W.divider())
            body.Children.Append(self._section_note("联动模块（模块 → 核心）"))
            for entry in module_rows:
                body.Children.Append(self._module_channel_row(entry))
        inner.Children.Append(body)
        return W.card(inner)

    def _section_note(self, text: str) -> object:
        return W.box(height=30, child=W.text(text, size=11, color="text3",
                                             bold=W.SEMIBOLD, v="center"),
                     h="stretch")

    def _input_channel_row(self, entry: dict) -> object:
        fg, bg = (("success", "success_soft") if entry["enabled"]
                  else ("text3", "track"))
        label = "已启用" if entry["enabled"] else "未启用"
        g = W.grid(W.star(1), W.auto())
        left = W.stack(spacing=2, v="center")
        left.Children.Append(W.text(entry["name"], size=13, bold=W.SEMIBOLD,
                                    trimming=True))
        hint = entry.get("hint") or entry["detail"]
        left.Children.Append(W.text(hint, size=11, color="text3", trimming=True))
        g.Children.Append(W.put(left, 0))
        g.Children.Append(W.put(W.pill(label, fg, bg,
                                       dot_color=fg if entry["enabled"] else None), 1))
        return W.box(height=48, child=g, h="stretch")

    def _module_channel_row(self, entry: dict) -> object:
        ok = entry.get("probe_ok")
        if ok is True:
            pfg, pbg = "success", "success_soft"
        elif ok is False:
            pfg, pbg = "danger", "danger_soft"
        else:
            pfg, pbg = "text3", "track"
        g = W.grid(W.star(1), W.auto(), W.auto())
        left = W.stack(spacing=2, v="center")
        left.Children.Append(W.text(f"{entry['module']} · {entry['direction']}",
                                    size=13, bold=W.SEMIBOLD, trimming=True))
        detail = f"{entry['count']} 条映射 · 探活 {entry['probe']}"
        if entry.get("probe_detail"):
            detail += f"（{entry['probe_detail']}）"
        left.Children.Append(W.text(detail, size=11, color="text3",
                                    trimming=True))
        g.Children.Append(W.put(left, 0))
        g.Children.Append(W.put(W.pill("已启用", "success", "success_soft",
                                       dot_color="success"), 1))
        g.Children.Append(W.put(W.box(margin=Thickness(8, 0, 0, 0),
                                      child=W.pill(entry["probe"], pfg, pbg),
                                      v="center"), 2))
        return W.box(height=48, child=g, h="stretch")

    def _output_channels_card(self) -> object:
        inner = self._card_frame(
            "输出通道",
            "输出设备通道探活与联动模块回传通道",
            symbol="Remote",
            trailing=nav.link("设备控制", "control"),
        )
        body = W.stack(spacing=0)
        rows = live.output_channel_rows(self.shell.state)
        for i, entry in enumerate(rows):
            if i:
                body.Children.Append(W.divider())
            body.Children.Append(self._output_channel_row(entry))
        if not rows:
            body.Children.Append(W.box(height=36, child=W.text(
                "（未接入输出设备）", size=12, color="text3", v="center")))
        module_rows = [r for r in live.module_channel_rows(self.shell.engine)
                       if r["direction"] == "核心→模块"]
        if module_rows:
            body.Children.Append(W.divider())
            body.Children.Append(self._section_note("联动模块（核心 → 模块）"))
            for entry in module_rows:
                body.Children.Append(self._module_channel_row(entry))
        inner.Children.Append(body)
        return W.card(inner)

    def _output_channel_row(self, entry: dict) -> object:
        fg, bg = (("success", "success_soft") if entry["alive"]
                  else ("danger", "danger_soft"))
        g = W.grid(W.fixed(110), W.star(1), W.auto())
        title = W.stack(spacing=2, v="center")
        title.Children.Append(W.text(f"{entry['device']} · {entry['channel']}",
                                     size=12, bold=W.SEMIBOLD, trimming=True))
        title.Children.Append(W.text(f"{entry['value']}/{entry['limit']}",
                                     size=11, color="text3"))
        g.Children.Append(W.put(title, 0))
        g.Children.Append(W.put(W.box(margin=Thickness(0, 0, 10, 0),
                                      child=W.meter(entry["percent"], width=120),
                                      v="center", h="left"), 1))
        g.Children.Append(W.put(W.pill(f"探活 {entry['alive_text']}", fg, bg,
                                       dot_color=fg), 2))
        return W.box(height=48, child=g, h="stretch")

    def _input_values_card(self) -> object:
        inner = self._card_frame(
            "输入数据值",
            "全部联动模块的输入信号与传感器实时数值",
            symbol="Contact",
            trailing=nav.link("参数映射", "link"),
        )
        table_head = W.grid(W.star(1.4), W.fixed(96), W.star(1), W.fixed(74))
        gap = Thickness(12, 0, 0, 0)
        for label, col in (("参数", 0), ("来源", 1), ("当前值", 2), ("时间", 3)):
            cell = W.text(label, size=12, color="text3", margin=gap if col else None)
            table_head.Children.Append(W.put(cell, col))
        inner.Children.Append(table_head)
        inner.Children.Append(W.divider(margin=Thickness(0, 6, 0, 0)))

        rows = W.stack(spacing=0)
        lines = live.input_value_rows(self.shell.engine, self.shell.state)
        if not lines:
            rows.Children.Append(W.box(height=36, child=W.text(
                "（暂无输入数据：模块运行并收到数据后自动出现）",
                size=12, color="text3", v="center")))
        for i, line in enumerate(lines):
            if i:
                rows.Children.Append(W.divider())
            row, tb = self._input_value_row(line)
            rows.Children.Append(row)
            self._input_value_tbs[(line["kind"], line["name"])] = tb
        inner.Children.Append(rows)
        return W.card(inner)

    def _input_value_row(self, line: dict) -> tuple:
        g = W.grid(W.star(1.4), W.fixed(96), W.star(1), W.fixed(74))
        gap = Thickness(12, 0, 0, 0)
        g.Children.Append(W.put(W.text(line["name"], size=12, color="text2",
                                       trimming=True, v="center"), 0))
        g.Children.Append(W.put(W.text(line["kind"], size=11, color="text3",
                                       margin=gap, v="center"), 1))
        value_tb = W.text(line["value"], size=13, bold=W.SEMIBOLD,
                          margin=gap, v="center")
        g.Children.Append(W.put(value_tb, 2))
        g.Children.Append(W.put(W.text(line["age"], size=11, color="text3",
                                       margin=gap, v="center"), 3))
        return W.box(height=34, child=g), value_tb

    def _output_values_card(self) -> object:
        inner = self._card_frame(
            "输出数据值",
            "设备输出通道的实时强度与波形",
            symbol="Sync",
            trailing=nav.link("参数映射", "link"),
        )
        table_head = W.grid(W.star(1.4), W.fixed(88), W.star(1), W.star(1))
        gap = Thickness(12, 0, 0, 0)
        for label, col in (("通道", 0), ("强度", 1), ("幅度", 2), ("波形", 3)):
            cell = W.text(label, size=12, color="text3", margin=gap if col else None)
            table_head.Children.Append(W.put(cell, col))
        inner.Children.Append(table_head)
        inner.Children.Append(W.divider(margin=Thickness(0, 6, 0, 0)))

        rows = W.stack(spacing=0)
        lines = live.output_value_rows(self.shell.engine, self.shell.state)
        if not lines:
            rows.Children.Append(W.box(height=36, child=W.text(
                "（未接入输出设备）", size=12, color="text3", v="center")))
        for i, line in enumerate(lines):
            if i:
                rows.Children.Append(W.divider())
            row, refs = self._output_value_row(line)
            rows.Children.Append(row)
            self._output_value_tbs[line["name"]] = refs
        inner.Children.Append(rows)

        module_lines = live.module_output_value_rows(self.shell.engine)
        if module_lines:
            inner.Children.Append(W.divider(margin=Thickness(0, 6, 0, 0)))
            inner.Children.Append(self._section_note("联动模块回传（核心 → 模块）"))
            m_head = W.grid(W.star(1.4), W.fixed(96), W.star(1), W.fixed(74))
            for label, col in (("字段", 0), ("来源", 1), ("当前值", 2), ("类型", 3)):
                cell = W.text(label, size=12, color="text3",
                              margin=gap if col else None)
                m_head.Children.Append(W.put(cell, col))
            inner.Children.Append(m_head)
            inner.Children.Append(W.divider(margin=Thickness(0, 6, 0, 0)))
            m_rows = W.stack(spacing=0)
            for i, line in enumerate(module_lines):
                if i:
                    m_rows.Children.Append(W.divider())
                row, tb = self._input_value_row(line)
                m_rows.Children.Append(row)
                self._module_value_tbs[(line["kind"], line["name"])] = tb
            inner.Children.Append(m_rows)
        return W.card(inner)

    def _output_value_row(self, line: dict) -> tuple:
        g = W.grid(W.star(1.4), W.fixed(88), W.star(1), W.star(1))
        gap = Thickness(12, 0, 0, 0)
        g.Children.Append(W.put(W.text(line["name"], size=12, color="text2",
                                       trimming=True, v="center"), 0))
        value_tb = W.text(line["value"], size=13, bold=W.SEMIBOLD,
                          margin=gap, v="center")
        g.Children.Append(W.put(value_tb, 1))
        meter = W.box(margin=gap, child=W.meter(line["percent"], width=110),
                      v="center", h="left")
        g.Children.Append(W.put(meter, 2))
        wave_tb = W.text(line["wave"], size=11, color="text3",
                         margin=gap, trimming=True, v="center")
        g.Children.Append(W.put(wave_tb, 3))
        return (W.box(height=34, child=g),
                {"value": value_tb, "meter": meter, "wave": wave_tb})

def _gap(el, left: float):
    el.Margin = Thickness(left, 0, 0, 0)
    return el
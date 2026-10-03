
from __future__ import annotations

import time

from win32more.Microsoft.UI.Xaml import Thickness
from win32more.Microsoft.UI.Xaml.Controls import Button, Page
from win32more.winui3 import XamlClass

from ui import live, theme, widgets as W
from ui.paths import xaml

MAX_ROWS = 300

class LogPage(XamlClass, Page):
    def __init__(self, shell):
        super().__init__()
        self.shell = shell
        self._level = "全部"
        self._keyword = ""
        self._version_seen = -1
        self._last = 0.0
        self.LoadComponentFromFile(xaml("LogPage.xaml"), encoding="utf-8")
        self.rebuild()

    def tick(self) -> None:
        now = time.monotonic()
        if now - self._last < 0.25:
            return
        if self.shell.logs.version == self._version_seen:
            return
        self._fill_rows()

    def rebuild(self) -> None:
        W.page_head(
            self.HeadHost,
            {"title": "日志",
             "subtitle": "运行事件与通信数据帧记录，可按等级筛选",
             "breadcrumb": ["控制台", "日志"]},
            actions=[
                W.text_button("清空记录", symbol="Clear", accent=True,
                              on_click=lambda s, e: self._clear()),
            ],
        )
        self._fill_filter()
        self._fill_tools()
        self._fill_rows()

    def _fill_filter(self) -> None:
        host = self.FilterHost
        host.Children.Clear()
        host.Children.Append(W.text("日志等级", size=12, color="text3", v="center"))
        for level in live.LOG_LEVELS:
            host.Children.Append(self._level_button(level))

    def _level_button(self, level: str) -> Button:
        selected = level == self._level
        b = Button()
        b.Content = W.text(level, size=12,
                           bold=W.SEMIBOLD if selected else W.NORMAL,
                           color="on_accent" if selected else "text2")
        b.Padding = Thickness(11, 4, 11, 4)
        b.VerticalAlignment = W.valign("center")
        if selected:
            b.Background = theme.brush("accent")
            b.BorderBrush = theme.brush("accent")
            b.Foreground = theme.brush("on_accent")
            W.solid_button_states(b, theme.color("accent"), theme.brush("on_accent"))
        b.Click += _click(self._select_level, level)
        return b

    def _select_level(self, level: str) -> None:
        self._level = level
        self._fill_filter()
        self._fill_rows()

    def _fill_tools(self) -> None:
        host = self.ToolsHost
        host.Children.Clear()
        search = W.text_box(placeholder="搜索日志内容", width=200, text=self._keyword)

        def _changed(sender, args):
            self._keyword = search.Text or ""
            self._fill_rows()

        search.TextChanged += _changed
        host.Children.Append(search)

    def _fill_rows(self) -> None:
        self._last = time.monotonic()
        lines = self._visible()
        self._version_seen = self.shell.logs.version
        self.CountText.Text = f"{len(lines)} 条显示 · 当前等级 {self._level}"

        host = self.LogHost
        host.Children.Clear()
        if not lines:
            host.Children.Append(W.box(height=40,
                                       child=W.text("暂无日志", size=12,
                                                    color="text3", v="center"),
                                       h="stretch"))
            return
        for i, (ts, level, message) in enumerate(lines):
            if i:
                host.Children.Append(W.divider())
            host.Children.Append(self._row(ts, level, message))

    def _visible(self):
        return self.shell.logs.filtered(self._level, self._keyword, MAX_ROWS)

    def _row(self, ts: float, level: str, message: str):
        fg, bg = {
            "debug": ("text3", "track"),
            "info": ("accent_text", "accent_soft"),
            "warn": ("warning", "warning_soft"),
            "error": ("danger", "danger_soft"),
        }.get(level, ("text3", "track"))
        g = W.grid(W.fixed(92), W.fixed(64), W.star(1))
        gap = Thickness(12, 0, 0, 0)

        g.Children.Append(W.put(W.text(time.strftime("%H:%M:%S", time.localtime(ts)),
                                       size=11, color="text3", v="center"), 0))
        g.Children.Append(W.put(W.pill(live.LEVEL_LABELS.get(level, level), fg, bg), 1))
        message_color = "text" if level in ("warn", "error") else "text2"
        g.Children.Append(W.put(
            W.text(message, size=12, color=message_color, margin=gap,
                   family="Consolas", trimming=True, v="center"), 2))
        return W.box(height=34, child=g)

    def _clear(self) -> None:
        self.shell.logs.clear()
        self._fill_rows()

def _click(handler, *args):
    def wrapped(sender, routed_args):
        handler(*args)

    return wrapped

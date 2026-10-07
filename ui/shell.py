
from __future__ import annotations

import queue

from win32more import asyncui
from win32more._box import box_value, unbox_value
from win32more.Microsoft.UI.Xaml import ElementTheme, Window
from win32more.Microsoft.UI.Xaml.Controls import NavigationViewItem, NavigationViewItemHeader
from win32more.Microsoft.UI.Xaml.Media.Imaging import BitmapImage
from win32more.Windows.Foundation import TimeSpan
from win32more.Windows.Graphics import SizeInt32
from win32more.Windows.Storage.Streams import DataWriter, InMemoryRandomAccessStream
from win32more.winui3 import XamlApplication, XamlClass, as_runtime_class

from ui import nav, theme, widgets as W
from ui.connect_page import ConnectPage
from ui.control_page import ControlPage
from ui.dashboard_page import DashboardPage
from ui.link_page import LinkPage
from ui.live import LogBuffer
from ui.log_page import LogPage
from ui.modules_page import ModulesPage
from ui.paths import xaml
from ui.settings_page import SettingsPage

WINDOW_SIZE = SizeInt32(1320, 880)

PAGE_CLASSES = {
    "dashboard": DashboardPage,
    "connect": ConnectPage,
    "control": ControlPage,
    "link": LinkPage,
    "modules": ModulesPage,
    "log": LogPage,
    "settings": SettingsPage,
}

NAV_LABELS = {
    "dashboard": ("概览", "Home"),
    "connect": ("连接", "Link"),
    "control": ("控制", "Play"),
    "link": ("联动", "Switch"),
    "modules": ("模块", "Download"),
    "log": ("日志", "List"),
}

class MainWindow(XamlClass, Window):
    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self.LoadComponentFromFile(xaml("MainWindow.xaml"), encoding="utf-8")
        self.ExtendsContentIntoTitleBar = True
        self.SetTitleBar(self.TitleDragArea)

        self.ui_queue: queue.Queue = queue.Queue()
        self.logs = LogBuffer()
        self.logs.mirror = engine._file_only
        self.state = engine.get_state()

        self._pages: dict[str, object] = {}
        self._items: dict[str, NavigationViewItem] = {}
        self._tag = "dashboard"
        self._dark = bool(engine.config.get("ui", {}).get("dark", True))

        self.AppWindow.Resize(WINDOW_SIZE)
        try:
            self.AppWindow.Title = "DGStudio"
        except Exception:
            pass
        self._apply_theme(initial=True)
        nav.bind(self)
        self._build_nav()
        self.NavView.SelectedItem = self._items["dashboard"]

        engine.events.on("state", self._on_engine_state)
        engine.events.on("log", self._on_engine_log)
        engine.events.on("saved_devices", lambda devs: self._notify("connect"))
        engine.events.on("ovc_button", self._on_ovc_button)
        engine.events.on("ovc_button_up", self._on_ovc_button_up)
        engine.events.on("modules_changed", self._on_modules_changed)
        engine.events.on("binding_modules_missing", self._on_binding_missing)
        self._missing_prompt: dict | None = None

        timer = self.DispatcherQueue.CreateTimer()
        # UI 心跳 100ms:泵 ui_queue + 当前页 tick(各页内部另有 0.2~0.5s
        # 数据刷新节流)。此前 10ms 会让跨 COM 的 XAML 属性写放大 10 倍,
        # 是连接/控制/联动页卡顿的公共放大器。
        timer.Interval = TimeSpan(Duration=1_000_000)
        timer.IsRepeating = True
        timer.Tick += self._on_tick
        timer.Start()

        async def _startup_check() -> None:
            # 引擎的模块自启动可能早于窗口订阅事件，这里兜底再查一次映射
            engine._check_missing_bindings()

        self.submit(_startup_check())

        self.Closed += self._on_closed
        self.Activate()

    def set_image_bytes(self, image, data: bytes) -> None:
        async def _run():
            try:
                stream = InMemoryRandomAccessStream()
                writer = DataWriter(stream.GetOutputStreamAt(0))
                writer.WriteBytes(data)
                await writer.StoreAsync()
                await writer.FlushAsync()
                writer.DetachStream()
                stream.Seek(0)
                bitmap = BitmapImage()
                await bitmap.SetSourceAsync(stream)
                image.Source = bitmap
            except Exception as exc:
                self.logs.append(f"图片更新失败: {exc!r}")

        asyncui.create_task(_run())

    def submit(self, coro) -> None:
        fut = self.engine.submit(coro)

        def _done(f):
            exc = f.exception()
            if exc:
                self.logs.append(f"错误: {exc!r}")

        fut.add_done_callback(_done)

    def _on_engine_state(self, state) -> None:
        self.state = state

    def _on_engine_log(self, msg: str) -> None:
        self.logs.append(msg, from_engine=True)

    def _on_ovc_button(self, slot_id: str, bit: int) -> None:
        self._flash_button(bit, True)

    def _on_ovc_button_up(self, slot_id: str, bit: int) -> None:
        self._flash_button(bit, False)

    def _flash_button(self, bit: int, pressed: bool) -> None:
        def _run():
            page = self._pages.get("control")
            flash = getattr(page, "flash_button", None)
            if flash is not None:
                flash(bit, pressed)

        self.ui_queue.put(_run)

    def _notify(self, tag: str) -> None:
        def _run():
            page = self._pages.get(tag)
            if page is not None and tag == self._tag:
                method = getattr(page, "on_notify", None)
                if method is not None:
                    method()

        self.ui_queue.put(_run)

    def _notify_all(self) -> None:
        """重建全部已构造页面（模块装卸等影响多页的变更用）。"""
        for page in list(self._pages.values()):
            method = getattr(page, "on_notify", None)
            if method is not None:
                try:
                    method()
                except Exception as exc:
                    self.logs.append(f"页面刷新失败: {exc!r}")

    def _on_modules_changed(self, module_id: str) -> None:
        def _run():
            self._notify_all()

        self.ui_queue.put(_run)

    def _on_binding_missing(self, payload: dict) -> None:
        def _run():
            self._missing_prompt = payload

        self.ui_queue.put(_run)

    async def _handle_missing_bindings(self, payload: dict) -> None:
        from ui.dialogs import confirm_dialog

        module_ids = payload.get("modules") or []
        bindings = payload.get("bindings") or {}
        names = []
        for mid in module_ids:
            meta = self.engine.modules.meta(mid) or {}
            names.append(meta.get("name") or mid)
        if names:
            lines = "\n".join(f"· {name}" for name in names)
            message = (f"当前的负鼠按键映射配置使用了以下未启用模块提供的动作：\n{lines}\n\n"
                       "启用相关模块后保留映射；拒绝加载则相关绑定会重置为「无动作」并保存。")
            ok = await confirm_dialog(self, "按键映射需要启用模块", message,
                                      primary="启用并加载", close="拒绝加载")
            if ok:
                for mid in module_ids:
                    self.submit(self.engine.modules.install(mid))
                return
        else:
            await confirm_dialog(
                self, "按键映射包含未知动作",
                "当前按键映射包含无法识别的动作，相关绑定将重置为「无动作」并保存。",
                close="知道了")
        self.engine.reset_bindings(list(bindings))
        self._notify_all()

    def _on_tick(self, sender, args) -> None:
        while True:
            try:
                fn = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception as exc:
                self.logs.append(f"UI 更新失败: {exc!r}")
        page = self._pages.get(self._tag)
        if page is not None:
            tick = getattr(page, "tick", None)
            if tick is not None:
                try:
                    tick()
                except Exception as exc:
                    self.logs.append(f"页面刷新失败: {exc!r}")
        if (self._missing_prompt is not None
                and self.RootGrid.XamlRoot is not None):
            payload, self._missing_prompt = self._missing_prompt, None
            asyncui.create_task(self._handle_missing_bindings(payload))

    def goto(self, tag: str) -> None:
        item = self._items.get(tag)
        if item is not None:
            self.NavView.SelectedItem = item

    def _build_nav(self) -> None:
        header = NavigationViewItemHeader()
        header.Content = "控制台"
        self.NavView.MenuItems.Append(header)
        for tag, (label, symbol) in NAV_LABELS.items():
            self.NavView.MenuItems.Append(self._make_item(tag, label, symbol=symbol))

        for tag, label in (("settings", "设置"), ("theme", "切换主题")):
            self.NavView.FooterMenuItems.Append(
                self._make_item(tag, label, glyph="moon" if tag == "theme" else None,
                                symbol=None if tag == "theme" else "Setting")
            )

    def _make_icon(self, tag: str, *, symbol: str | None, glyph: str | None):
        if tag == "theme":
            glyph = "sun" if theme.name() == "light" else "moon"
        return W.icon(glyph=glyph, symbol=symbol, size=16)

    def _make_item(self, tag: str, label: str, *, symbol: str | None = None,
                   glyph: str | None = None) -> NavigationViewItem:
        item = NavigationViewItem()
        item.Tag = box_value(tag)
        if tag == "theme":
            item.SelectsOnInvoked = False
        item.Icon = self._make_icon(tag, symbol=symbol, glyph=glyph)
        item.Content = label
        self._items[tag] = item
        return item

    def _refresh_nav_appearance(self) -> None:
        for tag, (label, symbol) in NAV_LABELS.items():
            item = self._items.get(tag)
            if item is not None:
                item.Icon = self._make_icon(tag, symbol=symbol, glyph=None)
        settings_item = self._items.get("settings")
        if settings_item is not None:
            settings_item.Icon = self._make_icon("settings", symbol="Setting", glyph=None)
        theme_item = self._items.get("theme")
        if theme_item is not None:
            theme_item.Icon = self._make_icon("theme", symbol=None, glyph=None)

    def NavView_SelectionChanged(self, sender, args):
        item = as_runtime_class(args.SelectedItemContainer)
        if not isinstance(item, NavigationViewItem):
            return
        tag = unbox_value(item.Tag)
        if tag == "theme":
            return
        self._tag = tag
        self.ShellHost.Content = self._page(tag)

    def _page(self, tag: str):
        page = self._pages.get(tag)
        if page is None:
            page = PAGE_CLASSES[tag](self)
            self._pages[tag] = page
        return page

    def NavView_ItemInvoked(self, sender, args):
        item = as_runtime_class(args.InvokedItemContainer)
        if isinstance(item, NavigationViewItem) and unbox_value(item.Tag) == "theme":
            self.toggle_theme()

    def toggle_theme(self) -> None:
        self._dark = not self._dark
        self.engine.config.setdefault("ui", {})["dark"] = self._dark
        self._apply_theme()

    def _apply_theme(self, initial: bool = False) -> None:
        name = "dark" if self._dark else "light"
        theme.set_theme(name)
        try:
            self.RootGrid.RequestedTheme = (
                ElementTheme.Dark if self._dark else ElementTheme.Light
            )
        except Exception:
            pass
        self._refresh_nav_appearance()
        if not initial:
            for page in self._pages.values():
                rebuild = getattr(page, "rebuild", None)
                if rebuild is not None:
                    rebuild()

    def _on_closed(self, sender, args) -> None:
        try:
            for page in self._pages.values():
                flush = getattr(page, "flush_config", None)
                if flush is not None:
                    try:
                        flush()
                    except Exception:
                        pass
            self.engine.config.setdefault("ui", {})["dark"] = self._dark
            self.engine.save_config()
        finally:
            self.engine.stop()

class App(XamlApplication):
    engine = None
    window = None

    def OnLaunched(self, args):
        from app import Engine

        if App.engine is None:
            factory = getattr(App, "engine_factory", None)
            engine = factory() if factory is not None else Engine()
            engine.start()
            App.engine = engine
        self.window = MainWindow(App.engine)
        App.window = self.window

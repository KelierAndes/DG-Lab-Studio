
from __future__ import annotations

from win32more.Microsoft.UI.Xaml.Controls import Page
from win32more.winui3 import XamlClass

from ui import live, widgets as W
from ui.paths import xaml
from module_store import MIRROR_PRESETS, PROXY_PRESETS

_GROUP_SYMBOLS = {
    "外观": "Highlight",
    "连接": "Remote",
    "配置文件": "Document",
    "模块市场": "Shop",
    "日志": "List",
    "关于": "Important",
}

class SettingsPage(XamlClass, Page):
    def __init__(self, shell):
        super().__init__()
        self.shell = shell
        self._updating = False
        self.LoadComponentFromFile(xaml("SettingsPage.xaml"), encoding="utf-8")
        self.rebuild()

    def rebuild(self) -> None:
        self._updating = True
        try:
            W.page_head(
                self.HeadHost,
                {"title": "设置",
                 "subtitle": "外观、连接、配置文件、模块市场与日志参数；"
                             "OSC 设置在「联动」页配置",
                 "breadcrumb": ["设置"]},
                actions=[
                    W.text_button("保存到文件", symbol="Save", accent=True,
                                  on_click=lambda s, e: self._save_all()),
                    W.text_button("恢复默认提示", symbol="Refresh",
                                  on_click=lambda s, e: self.shell.logs.append(
                                      "恢复默认：请删除 config.json 与 config/ 目录后重启应用")),
                ],
            )

            host = self.GroupsHost
            host.Children.Clear()
            host.Children.Append(self._group_appearance())
            host.Children.Append(self._group_connection())
            host.Children.Append(self._group_config_files())
            host.Children.Append(self._group_market())
            host.Children.Append(self._group_log())
            host.Children.Append(self._group_about())
        finally:
            self._updating = False

    def _wrap(self, title: str, subtitle: str, rows: list) -> object:
        body = W.stack(spacing=0)
        for i, row in enumerate(rows):
            if i:
                body.Children.Append(W.divider())
            body.Children.Append(row)
        head = W.card_head(title, subtitle=subtitle,
                           symbol=_GROUP_SYMBOLS.get(title, "Setting"), accent=True)
        inner = W.stack(spacing=10)
        inner.Children.Append(head)
        inner.Children.Append(body)
        return W.card(inner)

    def _group_appearance(self) -> object:
        rows = [self._switch_row("深色主题", "Fluent Studio 深色配色（浅色为亮色调色板）",
                                 bool(self.shell.engine.config.get("ui", {}).get("dark", True)),
                                 self._dark_changed)]
        return self._wrap("外观", "界面主题", rows)

    def _group_connection(self) -> object:
        cfg = self.shell.engine.config
        relay = cfg["relay"]
        rows = [
            self._switch_row("自动重连", "蓝牙连接意外断开时每 5 秒重试",
                             bool(cfg.get("auto_reconnect", True)),
                             self._auto_changed),
            self._text_row("Socket V4 中继服务器地址", "留空使用默认官方中继",
                           str(cfg.get("v4_url", "")),
                           lambda text: cfg.__setitem__("v4_url", text or cfg["v4_url"])),
            self._switch_row("Socket V4 使用本地中继", "本机作为局域网中继服务器，App 扫码直连",
                             bool(relay.get("v4_local", False)),
                             lambda value: relay.__setitem__("v4_local", value)),
            self._text_row("Socket V4 本地中继端口", "", str(relay.get("v4_port", 9998)),
                           lambda text: self._set_int(relay, "v4_port", text, 9998)),
            self._text_row("Socket V3 中继服务器地址", "留空使用默认官方中继",
                           str(cfg.get("v3_url", "")),
                           lambda text: cfg.__setitem__("v3_url", text or cfg["v3_url"])),
            self._switch_row("Socket V3 使用本地中继", "本机作为局域网中继服务器",
                             bool(relay.get("v3_local", False)),
                             lambda value: relay.__setitem__("v3_local", value)),
            self._text_row("Socket V3 本地中继端口", "", str(relay.get("v3_port", 9999)),
                           lambda text: self._set_int(relay, "v3_port", text, 9999)),
        ]
        return self._wrap("连接", "中继服务器与蓝牙", rows)

    def _group_config_files(self) -> object:
        engine = self.shell.engine
        rows = [
            self._info_row("主配置文件", "引擎与设备参数（config.json）", engine.config.path),
            self._info_row("模块配置目录", "每模块一个文件，启动时按声明自动装载补齐",
                           engine.modules.config_dir),
        ]
        buttons = W.stack(horizontal=True, spacing=10, v="center", h="right")
        buttons.Children.Append(W.text_button(
            "保存到文件", symbol="Save", accent=True,
            on_click=lambda s, e: self._save_all()))
        buttons.Children.Append(W.text_button(
            "从指定文件载入…", symbol="OpenLocal",
            on_click=lambda s, e: self._load_config()))
        buttons.Children.Append(W.text_button(
            "导出全部配置…", symbol="Upload",
            on_click=lambda s, e: self._export_config()))
        rows.append(W.field_row("手动存取", "载入会写盘并重启运行中的模块立即生效", buttons))
        return self._wrap("配置文件", "核心：启动自动装载 · 手动保存 / 载入 / 导出", rows)

    # ------------------------------------------------------ 模块市场

    def _group_market(self) -> object:
        market = self.shell.engine.config.setdefault("modules_market", {})
        rows = [
            self._text_row("市场仓库", "留空 = 官方 KelierAndes/dgstudio-modules-market；"
                                      "可填 owner/name 或 owner/name@branch",
                           str(market.get("repo") or ""),
                           lambda text: market.__setitem__("repo", text)),
            self._suggest_row("GitHub 加速前缀",
                              "直连失败时的常用加速源（可自行输入其它）；留空直连",
                              str(market.get("mirror") or ""),
                              MIRROR_PRESETS,
                              lambda text: market.__setitem__("mirror", text)),
            self._suggest_row("网络代理",
                              "常用端口：Clash 7890 / Clash Verge 7897 / "
                              "v2rayN 10809；留空跟随系统代理（socks 不支持）",
                              str(market.get("proxy") or ""),
                              PROXY_PRESETS,
                              lambda text: market.__setitem__("proxy", text)),
            self._switch_row("忽略系统代理",
                             "不使用系统/环境代理直连 GitHub（设置了网络代理时无效）",
                             bool(market.get("no_proxy", False)),
                             lambda value: market.__setitem__("no_proxy", value)),
        ]
        return self._wrap("模块市场", "在线模块的下载源、加速与代理", rows)

    def _suggest_row(self, label: str, description: str, value: str,
                     choices, on_commit) -> object:
        """可输入 + 常用项建议的设置行（输入时列出建议项）。"""
        box = W.suggest_box(text=value, choices=list(choices), width=240,
                            placeholder="留空",
                            on_commit=lambda text: (
                                None if self._updating else on_commit(text)))
        return W.field_row(label, description, box)

    # ------------------------------------------------------ 初始化配置模块

    def _init_module(self):
        manager = self.shell.engine.modules
        inst = manager.instance("config_init")
        if inst is None:
            try:
                inst = manager.load("config_init")
            except RuntimeError as exc:
                self.shell.logs.append(f"初始化配置模块不可用: {exc}")
        return inst

    def _save_all(self) -> None:
        inst = self._init_module()
        if inst is None:
            return
        inst.save_all()
        self.shell.logs.append("配置已全部保存到文件")

    def _load_config(self) -> None:
        from ui.dialogs import pick_open_path

        path = pick_open_path("选择要载入的配置文件",
                              initial_dir=self.shell.engine.modules.config_dir)
        if not path:
            return
        inst = self._init_module()
        if inst is None:
            return

        async def _run():
            try:
                applied = inst.load_from(path)
                restarted = await inst.restart_running()
                inst.save_all()
                self.shell.engine._log(
                    f"已从文件载入配置：应用 {applied} 个文件，"
                    f"重启模块 {len(restarted)} 个")
            except (OSError, ValueError) as exc:
                self.shell.engine._log(f"载入配置失败: {exc!r}")
            except Exception as exc:
                self.shell.engine._log(f"载入配置异常: {exc!r}")
            self.shell.ui_queue.put(self.rebuild)

        self.shell.submit(_run())

    def _export_config(self) -> None:
        from ui.dialogs import pick_save_path

        path = pick_save_path("导出全部配置", default_name="dgstudio-config.json")
        if not path:
            return
        inst = self._init_module()
        if inst is None:
            return
        try:
            count = inst.export_to(path)
            self.shell.logs.append(f"已导出 {count} 个配置文件到 {path}")
        except OSError as exc:
            self.shell.logs.append(f"导出配置失败: {exc!r}")

    def _group_log(self) -> object:
        cfg = self.shell.engine.config
        rows = [
            self._switch_row("写日志文件", "记录到应用目录 dgstudio.log",
                             bool(cfg.get("log_to_file", True)),
                             self._log_file_changed),
        ]
        return self._wrap("日志", "记录策略", rows)

    def _group_about(self) -> object:
        rows = [
            self._info_row("版本", "DGStudio", "2.1 (模块化)"),
            self._info_row("运行时", "win32more / WinUI 3", "0.8+"),
            self._info_row("配置文件", "应用目录下 config.json",
                           self.shell.engine.config.path),
        ]
        return self._wrap("关于", "版本与数据", rows)

    def _switch_row(self, label: str, description: str, is_on: bool, on_changed) -> object:
        toggle = W.switch(is_on)

        def handler(sender, args):
            on_changed(bool(toggle.IsOn))

        toggle.Toggled += handler
        return W.field_row(label, description, toggle)

    def _text_row(self, label: str, description: str, value: str, on_commit) -> object:
        box = W.text_box(text=value, width=170)

        def _commit(sender, args):
            if self._updating:
                return
            on_commit((box.Text or "").strip())

        box.TextChanged += _commit
        return W.field_row(label, description, box)

    def _info_row(self, label: str, description: str, value: str) -> object:
        return W.field_row(label, description,
                           W.text(str(value), size=12, color="text2",
                                  h="right", v="center", trimming=True))

    def _dark_changed(self, dark: bool) -> None:
        if self._updating:
            return
        if dark != self.shell._dark:
            self.shell.toggle_theme()

    def _auto_changed(self, value: bool) -> None:
        if self._updating:
            return
        self.shell.engine.config["auto_reconnect"] = value

    def _log_file_changed(self, value: bool) -> None:
        if self._updating:
            return
        self.shell.engine.set_file_logging(value)

    @staticmethod
    def _set_int(target: dict, key: str, text: str, default, low=1, high=65535) -> None:
        try:
            target[key] = max(low, min(high, int(text)))
        except (TypeError, ValueError):
            target[key] = default

    def flush_config(self) -> None:
        pass

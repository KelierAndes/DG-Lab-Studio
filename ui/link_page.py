

from __future__ import annotations

import os
import time

from win32more.Microsoft.UI.Xaml import (HorizontalAlignment, Thickness,
                                         VerticalAlignment)
from win32more.Microsoft.UI.Xaml.Controls import (MenuFlyout, MenuFlyoutItem,
                                                  Page, TextBox)
from win32more.Microsoft.UI.Xaml.Media import VisualTreeHelper
from win32more.winui3 import XamlClass

from dglab.naming import default_input_name, default_output_name, device_osc_names
from dglab.parsing import parse_name, parse_rect
from dglab.params import core_inputs, label_of, output_spec, output_specs
from ui import theme, widgets as W
from ui.paths import xaml
from ui.region_pick import pick_crop, pick_region

# 大卡片按「联动模块」分类：每张模块卡片共用统一模板
# （输出映射表 → 输入映射表 → 模块设置），条目由模块配置声明自动生成。
HIDDEN_MODULES = {"config_init"}

# 输入表（通用模块）：核心参数（名称固定）| 表达式 | 实时值 | 操作
_IN_COLS = (W.fixed(210), W.star(1.5), W.fixed(66), W.auto())
_IN_HEAD = ("核心输入参数（名称固定）", "表达式（{变量} 四则运算）", "实时值", "")
# 输入表（OSC）：核心参数 | 参数名（可自定义）| 表达式 | 实时值 | 操作
_IN_OSC_COLS = (W.fixed(200), W.fixed(160), W.star(1.3), W.fixed(66), W.auto())
_IN_OSC_HEAD = ("核心输入参数（名称固定）", "参数名（可自定义）", "表达式", "实时值", "")
# 输出表：核心来源参数 | 参数名（可改）| 表达式 | 实时值 | 操作
_OUT_COLS = (W.fixed(190), W.fixed(170), W.star(1.4), W.fixed(66), W.auto())
_OUT_HEAD = ("核心来源参数（固定）", "参数名（可重命名）", "表达式", "实时值", "")

_GAP = Thickness(12, 0, 0, 0)
_BTN_GAP = Thickness(4, 0, 0, 0)
_ROW_H = 44

# 运算符菜单：显示字符 → 插入片段。全部半角符号（同一字体渲染、宽度一致、
# 默认模板左对齐成列）；× ÷ 仅显示用，插入片段仍是 expr 支持的 * /。
_EXPR_OPS = (("(", " ("), ("+", " + "), ("-", " - "), ("×", " * "),
             ("÷", " / "), (")", ")"))

# 实时参数（画面识别）：参数名 ← 检测行为；各行为的缺省字段
_DETECTOR_KINDS = (("color", "检测颜色"), ("image", "检测图片"),
                   ("number", "检测数值"), ("bar", "检测数值条"))
_DETECTOR_DEFAULTS = {
    "color": {"color": "FF0000", "tol": 40, "ratio": 0.05},
    "image": {"file": "", "thresh": 0.8},
    "number": {"fmt": "int", "thresh": 0.6, "text": ""},
    "bar": {"min": 0, "max": 100, "thresh": 0.7, "anchor": ""},
}


def _fmtv(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "真" if value else "假"
    try:
        num = float(value)
    except (TypeError, ValueError):
        return str(value)
    if num.is_integer():
        return str(int(num))
    return f"{num:g}"


def _vcenter(el):
    el.VerticalAlignment = VerticalAlignment.Center
    return el


def _inner_text_box(box):
    """AutoSuggestBox 自身没有光标/选区 API：取其模板内的编辑框，
    模板未应用时返回 None。"""
    stack = [box]
    while stack:
        el = stack.pop()
        if el is None:
            continue
        try:
            inner = el.try_as(TextBox)
        except Exception:
            inner = None
        if inner is not None:
            return inner
        try:
            count = VisualTreeHelper.GetChildrenCount(el)
            for i in range(count):
                stack.append(VisualTreeHelper.GetChild(el, i))
        except Exception:
            pass
    return None


def _append_token(box, token: str, touched, on_commit=None) -> None:
    def handler(sender, args):
        text = box.Text or ""
        start, selected = len(text), 0
        # 用户聚焦过输入框：插到光标处（有选区则整体替换），而非总是追加
        # 到末尾——否则先写好表达式再点（ 括号，括号会落到末尾包不住要括的部分
        inner = _inner_text_box(box) if touched["on"] else None
        if inner is not None:
            try:
                start = inner.SelectionStart
                selected = inner.SelectionLength
            except Exception:
                start, selected = len(text), 0
        box.Text = text[:start] + token + text[start + selected:]
        if inner is not None:
            try:
                inner.SelectionStart = start + len(token)
                inner.SelectionLength = 0
            except Exception:
                pass
        if on_commit is not None:
            # 程序化赋值不保证回声 TextChanged，直接提交落盘
            #（提交方幂等，与回声路径重复调用无害）
            on_commit((box.Text or "").strip())
    return handler


class LinkPage(XamlClass, Page):
    def __init__(self, shell):
        super().__init__()
        self.shell = shell
        self._updating = False
        self._pills: dict[str, object] = {}
        self._live_in: list[tuple] = []
        self._live_out: list[tuple] = []
        self._live_signals: list[tuple] = []
        self._core_choices: list[tuple[str, str]] = []
        self._core_index: dict[str, int] = {}
        self._card_modules: list[str] = []
        self._last_render = 0.0
        self.LoadComponentFromFile(xaml("LinkPage.xaml"), encoding="utf-8")
        self.rebuild()

    # ------------------------------------------------------------------ 框架

    def tick(self) -> None:
        now = time.monotonic()
        if now - self._last_render < 0.5:
            return
        self._last_render = now
        self._refresh_pills()
        self._refresh_live()

    def on_notify(self) -> None:
        self.rebuild()

    def rebuild(self) -> None:
        self._updating = True
        try:
            W.page_head(
                self.HeadHost,
                {"title": "联动",
                 "subtitle": "大卡片按联动模块分类，每张卡片共用统一模板"
                             "（输出映射表 / 输入映射表 / 模块设置）；"
                             "核心参数名固定不可改，模块侧字段名可重命名，表达式双向可用",
                 "breadcrumb": ["控制台", "联动"]},
                actions=[
                    W.text_button("保存设置", symbol="Save", accent=True,
                                  on_click=lambda s, e: self._save()),
                ],
            )
            specs = core_inputs()
            self._core_choices = [(spec["key"],
                                   f"{spec['label']}（{spec['type']}）")
                                  for spec in specs]
            self._core_index = {spec["key"]: i
                                for i, spec in enumerate(specs)}
            self._pills = {}
            self._live_in = []
            self._live_out = []
            self._live_signals = []

            content = W.stack(spacing=12, h="stretch")
            self._card_modules = []
            for meta in self.shell.engine.modules.list_modules():
                if meta["id"] in HIDDEN_MODULES or not meta["config"]:
                    continue
                if not meta["enabled"]:
                    continue
                self._card_modules.append(meta["id"])
                content.Children.Append(self._module_card(meta))
            if not self._card_modules:
                content.Children.Append(W.text(
                    "（没有已安装的联动模块：到「模块」页安装后，此处显示其映射卡片）",
                    size=12, color="text3"))
            self.VrcHost.Content = content
        finally:
            self._updating = False

    def flush_config(self) -> None:
        # 配置控件为写穿式（JsonDict），编辑即落盘，无需集中 flush
        pass

    def _save(self) -> None:
        self.shell.engine.save_config()
        modules = self.shell.engine.modules
        for meta in modules.list_modules():
            inst = modules.instance(meta["id"])
            reload = getattr(inst, "reload_config", None)
            if reload is not None:
                self.shell.submit(reload())
        self.shell.logs.append("设置已保存，运行中模块的映射表已重载")

    # ------------------------------------------------------------ 卡片模板

    def _card_shell(self, title: str, *, subtitle: str, symbol: str,
                    trailing=None, blocks: list) -> object:
        head = W.card_head(title, subtitle=subtitle, symbol=symbol, accent=True)
        inner = W.stack(spacing=12, h="stretch")
        if trailing is not None:
            head_g = W.grid(W.star(1), W.auto())
            head_g.Children.Append(W.put(head, 0))
            head_g.Children.Append(W.put(_gap(trailing, 14), 1))
            inner.Children.Append(head_g)
        else:
            inner.Children.Append(head)
        for block in blocks:
            inner.Children.Append(block)
        return W.card(inner)

    def _block(self, caption: str, cols, head_names, rows, *,
               note: str = "", tail_button: bool = False) -> object:
        inner = W.stack(spacing=8, h="stretch")
        inner.Children.Append(W.text(caption, size=11, bold=W.SEMIBOLD, color="text3"))
        inner.Children.Append(self._head_row(cols, head_names, tail_button))
        inner.Children.Append(W.divider(margin=Thickness(0, 6, 0, 0)))
        body = W.stack(spacing=0)
        for i, row in enumerate(rows):
            if i:
                body.Children.Append(W.divider())
            body.Children.Append(row)
        inner.Children.Append(body)
        if note:
            inner.Children.Append(W.text(note, size=11, color="text3", wrap=True))
        return W.panel(inner, padding=14)

    def _row(self, cols, elems, *, height: float = _ROW_H) -> object:
        g = W.grid(*cols)
        for i, el in enumerate(elems):
            if el is None:
                continue
            if i:
                el.Margin = _GAP
            g.Children.Append(W.put(el, i))
        return W.box(height=height, child=g, h="stretch")

    def _head_row(self, cols, names, tail_button: bool = False) -> object:
        g = W.grid(*cols)
        for i, name in enumerate(names):
            if not name:
                continue
            tb = W.text(name, size=11, color="text3", trimming=True, v="center")
            if i:
                tb.Margin = _GAP
            g.Children.Append(W.put(tb, i))
        if tail_button and names and not names[-1]:
            # auto 列无内容会塌缩为 0，导致 star 列变宽、表头整体右移；
            # 放一个不可见占位按钮撑起与数据行删除按钮等宽的列。
            spacer = W.text_button("删除", symbol="Delete")
            spacer.Opacity = 0
            spacer.IsHitTestVisible = False
            spacer.Margin = _GAP
            g.Children.Append(W.put(spacer, len(names) - 1))
        return W.box(height=26, child=g, h="stretch")

    def _expr_field(self, text: str, choices, *, placeholder: str,
                    on_commit) -> object:
        """表达式编辑格：输入框 + 「参数」「运算」下拉，点选插入到光标处
        （未聚焦过时追加到尾部）。"""
        box = W.suggest_box(text=text, choices=choices,
                            placeholder=placeholder, on_commit=on_commit)
        box.HorizontalAlignment = HorizontalAlignment.Stretch
        box.VerticalAlignment = VerticalAlignment.Center
        # GotFocus 是路由事件：内部编辑框获得焦点时即冒泡到这里。
        # 只有用户实际聚焦过才按光标插入；纯下拉点选流程保持尾部追加。
        touched = {"on": False}
        box.GotFocus += lambda s, e: touched.update(on=True)
        g = W.grid(W.star(1), W.auto(), W.auto())
        g.Children.Append(W.put(box, 0))
        g.Children.Append(W.put(self._insert_button(
            "参数", [(name, "{" + name + "}") for name in choices], box,
            touched=touched, on_commit=on_commit), 1))
        g.Children.Append(W.put(self._insert_button(
            "运算", _EXPR_OPS, box, touched=touched,
            on_commit=on_commit), 2))
        return g

    def _insert_button(self, label: str, tokens, box, *,
                       touched=None, on_commit=None) -> object:
        flyout = MenuFlyout()
        state = {"built": False}

        def _opening(sender, args) -> None:
            if state["built"]:
                return
            state["built"] = True
            for token in tokens:
                shown, piece = token if isinstance(token, tuple) \
                    else (token, token)
                item = MenuFlyoutItem()
                item.Text = shown
                # 默认模板：条目等宽、文字左对齐——符号天然成列；
                # 显示字符全部半角（_EXPR_OPS），避免全角字形宽度不一
                item.Click += _append_token(box, piece,
                                            touched or {"on": False},
                                            on_commit)
                flyout.Items.Append(item)

        try:
            flyout.Opening += _opening
        except AttributeError:
            _opening(flyout, None)
        btn = W.button(W.text(label, size=12, color="text2"), width=48,
                       height=32)
        btn.Flyout = flyout
        btn.Margin = _BTN_GAP
        btn.VerticalAlignment = VerticalAlignment.Center
        return btn

    def _placeholder_row(self, text: str) -> object:
        return W.box(height=36, child=W.text(text, size=12, color="text3",
                                             v="center"))

    def _add_row(self, label: str, on_click) -> object:
        return W.box(height=40, child=W.text_button(label, symbol="Add",
                                                    on_click=on_click))

    def _status_trailing(self, module_id: str):
        """模块卡片头部：状态 pill + 启用开关（安装/卸载语义与模块页一致）。"""
        modules = self.shell.engine.modules
        pill_host = W.box(v="center", h="right")
        running = bool((modules.meta(module_id) or {}).get("running"))
        pill_host.Child = self._make_pill(running)
        self._pills[module_id] = pill_host
        toggle = W.switch(modules.is_enabled(module_id))

        def _changed(sender, args, _mid=module_id, _t=toggle) -> None:
            if self._updating:
                return
            if bool(_t.IsOn):
                self.shell.submit(self.shell.engine.modules.install(_mid))
            else:
                self.shell.submit(self.shell.engine.modules.uninstall(_mid))

        toggle.Toggled += _changed
        trailing = W.stack(horizontal=True, spacing=10, v="center")
        trailing.Children.Append(pill_host)
        trailing.Children.Append(toggle)
        return trailing

    @staticmethod
    def _make_pill(running: bool):
        if running:
            return W.pill("运行中", "success", "success_soft", dot_color="success")
        return W.pill("已停止", "text3", "track")

    def _refresh_pills(self) -> None:
        modules = self.shell.engine.modules
        for module_id, host in self._pills.items():
            running = bool((modules.meta(module_id) or {}).get("running"))
            host.Child = self._make_pill(running)

    def _engine(self, module_id: str):
        """运行中的映射引擎（OSC→bridge.engine，AIC→server.engine）。"""
        inst = self.shell.engine.modules.instance(module_id)
        runtime = getattr(inst, "bridge", None) or getattr(inst, "server", None)
        return getattr(runtime, "engine", None) if runtime is not None else None

    def _refresh_live(self) -> None:
        engines: dict[str, object] = {}
        for tb, module_id, key in self._live_in:
            engine = engines.setdefault(module_id, self._engine(module_id))
            value = engine.last_values.get(key) if engine is not None else None
            if engine is not None and key in engine.errors:
                tb.Text = "错误"
                continue
            tb.Text = _fmtv(value)
        for tb, module_id, name in self._live_out:
            engine = engines.setdefault(module_id, self._engine(module_id))
            value = engine.out_values.get(name) if engine is not None else None
            tb.Text = _fmtv(value)
        for tb, module_id, name in self._live_signals:
            engine = engines.setdefault(module_id, self._engine(module_id))
            value = engine.signals.get(name) if engine is not None else None
            tb.Text = _fmtv(value)

    # ------------------------------------------------------- 统一模块卡片

    def _module_card(self, meta: dict) -> object:
        module_id = meta["id"]
        spec = self.shell.engine.modules.config_spec_for(module_id)
        cfg = self.shell.engine.modules.settings_for(module_id)
        varpool = self._var_pool(module_id)
        blocks = [
            self._realtime_block(module_id, cfg),
            self._output_block(module_id, cfg, varpool),
            self._input_block(module_id, cfg, varpool),
            self._settings_block(module_id, spec, cfg, varpool),
        ]
        return self._card_shell(
            meta["name"],
            subtitle=f"{module_id} · 输出映射表 / 输入映射表 / 模块设置",
            symbol="Contact" if module_id == "osc_bridge" else "View",
            trailing=self._status_trailing(module_id),
            blocks=blocks)

    def _dynamic(self, module_id: str) -> bool:
        return bool((self.shell.engine.modules.meta(module_id)
                     or {}).get("dynamic_params"))

    # ------------------------------------------------------------- 实时数据

    def _realtime_block(self, module_id: str, cfg: dict) -> object:
        """实时数据子卡片：模块收到的输入数值实时状态（中英对照网格）。"""
        meta = self.shell.engine.modules.meta(module_id) or {}
        if meta.get("realtime_manager"):
            return self._detector_block(module_id, cfg)
        entries: list[tuple[str, str]] = [
            (str(name), str((item or {}).get("label") or ""))
            for name, item in (meta.get("params") or {}).items()]
        engine = self._engine(module_id)
        if engine is not None:
            try:
                known = {name for name, _label in entries}
                for name in sorted(str(key) for key in engine.signals):
                    if name not in known:
                        entries.append((name, ""))
            except Exception:
                pass
        inner = W.stack(spacing=6, h="stretch")
        inner.Children.Append(W.text("实时数据（输入数值 · 中英对照）",
                                     size=13, bold=W.SEMIBOLD))
        inner.Children.Append(W.text(
            "显示模块实际收到的输入数值；上为实时值，下为「中文说明 变量名」"
            "（变量名即映射表达式中的 {名称}）。",
            size=11, color="text3", wrap=True))
        if not entries:
            inner.Children.Append(self._placeholder_row(
                "（暂无输入参数：模块运行并收到数据后自动出现）"))
        for start in range(0, len(entries), 3):
            g = W.grid(*[W.star(1)] * 3)
            for i, (name, label) in enumerate(entries[start:start + 3]):
                g.Children.Append(W.put(self._signal_cell(module_id, name,
                                                          label), i))
            inner.Children.Append(g)
        return W.panel(inner, padding=14)

    def _signal_cell(self, module_id: str, name: str, label: str) -> object:
        value_tb = W.text("—", size=14, bold=W.SEMIBOLD, family="Consolas",
                          trimming=True)
        self._live_signals.append((value_tb, module_id, name))
        caption = f"{label} {name}".strip()
        cell = W.stack(spacing=1, margin=Thickness(0, 6, 10, 0))
        cell.Children.Append(value_tb)
        cell.Children.Append(W.text(caption, size=10, color="text3",
                                    trimming=True))
        return cell

    # ------------------------------------------------ 实时参数（画面识别）

    def _detector_block(self, module_id: str, cfg: dict) -> object:
        inner = W.stack(spacing=6, h="stretch")
        inner.Children.Append(W.text("实时参数（参数名 ← 检测行为，改动即时生效）",
                                     size=13, bold=W.SEMIBOLD))
        inner.Children.Append(W.text(
            "每个参数名即映射表达式中的 {变量}：行为四选一——检测颜色/检测图片/检测数值"
            "输出 真/假 或数值，检测数值条输出 0~1。区域坐标为截图图像像素，"
            "可手动输入 x,y,w,h 或点「截区域」框选（数值条整条框选时 0%/100% 位置"
            "自动取区域两端）；例图/锚点经「截例图」框选后自动存入模板目录"
            "（锚点取填充色与背景色交界的竖直窄条）。数字/文字由 RapidOCR 识别。",
            size=11, color="text3", wrap=True))
        dets = [d for d in (cfg.get("detectors") or []) if isinstance(d, dict)]
        if not dets:
            inner.Children.Append(self._placeholder_row(
                "（暂无实时参数：点击下方「添加参数」新建，先选行为再截取区域）"))
        for entry in dets:
            inner.Children.Append(self._detector_row(module_id, cfg, entry))
        inner.Children.Append(self._add_row(
            "添加参数", lambda s, e, _m=module_id: self._add_detector(_m)))
        return W.panel(inner, padding=14)

    def _detector_row(self, module_id: str, cfg: dict, entry: dict) -> object:
        top = W.grid(W.fixed(150), W.fixed(110), W.star(1), W.auto())
        top.ColumnSpacing = 8

        name_box = W.text_box(text=str(entry.get("name") or ""),
                              placeholder="参数名")
        name_box.HorizontalAlignment = HorizontalAlignment.Stretch
        name_box.LostFocus += lambda s, a, _e=entry, _b=name_box: \
            self._commit_detector_name(module_id, _e,
                                       (_b.Text or "").strip())
        kind_idx = next((i for i, (k, _l) in enumerate(_DETECTOR_KINDS)
                         if k == entry.get("kind")), 0)
        kind_cb = W.combo([label for _k, label in _DETECTOR_KINDS],
                          selected=kind_idx)
        kind_cb.SelectionChanged += lambda s, e, _e=entry, _c=kind_cb: \
            self._switch_detector_kind(module_id, _e, _c.SelectedIndex)
        live_tb = W.text("—", size=12, bold=W.SEMIBOLD, family="Consolas",
                         trimming=True, v="center")
        self._live_signals.append((live_tb, module_id,
                                   str(entry.get("name") or "")))
        delete = W.text_button("删除", symbol="Delete",
                               on_click=lambda s, e, _e=entry:
                               self._remove_detector(module_id, _e))
        top.Children.Append(W.put(_vcenter(name_box), 0))
        top.Children.Append(W.put(_vcenter(kind_cb), 1))
        top.Children.Append(W.put(_vcenter(live_tb), 2))
        top.Children.Append(W.put(_vcenter(delete), 3))

        args = W.stack(horizontal=True, spacing=8, v="center")
        self._detector_args(module_id, cfg, entry, args)
        err = self._detector_error(module_id, entry)
        if err:
            args.Children.Append(W.text(f"⚠ {err}", size=11, color="danger",
                                        v="center"))

        body = W.stack(spacing=6, h="stretch")
        body.Children.Append(top)
        body.Children.Append(args)
        return W.box(corner=6, padding=Thickness(10, 8, 10, 8),
                     background=theme.brush("section"),
                     border=theme.brush("stroke"), child=body, h="stretch")

    def _detector_args(self, module_id: str, cfg: dict, entry: dict,
                       host) -> None:
        kind = str(entry.get("kind") or "color")
        host.Children.Append(
            self._labeled("检测区域", self._rect_field(module_id, cfg, entry)))
        if kind == "color":
            host.Children.Append(self._text_arg("颜色 #RRGGBB", entry, "color",
                                                module_id, width=92))
            host.Children.Append(self._number_arg("容差", entry, "tol",
                                                  0, 255, module_id, 0))
            host.Children.Append(self._number_arg("占比", entry, "ratio",
                                                  1, 100, module_id, 5,
                                                  is_percent=True))
        elif kind == "image":
            host.Children.Append(self._file_arg("例图", entry, "file",
                                                module_id))
            host.Children.Append(self._thresh_arg("匹配阈值", entry,
                                                  module_id, 0.8))
        elif kind == "number":
            fmt = str(entry.get("fmt") or "int")
            fmt_idx = ("int", "float", "text").index(fmt) \
                if fmt in ("int", "float", "text") else 0
            fmt_cb = W.combo(["整数", "小数", "文字"], selected=fmt_idx,
                             width=84)
            fmt_cb.SelectionChanged += lambda s, e, _e=entry, _c=fmt_cb: \
                self._commit_detector(module_id, _e,
                                      {"fmt": ("int", "float",
                                               "text")[_c.SelectedIndex]})
            host.Children.Append(_vcenter(self._labeled("输出类型", fmt_cb)))
            if fmt == "text":
                host.Children.Append(self._text_arg("期望文本", entry, "text",
                                                    module_id, width=120))
            host.Children.Append(self._thresh_arg("匹配阈值", entry,
                                                  module_id, 0.6))
        elif kind == "bar":
            host.Children.Append(self._number_arg("0% 位置(x/y)", entry, "min",
                                                  -99999, 99999, module_id, 0))
            host.Children.Append(self._number_arg("100% 位置(x/y)", entry, "max",
                                                  -99999, 99999, module_id, 100))
            host.Children.Append(self._file_arg("锚点例图", entry, "anchor",
                                                module_id))
            host.Children.Append(self._thresh_arg("匹配阈值", entry,
                                                  module_id, 0.7))

    def _rect_field(self, module_id: str, cfg: dict, entry: dict) -> object:
        rect = entry.get("rect")
        text = (f"{rect[0]},{rect[1]},{rect[2]},{rect[3]}"
                if isinstance(rect, (list, tuple)) and len(rect) == 4 else "")
        box = W.text_box(text=text, placeholder="x,y,w,h", width=128)
        box.LostFocus += lambda s, a, _e=entry, _b=box: \
            self._commit_rect(module_id, _e, (_b.Text or "").strip())
        pick = W.text_button("截区域", symbol="Crop",
                             on_click=lambda s, e, _e=entry:
                             self._pick_rect(module_id, _e))
        row = W.stack(horizontal=True, spacing=4, v="center")
        row.Children.Append(box)
        row.Children.Append(pick)
        return row

    def _text_arg(self, header: str, entry: dict, key: str, module_id: str,
                  *, width: float = 96) -> object:
        box = W.text_box(text=str(entry.get(key) or ""), width=width)
        box.LostFocus += lambda s, a, _e=entry, _b=box, _k=key: \
            self._commit_detector(module_id, _e,
                                  {_k: (_b.Text or "").strip()})
        return self._labeled(header, box)

    def _number_arg(self, header: str, entry: dict, key: str, lo: float,
                    hi: float, module_id: str, default: float,
                    *, width: float = 68, is_percent: bool = False) -> object:
        try:
            current = float(entry.get(key, default))
        except (TypeError, ValueError):
            current = float(default)
        if is_percent:
            current = current * 100.0

        def commit(value: float, _e=entry, _k=key, _m=module_id) -> None:
            self._commit_detector(_m, _e,
                                  {_k: (value / 100.0 if is_percent
                                        else int(round(value)))})

        return self._labeled(header, W.number_box(current, lo, hi,
                                                  width=width,
                                                  on_commit=commit))

    def _thresh_arg(self, header: str, entry: dict, module_id: str,
                    default: float) -> object:
        return self._number_arg(header, entry, "thresh", 5, 100, module_id,
                                default * 100.0, is_percent=True)

    def _file_arg(self, header: str, entry: dict, key: str,
                  module_id: str) -> object:
        box = W.text_box(text=str(entry.get(key) or ""), width=116,
                         placeholder="自动生成")
        box.LostFocus += lambda s, a, _e=entry, _b=box, _k=key: \
            self._commit_detector(module_id, _e, {_k: (_b.Text or "").strip()})
        label = "截例图" if key == "file" else "截锚点"
        pick = W.text_button(label, symbol="Crop",
                             on_click=lambda s, e, _e=entry, _k=key:
                             self._pick_crop(module_id, _e, _k))
        row = W.stack(horizontal=True, spacing=4, v="center")
        row.Children.Append(box)
        row.Children.Append(pick)
        return self._labeled(header, row)

    def _labeled(self, header: str, control) -> object:
        cell = W.stack(spacing=2, v="center")
        cell.Children.Append(W.text(header, size=10, color="text3",
                                    trimming=True))
        cell.Children.Append(control)
        return cell

    # -- 实时参数提交与截图选取 -------------------------------------

    def _commit_detector(self, module_id: str, entry: dict,
                         changes: dict) -> None:
        if self._updating:
            return
        if all(entry.get(k) == v for k, v in changes.items()):
            return
        entry.update(changes)
        self._save_and_reload(module_id)

    def _commit_detector_name(self, module_id: str, entry: dict,
                              text: str) -> None:
        if self._updating or not text or text == entry.get("name"):
            return
        try:
            parse_name(text)
        except ValueError:
            self.shell.logs.append(f"参数名 {text!r} 无效：需字母开头的"
                                   "字母/数字/下划线")
            return
        entry["name"] = text
        self._save_and_reload(module_id)
        self.rebuild()

    def _commit_rect(self, module_id: str, entry: dict, text: str) -> None:
        if self._updating:
            return
        if not text:
            if entry.get("rect") is None:
                return
            entry["rect"] = None
        else:
            try:
                rect = list(parse_rect(text))
            except ValueError as exc:
                self.shell.logs.append(f"检测区域无效: {exc}")
                return
            if entry.get("rect") == rect:
                return
            entry["rect"] = rect
        self._save_and_reload(module_id)
        self.rebuild()

    def _switch_detector_kind(self, module_id: str, entry: dict,
                              index) -> None:
        if self._updating or not isinstance(index, int):
            return
        if not 0 <= index < len(_DETECTOR_KINDS):
            return
        kind = _DETECTOR_KINDS[index][0]
        if entry.get("kind") == kind:
            return
        entry["kind"] = kind
        for key, value in _DETECTOR_DEFAULTS.get(kind, {}).items():
            entry.setdefault(key, value)
        self._save_and_reload(module_id)
        self.rebuild()

    def _add_detector(self, module_id: str) -> None:
        if self._updating:
            return
        cfg = self.shell.engine.modules.settings_for(module_id)
        rows = [r for r in (cfg.get("detectors") or [])
                if isinstance(r, dict)]
        used = {str(r.get("name") or "") for r in rows}
        n = 1
        while f"param{n}" in used:
            n += 1
        entry = {"name": f"param{n}", "kind": "color", "rect": None}
        entry.update(_DETECTOR_DEFAULTS["color"])
        rows.append(entry)
        cfg["detectors"] = rows
        self.rebuild()

    def _remove_detector(self, module_id: str, entry: dict) -> None:
        if self._updating:
            return
        cfg = self.shell.engine.modules.settings_for(module_id)
        cfg["detectors"] = [r for r in (cfg.get("detectors") or [])
                            if r is not entry]
        self._save_and_reload(module_id)
        self.rebuild()

    def _pick_rect(self, module_id: str, entry: dict) -> None:
        def done(rect) -> None:
            if rect is None or self._updating:
                return
            entry["rect"] = list(rect)
            if str(entry.get("kind")) == "bar":
                # 整条截图选取：0%/100% 位置自动取区域左/上缘与右/下缘
                x, y, w, h = rect
                if w >= h:
                    entry["min"], entry["max"] = x, x + w
                else:
                    entry["min"], entry["max"] = y, y + h
            self._save_and_reload(module_id)
            self.rebuild()

        pick_region(done)

    def _pick_crop(self, module_id: str, entry: dict, key: str) -> None:
        def done(result) -> None:
            if result is None or self._updating:
                return
            png, _rect = result
            inst = self.shell.engine.modules.instance(module_id)
            make_dir = getattr(inst, "template_dir", None)
            if make_dir is None:
                return
            directory = make_dir()
            os.makedirs(directory, exist_ok=True)
            fname = f"{entry.get('name') or 'param'}_{key}.png"
            with open(os.path.join(directory, fname), "wb") as f:
                f.write(png)
            entry[key] = fname
            self._save_and_reload(module_id)
            self.rebuild()

        pick_crop(done)

    def _save_and_reload(self, module_id: str) -> None:
        cfg = self.shell.engine.modules.settings_for(module_id)
        cfg.save()
        inst = self.shell.engine.modules.instance(module_id)
        reload = getattr(inst, "reload_config", None)
        if reload is not None:
            self.shell.submit(reload())

    def _detector_error(self, module_id: str, entry: dict) -> str:
        inst = self.shell.engine.modules.instance(module_id)
        bridge = getattr(inst, "bridge", None)
        for det in getattr(bridge, "detectors", None) or []:
            if det.name == str(entry.get("name") or "") and det.error:
                return det.error
        return ""


    def _var_pool(self, module_id: str) -> list[str]:
        """表达式变量池：模块声明参数 ∪ 模块自定义参数 ∪ 核心输出参数 ∪ 运行期信号。"""
        pool: set[str] = set()
        meta = self.shell.engine.modules.meta(module_id) or {}
        for name in (meta.get("params") or {}):
            pool.add(str(name))
        inst = self.shell.engine.modules.instance(module_id)
        if inst is not None:
            try:
                for name, _label in inst.link_params():
                    pool.add(str(name))
            except Exception:
                pass
        engine = self._engine(module_id)
        if engine is not None:
            try:
                pool.update(str(key) for key in engine.values())
            except Exception:
                pass
        try:
            names = device_osc_names(self.shell.state, {})
        except Exception:
            names = {}
        counters: dict[str, int] = {}
        for sid in sorted(names):
            info = names[sid]
            fam, idx = info["family"], int(info.get("index", 1))
            for sp in output_specs(fam, idx):
                pool.add(sp["key"])
        pool.update(("Strength", "Limit", "max", "Battery", "Connected",
                     "Pressure", "Action"))
        return sorted(pool)

    # ------------------------------------------------------------ 输入映射表

    def _input_block(self, module_id: str, cfg: dict, varpool) -> object:
        dynamic = self._dynamic(module_id)
        rows = []
        for entry in cfg.get("mappings") or []:
            if isinstance(entry, dict):
                rows.append(self._input_row(module_id, cfg, entry, varpool,
                                            dynamic))
        if not rows:
            rows.append(self._placeholder_row("（暂无输入映射，点击下方「添加映射」新建）"))
        rows.append(self._add_row(
            "添加映射",
            lambda s, e, _m=module_id: self._add_mapping(_m)))
        if dynamic:
            caption = ("输入映射表（核心输入参数 ← 头像参数：选择核心参数即自动生成"
                       "默认参数名与表达式，参数名可自定义，表达式可混合 {模块参数} 与"
                       " {核心输出参数} 四则运算）")
            return self._block(caption, _IN_OSC_COLS, _IN_OSC_HEAD, rows,
                               tail_button=True)
        return self._block(
            "输入映射表（核心输入参数 ← 表达式：核心参数名固定不可改，"
            "表达式可混合 {模块参数} 与 {核心输出参数}，"
            "「参数」「运算」下拉快速插入，结果取整钳制后派发设备动作）",
            _IN_COLS, _IN_HEAD, rows, tail_button=True)

    def _input_row(self, module_id: str, cfg: dict, entry: dict,
                   varpool, dynamic: bool = False) -> object:
        key = str(entry.get("param") or "")

        def _pick(sender, args, _e=entry, _cfg=cfg) -> None:
            if self._updating:
                return
            idx = combo.SelectedIndex
            if not isinstance(idx, int) or not 0 <= idx < len(self._core_choices):
                return
            new_key = self._core_choices[idx][0]
            if _e.get("param") != new_key:
                _e["param"] = new_key
                if dynamic:
                    name = default_input_name(_cfg, new_key)
                    _e["name"] = name
                    _e["expr"] = "{" + name + "}"
                _cfg.save()
                self.rebuild()

        def _commit(text: str, _e=entry, _cfg=cfg) -> None:
            if self._updating:
                return
            if _e.get("expr") != text:
                _e["expr"] = text
                _cfg.save()

        def _commit_name(text: str, _e=entry, _cfg=cfg) -> None:
            if self._updating or not text:
                return
            old = str(_e.get("name") or "")
            if old == text:
                return
            _e["name"] = text
            changed_expr = False
            current = str(_e.get("expr") or "").strip()
            if not current or current == "{" + old + "}":
                _e["expr"] = "{" + text + "}"
                changed_expr = True
            _cfg.save()
            if changed_expr:
                self.rebuild()

        def _remove(_e=entry, _cfg=cfg) -> None:
            if self._updating:
                return
            _cfg["mappings"] = [r for r in (_cfg.get("mappings") or [])
                                if r is not _e]
            self.rebuild()

        combo = _vcenter(W.combo([label for _k, label in self._core_choices],
                                 selected=self._core_index.get(key, 0)))
        combo.SelectionChanged += _pick
        name_box = None
        if dynamic:
            name_box = W.suggest_box(text=str(entry.get("name") or ""),
                                     choices=varpool, placeholder="参数名")
            name_box.HorizontalAlignment = HorizontalAlignment.Stretch
            name_box.VerticalAlignment = VerticalAlignment.Center
            name_box.QuerySubmitted += lambda sender, args, _b=name_box: \
                _commit_name((_b.Text or "").strip())
            name_box.LostFocus += lambda sender, args, _b=name_box: \
                _commit_name((_b.Text or "").strip())
        expr_field = self._expr_field(str(entry.get("expr") or ""), varpool,
                                      placeholder="{DGLabStrengthA}",
                                      on_commit=_commit)
        live_tb = W.text(_fmtv(self._input_live(module_id, key)),
                         size=12, bold=W.SEMIBOLD, family="Consolas",
                         trimming=True, v="center")
        self._live_in.append((live_tb, module_id, key))
        delete = _vcenter(W.text_button("删除", symbol="Delete",
                                        on_click=lambda s, e, _ent=entry: _remove()))
        if dynamic:
            return self._row(_IN_OSC_COLS, [
                combo, name_box, expr_field, live_tb, delete])
        return self._row(_IN_COLS, [
            combo, expr_field, live_tb, delete])

    def _input_live(self, module_id: str, key: str):
        engine = self._engine(module_id)
        return engine.last_values.get(key) if engine is not None else None

    def _add_mapping(self, module_id: str) -> None:
        if self._updating:
            return
        cfg = self.shell.engine.modules.settings_for(module_id)
        rows = [r for r in (cfg.get("mappings") or []) if isinstance(r, dict)]
        used = {str(r.get("param") or "") for r in rows}
        default = next((key for key, _l in self._core_choices
                        if key not in used),
                       self._core_choices[0][0] if self._core_choices else "")
        if self._dynamic(module_id):
            name = default_input_name(cfg, default)
            rows.append({"param": default, "name": name,
                         "expr": "{" + name + "}"})
        else:
            rows.append({"param": default, "expr": ""})
        cfg["mappings"] = rows
        cfg.save()
        self.rebuild()

    # ------------------------------------------------------------ 输出映射表

    def _output_block(self, module_id: str, cfg: dict, varpool) -> object:
        ids = self._output_ids(cfg)
        rows = []
        for entry in cfg.get("outputs") or []:
            if isinstance(entry, dict):
                rows.append(self._output_row(module_id, cfg, entry, ids, varpool))
        if not rows:
            rows.append(self._placeholder_row("（暂无输出映射，点击下方「添加映射」新建）"))
        rows.append(self._add_row(
            "添加映射",
            lambda s, e, _m=module_id: self._add_output(_m)))
        return self._block(
            "输出映射表（模块字段 ← 核心输出参数表达式：来源参数固定，"
            "模块侧参数名可自由重命名，「参数」「运算」下拉快速插入，"
            "表达式取整/归真后回传）",
            _OUT_COLS, _OUT_HEAD, rows, tail_button=True)

    def _output_row(self, module_id: str, cfg: dict, entry: dict,
                    ids: list, varpool) -> object:
        key = str(entry.get("param") or "")
        pairs = list(ids)
        if key and key not in {k for k, _l in pairs}:
            t = str((output_spec(key) or {}).get("type") or "")
            label = label_of(key)
            pairs.append((key, f"{label}（{t}）" if t else label))
        index = next((i for i, (k, _l) in enumerate(pairs) if k == key), 0)

        def _pick(sender, args, _e=entry, _pairs=pairs, _cfg=cfg) -> None:
            if self._updating:
                return
            idx = combo.SelectedIndex
            if not isinstance(idx, int) or not 0 <= idx < len(_pairs):
                return
            new_key = _pairs[idx][0]
            if _e.get("param") != new_key:
                _e["param"] = new_key
                _e["name"] = self._default_output_name(module_id, _cfg,
                                                       new_key)
                _e["expr"] = "{" + new_key + "}"
                _e["type"] = str((output_spec(new_key) or {}).get("type")
                                 or "Int")
                _cfg.save()
                self.rebuild()

        def _commit_name(text: str, _e=entry, _cfg=cfg) -> None:
            if self._updating or not text:
                return
            if _e.get("name") != text:
                _e["name"] = text
                _cfg.save()

        def _commit_expr(text: str, _e=entry, _cfg=cfg) -> None:
            if self._updating:
                return
            if _e.get("expr") != text:
                _e["expr"] = text
                _cfg.save()

        def _remove(_e=entry, _cfg=cfg) -> None:
            if self._updating:
                return
            _cfg["outputs"] = [r for r in (_cfg.get("outputs") or [])
                               if r is not _e]
            self.rebuild()

        name = str(entry.get("name") or "")
        combo = _vcenter(W.combo([label for _k, label in pairs],
                                 selected=index))
        combo.SelectionChanged += _pick
        name_box = W.suggest_box(text=name, choices=varpool,
                                 placeholder="参数名", on_commit=_commit_name)
        name_box.HorizontalAlignment = HorizontalAlignment.Stretch
        name_box.VerticalAlignment = VerticalAlignment.Center
        expr_field = self._expr_field(str(entry.get("expr") or ""), varpool,
                                      placeholder="{" + key + "}",
                                      on_commit=_commit_expr)
        live_tb = W.text(_fmtv(self._output_live(module_id, name)),
                         size=12, bold=W.SEMIBOLD, family="Consolas",
                         trimming=True, v="center")
        self._live_out.append((live_tb, module_id, name))
        return self._row(_OUT_COLS, [
            combo, name_box, expr_field, live_tb,
            _vcenter(W.text_button("删除", symbol="Delete",
                                   on_click=lambda s, e, _ent=entry: _remove()))])

    def _output_live(self, module_id: str, name: str):
        engine = self._engine(module_id)
        if engine is None or not name:
            return None
        return engine.out_values.get(name)

    def _output_ids(self, cfg: dict) -> list:
        ids: list[tuple[str, str]] = []
        seen: set[str] = set()
        try:
            names = device_osc_names(self.shell.state,
                                     cfg.get("device_prefixes") or {})
        except Exception:
            names = {}
        for sid in sorted(names):
            info = names[sid]
            for spec in output_specs(info["family"], int(info.get("index", 1))):
                if spec["key"] not in seen:
                    seen.add(spec["key"])
                    ids.append((spec["key"],
                                f"{spec['label']}（{spec['type']}）"))
        if "Action" not in seen:
            ids.append(("Action", "App 按键反馈（Int）"))
        return ids

    def _add_output(self, module_id: str) -> None:
        if self._updating:
            return
        cfg = self.shell.engine.modules.settings_for(module_id)
        rows = [r for r in (cfg.get("outputs") or []) if isinstance(r, dict)]
        ids = self._output_ids(cfg)
        used = {str(r.get("param") or "") for r in rows}
        key, _label = next(((k, l) for k, l in ids if k not in used),
                           ("Action", "App 按键反馈"))
        spec = output_spec(key) or {}
        rows.append({"param": key,
                     "name": self._default_output_name(module_id, cfg, key),
                     "expr": "{" + key + "}",
                     "type": str(spec.get("type") or "Int")})
        cfg["outputs"] = rows
        cfg.save()
        self.rebuild()

    def _default_output_name(self, module_id: str, cfg: dict, key: str) -> str:
        """输出行默认模块侧参数名：META["reads"] 声明优先，OSC 按设备前缀，其余取短名。"""
        meta = self.shell.engine.modules.meta(module_id) or {}
        spec = output_spec(key) or {}
        signal = str(spec.get("signal") or str(key).split(".")[-1])
        reads = meta.get("reads") or {}
        if signal in reads:
            return str((reads.get(signal) or {}).get("name") or signal)
        if meta.get("dynamic_params"):
            return default_output_name(cfg, key)
        return signal

    # -------------------------------------------------------------- 模块设置

    def _settings_block(self, module_id: str, spec: dict, cfg: dict,
                        varpool) -> object:
        head = W.stack(horizontal=True, spacing=6, v="center")
        head.Children.Append(W.text("OSC 地址与端口" if module_id == "osc_bridge"
                                    else "模块设置",
                                    size=13, bold=W.SEMIBOLD))
        head.Children.Append(W.text(
            "（修改后重新开关上方桥接生效）" if module_id == "osc_bridge"
            else "（修改后重新开关上方模块生效）",
            size=11, color="text3", v="center"))
        inner = W.stack(spacing=8, h="stretch")
        inner.Children.Append(head)
        inner.Children.Append(W.divider(margin=Thickness(0, 6, 0, 0)))
        rows = []
        for key, item in spec.items():
            if not isinstance(item, dict):
                continue
            if item.get("type") == "list":
                continue
            rows.append(self._declared_row(key, item, cfg, varpool))
        if not rows:
            rows.append(W.text("（该模块无额外设置项）", size=12, color="text3"))
        body = W.stack(spacing=0)
        for i, row in enumerate(rows):
            if i:
                body.Children.Append(W.divider())
            body.Children.Append(row)
        inner.Children.Append(body)
        return W.panel(inner, padding=14)

    def _declared_row(self, key: str, item: dict, cfg: dict, varpool) -> object:
        itype = str(item.get("type") or "str")
        label = str(item.get("label") or key)
        desc = str(item.get("desc") or "")

        if itype == "map":
            return W.field_row(label, desc, self._map_field(key, item, cfg))

        def commit(value) -> None:
            if self._updating:
                return
            if cfg.get(key) != value:
                cfg[key] = value
                cfg.save()

        if itype == "param":
            box = W.suggest_box(
                text=str(cfg.get(key, item.get("default", ""))),
                choices=varpool, width=200,
                on_commit=lambda t, _k=key: commit(t or None))
            return W.field_row(label, desc, box)

        if itype in ("int", "float"):
            low = float(item.get("min", 0))
            high = float(item.get("max", 100))
            step = float(item.get("step", 1 if itype == "int" else 0.1))
            current = float(cfg.get(key, item.get("default", low)))
            unit = str(item.get("unit") or "")
            value_tb = W.text(self._fmt(current, itype, unit), size=12,
                              bold=W.SEMIBOLD, family="Consolas",
                              trimming=True, v="center")

            def _show(value: float, _i=itype, _u=unit, _tb=value_tb) -> None:
                _tb.Text = self._fmt(value, _i, _u)

            def _apply(value: float) -> None:
                commit(int(round(value)) if itype == "int" else round(value, 3))

            if high - low <= 1000:
                slider = W.slider(low, high, current, step=step, width=170,
                                  on_change=_show, on_commit=_apply)
                control = W.stack(horizontal=True, spacing=8, v="center")
                control.Children.Append(slider)
                control.Children.Append(value_tb)
            else:
                control = W.number_box(current, low, high, width=130,
                                       on_commit=lambda v: (_apply(v), _show(v)))
            return W.field_row(label, desc, control)

        if itype == "bool":
            toggle = W.switch(bool(cfg.get(key, item.get("default", False))))

            def _toggle(sender, args) -> None:
                commit(bool(toggle.IsOn))

            toggle.Toggled += _toggle
            return W.field_row(label, desc, toggle)

        if itype == "choice":
            choices = [str(c) for c in (item.get("choices") or [])]
            current = str(cfg.get(key, item.get("default", "")))
            idx = choices.index(current) if current in choices else 0
            combo = W.combo(choices, selected=idx, width=170)

            def _pick(sender, args, _choices=choices) -> None:
                sel = combo.SelectedIndex
                if self._updating or not isinstance(sel, int) \
                        or not 0 <= sel < len(_choices):
                    return
                commit(_choices[sel])

            combo.SelectionChanged += _pick
            return W.field_row(label, desc, combo)

        box = W.text_box(text=str(cfg.get(key, item.get("default", ""))),
                         width=170)
        box.TextChanged += lambda sender, args: commit(
            (sender.Text or "").strip() or None)
        return W.field_row(label, desc, box)

    @staticmethod
    def _fmt(value: float, itype: str, unit: str) -> str:
        shown = str(int(round(value))) if itype == "int" else f"{value:g}"
        return f"{shown} {unit}".strip()

    def _map_field(self, key: str, item: dict, cfg: dict) -> object:
        """map 型配置（如设备参数前缀）：每个键一个可编辑小格。"""
        table = cfg.setdefault(key, {})
        defaults = dict(item.get("default") or {})
        keys = list(dict.fromkeys([*table.keys(), *defaults.keys()]))
        row = W.stack(horizontal=True, spacing=8, v="center")

        def _commit(family: str, text: str, _cfg=cfg) -> None:
            if self._updating:
                return
            value = (text or "").strip()
            if value and cfg[key].get(family) != value:
                cfg[key][family] = value
                _cfg.save()

        for family in keys:
            cell = W.stack(spacing=2, v="center")
            cell.Children.Append(W.text(family, size=10, color="text3"))
            cell.Children.Append(W.suggest_box(
                text=str(table.get(family) or defaults.get(family, "")),
                width=110, on_commit=lambda t, _f=family: _commit(_f, t)))
            row.Children.Append(cell)
        return row


def _gap(el, left: float):
    if left:
        el.Margin = Thickness(left, 0, 0, 0)
    return el

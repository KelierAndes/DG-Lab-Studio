

from __future__ import annotations

import os
import time

from win32more.Microsoft.UI.Xaml import (HorizontalAlignment, Thickness,
                                         VerticalAlignment)
from win32more.Microsoft.UI.Xaml.Controls import (MenuFlyout, MenuFlyoutItem,
                                                  Page, TextBox)
from win32more.Microsoft.UI.Xaml.Media import VisualTreeHelper
from win32more.winui3 import XamlClass

from dglab import expr
from dglab.mapping import bool_value
from dglab.naming import device_osc_names
from dglab.parsing import parse_name, parse_rect
from dglab.params import core_inputs, output_specs
from ui import theme, widgets as W
from ui.paths import xaml
from ui.region_pick import pick_crop, pick_region

# 大卡片按「联动模块」分类：每张模块卡片共用统一模板
# （实时数据 → 事件流 → 临时变量 → 模块设置）。数据处理为事件流推送：
# 事件小卡片 = 驱动事件（周期更新 / 变量变更时 / if 判断）+ 动作直列
# （输入 核心参数 ← 变量 / 输出 核心信号 → 变量），动作不做运算，
# 运算只属于临时变量表；四个区域均可折叠——模块运行中展开，已停用的
# 模块卡片保留在本页并整体折叠（开关重开即恢复，不从联动页移除）。
HIDDEN_MODULES = {"config_init"}

# 临时变量表：变量名 | 表达式 | 实时值 | 操作
_TEMP_COLS = (W.fixed(340), W.star(1.2), W.fixed(66), W.auto())
_TEMP_HEAD = ("变量名", "表达式（空 = 模块维护）", "实时值", "")
# 事件动作行：方向 | 核心参数 | 流向 | 变量 | 实时值 | 操作
_ACTION_COLS = (W.fixed(44), W.star(1.1), W.fixed(26), W.star(1),
                W.fixed(64), W.auto())

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
        self._live_temps: list[tuple] = []
        self._live_event: list[tuple] = []
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
                             "（实时数据 / 事件流 / 临时变量 / 模块设置）；"
                             "事件流推送驱动：驱动事件触发动作直列，"
                             "运算只写在临时变量表",
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
            self._live_temps = []
            self._live_event = []

            content = W.stack(spacing=12, h="stretch")
            self._card_modules = []
            for meta in self.shell.engine.modules.list_modules():
                if meta["id"] in HIDDEN_MODULES or not meta["config"]:
                    continue
                # 已停用模块的卡片保留（折叠展示），开关重新打开即恢复
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
            if modules.instance(meta["id"]) is not None:
                # 原生 reload_config + 宿主逻辑表（临时变量/事件流）
                self.shell.submit(modules.reload(meta["id"]))
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
               note: str = "", tail_button: bool = False,
               expanded: bool = True) -> object:
        # caption 形如「标题（使用说明）」：标题进折叠头，说明留在展开区
        split = caption.find("（")
        title = caption if split < 0 else caption[:split]
        detail = "" if split < 0 else caption[split:]
        inner = W.stack(spacing=8, h="stretch")
        if detail:
            inner.Children.Append(W.text(detail, size=11, color="text3",
                                         wrap=True))
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
        return W.panel(W.collapsible(title, inner, expanded=expanded),
                       padding=14)

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
                # 停用不卸载：实例与导入缓存保留，卡片折叠保留在本页
                self.shell.submit(self.shell.engine.modules.deactivate(_mid))

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
        for tb, module_id, name in self._live_temps:
            # 临时变量真源在宿主（attach 后与引擎共享），停用模块仍可显示
            value = self.shell.engine.modules.temps_space(module_id).get(name)
            tb.Text = _fmtv(value)
        vals_cache: dict[str, dict] = {}
        for tb, module_id, spec in self._live_event:
            vals = vals_cache.setdefault(module_id, self._event_values(module_id))
            if spec.get("kind") == "var":
                tb.Text = _fmtv(vals.get(str(spec.get("name") or "")))
                continue
            try:                                   # if 判断体：实时真假
                truth = bool_value(expr.evaluate(
                    expr.normalize(str(spec.get("cond") or "")), vals))
                tb.Text = "真" if truth else "假"
            except expr.ExprError:
                tb.Text = "—"

    def _event_values(self, module_id: str) -> dict:
        """事件流可视化的值空间：引擎值空间（设备变量∪信号∪临时变量），
        无引擎时退回宿主临时变量空间。"""
        eng = self._engine(module_id)
        if eng is not None:
            try:
                return eng.values()
            except Exception:
                pass
        return self.shell.engine.modules.temps_space(module_id)

    # ------------------------------------------------------- 统一模块卡片

    def _module_card(self, meta: dict) -> object:
        module_id = meta["id"]
        modules = self.shell.engine.modules
        spec = modules.config_spec_for(module_id)
        cfg = modules.settings_for(module_id)
        varpool = self._var_pool(module_id)
        # 事件流面板可选显示：模块有映射引擎（可执行动作）或已配置事件卡片
        has_events = bool([e for e in (cfg.get("events") or [])
                           if isinstance(e, dict)]) \
            or self._engine(module_id) is not None
        # 临时变量面板：有映射引擎（表达式可被求值）或模块声明了 temps
        # （META["temps"] / temp_specs）或已有配置行——三者其一即显示；
        # 模块不声明也能用（有引擎即可添加行），声明行仅作模块维护展示
        has_temps = self._engine(module_id) is not None \
            or bool(modules.temp_specs_for(module_id)) \
            or bool([r for r in (cfg.get("temps") or [])
                     if isinstance(r, dict)])
        # 模块运行中面板展开，已停止则整卡折叠（卡片保留不移除）
        running = bool((modules.meta(module_id) or {}).get("running"))
        blocks = [self._realtime_block(module_id, cfg, expanded=running)]
        if has_events:
            blocks.append(self._events_block(module_id, cfg, varpool,
                                             expanded=running))
        if has_temps:
            blocks.append(self._temps_block(module_id, cfg, varpool,
                                            expanded=running))
        blocks.append(self._settings_block(module_id, spec, cfg, varpool))
        sections = [s for s in ("事件流" if has_events else None,
                                "临时变量" if has_temps else None,
                                "模块设置") if s]
        return self._card_shell(
            meta["name"],
            subtitle=f"{module_id} · {' / '.join(sections)}",
            symbol="Contact" if module_id == "osc_bridge" else "View",
            trailing=self._status_trailing(module_id),
            blocks=blocks)

    # ------------------------------------------------------------- 实时数据

    def _realtime_block(self, module_id: str, cfg: dict, *,
                        expanded: bool = True) -> object:
        """实时数据子卡片：模块收到的输入数值实时状态（中英对照网格）。"""
        meta = self.shell.engine.modules.meta(module_id) or {}
        if meta.get("realtime_manager"):
            return self._detector_block(module_id, cfg, expanded=expanded)
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
        return W.panel(W.collapsible("实时数据", inner,
                                     subtitle="（输入数值 · 中英对照）",
                                     expanded=expanded), padding=14)

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

    def _detector_block(self, module_id: str, cfg: dict, *,
                        expanded: bool = True) -> object:
        inner = W.stack(spacing=6, h="stretch")
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
        return W.panel(W.collapsible("实时参数", inner,
                                     subtitle="（参数名 ← 检测行为，改动即时生效）",
                                     expanded=expanded), padding=14)

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
        # 原生 reload_config + 宿主逻辑表（临时变量/事件流）一并重载
        self.shell.submit(self.shell.engine.modules.reload(module_id))

    def _detector_error(self, module_id: str, entry: dict) -> str:
        inst = self.shell.engine.modules.instance(module_id)
        bridge = getattr(inst, "bridge", None)
        for det in getattr(bridge, "detectors", None) or []:
            if det.name == str(entry.get("name") or "") and det.error:
                return det.error
        return ""


    def _var_pool(self, module_id: str) -> list[str]:
        """表达式变量池：模块声明参数 ∪ 模块自定义参数 ∪ 临时变量 ∪
        核心输出参数 ∪ 运行期信号。"""
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
        # 临时变量（模块声明 + 用户表达式行）全部可被表达式引用
        for spec in self.shell.engine.modules.temp_specs_for(module_id):
            if spec.get("key"):
                pool.add(str(spec["key"]))
        cfg = self.shell.engine.modules.settings_for(module_id)
        for row in (cfg.get("temps") or []):
            if isinstance(row, dict) and row.get("name"):
                pool.add(str(row["name"]))
        return sorted(pool)

    def _temp_pool(self, module_id: str) -> list[str]:
        """事件流绑定用变量池：仅临时参数（模块自动注册 + 用户表达式行）。

        与表达式变量池（_var_pool，可含核心参数）不同，事件流动作绑定
        只允许引用临时变量——数据一律经临时变量流转。
        """
        modules = self.shell.engine.modules
        pool: set[str] = set()
        for spec in modules.temp_specs_for(module_id):
            if spec.get("key"):
                pool.add(str(spec["key"]))
        cfg = modules.settings_for(module_id)
        for row in (cfg.get("temps") or []):
            if isinstance(row, dict) and row.get("name"):
                pool.add(str(row["name"]))
        return sorted(pool)

    # ------------------------------------------------------------- 事件流

    _TRIGGERS = (("period", "周期更新"), ("change", "变量变更时"),
                 ("if", "if 判断"))

    def _events_block(self, module_id: str, cfg: dict, varpool, *,
                      expanded: bool = True) -> object:
        """事件流大卡片：事件小卡片列表（驱动事件 + 动作直列）。"""
        inner = W.stack(spacing=8, h="stretch")
        inner.Children.Append(W.text(
            "每张事件小卡片 = 驱动事件 + 动作直列：周期更新（按毫秒轮询）、"
            "变量变更时（值变化即触发）、if 判断（判断体为真触发一次，可写 "
            "bool 变量名或 {HP} > 40 式条件）。动作不做运算——「输入」把变量"
            "当前值派发给核心参数（触发即生效，同值也重复执行），「输出」把"
            "核心信号实时值写入临时变量；运算请写在临时变量表。",
            size=11, color="text3", wrap=True))
        cards = [e for e in (cfg.get("events") or []) if isinstance(e, dict)]
        if not cards:
            inner.Children.Append(self._placeholder_row(
                "（暂无事件：点击下方「添加事件」新建）"))
        for card in cards:
            inner.Children.Append(self._event_card(module_id, cfg, card,
                                                   varpool))
        inner.Children.Append(self._add_row(
            "添加事件", lambda s, e, _m=module_id: self._add_event(_m)))
        return W.panel(W.collapsible("事件流", inner,
                                     subtitle="（事件小卡片 · 驱动事件 → 动作直列）",
                                     expanded=expanded), padding=14)

    def _event_card(self, module_id: str, cfg: dict, card: dict,
                    varpool) -> object:
        inner = W.stack(spacing=6, h="stretch")
        head = W.grid(W.fixed(120), W.auto(), W.star(1), W.auto())
        head.ColumnSpacing = 8

        def _commit_name(text, _c=card, _cfg=cfg):
            if self._updating:
                return
            name = (text or "").strip()
            if name and _c.get("name") != name:
                _c["name"] = name
                _cfg.save()

        name_box = W.text_box(text=str(card.get("name") or ""), width=120)
        name_box.TextChanged += lambda s, e, _b=name_box: _commit_name(
            (_b.Text or "").strip())
        head.Children.Append(W.put(_vcenter(name_box), 0))

        trigger = str(card.get("trigger") or "period")
        combo = _vcenter(W.combo([label for _k, label in self._TRIGGERS],
                                 selected=next(
                                     (i for i, (k, _l)
                                      in enumerate(self._TRIGGERS)
                                      if k == trigger), 0)))

        def _pick_trigger(sender, args, _c=card, _cfg=cfg):
            if self._updating:
                return
            idx = combo.SelectedIndex
            if isinstance(idx, int) and 0 <= idx < len(self._TRIGGERS) \
                    and _c.get("trigger") != self._TRIGGERS[idx][0]:
                _c["trigger"] = self._TRIGGERS[idx][0]
                _cfg.save()
                self.rebuild()          # 驱动参数控件随种类切换

        combo.SelectionChanged += _pick_trigger
        head.Children.Append(W.put(_vcenter(combo), 1))
        head.Children.Append(W.put(self._trigger_arg(module_id, cfg, card,
                                                     varpool), 2))

        def _remove(_c=cfg, _card=card):
            if self._updating:
                return
            _c["events"] = [e for e in (_c.get("events") or [])
                            if e is not _card]
            _c.save()
            self.rebuild()

        head.Children.Append(W.put(_vcenter(W.text_button(
            "删除事件", symbol="Delete",
            on_click=lambda s, e: _remove())), 3))
        inner.Children.Append(head)
        actions = [a for a in (card.get("actions") or [])
                   if isinstance(a, dict)]
        if not actions:
            inner.Children.Append(self._placeholder_row(
                "（暂无动作：添加「输入」把变量派发给核心参数，"
                "或「输出」把核心信号写入变量）"))
        for i, action in enumerate(actions):
            inner.Children.Append(self._action_row(module_id, cfg, card, i,
                                                   action, varpool))
        adds = W.stack(horizontal=True, spacing=8)
        adds.Children.Append(W.text_button(
            "添加输入", symbol="Add",
            on_click=lambda s, e, _m=module_id, _c=card:
                self._add_action(_m, cfg, _c, "in")))
        adds.Children.Append(W.text_button(
            "添加输出", symbol="Add",
            on_click=lambda s, e, _m=module_id, _c=card:
                self._add_action(_m, cfg, _c, "out")))
        inner.Children.Append(adds)
        return W.panel(inner, padding=10)

    def _trigger_arg(self, module_id: str, cfg: dict, card: dict, varpool):
        """驱动事件参数控件：周期毫秒 / 变量下拉+实时值 / 判断体+真假。"""
        trigger = str(card.get("trigger") or "period")

        def _commit(value, _c=card, _cfg=cfg):
            if self._updating:
                return
            if _c.get("arg") != value:
                _c["arg"] = value
                _cfg.save()

        if trigger == "period":
            try:
                current = max(50, int(float(card.get("arg") or 100)))
            except (TypeError, ValueError):
                current = 100
            return W.number_box(current, 50, 3600000, width=130,
                                on_commit=lambda v: _commit(int(v)))

        if trigger == "change":
            cell: dict = {"kind": "var", "name": str(card.get("arg") or "")}
            combo = self._var_combo(cell["name"], varpool, cell,
                                    on_pick=_commit)
            live_tb = W.text(_fmtv(self._event_values(module_id)
                                   .get(cell["name"])),
                             size=12, bold=W.SEMIBOLD, family="Consolas",
                             trimming=True, v="center")
            self._live_event.append((live_tb, module_id, cell))
            wrap = W.grid(W.fixed(200), W.star(1))
            wrap.Children.Append(W.put(combo, 0))
            wrap.Children.Append(W.put(_gap(live_tb, 12), 1))
            return wrap

        cell = {"kind": "if", "cond": str(card.get("arg") or "")}

        def _commit_cond(text, _cell=cell):
            _cell["cond"] = text
            _commit(text)

        field = self._expr_field(str(card.get("arg") or ""), varpool,
                                 placeholder="bool变量 或 {HP} > 40",
                                 on_commit=_commit_cond)
        truth_tb = W.text("—", size=12, bold=W.SEMIBOLD, family="Consolas",
                          v="center")
        self._live_event.append((truth_tb, module_id, cell))
        wrap = W.grid(W.star(1), W.auto())
        wrap.Children.Append(W.put(field, 0))
        wrap.Children.Append(W.put(_gap(truth_tb, 12), 1))
        return wrap

    def _var_combo(self, current: str, varpool, cell: dict,
                   on_pick=None) -> object:
        """变量下拉：首项「（未选择）」，池外的存量引用追加在尾；
        选择即更新 cell（实时可视化跟随切换），on_pick 提交落盘。"""
        pool = ["（未选择）"] + [str(name) for name in varpool]
        current = str(current or "").strip()
        if current and current not in pool:
            pool.append(current)
        combo = _vcenter(W.combo(pool,
                                 selected=pool.index(current) if current else 0))

        def _pick(sender, args):
            sel = combo.SelectedIndex
            if self._updating or not isinstance(sel, int) or sel < 0:
                return
            name = "" if sel == 0 else pool[sel]
            cell["name"] = name
            if on_pick is not None:
                on_pick(name)

        combo.SelectionChanged += _pick
        return combo

    def _action_row(self, module_id: str, cfg: dict, card: dict, index: int,
                    action: dict, varpool) -> object:
        direction = str(action.get("dir") or "in")

        def _pick(sender, args, _a=action, _c=cfg):
            if self._updating:
                return
            idx = combo.SelectedIndex
            choices = self._param_choices(cfg, direction)
            if isinstance(idx, int) and 0 <= idx < len(choices) \
                    and _a.get("param") != choices[idx][0]:
                _a["param"] = choices[idx][0]
                _c.save()

        def _commit_var(name, _a=action, _c=cfg):
            if self._updating:
                return
            if _a.get("var") != name:
                _a["var"] = name
                _c.save()

        def _remove(_c=cfg, _card=card, _i=index):
            if self._updating:
                return
            _card["actions"] = [a for j, a in
                                enumerate(_card.get("actions") or [])
                                if j != _i]
            _c.save()
            self.rebuild()

        choices = self._param_choices(cfg, direction)
        current = str(action.get("param") or "")
        idx = next((i for i, (k, _l) in enumerate(choices) if k == current), 0)
        combo = _vcenter(W.combo([label for _k, label in choices],
                                 selected=idx))
        combo.SelectionChanged += _pick
        cell: dict = {"kind": "var", "name": str(action.get("var") or "")}
        # 事件流绑定的变量只允许临时参数（模块自动注册 + 用户表达式行）
        var_combo = self._var_combo(cell["name"], self._temp_pool(module_id),
                                    cell, on_pick=_commit_var)
        live_tb = W.text(_fmtv(self._event_values(module_id)
                               .get(cell["name"])),
                         size=12, bold=W.SEMIBOLD, family="Consolas",
                         trimming=True, v="center")
        self._live_event.append((live_tb, module_id, cell))
        return self._row(_ACTION_COLS, [
            _vcenter(W.text("输入" if direction == "in" else "输出",
                            size=12, bold=W.SEMIBOLD,
                            color="accent_text" if direction == "in"
                            else "success")),
            combo,
            _vcenter(W.text("←" if direction == "in" else "→", size=14,
                            family="Consolas", color="text3")),
            var_combo,
            live_tb,
            _vcenter(W.text_button("删除", symbol="Delete",
                                   on_click=lambda s, e: _remove())),
        ])

    def _param_choices(self, cfg: dict,
                       direction: str) -> list[tuple[str, str]]:
        """动作可选括参数：输入 = 核心输入参数；输出 = 核心输出信号。"""
        if direction == "in":
            return list(self._core_choices)
        return self._output_ids(cfg)

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
            for spec in output_specs(info["family"],
                                     int(info.get("index", 1))):
                if spec["key"] not in seen:
                    seen.add(spec["key"])
                    ids.append((spec["key"],
                                f"{spec['label']}（{spec['type']}）"))
        if "Action" not in seen:
            ids.append(("Action", "App 按键反馈（Int）"))
        return ids

    def _add_action(self, module_id: str, cfg: dict, card: dict,
                    direction: str) -> None:
        if self._updating:
            return
        actions = [a for a in (card.get("actions") or [])
                   if isinstance(a, dict)]
        choices = self._param_choices(cfg, direction)
        used = {str(a.get("param") or "") for a in actions
                if str(a.get("dir") or "in") == direction}
        default = next((k for k, _l in choices if k not in used),
                       choices[0][0] if choices else "")
        actions.append({"dir": direction, "param": default, "var": ""})
        card["actions"] = actions
        cfg.save()
        self.rebuild()

    def _add_event(self, module_id: str) -> None:
        if self._updating:
            return
        cfg = self.shell.engine.modules.settings_for(module_id)
        cards = [e for e in (cfg.get("events") or []) if isinstance(e, dict)]
        used = {str(c.get("name") or "") for c in cards}
        serial = 1
        while f"事件{serial}" in used:
            serial += 1
        cards.append({"name": f"事件{serial}", "trigger": "period",
                      "arg": 100, "actions": []})
        cfg["events"] = cards
        cfg.save()
        self.rebuild()

    def _temps_block(self, module_id: str, cfg: dict, varpool, *,
                     expanded: bool = True) -> object:
        """临时变量面板：模块声明行（模块维护）+ 用户表达式行（引擎求值）。"""
        modules = self.shell.engine.modules
        declared_keys = {str(s.get("key") or "")
                         for s in modules.temp_specs_for(module_id)}
        user_names: set[str] = set()
        rows = []
        for entry in (cfg.get("temps") or []):
            if isinstance(entry, dict):
                user_names.add(str(entry.get("name") or ""))
                rows.append(self._temp_row(module_id, cfg, entry, varpool))
        for key in sorted(declared_keys - user_names):
            spec = next((s for s in modules.temp_specs_for(module_id)
                         if str(s.get("key") or "") == key), {})
            rows.insert(0, self._declared_temp_row(module_id, key, spec))
        if not rows:
            rows.append(self._placeholder_row(
                "（暂无临时变量：点击下方「添加变量」新建，或由模块声明）"))
        rows.append(self._add_row(
            "添加变量",
            lambda s, e, _m=module_id: self._add_temp(_m)))
        return self._block(
            "临时变量（名字 ← 表达式：每次映射重算按序求值，全部表达式与"
            "事件条件可 {名} 引用；自引用取上一轮值可做累加器，模块声明的"
            "变量由模块代码读写）",
            _TEMP_COLS, _TEMP_HEAD, rows, tail_button=True, expanded=expanded)

    def _declared_temp_row(self, module_id: str, key: str, spec: dict) -> object:
        label = str(spec.get("label") or "")
        desc = str(spec.get("desc") or "")
        caption = f"{key}（模块维护）" if not label else f"{key} · {label}"
        name_cell = W.stack(spacing=1, v="center")
        name_cell.Children.Append(W.text(caption, size=12, v="center"))
        if desc:
            name_cell.Children.Append(W.text(desc, size=10, color="text3",
                                             trimming=True))
        live_tb = W.text(_fmtv(self._temp_live(module_id, key)), size=12,
                         bold=W.SEMIBOLD, family="Consolas", trimming=True,
                         v="center")
        self._live_temps.append((live_tb, module_id, key))
        return self._row(_TEMP_COLS, [
            _vcenter(name_cell),
            _vcenter(W.text("（模块维护）", size=11, color="text3")),
            live_tb,
            _vcenter(W.text("", size=12)),
        ])

    def _temp_row(self, module_id: str, cfg: dict, entry: dict,
                  varpool) -> object:
        def _commit_name(text: str, _e=entry, _c=cfg) -> None:
            if self._updating:
                return
            name = (text or "").strip()
            if not name or name == str(_e.get("name") or ""):
                return
            _e["name"] = name
            _c.save()
            self.rebuild()

        def _commit_expr(text: str, _e=entry, _c=cfg) -> None:
            if self._updating:
                return
            if _e.get("expr") != text:
                _e["expr"] = text
                _c.save()

        def _remove(_e=entry, _c=cfg) -> None:
            if self._updating:
                return
            _c["temps"] = [r for r in (_c.get("temps") or []) if r is not _e]
            _c.save()
            self.rebuild()

        name_box = W.suggest_box(text=str(entry.get("name") or ""),
                                 choices=varpool, placeholder="变量名")
        name_box.HorizontalAlignment = HorizontalAlignment.Stretch
        name_box.VerticalAlignment = VerticalAlignment.Center
        name_box.QuerySubmitted += lambda sender, args, _b=name_box: \
            _commit_name((_b.Text or "").strip())
        name_box.LostFocus += lambda sender, args, _b=name_box: \
            _commit_name((_b.Text or "").strip())
        expr_field = self._expr_field(str(entry.get("expr") or ""), varpool,
                                      placeholder="{loudness} > 40",
                                      on_commit=_commit_expr)
        live_tb = W.text(_fmtv(self._temp_live(
            module_id, str(entry.get("name") or ""))), size=12,
            bold=W.SEMIBOLD, family="Consolas", trimming=True, v="center")
        self._live_temps.append((live_tb, module_id,
                                 str(entry.get("name") or "")))
        delete = _vcenter(W.text_button("删除", symbol="Delete",
                                        on_click=lambda s, e: _remove()))
        return self._row(_TEMP_COLS, [name_box, expr_field, live_tb, delete])

    def _temp_live(self, module_id: str, name: str):
        return self.shell.engine.modules.temps_space(module_id).get(name)

    def _add_temp(self, module_id: str) -> None:
        if self._updating:
            return
        cfg = self.shell.engine.modules.settings_for(module_id)
        rows = [r for r in (cfg.get("temps") or []) if isinstance(r, dict)]
        used = {str(r.get("name") or "") for r in rows}
        serial = 1
        while f"temp{serial}" in used:
            serial += 1
        rows.append({"name": f"temp{serial}", "expr": ""})
        cfg["temps"] = rows
        cfg.save()
        self.rebuild()

    # -------------------------------------------------------------- 模块设置

    def _settings_block(self, module_id: str, spec: dict, cfg: dict,
                        varpool) -> object:
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
        # 模块设置默认折叠：卡片纵向以映射表为主，设置项点击标题展开
        return W.panel(W.collapsible(
            "OSC 地址与端口" if module_id == "osc_bridge" else "模块设置",
            body,
            subtitle="（修改后重新开关上方桥接生效）" if module_id == "osc_bridge"
                     else "（修改后重新开关上方模块生效）",
            expanded=False), padding=14)

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

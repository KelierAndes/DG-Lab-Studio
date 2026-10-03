
from __future__ import annotations

import math
import time
from collections import deque

from win32more import asyncui
from win32more.Microsoft.UI.Xaml import TextAlignment, Thickness, Visibility
from win32more.Microsoft.UI.Xaml.Controls import (
    Canvas,
    ComboBoxItem,
    MenuFlyout,
    MenuFlyoutItem,
    Page,
    ToolTip,
    ToolTipService,
)
from win32more.Microsoft.UI.Xaml.Media import (
    PenLineJoin,
    PointCollection,
    SolidColorBrush,
)
from win32more.Microsoft.UI.Xaml.Shapes import Ellipse, Polyline
from win32more.Windows.Foundation import Point
from win32more.Windows.UI import Color
from win32more.winui3 import XamlClass

from dglab.state import family_of
from dglab import keys as keyboard_keys
from ui import charts
from ui import live, theme, widgets as W
from ui.dialogs import confirm_dialog, prompt_text
from ui.paths import xaml

class CardView:

    def __init__(self, sid: str, family: str):
        self.sid = sid
        self.family = family
        self.labels: dict[str, object] = {}
        self.meters: dict[str, object] = {}
        self.wave_combos: dict[str, object] = {}
        self.pill_host = None
        self.chart_host = None
        self.reading_tb = None
        self.edge_tb = None
        self.summary_tb = None
        self.hold_label = None
        self.led_combo = None
        self.chart_image = None
        self.hist_sig: tuple | None = None
        self.direct_a = None
        self.direct_b = None
        self.bindings: dict[int, object] = {}
        self.binding_inputs: dict[int, object] = {}
        self.binding_keys: dict[int, object] = {}
        self.binding_canvas = None
        self.profile_combo = None
        self.button_glows: dict[int, object] = {}

class _Series:
    """line_chart 的一条曲线（鸭子类型，匹配原型 PressureSeries）。"""

    def __init__(self, label: str, color: int, points: tuple):
        self.label = label
        self.color = color
        self.points = points


class ControlPage(XamlClass, Page):
    def __init__(self, shell):
        super().__init__()
        self.shell = shell
        self._updating = False
        self._cards: dict[str, CardView] = {}
        self._slots_seen: tuple = ()
        self._wave_values: dict[str, list[str]] = {}
        self._pressure_hist: dict[str, deque] = {}
        self._last_pressure_sample: dict[str, float] = {}
        self._last_chart_render = 0.0
        self._last_pressure_render = 0.0
        self._last_value_render = 0.0
        self._glow_state: dict[int, tuple] = {}
        self.LoadComponentFromFile(xaml("ControlPage.xaml"), encoding="utf-8")
        self.rebuild()

    def on_notify(self) -> None:
        self.rebuild()

    def tick(self) -> None:
        state = self.shell.state
        slots = tuple(sorted(state.slots))
        if slots != self._slots_seen:
            self.rebuild()
            return
        now = time.monotonic()
        self._sample_pressure(now)
        self._refresh_glows()
        if now - self._last_value_render >= 0.2:
            self._last_value_render = now
            self._update_values(state)
        if now - self._last_chart_render >= 0.25:
            self._last_chart_render = now
            self._render_wave_charts()
        if now - self._last_pressure_render >= 0.5:
            self._last_pressure_render = now
            self._render_pressure_charts()

    def rebuild(self) -> None:
        state = self.shell.state
        self._slots_seen = tuple(sorted(state.slots))
        W.page_head(
            self.HeadHost,
            {"title": "控制",
             "subtitle": "每台设备一张控制卡片：输出强度、波形选择与实时柱状波形图",
             "breadcrumb": ["控制台", "控制"]},
            actions=[
                W.text_button("全部归零", symbol="Clear",
                              on_click=lambda s, e: self._clear_all()),
                W.estop_button("急停全部设备",
                               on_click=lambda s, e: self._estop()),
            ],
        )

        host = self.CardsHost
        host.Children.Clear()
        self._cards.clear()
        self._glow_state.clear()
        if not state.slots:
            host.Children.Append(self._empty_note())
            return
        for sid in sorted(state.slots):
            slot = state.slots[sid]
            family = family_of(slot.type)
            view = CardView(sid, family)
            self._cards[sid] = view
            builder = self._sensor_card if family == "BMTR" else self._output_card
            host.Children.Append(builder(view, sid, slot))

    def _empty_note(self):
        row = W.grid(W.star(1), W.auto())
        row.Children.Append(W.put(
            W.text("尚未接入设备：先在「连接」页接入后再回来控制",
                   size=12, color="text3", v="center"), 0))
        button = W.text_button("前往连接页", symbol="Link", accent=True,
                               on_click=lambda s, e: self.shell.goto("connect"))
        row.Children.Append(W.put(button, 1))
        return W.box(
            corner=6, padding=Thickness(12, 9, 12, 9),
            background=theme.brush("section"), border=theme.brush("stroke"),
            child=row, h="stretch")

    def _int_field(self, target: dict, key: str, header: str, low: int, high: int,
                   width: float, default: int, round_to: int = 1,
                   keep_zero: bool = False) -> object:
        box = W.number_field(header, target.get(key, default), width)

        def _commit(sender, args):
            if self._updating:
                return
            try:
                value = int(box.Text)
            except (TypeError, ValueError):
                return
            if round_to > 1 and not (keep_zero and value <= 0):
                value = max(round_to, (value + round_to // 2) // round_to * round_to)
            value = max(low, min(high, value))
            if keep_zero and value <= 0:
                value = 0
            target[key] = value
            text = str(value)
            if box.Text != text:
                self._updating = True
                try:
                    box.Text = text
                finally:
                    self._updating = False

        box.TextChanged += _commit
        return box

    def _device_params(self, view: CardView, sid: str) -> object:
        engine = self.shell.engine
        ovc = view.family == "OVC"
        step = 10 if ovc else 1
        low = 10 if ovc else 0
        settings = engine.config.setdefault("device_settings", {}).setdefault(sid, {})
        if ovc:
            settings.setdefault(
                "max_strength",
                live.clamp_ovc_strength(engine.config.get("max_strength", 100)))
            settings.setdefault(
                "fire_strength",
                live.clamp_ovc_strength(engine.config.get("fire_strength", 0),
                                        allow_zero=True))
            settings.setdefault(
                "strength_step",
                live.round_step10(max(10, min(50, int(engine.config.get("strength_step", 1))))))
        row = W.stack(horizontal=True, spacing=10, v="center")
        row.Children.Append(self._int_field(
            settings, "max_strength", "最大强度上限", low, 200, 110,
            int(engine.config.get("max_strength", 100)), round_to=step))
        row.Children.Append(self._int_field(
            settings, "fire_strength", "开火强度", 0, 200, 100,
            int(engine.config.get("fire_strength", 0)), round_to=step,
            keep_zero=ovc))
        row.Children.Append(self._int_field(
            settings, "strength_step", "加减步长", low if ovc else 1, 50, 80,
            int(engine.config.get("strength_step", 1)), round_to=step))
        row.Children.Append(W.text(
            "仅对本设备生效 · 开火强度 0 = 跟随本卡上限"
            + (" · OVC 强度按 10 取整" if ovc else ""),
            size=11, color="text3", v="center"))
        return W.panel(row, padding=10)

    def _output_card(self, view: CardView, sid: str, slot):
        engine = self.shell.engine
        family = view.family

        head, pill_host = self._card_head(view, sid, slot, output=True)
        view.pill_host = pill_host

        rows = W.stack(spacing=8, h="stretch")
        for ch in ("A", "B"):
            rows.Children.Append(self._output_row(view, sid, ch))

        chart_panel = W.panel(self._chart_inner(view), padding=12)

        actions = W.stack(horizontal=True, spacing=8, v="center")

        def _fire(sender, args):
            self.shell.submit(engine.fire(slot_id=sid))

        actions.Children.Append(W.text_button("一键开火", symbol="Play", accent=True,
                                              on_click=_fire))
        hold_border, hold_label = W.hold_border("按住持续开火 (放开停止)")
        hold_border.PointerPressed += self._make_hold(sid, True)
        hold_border.PointerReleased += self._make_hold(sid, False)
        hold_border.PointerCaptureLost += self._make_hold(sid, False)
        view.hold_label = hold_label
        actions.Children.Append(hold_border)

        if self.shell.state.backend == "ble":
            actions.Children.Append(self._led_combo(view, sid))
            if family == "OVC":
                actions.Children.Append(self._profile_selector(view))

        inner = W.stack(spacing=12, h="stretch")
        inner.Children.Append(head)
        inner.Children.Append(W.divider())
        inner.Children.Append(self._device_params(view, sid))
        inner.Children.Append(rows)
        inner.Children.Append(chart_panel)
        inner.Children.Append(actions)

        if family == "OVC" and self.shell.state.backend == "ble":
            inner.Children.Append(self._binding_blocks(view))

        inner.Children.Append(W.text(
            "强度用加减键调节（步长见本卡参数）；波形可下拉跳变或 ‹ / › 逐步切换；"
            "「归零」清强度并切回静默。开火在静默时临时切持续波形，结束后自动恢复。",
            size=11, color="text3", wrap=True))
        return W.card(inner)

    def _output_row(self, view: CardView, sid: str, ch: str) -> object:
        engine = self.shell.engine
        family = view.family
        items = live.wave_items(family)
        if family not in self._wave_values:
            self._wave_values[family] = [value for _label, value in items]

        def _adjust(delta):
            def handler(sender, args):
                try:
                    step = max(1, min(50, int(engine.device_setting(sid, "strength_step"))))
                except (TypeError, ValueError):
                    step = 1
                if view.family == "OVC":
                    step = live.round_step10(max(10, step))
                self.shell.submit(engine.add_strength(ch, delta * step, slot_id=sid))
            return handler

        steps = W.stack(horizontal=True, spacing=4, v="center")
        steps.Children.Append(W.button("−", width=30, height=30, v="center",
                                       on_click=_adjust(-1)))
        steps.Children.Append(W.button("+", width=30, height=30, v="center",
                                       on_click=_adjust(+1)))

        value = W.stack(spacing=2, v="center")
        label = W.text(f"{ch}: 0/0", size=13, bold=W.SEMIBOLD)
        meter = W.meter(0, width=86)
        value.Children.Append(label)
        value.Children.Append(meter)
        view.labels[ch] = label
        view.meters[ch] = meter

        def _on_combo(sender, args):
            self._on_wave_combo(view, ch, combo)

        combo = W.combo([label for label, _v in items],
                        width=150, on_changed=_on_combo)
        view.wave_combos[ch] = combo

        def _step_wave(delta):
            def handler(sender, args):
                values = self._wave_values.get(family) or []
                if not values:
                    return
                index = combo.SelectedIndex
                if index is None or index < 0:
                    index = 0
                combo.SelectedIndex = (index + delta) % len(values)
            return handler

        wave = W.stack(horizontal=True, spacing=6, v="center")
        wave.Children.Append(W.text("波形", size=11, color="text3", v="center"))
        wave.Children.Append(W.button("‹", width=30, height=30, v="center",
                                      on_click=_step_wave(-1)))
        wave.Children.Append(combo)
        wave.Children.Append(W.button("›", width=30, height=30, v="center",
                                      on_click=_step_wave(+1)))

        def _reset(sender, args):
            self.shell.submit(engine.reset_strength(ch, slot_id=sid))

        wave.Children.Append(W.text_button("归零", symbol="Clear", on_click=_reset))

        direct = W.stack(horizontal=True, spacing=8, v="center", margin=Thickness(0, 6, 0, 0))
        box = W.text_box(placeholder=f"{ch} 强度", width=150)
        if ch == "A":
            view.direct_a = box
        else:
            view.direct_b = box
        direct.Children.Append(box)

        if ch == "B":
            def _apply(sender, args):
                self._apply_direct(view, sid)

            direct.Children.Append(W.text_button("应用直接设置", symbol="Accept",
                                                 on_click=_apply))
            hint = ("直接设置 10 的倍数 (自动取整)，任填其一或同时填写" if family == "OVC"
                    else "直接设置 0-200，任填其一或同时填写")
            direct.Children.Append(W.text(hint, size=11, color="text3", v="center"))

        g = W.grid(W.auto(), W.fixed(96), W.star(1))
        g.ColumnSpacing = 14
        g.Children.Append(W.put(steps, 0))
        g.Children.Append(W.put(value, 1))
        g.Children.Append(W.put(wave, 2))
        row = W.box(
            corner=6, padding=Thickness(10, 8, 10, 8),
            background=theme.brush("track"), child=g, h="stretch")

        outer = W.stack(spacing=0, h="stretch")
        outer.Children.Append(row)
        outer.Children.Append(direct)
        return outer

    def _apply_direct(self, view: CardView, sid: str) -> None:
        ovc = view.family == "OVC"
        for ch, box in (("A", view.direct_a), ("B", view.direct_b)):
            if box is None:
                continue
            text = (box.Text or "").strip()
            if not text:
                continue
            try:
                value = int(text)
            except ValueError:
                self.shell.logs.append(f"直接设置 {ch} 失败: 不是整数 ({text!r})")
                continue
            if ovc:
                value = live.clamp_ovc_strength(value)
            self.shell.submit(self.shell.engine.set_strength(ch, value, slot_id=sid))
            box.Text = str(value) if ovc else ""

    def _on_wave_combo(self, view: CardView, ch: str, combo) -> None:
        if self._updating:
            return
        values = self._wave_values.get(view.family) or []
        index = combo.SelectedIndex
        if index is None or index < 0 or index >= len(values):
            return
        value = values[index]
        self.shell.engine._selected_wave[ch] = value
        self.shell.submit(self.shell.engine.set_wave(ch, value, slot_id=view.sid))

    def _make_hold(self, sid: str, active: bool):
        engine = self.shell.engine

        def handler(sender, args):
            view = self._cards.get(sid)
            if active:
                self.shell.submit(engine.fire_start(slot_id=sid))
                if view is not None and view.hold_label is not None:
                    view.hold_label.Text = "开火中… (放开停止)"
            else:
                self.shell.submit(engine.fire_stop(slot_id=sid))
                if view is not None and view.hold_label is not None:
                    view.hold_label.Text = "按住持续开火 (放开停止)"

        return handler

    def _led_combo(self, view: CardView, sid: str) -> object:
        combo = W.combo((), width=150)
        for _byte, label, hexs in live.LED_OPTIONS:
            item = ComboBoxItem()
            row = W.stack(horizontal=True, spacing=8, v="center")
            row.Children.Append(W.box(width=10, height=10, corner=5,
                                      background=theme.solid(hexs)))
            row.Children.Append(W.text(label, size=13))
            item.Content = row
            combo.Items.Append(item)
        combo.SelectedIndex = 1
        view.led_combo = combo

        def _changed(sender, args):
            if self._updating:
                return
            index = combo.SelectedIndex
            if 0 <= index < len(live.LED_OPTIONS):
                byte = live.LED_OPTIONS[index][0]
                self.shell.submit(self.shell.engine.set_led_color(byte,
                                                                  slot_id=sid))

        combo.SelectionChanged += _changed
        cell = W.stack(spacing=4)
        cell.Children.Append(W.text("LED 颜色 (蓝牙)", size=11, color="text3"))
        cell.Children.Append(combo)
        return cell

    # ---- 负鼠按键映射配置文件 ----

    def _active_bindings(self) -> dict:
        """当前激活配置文件的按键映射 (bit → 动作)."""
        ble = self.shell.engine.config.setdefault("ble", {})
        profiles = ble.setdefault("ovc_profiles", {})
        active = ble.setdefault("ovc_profile", "默认")
        return profiles.setdefault(active, {})

    def _profile_selector(self, view: CardView) -> object:
        engine = self.shell.engine
        ble = engine.config.setdefault("ble", {})
        profiles = ble.setdefault("ovc_profiles", {})
        names = list(profiles) or ["默认"]
        active = ble.get("ovc_profile") if ble.get("ovc_profile") in names else names[0]
        combo = W.combo(names, selected=names.index(active), width=140)
        view.profile_combo = combo

        def _switch(sender, args):
            if self._updating:
                return
            index = combo.SelectedIndex
            if not (0 <= index < len(names)):
                return
            target = names[index]
            if ble.get("ovc_profile") == target:
                return
            missing = engine.binding_missing_modules(profiles.get(target) or {})
            if not missing:
                ble["ovc_profile"] = target
                engine.save_config()
                self.rebuild()
                return
            # 目标配置引用了未启用模块的动作：先还原选择，再弹窗询问
            module_ids = engine.modules_for_bindings(missing)
            self._updating = True
            try:
                combo.SelectedIndex = names.index(active)
            finally:
                self._updating = False
            asyncui.create_task(
                self._confirm_load_profile(target, module_ids, missing))

        combo.SelectionChanged += _switch

        def _new(sender, args):
            template = dict(self._active_bindings())
            index = 1
            while f"配置{index}" in profiles:
                index += 1
            name = f"配置{index}"
            profiles[name] = template
            ble["ovc_profile"] = name
            engine.save_config()
            self.rebuild()

        new_btn = W.button("＋新建", width=64, height=32, v="center", on_click=_new)
        self._bv_tip(new_btn, "以当前配置文件的映射为模板新建一份，并切换过去")

        def _rename(sender, args):
            asyncui.create_task(self._rename_profile_flow())

        rename_btn = W.button("✎改名", width=64, height=32, v="center",
                              on_click=_rename)
        self._bv_tip(rename_btn, "重命名当前按键映射配置文件")

        row = W.stack(horizontal=True, spacing=6, v="center")
        row.Children.Append(combo)
        row.Children.Append(new_btn)
        row.Children.Append(rename_btn)
        cell = W.stack(spacing=4)
        cell.Children.Append(W.text("按键映射配置文件 (负鼠)", size=11, color="text3"))
        cell.Children.Append(row)
        return cell

    async def _confirm_load_profile(self, target: str, module_ids: list[str],
                                    missing: dict) -> None:
        engine = self.shell.engine
        if module_ids:
            names = []
            for mid in module_ids:
                meta = engine.modules.meta(mid) or {}
                names.append(meta.get("name") or mid)
            lines = "\n".join(f"· {name}" for name in names)
            message = (f"配置「{target}」的按键映射使用了以下未启用模块提供的动作：\n{lines}\n\n"
                       "启用相关模块后加载该配置；拒绝则保持当前配置不变。")
            ok = await confirm_dialog(self.shell, "加载配置需要启用模块", message,
                                      primary="启用并加载", close="拒绝加载")
            if not ok:
                self.shell.logs.append(f"已拒绝加载配置「{target}」")
                return
            for mid in module_ids:
                self.shell.submit(engine.modules.install(mid))
        else:
            await confirm_dialog(
                self.shell, "配置包含未知动作",
                f"配置「{target}」包含无法识别的按键动作，无法加载。"
                "可切换到该配置后将相关绑定改回其他动作。",
                close="知道了")
            return
        ble = engine.config.setdefault("ble", {})
        ble["ovc_profile"] = target
        engine.save_config()
        self.rebuild()

    async def _rename_profile_flow(self) -> None:
        engine = self.shell.engine
        ble = engine.config.setdefault("ble", {})
        profiles = ble.setdefault("ovc_profiles", {})
        old = ble.get("ovc_profile") or next(iter(profiles), "默认")
        new = await prompt_text(self.shell, "重命名配置文件",
                                f"将按键映射配置「{old}」重命名为：",
                                initial=old, primary="重命名")
        if new is None or new == old:
            return
        error = engine.rename_ovc_profile(old, new)
        if error:
            self.shell.logs.append(f"重命名失败：{error}")
            return
        self.shell.logs.append(f"配置文件已重命名：{old} → {new}")
        self.rebuild()

    # ---- 负鼠绑定块：官方离线模式页 1:1 复刻 ----
    # 坐标一律取官方截图像素系（647×291），经 _BV_SCALE 缩放平移后画到画布上。
    # 机身对称轴 x=316：十字键簇中心 246 与菱形键簇中心 386 互为镜像。
    # _BV_OX 取 -30：给左侧 OSC 输入框留出画布空间（输入框与选择按钮错开摆放）。
    _BV_SCALE = 1.5
    _BV_OX, _BV_OY = -30, 78

    _BV_BG = "#0B0B0B"
    _BV_EDGE = ("#8E8E8E", "#585858", "#414141")   # 机身三层描边（外→内）
    _BV_ART = "#8F8F8F"                            # 屏幕/键外层描边
    _BV_ART2 = "#6F6F6F"                           # 键内层描边（双层样式）
    _BV_GLYPH = "#C6C6C6"                          # 键面符号/字母
    _BV_LEAD = "#C9C9C9"                           # 指示线
    _BV_DOT = "#F2F2F2"                            # 指示线端点（按键内偏侧）
    _BV_TEXT = "#D9CFA6"                           # 标注文字（米黄）

    _BV_BODY = (188, 92, 444, 227, 16)              # x0 y0 x1 y1 切角
    _BV_SCREEN = (284, 112, 348, 143)
    # 十字键：中心 (246,165)，半长 38 使整体 76×76 与右侧键组（76×74）一致，
    # 臂厚 20 与中心圆直径一致；外围折角圆角化。
    _BV_CXKEY = (246, 165, 38, 10)                  # cx cy 半长 半宽
    _BV_DIRS = {8: ((246, 132), (241, 140), (251, 140)),
                9: ((246, 198), (241, 190), (251, 190)),
                10: ((216, 165), (224, 160), (224, 170)),
                11: ((276, 165), (268, 160), (268, 170))}
    _BV_FACES = {14: (386, 141, "G"), 15: (361, 165, "D"),
                 12: (411, 165, "B"), 13: (386, 189, "A")}
    # 底部 SEL_1 / HOME / SEL_2：双层线稿，中心线 y=202 对齐键组最下缘
    _BV_SMALLS = {0: (((282, 202), (296, 194), (296, 210)),
                      ((285.5, 202), (294, 196.8), (294, 207.2))),
                  2: (((309, 194), (323, 194), (323, 210), (309, 210)),
                      ((311.3, 196.3), (320.7, 196.3), (320.7, 207.7),
                       (311.3, 207.7))),
                  1: (((350, 202), (336, 194), (336, 210)),
                      ((346.5, 202), (338, 196.8), (338, 207.2)))}
    # 指示线：终点圆点落在键面内、避开字符/箭头（左簇左移 8，右簇右移 8，
    # 关于轴 316 镜像对称）。右簇行 2=D、行 3=B：D 线从 G 键下方穿过 G/B 环
    # 间隙落到 D 面右上，B 线自行 3 斜上落到 B 面右侧。
    _BV_LINKS = {
        8: [(163, 104), (205, 104), (238, 137)],
        10: [(163, 141), (189, 141), (213, 165)],
        11: [(163, 178), (250, 178), (263, 165)],
        9: [(163, 215), (216, 215), (238, 193)],
        14: [(469, 104), (431, 104), (394, 141)],
        15: [(469, 141), (414, 141), (397, 158), (368, 158)],
        12: [(469, 178), (432, 178), (419, 165)],
        13: [(469, 215), (420, 215), (394, 189)],
        0: [(238, 240), (275, 240), (291, 224), (291, 202)],
        2: [(316, 248), (316, 202)],
        1: [(394, 240), (357, 240), (341, 224), (341, 202)],
    }
    # 四行标注 y=104/141/178/215，整体中心 159.5 = 机身高度中心（垂直居中）
    # bit -> (文字锚点x, y, 宽(画布px), 对齐)；锚点=文字靠近引导线一侧的边缘
    _BV_LABELS = {
        8: (158, 104, 130, "right"), 10: (158, 141, 130, "right"),
        11: (158, 178, 130, "right"), 9: (158, 215, 130, "right"),
        14: (474, 104, 130, "left"), 15: (474, 141, 130, "left"),
        12: (474, 178, 130, "left"), 13: (474, 215, 130, "left"),
        0: (234, 240, 130, "right"), 1: (398, 240, 130, "left"),
        2: (316, 262, 130, "center"),
    }
    _BV_BTN_H = 30                                  # 标注按钮高度（画布px）
    _BV_BOX_W = 130                                 # OSC 输入框宽（画布px）
    # 按下实时反馈的高亮覆盖层：bit -> (中心x, 中心y, 半径)（官方像素系）。
    # 十字键落在臂端箭头上，小键落在键面中心，菱形键覆盖整个双环。
    _BV_GLOW = {8: (246, 138, 9), 9: (246, 192, 9),
                10: (220, 165, 9), 11: (272, 165, 9),
                0: (291, 202, 9), 2: (316, 202, 9), 1: (341, 202, 9),
                14: (386, 141, 12.5), 15: (361, 165, 12.5),
                12: (411, 165, 12.5), 13: (386, 189, 12.5)}
    _BV_GLOW_FILL = Color(110, 90, 190, 255)        # 半透明亮蓝填充
    _BV_GLOW_STROKE = Color(235, 140, 210, 255)     # 近不透明描边
    _BV_GLOW_MIN = 0.25                             # 快按最短点亮时长（秒）
    _BV_GLOW_MAX = 8.0                              # 丢抬起沿时的兜底熄灭（秒）
    # 画布固定在深色底上，块内原生控件不随系统主题变化：
    # 用控件级 Resources 覆写 Button/TextBox 模板的主题资源键，
    # 亮色模式下仍是深色芯片观感，米黄文字保持可读。
    _BV_BTN_DARK = {
        "ButtonBackground": "#141414",
        "ButtonBackgroundPointerOver": "#1F1F1F",
        "ButtonBackgroundPressed": "#0D0D0D",
        "ButtonBackgroundDisabled": "#0D0D0D",
        "ButtonBorderBrush": "#585858",
        "ButtonBorderBrushPointerOver": "#7A7A7A",
        "ButtonBorderBrushPressed": "#414141",
        "ButtonBorderBrushDisabled": "#3A3A3A",
        "ButtonForeground": "#D9CFA6",
        "ButtonForegroundPointerOver": "#EADFBC",
        "ButtonForegroundPressed": "#C4BA94",
        "ButtonForegroundDisabled": "#6B6650",
    }
    _BV_BOX_DARK = {
        "TextControlBackground": "#141414",
        "TextControlBackgroundPointerOver": "#141414",
        "TextControlBackgroundFocused": "#141414",
        "TextControlForeground": "#D9CFA6",
        "TextControlForegroundPointerOver": "#D9CFA6",
        "TextControlForegroundFocused": "#D9CFA6",
        "TextControlBorderBrush": "#585858",
        "TextControlBorderBrushPointerOver": "#7A7A7A",
        "TextControlBorderBrushFocused": "#9A9A9A",
        "TextControlPlaceholderForeground": "#8A846C",
        "TextControlPlaceholderForegroundPointerOver": "#8A846C",
        "TextControlPlaceholderForegroundFocused": "#8A846C",
    }

    def _bv_pt(self, x: float, y: float) -> tuple:
        return ((x - self._BV_OX) * self._BV_SCALE,
                (y - self._BV_OY) * self._BV_SCALE)

    def _bv_poly(self, canvas, pts, stroke, *, width=1.2, fill=None,
                 close: bool = False) -> None:
        if close:
            pts = list(pts) + [pts[0]]
        coll = PointCollection()
        for x, y in pts:
            px, py = self._bv_pt(x, y)
            coll.Append(Point(px, py))
        pl = Polyline()
        pl.Points = coll
        pl.Stroke = stroke
        pl.StrokeThickness = width
        pl.StrokeLineJoin = PenLineJoin.Round
        if fill is not None:
            pl.Fill = fill
        canvas.Children.Append(pl)

    def _bv_rpoly(self, canvas, pts, stroke, radius, *, width=1.2,
                  fill=None) -> None:
        """闭合多边形折角圆角化：每个顶点沿两侧边各缩进 radius，配 Round 接头。"""
        n = len(pts)
        rounded = []
        for i in range(n):
            px, py = pts[i]
            ax, ay = pts[i - 1]
            bx, by = pts[(i + 1) % n]
            for (sx, sy), (ex, ey) in (((px, py), (ax, ay)),
                                       ((px, py), (bx, by))):
                dx, dy = sx - ex, sy - ey
                dist = math.hypot(dx, dy)
                if dist <= 0:
                    continue
                k = min(radius, dist / 2) / dist
                rounded.append((sx - dx * k, sy - dy * k))
        self._bv_poly(canvas, rounded, stroke, width=width, fill=fill,
                      close=True)

    def _bv_ring(self, canvas, cx, cy, r, stroke, *, width=1.2,
                 fill=None) -> None:
        e = Ellipse()
        side = r * 2 * self._BV_SCALE
        px, py = self._bv_pt(cx, cy)
        e.Width = side
        e.Height = side
        Canvas.SetLeft(e, px - side / 2)
        Canvas.SetTop(e, py - side / 2)
        e.Stroke = stroke
        e.StrokeThickness = width
        if fill is not None:
            e.Fill = fill
        canvas.Children.Append(e)

    def _bv_dark(self, control, keys: dict) -> None:
        rd = control.Resources
        for key, raw in keys.items():
            rd[key] = theme.solid(raw)

    def _bv_tip(self, element, message: str) -> None:
        tip = ToolTip()
        content = W.text(message, size=12, color="text2", wrap=True)
        content.MaxWidth = 220
        tip.Content = content
        ToolTipService.SetToolTip(element, tip)

    def flash_button(self, bit: int, pressed: bool) -> None:
        """shell 收到 ovc_button/ovc_button_up 事件后在 UI 线程调用。

        真机只在电平变化时发一次边沿事件：长按=按下后无事件，直到抬起。
        因此按下即点亮（保持到抬起沿），抬起时保证最短点亮 _BV_GLOW_MIN，
        _BV_GLOW_MAX 仅作为丢抬起沿（如断连）的兜底熄灭。
        """
        found = False
        now = time.monotonic()
        for view in self._cards.values():
            glow = view.button_glows.get(bit)
            if glow is None:
                continue
            found = True
            if pressed:
                glow.Visibility = Visibility.Visible
        if not found:
            return
        if pressed:
            self._glow_state[bit] = (now, now + self._BV_GLOW_MAX)
        else:
            ts = self._glow_state.get(bit, (0.0, 0.0))[0]
            self._glow_state[bit] = (ts, max(now, ts + self._BV_GLOW_MIN))

    def _refresh_glows(self) -> None:
        if not self._glow_state:
            return
        now = time.monotonic()
        for bit, (_ts, until) in list(self._glow_state.items()):
            if now < until:
                continue
            del self._glow_state[bit]
            for view in self._cards.values():
                glow = view.button_glows.get(bit)
                if glow is not None:
                    glow.Visibility = Visibility.Collapsed

    def _binding_blocks(self, view: CardView) -> object:
        bindings = self._active_bindings()
        bg = theme.solid(self._BV_BG)
        edges = [theme.solid(c) for c in self._BV_EDGE]
        art = theme.solid(self._BV_ART)
        art2 = theme.solid(self._BV_ART2)
        glyph = theme.solid(self._BV_GLYPH)
        lead = theme.solid(self._BV_LEAD)
        dot = theme.solid(self._BV_DOT)

        canvas = Canvas()
        canvas.Width = 1040
        canvas.Height = 340
        view.binding_canvas = canvas

        def at(el, x, y):
            Canvas.SetLeft(el, float(x))
            Canvas.SetTop(el, float(y))
            canvas.Children.Append(el)

        x0, y0, x1, y1, ch = self._BV_BODY

        def octagon(a, b, c, d, k):
            return [(a + k, b), (c - k, b), (c, b + k), (c, d - k),
                    (c - k, d), (a + k, d), (a, d - k), (a, b + k)]

        # 机身：三层切角描边（上下轮廓一致，无齿状凸起）
        self._bv_poly(canvas, octagon(x0, y0, x1, y1, ch), edges[0],
                      width=1.4, fill=bg, close=True)
        self._bv_poly(canvas, octagon(x0 + 5, y0 + 5, x1 - 5, y1 - 5, ch - 3),
                      edges[1], width=1.0, close=True)
        self._bv_poly(canvas, octagon(x0 + 10, y0 + 10, x1 - 10, y1 - 10,
                                      ch - 5), edges[2], width=1.0, close=True)

        # 屏显窗（中心 x=316，与机身同轴）
        sx0, sy0, sx1, sy1 = self._BV_SCREEN
        self._bv_poly(canvas, [(sx0, sy0), (sx1, sy0), (sx1, sy1),
                               (sx0, sy1)], art, fill=bg, close=True)

        # 十字方向键：收窄的双层圆角十字 + 双层中心圆 + 四向箭头（远离中心）
        ccx, ccy, half, arm = self._BV_CXKEY

        def cross(hf, af):
            return [(ccx - af, ccy - hf), (ccx + af, ccy - hf),
                    (ccx + af, ccy - af), (ccx + hf, ccy - af),
                    (ccx + hf, ccy + af), (ccx + af, ccy + af),
                    (ccx + af, ccy + hf), (ccx - af, ccy + hf),
                    (ccx - af, ccy + af), (ccx - hf, ccy + af),
                    (ccx - hf, ccy - af), (ccx - af, ccy - af)]

        self._bv_rpoly(canvas, cross(half, arm), art, 3.5, width=1.3,
                       fill=bg)
        self._bv_rpoly(canvas, cross(half - 2.5, arm - 2.5), art2, 2.5,
                       width=1.0)
        self._bv_ring(canvas, ccx, ccy, 10, art, fill=bg)
        self._bv_ring(canvas, ccx, ccy, 6.5, art2, width=1.0)
        for pts in self._BV_DIRS.values():
            self._bv_poly(canvas, pts, glyph, width=1.1, fill=bg, close=True)

        # G/D/B/A 双环菱形键（簇中心 386，与十字键簇 246 关于轴 316 对称）
        for fx, fy, _letter in self._BV_FACES.values():
            self._bv_ring(canvas, fx, fy, 13, art, fill=bg)
            self._bv_ring(canvas, fx, fy, 9.5, art2)

        # 底部 SEL_1 / HOME / SEL_2 小键（◁ ▢ ▷ 双层线稿、圆角连接）
        for outer, inner in self._BV_SMALLS.values():
            self._bv_poly(canvas, outer, art, width=1.2, fill=bg, close=True)
            self._bv_poly(canvas, inner, art2, width=0.9, close=True)

        # 指示线（45° 斜段 + 平行横段），终点圆点落在按键正中心
        for pts in self._BV_LINKS.values():
            self._bv_poly(canvas, pts, lead, width=1.0)
            ex, ey = pts[-1]
            e = Ellipse()
            e.Width = e.Height = 1.9 * 2 * self._BV_SCALE
            px, py = self._bv_pt(ex, ey)
            Canvas.SetLeft(e, px - e.Width / 2)
            Canvas.SetTop(e, py - e.Height / 2)
            e.Fill = dot
            canvas.Children.Append(e)

        # 键面字母最后画，压在圆点之上
        for fx, fy, letter in self._BV_FACES.values():
            t = W.text(letter, size=16, bold=W.SEMIBOLD, align="center")
            t.Foreground = glyph
            t.Width = 28
            at(t, *self._bv_pt(fx, fy))
            Canvas.SetLeft(t, Canvas.GetLeft(t) - 14)
            Canvas.SetTop(t, Canvas.GetTop(t) - 12)

        # 按下反馈高亮层：默认隐藏，收到 ovc_button 事件时点亮
        glow_fill = SolidColorBrush(self._BV_GLOW_FILL)
        glow_stroke = SolidColorBrush(self._BV_GLOW_STROKE)
        for bit, (gx, gy, gr) in self._BV_GLOW.items():
            e = Ellipse()
            side = gr * 2 * self._BV_SCALE
            px, py = self._bv_pt(gx, gy)
            e.Width = side
            e.Height = side
            Canvas.SetLeft(e, px - side / 2)
            Canvas.SetTop(e, py - side / 2)
            e.Fill = glow_fill
            e.Stroke = glow_stroke
            e.StrokeThickness = 1.4
            e.Visibility = Visibility.Collapsed
            view.button_glows[bit] = e
            canvas.Children.Append(e)

        # 标注：原生默认样式按钮（点击文字弹出选择框改绑）；
        # OSC 时输入框与按钮错开摆放（左列在左、右列在右、中间在下方）
        for bit, (ax, ly, width, align) in self._BV_LABELS.items():
            px, py = self._bv_pt(ax, ly)
            btn = self._binding_label(view, bit, bindings, width=width,
                                      x=px, y=py - self._BV_BTN_H / 2,
                                      align=align)
            if bit == 2:
                self._bv_tip(btn, "HOME 键连点 5 下，设备进入可被插槽搜索"
                                  "连接的状态")
            at(btn, Canvas.GetLeft(btn), Canvas.GetTop(btn))

        wrapper = W.stack()
        wrapper.HorizontalAlignment = W._HALIGN["center"]
        wrapper.Children.Append(canvas)
        panel = W.box(background=bg, corner=10, padding=Thickness(14, 10, 14, 10),
                      child=wrapper, h="stretch")
        return panel

    def _binding_label(self, view: CardView, bit: int, bindings,
                       *, width: float, x: float, y: float,
                       align: str) -> object:
        current = str(bindings.get(str(bit), "none"))

        label = W.text(self._binding_label_text(current), size=15,
                       trimming=True)
        label.Foreground = theme.solid(self._BV_TEXT)
        label.Width = width
        if align == "right":
            label.TextAlignment = TextAlignment.Right
        elif align == "center":
            label.TextAlignment = TextAlignment.Center
        view.bindings[bit] = label

        flyout = MenuFlyout()
        for key, text_label in live.button_actions(self.shell.engine):
            item = MenuFlyoutItem()
            item.Text = text_label
            item.Click += self._make_binding_click(view, bit, key)
            flyout.Items.Append(item)

        # 保留原生默认 Button 模板（点击弹出系统选择框观感），仅通过
        # 控件级资源键固定深色系配色，使亮色主题下画布内控件不翻浅。
        # OSC 绑定时按钮保持可见（仍可点开选择框改回其他动作），
        # 输入框错开摆放：左列在按钮左侧、右列在右侧、中间在下方。
        bw = width + 12
        if align == "right":
            bx = x - bw
        elif align == "center":
            bx = x - bw / 2
        else:
            bx = x
        btn = W.button(label, width=bw, height=self._BV_BTN_H)
        btn.Padding = W.uniform(2)
        btn.Flyout = flyout
        self._bv_dark(btn, self._BV_BTN_DARK)
        Canvas.SetLeft(btn, float(bx))
        Canvas.SetTop(btn, float(y))

        box = self._binding_input(view, bit,
                                  current[4:] if current.startswith("osc:") else "",
                                  self._BV_BOX_W)
        keybox = self._binding_key_capture(view, bit,
                                           current[4:] if current.startswith("key:") else "",
                                           self._BV_BOX_W)
        if align == "right":
            box_x, box_y = bx - 4 - self._BV_BOX_W, y + 1
        elif align == "left":
            box_x, box_y = bx + bw + 4, y + 1
        else:
            box_x, box_y = bx, y + self._BV_BTN_H + 4
        box.Visibility = Visibility.Visible if current.startswith("osc:") \
            else Visibility.Collapsed
        keybox.Visibility = Visibility.Visible if current.startswith("key:") \
            else Visibility.Collapsed

        canvas = view.binding_canvas
        if canvas is not None:
            for input_box in (box, keybox):
                Canvas.SetLeft(input_box, float(box_x))
                Canvas.SetTop(input_box, float(box_y))
                canvas.Children.Append(input_box)
        return btn

    def _binding_input(self, view: CardView, bit: int, address: str,
                       width: float) -> object:
        box = W.text_box(text=address, width=width)
        box.FontSize = 10
        box.Height = 28
        box.PlaceholderText = "/avatar/parameters/…"
        self._bv_dark(box, self._BV_BOX_DARK)

        def _changed(sender, args):
            if self._updating:
                return
            cfg = self._active_bindings()
            cfg[str(bit)] = "osc:" + (box.Text or "").strip()

        box.TextChanged += _changed
        view.binding_inputs[bit] = box
        return box

    def _binding_key_capture(self, view: CardView, bit: int, name: str,
                             width: float) -> object:
        """键盘绑定捕获框: 点击聚焦后按下任意键完成绑定."""
        box = W.text_box(text=name, width=width)
        box.FontSize = 10
        box.Height = 28
        box.PlaceholderText = "点击此处后按下要绑定的按键"
        self._bv_dark(box, self._BV_BOX_DARK)

        def _on_key(sender, args):
            try:
                vk = int(getattr(args.Key, "Value", args.Key))
            except (TypeError, ValueError):
                return
            args.Handled = True
            label = keyboard_keys.key_name(vk)
            self._active_bindings()[str(bit)] = "key:" + label
            self._updating = True
            try:
                box.Text = label
            finally:
                self._updating = False
            text = view.bindings.get(bit)
            if text is not None:
                text.Text = self._binding_label_text("key:" + label)

        box.KeyDown += _on_key
        view.binding_keys[bit] = box
        return box

    def _binding_label_text(self, key: str) -> str:
        if key.startswith("osc:"):
            address = key[4:].strip()
            return f"OSC {address}" if address else "OSC 参数"
        if key.startswith("key:"):
            name = key[4:].strip()
            return f"键盘 {name}" if name else "键盘按键"
        for action_key, label in live.button_actions(self.shell.engine):
            if action_key == key:
                return label
        return key

    def _make_binding_click(self, view: CardView, bit: int, key: str):
        def handler(sender, args):
            self._set_binding(view, bit, key)

        return handler

    def _set_binding(self, view: CardView, bit: int, key: str) -> None:
        bindings = self._active_bindings()
        label = view.bindings.get(bit)
        box = view.binding_inputs.get(bit)
        keybox = view.binding_keys.get(bit)
        if key == "osc":
            current = str(bindings.get(str(bit), ""))
            address = current[4:] if current.startswith("osc:") else ""
            bindings[str(bit)] = "osc:" + address
            if box is not None:
                self._updating = True
                try:
                    box.Text = address
                finally:
                    self._updating = False
                box.Visibility = Visibility.Visible
            if keybox is not None:
                keybox.Visibility = Visibility.Collapsed
            if label is not None:
                label.Text = self._binding_label_text("osc:" + address)
                label.Foreground = theme.solid(self._BV_TEXT)
            return
        if key == "key":
            current = str(bindings.get(str(bit), ""))
            name = current[4:] if current.startswith("key:") else ""
            bindings[str(bit)] = "key:" + name
            if keybox is not None:
                self._updating = True
                try:
                    keybox.Text = name
                finally:
                    self._updating = False
                keybox.Visibility = Visibility.Visible
            if box is not None:
                box.Visibility = Visibility.Collapsed
            if label is not None:
                label.Text = self._binding_label_text("key:" + name)
                label.Foreground = theme.solid(self._BV_TEXT)
            return
        bindings[str(bit)] = key
        if box is not None:
            box.Visibility = Visibility.Collapsed
        if keybox is not None:
            keybox.Visibility = Visibility.Collapsed
        if label is not None:
            label.Text = self._binding_label_text(key)
            label.Foreground = theme.solid(self._BV_TEXT)

    def _sensor_card(self, view: CardView, sid: str, slot):
        engine = self.shell.engine
        head, pill_host = self._card_head(view, sid, slot, output=False)
        view.pill_host = pill_host

        reading = W.text("气压: -- kPa", size=22, bold=W.SEMIBOLD)
        edge = W.text("边控状态: --", size=13, color="text2", v="center")
        summary = W.text(slot.summary(), size=11, color="text3", v="center")
        view.reading_tb = reading
        view.edge_tb = edge
        view.summary_tb = summary

        chart_host = W.stack(spacing=6, h="stretch")
        view.chart_host = chart_host

        chart_inner = W.stack(spacing=8, h="stretch")
        readout = W.stack(horizontal=True, spacing=18, v="center")
        readout.Children.Append(reading)
        readout.Children.Append(edge)
        readout.Children.Append(summary)
        chart_inner.Children.Append(readout)
        chart_inner.Children.Append(W.text("气压曲线（最近 60 秒 · 0-60 kPa）",
                                           size=11, color="text3"))
        chart_inner.Children.Append(chart_host)
        chart_inner.Children.Append(W.text(
            "灵猫无输出通道：本页不下发任何波形 / 强度指令，仅订阅气压与边控状态。",
            size=11, color="text3", wrap=True))

        actions = W.stack(horizontal=True, spacing=8, v="center")

        def _reset(sender, args):
            self.shell.submit(engine.reset_pressure(slot_id=sid))

        def _flip(sender, args):
            self.shell.submit(engine.bmtr_flip(slot_id=sid))

        actions.Children.Append(W.text_button("气压清零", symbol="Clear", on_click=_reset))
        actions.Children.Append(W.text_button("翻转屏幕", symbol="Sync", on_click=_flip))
        if self.shell.state.backend == "ble":
            actions.Children.Append(self._led_combo(view, sid))

        inner = W.stack(spacing=12, h="stretch")
        inner.Children.Append(head)
        inner.Children.Append(W.divider())
        inner.Children.Append(W.panel(chart_inner, padding=12))
        inner.Children.Append(actions)
        return W.card(inner)

    def _card_head(self, view: CardView, sid: str, slot, *, output: bool):
        family = view.family
        tile = W.box(
            width=34, height=34, corner=8,
            background=theme.brush("accent_soft"),
            child=W.icon(symbol=live.FAMILY_SYMBOLS[family], size=15, color="accent_text"),
            v="center")

        title = W.stack(spacing=2, v="center")
        line = W.stack(horizontal=True, spacing=8, v="bottom")
        line.Children.Append(W.text(slot.name or slot.type or sid, size=15,
                                    bold=W.SEMIBOLD, trimming=True))
        line.Children.Append(W.text(f"{live.FAMILY_LABELS[family]} · {slot.type}",
                                    size=11, color="text3", v="bottom"))
        title.Children.Append(line)
        backend = self.shell.state.backend
        title.Children.Append(W.text(
            f"连接通道: {live.BACKEND_LABELS.get(backend, backend)}",
            size=11, color="text3"))

        trailing = W.stack(horizontal=True, spacing=10, v="center")
        bat = slot.battery
        if bat is not None:
            trailing.Children.Append(W.pill(
                f"电量 {bat}%", "success" if bat > 60 else "warning",
                "success_soft" if bat > 60 else "warning_soft"))
        if output:
            trailing.Children.Append(W.text("2 路输出", size=11, color="text3", v="center"))
        else:
            trailing.Children.Append(W.pill("只监听", "accent_text", "accent_soft"))
        pill_host = W.box(child=W.pill("在线", "success", "success_soft",
                                       dot_color="success"), v="center", h="right")
        trailing.Children.Append(pill_host)

        head = W.grid(W.fixed(46), W.star(1), W.auto())
        head.Children.Append(W.put(tile, 0))
        head.Children.Append(W.put(title, 1))
        head.Children.Append(W.put(_gap(trailing, 14), 2))
        return head, pill_host

    def _chart_inner(self, view: CardView) -> object:
        head = W.stack(horizontal=True, spacing=8, v="center")
        head.Children.Append(W.text("实时输出波形", size=11, bold=W.SEMIBOLD, color="text3"))
        head.Children.Append(W.text("A / B 各一栏 · 最右侧为最新采样 · 0-100",
                                    size=11, color="text3", v="center"))
        view.chart_image = W.image(width=720, height=130)
        inner = W.stack(spacing=8, h="stretch")
        inner.Children.Append(head)
        inner.Children.Append(view.chart_image)
        return inner

    def _update_values(self, state) -> None:
        self._updating = True
        try:
            for sid, view in self._cards.items():
                slot = state.slots.get(sid)
                if slot is None:
                    continue
                if view.family == "BMTR":
                    pressure = slot.pressure
                    view.reading_tb.Text = (f"气压: {pressure:.2f} kPa"
                                            if pressure is not None else "气压: -- kPa")
                    view.edge_tb.Text = ("边控状态: "
                                         + live.EDGE_STATES.get(slot.edge_state,
                                                                str(slot.edge_state)))
                    view.summary_tb.Text = slot.summary()
                    continue
                for ch in ("A", "B"):
                    out = live.output_row(slot, ch)
                    view.labels[ch].Text = f"{ch}: {out['value']}/{out['limit']}"
                    self._set_meter(view.meters[ch], out["percent"], width=86)
                self._sync_wave_combo(view)
        finally:
            self._updating = False

    def _sync_wave_combo(self, view: CardView) -> None:
        values = self._wave_values.get(view.family) or []
        for ch, combo in view.wave_combos.items():
            value = str(self.shell.engine._selected_wave.get(ch, ""))
            if value not in values:
                continue
            index = values.index(value)
            if combo.SelectedIndex != index:
                combo.SelectedIndex = index

    @staticmethod
    def _set_meter(meter, percent: float, *, width: float = 86) -> None:
        ratio = max(0.0, min(1.0, percent / 100.0))
        try:
            fill = list(meter.Children)[1]
            fill.Width = max(width * ratio, 6.0)
        except Exception:
            pass

    def _render_wave_charts(self) -> None:
        engine = self.shell.engine
        dark = self.shell._dark
        for sid, view in self._cards.items():
            if view.family == "BMTR" or view.chart_image is None:
                continue
            monitor = engine.wave_history(sid)
            samples = monitor.window(5.0) if monitor is not None else []
            try:
                png = charts.render_wave_live(samples, dark=dark)
                self.shell.set_image_bytes(view.chart_image, png)
            except Exception as exc:
                self.shell.logs.append(f"波形图渲染失败: {exc!r}")

    def _sample_pressure(self, now: float) -> None:
        state = self.shell.state
        for sid, slot in state.slots.items():
            if family_of(slot.type) != "BMTR" or slot.pressure is None:
                continue
            last = self._last_pressure_sample.get(sid, 0.0)
            if now - last >= 0.08:
                self._last_pressure_sample[sid] = now
                hist = self._pressure_hist.setdefault(sid, live.new_history())
                hist.append((now, slot.pressure))

    def _render_pressure_charts(self) -> None:
        for sid, view in self._cards.items():
            if view.family != "BMTR" or view.chart_host is None:
                continue
            hist = self._pressure_hist.get(sid)
            if not hist:
                if view.hist_sig is not None:
                    view.hist_sig = None
                    view.chart_host.Children.Clear()
                    view.chart_host.Children.Append(
                        W.text("等待气压采样…", size=11, color="text3"))
                continue
            sig = (len(hist), hist[-1][1])
            if sig == view.hist_sig:
                continue
            view.hist_sig = sig
            slot = self.shell.state.slots.get(sid)
            label = (slot.name if slot else sid) or sid
            series = [_Series(label, 0, tuple(v for _t, v in hist))]
            host = view.chart_host
            host.Children.Clear()
            host.Children.Append(W.line_chart(
                series, live.PRESSURE_COLORS,
                ymin=live.PRESSURE_MIN_KPA, ymax=live.PRESSURE_MAX_KPA,
                x_left="-60 s", x_right="现在", width=640, height=190))

    def _clear_all(self) -> None:
        self.shell.submit(self.shell.engine.clear_wave())

    def _estop(self) -> None:
        self.shell.submit(self.shell.engine.emergency_stop())

    def flush_config(self) -> None:
        pass

def _gap(el, left: float):
    if left:
        el.Margin = Thickness(left, 0, 0, 0)
    return el

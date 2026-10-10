"""事件流画布：Win2D 自绘的蓝图节点编辑器（输入 / 输出两张图）。"""

from __future__ import annotations

import ctypes
import math
import time
from typing import Any

from win32more import asyncui
from win32more.Microsoft.Graphics.Canvas import CanvasDevice
from win32more.Microsoft.Graphics.Canvas.Geometry import (
    CanvasFigureLoop, CanvasGeometry, CanvasPathBuilder, CanvasStrokeStyle)
from win32more.Microsoft.Graphics.Canvas.Text import (
    CanvasTextFormat, CanvasTextLayout, CanvasTextTrimmingGranularity, CanvasWordWrapping)
from win32more.Microsoft.UI.Xaml import (
    FocusState, HorizontalAlignment, Thickness, VerticalAlignment, Visibility)
from win32more.Microsoft.UI.Xaml.Controls import (
    Border, Canvas, Grid, Page, ScrollBarVisibility, ScrollViewer, StackPanel,
    TextBox, TextBlock, ToolTip, ToolTipService)
from win32more.Microsoft.UI.Xaml.Media import SolidColorBrush
from win32more.Windows.Foundation import Point, Rect
from win32more.Windows.Foundation.Numerics import Matrix3x2
from win32more.Windows.UI import Color
from win32more.winui3 import XamlClass

from dglab import event_flow
from dglab.event_flow import (
    BOOL, EXEC, FLOAT, INT, PAGE_INPUT, PAGE_OUTPUT, STR, TYPE_LABELS,
    compatible)
from ui import theme, widgets as W
from ui.dialogs import confirm_dialog, prompt_text
from ui.paths import xaml

HEADER_H = 26.0
ROW_H = 21.0
FIELD_H = 22.0
ERROR_H = 17.0
PAD = 8.0
PIN_R = 5.0
MIN_W = 154.0
MAX_W = 306.0
VAR_MIN_W = 190.0
VAR_MAX_W = 620.0
SIDE_DEFAULT = 380.0
SIDE_MIN = 260.0
VAR_CELL_H = 22.0
VAR_LINE_H = 24.0
VAR_ROW_H = 30.0
GRIP_W = 14.0
SECTION_H = 26.0
VALUE_TICK_S = 0.25
_VAR_OPS = ("temp_read", "temp_write", "mod_read", "mod_write",
            "core_read", "core_write")
_DIR_MARKS = {"in": "读", "out": "写", "inout": "读写"}
_DIR_CYCLE = {"in": "out", "out": "inout", "inout": "in"}
FONT = "Microsoft YaHei UI"
DASH_EXEC = (7.0, 5.0)
PALETTE_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("事件", ("模块事件",)),
    ("流程控制", ("流程控制",)),
    ("核心变量", ("核心写入", "核心读出")),
    ("变量", ("变量",)),
    ("运算", ("数学", "逻辑", "表达式", "范围", "实用")),
    ("常量", ("常数",)),
)
_CAT_GROUP = {cat: index for index, (_name, cats) in enumerate(PALETTE_GROUPS)
              for cat in cats}
_CAT_ORDER = {cat: (index, sub)
              for index, (_name, cats) in enumerate(PALETTE_GROUPS)
              for sub, cat in enumerate(cats)}
_IDENTITY = Matrix3x2(M11=1.0, M12=0.0, M21=0.0, M22=1.0, M31=0.0, M32=0.0)

VK_SHIFT = 16
VK_CONTROL = 17
VK_MENU = 18
VK_BACK = 8
VK_RETURN = 13
VK_ESCAPE = 27
VK_SPACE = 32
VK_PRIOR = 33
VK_NEXT = 34
VK_LEFT = 37
VK_UP = 38
VK_RIGHT = 39
VK_DOWN = 40
VK_DELETE = 46
VK_A = 65
VK_TAB = 9

BLUEPRINT: dict[str, dict[str, tuple[int, int, int]]] = {
    "dark": {
        "bg": (23, 24, 27), "grid": (34, 36, 40), "grid_major": (48, 50, 56),
        "panel": (28, 29, 33), "panel_head": (38, 40, 45), "divider": (58, 61, 68),
        "node": (41, 43, 48), "node_sel": (56, 60, 68), "node_border": (24, 25, 28),
        "node_border_sel": (120, 170, 255), "text": (226, 228, 232),
        "text_dim": (150, 154, 162), "text_faint": (104, 108, 118),
        "accent": (94, 176, 255), "ok": (112, 200, 156), "danger": (224, 96, 96),
        "param": (24, 26, 30), "param_hot": (36, 40, 46), "row_sel": (64, 68, 78),
        "row_hot": (44, 47, 54), "pin_off": (118, 122, 130), "hole": (28, 30, 34),
        "hint": (16, 17, 20), "menu": (32, 34, 39), "menu_alt": (44, 47, 53),
        "menu_sel": (66, 60, 40),
    },
    "light": {
        "bg": (243, 244, 246), "grid": (226, 229, 234), "grid_major": (206, 211, 219),
        "panel": (236, 238, 241), "panel_head": (223, 226, 231), "divider": (196, 200, 208),
        "node": (252, 252, 253), "node_sel": (233, 242, 253), "node_border": (212, 216, 222),
        "node_border_sel": (0, 95, 184), "text": (30, 32, 38),
        "text_dim": (92, 98, 108), "text_faint": (134, 140, 150),
        "accent": (0, 95, 184), "ok": (16, 128, 90), "danger": (196, 43, 28),
        "param": (246, 247, 249), "param_hot": (231, 238, 248), "row_sel": (206, 224, 246),
        "row_hot": (233, 237, 243), "pin_off": (128, 134, 144), "hole": (249, 250, 252),
        "hint": (255, 255, 255), "menu": (250, 251, 252), "menu_alt": (240, 242, 245),
        "menu_sel": (255, 236, 180),
    },
}

_HIDDEN_POOL = ("core_in", "core_out", "mod_param")

_shared: CanvasDevice | None = None


def _module_tag(row: dict) -> str:
    """变量所属模块的短标签（核心读出与用户临时变量不带标签）。"""
    head = str(row.get("source") or "").split("（")[0].strip()
    return "" if head in ("", "核心", "临时变量") else head


def _device() -> CanvasDevice:
    global _shared
    if _shared is None:
        _shared = CanvasDevice.GetSharedDevice()
    return _shared


def _rgb(raw: str, alpha: int = 255) -> Color:
    raw = raw.lstrip("#")
    return Color(alpha, int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16))


def _mix(color: Color, factor: float, alpha: int = 255) -> Color:
    return Color(alpha, min(255, int(color.R * factor)),
                 min(255, int(color.G * factor)), min(255, int(color.B * factor)))


def _down(vk: int) -> bool:
    try:
        return bool(ctypes.windll.user32.GetKeyState(vk) & 0x8000)
    except Exception:
        return False


def _key_of(args) -> int:
    try:
        return int(getattr(args.Key, "Value", args.Key))
    except (TypeError, ValueError, AttributeError):
        return 0


class Painter:
    """蓝图需要的少量 Win2D 原语；文本度量与画刷对象按页面级缓存复用。"""

    def __init__(self, ds, caches: dict[str, dict], fg: Color):
        self.ds = ds
        self.device = _device()
        self.fg = fg
        self._formats = caches["formats"]
        self._strokes = caches["strokes"]
        self._metrics = caches["metrics"]
        self._geo = caches.setdefault("geo", {})
        self._texts = caches.setdefault("texts", {})
        self._layers: list[Any] = []

    def clip(self, x: float, y: float, w: float, h: float) -> None:
        try:
            self._layers.append(
                self.ds.CreateLayerWithOpacityAndClipRectangle(
                    1.0, Rect(float(x), float(y), float(w), float(h))))
        except Exception:
            self._layers.append(None)

    def unclip(self) -> None:
        layer = self._layers.pop() if self._layers else None
        try:
            if layer is not None:
                layer.Close()
        except Exception:
            pass

    def format(self, size: float, bold: bool = False, trim: bool = False):
        key = (round(float(size), 1), bold, trim)
        fmt = self._formats.get(key)
        if fmt is None:
            fmt = CanvasTextFormat()
            fmt.FontFamily = FONT
            fmt.FontSize = float(size)
            fmt.WordWrapping = CanvasWordWrapping.NoWrap
            if bold:
                fmt.FontWeight = W.weight(600)
            if trim:
                fmt.TrimmingGranularity = CanvasTextTrimmingGranularity.Character
                fmt.TrimmingDelimiter = "…"
            self._formats[key] = fmt
        return fmt

    def stroke(self, dash):
        if not dash:
            return None
        style = self._strokes.get(dash)
        if style is None:
            style = CanvasStrokeStyle()
            style.DashStyle = 4          # Custom：绑定未暴露该枚举名
            style.CustomDashStyle = [float(v) for v in dash]
            if len(self._strokes) > 64:
                self._strokes.clear()
            self._strokes[dash] = style
        return style

    def layout_cache(self, text, size: float, bold: bool = False):
        """同一字符串只做一次排版：DrawTextLayout 比每帧 DrawText 省一次布局。"""
        key = (str(text), round(float(size), 1), bold)
        item = self._texts.get(key)
        if item is None:
            item = CanvasTextLayout(self.device, key[0], self.format(size, bold),
                                    10000.0, 400.0)
            bounds = item.LayoutBounds
            self._metrics[key] = (float(bounds.Width), float(bounds.Height))
            if len(self._texts) > 3000:
                self._texts.clear()
            self._texts[key] = item
        return item

    def box_size(self, text: str, size: float, bold: bool = False) -> tuple[float, float]:
        size = float(size)
        self.layout_cache(text, size, bold)
        return self._metrics[(str(text), round(size, 1), bold)]

    def text_w(self, text, size: float, bold: bool = False) -> float:
        return self.box_size(str(text), size, bold)[0]

    def text(self, value, x: float, y: float, size: float = 13, color=None,
             bold: bool = False, maxw: float | None = None) -> float:
        s = str(value)
        if not s:
            return 0.0
        color = self.fg if color is None else color
        if maxw is None:
            item = self.layout_cache(s, size, bold)
            self.ds.DrawTextLayoutAtCoordsWithColor(item, float(x), float(y), color)
            return self._metrics[(s, round(float(size), 1), bold)][0]
        self.ds.DrawTextAtRectWithColorAndFormat(
            s, Rect(float(x), float(y), float(maxw), float(size) * 1.6), color,
            self.format(size, bold, True))
        return min(self.text_w(s, size, bold), maxw)

    def clear(self, color: Color) -> None:
        self.ds.Clear(color)

    def rect(self, x: float, y: float, w: float, h: float, color: Color) -> None:
        self.ds.FillRectangleAtCoordsWithColor(float(x), float(y), float(w), float(h), color)

    def rrect(self, x: float, y: float, w: float, h: float, r: float, color: Color) -> None:
        self.ds.FillRoundedRectangleWithColor(Rect(x, y, w, h), r, r, color)

    def rrect_stroke(self, x: float, y: float, w: float, h: float, r: float,
                     color: Color, width: float = 1.0, dash=None) -> None:
        self.ds.DrawRoundedRectangleWithColorAndStrokeWidthAndStrokeStyle(
            Rect(x, y, w, h), r, r, color, width, self.stroke(dash))

    def line(self, x0: float, y0: float, x1: float, y1: float, color: Color,
             width: float = 1.0, dash=None) -> None:
        style = self.stroke(dash)
        if style is None:
            self.ds.DrawLineAtCoordsWithColorAndStrokeWidth(x0, y0, x1, y1, color, width)
        else:
            self.ds.DrawLineAtCoordsWithColorAndStrokeWidthAndStrokeStyle(
                x0, y0, x1, y1, color, width, style)

    def circle(self, cx: float, cy: float, r: float, color: Color,
               fill: bool = True, width: float = 1.4) -> None:
        if fill:
            self.ds.FillCircleAtCoordsWithColor(cx, cy, r, color)
        else:
            self.ds.DrawCircleAtCoordsWithColorAndStrokeWidth(cx, cy, r, color, width)

    def tri(self, cx: float, cy: float, size: float, color: Color,
            right: bool = True, angle: float | None = None) -> None:
        """单位三角形复用一条几何，靠变换定位缩放——避免每帧重建路径。"""
        if size < 0.05:
            return
        unit = self._unit_tri()
        if angle is None:
            m11, m12, m21, m22 = (size if right else -size), 0.0, 0.0, size
        else:
            cos, sin = math.cos(angle), math.sin(angle)
            m11, m12, m21, m22 = size * cos, size * sin, -size * sin, size * cos
        self.ds.Transform = Matrix3x2(
            M11=m11, M12=m12, M21=m21, M22=m22, M31=float(cx), M32=float(cy))
        try:
            self.ds.DrawGeometryAtOriginWithColorAndStrokeWidth(unit, color, 1.6 / size)
        finally:
            self.ds.Transform = _IDENTITY

    def _unit_tri(self):
        unit = self._geo.get("unit_tri")
        if unit is None:
            builder = CanvasPathBuilder(self.device)
            builder.BeginFigureAtCoords(-1.0, -1.0)
            builder.AddLineWithCoords(-1.0, 1.0)
            builder.AddLineWithCoords(1.0, 0.0)
            builder.EndFigure(CanvasFigureLoop.Closed)
            unit = CanvasGeometry.CreatePath(builder)
            builder.Close()
            self._geo["unit_tri"] = unit
        return unit

    def build(self, groups) -> Any:
        """把多组折线合成一条 CanvasGeometry（CreatePath 很贵，尽量一次建好）。"""
        if not groups:
            return None
        builder = CanvasPathBuilder(self.device)
        try:
            for points, closed in groups:
                builder.BeginFigureAtCoords(points[0][0], points[0][1])
                for x, y in points[1:]:
                    builder.AddLineWithCoords(x, y)
                builder.EndFigure(CanvasFigureLoop.Closed if closed
                                  else CanvasFigureLoop.Open)
            return CanvasGeometry.CreatePath(builder)
        finally:
            builder.Close()

    def stroke_geometry(self, geometry, color: Color, width: float,
                        dash=None, transform=None) -> None:
        """画一条已建好的几何；transform 用于整体平移/缩放（描边宽度也随之缩放）。"""
        if geometry is None:
            return
        if transform is not None:
            self.ds.Transform = transform
        try:
            style = self.stroke(dash)
            if style is None:
                self.ds.DrawGeometryAtOriginWithColorAndStrokeWidth(
                    geometry, color, width)
            else:
                self.ds.DrawGeometryAtOriginWithColorAndStrokeWidthAndStrokeStyle(
                    geometry, color, width, style)
        finally:
            if transform is not None:
                self.ds.Transform = _IDENTITY

    def poly(self, pts, color: Color, width: float = 1.0, dash=None) -> None:
        if len(pts) < 2:
            return
        self._stroke([([(float(x), float(y)) for x, y in pts], False)], color, width, dash)

    def _stroke(self, groups, color: Color, width: float, dash=None) -> None:
        """把多个图元合成一条 CanvasGeometry：上万次 DrawLine 压成一次调用。"""
        geometry = self.build(groups)
        if geometry is None:
            return
        try:
            self.stroke_geometry(geometry, color, width, dash)
        finally:
            geometry.Close()


class NodeLayout:
    """一个节点在当前视图下的几何：引脚、参数框、字段行。"""

    __slots__ = ("node", "x", "y", "w", "h", "ins", "outs", "exec", "pin_box", "fields")

    def __init__(self, node):
        self.node = node
        self.x = float(node.x)
        self.y = float(node.y)
        self.w = MIN_W
        self.h = HEADER_H
        self.ins: list[tuple[float, float]] = []
        self.outs: list[tuple[float, float]] = []
        self.exec: tuple[float, float] = (self.x, self.y)
        self.pin_box: dict[tuple[str, int], tuple[float, float, float, float]] = {}
        self.fields: dict[str, tuple[float, float, float, float, float]] = {}


class FlowPage(XamlClass, Page):

    def __init__(self, shell):
        super().__init__()
        self.shell = shell
        self.engine = shell.engine
        self.LoadComponentFromFile(xaml("FlowPage.xaml"), encoding="utf-8")

        self._page_key = PAGE_INPUT
        self._t0 = time.time()
        self.zoom = 1.0
        self.cam_x = 40.0
        self.cam_y = 16.0
        self.side_w = SIDE_DEFAULT
        self.side_scroll = 0.0
        self.side_hits: list[tuple] = []
        self.side_drag: float | None = None
        self.var_drag: tuple | None = None
        self._side_rows: dict[str, dict] = {}
        self._var_boxes: list[TextBox] = []
        self._var_box_keys: list[tuple | None] = []
        self._var_box_names: list[str] = []
        self._var_rects: list[tuple] = []
        self.pw = 900.0
        self.ph = 620.0
        self._scene_sig: tuple | None = None
        self._value_sig: tuple | None = None
        self._value_at = 0.0
        self.hover: tuple | None = None
        self.panning: tuple | None = None
        self.moving: tuple | None = None
        self.marquee: tuple | None = None
        self.drag_wire: tuple | None = None
        self.param_drag: list | None = None
        self.divider_drag: float | None = None
        self.space_pan = False
        self.pending_link: tuple | None = None
        self.status = ""
        self.status_until = 0.0
        self.usage: dict[str, int] = {}
        self._mouse = (0.0, 0.0)
        # 模块注入 / 移除默认配置后配置下拉要跟着变：事件在引擎线程发，
        # 经 ui_queue 折回 UI 线程再重建页面
        self.engine.events.on("profiles_changed", self._on_profiles_changed)

        self._paint: Painter | None = None
        self._layouts: dict[str, NodeLayout] = {}
        self._caches: dict[str, dict] = {"formats": {}, "strokes": {},
                                         "metrics": {}, "geo": {}, "texts": {}}
        self._geo: dict[str, Any] = {}
        self._geo_sig: tuple | None = None
        self._geo_old: dict[str, Any] = {}
        self._wire_pts: dict[str, list] = {}
        self._grid: dict[str, Any] = {}
        self._grid_sig: tuple | None = None
        self._grid_old: dict[str, Any] = {}
        self._colors: dict[str, Color] = {}
        self._type_colors: dict[str, Color] = {}
        self._cat_colors: dict[str, Color] = {}
        self._palette_rows: list[dict] = []
        self._pal_index = 0
        self._pal_open = False
        self._pal_filter: tuple | None = None
        self._pal_note = ""
        self._edit_target: tuple | None = None
        self._edit_panel = False
        self._edit_rect: tuple | None = None
        self._edit_min_w = 104.0
        self._edit_at = 0.0
        self._saved_at = 0.0
        self._confirm_clear = 0.0

        self._build_ui()

    # ------------------------------------------------------------------ 数据源
    @property
    def host(self):
        return getattr(self.engine, "flow", None)

    @property
    def catalog(self):
        host = self.host
        return host.catalog if host is not None else None

    @property
    def runtime(self):
        host = self.host
        return host.runtime if host is not None else None

    @property
    def graph(self):
        runtime = self.runtime
        return runtime.graph(self._page_key) if runtime is not None else None

    def _def(self, node) -> dict[str, Any]:
        return self.catalog.definition(node.def_key)

    def _title(self, node) -> str:
        return node.title(self.catalog)

    # ======================================================================
    # 顶部工具栏 + 画布容器
    # ======================================================================
    def _build_ui(self) -> None:
        self.TopBarHost.Children.Clear()
        self.CanvasHost.Children.Clear()
        self._colors.clear()
        self._paint = None
        self._layouts.clear()
        self._geo_sig = None
        self._grid_sig = None
        self._release_geo()

        top = W.grid(W.auto(), W.fixed(14), W.auto(), W.star(1), W.auto())
        top.VerticalAlignment = VerticalAlignment.Center

        tabs = W.stack(horizontal=True, spacing=6)
        self._tab_button(PAGE_INPUT, "输入 · 模块 → 核心", tabs)
        self._tab_button(PAGE_OUTPUT, "输出 · 核心 → 模块", tabs)
        top.Children.Append(W.put(tabs, 0))

        top.Children.Append(W.put(self._profile_selector(), 2))

        acts = W.stack(horizontal=True, spacing=8, h="right", v="center")
        acts.Children.Append(W.text_button("添加卡片", symbol="Add",
                                           on_click=lambda s, e: self._add_card_pressed()))
        acts.Children.Append(W.text_button("整理布局", symbol="Bullets",
                                           on_click=lambda s, e: self._auto_arrange()))
        acts.Children.Append(W.text_button("保存", symbol="Save",
                                           on_click=lambda s, e: self._commit(force=True)))
        acts.Children.Append(W.text_button("重置视图", symbol="Refresh",
                                           on_click=lambda s, e: self._action("reset")))
        acts.Children.Append(W.text_button("清空画布", symbol="Delete",
                                           on_click=lambda s, e: self._action("clear")))
        self.pill_text = W.text("● 实时求值中", size=12, color="text2", trimming=True)
        self.pill_text.MaxWidth = 210
        pill = W.stack(horizontal=True, spacing=6, v="center")
        pill.Children.Append(self.pill_text)
        acts.Children.Append(W.box(child=pill, padding=Thickness(10, 4, 10, 4), corner=12,
                                   background=SolidColorBrush(self._bc("panel_head")),
                                   v="center"))
        top.Children.Append(W.put(acts, 4))
        self.TopBarHost.Children.Append(top)
        self._update_tabs()

        frame = Border()
        frame.CornerRadius = W.radius(8)
        frame.BorderThickness = Thickness(1, 1, 1, 1)
        frame.BorderBrush = SolidColorBrush(self._bc("divider"))
        frame.Background = SolidColorBrush(self._bc("bg"))
        frame.HorizontalAlignment = HorizontalAlignment.Stretch
        frame.VerticalAlignment = VerticalAlignment.Stretch

        from win32more.Microsoft.Graphics.Canvas.UI.Xaml import CanvasControl

        self.canvas = CanvasControl()
        self.canvas.Draw += self._on_draw
        self.canvas.PointerPressed += self._on_pressed
        self.canvas.PointerMoved += self._on_moved
        self.canvas.PointerReleased += self._on_released
        self.canvas.PointerWheelChanged += self._on_wheel
        self.canvas.RightTapped += self._on_right_tapped
        self.canvas.DoubleTapped += self._on_double_tapped
        self.canvas.IsTapEnabled = True
        self.canvas.IsRightTapEnabled = True
        self.canvas.IsDoubleTapEnabled = True

        self.overlay = Canvas()
        self.overlay.HorizontalAlignment = HorizontalAlignment.Stretch
        self.overlay.VerticalAlignment = VerticalAlignment.Stretch
        # 变量名输入框挂在 overlay 上：overlay 重建后旧控件全部作废
        self._drop_var_boxes()

        host = Grid()
        host.Children.Append(self.canvas)
        host.Children.Append(self.overlay)
        frame.Child = host
        self.CanvasHost.Children.Append(frame)

        self.KeyDown += self._on_key_down
        self.KeyUp += self._on_key_up

        self._build_palette()
        self._build_inline_edit()

    def _tab_button(self, key: str, label: str, host) -> None:
        lbl = W.text(label, size=13)
        row = W.stack(horizontal=True, spacing=6, v="center")
        row.Children.Append(lbl)
        btn = W.button(row, on_click=lambda s, e, _k=key: self._switch_page(_k))
        host.Children.Append(btn)
        setattr(self, "_tab_" + key, (btn, lbl))

    def _update_tabs(self) -> None:
        for key in (PAGE_INPUT, PAGE_OUTPUT):
            entry = getattr(self, "_tab_" + key, None)
            if entry is None:
                continue
            btn, lbl = entry
            on = key == self._page_key
            btn.Background = SolidColorBrush(self._bc("accent" if on else "panel_head"))
            btn.BorderBrush = SolidColorBrush(self._bc("accent" if on else "divider"))
            lbl.Foreground = SolidColorBrush(self._bc("text" if on else "text_dim"))

    def _switch_page(self, key: str) -> None:
        if key == self._page_key:
            return
        self._page_key = key
        self._close_palette()
        self._cancel_edit()
        self.pending_link = None
        self._update_tabs()
        self._layouts.clear()
        self._invalidate()

    def _on_profiles_changed(self, module_id=None) -> None:
        try:
            self.shell.ui_queue.put(self.rebuild)
        except Exception as exc:
            self.shell.logs.append(f"配置列表刷新失败: {exc!r}")

    # ------------------------------------------------------------- 配置文件
    def _profile_selector(self) -> object:
        runtime = self.runtime
        names = list(runtime.profile_names) if runtime is not None else ["默认"]
        active = (runtime.active if runtime is not None else names[0])
        combo = W.combo(names, selected=max(0, names.index(active)), width=150)
        combo.VerticalAlignment = VerticalAlignment.Center

        def _switch(sender, args):
            index = combo.SelectedIndex
            if not 0 <= index < len(names) or names[index] == active:
                return
            asyncui.create_task(self._switch_profile(names[index]))

        combo.SelectionChanged += _switch

        new_btn = W.button("＋新建", width=64, height=32, v="center",
                           on_click=lambda s, e: self._new_profile())
        self._tip(new_btn, "以当前配置的画布为模板新建一份事件流配置，并切换过去")

        rename_btn = W.button("✎改名", width=64, height=32, v="center",
                              on_click=lambda s, e: asyncui.create_task(
                                  self._rename_profile()))
        self._tip(rename_btn, "重命名当前事件流配置文件")

        row = W.stack(horizontal=True, spacing=6, v="center")
        row.Children.Append(combo)
        row.Children.Append(new_btn)
        row.Children.Append(rename_btn)

        missing = self.host.missing_modules(active) if self.host is not None else []
        label = f"事件流配置文件 · {active}"
        if missing:
            names_txt = "、".join(self._module_name(mid) for mid in missing)
            label += f" · 缺少模块：{names_txt}"
        cell = W.stack(spacing=4, v="center")
        cell.Children.Append(W.text(label, size=11,
                                    color="danger" if missing else "text3",
                                    trimming=True))
        cell.Children.Append(row)
        return cell

    def _module_name(self, module_id: str) -> str:
        meta = self.engine.modules.meta(module_id) or {}
        return str(meta.get("name") or module_id)

    def _tip(self, element, message: str) -> None:
        tip = ToolTip()
        content = W.text(message, size=12, color="text2", wrap=True)
        content.MaxWidth = 240
        tip.Content = content
        ToolTipService.SetToolTip(element, tip)

    async def _switch_profile(self, name: str) -> None:
        host = self.host
        if host is None:
            return
        missing = host.missing_modules(name)
        if missing:
            lines = "\n".join(f"· {self._module_name(mid)}" for mid in missing)
            ok = await confirm_dialog(
                self.shell, "切换事件流配置需要启用模块",
                f"配置「{name}」用到了以下未启用的联动模块：\n{lines}\n\n"
                "启用相关模块后切换；拒绝则保持当前配置不变。",
                primary="启用并切换", close="拒绝切换")
            if not ok:
                self.rebuild()
                return
            for mid in missing:
                self.shell.submit(self.engine.modules.install(mid))
        self._close_palette()
        self._cancel_edit()
        self.pending_link = None
        if host.switch_profile(name):
            self._layouts.clear()
            self.say(f"已切换到事件流配置「{name}」")
            self.shell.logs.append(f"事件流配置已切换：{name}")
        self.rebuild()

    def _new_profile(self) -> None:
        host, runtime = self.host, self.runtime
        if runtime is None:
            return
        self._close_palette()
        name = runtime.new_profile()
        runtime.save()
        self._layouts.clear()
        self.say(f"已新建事件流配置「{name}」（复制自当前配置）")
        self.shell.logs.append(f"事件流配置已新建：{name}")
        self.rebuild()

    async def _rename_profile(self) -> None:
        runtime = self.runtime
        if runtime is None:
            return
        old = runtime.active
        new = await prompt_text(self.shell, "重命名配置文件",
                                f"将事件流配置「{old}」重命名为：",
                                initial=old, primary="重命名")
        if new is None or new == old:
            self.rebuild()
            return
        error = runtime.rename_profile(old, (new or "").strip())
        if error:
            self.shell.logs.append(f"重命名失败：{error}")
        else:
            runtime.save()
            self.shell.logs.append(f"事件流配置文件已重命名：{old} → {runtime.active}")
        self.rebuild()

    # ------------------------------------------------------------------ 配色
    def _bc(self, key: str, alpha: int | None = None) -> Color:
        name = key if alpha is None else f"{key}@{alpha}"
        cached = self._colors.get(name)
        if cached is not None:
            return cached
        table = BLUEPRINT[theme.name()] if theme.name() in BLUEPRINT else BLUEPRINT["dark"]
        color = Color(255 if alpha is None else alpha, *table[key])
        self._colors[name] = color
        return color

    def _type_color(self, ptype: str) -> Color:
        color = self._type_colors.get(ptype)
        if color is None:
            color = _rgb(event_flow.TYPE_COLORS.get(ptype or "", "#B0B4BA"))
            self._type_colors[ptype] = color
        return color

    def _cat_color(self, cat: str) -> Color:
        color = self._cat_colors.get(cat)
        if color is None:
            color = _rgb(event_flow.CATEGORY_COLORS.get(cat or "", "#6B7280"))
            self._cat_colors[cat] = color
        return color

    # ======================================================================
    # 坐标换算
    # ======================================================================
    @property
    def now(self) -> float:
        return time.time() - self._t0

    def _screen(self, wx: float, wy: float) -> tuple[float, float]:
        return (wx * self.zoom + self.cam_x, wy * self.zoom + self.cam_y)

    def _world(self, sx: float, sy: float) -> tuple[float, float]:
        return ((sx - self.cam_x) / self.zoom, (sy - self.cam_y) / self.zoom)

    def _canvas_bottom(self) -> float:
        return max(60.0, self.ph - 4.0)

    @property
    def _cw(self) -> float:
        """画布可用宽度：右侧变量表面板占掉的那部分不算进来。"""
        return max(240.0, self.pw - self.side_w)

    def say(self, text: str) -> None:
        self.status = text
        self.status_until = self.now + 3.2

    def _invalidate(self) -> None:
        try:
            self.canvas.Invalidate()
        except Exception:
            pass

    # ======================================================================
    # 节点几何
    # ======================================================================
    def _tw(self, text, size: float, bold: bool = False) -> float:
        painter = self._paint
        if painter is None:
            return len(str(text)) * size * 0.62
        return painter.text_w(text, size, bold)

    def _node_error(self, node) -> str:
        if node.error:
            return str(node.error)
        runtime = self.runtime
        if runtime is None:
            return ""
        return str(runtime.errors.get(node.id, ""))

    def _fields(self, node) -> list[dict[str, Any]]:
        item = self._def(node)
        rows = []
        for spec in item.get("fields") or []:
            if item["op"] == "const" and spec["id"] == "v":
                continue
            if spec["id"] == "module" or str(spec.get("pool") or "") in _HIDDEN_POOL:
                continue
            rows.append(spec)
        return rows

    def _field_value(self, node, spec):
        return node.param(self.catalog, spec["id"])

    def _field_text(self, node, spec) -> str:
        value = self._field_value(node, spec)
        kind = str(spec.get("type") or "")
        if kind == "enum":
            choices = list(spec.get("choices") or [])
            labels = list(spec.get("labels") or choices)
            if choices and value in choices:
                return labels[choices.index(value)]
            return labels[0] if labels else "—"
        if kind == "bool":
            return "真" if value else "假"
        if value is None or value == "":
            return "未设置" if spec.get("pool") or kind in ("text", "expr") else "0"
        if kind in ("int", "float"):
            return event_flow.value_to_text(event_flow.as_float(value))
        return str(value)

    def _show_input_param(self, node, index: int) -> bool:
        pins = node.inputs(self.catalog)
        if index >= len(pins):
            return False
        pin = pins[index]
        if pin["default"] is None or pin["type"] not in (FLOAT, INT, BOOL, STR):
            return False
        graph = self.graph
        return graph is not None and graph.wire_into(node.id, index) is None

    def _input_value(self, node, index: int):
        if index in node.overrides:
            return node.overrides[index]
        return node.inputs(self.catalog)[index]["default"]

    def _set_input_value(self, node, index: int, value) -> None:
        node.overrides[index] = value

    def _const_field(self, node) -> dict[str, Any] | None:
        item = self._def(node)
        if item["op"] != "const":
            return None
        for spec in item.get("fields") or []:
            if spec["id"] == "v":
                return spec
        return None

    @property
    def _mirror(self) -> bool:
        """输入页镜像：卡片可读（出）引脚在左缘、可写（入）引脚在右缘，数据右→左。"""
        return self._page_key == PAGE_INPUT

    def _pin_value(self, node, kind: str, index: int):
        if kind == "in":
            pins = node.inputs(self.catalog)
            if index >= len(pins):
                return None, None
            return pins[index]["type"], self._input_value(node, index)
        spec = self._const_field(node)
        if spec is None:
            return None, None
        return spec["type"], node.param(self.catalog, "v")

    def layout(self, node):
        cached = self._layouts.get(node.id)
        if cached is not None:
            return cached
        item = self._def(node)
        ins, outs = node.inputs(self.catalog), node.outputs(self.catalog)
        rows = max(len(ins), len(outs), 1)
        fields = self._fields(node)

        width = self._tw(self._title(node), 13, True) + 34 + self._tw(item["cat"], 10)
        if item["exec_in"]:
            width += 14
        for index, pin in enumerate(ins):
            cell = 26 + self._tw(pin["name"], 12)
            if self._show_input_param(node, index):
                cell += self._tw(event_flow.value_to_text(self._input_value(node, index)),
                                 12) + 34
            width = max(width, cell)
        for index, pin in enumerate(outs):
            cell = 26 + self._tw(pin["name"], 12)
            if pin["type"] != EXEC and self._const_field(node) is not None:
                cell += self._tw(event_flow.value_to_text(node.param(self.catalog, "v")),
                                 12) + 34
            width = max(width, cell)
        for spec in fields:
            cell = (14 + self._tw(str(spec["label"]), 11) + 8
                    + self._tw(self._field_text(node, spec), 11.5) + 26)
            width = max(width, cell)
        if item["op"] in _VAR_OPS:
            width = min(VAR_MAX_W, max(VAR_MIN_W, width + 20))
        else:
            width = min(MAX_W, max(MIN_W, width + 20))

        lay = NodeLayout(node)
        lay.w = width
        lay.h = (HEADER_H + ROW_H * rows
                 + (FIELD_H * len(fields) + 4 if fields else 0)
                 + (ERROR_H if self._node_error(node) else 0) + PAD * 2)
        base_y = lay.y + HEADER_H + PAD
        mirror = self._mirror
        for index in range(len(ins)):
            py = base_y + index * ROW_H + ROW_H * 0.5
            px = lay.x + width if mirror else lay.x
            lay.ins.append((px, py))
            if self._show_input_param(node, index):
                label = self._tw(ins[index]["name"], 12)
                value = event_flow.value_to_text(self._input_value(node, index))
                box_w = max(30.0, self._tw(value, 11.5) + 18)
                chip_x = (px - 18 - label - box_w if mirror
                          else lay.x + 18 + label)
                lay.pin_box[("in", index)] = (chip_x, py, box_w, 16.0)
        for index in range(len(outs)):
            py = base_y + index * ROW_H + ROW_H * 0.5
            px = lay.x if mirror else lay.x + width
            lay.outs.append((px, py))
            if outs[index]["type"] != EXEC and self._const_field(node) is not None:
                value = event_flow.value_to_text(node.param(self.catalog, "v"))
                box_w = max(30.0, self._tw(value, 11.5) + 18)
                label = self._tw(outs[index]["name"], 12)
                chip_x = (px + 18 + label if mirror
                          else lay.x + width - 18 - box_w)
                lay.pin_box[("out", index)] = (chip_x, py, box_w, 16.0)
        lay.exec = ((lay.x + width) if (mirror and item["exec_in"]) else lay.x,
                    lay.y + HEADER_H * 0.5)
        field_top = base_y + ROW_H * rows + 2
        for offset, spec in enumerate(fields):
            cy = field_top + offset * FIELD_H + FIELD_H * 0.5
            label_w = self._tw(str(spec["label"]), 11)
            chip_w = max(38.0, self._tw(self._field_text(node, spec), 11.5) + 18)
            lay.fields[spec["id"]] = (lay.x + 12, cy, lay.x + 14 + label_w + 12,
                                      chip_w, 17.0)
        self._layouts[node.id] = lay
        return lay

    def _layout_all(self) -> None:
        graph = self.graph
        self._layouts.clear()
        if graph is None:
            return
        for node in graph.nodes:
            self.layout(node)

    # ------------------------------------------------------------------ 连线曲线
    @staticmethod
    def _bezier(x0, y0, x1, y1, mirror: bool = False):
        bend = max(48.0, abs(x1 - x0) * 0.55)
        if mirror:
            return (x0, y0, x0 - bend, y0, x1 + bend, y1, x1, y1)
        return (x0, y0, x0 + bend, y0, x1 - bend, y1, x1, y1)

    @staticmethod
    def _bezier_point(curve, u: float):
        x0, y0, c0x, c0y, c1x, c1y, x1, y1 = curve
        iu = 1.0 - u
        cube = (iu * iu * iu, 3 * iu * iu * u, 3 * iu * u * u, u * u * u)
        return (cube[0] * x0 + cube[1] * c0x + cube[2] * c1x + cube[3] * x1,
                cube[0] * y0 + cube[1] * c0y + cube[2] * c1y + cube[3] * y1)

    def _wire_curve(self, wire):
        graph = self.graph
        if graph is None:
            return None
        src, dst = graph.find(wire.src[0]), graph.find(wire.dst[0])
        if src is None or dst is None:
            return None
        outs = src.outputs(self.catalog)
        if wire.src[1] < 0 or wire.src[1] >= len(outs):
            return None
        sx, sy = self.layout(src).outs[wire.src[1]]
        if wire.dst[1] < 0:
            dx, dy = self.layout(dst).exec
        else:
            ins = self.layout(dst).ins
            if wire.dst[1] >= len(ins):
                return None
            dx, dy = ins[wire.dst[1]]
        return self._bezier(sx, sy, dx, dy, mirror=self._mirror)

    def _wire_world(self, curve, steps: int | None = None):
        """取样点留在世界坐标：几何与相机无关，平移缩放都能直接复用。"""
        if steps is None:
            x0, y0, _c0x, _c0y, _c1x, _c1y, x1, y1 = curve
            span = math.hypot(x1 - x0, y1 - y0)
            steps = max(6, min(20, int(span / 30.0) + 6))
        return [tuple(self._bezier_point(curve, s / steps)) for s in range(steps + 1)]

    def _wire_screen(self, curve, steps: int) -> list[tuple[float, float]]:
        return [self._screen(*self._bezier_point(curve, s / steps))
                for s in range(steps + 1)]

    # ======================================================================
    # 命中测试
    # ======================================================================
    def _hit(self, wx: float, wy: float) -> tuple:
        graph = self.graph
        if graph is None:
            return ("grid", None)
        for node in reversed(graph.nodes):
            lay = self.layout(node)
            if lay.x - 12 <= wx <= lay.x + lay.w + 12 and lay.y - 2 <= wy <= lay.y + lay.h + 2:
                for index, (px, py) in enumerate(lay.outs):
                    if (px - wx) ** 2 + (py - wy) ** 2 < 100:
                        return ("pin", node, "out", index)
                for index, (px, py) in enumerate(lay.ins):
                    if (px - wx) ** 2 + (py - wy) ** 2 < 100:
                        return ("pin", node, "in", index)
                if self._def(node)["exec_in"]:
                    ex, ey = lay.exec
                    if (ex - wx) ** 2 + (ey - wy) ** 2 < 150:
                        return ("pin", node, "in", -1)
                for (kind, index), box in lay.pin_box.items():
                    if self._in_box(wx, wy, box, 3.0):
                        return ("param", node, kind, index)
                for fid, box in lay.fields.items():
                    if self._in_field(wx, wy, box):
                        return ("field", node, fid)
                if lay.y <= wy <= lay.y + HEADER_H:
                    return ("header", node)
                return ("body", node)
        for wire in graph.wires:
            curve = self._wire_curve(wire)
            if curve is None:
                continue
            for step in range(21):
                px, py = self._bezier_point(curve, step / 20.0)
                if (px - wx) ** 2 + (py - wy) ** 2 < 40:
                    return ("wire", wire)
        return ("grid", None)

    @staticmethod
    def _in_box(wx, wy, box, slack=0.0) -> bool:
        x, cy, w, h = box
        return (x - slack <= wx <= x + w + slack
                and cy - h / 2 - slack <= wy <= cy + h / 2 + slack)

    @staticmethod
    def _in_field(wx, wy, box) -> bool:
        label_x, cy, chip_x, chip_w, chip_h = box
        return (label_x - 2 <= wx <= chip_x + chip_w + 4
                and cy - chip_h / 2 - 2 <= wy <= cy + chip_h / 2 + 2)

    def _side_point(self, sx: float, sy: float) -> tuple | None:
        x0 = self.pw - self.side_w
        if sx < x0 - 5:
            return None
        if x0 - 5 <= sx <= x0 + 5:
            return ("side-divider", None)
        for box in reversed(self.side_hits):
            x, y, w, h, kind, payload = box
            if x <= sx <= x + w and y <= sy <= y + h:
                return (kind, payload, x, y, w, h)
        return ("side", None)

    # ======================================================================
    # 绘制
    # ======================================================================
    def _on_draw(self, sender, args) -> None:
        runtime = self.runtime
        if runtime is None:
            return
        ds = args.DrawingSession
        self.pw = float(sender.ActualWidth or 900.0)
        self.ph = float(sender.ActualHeight or 620.0)
        self._paint = Painter(ds, self._caches, self._bc("text"))
        self._layout_all()
        d = self._paint
        d.clear(self._bc("bg"))
        self._paint_canvas()
        self._paint_side(runtime)
        # 内联输入框最后画：否则会被右侧变量表整块盖住（看起来像「新增」没反应）
        self._paint_edit()
        self._paint_var_ghost()
        while d._layers:
            d.unclip()

    def _paint_canvas(self) -> None:
        d = self._paint
        graph = self.graph
        bottom = self._canvas_bottom()
        d.clip(0, 0, self._cw, bottom)
        self._paint_grid(bottom)
        if graph is None:
            d.unclip()
            return
        self._paint_wires(graph)
        if self.drag_wire is not None:
            self._paint_drag_wire()
        for node in graph.nodes:
            lay = self.layout(node)
            sx, sy = self._screen(lay.x, lay.y)
            sw, sh = lay.w * self.zoom, lay.h * self.zoom
            if (sx + sw < -16.0 or sy + sh < -16.0
                    or sx > self._cw + 16.0 or sy > bottom + 16.0):
                continue
            self._paint_node(node)
        if self.marquee is not None:
            (ax, ay), (bx, by) = self.marquee
            d.rect(min(ax, bx), min(ay, by), abs(bx - ax), abs(by - ay), self._bc("accent", 26))
            d.rrect_stroke(min(ax, bx), min(ay, by), abs(bx - ax), abs(by - ay), 2,
                           self._bc("accent"), 1.0, (4, 3))
        self._paint_hintbar(bottom)
        d.unclip()

    def _paint_grid(self, bottom: float) -> None:
        d = self._paint
        step = 32.0 * self.zoom
        while step < 16.0:
            step *= 4.0
        sig = (round(step, 3), round(self.cam_x % step, 3),
               round(self.cam_y % step, 3), round(self._cw, 1),
               round(bottom, 1))
        if sig != self._grid_sig:
            self._grid_sig = sig
            self._drop(self._grid_old)
            self._grid_old = self._grid
            self._grid = self._build_grid(step, bottom)
        d.stroke_geometry(self._grid.get("minor"), self._bc("grid"), 1.0)
        d.stroke_geometry(self._grid.get("major"), self._bc("grid_major"), 1.0)

    def _build_grid(self, step: float, bottom: float) -> dict[str, Any]:
        minor: list = []
        major: list = []
        x = self.cam_x % step
        column = math.floor(self.cam_x / step)
        while x < self._cw:
            (major if column % 4 == 0 else minor).append(([(x, 0.0), (x, bottom)], False))
            x += step
            column += 1
        y = self.cam_y % step
        row = math.floor(self.cam_y / step)
        while y < bottom:
            (major if row % 4 == 0 else minor).append(
                ([(0.0, y), (self._cw, y)], False))
            y += step
            row += 1
        return {"minor": self._paint.build(minor), "major": self._paint.build(major)}

    def _curve_visible(self, curve, bottom: float) -> bool:
        xs, ys = curve[0::2], curve[1::2]
        sx0, sy0 = self._screen(min(xs), min(ys))
        sx1, sy1 = self._screen(max(xs), max(ys))
        return not (sx1 < -16.0 or sy1 < -16.0
                    or sx0 > self._cw + 16.0 or sy0 > bottom + 16.0)

    def _paint_wires(self, graph) -> None:
        """连线按类型合并成少量世界坐标几何一次画完，几何按图内容跨帧缓存。"""
        bottom = self._canvas_bottom()
        entries = []
        for wire in graph.wires:
            curve = self._wire_curve(wire)
            if curve is None or not self._curve_visible(curve, bottom):
                continue
            entries.append((wire, curve))
        sig = tuple((w.id, tuple(round(v, 1) for v in c), w.type) for w, c in entries)
        if sig != self._geo_sig:
            self._rebuild_wires(entries)
            self._geo_sig = sig
        zoom = max(self.zoom, 0.05)
        inv = 1.0 / zoom
        cam_x, cam_y = float(self.cam_x), float(self.cam_y)

        def view(dy: float = 0.0) -> Matrix3x2:
            return Matrix3x2(M11=zoom, M12=0.0, M21=0.0, M22=zoom,
                             M31=cam_x, M32=cam_y + dy)

        for geometry in self._geo.values():
            self._paint.stroke_geometry(geometry, Color(110, 0, 0, 0), 3.4 * inv,
                                        None, view(1.3))
        for ptype, geometry in self._geo.items():
            dash = tuple(round(v * inv, 2) for v in DASH_EXEC) if ptype == EXEC else None
            self._paint.stroke_geometry(geometry, self._type_color(ptype),
                                        2.0 * inv, dash, view())
        for wire, curve in entries:
            self._paint_wire_overlay(wire, curve)

    def _rebuild_wires(self, entries) -> None:
        d = self._paint
        self._close_geo()
        groups: dict[str, list] = {}
        points: dict[str, list] = {}
        for wire, curve in entries:
            world = self._wire_world(curve)
            points[wire.id] = world
            groups.setdefault(wire.type, []).append((world, False))
        self._geo_old = self._geo
        self._geo = {ptype: d.build(shapes) for ptype, shapes in groups.items()}
        self._wire_pts = points

    def _close_geo(self) -> None:
        """上一代几何已在上一次绘制中用完，可以安全释放。"""
        self._drop(self._geo_old)
        self._geo_old = {}

    @staticmethod
    def _drop(store: dict) -> None:
        for geometry in store.values():
            if geometry is None:
                continue
            try:
                geometry.Close()
            except Exception:
                pass

    def _release_geo(self) -> None:
        """重建页面时把缓存里的几何全部释放，避免换主题反复泄漏。"""
        for store in (self._geo, self._geo_old, self._grid, self._grid_old):
            self._drop(store)
        self._geo = {}
        self._geo_old = {}
        self._grid = {}
        self._grid_old = {}

    def _paint_wire_overlay(self, wire, curve) -> None:
        """方向箭头与选中高亮：数量少，逐条画。"""
        d = self._paint
        world = self._wire_pts.get(wire.id)
        if not world:
            return
        points = [self._screen(x, y) for x, y in world]
        is_exec = wire.type == EXEC
        selected = wire.id in self.graph.selected_wires
        hovered = bool(self.hover and self.hover[0] == "wire"
                       and getattr(self.hover[1], "id", None) == wire.id)
        color = self._type_color(wire.type)
        if selected or hovered:
            d.poly(points, color, 2.8, DASH_EXEC if is_exec else None)
        px, py = self._screen(*self._bezier_point(curve, 0.5))
        qx, qy = self._screen(*self._bezier_point(curve, 0.56))
        angle = math.atan2(qy - py, qx - px)
        d.tri(px, py, 4.6 if is_exec else 3.8, color, angle=angle)
        if selected or hovered:
            mid = self._screen(*self._bezier_point(curve, 0.5))
            label = (f"{TYPE_LABELS.get(wire.type, wire.type)} 流" if is_exec else
                     f"{TYPE_LABELS.get(wire.type, wire.type)} · "
                     f"{event_flow.value_to_text(wire.value)}")
            box_w = self._tw(label, 11) + 16
            d.rrect(mid[0] - box_w / 2, mid[1] - 9, box_w, 18, 5, self._bc("hint", 235))
            d.text(label, mid[0] - box_w / 2 + 8, mid[1] - 6, 11, color)

    def _paint_drag_wire(self) -> None:
        d = self._paint
        _started, node, kind, index = self.drag_wire
        lay = self.layout(node)
        wx, wy = self._world(*self._mouse)
        if kind == "out":
            if index >= len(lay.outs):
                return
            sx, sy = lay.outs[index]
            curve = self._bezier(sx, sy, wx, wy, mirror=self._mirror)
            ptype = self.graph.out_type(self.catalog, node, index)
        else:
            if index < 0:
                sx, sy = lay.exec
            elif index < len(lay.ins):
                sx, sy = lay.ins[index]
            else:
                return
            curve = self._bezier(wx, wy, sx, sy, mirror=self._mirror)
            ptype = EXEC if index < 0 else self.graph.in_type(self.catalog, node, index)
        end = (wx, wy)
        target = self.hover
        if (target and target[0] == "pin" and target[1] is not node
                and target[1] in self.graph.nodes and target[2] != kind):
            tkind, tindex = target[2], target[3]
            ttype = (EXEC if tindex < 0 else
                     (self.graph.in_type(self.catalog, target[1], tindex)
                      if tkind == "in" else
                      self.graph.out_type(self.catalog, target[1], tindex)))
            src_type, dst_type = ((ptype, ttype) if kind == "out"
                                  else (ttype, ptype))
            if compatible(src_type, dst_type):
                tlay = self.layout(target[1])
                if tindex < 0:
                    end = tlay.exec
                else:
                    cells = tlay.ins if tkind == "in" else tlay.outs
                    if tindex < len(cells):
                        end = cells[tindex]
                curve = (self._bezier(sx, sy, *end, mirror=self._mirror)
                         if kind == "out"
                         else self._bezier(*end, sx, sy, mirror=self._mirror))
        color = self._type_color(ptype)
        d.poly(self._wire_screen(curve, 20), color, 2.4,
               DASH_EXEC if ptype == EXEC else None)
        d.circle(*self._screen(*end), 3.2, color)
        if target and target[0] == "pin" and target[1] is not node:
            tlay = self.layout(target[1])
            if target[3] < 0:
                px, py = tlay.exec
            else:
                cells = tlay.ins if target[2] == "in" else tlay.outs
                if target[3] >= len(cells):
                    return
                px, py = cells[target[3]]
            sx, sy = self._screen(px, py)
            d.circle(sx, sy, (PIN_R + 3.5) * self.zoom, Color(153, 255, 255, 255), False, 1.4)

    def _paint_node(self, node) -> None:
        d = self._paint
        lay = self.layout(node)
        item = self._def(node)
        sx, sy = self._screen(lay.x, lay.y)
        sw, sh = lay.w * self.zoom, lay.h * self.zoom
        head = self._cat_color(item["cat"])
        hovered = bool(self.hover and self.hover[0] in ("header", "body", "param", "field", "pin")
                       and len(self.hover) > 1 and self.hover[1] is node)
        d.rrect(sx + 1.5, sy + 2.5, sw, sh, 7, Color(110, 0, 0, 0))
        d.rrect(sx, sy, sw, sh, 7, self._bc("node_sel" if node.selected else "node"))
        hh = HEADER_H * self.zoom
        d.rrect(sx, sy, sw, hh + 5, 7, head)
        d.rect(sx + 1, sy + hh - 5, sw - 2, 5, head)
        d.rect(sx + 1, sy + hh, sw - 2, 1.0, _mix(head, 0.55))
        d.rrect_stroke(sx, sy, sw, sh, 7,
                       self._bc("node_border_sel" if node.selected else
                                ("divider" if hovered else "node_border")),
                       1.8 if node.selected else 1.0)

        mirror = self._mirror
        has_exec_out = any(p["type"] == EXEC for p in node.outputs(self.catalog))
        label_x = sx + (24 * self.zoom
                        if (item["exec_in"] != mirror
                            or (mirror and has_exec_out)) else 12)
        scale = max(self.zoom, 0.72)
        size = 13 * scale
        cat = item["cat"]
        cat_w = self._tw(cat, 10) * scale
        live = self._live_head(node, item)
        live_w = (self._tw(live[0], 11.5) * scale + 16.0) if live else 0.0
        head_right = (28 if mirror and item["exec_in"] else 10) * self.zoom
        if not self._editing(node, "alias"):
            d.text(self._title(node), label_x, sy + 5 * self.zoom, size,
                   Color(255, 245, 246, 248), True,
                   maxw=max(20.0, sw - cat_w - live_w - head_right - 16))
        d.text(cat, sx + sw - cat_w - live_w - head_right,
               sy + 7 * self.zoom, 10 * scale, Color(170, 255, 255, 255))
        if live:
            self._paint_head_value(live, sx + sw - live_w - head_right + 4,
                                   sy + 4 * self.zoom, live_w - 8,
                                   hh - 8 * self.zoom)

        error = self._node_error(node)
        if error:
            d.text(error, label_x, sy + sh - 15 * self.zoom,
                   10.5 * max(self.zoom, 0.7), self._bc("danger"),
                   maxw=max(20.0, sw - 24 * self.zoom))

        if item["exec_in"]:
            ex, ey = self._screen(*lay.exec)
            connected = bool(self.graph.wires_into(node.id, -1))
            d.tri(ex - 4 if mirror else ex + 4, ey, 6 * self.zoom,
                  self._type_color(EXEC) if connected else self._bc("pin_off"),
                  angle=math.pi if mirror else None)

        pins = node.inputs(self.catalog)
        for index, (px, py) in enumerate(lay.ins):
            wired = self.graph.wire_into(node.id, index) is not None
            x, y = self._screen(px, py)
            pcolor = self._type_color(pins[index]["type"])
            d.circle(x, y, PIN_R * self.zoom, pcolor if wired else self._bc("hole"))
            if not wired:
                d.circle(x, y, PIN_R * self.zoom, self._bc("pin_off"), False, 1.4)
            name_w = self._tw(pins[index]["name"], 12) * max(self.zoom, 0.7)
            d.text(pins[index]["name"],
                   x - 10 - name_w if mirror else x + 10,
                   y - 7 * max(self.zoom, 0.7),
                   12 * max(self.zoom, 0.7), self._bc("text_dim"))
            if ("in", index) in lay.pin_box:
                self._paint_param(lay.pin_box[("in", index)], pins[index]["type"],
                                  self._input_value(node, index), node, "in", index)

        outs = node.outputs(self.catalog)
        for index, (px, py) in enumerate(lay.outs):
            wired = bool(self.graph.wires_from(node.id, index))
            x, y = self._screen(px, py)
            color = self._type_color(outs[index]["type"])
            label_w = self._tw(outs[index]["name"], 12) * max(self.zoom, 0.7)
            if outs[index]["type"] == EXEC:
                d.tri(x, y, 6 * self.zoom, color if wired else self._bc("pin_off"),
                      angle=math.pi if mirror else None)
            else:
                d.circle(x, y, PIN_R * self.zoom, color if wired else self._bc("hole"))
                if not wired:
                    d.circle(x, y, PIN_R * self.zoom, self._bc("pin_off"), False, 1.4)
            d.text(outs[index]["name"],
                   x + 10 if mirror else x - 10 - label_w,
                   y - 7 * max(self.zoom, 0.7),
                   12 * max(self.zoom, 0.7), self._bc("text_dim"))
            if ("out", index) in lay.pin_box:
                ptype, value = self._pin_value(node, "out", index)
                self._paint_param(lay.pin_box[("out", index)], ptype, value, node, "out", index)

        for spec in self._fields(node):
            box = lay.fields.get(spec["id"])
            if box is not None:
                self._paint_field(node, spec, box)

    def _live_head(self, node, item):
        """卡片抬头上显示的实时值：读数卡显示当前值，写入卡显示这一拍写进去的值。

        值放在抬头而不是引脚行——贴在引脚旁的值框会挡住连接点标志，连线时
        看不清落点。
        """
        op = item["op"]
        if op in ("mod_read", "core_read", "temp_read"):
            pins = node.outputs(self.catalog)
            if op == "temp_read":
                temps = dict(self.runtime.temps or {}) if self.runtime else {}
                value = temps.get(self._node_var_name(node))
            else:
                value = node.live[0] if node.live else None
        elif (op in ("core_write", "mod_write", "temp_write")
                and self.graph is not None
                and self.graph.wire_into(node.id, 0)):
            pins = node.inputs(self.catalog)
            value = node.live[0] if node.live else None
        else:
            return None
        if value is None or not pins:
            return None
        return (event_flow.value_to_text(value), pins[0]["type"])

    def _node_var_name(self, node) -> str:
        """变量卡片指向的变量名：优先取连进来的变量对象卡，其次卡片自己填的名。"""
        return str(node.params.get("name") or "")

    def _paint_head_value(self, live, x: float, y: float, w: float,
                          h: float) -> None:
        d = self._paint
        text, ptype = live
        color = self._type_color(ptype)
        d.rrect(x, y, w, h, 3.5, self._bc("param"))
        d.rrect_stroke(x, y, w, h, 3.5, color, 1.0)
        size = 11.5 * max(self.zoom, 0.72)
        d.text(text, x + 5, y + max(0.0, (h - size) / 2) + 0.5, size, color,
               True, maxw=max(12.0, w - 9))

    def _editing(self, node, kind: str, key=None) -> bool:
        """该控件此刻正被 Win2D 输入框接管，自绘时应让位，避免文字叠字。"""
        target = self._edit_target
        if target is None or self._edit_rect is None or target[0] is not node:
            return False
        if target[1] != kind:
            return False
        if key is None:
            return True
        current = target[2]
        return current.get("id") == key if isinstance(current, dict) else current == key

    def _paint_param(self, box, ptype, value, node, kind, index) -> None:
        d = self._paint
        if self._editing(node, kind, index):
            return
        x, cy, w, h = box
        sx, sy = self._screen(x, cy)
        sw, sh = w * self.zoom, h * self.zoom
        active = self._param_hot(node, kind, index)
        d.rrect(sx, sy - sh / 2, sw, sh, 3.5, self._bc("param_hot" if active else "param"))
        d.rrect_stroke(sx, sy - sh / 2, sw, sh, 3.5,
                       self._type_color(ptype) if active else self._bc("divider"), 1.0)
        text = ("真" if value is True else "假" if value is False
                else event_flow.value_to_text(value))
        color = self._type_color(BOOL) if isinstance(value, bool) else self._type_color(ptype)
        width = self._tw(text, 11.5) * max(self.zoom, 0.72)
        d.text(text, sx + max(5.0, (sw - width) / 2 + 3), sy - sh / 2 + 1.5,
               11.5 * max(self.zoom, 0.72), color)

    def _paint_field(self, node, spec, box) -> None:
        d = self._paint
        label_x, cy, chip_x, chip_w, chip_h = box
        lx, ly = self._screen(label_x, cy)
        size = 11 * max(self.zoom, 0.72)
        d.text(spec["label"], lx, ly - size * 0.62, size, self._bc("text_faint"))
        if self._editing(node, "field", spec["id"]):
            return
        cx, cy2 = self._screen(chip_x, cy)
        text = self._field_text(node, spec)
        sw, sh = chip_w * self.zoom, chip_h * self.zoom
        active = bool(self.hover and self.hover[0] == "field" and self.hover[1] is node
                      and self.hover[2] == spec["id"])
        dragging = bool(self.param_drag and self.param_drag[1] is node
                        and self.param_drag[2] == "field" and self.param_drag[3] == spec["id"])
        d.rrect(cx, cy2 - sh / 2, sw, sh, 3.5,
                self._bc("param_hot" if (active or dragging) else "param"))
        d.rrect_stroke(cx, cy2 - sh / 2, sw, sh, 3.5,
                       self._bc("accent") if (active or dragging) else self._bc("divider"), 1.0)
        color = self._bc("text_faint") if text == "未设置" else (
            self._type_color(BOOL) if str(spec.get("type")) == "bool" else self._bc("text"))
        d.text(text, cx + 6, cy2 - sh / 2 + 1.5, 11.5 * max(self.zoom, 0.72), color,
               maxw=max(10.0, sw - 12))

    def _param_hot(self, node, kind: str, index: int) -> bool:
        drag = self.param_drag
        if drag and drag[1] is node and drag[2] == kind and drag[3] == index:
            return True
        return bool(self.hover and self.hover[0] == "param" and self.hover[1] is node
                    and self.hover[2] == kind and self.hover[3] == index)

    def _paint_hintbar(self, bottom: float) -> None:
        d = self._paint
        hot = bool(self.status and self.status_until > self.now)
        hint = self.status if hot else (
            "右键空白 → 搜索卡片     从引脚拖出 → 连线（拖到空白可搜索可连接卡片）     "
            "中键 / 空格拖动 → 平移     滚轮 → 缩放     Del → 删除     右键连线 → 断开")
        y = bottom - 32
        box_w = min(self._cw - 28, self._tw(hint, 11.5) + 28)
        d.rrect(14, y, box_w, 26, 7, self._bc("hint", 225))
        d.rrect_stroke(14, y, box_w, 26, 7,
                       self._bc("accent") if hot else self._bc("divider"), 1.0)
        d.text(hint, 28, y + 6, 11.5, self._bc("accent") if hot else self._bc("text_dim"),
               maxw=box_w - 28)

    # ---------------------------------------------------------------- 变量表面板
    def _paint_side(self, runtime) -> None:
        d = self._paint
        x0 = self.pw - self.side_w
        self.side_hits = []
        self._var_rects = []
        d.rect(x0, 0, self.side_w, self.ph, self._bc("panel"))
        d.line(x0, 0, x0, self.ph, self._bc("divider"), 1.0)
        d.line(x0 - 3.5, 0, x0 - 3.5, self.ph,
               self._bc("accent") if self.side_drag is not None
               else Color(13, 255, 255, 255), 2.5)
        system, user = runtime.var_table()
        d.clip(x0, 0, self.side_w, 34)
        d.rect(x0, 0, self.side_w, 34, self._bc("panel_head"))
        title = "变量表 · Variables"
        d.text(title, x0 + 12, 9, 13, self._bc("text"), True)
        info = f"{len(system)} + {len(user)}"
        d.text(info, x0 + self.side_w - 12 - self._tw(info, 11), 11, 11,
               self._bc("text_faint"))
        d.unclip()
        self._paint_side_rows(x0 + 10, 40.0, x0 + self.side_w - 10,
                              self.ph - 8.0, system, user)
        self._sync_var_boxes()

    def _side_rows_total(self, system: list, user: list) -> float:
        return (SECTION_H + max(len(system), 1) * VAR_ROW_H + 8.0 + SECTION_H
                + max(len(user), 1) * VAR_ROW_H + VAR_ROW_H)

    def _paint_side_rows(self, left: float, top: float, right: float,
                         bottom: float, system: list, user: list) -> None:
        """系统参数（不可改名）与临时变量（可改名）两段，整列可滚动、可拖出建卡。"""
        d = self._paint
        width = max(60.0, right - left)
        total = self._side_rows_total(system, user)
        visible = max(40.0, bottom - top)
        self.side_scroll = max(0.0, min(self.side_scroll, total - visible))
        d.clip(left - 10, top - 2, width + 20, visible + 4)
        y = top - self.side_scroll

        d.text(f"系统参数 · 不可改名（{len(system)}）", left + 2, y + 5, 11,
               self._bc("text_dim"))
        y += SECTION_H
        if not system:
            d.text("（模块未启动 / 设备未连接）", left + 2, y + 6, 11.5,
                   self._bc("text_faint"))
            # 空态提示占整行高度：总数按空段预留了一行，绘制侧不占位
            # 就会和下一段标题叠在一起
            y += VAR_ROW_H
        for row in system:
            self._paint_var_row(left, y, width, row, drag=True)
            y += VAR_ROW_H
        y += 8.0

        d.text(f"可改名参数 · 点名称框改名（{len(user)}）", left + 2, y + 5, 11,
               self._bc("text_dim"))
        add_label = "＋ 新增"
        add_w = self._tw(add_label, 11.5) + 16
        add_x = right - add_w
        hot = bool(self.hover and self.hover[0] == "var-add")
        d.rrect(add_x, y + 2, add_w, VAR_ROW_H - 6, 4,
                self._bc("param_hot" if hot else "param"))
        d.rrect_stroke(add_x, y + 2, add_w, VAR_ROW_H - 6, 4,
                       self._bc("divider"), 1.0)
        d.text(add_label, add_x + 8, y + 6, 11.5,
               self._bc("text") if hot else self._bc("text_dim"))
        self.side_hits.append((add_x, y + 2, add_w, VAR_ROW_H - 4,
                               "var-add", None))
        y += SECTION_H
        if not user:
            d.text("（点「＋ 新增」登记一个变量名）", left + 2, y + 6, 11.5,
                   self._bc("text_faint"))
            y += VAR_ROW_H
        for row in user:
            self._paint_var_row(left, y, width, row, rename=True,
                                boxed=top - 2.0 <= y <= bottom)
            y += VAR_ROW_H
        d.unclip()

    def _paint_var_row(self, left: float, y: float, width: float,
                       row: dict, *, rename: bool = False,
                       drag: bool = False, boxed: bool = True) -> None:
        """一行变量：名称 + 来源 + 读 / 写 + 类型 + 实时值 + 删除。

        可改名行的名称格不自己画字，而是留位置给叠在上面的输入框（boxed=False
        表示这行滚出可视区了，输入框也就不用贴，免得盖到画布上）。
        宽度不够时**只压缩变量名**，来源 / 读写 / 类型 / 实时值 / 删除全部保留；
        临时变量的读写胶囊与删除按钮始终可点。
        """
        d = self._paint
        name = str(row.get("name") or "")
        mid = str(row.get("mid") or "")
        value = row.get("value")
        ptype = event_flow.spec_type(row.get("type"))
        type_text = event_flow.TYPE_LABELS.get(ptype, "数值")
        direction = str(row.get("dir") or "in")
        dir_text = _DIR_MARKS.get(direction, "")
        value_text = (event_flow.value_to_text(value)
                      if value is not None else "—")
        tag = _module_tag(row)
        deletable = bool(rename) and not mid
        toggle = bool(rename) and not mid
        hov = bool(self.hover and self.hover[0] == "var-row"
                   and self.hover[1] == name)
        d.rrect(left, y + 1, width, VAR_ROW_H - 3, 5,
                self._bc("param_hot" if hov else "param"))
        pad = 8.0
        right = left + width
        del_label = "删除"
        del_w = (self._tw(del_label, 11) + 14) if deletable else 0.0
        value_w = self._tw(value_text, 12) + 6
        type_w = self._tw(type_text, 10.5) + 8
        dir_w = (self._tw(dir_text, 10.5) + 14) if dir_text else 0.0
        tag_w = (self._tw(f"· {tag}", 10) + 8) if tag else 0.0
        cursor = right - pad
        if del_w:
            cursor -= del_w
            hot_del = bool(self.hover and self.hover[0] == "var-del"
                           and self.hover[1] == name)
            d.rrect(cursor, y + 5, del_w, VAR_ROW_H - 11, 4,
                    self._bc("param_hot") if hot_del
                    else self._bc("panel_head"))
            d.rrect_stroke(cursor, y + 5, del_w, VAR_ROW_H - 11, 4,
                           self._bc("danger") if hot_del else self._bc("divider"),
                           1.0)
            d.text(del_label, cursor + 7, y + 7, 11,
                   self._bc("danger") if hot_del else self._bc("text_dim"))
        cursor -= value_w
        d.text(value_text, cursor + 3, y + 6, 12,
               self._bc("text") if value is not None else self._bc("text_faint"))
        cursor -= type_w
        d.text(type_text, cursor + 4, y + 8, 10.5, self._bc("text_faint"))
        if dir_w:
            cursor -= dir_w
            hot_dir = bool(self.hover and self.hover[0] == "var-dir"
                           and self.hover[1] == name)
            d.rrect(cursor, y + 5, dir_w, VAR_ROW_H - 11, 4,
                    self._bc("param_hot") if (toggle and hot_dir)
                    else self._bc("panel_head"))
            d.rrect_stroke(cursor, y + 5, dir_w, VAR_ROW_H - 11, 4,
                           self._bc("accent") if (toggle and hot_dir)
                           else self._bc("divider"), 1.0)
            d.text(dir_text, cursor + 7, y + 8, 10.5, self._bc("accent"))
        if tag_w:
            cursor -= tag_w
            d.text(f"· {tag}", cursor + 4, y + 9, 10, self._bc("text_faint"))
        grip = bool(rename or drag)
        indent = GRIP_W if grip else 0.0
        name_room = max(36.0, cursor - left - pad - indent)
        if grip:
            # 系统参数行与可改名行用同一套拖动柄：整列左缘对齐，拖哪个都一样
            self._paint_grip(left + 3.0, y + VAR_ROW_H / 2.0)
        if rename and boxed:
            # 名称格交给叠在上面的真实输入框：点进去就是改名，不会再和拖动抢位置
            self._var_rects.append((name, left + pad + indent, y + 4.0,
                                    name_room, VAR_ROW_H - 10.0))
        else:
            d.text(name, left + pad + indent, y + 6, 12,
                   self._bc("text_dim"), maxw=name_room)
        if grip:
            self.side_hits.append((left, y + 1, max(20.0, cursor - left),
                                   VAR_ROW_H - 3, "var-row", name))
            self._side_rows[name] = dict(row)
        if toggle:
            self.side_hits.append((cursor + tag_w, y + 1, dir_w, VAR_ROW_H - 3,
                                   "var-dir", name))
        if deletable:
            self.side_hits.append((right - pad - del_w, y + 1, del_w,
                                   VAR_ROW_H - 3, "var-del", name))

    def _paint_grip(self, x: float, cy: float) -> None:
        """行左侧的拖动柄：按住它把变量拖到画布建卡（名称格是输入框，不响应拖动）。"""
        d = self._paint
        for row in (-5.0, 0.0, 5.0):
            d.circle(x + 2.0, cy + row, 1.3, self._bc("text_faint"))
            d.circle(x + 7.0, cy + row, 1.3, self._bc("text_faint"))

    # ------------------------------------------------------------ 名称输入框
    # 行号在建框时就绑进闭包：Tag 读回来是裸 IInspectable、事件 sender 也一样，
    # 控件对象之间又不能可靠地比 identity（list.index 走 __eq__），只有下标稳。
    def _make_var_box(self, index: int) -> TextBox:
        box = TextBox()
        box.FontSize = 12.0
        box.Padding = Thickness(3, 0, 3, 0)
        box.Margin = Thickness(0)
        # WinUI 的 TextBox 样式自带 MinHeight 32：不显式清掉就会顶掉行高、
        # 一格的框压到下一行上
        box.MinHeight = 0.0
        box.MinWidth = 0.0
        box.HorizontalAlignment = HorizontalAlignment.Left
        box.VerticalAlignment = VerticalAlignment.Top
        box.IsSpellCheckEnabled = False
        box.Visibility = Visibility.Collapsed
        box.KeyDown += lambda s, e, _i=index: self._on_var_box_key(_i, e)
        box.LostFocus += lambda s, e, _i=index: self._commit_var_box(_i)
        self.overlay.Children.Append(box)
        self._var_boxes.append(box)
        self._var_box_keys.append(None)
        self._var_box_names.append("")
        return box

    def _sync_var_boxes(self) -> None:
        """把本轮画出的名称格贴上一一对应的输入框，其余全部收起。"""
        want = [] if self._pal_open else list(self._var_rects)
        ox, oy = self._overlay_of_canvas(0.0, 0.0)
        for index, item in enumerate(want):
            name, x, y, w, h = item
            while len(self._var_boxes) <= index:
                self._make_var_box(len(self._var_boxes))
            box = self._var_boxes[index]
            key = (name, round(x + ox, 1), round(y + oy, 1), round(w, 1))
            if self._var_box_keys[index] == key:
                continue
            self._var_box_keys[index] = key
            self._var_box_names[index] = name
            box.Width = max(36.0, w)
            box.Height = max(16.0, h)
            Canvas.SetLeft(box, x + ox)
            Canvas.SetTop(box, y + oy)
            box.Visibility = Visibility.Visible
            try:
                focused = box.FocusState != FocusState.Unfocused
            except Exception:
                focused = False
            if not focused and (box.Text or "") != name:
                box.Text = name
        for extra in range(len(want), len(self._var_boxes)):
            if self._var_box_keys[extra] is None:
                continue
            self._var_box_keys[extra] = None
            self._var_box_names[extra] = ""
            self._var_boxes[extra].Visibility = Visibility.Collapsed

    def _drop_var_boxes(self) -> None:
        self._var_boxes = []
        self._var_box_keys = []
        self._var_box_names = []
        self._var_rects = []

    def _var_box_index(self, name: str) -> int:
        """变量名 → 输入框下标；找不到返回 -1（overlay 重建后旧闭包就走这条）。"""
        try:
            return self._var_box_names.index(name)
        except ValueError:
            return -1

    def _var_box_for(self, name: str):
        """按变量名找到它那一格输入框（自检与外部定位都用它）。"""
        index = self._var_box_index(name)
        return self._var_boxes[index] if 0 <= index < len(self._var_boxes) else None

    def _on_var_box_key(self, index: int, args) -> None:
        vk = _key_of(args)
        if vk in (VK_RETURN, VK_TAB):
            self._commit_var_box(index)
            args.Handled = True
        elif vk == VK_ESCAPE:
            self._restore_var_box(index)
            args.Handled = True

    def _restore_var_box(self, index: int) -> None:
        box = self._var_boxes[index] if 0 <= index < len(self._var_boxes) else None
        name = str(self._var_box_names[index] or "") if box is not None else ""
        if box is not None and name and (box.Text or "") != name:
            box.Text = name

    def _commit_var_box(self, index: int) -> None:
        if not 0 <= index < len(self._var_boxes):
            return
        name = str(self._var_box_names[index] or "")
        text = (self._var_boxes[index].Text or "").strip()
        if not name or not text or text == name:
            return
        self._commit_var_edit(name, text)

    def _finish_var_drag(self, wx: float, wy: float) -> bool:
        """从变量表拖到画布松手：按方向与当前页面建该变量的读数 / 写入卡。"""
        row = self.var_drag[0]
        if not self.var_drag[3] or wx >= self._cw:
            return False
        name = str(row.get("name") or "")
        if not name:
            return False
        key = event_flow.var_card_key(name, str(row.get("dir") or ""),
                                      self._page_key, str(row.get("mid") or ""))
        node = self._create_node(key, wx - 60.0, wy - 20.0)
        if node is None:
            return True
        node.params["name"] = name
        if row.get("mid"):
            node.params["module"] = str(row.get("mid"))
        self._layouts.clear()
        self._commit(force=True)
        self.say(f"已建卡片：{self._title(node)}")
        return True

    def _paint_var_ghost(self) -> None:
        """拖动变量时的跟随提示：告诉用户松手会建什么卡。"""
        if not self.var_drag:
            return
        row, sx, sy = self.var_drag[0], self.var_drag[1], self.var_drag[2]
        d = self._paint
        label = f"{row.get('name')} → {self._var_card_hint(row)}"
        w = self._tw(label, 11.5) + 20
        d.rrect(sx - w / 2, sy - 12, w, 24, 6, self._bc("menu", 235))
        d.rrect_stroke(sx - w / 2, sy - 12, w, 24, 6, self._bc("accent"), 1.2)
        d.text(label, sx - w / 2 + 10, sy - 5, 11.5, self._bc("text"))

    def _var_card_hint(self, row: dict) -> str:
        """拖动提示：松手会建这张变量的读数卡还是写入卡。"""
        key = event_flow.var_card_key(
            str(row.get("name") or ""), str(row.get("dir") or ""),
            self._page_key, str(row.get("mid") or ""))
        return "写入卡" if ".write." in key else "读数卡"

    # ======================================================================
    # 指针交互
    # ======================================================================
    def _point(self, sender, args) -> tuple[float, float]:
        try:
            pos = args.GetCurrentPoint(self.canvas).Position
            return float(pos.X), float(pos.Y)
        except Exception:
            return 0.0, 0.0

    def _buttons_of(self, args) -> tuple[bool, bool, bool]:
        try:
            props = args.GetCurrentPoint(self.canvas).Properties
            return (bool(props.IsLeftButtonPressed), bool(props.IsRightButtonPressed),
                    bool(props.IsMiddleButtonPressed))
        except Exception:
            return True, False, False

    def _on_pressed(self, sender, args) -> None:
        sx, sy = self._point(sender, args)
        self._mouse = (sx, sy)
        left, right, middle = self._buttons_of(args)
        try:
            self.canvas.Focus(FocusState.Programmatic)
        except Exception:
            pass
        if self._pal_open and not self._in_palette(sx, sy):
            self._close_palette()
            return
        if self._edit_target is not None:
            self._commit_edit()
        if middle or (self.space_pan and left):
            self.panning = (sx, sy)
            self.canvas.CapturePointer(args.Pointer)
            args.Handled = True
            return
        if right:
            return
        side = self._side_point(sx, sy)
        if side is not None:
            self._side_pressed(side, args)
            return
        wx, wy = self._world(sx, sy)
        hit = self._hit(wx, wy)
        kind = hit[0]
        if kind == "pin":
            self.drag_wire = (True, hit[1], hit[2], hit[3])
            self.canvas.CapturePointer(args.Pointer)
        elif kind == "param":
            self.param_drag = [True, hit[1], hit[2], hit[3], sx, sy]
            self._select_only(hit[1], _down(VK_SHIFT))
            self.canvas.CapturePointer(args.Pointer)
        elif kind == "field":
            self.param_drag = [True, hit[1], "field", hit[2], sx, sy]
            self._select_only(hit[1], _down(VK_SHIFT))
            self.canvas.CapturePointer(args.Pointer)
        elif kind in ("header", "body"):
            node = hit[1]
            if _down(VK_SHIFT) or _down(VK_MENU):
                node.selected = not node.selected
            elif not node.selected:
                self._select_only(node, False)
            self.moving = (wx, wy)
            self.canvas.CapturePointer(args.Pointer)
        elif kind == "wire":
            wire = hit[1]
            graph = self.graph
            if _down(VK_SHIFT):
                graph.selected_wires ^= {wire.id}
            else:
                graph.selected_wires = {wire.id}
            if _down(VK_MENU):
                graph.remove_wire(wire)
                self.say("已断开连线")
                self._commit()
        else:
            self._clear_selection()
            self.marquee = ((sx, sy), (sx, sy))
            self.canvas.CapturePointer(args.Pointer)
        args.Handled = True
        self._invalidate()

    def _in_palette(self, sx: float, sy: float) -> bool:
        try:
            left = float(Canvas.GetLeft(self.palette))
            top = float(Canvas.GetTop(self.palette))
            wide = float(self.palette.ActualWidth or 324.0)
            tall = float(self.palette.ActualHeight or 300.0)
            return left - 4 <= sx <= left + wide + 4 and top - 4 <= sy <= top + tall + 4
        except Exception:
            return False

    def _side_pressed(self, side, args) -> None:
        kind = side[0]
        if kind == "side-divider":
            self.side_drag = self.side_w - (self.pw - self._mouse[0])
            self.canvas.CapturePointer(args.Pointer)
        elif kind == "var-add":
            self._begin_var_edit("", side[2:])
        elif kind == "var-del":
            self._remove_var(str(side[1]))
        elif kind == "var-dir":
            self._toggle_var_dir(str(side[1]))
        elif kind == "var-row":
            # 行上没有改名输入框了：按下行只可能是拖到画布建卡
            self.var_drag = [self._side_rows.get(str(side[1])) or {},
                             self._mouse[0], self._mouse[1], False,
                             self._mouse[0], self._mouse[1]]
        self._invalidate()

    def _toggle_var_dir(self, name: str) -> None:
        """临时变量切换读 / 写：只读=只能取用，只写=只能回传，读写两者都行。"""
        runtime = self.runtime
        if runtime is None:
            return
        current = runtime.user_var_dir(name)
        want = _DIR_CYCLE.get(current, "in")
        note = runtime.set_var_dir(name, want)
        self.say(note or f"{name} 已切为「{_DIR_MARKS.get(want)}」")
        if not note:
            self._commit(force=True)
        self._invalidate()

    def _remove_var(self, name: str) -> None:
        runtime = self.runtime
        if runtime is None:
            return
        if runtime.remove_user_var(name):
            self.say(f"已删除变量 {name}")
            self._commit(force=True)
        else:
            self.say("该变量不在临时变量表里")

    def _begin_var_edit(self, name: str, rect) -> None:
        x, y, w, h = rect
        self._edit_target = (None, "var", name)
        self._edit_panel = True
        self._edit_rect = (x, y + 1.0, max(170.0, w), h - 3.0)
        self._edit_min_w = max(170.0, w)
        self._edit_at = self.now
        self.edit.Text = name
        self.edit.Width = self._edit_rect[2]
        self.edit.Height = self._edit_rect[3]
        Canvas.SetLeft(self.edit, self._edit_rect[0])
        Canvas.SetTop(self.edit, self._edit_rect[1])
        self.edit.Visibility = Visibility.Visible
        try:
            self.edit.Focus(FocusState.Programmatic)
            self.edit.Select(0, len(name))
        except Exception:
            pass
        self._invalidate()

    def _on_moved(self, sender, args) -> None:
        sx, sy = self._point(sender, args)
        self._mouse = (sx, sy)
        side = self._side_point(sx, sy)
        self.hover = side if side is not None else self._hit(*self._world(sx, sy))
        if self.var_drag is not None:
            drag = self.var_drag
            drag[1] = sx
            drag[2] = sy
            if sx < self._cw - 8:
                drag[3] = True
        if self.panning is not None:
            self.cam_x += sx - self.panning[0]
            self.cam_y += sy - self.panning[1]
            self.panning = (sx, sy)
        if self.side_drag is not None:
            limit = max(SIDE_MIN, self.pw * 0.62)
            self.side_w = max(SIDE_MIN, min(limit, self.pw - sx + self.side_drag))
            self._layouts.clear()
        if self.moving is not None:
            wx, wy = self._world(sx, sy)
            dx, dy = wx - self.moving[0], wy - self.moving[1]
            for node in self._selected_nodes():
                node.x += dx
                node.y += dy
            self.moving = (wx, wy)
            self._layouts.clear()
        if self.marquee is not None:
            self.marquee = (self.marquee[0], (sx, sy))
        if self.param_drag is not None:
            self._drag_param(sx, sy)
        self._invalidate()

    def _on_released(self, sender, args) -> None:
        sx, sy = self._point(sender, args)
        wx, wy = self._world(sx, sy)
        try:
            self.canvas.ReleasePointerCapture(args.Pointer)
        except Exception:
            pass
        saved = False
        if self.var_drag is not None:
            if self.var_drag[3]:
                saved = self._finish_var_drag(wx, wy) or saved
        if self.drag_wire is not None:
            self._finish_drag_wire(wx, wy)
            saved = True
        if self.marquee is not None:
            # 抬起点也算框角：没有尾随 PointerMoved 时（触屏 / 注入）少选一片
            self.marquee = (self.marquee[0], (sx, sy))
            self._apply_marquee()
        if self.param_drag is not None:
            moved = abs(sx - self.param_drag[4]) + abs(sy - self.param_drag[5])
            if moved < 3:
                self._click_param(self.param_drag[1], self.param_drag[2], self.param_drag[3])
            self.param_drag = None
            saved = True
        if self.moving is not None:
            saved = True
        self.moving = None
        self.marquee = None
        self.panning = None
        self.divider_drag = None
        self.side_drag = None
        self.var_drag = None
        if saved:
            self._commit()
        self._invalidate()

    def _on_wheel(self, sender, args) -> None:
        sx, sy = self._point(sender, args)
        delta = self._wheel_delta(args)
        if sx >= self.pw - self.side_w - 5:
            self.side_scroll = max(0.0, self.side_scroll - delta * 0.28)
        elif sx < self._cw and sy < self._canvas_bottom():
            wx, wy = self._world(sx, sy)
            self.zoom = max(0.35, min(2.3, self.zoom * (1.11 if delta > 0 else 1 / 1.11)))
            self.cam_x = sx - wx * self.zoom
            self.cam_y = sy - wy * self.zoom
            self._layouts.clear()
        args.Handled = True
        self._invalidate()

    @staticmethod
    def _wheel_delta(args) -> float:
        for getter in (lambda: args.GetCurrentPoint(None).Properties.MouseWheelDelta,
                       lambda: args.Delta):
            try:
                value = float(getter())
                if value:
                    return value
            except Exception:
                continue
        return 120.0

    def _tap_point(self, args) -> tuple[float, float]:
        try:
            pos = args.GetPosition(self.canvas)
            return float(pos.X), float(pos.Y)
        except Exception:
            return self._mouse

    def _on_right_tapped(self, sender, args) -> None:
        sx, sy = self._tap_point(args)
        self._mouse = (sx, sy)
        if self._side_point(sx, sy) is not None:
            return
        hit = self._hit(*self._world(sx, sy))
        graph = self.graph
        if hit[0] == "wire":
            graph.remove_wire(hit[1])
            self.say("已断开连线")
            self._commit()
        elif hit[0] == "header":
            graph.remove_node(hit[1])
            self.say("已删除卡片")
            self._commit()
        elif hit[0] == "param" and hit[2] == "in":
            hit[1].overrides.pop(hit[3], None)
            self.say("已恢复默认值")
            self._layouts.clear()
            self._commit()
        elif hit[0] == "field":
            spec = self.catalog.field_spec(hit[1].def_key, hit[2])
            if spec is not None:
                hit[1].params[spec["id"]] = spec["default"]
                self.say(f"已恢复「{spec['label']}」默认值")
                self._layouts.clear()
                self._commit()
        else:
            self.pending_link = None
            self._open_palette(self._overlay_of_canvas(sx, sy))
        args.Handled = True
        self._invalidate()

    def _on_double_tapped(self, sender, args) -> None:
        sx, sy = self._tap_point(args)
        hit = self._hit(*self._world(sx, sy))
        if hit[0] == "field":
            self._begin_edit(hit[1], "field", hit[2])
        elif hit[0] == "param":
            self._begin_edit(hit[1], hit[2], hit[3])
        elif hit[0] == "pin" and hit[2] == "in" and hit[3] >= 0:
            wire = self.graph.wire_into(hit[1].id, hit[3])
            if wire is not None:
                self.graph.remove_wire(wire)
                self.say("已断开该输入")
                self._commit()
        elif hit[0] == "header":
            self._begin_edit(hit[1], "alias", 0)
        elif hit[0] == "grid":
            self.pending_link = None
            self._open_palette(self._overlay_of_canvas(sx, sy))
        args.Handled = True
        self._invalidate()

    def _var_box_focused(self) -> bool:
        """焦点在某一格变量名输入框里：按键属于打字，不是画布指令。"""
        for box in self._var_boxes:
            try:
                if box.FocusState != FocusState.Unfocused:
                    return True
            except Exception:
                continue
        return False

    def _on_key_down(self, sender, args) -> None:
        if getattr(args, "Handled", False):
            return
        if self._var_box_focused():
            return               # 根节点是 PreviewKeyDown，这里吃掉键输入框就收不到了
        vk = _key_of(args)
        if self._pal_open and self._palette_keys(vk):
            args.Handled = True
            return
        if vk == VK_ESCAPE and self._edit_target is not None:
            self._cancel_edit()          # 编辑器卡住会让 Del / 框选删除全部失灵
            args.Handled = True
            return
        if self._edit_target is not None:
            return
        graph = self.graph
        if vk in (VK_DELETE, VK_BACK):
            count = self._delete_selection()
            if count:
                self.say(f"已删除 {count} 项")
                self._commit()
                self._invalidate()
            return
        if vk == VK_ESCAPE:
            self.drag_wire = None
            self.pending_link = None
            self._clear_selection()
            args.Handled = True
            self._invalidate()
            return
        if vk == VK_SPACE:
            self.space_pan = True
            return
        if _down(VK_CONTROL) and vk == VK_A:
            for node in graph.nodes:
                node.selected = True
            args.Handled = True
            self._invalidate()
            return
        if vk in (VK_LEFT, VK_RIGHT, VK_UP, VK_DOWN):
            step = 2 if _down(VK_SHIFT) else 10
            dx = step * ((vk == VK_RIGHT) - (vk == VK_LEFT))
            dy = step * ((vk == VK_DOWN) - (vk == VK_UP))
            for node in self._selected_nodes():
                node.x += dx
                node.y += dy
            self._layouts.clear()
            args.Handled = True
            self._invalidate()
            return
        if vk == VK_RETURN:
            self._commit(force=True)
            self.say("事件流已保存")
            args.Handled = True

    def root_key_down(self, args) -> None:
        """窗口根节点转发的按键：画布拿不到焦点，Del / Ctrl+A 只能这样送进来。"""
        self._on_key_down(None, args)

    def _on_key_up(self, sender, args) -> None:
        if _key_of(args) == VK_SPACE:
            self.space_pan = False

    # ------------------------------------------------------------------ 选择与移动
    def _selected_nodes(self):
        return [n for n in self.graph.nodes if n.selected]

    def _select_only(self, node, keep: bool) -> None:
        if not keep:
            for other in self.graph.nodes:
                other.selected = False
        node.selected = True

    def _clear_selection(self) -> None:
        for node in self.graph.nodes:
            node.selected = False
        self.graph.selected_wires.clear()

    def _apply_marquee(self) -> None:
        (ax, ay), (bx, by) = self.marquee
        if abs(bx - ax) < 4 and abs(by - ay) < 4:
            return
        x0, y0 = self._world(min(ax, bx), min(ay, by))
        x1, y1 = self._world(max(ax, bx), max(ay, by))
        for node in self.graph.nodes:
            lay = self.layout(node)
            if lay.x < x1 and lay.x + lay.w > x0 and lay.y < y1 and lay.y + lay.h > y0:
                node.selected = True

    def _delete_selection(self) -> int:
        graph = self.graph
        wires = [w for w in list(graph.wires) if w.id in graph.selected_wires]
        for wire in wires:
            graph.remove_wire(wire)
        nodes = [n for n in list(graph.nodes) if n.selected]
        for node in nodes:
            graph.remove_node(node)
        return len(wires) + len(nodes)

    # ------------------------------------------------------------------ 参数编辑
    def _drag_param(self, sx: float, sy: float) -> None:
        node, kind, key = self.param_drag[1], self.param_drag[2], self.param_drag[3]
        dx = sx - self.param_drag[4]
        self.param_drag[4] = sx
        if abs(dx) < 1:
            return
        if kind == "field":
            spec = self.catalog.field_spec(node.def_key, key)
            if spec is None or str(spec.get("type")) not in ("int", "float"):
                return
            value = event_flow.as_float(self._field_value(node, spec))
            node.params[spec["id"]] = self._tune(value, dx, str(spec.get("type")) == "int",
                                                 spec.get("min"), spec.get("max"))
            self._layouts.clear()
            return
        ptype, current = self._pin_value(node, kind, key)
        if ptype is None or current is None or ptype in (BOOL, STR):
            return
        value = self._tune(event_flow.as_float(current), dx, ptype == INT, None, None)
        if kind == "in":
            self._set_input_value(node, key, value)
        else:
            node.params["v"] = value
        self._layouts.clear()

    def _tune(self, value: float, dx: float, is_int: bool, minimum, maximum):
        rate = (0.02 if _down(VK_SHIFT) else 0.12) / max(self.zoom, 0.35)
        raw = value + dx * rate * (20 if is_int else 1)
        raw = round(raw) if is_int else round(raw, 3)
        if minimum is not None:
            raw = max(float(minimum), raw)
        if maximum is not None:
            raw = min(float(maximum), raw)
        return int(raw) if is_int else raw

    def _click_param(self, node, kind: str, key) -> None:
        if kind == "field":
            spec = self.catalog.field_spec(node.def_key, key)
            if spec is None:
                return
            ftype = str(spec.get("type"))
            if ftype == "bool":
                node.params[key] = not bool(self._field_value(node, spec))
            elif ftype == "enum":
                node.params[key] = self._cycle(spec, self._field_value(node, spec))
            else:
                self._begin_edit(node, "field", key)
                return
            self._layouts.clear()
            self._commit()
            self._invalidate()
            return
        ptype, value = self._pin_value(node, kind, key)
        if ptype != BOOL:
            return
        flipped = not bool(value)
        if kind == "in":
            self._set_input_value(node, key, flipped)
        else:
            node.params["v"] = flipped
        self.say(f"{self._title(node)} = {'真' if flipped else '假'}")
        self._layouts.clear()
        self._commit()
        self._invalidate()

    def _cycle(self, spec, current):
        choices = list(spec.get("choices") or [])
        if not choices:
            return current
        try:
            index = choices.index(current)
        except ValueError:
            index = -1
        return choices[(index + 1) % len(choices)]

    def _change_field(self, node, spec, text: str) -> None:
        ftype = str(spec.get("type"))
        if ftype == "int":
            try:
                node.params[spec["id"]] = int(round(float(text)))
            except ValueError:
                self.say("需要整数，已忽略")
                return
        elif ftype == "float":
            try:
                node.params[spec["id"]] = float(text)
            except ValueError:
                self.say("需要数值，已忽略")
                return
        elif ftype == "bool":
            node.params[spec["id"]] = str(text).strip().lower() in ("1", "true", "真", "on")
        elif ftype == "enum":
            choices = list(spec.get("choices") or [])
            labels = list(spec.get("labels") or choices)
            if str(text) in choices:
                node.params[spec["id"]] = str(text)
            elif text in labels:
                node.params[spec["id"]] = choices[labels.index(text)]
            else:
                self.say("选项里没有这个值，已忽略")
                return
        else:
            node.params[spec["id"]] = text.strip()
        self._layouts.clear()
        self._commit(force=True)
        self._invalidate()

    # ======================================================================
    # 卡片搜索面板（自动联想）
    # ======================================================================
    def _build_palette(self) -> None:
        self.pal_edit = W.text_box(placeholder="搜索卡片：中文 / 英文 / 拼音")
        self.pal_edit.Width = 300
        self.pal_edit.TextChanged += lambda s, e: self._refresh_palette()
        try:
            self.pal_edit.PreviewKeyDown += self._on_palette_preview_key
        except Exception:
            self.pal_edit.KeyDown += self._on_palette_preview_key

        self.pal_rows = StackPanel()
        self.pal_rows.Spacing = 2
        scroller = ScrollViewer()
        scroller.Content = self.pal_rows
        scroller.MaxHeight = 420
        scroller.MinHeight = 90
        scroller.VerticalScrollBarVisibility = ScrollBarVisibility.Auto
        scroller.HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled

        self.pal_note = W.text("", size=11, color="text3")
        body = W.stack(spacing=6)
        body.Children.Append(self.pal_edit)
        body.Children.Append(scroller)
        body.Children.Append(self.pal_note)
        self.palette = W.box(child=body, width=324, padding=Thickness(10, 10, 10, 10),
                             corner=8, background=SolidColorBrush(self._bc("menu")),
                             border=SolidColorBrush(self._bc("divider")), border_thickness=1)
        self.palette.Visibility = Visibility.Collapsed
        self.overlay.Children.Append(self.palette)

    def _overlay_of_canvas(self, sx: float, sy: float) -> tuple[float, float]:
        try:
            pos = self.canvas.TransformToVisual(self.overlay).TransformPoint(Point(0, 0))
            return float(pos.X) + sx, float(pos.Y) + sy
        except Exception:
            return 120.0, 80.0

    def _open_palette(self, point, want=None, note: str = "") -> None:
        if self.catalog is None:
            return
        self._pal_filter = want
        self._pal_note = note
        self.pal_edit.Visibility = Visibility.Visible
        self.pal_edit.Text = ""
        self.palette.Visibility = Visibility.Visible
        wide = min(max(4.0, float(self.overlay.ActualWidth or self.pw) - 330), point[0])
        tall = min(max(4.0, float(self.overlay.ActualHeight or self.ph) - 500), point[1])
        Canvas.SetLeft(self.palette, max(4.0, wide))
        Canvas.SetTop(self.palette, max(4.0, tall))
        self._pal_open = True
        self._refresh_palette()
        try:
            self.pal_edit.Focus(FocusState.Programmatic)
        except Exception:
            pass

    def _close_palette(self) -> None:
        if not self._pal_open:
            return
        self._pal_open = False
        self.palette.Visibility = Visibility.Collapsed
        self._pal_filter = None

    def _on_palette_preview_key(self, sender, args) -> None:
        if self._pal_open and self._palette_keys(_key_of(args)):
            try:
                args.Handled = True
            except Exception:
                pass

    def _palette_keys(self, vk: int) -> bool:
        rows = self._palette_rows
        step = {VK_UP: -1, VK_DOWN: 1, VK_PRIOR: -6, VK_NEXT: 6}.get(vk)
        if step is not None:
            if not rows:
                return True
            self._pal_index = max(0, min(len(rows) - 1, self._pal_index + step))
            self._paint_palette_rows()
            return True
        if vk in (VK_RETURN, VK_TAB):
            if rows:
                self._pick(min(self._pal_index, len(rows) - 1))
            return True
        if vk == VK_ESCAPE:
            self._close_palette()
            return True
        return False

    def _query(self) -> str:
        return self.pal_edit.Text or ""

    def _accepts(self, item, want) -> bool:
        kind, ptype = want
        if kind == "receive":
            if ptype == EXEC:
                return bool(item["exec_in"])
            return any(compatible(ptype, pin["type"]) for pin in item["inputs"])
        if ptype == EXEC:
            return item["entry"] or any(pin["type"] == EXEC for pin in item["outputs"])
        return any(compatible(pin["type"], ptype) for pin in item["outputs"])

    def _refresh_palette(self) -> None:
        if self.catalog is None:
            return
        graph = self.graph
        used_writes: set[str] = {
            node.def_key for node in graph.nodes
            if str(node.def_key).startswith("core.write.")
        } if graph is not None else set()
        rows = []
        for cand in self.catalog.search(self._page_key, self._query(), limit=400):
            item = cand["def"]
            if item["key"] in used_writes:
                continue    # 核心写入互斥：已使用的参数不再出现在卡片列表里
            if self._pal_filter is not None and not self._accepts(item, self._pal_filter):
                continue
            score = cand["score"] + 8 * min(self.usage.get(item["key"], 0), 6)
            rows.append((*_CAT_ORDER.get(item["cat"], (len(PALETTE_GROUPS), 0)),
                         -score, item["title"], cand))
        rows.sort(key=lambda row: (row[0], row[1], row[2], row[3]))
        self._palette_rows = [row[4] for row in rows]
        self._pal_index = 0
        self._paint_palette_rows()
        note = self._pal_note
        if not note and self._pal_filter is not None:
            ptype = self._pal_filter[1]
            note = f"仅显示可连接「{TYPE_LABELS.get(ptype, ptype)}」的卡片"
        tail = "↑↓ 选择 · Enter 创建 · 单击创建 · Esc 关闭"
        self.pal_note.Text = f"{note} · {tail}" if note else tail

    def _paint_palette_rows(self) -> None:
        host = self.pal_rows
        host.Children.Clear()
        if not self._palette_rows:
            host.Children.Append(W.text("没有匹配的卡片", size=12, color="text3",
                                        margin=Thickness(6, 4, 0, 4)))
            return
        last_group: int | None = None
        counts: dict[int, int] = {}
        for cand in self._palette_rows:
            key = _CAT_GROUP.get(cand["def"]["cat"], len(PALETTE_GROUPS))
            counts[key] = counts.get(key, 0) + 1
        for index, cand in enumerate(self._palette_rows):
            item = cand["def"]
            group = _CAT_GROUP.get(item["cat"], len(PALETTE_GROUPS))
            if group != last_group:
                last_group = group
                name = (PALETTE_GROUPS[group][0]
                        if group < len(PALETTE_GROUPS) else "其他")
                host.Children.Append(W.text(
                    f"{name} · {counts[group]}", size=10.5, color="text3",
                    margin=Thickness(8, 7, 0, 2)))
            cells = W.stack(horizontal=True, spacing=0, v="center")
            bar = W.text("▍", size=12.5)
            bar.Foreground = SolidColorBrush(self._cat_color(item["cat"]))
            cells.Children.Append(bar)
            start, length = cand.get("hl") or (-1, 0)
            title = item["title"]
            chunks = ([(title[:start], False), (title[start:start + length], True),
                       (title[start + length:], False)] if start >= 0 and length
                      else [(title, False)])
            for chunk, accent in chunks:
                if not chunk:
                    continue
                run = W.text(chunk, size=12.5, bold=W.SEMIBOLD if accent else W.NORMAL)
                run.Foreground = SolidColorBrush(self._bc("accent") if accent
                                                 else self._bc("text"))
                cells.Children.Append(run)
            tail = W.text(f"   {item['en']} · {item['cat']}", size=11, color="text3")
            tail.Foreground = SolidColorBrush(self._bc("text_faint"))
            cells.Children.Append(tail)
            selected = index == self._pal_index
            row = W.box(child=cells, padding=Thickness(8, 5, 8, 5), corner=5,
                        background=SolidColorBrush(
                            self._bc("menu_sel" if selected else
                                      ("menu_alt" if index % 2 == 0 else "menu"))))
            row.PointerPressed += lambda s, e, i=index: self._pick(i)
            host.Children.Append(row)

    def _pick(self, index: int) -> None:
        if index < 0 or index >= len(self._palette_rows):
            return
        item = self._palette_rows[index]["def"]
        self.usage[item["key"]] = self.usage.get(item["key"], 0) + 1
        point = (float(Canvas.GetLeft(self.palette) or 0.0),
                 float(Canvas.GetTop(self.palette) or 0.0))
        self._close_palette()
        wx, wy = self._world(point[0] + 90, point[1] + 30)
        link, self.pending_link = self.pending_link, None
        node = self._create_node(item["key"], wx, wy)
        if node is not None and link is not None:
            self._auto_link(link, node)

    def _add_card_pressed(self) -> None:
        self.pending_link = None
        self._open_palette(self._overlay_of_canvas(self._cw * 0.28, 12.0))

    # ======================================================================
    # 建卡 / 连线
    # ======================================================================
    def _create_node(self, def_key: str, wx: float, wy: float):
        graph = self.graph
        if graph is None:
            return None
        if str(def_key).startswith("core.write."):
            existing = next((n for n in graph.nodes if n.def_key == def_key), None)
            if existing is not None:
                self.say(f"「{self._title(existing)}」已存在：同一核心写入卡片每张画布只有一张")
                self._select_only(existing, False)
                self._layouts.clear()
                self._invalidate()
                return None
        for node in graph.nodes:
            node.selected = False
        node = graph.add_node(self.catalog, def_key, max(0.0, wx - 76), max(0.0, wy - 24))
        node.selected = True
        self.say(f"已创建卡片：{self._title(node)}（{self._def(node)['cat']}）")
        self._layouts.clear()
        self._commit()
        self._invalidate()
        return node

    def _auto_link(self, link, node) -> None:
        src, kind, index = link
        if kind == "out":
            target = self._guess_pin(src, index, "out", node)
            if target is None:
                return
            wire, why = self.graph.connect(self.catalog, src, index, node, target)
            if wire is not None:
                self._adopt_var_name(src, node)
            self.say(f"已自动连接：{self._title(src)} → {self._title(node)}"
                     if wire else why)
        else:
            source = self._guess_pin(node, index, "in", src)
            if source is None:
                return
            wire, why = self.graph.connect(self.catalog, node, source, src, index)
            if wire is not None:
                self._adopt_var_name(node, src)
            self.say(f"已自动连接：{self._title(node)} → {self._title(src)}"
                     if wire else why)

    def _guess_pin(self, src, src_index: int, kind: str, dst=None):
        """猜一个兼容引脚。kind 为 "out"：src 是连线的源头，返回 dst 的输入序号。
        kind 为 "in"：src 是需要被喂的槽位，src_index 是 dst 的输入序号，返回 src 的输出序号。"""
        graph = self.graph
        outs = src.outputs(self.catalog)
        if kind == "out":
            if not (0 <= src_index < len(outs)):
                return None
            stype = outs[src_index]["type"]
            if stype == EXEC and self._def(dst)["exec_in"]:
                return -1
            pins = dst.inputs(self.catalog)
            free = [i for i, p in enumerate(pins)
                    if compatible(stype, p["type"]) and graph.wire_into(dst.id, i) is None]
            if free:
                return free[0]
            any_ = [i for i, p in enumerate(pins) if compatible(stype, p["type"])]
            return any_[0] if any_ else None
        if dst is None:
            return None
        if src_index < 0:
            dtype = EXEC
        elif src_index < len(dst.inputs(self.catalog)):
            dtype = graph.in_type(self.catalog, dst, src_index)
        else:
            return None
        for i, p in enumerate(outs):
            if compatible(p["type"], dtype):
                return i
        return None

    def _finish_drag_wire(self, wx: float, wy: float) -> None:
        _started, node, kind, index = self.drag_wire
        self.drag_wire = None
        hit = self._hit(wx, wy)
        if hit[0] == "grid":
            ptype = (self.graph.out_type(self.catalog, node, index) if kind == "out"
                     else (EXEC if index < 0 else self.graph.in_type(self.catalog, node, index)))
            self.pending_link = (node, kind, index)
            self._open_palette(self._overlay_of_canvas(*self._mouse),
                               want=(("receive", ptype) if kind == "out" else ("feed", ptype)))
            self.say("搜索可连接的卡片，选中后自动接线")
            return
        if hit[0] == "pin":
            target, tkind, tindex = hit[1], hit[2], hit[3]
        elif hit[0] in ("header", "body"):
            target = hit[1]
            tkind = "in" if kind == "out" else "out"
            # 落在卡片身上：从输入引脚拖出时，要在被拖到的卡片上找输出引脚
            tindex = (self._guess_pin(node, index, kind, target) if kind == "out"
                      else self._guess_pin(target, index, "in", node))
            if tindex is None:
                self.say("该卡片没有兼容引脚")
                return
        else:
            self.say("未连接到任何引脚")
            return
        if tkind == kind and hit[0] == "pin":
            self.say("只能从输出引脚连到输入引脚")
            return
        if kind == "out":
            wire, why = self.graph.connect(self.catalog, node, index, target, tindex)
            if wire is not None:
                self._adopt_var_name(node, target)
        else:
            wire, why = self.graph.connect(self.catalog, target, tindex, node, index)
            if wire is not None:
                self._adopt_var_name(target, node)
        if wire is None:
            self.say(why or "无法连接")
            return
        self.pending_link = None
        self.say("已连接：" + self._wire_label(wire))

    def _adopt_var_name(self, src, dst) -> None:
        """从变量卡片连线即绑定：读/写变量卡还没填名字时，沿用源卡代表的变量名。"""
        cat = self.catalog
        if cat.definition(dst.def_key)["op"] not in ("temp_read", "temp_write"):
            return
        if str(dst.params.get("name") or "").strip():
            return
        source = cat.definition(src.def_key)
        name = ""
        if source["op"] in ("mod_read", "mod_write", "temp_read",
                            "temp_write"):
            name = str(src.params.get("name") or "").strip()
        elif source["op"] == "core_read":
            name = str(src.params.get("key") or "").strip()
        if not name:
            return
        dst.params["name"] = name
        self._layouts.clear()
        self.say(f"已绑定变量 {name}")

    def _wire_label(self, wire) -> str:
        graph = self.graph
        src, dst = graph.find(wire.src[0]), graph.find(wire.dst[0])
        if src is None or dst is None:
            return wire.id
        dst_pin = ("执行" if wire.dst[1] < 0 else
                   dst.inputs(self.catalog)[wire.dst[1]]["name"])
        return (f"{self._title(src)}.{src.outputs(self.catalog)[wire.src[1]]['name']} → "
                f"{self._title(dst)}.{dst_pin}")

    # ======================================================================
    # 内联输入框
    # ======================================================================
    def _build_inline_edit(self) -> None:
        # 隐形输入代理：只负责收字与 IME 候选窗锚点，输入框外观全部 Win2D 自绘。
        self.edit = TextBox()
        self.edit.Visibility = Visibility.Collapsed
        self.edit.Opacity = 0.0
        self.edit.KeyDown += self._on_edit_key
        self.edit.TextChanged += lambda s, e: self._invalidate()
        try:
            self.edit.SelectionChanged += lambda s, e: self._invalidate()
        except Exception:
            pass
        self.edit.LostFocus += lambda s, e: self._commit_edit()
        self.overlay.Children.Append(self.edit)

    def _set_edit_rect(self, wx: float, wy: float, ww: float, wh: float) -> None:
        sx, sy = self._screen(wx, wy)
        width = max(104.0, ww * self.zoom) + 10.0
        height = max(22.0, wh * self.zoom) + 4.0
        limit = self._edit_panel and float(self.pw) or float(self._cw)
        x = max(6.0, min(sx - 5.0, limit - width - 6.0))
        self._edit_min_w = width
        self._edit_rect = (x, sy - height / 2.0, width, height)
        self.edit.Width = width
        self.edit.Height = height
        Canvas.SetLeft(self.edit, self._edit_rect[0])
        Canvas.SetTop(self.edit, self._edit_rect[1])

    def _begin_edit(self, node, kind: str, key) -> None:
        current = ""
        self._edit_panel = False
        lay = self.layout(node)
        if kind == "alias":
            current = node.alias or self._title(node)
            self._set_edit_rect(lay.x + lay.w / 2, lay.y + HEADER_H / 2,
                                lay.w - 26, HEADER_H - 8)
            self._edit_target = (node, "alias", None)
        elif kind == "field":
            spec = self.catalog.field_spec(node.def_key, key)
            if spec is None or str(spec.get("type")) in ("bool", "enum"):
                return
            box = lay.fields.get(key)
            if box is None:
                return
            current = str(self._field_value(node, spec) or "")
            label_x, cy, chip_x, chip_w, chip_h = box
            self._set_edit_rect(chip_x + chip_w / 2, cy, chip_w, chip_h + 4)
            self._edit_target = (node, "field", spec)
        else:
            ptype, value = self._pin_value(node, kind, key)
            if ptype is None or ptype == BOOL:
                return
            box = lay.pin_box.get((kind, key))
            if box is None:
                return
            current = event_flow.value_to_text(value)
            px, py, pw, ph = box
            self._set_edit_rect(px + pw / 2, py, pw, ph + 4)
            self._edit_target = (node, kind, int(key))
        self.edit.Text = current
        self._edit_at = self.now
        self.edit.Visibility = Visibility.Visible
        try:
            self.edit.Focus(FocusState.Programmatic)
            self.edit.Select(0, len(current))
        except Exception:
            pass
        self._invalidate()

    def _fit_edit_width(self, x: float, w: float, natural: float) -> float:
        """输入框随内容变宽：不小于字段框原宽，也不越过画布右缘。"""
        limit = self._edit_panel and float(self.pw) or float(self._cw)
        return max(self._edit_min_w, min(natural, limit - 12.0 - x))

    def _paint_edit(self) -> None:
        rect = self._edit_rect
        if rect is None or self._edit_target is None:
            return
        d = self._paint
        x, y, w, h = rect
        size = 12.0 if self._edit_panel else 12.0 * max(self.zoom, 0.85)
        try:
            text = self.edit.Text or ""
            start = int(self.edit.SelectionStart)
            length = int(self.edit.SelectionLength)
        except Exception:
            return
        w = self._fit_edit_width(x, w, d.text_w(text, size) + 22.0)
        d.rrect(x, y, w, h, 4.0, self._bc("param"))
        d.rrect_stroke(x, y, w, h, 4.0, self._bc("accent"), 1.4)
        ty = y + max(1.0, (h - size * 1.4) / 2)
        d.clip(x + 1, y + 1, w - 2, h - 2)
        if length:
            pre = d.text_w(text[:start], size)
            d.rect(x + 6 + pre, y + 3, d.text_w(text[start:start + length], size),
                   h - 6, self._bc("accent", 66))
        d.text(text, x + 6, ty, size, self._bc("text"))
        if not text:
            d.text("留空即清除", x + 6, ty, size, self._bc("text_faint"))
        caret_x = x + 6 + d.text_w(text[:start], size)
        if length == 0 and (self.now - self._edit_at) % 1.0 < 0.55:
            d.line(caret_x, y + 4, caret_x, y + h - 4, self._bc("text"), 1.2)
        d.unclip()

    def _on_edit_key(self, sender, args) -> None:
        vk = _key_of(args)
        if vk in (VK_RETURN, VK_TAB):
            self._commit_edit()
            args.Handled = True
        elif vk == VK_ESCAPE:
            self._cancel_edit()
            args.Handled = True

    def _commit_edit(self) -> None:
        target, self._edit_target = self._edit_target, None
        self._edit_rect = None
        self.edit.Visibility = Visibility.Collapsed
        if target is None:
            return
        node, kind, key = target
        text = (self.edit.Text or "").strip()
        if kind == "var":
            self._commit_var_edit(str(key or ""), text)
            return
        self._edit_panel = False
        if kind == "alias":
            node.alias = text
        elif kind == "field":
            self._change_field(node, key, text)
            return
        elif kind == "in":
            ptype = node.inputs(self.catalog)[key]["type"]
            self._set_input_value(node, key, self._parse(text, ptype))
        else:
            self._set_const_value(node, text)
        self._layouts.clear()
        self._commit(force=True)
        self._invalidate()

    def _commit_var_edit(self, old: str, text: str) -> None:
        runtime = self.runtime
        if runtime is None or not text:
            self._invalidate()
            return
        if old:
            row = self._side_rows.get(old) or {"name": old}
            why = runtime.rename_var_row(row, text)
            done = f"已改名为 {text}"
        else:
            why = runtime.add_user_var(text)
            done = f"已登记变量 {text}"
        self.say(why or done)
        if not why:
            self._commit(force=True)
        self._invalidate()

    def _set_const_value(self, node, text: str) -> None:
        pins = node.outputs(self.catalog)
        ptype = pins[0]["type"] if pins else FLOAT
        node.params["v"] = self._parse(text, ptype)

    @staticmethod
    def _parse(text: str, ptype: str):
        if ptype == INT:
            try:
                return int(round(float(text)))
            except ValueError:
                return 0
        if ptype == BOOL:
            return str(text).strip().lower() in ("1", "true", "真", "on")
        if ptype == STR:
            return text
        try:
            return float(text)
        except ValueError:
            return 0.0

    def _cancel_edit(self) -> None:
        self._edit_target = None
        self._edit_panel = False
        self._edit_rect = None
        self.edit.Visibility = Visibility.Collapsed
        self._invalidate()

    # ======================================================================
    # 面板动作
    # ======================================================================
    def _action(self, act: str) -> None:
        runtime = self.runtime
        if runtime is None:
            return
        if act == "reset":
            self.zoom = 1.0
            self.cam_x, self.cam_y = 40.0, 16.0
            self._layouts.clear()
            self.say("视图已重置")
        elif act == "clear":
            if self._confirm_clear < self.now:
                self._confirm_clear = self.now + 3.0
                self.say("再次点击「再次点击确认清空」将清空这张画布")
            else:
                self._confirm_clear = 0.0
                self.graph.clear()
                self._layouts.clear()
                self._commit(force=True)
                self.say("画布已清空（右键空白处搜索卡片）")
        self._invalidate()

    _ARRANGE_ORDER = ("事件", "源", "运算", "分支", "汇")
    _ARRANGE_OPS = {
        "事件": ("driver_period", "driver_change"),
        "源": ("const", "mod_read", "core_read", "temp_read"),
        "运算": ("add", "sub", "mul", "div", "mod", "pow", "min", "max", "lerp",
                 "abs", "neg", "sqrt", "sin", "cos", "clamp", "map_range", "round",
                 "compare", "eq", "neq", "gt", "gte", "lt", "lte",
                 "and", "or", "not", "select", "formula", "free_expr",
                 "reroute", "to_int", "to_bool", "to_str"),
        "分支": ("branch", "gate", "sequence"),
        "汇": ("core_write", "mod_write", "temp_write"),
    }
    _ARRANGE_TITLES = {
        False: ("事件", "核心数值输出", "运算", "分支", "模块变量接收"),
        True: ("事件", "模块变量传出", "运算", "分支", "核心数据接收"),
    }

    def _auto_arrange(self) -> None:
        """按数据流方向分五列：驱动 → 读数 → 运算 → 分支 → 写入。
        输入页镜像后同一列序从右往左铺（驱动在最右、写入在最左），
        列间距按该列最宽卡片收紧，行间距按各卡片自身高度收紧。"""
        graph = self.graph
        if graph is None:
            return
        self._layouts.clear()
        columns: list[list] = [[] for _ in self._ARRANGE_ORDER]
        index_of = {op: index for index, name in enumerate(self._ARRANGE_ORDER)
                    for op in self._ARRANGE_OPS[name]}
        for node in graph.nodes:
            columns[index_of.get(self._def(node)["op"], 2)].append(node)
        order = range(len(columns) - 1, -1, -1) if self._mirror else range(len(columns))
        x = 40.0
        for index in order:
            nodes = sorted(columns[index], key=lambda n: (n.y, n.x))
            if not nodes:
                continue
            y = 40.0
            widest = 0.0
            for node in nodes:
                lay = self.layout(node)
                node.x = x
                node.y = y
                y += lay.h + 14.0
                widest = max(widest, lay.w)
            x += widest + 28.0
        self._layouts.clear()
        self._commit(force=True)
        titles = " · ".join(self._ARRANGE_TITLES[self._mirror])
        self.say(f"已按数据流方向整理布局：{titles}")
        self._invalidate()

    def _commit(self, force: bool = False) -> None:
        runtime = self.runtime
        if runtime is None:
            return
        stamp = time.monotonic()
        if not force and stamp - self._saved_at < 0.5:
            return
        self._saved_at = stamp
        try:
            runtime.save()
        except Exception:
            pass

    # ======================================================================
    # 页面生命周期
    # ======================================================================
    def tick(self) -> None:
        runtime = self.runtime
        graph = self.graph
        if runtime is None or graph is None:
            return
        self.pill_text.Text = "● 实时求值中"
        sig = self._signature(runtime, graph)
        if sig != self._scene_sig:
            self._scene_sig = sig
            self._layouts.clear()
            self._invalidate()
            return
        busy = (self._edit_target is not None or self.panning is not None
                or self.moving is not None or self.marquee is not None
                or self.param_drag is not None or self.drag_wire is not None)
        if not busy:
            self._refresh_values(runtime, graph)

    def _refresh_values(self, runtime, graph) -> None:
        """实时值按 4 Hz 过一遍：只有值真的变了才重绘，不让变量表每帧跳。"""
        now = self.now
        if now - self._value_at < VALUE_TICK_S:
            return
        self._value_at = now
        sig = self._value_signature(runtime, graph)
        if sig != self._value_sig:
            self._value_sig = sig
            self._invalidate()

    def _signature(self, runtime, graph) -> tuple:
        """画面结构签名：只有布局 / 内容 / 选择真的变了才重排 + 重绘。

        实时值不在这里——值每拍都在动，混进结构签名会让惰性更新失效。
        """
        hover = self.hover
        system, user = runtime.var_table()
        return (
            round(self.zoom, 4), round(self.cam_x, 2), round(self.cam_y, 2),
            round(self.side_w, 1), round(self.side_scroll, 1),
            tuple((r["name"], str(r.get("dir")), str(r.get("type")))
                  for r in system),
            tuple((r["name"], str(r.get("dir"))) for r in user),
            len(graph.nodes), len(graph.wires),
            hash(tuple(sorted(graph.selected_wires))),
            hash(tuple(sorted(n.id for n in graph.nodes if n.selected))),
            hash(tuple((n.id, repr(n.params), n.alias, n.error)
                       for n in graph.nodes)),
            hash(tuple(sorted(runtime.errors.items()))),
            (hover[0], hash(getattr(hover[1], "id", None))) if hover else None,
            self.status, self.status_until > self.now,
        )

    def _value_signature(self, runtime, graph) -> tuple:
        system, user = runtime.var_table()
        return (
            tuple(str(r.get("value")) for r in system),
            tuple(str(r.get("value")) for r in user),
            tuple((n.id, tuple(str(v) for v in n.live))
                  for n in graph.nodes if n.live),
        )

    def on_notify(self) -> None:
        self._layouts.clear()
        self._invalidate()

    def rebuild(self) -> None:
        zoom, cam_x, cam_y, side = self.zoom, self.cam_x, self.cam_y, self.side_w
        self._build_ui()
        self.zoom, self.cam_x, self.cam_y, self.side_w = zoom, cam_x, cam_y, side

    def flush_config(self) -> None:
        self._commit(force=True)

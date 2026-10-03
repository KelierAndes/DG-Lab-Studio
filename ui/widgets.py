
from __future__ import annotations

import sys

from win32more.Windows.UI.Text import FontWeight
from win32more.Microsoft.UI.Xaml import (
    CornerRadius,
    GridLength,
    GridUnitType,
    HorizontalAlignment,
    TextAlignment,
    TextTrimming,
    TextWrapping,
    Thickness,
    VerticalAlignment,
    Visibility,
)
from win32more.Microsoft.UI.Xaml.Controls import (
    AutoSuggestBox,
    Border,
    Button,
    ComboBox,
    ComboBoxItem,
    ColumnDefinition,
    FontIcon,
    Grid,
    HyperlinkButton,
    Image,
    NumberBox,
    Orientation,
    RowDefinition,
    ScrollBarVisibility,
    ScrollViewer,
    Slider,
    StackPanel,
    Symbol,
    SymbolIcon,
    TextBox,
    TextBlock,
    ToggleSwitch,
)
from win32more.Microsoft.UI.Xaml.Media import (
    Brush,
    FontFamily,
    PointCollection,
    SolidColorBrush,
    Stretch,
)
from win32more.Windows.UI import Color
from win32more.Microsoft.UI.Xaml.Shapes import Polyline
from win32more.Windows.Foundation import Point

from ui import theme
NORMAL = 400
SEMIBOLD = 600

GLYPH_CHEVRON = ""
GLYPH_CHEVRON_LEFT = ""
GLYPH_MORE = ""
GLYPH_MOON = ""
GLYPH_SUN = ""
GLYPH_CLOUD = ""
GLYPH_BLUETOOTH = ""
GLYPH_CONNECT = ""
GLYPH_PLAY = ""
GLYPH_STOP = ""
GLYPH_SEND = ""
GLYPH_RECEIVE = ""

GLYPH_CHEVRON_DOWN = ""

_VALIGN = {
    "top": VerticalAlignment.Top,
    "center": VerticalAlignment.Center,
    "bottom": VerticalAlignment.Bottom,
}
_HALIGN = {
    "left": HorizontalAlignment.Left,
    "center": HorizontalAlignment.Center,
    "right": HorizontalAlignment.Right,
    "stretch": HorizontalAlignment.Stretch,
}

def weight(value: int) -> FontWeight:
    fw = FontWeight()
    fw.Weight = value
    return fw

def star(value: float = 1) -> GridLength:
    return GridLength(value, GridUnitType.Star)

def fixed(value: float) -> GridLength:
    return GridLength(value, GridUnitType.Pixel)

def auto() -> GridLength:
    return GridLength(0, GridUnitType.Auto)

def column(*, stars: float | None = None, px: float | None = None) -> ColumnDefinition:
    col = ColumnDefinition()
    col.Width = star(stars) if stars is not None else fixed(px or 0)
    return col

def uniform(value: float) -> Thickness:
    return Thickness(value, value, value, value)

def radius(value: float) -> CornerRadius:
    return CornerRadius(value, value, value, value)

def _place(el, *, v: str | None = None, h: str | None = None):
    if v:
        el.VerticalAlignment = _VALIGN[v]
    if h:
        el.HorizontalAlignment = _HALIGN[h]
    return el

def text(
    value: str,
    *,
    size: float = 14,
    color: str = "text",
    bold: int = NORMAL,
    margin: Thickness | None = None,
    trimming: bool = False,
    wrap: bool = False,
    align: str | None = None,
    family: str | None = None,
    line_height: float | None = None,
    v: str | None = None,
    h: str | None = None,
) -> TextBlock:
    tb = TextBlock()
    tb.Text = value
    tb.FontSize = size
    tb.Foreground = theme.brush(color)
    if bold != NORMAL:
        tb.FontWeight = weight(bold)
    if margin is not None:
        tb.Margin = margin
    if trimming:
        tb.TextTrimming = TextTrimming.CharacterEllipsis
    if wrap:
        tb.TextWrapping = TextWrapping.Wrap
    if align:
        tb.TextAlignment = TextAlignment.Center
    if family:
        tb.FontFamily = FontFamily(family)
    if line_height:
        tb.LineHeight = line_height
    return _place(tb, v=v, h=h)

def stack(
    *,
    horizontal: bool = False,
    spacing: float = 0,
    margin: Thickness | None = None,
    v: str | None = None,
    h: str | None = None,
) -> StackPanel:
    sp = StackPanel()
    if horizontal:
        sp.Orientation = Orientation.Horizontal
    if spacing:
        sp.Spacing = spacing
    if margin is not None:
        sp.Margin = margin
    return _place(sp, v=v, h=h)

def box(
    *,
    width: float | None = None,
    height: float | None = None,
    background=None,
    border=None,
    border_thickness: float = 1,
    corner: float = 0,
    padding: Thickness | None = None,
    margin: Thickness | None = None,
    child=None,
    v: str | None = None,
    h: str | None = None,
) -> Border:
    b = Border()
    if width is not None:
        b.Width = width
    if height is not None:
        b.Height = height
    if background is not None:
        b.Background = background
    if border is not None:
        b.BorderBrush = border
        b.BorderThickness = uniform(border_thickness)
    if corner:
        b.CornerRadius = radius(corner)
    if padding is not None:
        b.Padding = padding
    if margin is not None:
        b.Margin = margin
    if child is not None:
        b.Child = child
    return _place(b, v=v, h=h)

def grid(*columns: GridLength) -> Grid:
    g = Grid()
    for width in columns:
        col = ColumnDefinition()
        col.Width = width
        g.ColumnDefinitions.Append(col)
    return g

def put(el, column: int):
    Grid.SetColumn(el, column)
    return el

def put_row(el, row: int):
    Grid.SetRow(el, row)
    return el

def row(height: GridLength) -> RowDefinition:
    rd = RowDefinition()
    rd.Height = height
    return rd

def rows(*heights: GridLength) -> Grid:
    g = Grid()
    for height in heights:
        rd = RowDefinition()
        rd.Height = height
        g.RowDefinitions.Append(rd)
    return g

def symbol_icon(name: str, *, size: float = 16, color: str = "text2") -> SymbolIcon:
    ic = SymbolIcon()
    ic.Symbol = getattr(Symbol, name)
    ic.FontSize = size
    ic.Foreground = theme.brush(color)
    return ic

def glyph_icon(glyph: str, *, size: float = 16, color: str = "text2") -> FontIcon:
    ic = FontIcon()
    ic.Glyph = glyph
    ic.FontSize = size
    ic.Foreground = theme.brush(color)
    return ic

def glyph_value(name: str) -> str:
    return getattr(sys.modules[__name__], "GLYPH_" + name.upper(), "")

def icon(
    *, glyph: str | None = None, symbol: str | None = None, size: float = 16, color: str = "text2"
):
    if glyph:
        return glyph_icon(glyph_value(glyph), size=size, color=color)
    return symbol_icon(symbol or "List", size=size, color=color)

def valign(value: str):
    return _VALIGN[value]

def button(content, *, width: float | None = None, height: float | None = None,
           v: str | None = None, h: str | None = None, on_click=None):
    b = Button()
    b.Content = content
    if width is not None:
        b.Width = width
    if height is not None:
        b.Height = height
    if on_click is not None:
        b.Click += on_click
    return _place(b, v=v, h=h)

def text_button(
    label: str,
    glyph_name: str | None = None,
    symbol: str | None = None,
    *,
    accent: bool = False,
    on_click=None,
):
    tone = "on_accent" if accent else "text"
    row = stack(horizontal=True, spacing=8, v="center")
    if glyph_name or symbol:
        row.Children.Append(icon(glyph=glyph_name, symbol=symbol, size=14, color=tone))
    row.Children.Append(text(label, size=13, color=tone))
    b = Button()
    b.Content = row
    if accent:
        b.Background = theme.brush("accent")
        b.BorderBrush = theme.brush("accent")
        b.Foreground = theme.brush("on_accent")
        solid_button_states(b, theme.color("accent"), theme.brush("on_accent"))
    if on_click is not None:
        b.Click += on_click
    return b


def solid_button_states(b, base_color: Color, foreground) -> None:
    over = theme.shade(base_color, 0.90)
    pressed = theme.shade(base_color, 0.80)
    for key, value in (
        ("ButtonBackgroundPointerOver", SolidColorBrush(over)),
        ("ButtonBackgroundPressed", SolidColorBrush(pressed)),
        ("ButtonBorderBrushPointerOver", SolidColorBrush(over)),
        ("ButtonBorderBrushPressed", SolidColorBrush(pressed)),
        ("ButtonForegroundPointerOver", foreground),
        ("ButtonForegroundPressed", foreground),
    ):
        try:
            b.Resources.Insert(key, value)
        except Exception:
            try:
                b.Resources[key] = value
            except Exception:
                pass


def estop_button(label: str, *, symbol: str = "Stop", on_click=None):
    white = theme.estop_foreground()
    row = stack(horizontal=True, spacing=8, v="center")
    ic = symbol_icon(symbol, size=14)
    ic.Foreground = white
    row.Children.Append(ic)
    label_tb = text(label, size=13)
    label_tb.Foreground = white
    row.Children.Append(label_tb)

    b = Button()
    b.Content = row
    b.Background = theme.estop_fill()
    b.BorderBrush = theme.estop_fill()
    b.Foreground = white
    solid_button_states(b, theme.ESTOP_RED, white)
    if on_click is not None:
        b.Click += on_click
    return b

def card(child, *, padding: float = 16, margin: Thickness | None = None, corner: float = 8):
    return box(
        background=theme.brush("card"),
        border=theme.brush("stroke"),
        corner=corner,
        padding=uniform(padding),
        margin=margin,
        child=child,
    )

def divider(*, margin: Thickness | None = None):
    return box(height=1, background=theme.brush("divider"), margin=margin)

def link(label: str, *, size: float = 12):
    h = HyperlinkButton()
    h.Content = label
    h.FontSize = size
    h.Padding = Thickness(4, 0, 4, 0)
    return h

def page_head(host: StackPanel, head: dict, *, actions=None) -> None:
    host.Children.Clear()

    crumb = stack(horizontal=True, spacing=6, margin=Thickness(0, 12, 0, 0))
    parts = head["breadcrumb"]
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        crumb.Children.Append(text(part, size=13, color="text2" if last else "text3"))
        if not last:
            crumb.Children.Append(icon(glyph="chevron", size=12, color="text3"))
    host.Children.Append(crumb)

    g = grid(star(1), auto())
    left = stack(spacing=3, v="center")
    left.Children.Append(text(head["title"], size=28, bold=SEMIBOLD))
    left.Children.Append(text(head["subtitle"], size=13, color="text3", margin=Thickness(0, 3, 0, 0)))
    g.Children.Append(put(left, 0))

    if actions:
        row = stack(horizontal=True, spacing=8, v="center")
        for action in actions:
            row.Children.Append(action)
        g.Children.Append(put(row, 1))
    host.Children.Append(g)

def dot(color: str, *, size: float = 9) -> Border:
    return box(width=size, height=size, corner=size / 2, background=theme.brush(color), v="center")

def label_row(leading, label: str, value: str, *, value_color: str = "text") -> Grid:
    widths = []
    if leading is not None:
        widths.append(fixed(17))
    widths.append(star(1))
    widths.append(auto())
    g = grid(*widths)
    index = 0
    if leading is not None:
        g.Children.Append(put(leading, index))
        index += 1
    g.Children.Append(put(text(label, size=13, color="text2"), index))
    index += 1
    g.Children.Append(put(text(value, size=13, color=value_color, h="right"), index))
    return g

def pill(label: str, fg: str, bg: str, *, dot_color: str | None = None):
    row = stack(horizontal=True, spacing=6, v="center")
    if dot_color:
        row.Children.Append(dot(dot_color, size=6))
    row.Children.Append(text(label, size=11, color=fg))
    return box(
        corner=10,
        padding=Thickness(8, 3, 8, 3),
        background=theme.brush(bg),
        child=row,
        v="center",
        h="left",
    )

def meter(percent: float, *, fg: str = "accent", width: float = 120, height: float = 6):
    ratio = max(0.0, min(1.0, percent / 100.0))
    track = box(
        width=width,
        height=height,
        corner=height / 2,
        background=theme.brush("track"),
        v="center",
    )
    fill = box(
        width=max(width * ratio, height),
        height=height,
        corner=height / 2,
        background=theme.brush(fg),
        h="left",
        v="center",
    )
    g = Grid()
    g.Children.Append(track)
    g.Children.Append(fill)
    g.Width = width
    g.Height = height
    g.VerticalAlignment = valign("center")
    return g

def field_row(label: str, description: str, control):
    g = grid(star(1), auto())
    left = stack(spacing=1, v="center")
    left.Children.Append(text(label, size=14))
    if description:
        left.Children.Append(text(description, size=12, color="text3"))
    g.Children.Append(put(left, 0))
    _place(control, v="center", h="right")
    g.Children.Append(put(control, 1))
    return box(padding=Thickness(0, 9, 0, 9), child=g)

def card_head(title: str, *, subtitle: str = "", trailing=None, glyph: str | None = None,
              symbol: str | None = None, accent: bool = False):
    g = grid(auto(), star(1), auto())
    index = 0
    if glyph or symbol:
        tone = "accent_text" if accent else "text2"
        ic = icon(glyph=glyph, symbol=symbol, size=15, color=tone)
        tile = box(
            width=28,
            height=28,
            corner=6,
            background=theme.brush("accent_soft" if accent else "track"),
            child=ic,
            v="center",
        )
        g.Children.Append(put(tile, 0))
        index = 1

    title_stack = stack(spacing=1, v="center")
    title_stack.Margin = Thickness(10 if index else 0, 0, 0, 0)
    title_stack.Children.Append(text(title, size=14, bold=SEMIBOLD))
    if subtitle:
        title_stack.Children.Append(text(subtitle, size=12, color="text3"))
    g.Children.Append(put(title_stack, index))

    if trailing is not None:
        g.Children.Append(put(_place(trailing, v="center", h="right"), 2))
    return g

def switch(is_on: bool, on_changed=None) -> ToggleSwitch:
    t = ToggleSwitch()
    t.OnContent = ""
    t.OffContent = ""
    t.IsOn = is_on
    t.MinWidth = 0
    if on_changed is not None:
        t.Toggled += on_changed
    return _place(t, v="center")

def combo(choices, *, selected: int = 0, width: float | None = None,
          on_changed=None) -> ComboBox:
    cb = ComboBox()
    for choice in choices:
        item = ComboBoxItem()
        item.Content = choice
        cb.Items.Append(item)
    cb.SelectedIndex = min(max(selected, 0), max(len(choices) - 1, 0))
    if width:
        cb.Width = width
        cb.HorizontalAlignment = _HALIGN["left"]
    else:
        cb.HorizontalAlignment = _HALIGN["stretch"]
    if on_changed is not None:
        cb.SelectionChanged += on_changed
    return cb

def dropdown(label: str, choices, *, selected: int = 0, width: float | None = None) -> StackPanel:
    cell = stack(spacing=4, h="stretch")
    cell.Children.Append(text(label, size=11, color="text3", trimming=True))
    cell.Children.Append(combo(choices, selected=selected, width=width))
    return cell

def slider(minimum: float, maximum: float, value: float, *, step: float = 1.0,
           width: float | None = None, on_change=None, on_commit=None) -> Slider:
    """数值滑块：拖动中回调 on_change，松手（或 WinUI 事件缺失时即时）回调 on_commit。"""
    s = Slider()
    s.Minimum = float(minimum)
    s.Maximum = float(maximum)
    s.Value = float(value)
    s.StepFrequency = float(step)
    s.IsSnapToTickEnabled = True
    s.HorizontalAlignment = _HALIGN["stretch"]
    if width:
        s.Width = width
    if on_change is not None:
        s.ValueChanged += lambda sender, args: on_change(float(args.NewValue))
    if on_commit is not None:
        try:
            s.DragCompleted += lambda sender, args: on_commit(float(s.Value))
        except Exception:
            # 绑定层缺少 DragCompleted 时退回即时提交
            s.ValueChanged += lambda sender, args: on_commit(float(args.NewValue))
    return s

def suggest_box(*, text: str = "", choices=None, placeholder: str = "",
                width: float | None = None, on_commit=None) -> AutoSuggestBox:
    """可任意输入、可从预定义项中选择的编辑框。

    预定义项在首次用户输入时才填充：联动页每行一个输入框，逐项 Append
    数千个联想项会让页面重建明显卡顿。
    """
    box = AutoSuggestBox()
    box.Text = text
    if placeholder:
        box.PlaceholderText = placeholder
    if width:
        box.Width = width
        box.HorizontalAlignment = _HALIGN["left"]

    state = {"filled": not choices}

    def _fill() -> None:
        if state["filled"]:
            return
        state["filled"] = True
        for choice in (choices or []):
            box.Items.Append(str(choice))

    def _collapse() -> None:
        # 联想列表只应由用户输入打开：WinUI 在持焦状态下填充 Items /
        # 重新聚焦时会自动展开列表（点击输入框误弹下拉的来源），需收起；
        # 再排一拍兜底，防框架在本处理器之后才展开
        try:
            box.IsSuggestionListOpen = False
        except Exception:
            pass
        try:
            box.DispatcherQueue.TryEnqueue(
                lambda: setattr(box, "IsSuggestionListOpen", False))
        except Exception:
            pass

    def _text_changed(sender, args) -> None:
        try:
            user_input = int(args.Reason) == 0    # AutoSuggestionBoxTextChangeReason.UserInput
        except Exception:
            user_input = True
        if user_input:
            _fill()
        if on_commit is not None:
            on_commit((sender.Text or "").strip())

    try:
        box.TextChanged += _text_changed
        box.GotFocus += lambda sender, args: _collapse()
    except Exception:
        pass
    if on_commit is not None:
        def _submitted(sender, args):
            _fill()
            on_commit((sender.Text or "").strip())

        try:
            box.QuerySubmitted += _submitted
        except Exception:
            pass
    return box

def number_box(value, minimum: float, maximum: float, *, width: float | None = None,
               on_commit=None) -> NumberBox:
    nb = NumberBox()
    nb.Minimum = float(minimum)
    nb.Maximum = float(maximum)
    nb.Value = float(value)
    nb.SmallChange = 1
    nb.LargeChange = 10
    if width is not None:
        nb.Width = width
        nb.HorizontalAlignment = _HALIGN["left"]
    else:
        nb.HorizontalAlignment = _HALIGN["stretch"]
    if on_commit is not None:
        nb.ValueChanged += lambda sender, args: on_commit(float(args.NewValue))
    return nb

def interface_area(fields, *, per_row: int = 3, spacing: float = 16):
    outer = stack(spacing=10, h="stretch")
    combos = [f for f in fields if f.kind != "switch"]
    toggles = [f for f in fields if f.kind == "switch"]

    for start in range(0, len(combos), per_row):
        chunk = combos[start : start + per_row]
        g = grid(*[star(1)] * per_row)
        g.ColumnSpacing = spacing
        for i, field in enumerate(chunk):
            g.Children.Append(
                put(dropdown(field.label, field.choices, selected=getattr(field, "selected", 0)), i)
            )
        outer.Children.Append(g)

    if toggles:
        row = stack(horizontal=True, spacing=22, v="center")
        for field in toggles:
            cell = stack(horizontal=True, spacing=8, v="center")
            cell.Children.Append(text(field.label, size=12, color="text2"))
            cell.Children.Append(switch(field.value != "False"))
            row.Children.Append(cell)
        outer.Children.Append(row)
    return outer

def panel(child, *, padding: float = 14, margin: Thickness | None = None, corner: float = 8):
    return box(
        background=theme.brush("section"),
        border=theme.brush("stroke"),
        corner=corner,
        padding=uniform(padding),
        margin=margin,
        child=child,
        h="stretch",
    )

def _finder(matrix, origin_r, origin_c, size=7):
    for r in range(size):
        for c in range(size):
            edge = r in (0, size - 1) or c in (0, size - 1)
            core = 2 <= r <= size - 3 and 2 <= c <= size - 3
            matrix[origin_r + r][origin_c + c] = edge or core

def qr_matrix(payload: str, modules: int = 21):
    import random
    from zlib import crc32

    rnd = random.Random(crc32(payload.encode("utf-8")))
    matrix = [[rnd.random() < 0.46 for _ in range(modules)] for _ in range(modules)]
    _finder(matrix, 0, 0)
    _finder(matrix, 0, modules - 7)
    _finder(matrix, modules - 7, 0)
    for i in range(8, modules - 8):
        matrix[6][i] = i % 2 == 0
        matrix[i][6] = i % 2 == 0
    return matrix

def qr(payload: str, *, cell: float = 5.0, modules: int = 21, quiet: int = 1):
    matrix = qr_matrix(payload, modules)
    side = (modules + quiet * 2) * cell
    g = Grid()

    y = quiet * cell
    for row in matrix:
        x = quiet * cell
        start = 0
        while start < modules:
            if not row[start]:
                x += cell
                start += 1
                continue
            run = start
            while run + 1 < modules and row[run + 1]:
                run += 1
            g.Children.Append(
                box(
                    width=(run - start + 1) * cell,
                    height=cell,
                    background=theme.brush("qr_fg"),
                    margin=Thickness(x, y, 0, 0),
                    h="left",
                    v="top",
                )
            )
            x += (run - start + 1) * cell
            start = run + 1
        y += cell

    return box(
        width=side,
        height=side,
        corner=6,
        background=theme.brush("qr_bg"),
        child=g,
        h="left",
        v="top",
    )

def scroll(content, *, vertical: bool = True):
    sv = ScrollViewer()
    sv.Content = content
    sv.VerticalScrollBarVisibility = (
        ScrollBarVisibility.Auto if vertical else ScrollBarVisibility.Disabled
    )
    sv.HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled
    return sv

def log_box(lines, *, title: str = "通道日志", height: float = 116, family="Consolas"):
    body = stack(spacing=3, h="stretch")
    for line in lines:
        color = {"warn": "warning", "error": "danger", "debug": "text3"}.get(line.level, "text2")
        body.Children.Append(
            text(
                f"{line.time[:12]}  {line.message}",
                size=11,
                color=color,
                family=family,
                trimming=True,
            )
        )
    head = stack(horizontal=True, spacing=8, v="center")
    head.Children.Append(text(title, size=12, bold=SEMIBOLD))
    head.Children.Append(text(f"{len(lines)} 条", size=11, color="text3", v="center"))

    inner = stack(spacing=8, h="stretch")
    inner.Children.Append(head)
    inner.Children.Append(
        box(
            height=height,
            corner=6,
            padding=Thickness(10, 8, 10, 8),
            background=theme.brush("track"),
            child=scroll(body),
            h="stretch",
        )
    )
    return inner

def collapsible(
    title: str, body, *, symbol: str | None = None, subtitle: str = "",
    trailing=None, expanded: bool = False,
):
    chevron = glyph_icon(GLYPH_CHEVRON_DOWN if expanded else GLYPH_CHEVRON, size=12, color="text3")

    left = stack(horizontal=True, spacing=8, v="center")
    if symbol:
        left.Children.Append(icon(symbol=symbol, size=14, color="text2"))
    left.Children.Append(text(title, size=12, bold=SEMIBOLD))
    if subtitle:
        left.Children.Append(text(subtitle, size=11, color="text3", v="center"))

    right = stack(horizontal=True, spacing=8, v="center")
    if trailing is not None:
        right.Children.Append(trailing)
    right.Children.Append(chevron)

    head = grid(star(1), auto())
    head.Children.Append(put(left, 0))
    head.Children.Append(put(_place(right, v="center", h="right"), 1))

    header = Button()
    header.Content = head
    header.Padding = Thickness(2, 4, 2, 4)
    header.HorizontalContentAlignment = _HALIGN["stretch"]

    holder = stack(spacing=10, h="stretch")
    holder.Children.Append(body)
    holder.Visibility = Visibility.Visible if expanded else Visibility.Collapsed

    outer = stack(spacing=6, h="stretch")
    outer.Children.Append(header)
    outer.Children.Append(holder)
    outer._collapse_holder = holder
    outer._collapse_chevron = chevron
    outer._collapse_open = expanded
    header.Click += _collapse(outer)
    return outer


def is_expanded(outer) -> bool:
    return bool(getattr(outer, "_collapse_open", False))


def set_expanded(outer, expanded: bool) -> None:
    holder = getattr(outer, "_collapse_holder", None)
    chevron = getattr(outer, "_collapse_chevron", None)
    if holder is None or chevron is None:
        return
    holder.Visibility = Visibility.Visible if expanded else Visibility.Collapsed
    chevron.Glyph = GLYPH_CHEVRON_DOWN if expanded else GLYPH_CHEVRON
    outer._collapse_open = expanded


def _collapse(outer):
    def handler(sender, args):
        set_expanded(outer, not is_expanded(outer))

    return handler


def wave_bars(lanes, *, lane_height: float = 52, gap: float = 10):
    outer = stack(spacing=gap, h="stretch")
    for lane in lanes:
        values = list(lane.samples) or [0]
        bars = grid(*[star(1)] * len(values))
        bars.Margin = Thickness(4, 4, 4, 5)
        for i, value in enumerate(values):
            height = int((lane_height - 9) * max(0, min(100, value)) / 100)
            bars.Children.Append(
                put(
                    box(
                        height=height,
                        margin=Thickness(1, 0, 1, 0),
                        background=theme.brush("wave"),
                        v="bottom",
                        h="stretch",
                    ),
                    i,
                )
            )

        cell = Grid()
        cell.Height = lane_height
        cell.Children.Append(
            box(
                height=lane_height,
                corner=3,
                background=theme.brush("lane"),
                border=theme.brush("lane_stroke"),
                h="stretch",
            )
        )
        cell.Children.Append(bars)
        cell.Children.Append(
            text(
                f"{lane.label} STR 0-100",
                size=10,
                color="text3",
                family="Consolas",
                margin=Thickness(8, 4, 0, 0),
                v="top",
                h="left",
            )
        )
        outer.Children.Append(_place(cell, h="stretch"))
    return outer

def line_chart(
    series,
    colors,
    *,
    width: float = 680,
    height: float = 210,
    ymin: float = 0.0,
    ymax: float = 60.0,
    unit: str = "kPa",
    x_left: str = "-60 s",
    x_right: str = "现在",
):
    x0, x1 = 34.0, width - 12.0
    y0, y1 = 12.0, height - 26.0

    plot = Grid()
    plot.Width = width
    plot.Height = height
    plot.Children.Append(
        box(
            width=width,
            height=height,
            corner=4,
            background=theme.brush("lane"),
            border=theme.brush("lane_stroke"),
            h="left",
            v="top",
        )
    )

    for step in range(1, 4):
        y = y0 + (y1 - y0) * step / 4
        plot.Children.Append(
            box(
                width=x1 - x0,
                height=1,
                background=theme.brush("divider"),
                margin=Thickness(x0, y, 0, 0),
                h="left",
                v="top",
            )
        )

    for label, y in ((f"{ymax:g}", y0 - 7), (f"{ymin:g}", y1 - 7), (unit, (y0 + y1) / 2 - 7)):
        plot.Children.Append(
            text(label, size=10, color="text3", family="Consolas", margin=Thickness(6, y, 0, 0), h="left", v="top")
        )
    plot.Children.Append(
        text(x_left, size=10, color="text3", family="Consolas", margin=Thickness(x0, y1 + 5, 0, 0), h="left", v="top")
    )
    plot.Children.Append(
        text(x_right, size=10, color="text3", family="Consolas", margin=Thickness(0, y1 + 5, 8, 0), h="right", v="top")
    )

    for line in series:
        points = list(line.points)
        if len(points) < 2:
            continue
        collection = PointCollection()
        for i, value in enumerate(points):
            x = x0 + (x1 - x0) * i / (len(points) - 1)
            ratio = (value - ymin) / (ymax - ymin) if ymax > ymin else 0
            collection.Append(Point(x, y1 - max(0.0, min(1.0, ratio)) * (y1 - y0)))

        polyline = Polyline()
        polyline.Points = collection
        polyline.Stroke = theme.solid(colors[line.color % len(colors)])
        polyline.StrokeThickness = 2
        plot.Children.Append(polyline)

    legend = stack(horizontal=True, spacing=16, v="center")
    for line in series:
        item = stack(horizontal=True, spacing=6, v="center")
        item.Children.Append(
            box(
                width=14,
                height=3,
                corner=1.5,
                background=theme.solid(colors[line.color % len(colors)]),
                v="center",
            )
        )
        item.Children.Append(text(line.label, size=11, color="text3"))
        legend.Children.Append(item)

    outer = stack(spacing=6, h="stretch")
    outer.Children.Append(_place(plot, h="left"))
    outer.Children.Append(legend)
    return outer

def text_box(
    *,
    header: str | None = None,
    text: str = "",
    placeholder: str = "",
    width: float | None = None,
    on_changed=None,
    read_only: bool = False,
) -> TextBox:
    tb = TextBox()
    if header:
        tb.Header = header
    tb.Text = text
    if placeholder:
        tb.PlaceholderText = placeholder
    if width is not None:
        tb.Width = width
    if read_only:
        tb.IsReadOnly = True
    if on_changed is not None:
        tb.TextChanged += on_changed
    return tb

def image(*, width: float, height: float) -> Image:
    img = Image()
    img.Width = width
    img.Height = height
    img.Stretch = Stretch.Fill
    return img

def labeled_toggle(label: str, is_on: bool = False, on_changed=None) -> StackPanel:
    row = stack(horizontal=True, spacing=10, v="center")
    row.Children.Append(text(label, size=12, color="text2", v="center"))
    toggle = switch(is_on)
    if on_changed is not None:
        toggle.Toggled += on_changed
    row.Children.Append(toggle)
    return row

def number_field(header: str, value, width: float = 120, on_changed=None) -> TextBox:
    return text_box(header=header, text=str(value), width=width, on_changed=on_changed)

def hold_border(label: str, *, width: float = 180) -> tuple[Border, TextBlock]:
    label_tb = text(label, size=13)
    b = box(
        width=width,
        corner=4,
        padding=Thickness(12, 6, 12, 6),
        background=theme.brush("accent"),
        child=label_tb,
        v="center",
        h="center",
    )
    return b, label_tb


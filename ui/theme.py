
from __future__ import annotations

from win32more.Microsoft.UI.Xaml.Media import GradientStop, LinearGradientBrush, SolidColorBrush
from win32more.Windows.Foundation import Point
from win32more.Windows.UI import Color

DARK = {
    "text": (255, 255, 255, 255),
    "text2": (200, 255, 255, 255),
    "text3": (139, 255, 255, 255),
    "card": (13, 255, 255, 255),
    "stroke": (18, 255, 255, 255),
    "divider": (20, 255, 255, 255),
    "accent": (255, 76, 194, 255),
    "accent_soft": (36, 76, 194, 255),
    "accent_text": (255, 108, 194, 255),
    "on_accent": (255, 0, 0, 0),
    "success": (255, 108, 203, 95),
    "success_soft": (36, 108, 203, 95),
    "track": (32, 255, 255, 255),
    "white": (255, 255, 255, 255),
    "legend_2": (255, 143, 127, 240),
    "legend_3": (255, 67, 207, 174),
    "warning": (255, 240, 173, 78),
    "warning_soft": (36, 240, 173, 78),
    "danger": (255, 232, 92, 108),
    "danger_soft": (36, 232, 92, 108),
    "teal": (255, 67, 207, 174),
    "teal_soft": (36, 67, 207, 174),
    "section": (20, 255, 255, 255),
    "qr_bg": (255, 255, 255, 255),
    "qr_fg": (255, 18, 18, 22),
    "wave": (255, 96, 165, 250),
    "lane": (255, 40, 40, 44),
    "lane_stroke": (255, 90, 90, 96),
}

LIGHT = {
    "text": (255, 26, 26, 26),
    "text2": (158, 0, 0, 0),
    "text3": (115, 0, 0, 0),
    "card": (184, 255, 255, 255),
    "stroke": (15, 0, 0, 0),
    "divider": (18, 0, 0, 0),
    "accent": (255, 0, 95, 184),
    "accent_soft": (26, 0, 95, 184),
    "accent_text": (255, 0, 95, 184),
    "on_accent": (255, 255, 255, 255),
    "success": (255, 15, 123, 15),
    "success_soft": (26, 15, 123, 15),
    "track": (23, 0, 0, 0),
    "white": (255, 255, 255, 255),
    "legend_2": (255, 143, 127, 240),
    "legend_3": (255, 42, 181, 149),
    "warning": (255, 157, 93, 0),
    "warning_soft": (26, 157, 93, 0),
    "danger": (255, 196, 43, 28),
    "danger_soft": (26, 196, 43, 28),
    "teal": (255, 0, 128, 110),
    "teal_soft": (26, 0, 128, 110),
    "section": (10, 0, 0, 0),
    "qr_bg": (255, 255, 255, 255),
    "qr_fg": (255, 18, 18, 22),
    "wave": (255, 59, 130, 208),
    "lane": (255, 243, 244, 246),
    "lane_stroke": (255, 204, 204, 204),
}

AVATARS = {    "blue": ("#4f7cf0", "#7a5af5"),
    "teal": ("#0ea5a5", "#22c58b"),
    "warm": ("#ef6f8f", "#f2a154"),
    "slate": ("#64748b", "#475569"),
}

_current = "dark"

def set_theme(name: str) -> None:
    global _current
    _current = "light" if name == "light" else "dark"

def name() -> str:
    return _current

def tokens() -> dict[str, tuple[int, int, int, int]]:
    return LIGHT if _current == "light" else DARK

def color(key: str) -> Color:
    a, r, g, b = tokens()[key]
    return Color(a, r, g, b)

def brush(key: str) -> SolidColorBrush:
    return SolidColorBrush(color(key))

ESTOP_RED = Color(255, 232, 17, 35)
ESTOP_TEXT = Color(255, 255, 255, 255)

def estop_fill() -> SolidColorBrush:
    return SolidColorBrush(ESTOP_RED)

def estop_foreground() -> SolidColorBrush:
    return SolidColorBrush(ESTOP_TEXT)

def _rgb(raw: str) -> Color:
    raw = raw.lstrip("#")
    return Color(255, *(int(raw[i : i + 2], 16) for i in (0, 2, 4)))

def shade(color: Color, factor: float) -> Color:
    return Color(255, int(color.R * factor), int(color.G * factor),
                 int(color.B * factor))


def solid(raw: str) -> SolidColorBrush:
    return SolidColorBrush(_rgb(raw))

def avatar_brush(key: str) -> LinearGradientBrush:
    start, end = AVATARS[key]
    lb = LinearGradientBrush()
    lb.StartPoint = Point(0, 0)
    lb.EndPoint = Point(1, 1)
    for offset, raw in ((0.0, start), (1.0, end)):
        stop = GradientStop()
        stop.Offset = offset
        stop.Color = _rgb(raw)
        lb.GradientStops.Append(stop)
    return lb

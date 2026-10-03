from __future__ import annotations

import io
import time
from collections import deque

from PIL import Image, ImageDraw

from dglab.waves import wire_to_logical_freq

_STRENGTH_LIGHT = (59, 130, 208)
_STRENGTH_DARK = (96, 165, 250)
_FREQ_LIGHT = (150, 150, 150)
_FREQ_DARK = (110, 110, 110)


def _palette(dark: bool) -> dict:
    if dark:
        return {
            "lane_bg": (40, 40, 44, 255),
            "border": (90, 90, 96, 255),
            "grid": (58, 58, 62, 255),
            "text": (200, 200, 205, 255),
            "strength": _STRENGTH_DARK,
            "freq": _FREQ_DARK,
        }
    return {
        "lane_bg": (243, 244, 246, 255),
        "border": (204, 204, 204, 255),
        "grid": (235, 235, 235, 255),
        "text": (60, 60, 60, 255),
        "strength": _STRENGTH_LIGHT,
        "freq": _FREQ_LIGHT,
    }


def _lane(draw: ImageDraw.ImageDraw, x0, x1, y0, y1, palette) -> None:
    draw.rectangle([x0, y0, x1, y1], fill=palette["lane_bg"], outline=palette["border"])
    draw.line([x0, y1, x1, y1], fill=palette["border"])


def _bars(draw: ImageDraw.ImageDraw, x0, x1, y0, y1, values: list[int],
          vmax: float, color) -> None:
    if not values:
        return
    span = max(1, x1 - x0)
    step = span / len(values)
    w = max(1, int(step * 0.8))
    for i, v in enumerate(values):
        v = max(0.0, min(vmax, float(v)))
        h = int((y1 - y0 - 2) * (v / vmax))
        if h <= 0:
            continue
        cx = int(x0 + i * step)
        draw.rectangle([cx, y1 - 1 - h, cx + w, y1 - 1], fill=color)


def _label(draw: ImageDraw.ImageDraw, x: int, y: int, text: str, palette) -> None:
    draw.text((x, y), text, fill=palette["text"])


def render_wave_live(samples: list[tuple[float, tuple, tuple]],
                     dark: bool = False) -> bytes:
    palette = _palette(dark)
    width, lane_h, gap, margin = 720, 52, 10, 4
    height = margin + 2 * (lane_h + gap) + 2
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    window = 5.0
    now = time.monotonic()
    t0 = now - window
    x0, x1 = 8, width - 8
    span = max(0.001, now - t0)

    def tx(t: float) -> int:
        return int(x0 + (t - t0) / span * (x1 - x0))

    lanes = (("A", 0), ("B", 1))
    for lane_index, (name, idx) in enumerate(lanes):
        y0 = margin + lane_index * (lane_h + gap)
        y1 = y0 + lane_h
        _lane(draw, x0, x1, y0, y1, palette)
        _label(draw, x0 + 4, y0 + 2, f"{name} STR 0-100", palette)
        for t, segs_a, segs_b in samples:
            if t < t0 or t > now:
                continue
            segs = segs_a if idx == 0 else segs_b
            bar_w = max(1, int((x1 - x0) / (window / 0.1) * 0.8))
            for i, v in enumerate(segs):
                x = tx(t) + int(i * bar_w / 4)
                v = max(0, min(100, int(v)))
                h = int((y1 - y0 - 6) * (v / 100))
                if h <= 0:
                    continue
                draw.rectangle([x, y1 - 1 - h, x + bar_w, y1 - 1],
                               fill=palette["strength"])

    buffer = io.BytesIO()
    img.save(buffer, "PNG")
    return buffer.getvalue()


PRESSURE_WINDOW_S = 60.0
PRESSURE_MIN_KPA = 0.0
PRESSURE_MAX_KPA = 60.0
PRESSURE_COLORS = [(59, 130, 208), (214, 69, 65), (72, 170, 96), (160, 90, 200)]


def render_pressure_chart(series: list[tuple[str, list[tuple[float, float]]]],
                          dark: bool = False) -> bytes:
    palette = _palette(dark)
    width, height, margin = 720, 230, 34
    now = time.monotonic()
    t0 = now - PRESSURE_WINDOW_S
    lo, hi = PRESSURE_MIN_KPA, PRESSURE_MAX_KPA

    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    x0, y0, x1, y1 = margin, 10, width - 10, height - margin
    draw.rectangle([x0, y0, x1, y1], fill=palette["lane_bg"], outline=palette["border"])
    for i in range(1, 4):
        gy = y0 + (y1 - y0) * i // 4
        draw.line([x0, gy, x1, gy], fill=palette["grid"])

    def tx(t: float) -> int:
        return int(x1 - (now - t) / PRESSURE_WINDOW_S * (x1 - x0))

    def ty(v: float) -> int:
        v = max(lo, min(hi, v))
        return int(y1 - (v - lo) / (hi - lo) * (y1 - y0))

    for index, (label, samples) in enumerate(series):
        color = PRESSURE_COLORS[index % len(PRESSURE_COLORS)]
        points = [(tx(t), ty(v)) for t, v in samples if t >= t0]
        if len(points) >= 2:
            draw.line(points, fill=color, width=2)
        _label(draw, x0 + 4 + index * 150, y1 + 6, f"{label[:18]}", palette)

    _label(draw, 2, y0, f"{hi:.0f}", palette)
    _label(draw, 2, y1 - 10, f"{lo:.0f}", palette)
    _label(draw, 2, (y0 + y1) // 2 - 5, "kPa", palette)
    _label(draw, x0, y1 + 6, "-60s", palette)
    _label(draw, x1 - 30, y1 + 6, "now", palette)

    buffer = io.BytesIO()
    img.save(buffer, "PNG")
    return buffer.getvalue()


def new_history(maxlen: int = 700) -> deque:
    return deque(maxlen=maxlen)

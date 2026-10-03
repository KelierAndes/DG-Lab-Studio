"""联动配置的轻量文本解析（原属 vision_link 模块，联动页编辑器共用）。"""

from __future__ import annotations

import re

IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def parse_name(name) -> str:
    """参数名校验：字母开头的字母/数字/下划线（映射表达式变量名规则）。"""
    out = str(name or "").strip()
    if not IDENT.match(out):
        raise ValueError(f"参数名 {out!r} 需为字母开头的字母/数字/下划线"
                         f"（映射表达式变量名规则）")
    return out


def parse_rect(rect) -> tuple[int, int, int, int]:
    """``[x, y, w, h]`` 列表或 ``"x,y,w,h"`` 文本 → 整数四元组。"""
    if isinstance(rect, str):
        parts = [p.strip() for p in rect.split(",") if p.strip()]
        if len(parts) != 4:
            raise ValueError("区域需 x,y,w,h 四个数值")
        vals = [int(float(p)) for p in parts]
    else:
        try:
            vals = [int(float(v)) for v in list(rect)]
        except (TypeError, ValueError):
            raise ValueError("区域需 x,y,w,h 四个数值") from None
        if len(vals) != 4:
            raise ValueError("区域需 x,y,w,h 四个数值")
    x, y, w, h = vals
    if w <= 0 or h <= 0:
        raise ValueError("区域宽高需 > 0")
    return (x, y, w, h)

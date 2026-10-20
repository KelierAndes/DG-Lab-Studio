"""画面识别类模块的「识别参数」表：界面只认这份序列化出来的行与字段。

参数本身归模块（`vision_link` 读 `detectors` 配置，自己逐条 normalize 出错误），
这里负责三件事：把行 + 实时值 + 模块报的回错拼成一帧给界面；把界面发来的
一次编辑校验后整包落盘并 reload；把屏幕截下来供框选区域与例图。
写入永远是整包（`JsonDict` 存盘），半包会把别的参数一起带走。
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
from typing import Any

from dglab.event_flow import value_to_text
from dglab.parsing import parse_name, parse_rect

DETECTOR_KINDS = (
    ("color", "检测颜色"),
    ("image", "检测图片"),
    ("number", "检测数值"),
    ("bar", "检测数值条"),
)

DETECTOR_DEFAULTS: dict[str, dict[str, Any]] = {
    "color": {"color": "FF0000", "tol": 40, "ratio": 0.05},
    "image": {"file": "", "thresh": 0.8},
    "number": {"fmt": "int", "thresh": 0.6, "text": ""},
    "bar": {"min": 0, "max": 100, "thresh": 0.7, "anchor": ""},
}

# type 决定控件长什么样；percent 存 0~1、显示 5~100；when 让字段只在某个选项下出现。
DETECTOR_FIELDS: dict[str, list[dict[str, Any]]] = {
    "color": [
        {"key": "color", "label": "颜色 #RRGGBB", "type": "text", "width": 96},
        {"key": "tol", "label": "容差", "type": "int", "min": 0, "max": 255, "default": 40},
        {"key": "ratio", "label": "占比", "type": "percent", "min": 1, "max": 100, "default": 5},
    ],
    "image": [
        {"key": "file", "label": "例图", "type": "file"},
        {"key": "thresh", "label": "匹配阈值", "type": "percent", "min": 5, "max": 100, "default": 80},
    ],
    "number": [
        {"key": "fmt", "label": "输出类型", "type": "choice",
         "options": [["int", "整数"], ["float", "小数"], ["text", "文字"]]},
        {"key": "text", "label": "期望文本", "type": "text", "width": 110, "when": ["fmt", "text"]},
        {"key": "thresh", "label": "匹配阈值", "type": "percent", "min": 5, "max": 100, "default": 60},
    ],
    "bar": [
        {"key": "min", "label": "0% 位置(x/y)", "type": "int", "min": -99999, "max": 99999, "default": 0},
        {"key": "max", "label": "100% 位置(x/y)", "type": "int", "min": -99999, "max": 99999, "default": 100},
        {"key": "anchor", "label": "锚点例图", "type": "file"},
        {"key": "thresh", "label": "匹配阈值", "type": "percent", "min": 5, "max": 100, "default": 70},
    ],
}

# 截一次屏留给「截区域 / 截例图」用：框选发生在同一帧画面上，重新抓会错位。
_last_shot: dict[str, Any] = {"image": None, "path": ""}


def _supported(engine: Any, module_id: str) -> bool:
    meta = engine.modules.meta(module_id) or {}
    return bool(meta.get("realtime_manager"))


def _rows(cfg: dict) -> list[dict]:
    return [r for r in (cfg.get("detectors") or []) if isinstance(r, dict)]


def _field(kind: str, key: str) -> dict | None:
    return next((f for f in DETECTOR_FIELDS.get(kind, []) if f["key"] == key), None)


def _coerce(kind: str, key: str, value: Any) -> Any:
    """按字段类型把界面传来的显示值换算回存储值。"""
    field = _field(kind, key)
    spec = field or {}
    if value is None:
        return ""
    if spec.get("type") == "percent":
        try:
            return round(float(value) / 100.0, 4)
        except (TypeError, ValueError):
            return 0.05
    if spec.get("type") == "int":
        try:
            return int(round(float(value)))
        except (TypeError, ValueError):
            return int(spec.get("default", 0))
    return str(value).strip()


def _errors(engine: Any, module_id: str) -> dict[str, str]:
    inst = engine.modules.instance(module_id)
    bridge = getattr(inst, "bridge", None) if inst is not None else None
    out: dict[str, str] = {}
    for det in getattr(bridge, "detectors", None) or []:
        if getattr(det, "error", ""):
            out[str(det.name)] = str(det.error)
    return out


def _signals(engine: Any, module_id: str) -> dict[str, Any]:
    """识别参数的实时值在模块自己的映射表里，不在核心变量表里。"""
    try:
        return dict((engine.flow.module_signals() or {}).get(module_id) or {})
    except Exception:
        return {}


def state(engine: Any, params: dict) -> dict:
    module_id = str(params.get("module_id") or "")
    if not _supported(engine, module_id):
        return {"module_id": module_id, "supported": False, "rows": []}
    cfg = engine.modules.settings_for(module_id)
    errors = _errors(engine, module_id)
    signals = _signals(engine, module_id)
    rows = []
    for index, entry in enumerate(_rows(cfg)):
        name = str(entry.get("name") or "")
        kind = str(entry.get("kind") or "color")
        rect = entry.get("rect")
        values: dict[str, Any] = {}
        for field in DETECTOR_FIELDS.get(kind, []):
            key = str(field["key"])
            raw = entry.get(key, field.get("default"))
            if field["type"] == "percent":
                try:
                    values[key] = round(float(raw) * 100.0, 2)
                except (TypeError, ValueError):
                    values[key] = float(field.get("default", 50))
            else:
                values[key] = raw
        live = signals.get(name)
        rows.append({
            "index": index,
            "name": name,
            "kind": kind,
            "rect": list(rect) if isinstance(rect, (list, tuple)) and len(rect) == 4 else None,
            "values": values,
            "error": errors.get(name, ""),
            "live": value_to_text(live),
        })
    return {
        "module_id": module_id,
        "supported": True,
        "rows": rows,
        "kinds": [{"key": k, "label": label} for k, label in DETECTOR_KINDS],
        "fields": {k: list(v) for k, v in DETECTOR_FIELDS.items()},
        "defaults": {k: dict(v) for k, v in DETECTOR_DEFAULTS.items()},
    }


async def _commit(engine: Any, module_id: str, rows: list[dict]) -> dict:
    cfg = engine.modules.settings_for(module_id)
    cfg["detectors"] = rows
    cfg.save()
    try:
        await engine.modules.reload(module_id)
    except Exception as exc:
        engine._log(f"模块 {module_id} 重载失败：{exc!r}")
    return state(engine, {"module_id": module_id})


def _free_name(rows: list[dict]) -> str:
    used = {str(r.get("name") or "") for r in rows}
    n = 1
    while f"param{n}" in used:
        n += 1
    return f"param{n}"


async def add(engine: Any, params: dict) -> dict:
    module_id = str(params.get("module_id") or "")
    if not _supported(engine, module_id):
        raise ValueError(f"{module_id} 不是画面识别模块")
    kind = str(params.get("kind") or "color")
    if kind not in dict(DETECTOR_KINDS):
        raise ValueError(f"未知检测类型 {kind!r}")
    rows = [dict(r) for r in _rows(engine.modules.settings_for(module_id))]
    entry = {"name": _free_name(rows), "kind": kind, "rect": None}
    entry.update(DETECTOR_DEFAULTS[kind])
    rows.append(entry)
    return await _commit(engine, module_id, rows)


async def remove(engine: Any, params: dict) -> dict:
    module_id = str(params.get("module_id") or "")
    rows = [dict(r) for r in _rows(engine.modules.settings_for(module_id))]
    index = int(params.get("index", -1))
    if not 0 <= index < len(rows):
        raise ValueError("识别参数序号无效")
    del rows[index]
    return await _commit(engine, module_id, rows)


async def update(engine: Any, params: dict) -> dict:
    module_id = str(params.get("module_id") or "")
    if not _supported(engine, module_id):
        raise ValueError(f"{module_id} 不是画面识别模块")
    rows = [dict(r) for r in _rows(engine.modules.settings_for(module_id))]
    index = int(params.get("index", -1))
    if not 0 <= index < len(rows):
        raise ValueError("识别参数序号无效")
    entry = rows[index]
    patch = params.get("patch") or {}

    if "name" in patch:
        text = str(patch.get("name") or "").strip()
        if text and text != str(entry.get("name") or ""):
            entry["name"] = parse_name(text)
            if sum(1 for r in rows if str(r.get("name") or "") == entry["name"]) > 1:
                raise ValueError(f"参数名 {entry['name']!r} 已经有一个了")
    if "kind" in patch:
        kind = str(patch.get("kind") or "")
        if kind not in dict(DETECTOR_KINDS):
            raise ValueError(f"未知检测类型 {kind!r}")
        if kind != entry.get("kind"):
            entry["kind"] = kind
            for key, value in DETECTOR_DEFAULTS[kind].items():
                entry.setdefault(key, value)
    if "rect" in patch:
        raw = patch.get("rect")
        if raw in (None, "", []):
            entry["rect"] = None
        else:
            rect = list(parse_rect(raw))
            entry["rect"] = rect
            if params.get("from_pick") and str(entry.get("kind")) == "bar":
                bx, by, bw, bh = rect
                if bw >= bh:
                    entry["min"], entry["max"] = bx, bx + bw
                else:
                    entry["min"], entry["max"] = by, by + bh
    for key, value in (patch.get("values") or {}).items():
        entry[str(key)] = _coerce(str(entry.get("kind") or "color"), str(key), value)
    return await _commit(engine, module_id, rows)


def _grab():
    from PIL import ImageGrab

    return ImageGrab.grab(all_screens=True)


async def shot(engine: Any, params: dict) -> dict:
    """整屏抓一帧存成临时 PNG，路径给界面显示；坐标即模块检测用的图像像素。"""
    try:
        img = await asyncio.to_thread(_grab)
    except Exception as exc:
        raise ValueError(f"屏幕截图失败：{exc!r}") from None
    path = os.path.join(tempfile.gettempdir(), f"dgstudio-shot-{int(time.time() * 1000)}.png")
    await asyncio.to_thread(img.save, path)
    _last_shot["image"] = img
    _last_shot["path"] = path
    return {"path": path.replace("\\", "/"), "width": img.width, "height": img.height}


async def crop(engine: Any, params: dict) -> dict:
    """把截好的那帧按框选区域存进模块模板目录，并写回该参数的文件名。"""
    module_id = str(params.get("module_id") or "")
    key = str(params.get("key") or "")
    rect = list(parse_rect(params.get("rect")))
    rows = [dict(r) for r in _rows(engine.modules.settings_for(module_id))]
    index = int(params.get("index", -1))
    if not 0 <= index < len(rows):
        raise ValueError("识别参数序号无效")
    entry = rows[index]
    img = _last_shot["image"]
    if img is None:
        raise ValueError("还没有截图，先点一次截区域")
    x, y, w, h = rect
    box = (max(0, x), max(0, y), min(img.width, x + w), min(img.height, y + h))
    if box[2] - box[0] < 2 or box[3] - box[1] < 2:
        raise ValueError("框选区域太小")
    inst = engine.modules.instance(module_id)
    make_dir = getattr(inst, "template_dir", None) if inst is not None else None
    if make_dir is None:
        raise ValueError("该模块没有模板目录")
    directory = str(make_dir())
    os.makedirs(directory, exist_ok=True)
    fname = f"{entry.get('name') or 'param'}_{key}.png"
    await asyncio.to_thread(img.crop(box).save, os.path.join(directory, fname))
    entry[key] = fname
    if str(entry.get("kind") or "") == "bar" and key == "anchor":
        # 数值条整条框选时，0% / 100% 取区域两端，省得再手填。
        bx, by, bw, bh = rect
        if bw >= bh:
            entry["min"], entry["max"] = bx, bx + bw
        else:
            entry["min"], entry["max"] = by, by + bh
    return await _commit(engine, module_id, rows)


DETECTOR_METHODS: dict[str, Any] = {
    "detectors.state": state,
    "detectors.add": add,
    "detectors.remove": remove,
    "detectors.update": update,
    "detectors.shot": shot,
    "detectors.crop": crop,
}

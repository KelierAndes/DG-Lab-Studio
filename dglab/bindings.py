"""负鼠按键映射配置文件的存储层：config/bindings/ 下每份配置一个 JSON。

旧版把整套配置塞在主 config.json 的 ble.ovc_profiles 里，配置一多主文件就
臃肿，模块也没法自带默认配置。现在文件夹里每份配置独立成文件，当前配置记在
index.json，引擎的 ble 段不再保存任何映射配置。
"""

from __future__ import annotations

import json
import os
import re
import threading
import time

__all__ = ["bindings_dir", "load_binding_profiles", "save_binding_profiles",
           "OVC_BUTTON_BITS", "BUTTON_ACTIONS", "KEY_BINDING_ACTION",
           "button_actions", "button_action_labels"]

_INDEX_FILE = "index.json"
_FILENAME_BAD = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def bindings_dir(base_dir: str | None = None) -> str:
    root = base_dir or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "config", "bindings")


def _profile_filename(name: str) -> str:
    stem = _FILENAME_BAD.sub("_", str(name or "").strip()).strip(" .")
    return (stem or "profile") + ".json"


def _read_json(target: str):
    try:
        with open(target, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return None


def _write_json(target: str, payload) -> bool:
    os.makedirs(os.path.dirname(target), exist_ok=True)
    # 临时文件带线程号：两个线程同时存同一份配置时不会互相踩掉 .tmp。
    tmp = f"{target}.{threading.get_ident()}.tmp"
    # Windows 上刚写的文件会被杀软 / 索引器短暂独占，os.replace 偶发拒绝访问，
    # 重试几次即可；一次失败就报「保存失败」会让用户以为按钮坏了。
    for attempt in range(5):
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
            os.replace(tmp, target)
            return True
        except OSError:
            if not os.path.exists(tmp):
                continue
            time.sleep(0.05 * (attempt + 1))
    try:
        os.remove(tmp)
    except OSError:
        pass
    return False


def load_binding_profiles(directory: str | None = None
                          ) -> tuple[str, dict[str, dict[str, str]]]:
    """读取按键映射配置，返回 (当前配置名, {配置名: {按键位: 绑定}})。"""
    directory = directory or bindings_dir()
    profiles: dict[str, dict[str, str]] = {}
    if os.path.isdir(directory):
        for entry in sorted(os.listdir(directory)):
            if not entry.endswith(".json") or entry == _INDEX_FILE:
                continue
            data = _read_json(os.path.join(directory, entry))
            if not isinstance(data, dict):
                continue
            name = str(data.get("name") or entry[:-len(".json")]).strip()
            rows = data.get("bindings") if isinstance(data.get("bindings"), dict) \
                else {k: v for k, v in data.items()
                      if isinstance(k, str) and isinstance(v, str)}
            if name:
                # 空映射也认：「默认」可以是零绑定，注入非默认映射时它兜住 active
                profiles[name] = {str(bit): str(binding)
                                  for bit, binding in (rows or {}).items()}
    if not profiles:
        return "默认", {}
    index = _read_json(os.path.join(directory, _INDEX_FILE))
    if isinstance(index, dict):
        # index 里记着上次保存的配置顺序：下拉列表不因文件名排序而跳动
        order = [str(n) for n in index.get("order") or []]
        ordered = {name: profiles.pop(name) for name in order if name in profiles}
        profiles = {**ordered, **profiles}
    active = str(index.get("active") or "") if isinstance(index, dict) else ""
    if active not in profiles:
        active = next(iter(profiles), "默认")
    return active, profiles


def save_binding_profiles(active: str, profiles: dict[str, dict[str, str]],
                          directory: str | None = None) -> bool:
    """整包落盘：每份配置一个文件，删掉已不存在的配置文件（改名 / 删除配置）。"""
    directory = directory or bindings_dir()
    os.makedirs(directory, exist_ok=True)
    ok = True
    kept: set[str] = set()
    for name, rows in profiles.items():
        target = os.path.join(directory, _profile_filename(name))
        kept.add(os.path.normcase(os.path.basename(target)))
        ok = _write_json(target, {"version": 1, "name": str(name),
                                  "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                                  "bindings": {str(bit): str(binding)
                                               for bit, binding in (rows or {}).items()}
                                  }) and ok
    for entry in os.listdir(directory):
        if (entry.endswith(".json") and entry != _INDEX_FILE
                and os.path.normcase(entry) not in kept):
            try:
                os.remove(os.path.join(directory, entry))
            except OSError:
                ok = False
    if active not in profiles:
        active = next(iter(profiles), "默认")
    ok = _write_json(os.path.join(directory, _INDEX_FILE),
                     {"version": 1, "active": str(active),
                      "order": list(profiles)}) and ok
    return ok


# ------------------------------------------------------- 按键与动作目录
#
# 这些是设备语义（负鼠的 11 个按键位、内置动作），旧界面把它们写在 ui/live.py 里，
# 桥也要用，所以搬到引擎这一层；界面只负责画。

OVC_BUTTON_BITS = [
    (0, "SEL_1"), (1, "SEL_2"), (2, "HOME"),
    (8, "Up"), (9, "Down"), (10, "Left"), (11, "Right"),
    (12, "B"), (13, "A"), (14, "G"), (15, "D"),
]

BUTTON_ACTIONS = [
    ("none", "无"),
    ("a_strength_up", "A 通道强度 +10"), ("a_strength_down", "A 通道强度 -10"),
    ("a_strength_zero", "A 通道强度 归0"),
    ("a_wave_up", "A 切换上一个波形"), ("a_wave_down", "A 切换下一个波形"),
    ("b_strength_up", "B 通道强度 +10"), ("b_strength_down", "B 通道强度 -10"),
    ("b_strength_zero", "B 通道强度 归0"),
    ("b_wave_up", "B 切换上一个波形"), ("b_wave_down", "B 切换下一个波形"),
    ("fire", "持续开火 (按住，双通道)"),
    ("fire_a", "持续开火 A (按住，仅 A 通道)"),
    ("fire_b", "持续开火 B (按住，仅 B 通道)"),
    ("estop", "急停"),
]

KEY_BINDING_ACTION = ("key", "模拟键盘按键…")


def button_actions(engine=None) -> list[tuple[str, str]]:
    """内置动作 + 键盘注入 + 模块注册的动作（模块没起来就只有前两类）。"""
    items = list(BUTTON_ACTIONS)
    items.append(KEY_BINDING_ACTION)
    try:
        for action in engine.modules.button_actions():
            items.append((action.key, action.label))
    except Exception:
        pass
    return items


def button_action_labels(engine=None) -> dict[str, str]:
    return dict(button_actions(engine))

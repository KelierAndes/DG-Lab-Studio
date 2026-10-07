from __future__ import annotations

import ctypes
import logging

log = logging.getLogger(__name__)

KEY_NAMES: dict[int, str] = {
    0x08: "Backspace", 0x09: "Tab", 0x0D: "Enter",
    0x13: "Pause", 0x14: "CapsLock", 0x1B: "Escape", 0x20: "Space",
    0x21: "PageUp", 0x22: "PageDown", 0x23: "End", 0x24: "Home",
    0x25: "Left", 0x26: "Up", 0x27: "Right", 0x28: "Down",
    0x2C: "PrintScreen", 0x2D: "Insert", 0x2E: "Delete",
    0x5B: "LWin", 0x5C: "RWin",
    0x60: "Num0", 0x61: "Num1", 0x62: "Num2", 0x63: "Num3", 0x64: "Num4",
    0x65: "Num5", 0x66: "Num6", 0x67: "Num7", 0x68: "Num8", 0x69: "Num9",
    0x6A: "NumMultiply", 0x6B: "NumAdd", 0x6D: "NumSubtract",
    0x6E: "NumDecimal", 0x6F: "NumDivide",
    0x90: "NumLock", 0x91: "ScrollLock",
    0xBA: "Semicolon", 0xBB: "Equals", 0xBC: "Comma", 0xBD: "Minus",
    0xBE: "Period", 0xBF: "Slash", 0xC0: "Tilde",
    0xDB: "LBracket", 0xDC: "Backslash", 0xDD: "RBracket", 0xDE: "Quote",
}
KEY_NAMES.update({0x30 + i: str(i) for i in range(10)})
KEY_NAMES.update({0x41 + i: chr(ord("A") + i) for i in range(26)})
KEY_NAMES.update({0x70 + i: f"F{i + 1}" for i in range(24)})
KEY_NAMES[0x10] = "Shift"
KEY_NAMES[0x11] = "Ctrl"
KEY_NAMES[0x12] = "Alt"

NAME_TO_VK = {name.upper(): vk for vk, name in KEY_NAMES.items()}

_EXTENDED = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28,
             0x2C, 0x2D, 0x2E, 0x5B, 0x5C, 0x6F}

_KEYEVENTF_EXTENDEDKEY = 0x0001
_KEYEVENTF_KEYUP = 0x0002
_INPUT_KEYBOARD = 1


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = (("wVk", ctypes.c_ushort),
                ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.c_void_p))


class _INPUTUNION(ctypes.Union):
    _fields_ = (("ki", _KEYBDINPUT),
                ("padding", ctypes.c_ubyte * 32))


class _INPUT(ctypes.Structure):
    _fields_ = (("type", ctypes.c_ulong),
                ("union", _INPUTUNION))


def key_name(vk: int) -> str:
    return KEY_NAMES.get(vk, f"VK{vk:02X}")


def vk_from_name(name: str) -> int | None:
    token = name.strip().upper()
    if not token:
        return None
    if token in NAME_TO_VK:
        return NAME_TO_VK[token]
    if token.startswith("VK"):
        try:
            return int(token[2:], 16)
        except ValueError:
            return None
    return None


def _send(vk: int, down: bool) -> bool:
    if not 0x01 <= vk <= 0xFE:
        return False
    flags = 0
    if vk in _EXTENDED:
        flags |= _KEYEVENTF_EXTENDEDKEY
    if not down:
        flags |= _KEYEVENTF_KEYUP
    scan = ctypes.windll.user32.MapVirtualKeyW(vk, 0)
    inp = _INPUT()
    inp.type = _INPUT_KEYBOARD
    inp.union.ki = _KEYBDINPUT(vk, scan, flags, 0, None)
    try:
        sent = ctypes.windll.user32.SendInput(
            1, ctypes.byref(inp), ctypes.sizeof(_INPUT))
    except Exception as exc:
        log.warning("键盘注入失败 vk=%#x: %r", vk, exc)
        return False
    if not sent:
        log.warning("键盘注入被拒绝 vk=%#x", vk)
    return bool(sent)


def foreground_window() -> str:
    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if not hwnd:
            return ""
        buf = ctypes.create_unicode_buffer(256)
        n = ctypes.windll.user32.GetWindowTextW(hwnd, buf, 256)
        return buf.value if n > 0 else ""
    except Exception:
        return ""


def press(name: str) -> bool:
    vk = vk_from_name(name)
    if vk is None:
        log.warning("未知按键名: %r", name)
        return False
    return _send(vk, down=True)


def release(name: str) -> bool:
    vk = vk_from_name(name)
    if vk is None:
        return False
    return _send(vk, down=False)

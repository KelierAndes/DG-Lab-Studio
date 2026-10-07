

from __future__ import annotations

import ctypes
from ctypes import wintypes

from win32more.Microsoft.UI.Xaml.Controls import (
    ContentDialog,
    ContentDialogButton,
    ContentDialogResult,
)

from ui import widgets as W


class _OPENFILENAMEW(ctypes.Structure):
    _fields_ = [
        ("lStructSize", wintypes.DWORD),
        ("hwndOwner", wintypes.HWND),
        ("hInstance", wintypes.HINSTANCE),
        ("lpstrFilter", wintypes.LPWSTR),
        ("lpstrCustomFilter", wintypes.LPWSTR),
        ("nMaxCustFilter", wintypes.DWORD),
        ("nFilterIndex", wintypes.DWORD),
        ("lpstrFile", wintypes.LPWSTR),
        ("nMaxFile", wintypes.DWORD),
        ("lpstrFileTitle", wintypes.LPWSTR),
        ("nMaxFileTitle", wintypes.DWORD),
        ("lpstrInitialDir", wintypes.LPWSTR),
        ("lpstrTitle", wintypes.LPWSTR),
        ("Flags", wintypes.DWORD),
        ("nFileOffset", wintypes.WORD),
        ("nFileExtension", wintypes.WORD),
        ("lpstrDefExt", wintypes.LPWSTR),
        ("lCustData", wintypes.LPARAM),
        ("lpfnHook", ctypes.c_void_p),
        ("lpTemplateName", wintypes.LPWSTR),
        ("pvReserved", ctypes.c_void_p),
        ("dwReserved", wintypes.DWORD),
        ("FlagsEx", wintypes.DWORD),
    ]


_OFN_EXPLORER = 0x00080000
_OFN_PATHMUSTEXIST = 0x00000008
_OFN_FILEMUSTEXIST = 0x00001000
_OFN_OVERWRITEPROMPT = 0x00000002
_OFN_HIDEREADONLY = 0x00000004

_JSON_FILTER = "JSON 文件 (*.json)\0*.json\0所有文件 (*.*)\0*.*\0"


def _run_file_dialog(save: bool, title: str, *, wildcard: str = _JSON_FILTER,
                     initial_dir: str | None = None,
                     default_name: str = "") -> str | None:
    buffer = ctypes.create_unicode_buffer(max(len(default_name) + 1, 1024))
    for i, ch in enumerate(default_name):
        buffer[i] = ch
    ofn = _OPENFILENAMEW()
    ofn.lStructSize = ctypes.sizeof(ofn)
    ofn.lpstrFilter = wildcard
    ofn.lpstrFile = buffer
    ofn.nMaxFile = len(buffer)
    ofn.lpstrInitialDir = initial_dir
    ofn.lpstrTitle = title
    ofn.lpstrDefExt = "json"
    ofn.Flags = (_OFN_EXPLORER | _OFN_HIDEREADONLY | _OFN_PATHMUSTEXIST |
                 (_OFN_OVERWRITEPROMPT if save else _OFN_FILEMUSTEXIST))
    func = ctypes.windll.comdlg32.GetSaveFileNameW if save else ctypes.windll.comdlg32.GetOpenFileNameW
    if func(ctypes.byref(ofn)):
        path = buffer.value
        return path or None
    return None


def pick_open_path(title: str = "选择文件", **kwargs) -> str | None:
    return _run_file_dialog(False, title, **kwargs)


def pick_save_path(title: str = "另存为", *, default_name: str = "config.json",
                   **kwargs) -> str | None:
    return _run_file_dialog(True, title, default_name=default_name, **kwargs)


def _make_dialog(shell, *, title: str, content, primary: str | None,
                 close: str) -> ContentDialog:
    _dismiss_open_popups(shell)
    dialog = ContentDialog()
    dialog.Title = title
    dialog.Content = content
    if primary:
        dialog.PrimaryButtonText = primary
        dialog.DefaultButton = ContentDialogButton.Primary
    dialog.CloseButtonText = close
    dialog.XamlRoot = shell.RootGrid.XamlRoot
    return dialog


def _dismiss_open_popups(shell) -> None:
    try:
        root = shell.RootGrid
        xaml_root = root.XamlRoot
        if xaml_root is not None:
            try:
                from win32more.Microsoft.UI.Xaml.Media import VisualTreeHelper
                popups = VisualTreeHelper.GetOpenPopupsForXamlRoot(xaml_root)
                for i in range(popups.Size):
                    popups.GetAt(i).IsOpen = False
            except AttributeError:
                _close_dropdowns_in_tree(root)
    except Exception:
        pass


def _close_dropdowns_in_tree(element) -> None:
    from win32more.Microsoft.UI.Xaml.Media import VisualTreeHelper

    try:
        count = VisualTreeHelper.GetChildrenCount(element)
    except Exception:
        return
    for i in range(count):
        child = VisualTreeHelper.GetChild(element, i)
        try:
            if child.IsDropDownOpen:
                child.IsDropDownOpen = False
        except Exception:
            pass
        _close_dropdowns_in_tree(child)


async def confirm_dialog(shell, title: str, message: str, *,
                         primary: str = "确定", close: str = "取消") -> bool:
    panel = W.stack(spacing=12)
    panel.Children.Append(W.text(message, size=13, wrap=True))
    dialog = _make_dialog(shell, title=title, content=panel,
                          primary=primary, close=close)
    result = await dialog.ShowAsync()
    return result == ContentDialogResult.Primary


async def prompt_text(shell, title: str, message: str, *, initial: str = "",
                      primary: str = "确定", close: str = "取消") -> str | None:
    box = W.text_box(text=initial, width=280)
    panel = W.stack(spacing=12)
    panel.Children.Append(W.text(message, size=13, wrap=True))
    panel.Children.Append(box)
    dialog = _make_dialog(shell, title=title, content=panel,
                          primary=primary, close=close)
    result = await dialog.ShowAsync()
    if result == ContentDialogResult.Primary:
        return (box.Text or "").strip()
    return None

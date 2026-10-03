
from __future__ import annotations

from win32more.Microsoft.UI.Xaml.Controls import HyperlinkButton

from ui import widgets as W

_shell = None

def bind(shell) -> None:
    global _shell
    _shell = shell

def goto(tag: str) -> None:
    if _shell is not None:
        _shell.goto(tag)

def link(label: str, tag: str, *, size: float = 12) -> HyperlinkButton:
    b = W.link(label, size=size)
    b.Click += _handler(tag)
    return b

def _handler(tag: str):
    def wrapped(sender, routed_args):
        goto(tag)

    return wrapped

import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    XAML_DIR = Path(sys._MEIPASS) / "xaml"
else:
    XAML_DIR = Path(__file__).resolve().parent.parent / "xaml"

def xaml(name: str) -> Path:
    return XAML_DIR / name

import ctypes
import os
import struct
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

OUT = os.path.join(ROOT, "_tools", "_preview")
os.makedirs(OUT, exist_ok=True)


class _Cfg(dict):
    def save(self):
        pass


class _FakeModules:

    def __init__(self):
        self.running = False
        self._temps: dict[str, dict] = {}
        from dglab.mapping import MappingEngine
        self._engine = MappingEngine(lambda k, v: None)
        self.spec = {
            "device": {"type": "choice", "label": "目标设备",
                       "choices": ["A", "B"], "default": "A",
                       "desc": "选择联动的输出设备"},
            "threshold": {"type": "float", "label": "触发阈值",
                          "min": 0, "max": 100, "step": 0.5, "default": 40,
                          "desc": "超过阈值时触发输出"},
        }
        self.cfg = _Cfg({
            "threshold": 55.5,
            "temps": [{"name": "beat", "expr": "{beat}+1"}],
            "events": [
                {"name": "节拍开火", "trigger": "period", "arg": 100,
                 "actions": [{"dir": "in", "param": "in_fire_a",
                              "var": "beat"}]},
                {"name": "压力边沿", "trigger": "if", "arg": "{loud} > 40",
                 "actions": [{"dir": "in", "param": "in_strength_a",
                              "var": "loud"},
                             {"dir": "out", "param": "OVC.Pressure",
                              "var": "edge"}]},
                {"name": "响度变化", "trigger": "change", "arg": "loud",
                 "actions": []},
            ]})

    def list_modules(self):
        return [{"id": "preview_mod", "name": "预览模块", "version": "9.9.9",
                 "description": "事件流预览用假模块",
                 "enabled": True, "config": True,
                 "loaded": False, "running": self.running}]

    def meta(self, module_id):
        return {"id": "preview_mod", "name": "预览模块", "version": "9.9.9",
                "params": {"loudness": {"label": "响度"}},
                "running": self.running}

    def is_enabled(self, module_id):
        return True

    def config_spec_for(self, module_id):
        return self.spec

    def settings_for(self, module_id):
        return self.cfg

    def temp_specs_for(self, module_id):
        return []

    def temps_space(self, module_id):
        return self._temps.setdefault(module_id, {})

    def instance(self, module_id):
        from types import SimpleNamespace
        return SimpleNamespace(bridge=SimpleNamespace(engine=self._engine))


def grab(hwnd, path):
    from PIL import Image
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    rc = ctypes.wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rc))
    w, h = rc.right - rc.left, rc.bottom - rc.top
    hdc = user32.GetDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bmp)
    user32.PrintWindow(hwnd, mem, 2)
    bmi = ctypes.c_buffer(40)
    struct.pack_into("<IiiHHIIiiII", bmi, 0, 40, w, -h, 1, 32, 0,
                     w * h * 4, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, bmi, 0)
    img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1)
    img.convert("RGB").save(path)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hdc)


def main():
    import ui.widgets as W
    from ui.shell import App
    from win32more.winui3 import XamlApplication

    made = []
    _orig = W.collapsible

    def spy(*a, **k):
        el = _orig(*a, **k)
        made.append(el)
        return el

    W.collapsible = spy

    def navigate():
        win = App.window
        fake = _FakeModules()
        fake.running = True
        win.engine.modules = fake
        win.goto("link")
        win._page("link").tick()
        fake._engine.signal("loud", 55)
        fake._engine.signal("beat", 3)
        threading.Thread(target=shot_stopped, daemon=True).start()

    def shot_stopped():
        time.sleep(1.4)
        hwnd = ctypes.windll.user32.FindWindowW(None, "DGStudio")
        grab(hwnd, os.path.join(OUT, "link_running.png"))
        print("event stream captured, collapsibles:", len(made))
        App.window.ui_queue.put(close)

    def close():
        try:
            App.window.Close()
        except Exception:
            pass

    def watcher() -> None:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if App.window is not None:
                break
            time.sleep(0.2)
        else:
            print("window never appeared")
            os._exit(1)
        time.sleep(2.0)
        App.window.ui_queue.put(navigate)

    threading.Thread(target=watcher, daemon=True).start()
    XamlApplication.Start(App)


if __name__ == "__main__":
    main()

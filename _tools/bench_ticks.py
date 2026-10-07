import ctypes
import os
import statistics
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)


class _Cfg(dict):
    def save(self):
        pass


class _FakeModules:
    def __init__(self):
        self.running = True
        self._temps: dict[str, dict] = {}
        from dglab.mapping import MappingEngine
        self._engine = MappingEngine(lambda k, v: None)
        for i in range(12):
            self._engine.signal(f"param{i}", float(i))
        self._engine.set_temp_rows(
            [{"name": f"t{i}", "expr": f"{{t{i}}}+1"} for i in range(6)])
        self._engine.set_event_cards([
            {"name": "拍", "trigger": "period", "arg": 50,
             "actions": [{"dir": "out", "param": "COYOTE.Battery",
                          "var": "bat", "name": "Battery", "type": "Int"}]}])

    def list_modules(self):
        return [{"id": "bench", "name": "基准模块", "version": "1.0",
                 "description": "性能测量", "enabled": True, "config": True,
                 "loaded": True, "running": self.running}]

    def meta(self, module_id):
        return {"id": "bench", "name": "基准模块", "version": "1.0",
                "params": {f"param{i}": {"label": f"参数{i}"}
                           for i in range(12)},
                "running": self.running}

    def is_enabled(self, module_id):
        return True

    def config_spec_for(self, module_id):
        return {"threshold": {"type": "float", "label": "阈值",
                              "min": 0, "max": 100, "default": 40}}

    def settings_for(self, module_id):
        return _Cfg({"threshold": 55.5, "temps": [], "events": []})

    def temp_specs_for(self, module_id):
        return []

    def temps_space(self, module_id):
        return self._temps.setdefault(module_id, {})

    def instance(self, module_id):
        from types import SimpleNamespace
        return SimpleNamespace(bridge=SimpleNamespace(engine=self._engine))


class _Slot:
    def __init__(self, sid):
        self.type = "COYOTE_030"
        self.name = f"设备{sid}"
        self.strength = {"A": 80, "B": 40}
        self.strength_limit = {"A": 200, "B": 200}
        self.battery = 77
        self.is_output_device = True


class _State:
    backend = "ble"
    slots = {f"AA{i:02d}": _Slot(f"AA{i:02d}") for i in range(2)}
    paired = True
    status_text = "已连接"
    qr_text = ""
    client_id = "local"
    target_id = "peer"


def bench(label, fn, repeat):
    times = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t0) * 1000)
    print(f"  {label:34s} 平均 {statistics.mean(times):6.2f} ms  "
          f"最大 {max(times):6.2f} ms  ×{repeat}")
    return statistics.mean(times)


def probe():
    from ui.shell import App
    try:
        _run_bench()
    except Exception:
        import traceback
        with open("_bench_err.txt", "w", encoding="utf-8") as f:
            f.write(traceback.format_exc())
    finally:
        try:
            App.window.Close()
        except Exception:
            pass


def _run_bench():
    from ui.shell import App
    win = App.window
    fake = _FakeModules()
    win.engine.modules = fake

    def run(tag, page):
        print(f"[{tag}]")
        bench("rebuild(整页重建)", lambda: page.rebuild(), 20)
        if hasattr(page, "_last"):
            def force_tick():
                page._last = 0.0
                page.tick()
            bench("tick(节流到期,含刷新)", force_tick, 30)
        if hasattr(page, "_update_status"):
            bench("connect._update_status+pairing",
                  lambda: (page._update_status(), page._update_pairing()), 100)

    for tag in ("dashboard", "connect", "control", "link"):
        win.goto(tag)
        page = win._page(tag)
        page.tick()
        run(tag, page)
    print("done")
    App.window.ui_queue.put(lambda: App.window.Close())


def main():
    from ui.shell import App
    from win32more.winui3 import XamlApplication

    def watcher():
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if App.window is not None:
                break
            time.sleep(0.2)
        else:
            print("window never appeared")
            os._exit(1)
        time.sleep(1.5)
        App.window.ui_queue.put(probe)

    threading.Thread(target=watcher, daemon=True).start()
    XamlApplication.Start(App)


if __name__ == "__main__":
    main()

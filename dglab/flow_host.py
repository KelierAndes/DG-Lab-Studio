"""事件流全局宿主：常驻节拍、核心/模块读写桥、卡片目录动态刷新。"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from typing import Any

from dglab import event_flow
from dglab.event_flow import Catalog, FlowRuntime, TICK_INTERVAL
from dglab.params import (build_dispatchers, core_inputs, device_state_values)
from dglab.state import family_of
from dglab.waves import wave_order

REFRESH_INTERVAL_S = 1.0


def _base_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class DeviceApi:
    """把 Engine 的方法整成 build_dispatchers 需要的调用形状。"""

    def __init__(self, engine):
        self._engine = engine

    def resolve_slot(self, family: str = "") -> str | None:
        return self._engine.resolve_slot(None, str(family or "") or None,
                                         output_only=True)

    def slot_family(self, slot_id: str | None) -> str:
        slot = self._engine.get_state().slots.get(slot_id)
        return family_of(slot.type) if slot is not None else ""

    def wave_order(self, family: str = "") -> list[str]:
        return wave_order(str(family or "") or "COYOTE")

    def wave_selection(self) -> dict:
        return self._engine.wave_selection()

    def set_strength(self, channel: str, value, slot_id: str | None = None):
        return self._engine.set_strength(channel, int(value), slot_id=slot_id)

    def set_wave(self, channel: str, name, slot_id: str | None = None):
        return self._engine.set_wave(channel, str(name), slot_id=slot_id)

    def fire_start(self, slot_id: str | None = None, channel: str | None = None):
        return self._engine.fire_start(slot_id=slot_id, channel=channel)

    def fire_stop(self, slot_id: str | None = None, channel: str | None = None):
        return self._engine.fire_stop(slot_id=slot_id, channel=channel)

    def emergency_stop(self):
        return self._engine.emergency_stop()

    def push_pulse(self, channel: str, value, level=100,
                   slot_id: str | None = None):
        return self._engine.push_pulse_stream(int(value), channel=channel,
                                              level=int(level or 100),
                                              slot_id=slot_id)

    def run(self, coro) -> None:
        try:
            future = self._engine.submit(coro)
        except Exception:
            coro.close()
            return
        future.add_done_callback(_drop_cancelled)


def _drop_cancelled(future) -> None:
    if future.cancelled():
        return
    future.exception()


class FlowHost:

    def __init__(self, engine):
        self.engine = engine
        self.catalog = Catalog()
        self.api = DeviceApi(engine)
        self.dispatchers = build_dispatchers(self.api, core_inputs())
        main_dir = os.path.dirname(os.path.abspath(
            getattr(engine.config, "path", os.path.join(_base_dir(), "config"))))
        self.runtime = FlowRuntime(
            catalog=self.catalog,
            read_core=self.core_values,
            read_modules=self.module_signals,
            write_core=self.write_core,
            write_module=self.write_module,
            rename_module=self.rename_module_var,
            temps=engine.modules.temps_space(""),
            base_dir=main_dir,
            log=engine._log,
        )
        self._task = None
        self._signature: tuple | None = None
        self._note_seen: set[str] = set()

    # ---------------------------------------------------------------- 值来源
    def core_values(self) -> dict[str, float]:
        state = self.engine.get_state()
        vals = device_state_values(state)
        vals["Action"] = float(getattr(state, "last_action", None) or 0)
        return vals

    def module_signals(self) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        modules = self.engine.modules
        for mid in list(getattr(modules, "_instances", {})):
            eng = modules._mapping_engine(mid)
            signals = getattr(eng, "signals", None)
            if signals:
                out[mid] = dict(signals)
        return out

    # ---------------------------------------------------------------- 写入端
    def write_core(self, key: str, value: int) -> None:
        if self.engine.backend_kind == "none":
            return
        runner = self.dispatchers.get(str(key))
        if runner is None:
            return
        try:
            runner(int(value))
        except Exception as exc:
            self._note(f"事件流写入核心参数 {key} 失败: {exc!r}")

    def write_module(self, module_id: str, name: str, value) -> None:
        try:
            self.engine.modules.set_temp(module_id, str(name), value)
        except Exception as exc:
            self._note(f"事件流写入模块参数 {module_id}.{name} 失败: {exc!r}")

    def rename_module_var(self, module_id: str, old: str, new: str) -> str:
        """变量表里改模块登记的可改名参数：转交模块改它自己的地址，返回错误说明。"""
        inst = self.engine.modules.instance(str(module_id or ""))
        hook = getattr(inst, "rename_var", None)
        if not callable(hook):
            return "该模块登记的参数不支持改名"
        try:
            note = str(hook(str(old or ""), str(new or "")) or "")
        except Exception as exc:
            return f"改名失败：{exc!r}"
        if not note:
            self.refresh(force=True)     # 变量表标签立刻跟上，不等下一次签名比对
        return note

    def modules_changed(self) -> None:
        """模块启用 / 卸载后立刻调用：变量表与卡片列表不能等到下一次签名比对才刷新。"""
        self.refresh(force=True)

    def purge_missing_cards(self) -> int:
        """清掉画布上查不到定义的失效卡片。

        模块只是没启用时保留它的卡片（重新启用就恢复）；已取消的卡片层
        （变量对象卡）与模块已卸载的卡片直接删掉，别在画布上留一地「失效卡片」。
        """
        dropped = 0
        for graphs in self.runtime.profiles.values():
            for graph in graphs.values():
                for node in list(graph.nodes):
                    key = str(node.def_key or "")
                    if self.catalog.known(key):
                        continue
                    mid = _card_module(key)
                    if mid and not self.engine.modules.is_enabled(mid):
                        continue
                    graph.remove_node(node)
                    dropped += 1
        if dropped:
            self.runtime.save()
            self.engine._log(f"事件流：已清理 {dropped} 张失效卡片")
        return dropped

    def _note(self, text: str) -> None:
        if text in self._note_seen:
            return
        self._note_seen.add(text)
        self.engine._log(text)

    def reset(self) -> None:
        self.runtime.reset()
        self._note_seen.clear()

    # -------------------------------------------------------------- 配置文件
    def missing_modules(self, name: str = "") -> list[str]:
        """该配置引用到、但当前未启用的模块 id。"""
        graphs = self.runtime.profiles.get(name or self.runtime.active) or {}
        return [mid for mid in event_flow.profile_modules(graphs)
                if not self.engine.modules.is_enabled(mid)]

    def switch_profile(self, name: str) -> bool:
        if not self.runtime.switch_profile(name):
            return False
        self.runtime.save()
        self.refresh(force=True)
        return True

    # ---------------------------------------------------------------- 目录
    def _catalog_modules(self) -> list[dict[str, Any]]:
        modules = self.engine.modules
        rows: list[dict[str, Any]] = []
        for meta in modules.list_modules():
            mid = str(meta.get("id") or "")
            if not mid or not modules.is_enabled(mid):
                continue
            inst = modules.instance(mid)
            if inst is None:
                continue
            params: list = []
            for hook in ("link_params", "read_params"):
                try:
                    params.extend(getattr(inst, hook)() or [])
                except Exception:
                    pass
            try:
                params.extend(modules.temp_specs_for(mid))
            except Exception:
                pass
            rows.append({"id": mid, "name": str(meta.get("name") or mid),
                         "params": params})
        return rows

    def _device_count(self) -> int:
        counters: dict[str, int] = {}
        for slot in self.engine.get_state().slots.values():
            fam = str(family_of(slot.type))
            counters[fam] = counters.get(fam, 0) + 1
        return max(counters.values()) if counters else 1

    def refresh(self, force: bool = False) -> bool:
        rows = self._catalog_modules()
        count = self._device_count()
        self.runtime.declared_vars = _declared_vars(rows)
        user_vars = self.runtime.user_vars
        signature = (tuple(sorted(
            (str(r["id"]), str(r["name"]),
             tuple(sorted(str(n) for n in _pool_names(r))))
            for r in rows)), count,
            tuple(sorted(str(row.get("name") or "") for row in user_vars)))
        if not force and signature == self._signature:
            return False
        self._signature = signature
        self.catalog.refresh(rows, count, user_vars)
        return True

    # ---------------------------------------------------------------- 常驻节拍
    def start(self) -> None:
        if self._task is not None:
            return
        self.runtime.load()
        self.refresh(force=True)
        self.purge_missing_cards()
        loop = self.engine.loop
        if loop is None:
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        self._task = (loop.create_task(self._beat_loop())
                      if running is loop
                      else self.engine.submit(self._beat_loop()))

    def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()

    async def _beat_loop(self) -> None:
        next_refresh = 0.0
        try:
            while True:
                await asyncio.sleep(TICK_INTERVAL)
                now = time.monotonic()
                if now >= next_refresh:
                    next_refresh = now + REFRESH_INTERVAL_S
                    self.refresh()
                try:
                    self.runtime.tick(now)
                except Exception as exc:
                    self._note(f"事件流运行异常: {exc!r}")
        except asyncio.CancelledError:
            pass


def _card_module(key: str) -> str:
    """卡片 def key 里的模块 id（mod.read.<模块>.<名>）；取消的卡片层返回空。"""
    parts = str(key or "").split(".")
    if parts[:2] == ["mod", "obj"] or parts[:1] == ["var"] and "obj" in parts[1:2]:
        return ""
    if len(parts) > 3 and parts[0] == "mod" and parts[1] in ("read", "write"):
        return parts[2]
    return ""


def _declared_vars(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    """模块登记的实时参数 → 变量表行（名字 / 标签 / 方向 / 类型 / 来源）。

    同名参数被多个模块登记时先到先得，但**可改名行优先占位**：否则 OSC 的
    头像地址会被别的模块同名只读行顶掉，整行落进「系统参数 · 不可改名」栏。
    """
    seen: dict[str, dict[str, str]] = {}
    for row in rows:
        mid = str(row.get("id") or "")
        mname = str(row.get("name") or mid)
        for item in event_flow.module_pool(row):
            name = item["name"]
            entry = {"name": name, "label": item.get("label") or name,
                     "dir": item.get("dir") or "in",
                     "type": item.get("type") or "",
                     "renamable": bool(item.get("renamable")),
                     "mid": mid, "source": f"{mname}（{mid}）"}
            old = seen.get(name)
            if old is not None and (old["renamable"] or not entry["renamable"]):
                continue
            seen[name] = entry
    return list(seen.values())


def _pool_names(row: dict[str, Any]) -> list[str]:
    out = []
    for entry in row.get("params") or []:
        if isinstance(entry, dict):
            out.append(str(entry.get("name") or entry.get("key") or ""))
        elif isinstance(entry, (list, tuple)) and entry:
            out.append(str(entry[0]))
    return [n for n in out if n]


def _legacy_rows(engine) -> list[tuple[str, list, list]]:
    rows = []
    for meta in engine.modules.list_modules():
        mid = str(meta.get("id") or "")
        if not mid:
            continue
        try:
            cfg = engine.modules.settings_for(mid)
        except Exception:
            continue
        events = [r for r in (cfg.get("events") or []) if isinstance(r, dict)]
        temps = [r for r in (cfg.get("temps") or []) if isinstance(r, dict)]
        if events or temps:
            rows.append((mid, events, temps))
    return rows


def migrate_module_flows(host: FlowHost) -> int:
    """把各模块遗留的事件卡 / 临时变量表搬进事件流，并清掉旧配置项。"""
    engine = host.engine
    rows = _legacy_rows(engine)
    if not rows:
        return 0
    host.refresh(force=True)
    count = event_flow.migrate_legacy(host.catalog, host.runtime.graphs, rows)
    registered = 0
    for mid, _events, temps in rows:
        for row in temps:
            if row.get("expr") or row.get("auto"):
                continue        # 公式行已转成卡片，模块自登记行由模块维护
            if host.runtime.add_user_var(row.get("name") or "") == "":
                registered += 1
    for mid, _events, _temps in rows:
        try:
            cfg = engine.modules.settings_for(mid)
            cfg.pop("events", None)
            cfg.pop("temps", None)
            cfg.save()
        except Exception:
            continue
    if count or registered:
        host.runtime.save()
        host.runtime.save_user_vars()
    if count:
        engine._log(f"事件流：已把 {count} 张遗留联动卡片迁移到画布")
    if registered:
        engine._log(f"事件流：已把 {registered} 个遗留临时变量登记进变量表")
    return count

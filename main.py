from __future__ import annotations

import faulthandler
import json
import os
import sys
import tempfile
import threading
import time
import traceback

try:
    faulthandler.open(os.path.join(tempfile.gettempdir(),
                                   "dgstudio_fault.log"), all_threads=True)
except Exception:
    pass

if getattr(sys, "frozen", False):
    _exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    if _exe_dir not in sys.path:
        sys.path.insert(0, _exe_dir)

from win32more.winui3 import XamlApplication

from ui.shell import App

CRASH_LOG = os.path.join(tempfile.gettempdir(), "dgstudio_crash.log")
REPORT = {}


def _log_crash(context: str) -> None:
    try:
        with open(CRASH_LOG, "a", encoding="utf-8") as f:
            f.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] {context}\n")
            f.write(traceback.format_exc())
    except OSError:
        pass


class _FakePoint:
    def __init__(self, x, y):
        self.X, self.Y = x, y


class _FakeProps:
    IsLeftButtonPressed = True
    IsRightButtonPressed = False
    IsMiddleButtonPressed = False


class _FakeTapArgs:
    """桩 TappedEventArgs：只提供 _tap_point 读取的 GetPosition。"""

    Handled = False

    def __init__(self, x, y):
        self._point = _FakePoint(x, y)

    def GetPosition(self, _relative_to):
        return self._point


class _FakePointerArgs:
    """桩 PointerEventArgs：只提供画布交互真正读到的那几个成员。"""

    Handled = False
    Pointer = None

    def __init__(self, x, y):
        self.Position = _FakePoint(x, y)
        self.Properties = _FakeProps()

    def GetCurrentPoint(self, _relative_to):
        return self


class _StubPainter:
    """自检里没有真实绘制会话：桩画刷让面板走完整绘制路径。"""

    def text_w(self, text, size, bold=False) -> float:
        return len(str(text)) * float(size) * 0.6

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


def _drag_probe(flow, node, lay) -> bool:
    """自检：按下 → 拖动 → 抬起，验证卡片真的跟随指针位移。"""
    canvas = flow.canvas
    stubbed = {}
    for name in ("CapturePointer", "ReleasePointerCapture", "Focus"):
        original = getattr(canvas, name, None)
        if original is None:
            continue
        try:
            setattr(canvas, name, lambda *_a: True)
            stubbed[name] = original
        except Exception:
            pass
    sx, sy = flow._screen(lay.x + 40, lay.y + 10)
    before = (node.x, node.y)
    try:
        flow._on_pressed(canvas, _FakePointerArgs(sx, sy))
        flow._on_moved(canvas, _FakePointerArgs(sx + 60, sy + 40))
        flow._on_released(canvas, _FakePointerArgs(sx + 60, sy + 40))
    except Exception as exc:
        REPORT["drag_error"] = repr(exc)
        return False
    finally:
        for name, original in stubbed.items():
            try:
                setattr(canvas, name, original)
            except Exception:
                pass
    return (abs(node.x - before[0] - 60) < 1.5 and abs(node.y - before[1] - 40) < 1.5)


class _FakeKeyArgs:
    """桩 KeyEventArgs：按键自检只需要虚拟键码。"""

    Handled = False

    def __init__(self, vk: int):
        self.Key = vk


def _delete_probe(flow, graph) -> bool:
    """自检：空白处按下 → 拖动框选 → 抬起 → Del，整条链路真的批量删掉。

    合成鼠标注入只送按下 / 抬起、不送移动（画布收不到 PointerMoved），
    所以框选必须用桩事件自证，不能靠外部拖拽截图判断。
    """
    first = flow._create_node("math.add", 60.0, 5000.0)
    second = flow._create_node("math.sub", 60.0, 5090.0)
    if first is None or second is None:
        return False
    first.x, first.y = 60.0, 5000.0
    second.x, second.y = 60.0, 5090.0
    flow._layouts.clear()
    canvas = flow.canvas
    stubbed = {}
    for name in ("CapturePointer", "ReleasePointerCapture", "Focus"):
        original = getattr(canvas, name, None)
        if original is None:
            continue
        try:
            setattr(canvas, name, lambda *_a: True)
            stubbed[name] = original
        except Exception:
            pass
    try:
        flow._clear_selection()
        x0, y0 = flow._screen(20.0, 4970.0)
        x1, y1 = flow._screen(320.0, 5180.0)
        flow._on_pressed(canvas, _FakePointerArgs(x0, y0))
        flow._on_moved(canvas, _FakePointerArgs((x0 + x1) / 2, (y0 + y1) / 2))
        flow._on_moved(canvas, _FakePointerArgs(x1, y1))
        flow._on_released(canvas, _FakePointerArgs(x1, y1))
        picked = len(flow._selected_nodes())
        flow.root_key_down(_FakeKeyArgs(46))
        return (picked >= 2 and first not in graph.nodes
                and second not in graph.nodes)
    finally:
        for name, original in stubbed.items():
            try:
                setattr(canvas, name, original)
            except Exception:
                pass
        for node in (first, second):
            if node in graph.nodes:
                graph.remove_node(node)


def _var_ui_probe(flow, runtime) -> tuple[bool, str]:
    """自检：变量表的删 / 读写切换命中框可点，名称格是叠加输入框且不与拖动重合。"""
    if runtime.add_user_var("ProbeUI") != "":
        return False, "add failed"
    saved_paint = flow._paint
    saved_scroll = flow.side_scroll
    flow._paint = _StubPainter()
    try:
        # 新变量排在最后：滚到底才有名称输入框贴上去
        flow.side_scroll = 1e9
        flow.side_hits = []
        flow._paint_side(runtime)
        row = next((b for b in flow.side_hits if b[4] == "var-row"
                    and b[5] == "ProbeUI"), None)
        flip = next((b for b in flow.side_hits if b[4] == "var-dir"
                     and b[5] == "ProbeUI"), None)
        named = [b for b in flow.side_hits if b[4] == "var-name"]
        if row is None or flip is None or named:
            return False, f"hits={[b[4] for b in flow.side_hits][:8]}"
        box = _probe_var_box(flow, "ProbeUI")
        if box is None:
            return False, f"no box for row, rects={flow._var_rects}"
        index = flow._var_box_index("ProbeUI")
        if index < 0 or flow._var_boxes[index] is not box:
            return False, "box index lookup broken"
        boxed = (box.Text or "") == "ProbeUI"
        before = runtime.user_var_dir("ProbeUI")
        flow._side_pressed(flow._side_point(flip[0] + 3, flip[1] + 3), None)
        moved = runtime.user_var_dir("ProbeUI")
        # 名称格按下去只可能是拖动建卡，绝不会再弹改名框
        flow._side_pressed(flow._side_point(row[0] + 3, row[1] + 3), None)
        drag_only = flow.var_drag is not None
        flow.var_drag = None
        if not boxed:
            return False, "box text empty"
        box.Text = "ProbeUIRen"
        # 画布的 PreviewKeyDown 隧道会先于输入框看到按键：焦点在框里时
        # 它必须放行，否则回车 / Esc 永远到不了改名提交（上一轮打包版实测即此症）
        from win32more.Microsoft.UI.Xaml import FocusState
        try:
            box.Focus(FocusState.Programmatic)
            focused = box.FocusState != FocusState.Unfocused
        except Exception:
            focused = False
        if focused:
            key_args = _FakeKeyArgs(13)
            flow._on_key_down(None, key_args)
            if getattr(key_args, "Handled", False):
                return False, "canvas swallowed the key while the box had focus"
        # 走真实按键路径：XAML 事件闭包调的就是 _on_var_box_key(行号, 事件)
        flow._on_var_box_key(index, _FakeKeyArgs(13))
        if not any(r["name"] == "ProbeUIRen" for r in runtime.user_vars):
            return False, "rename via box failed"
        flow.side_scroll = 1e9
        flow.side_hits = []
        flow._paint_side(runtime)
        dele = next((b for b in flow.side_hits if b[4] == "var-del"
                     and b[5] == "ProbeUIRen"), None)
        if dele is None:
            return False, "no del hit"
        flow._side_pressed(flow._side_point(dele[0] + 3, dele[1] + 3), None)
        gone = not any(r["name"] == "ProbeUIRen" for r in runtime.user_vars)
        tagged = bool(_ef_tag({"source": "VRChat OSC 联动（osc_bridge）"}))
        plain = not _ef_tag({"source": "临时变量"})
        # 窄面板下控件不能被藏起来：先压缩名字，读写与删除始终保留
        runtime.add_user_var("AVeryLongVariableNameForNarrowPanelTest")
        wide = flow.side_w
        flow.side_w = 200.0
        try:
            flow.side_scroll = 1e9
            flow.side_hits = []
            flow._paint_side(runtime)
            tight = [b[4] for b in flow.side_hits
                     if b[5] == "AVeryLongVariableNameForNarrowPanelTest"]
        finally:
            flow.side_w = wide
            runtime.remove_user_var("AVeryLongVariableNameForNarrowPanelTest")
        ok = (moved != before and gone and tagged and plain
              and drag_only and not named
              and "var-dir" in tight and "var-del" in tight)
        note = "" if ok else (f"moved={moved != before} gone={gone} "
                              f"tag={tagged and plain} drag={drag_only} "
                              f"tight={tight}")
        return ok, note
    finally:
        flow._paint = saved_paint
        flow.side_scroll = saved_scroll
        runtime.remove_user_var("ProbeUI")
        runtime.remove_user_var("ProbeUIRen")


def _probe_var_box(flow, name: str):
    """找到贴在某个变量名上的输入框（变量表改名就靠它）。"""
    return flow._var_box_for(name)


def _osc_row_probe(runtime, engine) -> tuple[bool, str]:
    """自检：OSC 真机登记行全部落在可改名栏，方向一律按数据流判定。

    走的是模块自己的 link_params / temp_specs + 核心的合并口径，不是手搓
    夹具——上一轮就是夹具绿、真机反。模块的第三方依赖装在它自己的 _deps 目录，
    宿主加载时才挂进 sys.path，自检直接 import 要先补上这一段。
    """
    import sys

    from dglab.flow_host import _declared_vars
    from dglab.state import EngineState, Slot

    mods = getattr(engine, "modules", None)
    folder = mods.module_dir("osc_bridge") if mods is not None else None
    if not folder:
        return True, "osc_bridge 未安装：本项跳过"
    deps = str(mods.module_deps_dir("osc_bridge") or "")
    added = bool(deps) and deps not in sys.path
    if added:
        sys.path.append(deps)
    try:
        from modules.osc_bridge.plugin import OscModule
    except Exception as exc:
        if added:
            sys.path.remove(deps)
        return False, f"import failed: {exc!r}"
    if added:
        sys.path.remove(deps)

    state = EngineState(backend="v4", paired=True, connected=True)
    state.slots["c1"] = Slot(slot_id="c1", type="COYOTE_030")
    state.slots["b1"] = Slot(slot_id="b1", type="BMTR_1")

    class _Eng:
        def get_state(self):
            return state

    class _Ctx:
        def __init__(self):
            self.settings = {
                "prefix": "DGLab",
                "device_prefixes": {"COYOTE": "DGLab", "OVC": "DGLabOvc",
                                    "BMTR": "DGLabBmtr"}}
            self.engine = _Eng()
            self.log = lambda msg: None

    mod = OscModule()
    mod.ctx = _Ctx()
    rows = [{"id": "osc_bridge", "name": "VRChat OSC 联动",
             "params": list(mod.link_params()) + list(mod.temp_specs())}]
    declared = _declared_vars(rows)
    saved = runtime.declared_vars
    try:
        runtime.declared_vars = declared
        system, user = runtime.var_table()
    finally:
        runtime.declared_vars = saved
    if not declared or not user:
        return False, f"declared={len(declared)} user={len(user)}"
    leaked = [row["name"] for row in system
              if str(row.get("mid") or "") == "osc_bridge"]
    if leaked:
        return False, f"system={leaked}"
    paths = [row for row in user
             if "/" in str(row["name"])
             and str(row.get("mid") or "") == "osc_bridge"]
    if len(paths) < 10:
        return False, f"paths={len(paths)}"
    # 方向按数据流：设备读数由核心读出后发回头像（可写），头像发入的
    # 控制参数收包镜像给宿主读出（可读）
    readouts = ("Battery", "Connected", "ChannelOK", "Limit", "Pressure",
                "EdgeState", "Action")
    controls = ("Wave", "Fire", "Pulse", "Emergency", "Zap")
    bad = [f"{row['name']}={row['dir']}" for row in paths
           if any(tail in row["name"] for tail in readouts)
           and row["dir"] != "out"]
    bad += [f"{row['name']}={row['dir']}" for row in paths
            if any(tail in row["name"] for tail in controls)
            and row["dir"] == "out"]
    seen_read = any(any(tail in row["name"] for tail in readouts)
                    for row in paths)
    seen_ctrl = any(any(tail in row["name"] for tail in controls)
                    for row in paths)
    if not (seen_read and seen_ctrl):
        return False, "rows missing readout / control group"
    if bad:
        return False, f"dirs={bad[:4]}"
    return True, ""


def _module_wheels_probe(engine) -> tuple[bool, str]:
    """自检：模块随包 wheel 必须适配当前解释器 ABI，装依赖才不靠网络。

    cp312 那批 wheel 混在 wheels/ 里时，宿主装模块就退化成联网 pip；网络一侧
    出任何问题（证书过期、镜像不通、离线机器）都是「依赖安装失败」。OCR 用的
    ocr_wheels/ 只喂内置解释器，不适配只会让识别回退模板法，记为提醒不算失败。
    """
    import module_store
    from plugins import module_roots

    offenders: list[str] = []
    notes: list[str] = []
    for root in module_roots():
        if not os.path.isdir(root):
            continue
        for name in sorted(os.listdir(root)):
            folder = os.path.join(root, name)
            if not os.path.isfile(os.path.join(folder, "plugin.py")):
                continue
            stale = module_store.stale_wheels(os.path.join(folder, "wheels"))
            if stale:
                offenders.append(f"{name}/wheels={stale}")
            if not module_store.deps_abi_ok(os.path.join(folder, "_deps")):
                offenders.append(f"{name}/_deps ABI 不符")
            ocr = module_store.stale_wheels(
                os.path.join(folder, "ocr_wheels"))
            if ocr:
                notes.append(f"{name}/ocr_wheels={ocr}")
    if offenders:
        return False, "；".join(offenders)
    return True, ("提醒：" + "；".join(notes)) if notes else ""


def _ef_tag(row) -> str:
    head = str(row.get("source") or "").split("（")[0].strip()
    return "" if head in ("", "核心", "临时变量") else head


def _stale_table_probe(runtime) -> bool:
    """自检：模块下线后残留在共享值空间里的名字不再出现在变量表。"""
    runtime.temps["StaleProbe"] = 3.0
    try:
        system, user = runtime.var_table()
        names = {row["name"] for row in system} | {row["name"] for row in user}
        return "StaleProbe" not in names
    finally:
        runtime.temps.pop("StaleProbe", None)


def _run_selftest() -> None:
    def watcher() -> None:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            window = App.window
            if window is not None:
                break
            time.sleep(0.2)
        else:
            REPORT["error"] = "window never appeared"
            os._exit(1)

        time.sleep(2.5)

        def probe_and_close():
            try:
                win = App.window
                from dglab.state import EngineState, Slot

                fake = EngineState(backend="ble", connected=True, paired=True,
                                   status_text="自检模拟状态")
                fake.slots["AABBCCDDEEFF"] = Slot(
                    slot_id="AABBCCDDEEFF", name="郊狼自检", type="COYOTE_1",
                    strength={"A": 62, "B": 46},
                    strength_limit={"A": 200, "B": 200}, battery=76)
                fake.slots["112233445599"] = Slot(
                    slot_id="112233445599", name="负鼠自检", type="OVC_1",
                    strength={"A": 20, "B": 30},
                    strength_limit={"A": 200, "B": 200}, battery=64)
                fake.slots["112233445566"] = Slot(
                    slot_id="112233445566", name="灵猫自检", type="BMTR_1",
                    pressure=18.4, edge_state=1, battery=41)
                win.state = fake

                for tag in ("dashboard", "connect", "control", "flow", "modules",
                            "log", "settings"):
                    win.goto(tag)
                    page = win._page(tag)
                    REPORT[f"page_{tag}"] = page is not None
                    try:
                        tick = getattr(page, "tick", None)
                        if tick is not None:
                            tick()
                        REPORT[f"tick_{tag}_ok"] = True
                    except Exception as exc:
                        REPORT[f"tick_{tag}_ok"] = False
                        REPORT["tick_error"] = f"{tag}: {exc!r}"

                REPORT["modules_autofetch_ok"] = (
                    win._page("modules")._fetch_started)
                flow = win._page("flow")
                flow.tick()
                fgraph = flow.graph
                created = []
                try:
                    from dglab.event_flow import PAGE_INPUT, PAGE_OUTPUT

                    node_a = flow._create_node("var.const_float", 120.0, 120.0)
                    node_b = flow._create_node("core.write.in_strength_a", 420.0, 150.0)
                    created = [node_a, node_b]
                    flow._auto_link((node_a, "out", 0), node_b)
                    REPORT["flow_link_ok"] = bool(fgraph.wires)
                    REPORT["flow_core_write_unique_ok"] = (
                        flow._create_node("core.write.in_strength_a",
                                          620.0, 150.0) is None
                        and len([n for n in fgraph.nodes
                                 if n.def_key == "core.write.in_strength_a"]) == 1)
                    flow._open_palette((220.0, 60.0))
                    REPORT["flow_core_write_hidden_ok"] = (
                        all(row["def"]["key"] != "core.write.in_strength_a"
                            for row in flow._palette_rows)
                        and any(row["def"]["key"] == "core.write.in_ovc_strength_a"
                                for row in flow._palette_rows))
                    flow._close_palette()
                    read = flow._create_node("var.temp_read", 700.0, 260.0)
                    sink = flow._create_node("var.temp_write", 760.0, 340.0)
                    created += [n for n in (read, sink) if n is not None]
                    REPORT["flow_guess_pin_ok"] = (
                        read is not None and sink is not None
                        and flow._guess_pin(sink, 0, "in", read) is None
                        and flow._guess_pin(read, 0, "out", sink) == 0)
                    from dglab import event_flow as _ef
                    temps = _ef.Catalog()
                    temps.refresh([{"id": "osc_bridge", "name": "OSC 桥",
                                    "params": [{"key": "avatar/parameters/X",
                                                "dir": "in"},
                                               {"key": "DGLab/Action",
                                                "dir": "out"}]}], 1)
                    REPORT["flow_module_temp_cards_ok"] = (
                        temps.definition(
                            "mod.read.osc_bridge.avatar/parameters/X")["cat"]
                        == "临时变量"
                        and temps.definition(
                            "mod.write.osc_bridge.DGLab/Action")["page"]
                        == PAGE_OUTPUT
                        and not temps.known(
                            "mod.read.osc_bridge.DGLab/Action")
                        and not temps.known(
                            "mod.write.osc_bridge.avatar/parameters/X")
                        and not temps.known(
                            "mod.obj.osc_bridge.avatar/parameters/X")
                        and not temps.definition(
                            "mod.read.osc_bridge.avatar/parameters/X")["palette"]
                        and temps.definition(
                            "mod.read.osc_bridge.avatar/parameters/X")["outputs"]
                        [0]["type"] == "float")
                    plain = _ef.Catalog()
                    plain.refresh([{"id": "m", "name": "M",
                                    "params": [("hp", "HP")]}], 1)
                    REPORT["flow_dir_default_ok"] = (
                        plain.known("mod.read.m.hp")
                        and not plain.known("mod.write.m.hp"))
                    rt = flow.runtime
                    try:
                        osc_ok, osc_note = _osc_row_probe(rt, win.engine)
                    except Exception as exc:      # 单项探针不能拖垮整个自检
                        osc_ok, osc_note = False, f"raised {exc!r}"
                    REPORT["flow_osc_rows_ok"] = osc_ok
                    REPORT["flow_osc_rows_dbg"] = osc_note
                    try:
                        wheels_ok, wheels_note = _module_wheels_probe(
                            win.engine)
                    except Exception as exc:
                        wheels_ok, wheels_note = False, f"raised {exc!r}"
                    REPORT["flow_module_wheels_ok"] = wheels_ok
                    REPORT["flow_module_wheels_dbg"] = wheels_note
                    REPORT["flow_var_table_ok"] = (
                        rt.add_user_var("SelfTestVar") == ""
                        and any(r["name"] == "SelfTestVar"
                                for r in rt.user_vars)
                        and bool(rt.add_user_var("SelfTestVar"))
                        and rt.rename_user_var("SelfTestVar", "SelfTestVar2") == ""
                        and os.path.isfile(rt.vars_file)
                        and rt.remove_user_var("SelfTestVar2"))
                    saved_paint = flow._paint
                    flow._paint = _StubPainter()
                    added = dragged = drawn = False
                    try:
                        flow.side_hits = []
                        flow._paint_side(rt)
                        drawn = any(b[4] == "var-add" for b in flow.side_hits)
                        add = next((b for b in flow.side_hits
                                    if b[4] == "var-add"), None)
                        if add is not None:
                            flow._side_pressed(
                                flow._side_point(add[0] + 4, add[1] + 4), None)
                            flow.edit.Text = "ProbeAdd"
                            flow._commit_edit()
                            added = any(r["name"] == "ProbeAdd"
                                        for r in rt.user_vars)
                        flow.side_hits = []
                        flow._paint_side(rt)
                        row = next((b for b in flow.side_hits
                                    if b[4] in ("var-name", "var-row")
                                    and b[5] == "ProbeAdd"), None)
                        if row is not None:
                            flow._side_pressed(
                                flow._side_point(row[0] + 4, row[1] + 4), None)
                            if flow.var_drag is not None:
                                flow.var_drag[3] = True
                                flow._finish_var_drag(140.0, 140.0)
                            dragged = any(n.def_key == "var.read.ProbeAdd"
                                          for n in fgraph.nodes)
                        for junk in [n for n in list(fgraph.nodes)
                                     if str(n.def_key).endswith("ProbeAdd")]:
                            fgraph.remove_node(junk)
                        rt.remove_user_var("ProbeAdd")
                    finally:
                        flow._paint = saved_paint
                    REPORT["flow_var_panel_ok"] = drawn
                    REPORT["flow_var_add_ok"] = added
                    REPORT["flow_var_drag_ok"] = dragged
                    lay = flow.layout(node_a)
                    REPORT["flow_layout_ok"] = lay.w > 100 and len(lay.outs) == 1
                    REPORT["flow_hit_ok"] = flow._hit(
                        *flow._world(*flow._screen(lay.x, lay.y)))[0] in (
                        "pin", "header", "param", "body")
                    REPORT["flow_paint_ok"] = (flow.canvas is not None
                                               and flow._canvas_bottom() > 0)
                    REPORT["flow_drew"] = flow._paint is not None
                    REPORT["flow_panel_ok"] = flow._side_point(
                        flow.pw - 20, flow.ph - 10)[0] in (
                        "side", "var-row", "var-name", "var-add", "var-del")
                    node_a.overrides[0] = 3.5
                    REPORT["flow_drag_ok"] = _drag_probe(flow, node_a, lay)
                    driver = flow._create_node("mod.period", 300.0, 640.0)
                    calc = flow._create_node("math.add", 120.0, 640.0)
                    created += [n for n in (driver, calc) if n is not None]
                    flow._auto_arrange()
                    REPORT["flow_arrange_mirror_ok"] = (
                        driver is not None and calc is not None
                        and driver.x > node_a.x > calc.x > node_b.x)
                    made_var = rt.add_user_var("ProbeVar") == ""
                    win.engine.flow.refresh(force=True)
                    reread = flow._create_node("var.read.ProbeVar", 300.0, 700.0)
                    vdst = flow._create_node("var.write.ProbeVar", 620.0, 700.0)
                    vnum = flow._create_node("var.const_float", 460.0, 760.0)
                    created += [n for n in (reread, vdst, vnum) if n is not None]
                    wrote = False
                    if None not in (reread, vdst, vnum):
                        fgraph.connect(flow.catalog, vnum, 0, vdst, 0)
                        rt.temps["ProbeVar"] = 0.0
                        rt.tick()
                        wrote = abs(rt.temps.get("ProbeVar", 0.0) - 1.0) < 1e-6
                        REPORT["flow_var_live_ok"] = bool(
                            flow._live_head(vdst, flow._def(vdst)))
                    REPORT["flow_var_cards_ok"] = (
                        made_var and flow.catalog.known("var.read.ProbeVar")
                        and flow.catalog.definition(
                            "var.write.ProbeVar")["op"] == "temp_write"
                        and wrote and not flow.catalog.known("var.obj.ProbeVar")
                        and rt.remove_user_var("ProbeVar"))
                    REPORT["flow_del_ok"] = _delete_probe(flow, fgraph)
                    try:
                        ui_ok, ui_note = _var_ui_probe(flow, rt)
                    except Exception as exc:      # 同上：探针失败要能报出原因
                        ui_ok, ui_note = False, f"raised {exc!r}"
                    REPORT["flow_var_ui_ok"] = ui_ok
                    REPORT["flow_var_ui_dbg"] = ui_note
                    REPORT["flow_table_stale_ok"] = _stale_table_probe(rt)
                    flow._switch_page(PAGE_OUTPUT)
                    REPORT["flow_switch_ok"] = (flow._page_key == PAGE_OUTPUT
                                                and flow.graph is not None)
                    flow._switch_page(PAGE_INPUT)
                finally:
                    for node in created:
                        fgraph.remove_node(node)
                    flow._commit(force=True)
                rt = flow.runtime
                base = rt.active
                base_names = list(rt.profile_names)
                made = rt.new_profile()
                REPORT["flow_profile_new_ok"] = (rt.active == made
                                                 and made not in base_names)
                flow.rebuild()
                back = rt.switch_profile(base)
                REPORT["flow_profile_switch_ok"] = (back and rt.active == base
                                                    and rt.graphs is
                                                    rt.profiles[base])
                REPORT["flow_profile_rename_ok"] = (
                    rt.rename_profile(made, "自检流配置") == ""
                    and "自检流配置" in rt.profile_names)
                REPORT["flow_profile_clash_ok"] = bool(
                    rt.rename_profile(base, "自检流配置"))
                REPORT["flow_profile_missing_ok"] = isinstance(
                    win.engine.flow.missing_modules(base), list)
                rt.profiles = {key: value for key, value in rt.profiles.items()
                               if key in base_names}
                rt.active = base
                rt.graphs = rt.profiles[base]
                flow.rebuild()

                from ui.flow_page import PALETTE_GROUPS, _CAT_GROUP

                flow._open_palette((220.0, 60.0))
                cats = [cand["def"]["cat"] for cand in flow._palette_rows]
                groups = [_CAT_GROUP.get(cat, len(PALETTE_GROUPS)) for cat in cats]
                REPORT["flow_palette_all_ok"] = len(cats) >= 40
                REPORT["flow_palette_order_ok"] = (
                    groups == sorted(groups)
                    and [g for g in dict.fromkeys(groups)] == sorted(set(groups)))
                flow.pal_edit.Text = "分支"
                flow._refresh_palette()
                REPORT["flow_palette_filter_ok"] = (
                    len(flow._palette_rows) == 1
                    and flow._palette_rows[0]["def"]["op"] == "branch")
                flow.pal_edit.Text = "小于"
                flow._refresh_palette()
                REPORT["flow_compare_cards_ok"] = (
                    {r["def"]["key"] for r in flow._palette_rows} >= {
                        "logic.lt", "logic.lte"}
                    and all(r["def"]["key"] != "var.temp_expr"
                            for r in flow._palette_rows))
                flow._close_palette()

                probe_node = fgraph.add_node(flow.catalog, "expr.formula",
                                             60.0, 420.0)
                try:
                    flow._begin_edit(probe_node, "field", "expr")
                    REPORT["flow_edit_rect_ok"] = (flow._edit_rect is not None
                                                   and flow._edit_target is not None)
                    flow.edit.Text = "{COYOTE.StrengthA} * 2"
                    flow._commit_edit()
                    REPORT["flow_edit_commit_ok"] = (
                        probe_node.params["expr"] == "{COYOTE.StrengthA} * 2"
                        and flow._edit_rect is None and flow._edit_target is None)
                finally:
                    fgraph.remove_node(probe_node)
                    flow._cancel_edit()
                    flow._commit(force=True)

                dbl = fgraph.add_node(flow.catalog, "expr.formula", 60.0, 700.0)
                try:
                    lay = flow.layout(dbl)
                    box = lay.fields["expr"]
                    px, py = flow._screen(box[2] + box[3] / 2.0, box[1])
                    flow._on_double_tapped(flow.canvas, _FakeTapArgs(px, py))
                    REPORT["flow_dbl_field_ok"] = (
                        flow._edit_target is not None
                        and flow._edit_target[0] is dbl
                        and flow._edit_target[1] == "field")
                    REPORT["flow_dbl_skip_ok"] = flow._editing(
                        dbl, "field", "expr")
                    flow._cancel_edit()
                    flow._on_double_tapped(flow.canvas, _FakeTapArgs(6.0, 6.0))
                    REPORT["flow_dbl_grid_ok"] = flow._pal_open
                    flow._close_palette()
                except Exception as exc:
                    REPORT["flow_dbl_error"] = repr(exc)
                    REPORT["flow_dbl_field_ok"] = False
                finally:
                    fgraph.remove_node(dbl)
                    flow._cancel_edit()
                    flow._commit(force=True)
                control = win._page("control")
                control.tick()
                view = control._cards.get("AABBCCDDEEFF")
                REPORT["ctrl_label_a"] = view.labels["A"].Text if view else None

                ovc = control._cards.get("112233445599")
                page._updating = False
                combo_a = view.wave_combos["A"]
                combo_a.SelectedIndex = 1
                combo_a.SelectedIndex = 2
                REPORT["wave_step_ok"] = combo_a.SelectedIndex == 2
                led = ovc.led_combo
                led.SelectedIndex = 2
                REPORT["led_pick_ok"] = led.SelectedIndex == 2
                control._set_binding(ovc, 13, "fire")
                bind_label = ovc.bindings.get(13)
                REPORT["binding_pick_ok"] = (bind_label is not None
                                             and bind_label.Text == control._binding_label_text("fire"))
                page._updating = True

                from ui import live as ui_live
                action_keys = [k for k, _ in ui_live.button_actions(win.engine)]
                osc_meta = win.engine.modules.meta("osc_bridge") or {}
                osc_ready = bool(osc_meta.get("loaded"))
                REPORT["module_action_ok"] = (
                    (not osc_ready)
                    or ("osc" in action_keys and "key" in action_keys))
                REPORT["osc_action_dispatch_ok"] = (
                    (not osc_ready)
                    or win.engine.modules.action("osc") is not None)
                REPORT["builtin_binding_ok"] = (win.engine.binding_missing_modules(
                    {"13": "fire", "12": "key:F1"}) == {})
                err = win.engine.rename_ovc_profile("默认", "自检配置")
                renamed_active, renamed_profiles = win.engine.binding_profiles()
                REPORT["rename_ok"] = (err is None
                                       and "自检配置" in renamed_profiles
                                       and renamed_active == "自检配置")

                from win32more.Microsoft.UI.Xaml import Visibility
                glow = ovc.button_glows.get(13)
                control.flash_button(13, True)
                lit = glow is not None and glow.Visibility == Visibility.Visible
                control.flash_button(13, False)
                held = glow.Visibility == Visibility.Visible
                control._glow_state[13] = (0.0, 0.0)
                control._refresh_glows()
                REPORT["glow_flash_ok"] = (lit and held
                                           and glow.Visibility == Visibility.Collapsed)

                win.goto("dashboard")
                win._page("dashboard").tick()

                mods_page = win._page("modules")
                from ui.modules_page import HIDDEN_MODULES
                installed = [meta["id"] for meta in win.engine.modules.list_modules()
                             if meta["id"] not in HIDDEN_MODULES]
                REPORT["modules_merged_ok"] = (
                    "link" not in win._items
                    and hasattr(mods_page, "_detector_block")
                    and not hasattr(mods_page, "_temp_block")
                    and isinstance(mods_page._live_cells, list)
                    and mods_page._module_sig() == mods_page._sig)

                before = str(win.RootGrid.RequestedTheme)
                win.toggle_theme()
                after = str(win.RootGrid.RequestedTheme)
                REPORT["theme_before"] = before
                REPORT["theme_after_toggle"] = after
                REPORT["theme_ok"] = before != after
                REPORT["nav_tags"] = sorted(win._items.keys())
                REPORT["status_text"] = win.state.status_text
            except Exception:
                _log_crash("selftest probe")
                REPORT["error"] = "probe failed"
            try:
                win.Close()
            except Exception:
                pass

        window.ui_queue.put(probe_and_close)

    threading.Thread(target=watcher, daemon=True).start()


def main() -> int:
    selftest = "--selftest" in sys.argv
    if selftest:
        def _probe_engine():
            from app import Engine

            path = os.path.join(tempfile.gettempdir(), "dgstudio_selftest_cfg.json")
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
            return Engine(config_path=path)

        App.engine_factory = _probe_engine
    code = 0
    try:
        if selftest:
            _run_selftest()
        XamlApplication.Start(App)
    except Exception:
        _log_crash("main")
        REPORT["fatal"] = traceback.format_exc()
        code = 1
    finally:
        engine = App.engine
        if engine is not None:
            try:
                engine.stop()
            except Exception:
                pass
    if selftest:
        try:
            path = os.path.join(tempfile.gettempdir(), "dgstudio_selftest.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(REPORT, f, ensure_ascii=False, default=str, indent=2)
            if REPORT.get("launched", True) and "error" not in REPORT:
                ok = all(v for k, v in REPORT.items()
                         if k.startswith("page_") or k.endswith("_ok"))
                code = 0 if ok else 1
            else:
                code = 1
        except OSError:
            code = 1
    return code


if __name__ == "__main__":
    sys.exit(main())

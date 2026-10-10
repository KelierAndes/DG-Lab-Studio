

from __future__ import annotations

import os
import sys
import threading
import time

from win32more import asyncui
from win32more.Microsoft.UI.Xaml import (HorizontalAlignment, Thickness,
                                         VerticalAlignment)
from win32more.Microsoft.UI.Xaml.Controls import Page
from win32more.Windows.Foundation import Uri
from win32more.winui3 import XamlClass

from dglab.naming import device_osc_names
from dglab.parsing import parse_name, parse_rect
from dglab.params import output_specs
from module_store import deps_abi_ok, requirement_satisfied, version_key
from ui import theme, widgets as W
from ui.dialogs import confirm_dialog, pick_open_path
from ui.paths import xaml
from ui.region_pick import pick_crop, pick_region

HIDDEN_MODULES = {"config_init"}

_EXE_FILTER = "可执行文件 (*.exe)\0*.exe\0所有文件 (*.*)\0*.*\0"
_DOCS_URL = "https://github.com/KelierAndes/dgstudio-modules-market/blob/main/EXTENSIONS.md"

_DETECTOR_KINDS = (("color", "检测颜色"), ("image", "检测图片"),
                   ("number", "检测数值"), ("bar", "检测数值条"))
_DETECTOR_DEFAULTS = {
    "color": {"color": "FF0000", "tol": 40, "ratio": 0.05},
    "image": {"file": "", "thresh": 0.8},
    "number": {"fmt": "int", "thresh": 0.6, "text": ""},
    "bar": {"min": 0, "max": 100, "thresh": 0.7, "anchor": ""},
}


def online_section_state(store) -> tuple[str, list]:
    if store.entries:
        return "ready", store.entries
    if store.fetched_at:
        return "empty", []
    return "idle", []


def _fmtv(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "真" if value else "假"
    try:
        num = float(value)
    except (TypeError, ValueError):
        return str(value)
    if num.is_integer():
        return str(int(num))
    return f"{num:g}"


def _vcenter(el):
    el.VerticalAlignment = VerticalAlignment.Center
    return el



class ModulesPage(XamlClass, Page):
    def __init__(self, shell):
        super().__init__()
        self.shell = shell
        self._sig = None
        self._last = 0.0
        self._last_live = 0.0
        self._fetch_started = False
        self._busy: set[str] = set()
        self._live_cells: list[list] = []
        self._updating = False
        self._blocks: dict[str, object] = {}
        self._open_blocks: dict[str, bool] = {}
        self.LoadComponentFromFile(xaml("ModulesPage.xaml"), encoding="utf-8")
        self.rebuild()

    def tick(self) -> None:
        now = time.monotonic()
        if now - self._last_live >= 0.5:
            self._last_live = now
            self._refresh_live()
        if now - self._last < 1.0:
            return
        self._last = now
        sig = self._module_sig()
        if sig != self._sig:
            self.rebuild()

    def on_notify(self) -> None:
        self.rebuild()

    def _module_sig(self) -> tuple:
        modules = self.shell.engine.modules.list_modules()
        return tuple((m["id"], m["loaded"], m["running"]) for m in modules)

    # ------------------------------------------------------------ 页面骨架
    def _collapsible(self, key: str, title: str, body, *, subtitle: str,
                     default: bool = False) -> object:
        """记住每个可折叠块的展开状态：rebuild 后不再被折回去。"""
        for name, outer in self._blocks.items():
            self._open_blocks[name] = W.is_expanded(outer)
        outer = W.collapsible(title, body, subtitle=subtitle,
                              expanded=self._open_blocks.get(key, default))
        self._blocks[key] = outer
        return outer

    def rebuild(self) -> None:
        self._sig = self._module_sig()
        self._last = time.monotonic()
        W.page_head(
            self.HeadHost,
            {"title": "模块",
             "subtitle": "联动模块在此下载、安装与配置；实时数值与临时变量统一在"
                         "「事件流」页的变量表与卡片上显示",
             "breadcrumb": ["控制台", "模块"]},
            actions=[
                W.text_button("获取在线列表", symbol="Download",
                              on_click=lambda s, e: self._fetch_online(force=True)),
                W.text_button("扫描模块目录", symbol="Refresh",
                              on_click=lambda s, e: self._rescan()),
                W.text_button("保存设置", symbol="Save", accent=True,
                              on_click=lambda s, e: self._save()),
            ],
        )
        self._updating = True
        try:
            self._live_cells = []
            content = W.stack(spacing=10, h="stretch")
            installed = [meta for meta in self.shell.engine.modules.list_modules()
                         if meta["id"] not in HIDDEN_MODULES]
            if not installed:
                content.Children.Append(self._hint_card(
                    "模块目录为空",
                    f"可从下方「在线模块」一键下载，或将模块文件夹放入 "
                    f"{self.shell.engine.modules.base_dir} 后点「扫描模块目录」。"))
            for meta in installed:
                content.Children.Append(self._module_card(meta))
            content.Children.Append(self._section_head(
                "在线模块",
                "清单来自 dgstudio-modules-market 仓库，点击「下载」取回模块文件，"
                "下载完成后自动安装（不启用），点「启动」开始运行。"))
            content.Children.Append(self._online_list())
            content.Children.Append(self._hint_card(
                "开发自己的模块",
                "模块是 modules/<id>/plugin.py 中的一个 ModuleBase 子类，可获得强度参数、"
                "波形、开火、急停、临时变量（ctx.set_temp/get_temp，META[\"temps\"] 声明"
                "展示为模块维护行）等公开 API。接口说明与示例见 dgstudio-modules-market "
                "仓库的开发文档，模块依赖由 META[\"dependencies\"] 声明、安装时自动补装。",
                link_label="dgstudio-modules-market · 模块开发文档（EXTENSIONS.md）"))
            self.ListHost.Content = content
        finally:
            self._updating = False
        if not self._fetch_started:
            self._fetch_started = True
            self._fetch_online(force=False)

    def flush_config(self) -> None:
        pass

    def _save(self) -> None:
        self.shell.engine.save_config()
        modules = self.shell.engine.modules
        for meta in modules.list_modules():
            if modules.instance(meta["id"]) is not None:
                self.shell.submit(modules.reload(meta["id"]))
        self.shell.logs.append("设置已保存，运行中模块的映射表已重载")

    def _rescan(self) -> None:
        found = self.shell.engine.modules.discover()
        self.shell.logs.append(
            f"已扫描模块目录 {self.shell.engine.modules.base_dir}，发现 {len(found)} 个模块")
        self.rebuild()

    def _section_head(self, title: str, subtitle: str) -> object:
        inner = W.stack(spacing=2, margin=Thickness(4, 8, 4, 0))
        inner.Children.Append(W.text(title, size=14, bold=W.SEMIBOLD))
        inner.Children.Append(W.text(subtitle, size=11, color="text3", wrap=True))
        return inner

    # ------------------------------------------------------------ 已安装卡片
    def _module_card(self, meta: dict) -> object:
        module_id = meta["id"]
        engine = self.shell.engine

        status_fg, status_bg, status_text = (
            ("success", "success_soft", "运行中") if meta["running"]
            else ("accent_text", "accent_soft", "已安装") if meta["loaded"]
            else ("text3", "track", "未安装"))
        tile = W.box(
            width=34, height=34, corner=8,
            background=theme.brush("accent_soft" if meta["running"] else "track"),
            child=W.icon(symbol="View", size=16,
                         color="accent_text" if meta["running"] else "text2"),
            v="center",
        )

        title = W.stack(horizontal=True, spacing=8, v="center")
        title.Children.Append(W.text(meta["name"], size=14, bold=W.SEMIBOLD))
        title.Children.Append(W.text(f"v{meta['version']}", size=11, color="text3", v="center"))
        title.Children.Append(W.text(module_id, size=11, color="text3",
                                     family="Consolas", v="center"))

        head = W.grid(W.auto(), W.star(1), W.auto())
        head.Children.Append(W.put(tile, 0))
        info = W.stack(spacing=2, margin=Thickness(10, 0, 0, 0), v="center")
        info.Children.Append(title)
        desc = W.text(meta["description"] or "（无描述）", size=12, color="text3", wrap=True)
        info.Children.Append(desc)
        head.Children.Append(W.put(info, 1))
        head.Children.Append(W.put(W.pill(status_text, status_fg, status_bg,
                                          dot_color=status_fg if meta["running"] else None), 2))

        buttons = W.stack(horizontal=True, spacing=8, v="center")
        if not meta["loaded"]:
            buttons.Children.Append(W.text_button("安装并启动", symbol="Download",
                                                  accent=True,
                                                  on_click=lambda s, e, mid=module_id: self._install(mid)))
            buttons.Children.Append(W.text_button("删除", symbol="Delete",
                                                  on_click=lambda s, e, mid=module_id: self._delete(mid)))
        else:
            if meta["running"]:
                buttons.Children.Append(W.text_button("停止", symbol="Stop",
                                                      on_click=lambda s, e, mid=module_id: self._deactivate(mid)))
            else:
                buttons.Children.Append(W.text_button("启动", symbol="Play",
                                                      accent=True,
                                                      on_click=lambda s, e, mid=module_id: self._install(mid)))
            buttons.Children.Append(W.text_button("快捷配置", symbol="Accept",
                                                  on_click=lambda s, e, mid=module_id: self._apply_defaults(mid)))
            buttons.Children.Append(W.text_button("卸载", symbol="Remove",
                                                  on_click=lambda s, e, mid=module_id: self._uninstall(mid)))

        actions = W.stack(horizontal=True, spacing=8, v="center", h="right")
        actions.Children.Append(buttons)

        foot = W.grid(W.star(1), W.auto())
        note = ("启用状态已保存，重启后自动加载" if meta["enabled"]
                else "已停用，重启后不会加载")
        foot.Children.Append(W.put(W.text(note, size=11, color="text3", v="center"), 0))
        foot.Children.Append(W.put(actions, 1))

        inner = W.stack(spacing=10, h="stretch")
        inner.Children.Append(head)
        inner.Children.Append(W.divider())
        deps_row = self._deps_row(meta)
        if deps_row is not None:
            inner.Children.Append(deps_row)
            inner.Children.Append(W.divider())
        inner.Children.Append(foot)
        if meta["loaded"] and meta["config"]:
            inner.Children.Append(W.divider())
            inner.Children.Append(self._config_block(module_id))
        if meta["loaded"] and self._is_realtime_manager(module_id):
            inner.Children.Append(W.divider())
            inner.Children.Append(self._detector_block(module_id))
        if engine.modules.module_mods_dir(module_id):
            inner.Children.Append(W.divider())
            inner.Children.Append(self._game_mod_row(module_id))
        return W.card(inner)

    # ------------------------------------------------------------ 模块配置
    def _engine(self, module_id: str):
        inst = self.shell.engine.modules.instance(module_id)
        runtime = getattr(inst, "bridge", None) or getattr(inst, "server", None)
        return getattr(runtime, "engine", None) if runtime is not None else None

    def _config_block(self, module_id: str) -> object:
        modules = self.shell.engine.modules
        spec = modules.config_spec_for(module_id)
        cfg = modules.settings_for(module_id)
        varpool = self._var_pool(module_id)
        return self._settings_block(module_id, spec, cfg, varpool)

    def _placeholder_row(self, text: str) -> object:
        return W.box(height=36, child=W.text(text, size=12, color="text3",
                                             v="center"))

    def _add_row(self, label: str, on_click) -> object:
        return W.box(height=40, child=W.text_button(label, symbol="Add",
                                                    on_click=on_click))

    def _detector_note(self) -> object:
        return W.text(
            "识别参数（画面识别联动）：每个参数名即一个实时变量，"
            "「事件流」画布用 {名称} 引用。行为四选一——检测颜色/检测图片/检测数值"
            "输出 真/假 或数值，检测数值条输出 0~1。区域坐标为截图图像像素，"
            "可手动输入 x,y,w,h 或点「截区域」框选（数值条整条框选时 0%/100% 位置"
            "自动取区域两端）；例图/锚点经「截例图」框选后自动存入模板目录"
            "（锚点取填充色与背景色交界的竖直窄条）。数字/文字由 RapidOCR 识别。",
            size=11, color="text3", wrap=True)

    def _is_realtime_manager(self, module_id: str) -> bool:
        meta = self.shell.engine.modules.meta(module_id) or {}
        return bool(meta.get("realtime_manager"))

    def _detector_block(self, module_id: str) -> object:
        """画面识别类模块的识别参数表（临时变量已并入「事件流」页的变量表）。"""
        cfg = self.shell.engine.modules.settings_for(module_id)
        inner = W.stack(spacing=6, h="stretch")
        inner.Children.Append(self._detector_note())
        dets = [d for d in (cfg.get("detectors") or []) if isinstance(d, dict)]
        if not dets:
            inner.Children.Append(self._placeholder_row(
                "（暂无识别参数：点击下方「添加参数」新建，先选行为再截取区域）"))
        for entry in dets:
            inner.Children.Append(self._detector_row(module_id, cfg, entry))
        inner.Children.Append(self._add_row(
            "添加参数", lambda s, e, _m=module_id: self._add_detector(_m)))
        return W.panel(self._collapsible(
            f"{module_id}:detectors", "识别参数", inner,
            subtitle="（参数名 ← 检测行为，改动即时生效）"), padding=14)


    def _detector_row(self, module_id: str, cfg: dict, entry: dict) -> object:
        top = W.grid(W.fixed(150), W.fixed(110), W.fixed(90), W.auto())
        top.ColumnSpacing = 8

        name_box = W.text_box(text=str(entry.get("name") or ""),
                              placeholder="参数名")
        name_box.HorizontalAlignment = HorizontalAlignment.Stretch
        name_box.LostFocus += lambda s, a, _e=entry, _b=name_box: \
            self._commit_detector_name(module_id, _e,
                                       (_b.Text or "").strip())
        kind_idx = next((i for i, (k, _l) in enumerate(_DETECTOR_KINDS)
                         if k == entry.get("kind")), 0)
        kind_cb = W.combo([label for _k, label in _DETECTOR_KINDS],
                          selected=kind_idx)
        kind_cb.SelectionChanged += lambda s, e, _e=entry, _c=kind_cb: \
            self._switch_detector_kind(module_id, _e, _c.SelectedIndex)
        live_tb = W.text("—", size=12, bold=W.SEMIBOLD, family="Consolas",
                         trimming=True, v="center")
        self._live_cells.append([live_tb, "signal", module_id,
                                 str(entry.get("name") or ""), ""])
        delete = W.text_button("删除", symbol="Delete",
                               on_click=lambda s, e, _e=entry:
                               self._remove_detector(module_id, _e))
        top.Children.Append(W.put(_vcenter(name_box), 0))
        top.Children.Append(W.put(_vcenter(kind_cb), 1))
        top.Children.Append(W.put(_vcenter(live_tb), 2))
        top.Children.Append(W.put(_vcenter(delete), 3))

        args = W.stack(horizontal=True, spacing=8, v="center")
        self._detector_args(module_id, cfg, entry, args)
        err = self._detector_error(module_id, entry)
        if err:
            args.Children.Append(W.text(f"⚠ {err}", size=11, color="danger",
                                        v="center"))

        body = W.stack(spacing=6, h="stretch")
        body.Children.Append(top)
        body.Children.Append(args)
        return W.box(corner=6, padding=Thickness(10, 8, 10, 8),
                     background=theme.brush("section"),
                     border=theme.brush("stroke"), child=body, h="stretch")

    def _detector_args(self, module_id: str, cfg: dict, entry: dict,
                       host) -> None:
        kind = str(entry.get("kind") or "color")
        host.Children.Append(
            self._labeled("检测区域", self._rect_field(module_id, cfg, entry)))
        if kind == "color":
            host.Children.Append(self._text_arg("颜色 #RRGGBB", entry, "color",
                                                module_id, width=92))
            host.Children.Append(self._number_arg("容差", entry, "tol",
                                                  0, 255, module_id, 0))
            host.Children.Append(self._number_arg("占比", entry, "ratio",
                                                  1, 100, module_id, 5,
                                                  is_percent=True))
        elif kind == "image":
            host.Children.Append(self._file_arg("例图", entry, "file",
                                                module_id))
            host.Children.Append(self._thresh_arg("匹配阈值", entry,
                                                  module_id, 0.8))
        elif kind == "number":
            fmt = str(entry.get("fmt") or "int")
            fmt_idx = ("int", "float", "text").index(fmt) \
                if fmt in ("int", "float", "text") else 0
            fmt_cb = W.combo(["整数", "小数", "文字"], selected=fmt_idx,
                             width=84)
            fmt_cb.SelectionChanged += lambda s, e, _e=entry, _c=fmt_cb: \
                self._commit_detector(module_id, _e,
                                      {"fmt": ("int", "float",
                                               "text")[_c.SelectedIndex]})
            host.Children.Append(_vcenter(self._labeled("输出类型", fmt_cb)))
            if fmt == "text":
                host.Children.Append(self._text_arg("期望文本", entry, "text",
                                                    module_id, width=120))
            host.Children.Append(self._thresh_arg("匹配阈值", entry,
                                                  module_id, 0.6))
        elif kind == "bar":
            host.Children.Append(self._number_arg("0% 位置(x/y)", entry, "min",
                                                  -99999, 99999, module_id, 0))
            host.Children.Append(self._number_arg("100% 位置(x/y)", entry, "max",
                                                  -99999, 99999, module_id, 100))
            host.Children.Append(self._file_arg("锚点例图", entry, "anchor",
                                                module_id))
            host.Children.Append(self._thresh_arg("匹配阈值", entry,
                                                  module_id, 0.7))

    def _rect_field(self, module_id: str, cfg: dict, entry: dict) -> object:
        rect = entry.get("rect")
        text = (f"{rect[0]},{rect[1]},{rect[2]},{rect[3]}"
                if isinstance(rect, (list, tuple)) and len(rect) == 4 else "")
        box = W.text_box(text=text, placeholder="x,y,w,h", width=128)
        box.LostFocus += lambda s, a, _e=entry, _b=box: \
            self._commit_rect(module_id, _e, (_b.Text or "").strip())
        pick = W.text_button("截区域", symbol="Crop",
                             on_click=lambda s, e, _e=entry:
                             self._pick_rect(module_id, _e))
        row = W.stack(horizontal=True, spacing=4, v="center")
        row.Children.Append(box)
        row.Children.Append(pick)
        return row

    def _text_arg(self, header: str, entry: dict, key: str, module_id: str,
                  *, width: float = 96) -> object:
        box = W.text_box(text=str(entry.get(key) or ""), width=width)
        box.LostFocus += lambda s, a, _e=entry, _b=box, _k=key: \
            self._commit_detector(module_id, _e,
                                  {_k: (_b.Text or "").strip()})
        return self._labeled(header, box)

    def _number_arg(self, header: str, entry: dict, key: str, lo: float,
                    hi: float, module_id: str, default: float,
                    *, width: float = 68, is_percent: bool = False) -> object:
        try:
            current = float(entry.get(key, default))
        except (TypeError, ValueError):
            current = float(default)
        if is_percent:
            current = current * 100.0

        def commit(value: float, _e=entry, _k=key, _m=module_id) -> None:
            self._commit_detector(_m, _e,
                                  {_k: (value / 100.0 if is_percent
                                        else int(round(value)))})

        return self._labeled(header, W.number_box(current, lo, hi,
                                                  width=width,
                                                  on_commit=commit))

    def _thresh_arg(self, header: str, entry: dict, module_id: str,
                    default: float) -> object:
        return self._number_arg(header, entry, "thresh", 5, 100, module_id,
                                default * 100.0, is_percent=True)

    def _file_arg(self, header: str, entry: dict, key: str,
                  module_id: str) -> object:
        box = W.text_box(text=str(entry.get(key) or ""), width=116,
                         placeholder="自动生成")
        box.LostFocus += lambda s, a, _e=entry, _b=box, _k=key: \
            self._commit_detector(module_id, _e, {_k: (_b.Text or "").strip()})
        label = "截例图" if key == "file" else "截锚点"
        pick = W.text_button(label, symbol="Crop",
                             on_click=lambda s, e, _e=entry, _k=key:
                             self._pick_crop(module_id, _e, _k))
        row = W.stack(horizontal=True, spacing=4, v="center")
        row.Children.Append(box)
        row.Children.Append(pick)
        return self._labeled(header, row)

    def _labeled(self, header: str, control) -> object:
        cell = W.stack(spacing=2, v="center")
        cell.Children.Append(W.text(header, size=10, color="text3",
                                    trimming=True))
        cell.Children.Append(control)
        return cell

    def _commit_detector(self, module_id: str, entry: dict,
                         changes: dict) -> None:
        if self._updating:
            return
        if all(entry.get(k) == v for k, v in changes.items()):
            return
        entry.update(changes)
        self._save_and_reload(module_id)

    def _commit_detector_name(self, module_id: str, entry: dict,
                              text: str) -> None:
        if self._updating or not text or text == entry.get("name"):
            return
        try:
            parse_name(text)
        except ValueError:
            self.shell.logs.append(f"参数名 {text!r} 无效：需字母开头的"
                                   "字母/数字/下划线")
            return
        entry["name"] = text
        self._save_and_reload(module_id)
        self.rebuild()

    def _commit_rect(self, module_id: str, entry: dict, text: str) -> None:
        if self._updating:
            return
        if not text:
            if entry.get("rect") is None:
                return
            entry["rect"] = None
        else:
            try:
                rect = list(parse_rect(text))
            except ValueError as exc:
                self.shell.logs.append(f"检测区域无效: {exc}")
                return
            if entry.get("rect") == rect:
                return
            entry["rect"] = rect
        self._save_and_reload(module_id)
        self.rebuild()

    def _switch_detector_kind(self, module_id: str, entry: dict,
                              index) -> None:
        if self._updating or not isinstance(index, int):
            return
        if not 0 <= index < len(_DETECTOR_KINDS):
            return
        kind = _DETECTOR_KINDS[index][0]
        if entry.get("kind") == kind:
            return
        entry["kind"] = kind
        for key, value in _DETECTOR_DEFAULTS.get(kind, {}).items():
            entry.setdefault(key, value)
        self._save_and_reload(module_id)
        self.rebuild()

    def _add_detector(self, module_id: str) -> None:
        if self._updating:
            return
        cfg = self.shell.engine.modules.settings_for(module_id)
        rows = [r for r in (cfg.get("detectors") or [])
                if isinstance(r, dict)]
        used = {str(r.get("name") or "") for r in rows}
        n = 1
        while f"param{n}" in used:
            n += 1
        entry = {"name": f"param{n}", "kind": "color", "rect": None}
        entry.update(_DETECTOR_DEFAULTS["color"])
        rows.append(entry)
        cfg["detectors"] = rows
        self.rebuild()

    def _remove_detector(self, module_id: str, entry: dict) -> None:
        if self._updating:
            return
        cfg = self.shell.engine.modules.settings_for(module_id)
        cfg["detectors"] = [r for r in (cfg.get("detectors") or [])
                            if r is not entry]
        self._save_and_reload(module_id)
        self.rebuild()

    def _pick_rect(self, module_id: str, entry: dict) -> None:
        def done(rect) -> None:
            if rect is None or self._updating:
                return
            entry["rect"] = list(rect)
            if str(entry.get("kind")) == "bar":
                x, y, w, h = rect
                if w >= h:
                    entry["min"], entry["max"] = x, x + w
                else:
                    entry["min"], entry["max"] = y, y + h
            self._save_and_reload(module_id)
            self.rebuild()

        pick_region(done)

    def _pick_crop(self, module_id: str, entry: dict, key: str) -> None:
        def done(result) -> None:
            if result is None or self._updating:
                return
            png, _rect = result
            inst = self.shell.engine.modules.instance(module_id)
            make_dir = getattr(inst, "template_dir", None)
            if make_dir is None:
                return
            directory = make_dir()
            os.makedirs(directory, exist_ok=True)
            fname = f"{entry.get('name') or 'param'}_{key}.png"
            with open(os.path.join(directory, fname), "wb") as f:
                f.write(png)
            entry[key] = fname
            self._save_and_reload(module_id)
            self.rebuild()

        pick_crop(done)

    def _save_and_reload(self, module_id: str) -> None:
        cfg = self.shell.engine.modules.settings_for(module_id)
        cfg.save()
        self.shell.submit(self.shell.engine.modules.reload(module_id))

    def _detector_error(self, module_id: str, entry: dict) -> str:
        inst = self.shell.engine.modules.instance(module_id)
        bridge = getattr(inst, "bridge", None)
        for det in getattr(bridge, "detectors", None) or []:
            if det.name == str(entry.get("name") or "") and det.error:
                return det.error
        return ""

    def _var_pool(self, module_id: str) -> list[str]:
        pool: set[str] = set()
        meta = self.shell.engine.modules.meta(module_id) or {}
        for name in (meta.get("params") or {}):
            pool.add(str(name))
        inst = self.shell.engine.modules.instance(module_id)
        if inst is not None:
            try:
                for name, _label in inst.link_params():
                    pool.add(str(name))
            except Exception:
                pass
        engine = self._engine(module_id)
        if engine is not None:
            try:
                pool.update(str(key) for key in engine.values())
            except Exception:
                pass
        try:
            names = device_osc_names(self.shell.state, {})
        except Exception:
            names = {}
        counters: dict[str, int] = {}
        for sid in sorted(names):
            info = names[sid]
            fam, idx = info["family"], int(info.get("index", 1))
            for sp in output_specs(fam, idx):
                pool.add(sp["key"])
        pool.update(("Strength", "Limit", "max", "Battery", "Connected",
                     "Pressure", "Action"))
        for spec in self.shell.engine.modules.temp_specs_for(module_id):
            if spec.get("key"):
                pool.add(str(spec["key"]))
        cfg = self.shell.engine.modules.settings_for(module_id)
        for row in (cfg.get("temps") or []):
            if isinstance(row, dict) and row.get("name"):
                pool.add(str(row["name"]))
        return sorted(pool)

    def _settings_block(self, module_id: str, spec: dict, cfg: dict,
                        varpool) -> object:
        rows = []
        for key, item in spec.items():
            if not isinstance(item, dict):
                continue
            if item.get("type") == "list":
                continue
            rows.append(self._declared_row(key, item, cfg, varpool))
        if not rows:
            rows.append(W.text("（该模块无额外设置项）", size=12, color="text3"))
        body = W.stack(spacing=0)
        for i, row in enumerate(rows):
            if i:
                body.Children.Append(W.divider())
            body.Children.Append(row)
        title = "OSC 地址与端口" if module_id == "osc_bridge" else "模块设置"
        return W.panel(self._collapsible(
            f"{module_id}:config", title, body,
            subtitle="（修改后重新开关上方模块生效）"), padding=14)

    def _declared_row(self, key: str, item: dict, cfg: dict, varpool) -> object:
        itype = str(item.get("type") or "str")
        label = str(item.get("label") or key)
        desc = str(item.get("desc") or "")

        if itype == "map":
            return W.field_row(label, desc, self._map_field(key, item, cfg))

        def commit(value) -> None:
            if self._updating:
                return
            if cfg.get(key) != value:
                cfg[key] = value
                cfg.save()

        if itype == "param":
            box = W.suggest_box(
                text=str(cfg.get(key, item.get("default", ""))),
                choices=varpool, width=200,
                on_commit=lambda t, _k=key: commit(t or None))
            return W.field_row(label, desc, box)

        if itype in ("int", "float"):
            low = float(item.get("min", 0))
            high = float(item.get("max", 100))
            step = float(item.get("step", 1 if itype == "int" else 0.1))
            current = float(cfg.get(key, item.get("default", low)))
            unit = str(item.get("unit") or "")
            value_tb = W.text(self._fmt(current, itype, unit), size=12,
                              bold=W.SEMIBOLD, family="Consolas",
                              trimming=True, v="center")

            def _show(value: float, _i=itype, _u=unit, _tb=value_tb) -> None:
                _tb.Text = self._fmt(value, _i, _u)

            def _apply(value: float) -> None:
                commit(int(round(value)) if itype == "int" else round(value, 3))

            if high - low <= 1000:
                slider = W.slider(low, high, current, step=step, width=170,
                                  on_change=_show, on_commit=_apply)
                control = W.stack(horizontal=True, spacing=8, v="center")
                control.Children.Append(slider)
                control.Children.Append(value_tb)
            else:
                control = W.number_box(current, low, high, width=130,
                                       on_commit=lambda v: (_apply(v), _show(v)))
            return W.field_row(label, desc, control)

        if itype == "bool":
            toggle = W.switch(bool(cfg.get(key, item.get("default", False))))

            def _toggle(sender, args) -> None:
                commit(bool(toggle.IsOn))

            toggle.Toggled += _toggle
            return W.field_row(label, desc, toggle)

        if itype == "choice":
            choices = [str(c) for c in (item.get("choices") or [])]
            current = str(cfg.get(key, item.get("default", "")))
            idx = choices.index(current) if current in choices else 0
            combo = W.combo(choices, selected=idx, width=170)

            def _pick(sender, args, _choices=choices) -> None:
                sel = combo.SelectedIndex
                if self._updating or not isinstance(sel, int) \
                        or not 0 <= sel < len(_choices):
                    return
                commit(_choices[sel])

            combo.SelectionChanged += _pick
            return W.field_row(label, desc, combo)

        box = W.text_box(text=str(cfg.get(key, item.get("default", ""))),
                         width=170)
        box.TextChanged += lambda sender, args: commit(
            (box.Text or "").strip() or None)
        return W.field_row(label, desc, box)

    @staticmethod
    def _fmt(value: float, itype: str, unit: str) -> str:
        shown = str(int(round(value))) if itype == "int" else f"{value:g}"
        return f"{shown} {unit}".strip()

    def _map_field(self, key: str, item: dict, cfg: dict) -> object:
        table = cfg.setdefault(key, {})
        defaults = dict(item.get("default") or {})
        keys = list(dict.fromkeys([*table.keys(), *defaults.keys()]))
        row = W.stack(horizontal=True, spacing=8, v="center")

        def _commit(family: str, text: str, _cfg=cfg) -> None:
            if self._updating:
                return
            value = (text or "").strip()
            if value and cfg[key].get(family) != value:
                cfg[key][family] = value
                _cfg.save()

        for family in keys:
            cell = W.stack(spacing=2, v="center")
            cell.Children.Append(W.text(family, size=10, color="text3"))
            cell.Children.Append(W.suggest_box(
                text=str(table.get(family) or defaults.get(family, "")),
                width=110, on_commit=lambda t, _f=family: _commit(_f, t)))
            row.Children.Append(cell)
        return row

    # ------------------------------------------------------------ 实时值刷新
    def _refresh_live(self) -> None:
        """惰性刷新：只有数值文本变化才写 TextBlock。"""
        if not self._live_cells:
            return
        engines: dict[str, object] = {}
        for cell in self._live_cells:
            tb, kind, mid, name, last = (cell[0], cell[1], cell[2],
                                         cell[3], cell[4])
            engine = engines.setdefault(mid, self._engine(mid))
            value = engine.signals.get(name) if engine is not None else None
            text = _fmtv(value)
            if text != last:
                tb.Text = text
                cell[4] = text

    # ------------------------------------------------------------ 在线模块
    def _deps_row(self, meta: dict) -> object | None:
        requirements = [str(r) for r in (meta.get("dependencies") or [])
                        if str(r).strip()]
        if not requirements:
            return None
        module_id = meta["id"]
        store = self.shell.engine.modules.store
        deps = store.deps_dir(module_id)
        frozen = getattr(sys, "frozen", False)
        extra = (deps,) if frozen and deps and os.path.isdir(deps) else ()

        required = [r for r in requirements if not r.startswith("!")]
        optional = [r for r in requirements if r.startswith("!")]
        if deps and not deps_abi_ok(deps):
            missing = list(required)
        else:
            missing = [r for r in required
                       if not requirement_satisfied(r, extra)]

        chips = W.stack(horizontal=True, spacing=10, v="center")
        for req in required:
            ok = requirement_satisfied(req, extra)
            chip = W.stack(horizontal=True, spacing=4, v="center")
            chip.Children.Append(W.text("✓" if ok else "✗", size=11,
                                        color="success" if ok else "danger"))
            chip.Children.Append(W.text(req, size=11, family="Consolas",
                                        color="text2"))
            chips.Children.Append(chip)
        for req in optional:
            ok = requirement_satisfied(req[1:], extra)
            chip = W.stack(horizontal=True, spacing=4, v="center")
            chip.Children.Append(W.text("✓" if ok else "○", size=11,
                                        color="success" if ok else "text3"))
            chip.Children.Append(W.text(f"{req[1:]}（可选）", size=11,
                                        family="Consolas", color="text3"))
            chips.Children.Append(chip)

        row = W.stack(horizontal=True, spacing=8, v="center", h="stretch")
        row.Children.Append(W.text("依赖", size=11, color="text3", v="center"))
        row.Children.Append(chips)
        if missing:
            row.Children.Append(W.text_button("安装依赖", symbol="Download",
                                              accent=True,
                                              on_click=lambda s, e, mid=module_id: self._install_deps(mid)))
        return row

    def _hint_card(self, title: str, body: str, *,
                   link_label: str | None = None) -> object:
        inner = W.stack(spacing=6)
        inner.Children.Append(W.text(title, size=13, bold=W.SEMIBOLD))
        inner.Children.Append(W.text(body, size=12, color="text3", wrap=True))
        if link_label:
            hyperlink = W.link(link_label, size=12)
            hyperlink.NavigateUri = Uri(_DOCS_URL)
            inner.Children.Append(hyperlink)
        return W.card(inner)

    def _online_list(self) -> object:
        manager = self.shell.engine.modules
        store = manager.store
        host = W.stack(spacing=8, h="stretch")
        state, entries = online_section_state(store)
        if state != "ready":
            detail = ("点击上方「获取在线列表」从模块市场拉取可用模块。"
                      "拉取缓慢或失败时，可在「设置 → 模块市场」配置加速前缀"
                      "或网络代理。")
            if state == "empty":
                title, detail = "仓库暂无模块", "清单获取成功，但还没有发布任何模块。"
            else:
                title = "在线清单未获取"
                if store.last_error:
                    detail += f"上次获取失败：{store.last_error}"
            host.Children.Append(self._hint_card(title, detail))
            return host
        local = {meta["id"]: meta for meta in manager.list_modules()}
        for entry in entries:
            host.Children.Append(self._online_card(entry, local.get(entry["id"])))
        return host

    def _online_card(self, entry: dict, local: dict | None) -> object:
        module_id = entry["id"]
        remote_v = entry["version"]
        tile = W.box(width=34, height=34, corner=8, background=theme.brush("track"),
                     child=W.icon(symbol="Download", size=16, color="text2"),
                     v="center")

        title = W.stack(horizontal=True, spacing=8, v="center")
        title.Children.Append(W.text(entry["name"], size=14, bold=W.SEMIBOLD))
        title.Children.Append(W.text(f"v{remote_v}", size=11, color="text3", v="center"))
        title.Children.Append(W.text(module_id, size=11, color="text3",
                                     family="Consolas", v="center"))
        if entry.get("author"):
            title.Children.Append(W.text(f"@{entry['author']}", size=11,
                                         color="text3", v="center"))

        deps_note = "、".join(r.lstrip("!") + ("（可选）" if r.startswith("!") else "")
                              for r in entry.get("requirements") or [])
        info = W.stack(spacing=2, margin=Thickness(10, 0, 0, 0), v="center")
        info.Children.Append(title)
        info.Children.Append(W.text(entry["description"] or "（无描述）",
                                    size=12, color="text3", wrap=True))
        if deps_note:
            info.Children.Append(W.text(f"依赖：{deps_note}", size=11,
                                        color="text3", wrap=True))

        busy = module_id in self._busy
        if local is None:
            fg, bg, status = ("accent_text", "accent_soft", "可下载")
        elif version_key(local["version"]) < version_key(remote_v):
            fg, bg, status = ("warning", "warning_soft", "可更新")
        elif version_key(local["version"]) > version_key(remote_v):
            fg, bg, status = ("text3", "track", "本地更新")
        else:
            fg, bg, status = ("success", "success_soft", "已安装")

        actions = W.stack(horizontal=True, spacing=8, v="center")
        if busy:
            actions.Children.Append(W.pill("处理中…", "text3", "track"))
        elif local is None:
            actions.Children.Append(W.text_button(
                "下载", symbol="Download", accent=True,
                on_click=lambda s, e, ent=entry: self._download(ent)))
        elif version_key(local["version"]) < version_key(remote_v):
            actions.Children.Append(W.text_button(
                f"更新到 v{remote_v}", symbol="Sync",
                on_click=lambda s, e, ent=entry: self._download(ent, update=True)))

        side = W.stack(horizontal=True, spacing=8, v="center")
        side.Children.Append(W.pill(status, fg, bg))
        side.Children.Append(actions)
        head = W.grid(W.auto(), W.star(1), W.auto())
        head.Children.Append(W.put(tile, 0))
        head.Children.Append(W.put(info, 1))
        head.Children.Append(W.put(side, 2))
        return W.card(head)

    def _fetch_online(self, *, force: bool = False) -> None:
        self.shell.logs.append("正在获取在线模块列表…")

        def worker() -> None:
            store = self.shell.engine.modules.store
            try:
                store.fetch_market(force=force)
            except Exception as exc:
                self.shell.ui_queue.put(lambda: self.shell.logs.append(
                    f"获取在线模块列表失败: {exc}"))
            self.shell.ui_queue.put(self.rebuild)

        threading.Thread(target=worker, daemon=True).start()

    def _download(self, entry: dict, *, update: bool = False) -> None:
        module_id = entry["id"]
        manager = self.shell.engine.modules
        if module_id in self._busy:
            return

        def do_download() -> None:
            def worker() -> None:
                try:
                    dest = manager.store.download(module_id, entry)
                    manager.discover()
                    self.shell.ui_queue.put(lambda: self.shell.logs.append(
                        f"模块已{'更新' if update else '下载'}: "
                        f"{entry['name']} v{entry['version']} → {dest}"))
                    fut = self.shell.engine.submit(manager.prepare(module_id))
                    fut.add_done_callback(lambda f, mid=module_id:
                                          self._prepare_done(f, mid))
                except Exception as exc:
                    self.shell.ui_queue.put(lambda: self.shell.logs.append(
                        f"模块下载失败: {exc}"))
                finally:
                    self._busy.discard(module_id)
                    self.shell.ui_queue.put(self.rebuild)

            threading.Thread(target=worker, daemon=True).start()

        if update and (manager.meta(module_id) or {}).get("loaded"):
            self._busy.add(module_id)
            self.rebuild()

            def _unload_done(fut) -> None:
                exc = fut.exception()
                if exc is not None:
                    self._busy.discard(module_id)
                    self.shell.ui_queue.put(lambda: self.shell.logs.append(
                        f"卸载模块失败: {exc!r}"))
                    self.shell.ui_queue.put(self.rebuild)
                    return
                do_download()

            fut = self.shell.engine.submit(manager.uninstall(module_id))
            fut.add_done_callback(_unload_done)
            return
        self._busy.add(module_id)
        self.rebuild()
        do_download()

    def _prepare_done(self, fut, module_id: str) -> None:
        exc = fut.exception()
        if exc is not None:
            self.shell.ui_queue.put(lambda: self.shell.logs.append(
                f"模块 {module_id} 自动安装失败: {exc!r}（可点「安装依赖」后重试）"))
        else:
            self.shell.ui_queue.put(lambda: self.shell.logs.append(
                f"模块 {module_id} 已安装（未启用），点「启动」开始运行"))
        self.shell.ui_queue.put(self.rebuild)

    def _install_deps(self, module_id: str) -> None:
        manager = self.shell.engine.modules
        self.shell.logs.append(f"正在安装模块 {module_id} 的依赖…")

        def _pip_log(line: str) -> None:
            self.shell.logs.append(f"[pip] {line}")

        def worker() -> None:
            try:
                ok, still, _ = manager.store.ensure_dependencies(
                    module_id, log=_pip_log)
            except Exception as exc:
                self.shell.ui_queue.put(lambda: self.shell.logs.append(
                    f"依赖安装失败: {exc}"))
                return
            if still:
                self.shell.ui_queue.put(lambda: self.shell.logs.append(
                    f"依赖安装失败（{'、'.join(still)}），可检查网络后重试"))
            else:
                self.shell.ui_queue.put(lambda: self.shell.logs.append(
                    f"模块 {module_id} 依赖就绪"))
            self.shell.ui_queue.put(self.rebuild)

        threading.Thread(target=worker, daemon=True).start()

    def _delete(self, module_id: str) -> None:
        async def _confirm_delete() -> None:
            ok = await confirm_dialog(
                self.shell, "删除模块文件",
                f"将从磁盘删除模块 {module_id} 的全部文件（配置保留）。\n"
                f"之后可随时从「在线模块」重新下载。", primary="删除")
            if not ok:
                return

            def worker() -> None:
                try:
                    self.shell.engine.modules.delete_module(module_id)
                except Exception as exc:
                    self.shell.ui_queue.put(
                        lambda: self.shell.logs.append(f"删除模块失败: {exc}"))
                    return
                self.shell.ui_queue.put(self.rebuild)

            threading.Thread(target=worker, daemon=True).start()

        asyncui.create_task(_confirm_delete())

    def _run_module_action(self, coro, done_msg: str) -> None:
        engine = self.shell.engine

        def _done(fut) -> None:
            exc = fut.exception()
            if exc is not None:
                engine._log(f"模块操作失败: {exc!r}")
            else:
                self.shell.logs.append(done_msg)
            self.shell.ui_queue.put(self.rebuild)

        fut = engine.submit(coro)
        fut.add_done_callback(_done)

    def _install(self, module_id: str) -> None:
        self._run_module_action(
            self.shell.engine.modules.install(module_id),
            f"模块已安装并启动: {module_id}")

    def _deactivate(self, module_id: str) -> None:
        self._run_module_action(
            self.shell.engine.modules.deactivate(module_id),
            f"模块已停止: {module_id}")

    def _uninstall(self, module_id: str) -> None:
        self._run_module_action(
            self.shell.engine.modules.uninstall(module_id),
            f"模块已卸载: {module_id}")

    def _apply_defaults(self, module_id: str) -> None:
        """快捷配置：把模块自带的默认事件流 / 按键映射套用为当前配置。"""
        try:
            notes = self.shell.engine.modules.apply_module_default_profiles(
                module_id)
        except Exception as exc:
            import traceback
            self.shell.logs.append(
                f"模块 {module_id} 快捷配置失败:\n{traceback.format_exc()}")
            return
        for line in notes:
            self.shell.logs.append(line)
        self.rebuild()

    def _game_mod_row(self, module_id: str) -> object:
        engine = self.shell.engine
        cfg = engine.modules.settings_for(module_id)
        box = W.text_box(text=str(cfg.get("mods_root") or ""),
                         placeholder="游戏根目录（含 BepInEx），可手动输入或自动扫描",
                         width=330)

        def _scan(sender, args) -> None:
            self.shell.logs.append("正在扫描游戏目录…")

            def worker() -> None:
                marker = str(((engine.modules.meta(module_id) or {})
                              .get("mods") or {}).get("marker") or "")
                found = engine.modules.scan_game_roots(marker) if marker else []

                def apply() -> None:
                    if found:
                        box.Text = found[0]
                        self.shell.logs.append(
                            f"扫描到 {len(found)} 处游戏目录，已填入第一处：{found[0]}")
                    else:
                        self.shell.logs.append(
                            "未扫描到游戏目录，请手动输入游戏根目录后安装")

                self.shell.ui_queue.put(apply)

            threading.Thread(target=worker, daemon=True).start()

        def _install(sender, args) -> None:
            self._install_game_mod(module_id, (box.Text or "").strip() or None)

        row = W.stack(horizontal=True, spacing=8, v="center", h="stretch")
        row.Children.Append(W.text("游戏模组", size=11, color="text3", v="center"))
        row.Children.Append(box)
        row.Children.Append(W.text_button("自动扫描", symbol="Find",
                                          on_click=_scan))
        row.Children.Append(W.text_button("安装游戏模组", symbol="Download",
                                          accent=True, on_click=_install))
        return row

    def _install_game_mod(self, module_id: str,
                          root: str | None = None) -> None:
        self.shell.logs.append("正在定位游戏目录…")

        def worker() -> None:
            resolved, note = (root, "") if root \
                else self._resolve_game_root(module_id)
            if resolved is None:
                self.shell.ui_queue.put(
                    lambda: asyncui.create_task(
                        self._pick_and_install_game_mod(module_id)))
                return
            try:
                count = self.shell.engine.modules.install_game_mod(
                    module_id, resolved)
            except Exception as exc:
                self.shell.logs.append(f"游戏模组安装失败: {exc}")
                return

            dest = str((self.shell.engine.modules.meta(module_id)
                        or {}).get("mods", {}).get("dest") or "")

            def done() -> None:
                self._remember_mods_root(module_id, resolved)
                self.shell.logs.append(f"游戏模组已安装（{count} 个文件）→ "
                                       f"{resolved}\\{dest}{note}")
                self.rebuild()

            self.shell.ui_queue.put(done)

        threading.Thread(target=worker, daemon=True).start()

    def _resolve_game_root(self, module_id: str) -> tuple[str | None, str]:
        modules = self.shell.engine.modules
        cfg = modules.settings_for(module_id)
        meta = modules.meta(module_id) or {}
        marker = str((meta.get("mods") or {}).get("marker") or "")
        remembered = str(cfg.get("mods_root") or "").strip()
        if remembered and os.path.isdir(remembered):
            if (os.path.isdir(os.path.join(remembered, "BepInEx"))
                    or (marker and os.path.isfile(
                        os.path.join(remembered, marker)))
                    or any(name.lower().endswith(".exe")
                           for name in self._safe_listdir(remembered))):
                return remembered, ""
        if marker:
            candidates = modules.scan_game_roots(marker)
            if candidates:
                return candidates[0], f"（自动扫描命中 {len(candidates)} 处，取第一处）"
        return None, ""

    def _safe_listdir(self, path: str) -> list[str]:
        try:
            return os.listdir(path)
        except OSError:
            return []

    def _remember_mods_root(self, module_id: str, root: str) -> None:
        cfg = self.shell.engine.modules.settings_for(module_id)
        if cfg.get("mods_root") != root:
            cfg["mods_root"] = root
            cfg.save()

    async def _pick_and_install_game_mod(self, module_id: str) -> None:
        ok = await confirm_dialog(
            self.shell, "未自动找到游戏目录",
            "没有在已保存路径和本机磁盘浅层扫描中定位到游戏。\n\n"
            "点击「手动指定」选择游戏根目录或游戏主程序（exe）：缺 BepInEx 时"
            "会自动安装内置的 BepInEx 5 发行包，再释放安装模组。",
            primary="手动指定", close="取消")
        if not ok:
            self.shell.logs.append("已取消游戏模组安装")
            return
        picked = pick_open_path("选择游戏主程序", wildcard=_EXE_FILTER)
        if not picked:
            self.shell.logs.append("已取消游戏模组安装")
            return
        root = os.path.dirname(picked)
        try:
            count = self.shell.engine.modules.install_game_mod(module_id, root)
        except Exception as exc:
            self.shell.logs.append(f"游戏模组安装失败: {exc}")
            return
        self._remember_mods_root(module_id, root)
        dest = str((self.shell.engine.modules.meta(module_id)
                    or {}).get("mods", {}).get("dest") or "")
        self.shell.logs.append(f"游戏模组已安装（{count} 个文件）→ {root}\\{dest}")



from __future__ import annotations

import os
import sys
import threading
import time

from win32more import asyncui
from win32more.Microsoft.UI.Xaml import Thickness
from win32more.Microsoft.UI.Xaml.Controls import Page
from win32more.Windows.Foundation import Uri
from win32more.winui3 import XamlClass

from module_store import requirement_satisfied, version_key
from ui import nav, theme, widgets as W
from ui.dialogs import confirm_dialog, pick_open_path
from ui.paths import xaml

_EXE_FILTER = "可执行文件 (*.exe)\0*.exe\0所有文件 (*.*)\0*.*\0"
_DOCS_URL = "https://github.com/KelierAndes/dgstudio-modules-market/blob/main/EXTENSIONS.md"


def online_section_state(store) -> tuple[str, list]:
    """在线模块区状态（以 store 为唯一数据源，避免页面副本失步）：

    * ``ready`` —— store.entries 有清单，渲染模块卡片；
    * ``empty`` —— 已成功获取但清单为空；
    * ``idle`` —— 尚未获取 / 获取失败（配合 store.last_error 提示）。
    """
    if store.entries:
        return "ready", store.entries
    if store.fetched_at:
        return "empty", []
    return "idle", []


class ModulesPage(XamlClass, Page):
    def __init__(self, shell):
        super().__init__()
        self.shell = shell
        self._sig = None
        self._last = 0.0
        self._fetch_started = False
        self._busy: set[str] = set()
        self.LoadComponentFromFile(xaml("ModulesPage.xaml"), encoding="utf-8")
        self.rebuild()

    def tick(self) -> None:
        now = time.monotonic()
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

    def rebuild(self) -> None:
        self._sig = self._module_sig()
        self._last = time.monotonic()
        W.page_head(
            self.HeadHost,
            {"title": "模块",
             "subtitle": "联动模块从 dgstudio-modules-market 仓库按需下载，本地实时装卸无需重启",
             "breadcrumb": ["控制台", "模块"]},
            actions=[
                W.text_button("获取在线列表", symbol="Download",
                              on_click=lambda s, e: self._fetch_online(force=True)),
                W.text_button("扫描模块目录", symbol="Refresh",
                              on_click=lambda s, e: self._rescan()),
            ],
        )
        self.ListHost.Content = self._module_list()
        if not self._fetch_started:
            self._fetch_started = True
            self._fetch_online(force=False)

    # -------------------------------------------------------------- 本地模块

    def _rescan(self) -> None:
        found = self.shell.engine.modules.discover()
        self.shell.logs.append(
            f"已扫描模块目录 {self.shell.engine.modules.base_dir}，发现 {len(found)} 个模块")
        self.rebuild()

    def _module_list(self) -> object:
        modules = self.shell.engine.modules.list_modules()
        host = W.stack(spacing=10, h="stretch")
        if not modules:
            host.Children.Append(self._hint_card(
                "模块目录为空",
                f"可从下方「在线模块」一键下载，或将模块文件夹放入 "
                f"{self.shell.engine.modules.base_dir} 后点「扫描模块目录」。"))
        for meta in modules:
            host.Children.Append(self._module_card(meta))
        host.Children.Append(self._section_head(
            "在线模块",
            "清单来自 dgstudio-modules-market 仓库，点击「下载」取回模块文件，"
            "再「安装并启动」即可使用。"))
        host.Children.Append(self._online_list())
        host.Children.Append(self._hint_card(
            "开发自己的模块",
            "模块是 modules/<id>/plugin.py 中的一个 ModuleBase 子类，可获得强度参数、"
            "波形、开火、急停、临时变量（ctx.set_temp/get_temp，META[\"temps\"] 声明"
            "展示为模块维护行）等公开 API。接口说明与示例见 dgstudio-modules-market "
            "仓库的开发文档，模块依赖由 META[\"dependencies\"] 声明、安装时自动补装。",
            link_label="dgstudio-modules-market · 模块开发文档（EXTENSIONS.md）"))
        return host

    def _section_head(self, title: str, subtitle: str) -> object:
        inner = W.stack(spacing=2, margin=Thickness(4, 8, 4, 0))
        inner.Children.Append(W.text(title, size=14, bold=W.SEMIBOLD))
        inner.Children.Append(W.text(subtitle, size=11, color="text3", wrap=True))
        return inner

    def _module_card(self, meta: dict) -> object:
        module_id = meta["id"]
        engine = self.shell.engine

        status_fg, status_bg, status_text = (
            ("success", "success_soft", "运行中") if meta["running"]
            else ("accent_text", "accent_soft", "已加载") if meta["loaded"]
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
                                                      on_click=lambda s, e, mid=module_id: self._stop(mid)))
            else:
                buttons.Children.Append(W.text_button("启动", symbol="Play",
                                                      on_click=lambda s, e, mid=module_id: self._start(mid)))
            buttons.Children.Append(W.text_button("卸载", symbol="Remove",
                                                  on_click=lambda s, e, mid=module_id: self._uninstall(mid)))
        if meta["config"] and meta["enabled"]:
            # 已安装且声明配置项的模块在联动页有对应卡片，提供跳转入口
            buttons.Children.Append(nav.link("联动设置", "link"))

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
        if engine.modules.module_mods_dir(module_id):
            inner.Children.Append(W.divider())
            inner.Children.Append(self._game_mod_row(module_id))
        return W.card(inner)

    def _deps_row(self, meta: dict) -> object | None:
        """依赖声明行：逐项 ✓/缺 状态；缺必装依赖时给出「安装依赖」按钮。"""
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

    # -------------------------------------------------------------- 在线模块

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
                    self.shell.ui_queue.put(lambda: (
                        self.shell.logs.append(
                            f"模块已{'更新' if update else '下载'}: "
                            f"{entry['name']} v{entry['version']} → {dest}"),
                        self.rebuild))
                except Exception as exc:
                    self.shell.ui_queue.put(lambda: self.shell.logs.append(
                        f"模块下载失败: {exc}"))
                finally:
                    self._busy.discard(module_id)
                    self.shell.ui_queue.put(self.rebuild)

            threading.Thread(target=worker, daemon=True).start()

        if update and (manager.meta(module_id) or {}).get("loaded"):
            # 运行中的模块先卸载再替换文件
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

    # -------------------------------------------------------- 依赖安装/删除

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

            # 删除含短重试（等待杀软等瞬时占用释放），后台执行避免卡界面
            threading.Thread(target=worker, daemon=True).start()

        asyncui.create_task(_confirm_delete())

    # -------------------------------------------------------- 模块装卸操作

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

    def _uninstall(self, module_id: str) -> None:
        self._run_module_action(
            self.shell.engine.modules.uninstall(module_id),
            f"模块已卸载: {module_id}")

    def _start(self, module_id: str) -> None:
        self._run_module_action(
            self.shell.engine.modules.start(module_id),
            f"模块已启动: {module_id}")

    def _stop(self, module_id: str) -> None:
        self._run_module_action(
            self.shell.engine.modules.stop(module_id),
            f"模块已停止: {module_id}")

    # -------------------------------------------------- 一键安装游戏模组

    def _game_mod_row(self, module_id: str) -> object:
        """游戏模组安装行：路径输入框 + 自动扫描 + 一键安装。"""
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
        """模块携带的 mods/ 释放到游戏目录：路径框已填 / 记住的路径直接装，
        否则后台扫描，再不行转手动指定。全部文件操作在后台线程执行。"""
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
            # 游戏根目录即可（BepInEx 缺失时安装流程会自动装）
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

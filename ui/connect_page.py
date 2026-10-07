
from __future__ import annotations

import io
import time

import qrcode
from PIL import Image as PILImage

from win32more.Microsoft.UI.Xaml import Thickness, Visibility
from win32more.Microsoft.UI.Xaml.Controls import Page
from win32more.winui3 import XamlClass

from dglab.ble import BleClient
from dglab.state import family_of
from ui import live, theme, widgets as W
from ui.paths import xaml

_CHANNEL_SYMBOL = {"v4": "Link", "v3": "Remote", "ble": "CellPhone"}

class ConnectPage(XamlClass, Page):
    def __init__(self, shell):
        super().__init__()
        self.shell = shell
        self._scan_results: list[dict] = []
        self._selected_scan: str | None = None
        self._cards: dict[str, dict] = {}
        self._last_qr = ""
        self._backend_seen = None
        self._slots_seen = None
        self._last_device_render = 0.0
        self._lan_ip_cache = None
        self._lan_ip_at = None
        self.LoadComponentFromFile(xaml("ConnectPage.xaml"), encoding="utf-8")
        self.rebuild()

    def tick(self) -> None:
        state = self.shell.state
        slots = tuple(sorted(state.slots))
        now = time.monotonic()
        if state.backend != self._backend_seen or slots != self._slots_seen:
            self.rebuild()
            return
        self._update_status()
        self._update_pairing()
        if now - self._last_device_render >= 0.5:
            self._last_device_render = now
            self._update_devices()
            self._update_saved()

    def on_notify(self) -> None:
        self.rebuild()

    def rebuild(self) -> None:
        state = self.shell.state
        self._backend_seen = state.backend
        self._slots_seen = tuple(sorted(state.slots))

        W.page_head(
            self.HeadHost,
            {"title": "连接",
             "subtitle": "按通道管理：Socket V4 / V3 中继与蓝牙直连，V4/V3 扫码配对，蓝牙可扫描多台",
             "breadcrumb": ["控制台", "连接"]},
            actions=[
                W.text_button("重新扫描蓝牙", symbol="Scan",
                              on_click=lambda s, e: self._scan()),
                W.text_button("断开当前连接", symbol="DisconnectDrive",
                              on_click=lambda s, e: self._disconnect()),
                W.estop_button("急停全部设备",
                               on_click=lambda s, e: self._estop()),
            ],
        )
        self.HintBar.Message = (
            "Socket V4 / V3：勾选本地中继后 App 扫码直连本机，否则填远程中继地址；"
            "蓝牙直连：先扫描，再选择设备连接，可重复连接多台。输出调节前往「控制」页。"
        )

        host = self.CardsHost
        host.Children.Clear()
        self._cards.clear()
        host.Children.Append(self._relay_card("v4"))
        host.Children.Append(self._relay_card("v3"))
        host.Children.Append(self._ble_card())

    def _relay_card(self, key: str) -> object:
        engine = self.shell.engine
        is_v4 = key == "v4"
        title = "Socket V4 连接" if is_v4 else "Socket V3 连接"
        subtitle = ("DG-Lab 4.0 App（推荐）· 支持郊狼/负鼠/灵猫" if is_v4
                    else "官方 / 自建中继 · 兼容 3.x 固件（仅郊狼 3.0）")
        help_text = (
            "服务器地址、本地中继与端口在「设置」页配置；"
            "勾选本地中继时 App 扫码直连本机局域网地址，否则连到所填远程中继。"
            if is_v4 else
            "服务器地址、本地中继与端口在「设置」页配置；"
            "使用 DG-Lab 3.x App 或 4.0 App 的 Socket 控制入口扫码（仅郊狼 3.0 设备）。"
        )
        summary = W.text(self._relay_summary_text(key),
                         size=12, color="text2", family="Consolas", trimming=True)

        refs: dict = {}
        head = W.card_head(title, subtitle=subtitle, symbol=_CHANNEL_SYMBOL[key], accent=True)
        pill_host = W.box(child=W.pill("未连接", "text3", "track"), v="center", h="right")

        head_g = W.grid(W.star(1), W.auto())
        head_g.Children.Append(W.put(head, 0))
        head_g.Children.Append(W.put(_gap(pill_host, 14), 1))

        connect_btn = W.text_button(
            "连接并生成配对二维码", symbol="Link", accent=True,
            on_click=lambda s, e: self._connect_relay(key))
        disconnect_btn = W.text_button(
            "断开连接", symbol="DisconnectDrive",
            on_click=lambda s, e: self._disconnect())

        qr_image = W.image(width=190, height=190)
        status_tb = W.text("等待连接…", size=13, bold=W.SEMIBOLD, wrap=True)
        ids_tb = W.text("", size=11, color="text2", family="Consolas", wrap=True)
        payload_tb = W.text("二维码内容会显示在这里", size=11, color="text2",
                            family="Consolas", wrap=True)
        hint_tb = W.text("扫码后设备接入，状态会自动更新", size=11, color="text3", wrap=True)

        info = W.stack(spacing=6, h="stretch")
        info.Children.Append(status_tb)
        info.Children.Append(ids_tb)
        info.Children.Append(W.text("配对内容", size=11, color="text3"))
        info.Children.Append(W.box(
            corner=6, padding=Thickness(10, 7, 10, 7),
            background=theme.brush("track"),
            child=payload_tb, h="stretch"))
        info.Children.Append(hint_tb)

        pairing_body = W.grid(W.auto(), W.star(1))
        pairing_body.ColumnSpacing = 18
        pairing_body.Children.Append(W.put(_gap(
            W.box(width=200, height=200, corner=6, background=theme.brush("qr_bg"),
                  child=qr_image), 0), 0))
        pairing_body.Children.Append(W.put(info, 1))

        pairing = W.collapsible(
            "配对二维码 / 连接信息", pairing_body, symbol="Link",
            subtitle="等待连接", expanded=False)

        devices_host = W.stack(spacing=0, h="stretch")

        inner = W.stack(spacing=12, h="stretch")
        inner.Children.Append(head_g)
        inner.Children.Append(W.divider())
        inner.Children.Append(W.text("连接接口", size=11, bold=W.SEMIBOLD, color="text3"))
        inner.Children.Append(summary)
        inner.Children.Append(W.text(help_text, size=11, color="text3", wrap=True))
        action_row = W.stack(horizontal=True, spacing=8, v="center")
        action_row.Children.Append(connect_btn)
        action_row.Children.Append(disconnect_btn)
        inner.Children.Append(action_row)
        inner.Children.Append(W.divider())
        inner.Children.Append(self._devices_block(devices_host))
        inner.Children.Append(pairing)

        refs.update(pill_host=pill_host, qr_image=qr_image, status=status_tb,
                    ids=ids_tb, payload=payload_tb, pairing=pairing,
                    devices_host=devices_host, summary=summary)
        self._cards[key] = refs
        return W.card(inner)

    def _ble_card(self) -> object:
        engine = self.shell.engine
        refs: dict = {}
        head = W.card_head("蓝牙直连", subtitle="GATT 特征写入 · 郊狼 / 负鼠 / 灵猫，可同时连接多台",
                           symbol=_CHANNEL_SYMBOL["ble"], accent=True)
        pill_host = W.box(child=W.pill("未连接", "text3", "track"), v="center", h="right")
        head_g = W.grid(W.star(1), W.auto())
        head_g.Children.Append(W.put(head, 0))
        head_g.Children.Append(W.put(_gap(pill_host, 14), 1))

        actions = W.stack(horizontal=True, spacing=8, v="center")
        actions.Children.Append(W.text_button("扫描设备", symbol="Scan", accent=True,
                                              on_click=lambda s, e: self._scan()))
        actions.Children.Append(W.text_button("连接选中设备", symbol="Link",
                                              on_click=lambda s, e: self._connect_ble()))
        actions.Children.Append(W.text_button("断开选中", symbol="DisconnectDrive",
                                              on_click=lambda s, e: self._disconnect_selected()))
        actions.Children.Append(W.text_button("删除记录", symbol="Delete",
                                              on_click=lambda s, e: self._forget_saved()))

        scan_host = W.stack(spacing=0, h="stretch")
        scan_caption = W.text("尚未扫描：点击「扫描设备」开始 (约 6 秒)",
                              size=12, color="text3", v="center")

        auto_switch = W.switch(bool(engine.config.get("auto_reconnect", True)))

        def _auto_changed(sender, args):
            try:
                engine.config["auto_reconnect"] = bool(auto_switch.IsOn)
            except AttributeError:
                pass

        auto_switch.Toggled += _auto_changed
        auto_row = W.stack(horizontal=True, spacing=8, v="center")
        auto_row.Children.Append(W.text("自动重连（连接意外断开时每 5 秒重试）",
                                        size=12, color="text2", v="center"))
        auto_row.Children.Append(auto_switch)

        devices_host = W.stack(spacing=0, h="stretch")
        saved_rows_host = W.stack(spacing=0, h="stretch")

        saved = W.stack(spacing=8, h="stretch")
        saved.Children.Append(W.text("已保存设备（点击选中后可连接 / 删除记录）", size=11,
                                     bold=W.SEMIBOLD, color="text3"))
        saved.Children.Append(W.box(
            corner=6, border=theme.brush("stroke"), background=theme.brush("track"),
            child=saved_rows_host, h="stretch"))
        saved.Children.Append(auto_row)

        inner = W.stack(spacing=12, h="stretch")
        inner.Children.Append(head_g)
        inner.Children.Append(W.divider())
        inner.Children.Append(W.text("扫描与设备选择", size=11, bold=W.SEMIBOLD, color="text3"))
        inner.Children.Append(actions)
        inner.Children.Append(W.box(
            corner=6, border=theme.brush("stroke"), background=theme.brush("track"),
            child=scan_host, h="stretch"))
        inner.Children.Append(scan_caption)
        inner.Children.Append(W.divider())
        inner.Children.Append(self._devices_block(devices_host))
        inner.Children.Append(W.divider())
        inner.Children.Append(saved)
        inner.Children.Append(W.text(
            "支持：郊狼 3.0 (47L121000)、郊狼 2.0 (ESTIM01)、负鼠 (47L127000)、灵猫 (47L124000)。"
            "扫描前请确保设备未被手机 App 占用。",
            size=11, color="text3", wrap=True))

        refs.update(pill_host=pill_host, scan_host=scan_host, scan_caption=scan_caption,
                    devices_host=devices_host, saved_rows_host=saved_rows_host)
        self._cards["ble"] = refs
        self._update_saved()
        self._render_scan_rows()
        return W.card(inner)

    def _devices_block(self, rows_host):
        head = W.stack(horizontal=True, spacing=8, v="center")
        head.Children.Append(W.text("已接入设备", size=11, bold=W.SEMIBOLD, color="text3"))
        inner = W.stack(spacing=8, h="stretch")
        inner.Children.Append(head)
        inner.Children.Append(W.box(
            corner=6, border=theme.brush("stroke"), background=theme.brush("track"),
            child=rows_host, h="stretch"))
        return inner

    def _relay_summary_text(self, key: str) -> str:
        engine = self.shell.engine
        is_v4 = key == "v4"
        relay_cfg = engine.config["relay"]
        local = bool(relay_cfg.get("v4_local" if is_v4 else "v3_local"))
        port = relay_cfg.get("v4_port" if is_v4 else "v3_port", 9998 if is_v4 else 9999)
        if local:
            return f"ws://{self._lan_ip()}:{port} · 本地中继"
        url = engine.config["v4_url" if is_v4 else "v3_url"]
        return f"{url or '（未配置地址）'} · 远程中继"

    def _lan_ip(self) -> str:
        now = time.monotonic()
        if self._lan_ip_at is None or now - self._lan_ip_at > 5.0:
            from app import local_lan_ip

            self._lan_ip_at = now
            self._lan_ip_cache = local_lan_ip()
        return self._lan_ip_cache

    def _update_status(self) -> None:
        state = self.shell.state
        for key in ("v4", "v3"):
            refs = self._cards.get(key)
            if refs is not None and refs.get("summary") is not None:
                text = self._relay_summary_text(key)
                if refs["summary"].Text != text:
                    refs["summary"].Text = text
        active = state.backend
        for key, refs in self._cards.items():
            if key == "ble":
                connected = active == "ble" and bool(state.slots)
                label = f"已连接 {len(state.slots)} 台" if connected else "未连接"
                fg, bg = ("success", "success_soft") if connected else ("text3", "track")
            elif key == active:
                paired = state.paired
                label = ("已连接" if paired else "等待扫码配对")
                fg, bg = (("success", "success_soft") if paired
                          else ("warning", "warning_soft"))
            else:
                label = "未启用"
                fg, bg = ("text3", "track")
            sig = (label, fg, bg)
            if refs.get("pill_sig") == sig:
                continue
            refs["pill_sig"] = sig
            refs["pill_host"].Child = W.pill(label, fg, bg, dot_color=fg if fg != "text3" else None)

    def _update_pairing(self) -> None:
        state = self.shell.state
        for key in ("v4", "v3"):
            refs = self._cards.get(key)
            if key != state.backend:
                if refs.get("pairing_open"):
                    refs["pairing_open"] = False
                    W.set_expanded(refs["pairing"], False)
                    refs["status"].Text = "未连接"
                    refs["ids"].Text = ""
                    refs["payload"].Text = "二维码内容会显示在这里"
                    self._last_qr = ""
                continue
            if state.qr_text and state.qr_text != self._last_qr:
                self._last_qr = state.qr_text
                self._show_qr(refs["qr_image"], state.qr_text)
                refs["payload"].Text = state.qr_text
                W.set_expanded(refs["pairing"], True)
                refs["pairing_open"] = True
            if not state.qr_text and refs.get("pairing_open"):
                refs["pairing_open"] = False
                W.set_expanded(refs["pairing"], False)
                self._last_qr = ""
            ids = []
            if state.client_id:
                ids.append(f"本机 ID: {state.client_id}")
            if state.target_id:
                ids.append(f"对端: {state.target_id}")
            ids_text = "\n".join(ids)
            if refs["ids"].Text != ids_text:
                refs["ids"].Text = ids_text
            status_text = (state.status_text
                           or ("等待扫码配对" if state.qr_text else "未连接"))
            if refs["status"].Text != status_text:
                refs["status"].Text = status_text
            refs["pairing"].Visibility = Visibility.Visible

    def _update_devices(self) -> None:
        state = self.shell.state
        for key in ("v4", "v3", "ble"):
            refs = self._cards.get(key)
            if refs is None:
                continue
            host = refs["devices_host"]
            active = key == state.backend
            slots = [(sid, state.slots[sid]) for sid in sorted(state.slots)] if active else []
            sig = (active, tuple(sid for sid, _ in slots), self._selected_scan,
                   tuple((s.battery or 0) for _, s in slots))
            if refs.get("devices_sig") == sig and host.Children.Size > 0:
                continue
            refs["devices_sig"] = sig
            host.Children.Clear()
            if not slots:
                host.Children.Append(self._device_row_placeholder())
            for i, (sid, slot) in enumerate(slots):
                if i:
                    host.Children.Append(W.divider())
                host.Children.Append(self._device_row(sid, slot))

    def _device_row_placeholder(self):
        return W.box(height=40, padding=Thickness(10, 0, 10, 0),
                     child=W.text("该通道暂无已接入设备（点击行可选中后断开）",
                                  size=12, color="text3", v="center"),
                     h="stretch")

    def _device_row(self, sid: str, slot):
        family = family_of(slot.type)
        selected = sid == self._selected_scan
        g = W.grid(W.fixed(26), W.star(1), W.auto())
        g.ColumnSpacing = 10
        mark = W.box(
            width=14, height=14, corner=7,
            border=theme.brush("accent" if selected else "text3"),
            border_thickness=1.5,
            background=theme.brush("accent") if selected else None,
            v="center", h="center")
        g.Children.Append(W.put(mark, 0))
        names = W.stack(spacing=1, v="center")
        names.Children.Append(W.text(slot.name or slot.type or sid, size=13,
                                     bold=W.SEMIBOLD, trimming=True))
        names.Children.Append(W.text(
            f"{live.FAMILY_LABELS[family]} · {slot.type} · {live.battery_text(slot)}",
            size=11, color="text3", trimming=True))
        g.Children.Append(W.put(names, 1))
        g.Children.Append(W.put(W.pill(
            "已选中" if selected else "在线",
            "accent_text" if selected else "success",
            "accent_soft" if selected else "success_soft",
            dot_color=None if selected else "success"), 2))

        def _pick(sender, args, target=sid):
            self._selected_scan = target
            self._update_devices()

        row = W.box(height=48, padding=Thickness(10, 0, 10, 0), child=g)
        button = W.button(row, h="stretch")
        button.HorizontalContentAlignment = W._HALIGN["stretch"]
        button.Click += _pick
        return button

    def _connect_relay(self, key: str) -> None:
        engine = self.shell.engine
        is_v4 = key == "v4"
        relay_cfg = engine.config["relay"]
        local_key = "v4_local" if is_v4 else "v3_local"
        port_key = "v4_port" if is_v4 else "v3_port"
        use_local = bool(relay_cfg.get(local_key))
        try:
            port = max(1, min(65535, int(relay_cfg.get(port_key, 9998 if is_v4 else 9999))))
        except (TypeError, ValueError):
            port = 9998 if is_v4 else 9999
        if use_local:
            self.shell.submit(engine.connect_v4_local(port) if is_v4
                              else engine.connect_v3_local(port))
        else:
            self.shell.submit(engine.connect_v4() if is_v4 else engine.connect_v3())

    def _scan(self) -> None:
        self.shell.logs.append("正在扫描蓝牙设备 (6 秒)…")
        refs = self._cards.get("ble")
        if refs is not None:
            refs["scan_caption"].Text = "扫描中…"
        fut = self.shell.engine.submit(self.shell.engine.ble_scan(6.0))

        def _done(f):
            try:
                results = f.result()
            except Exception as exc:
                self.shell.logs.append(f"扫描失败: {exc!r}")
                return
            self._scan_results = results
            self.shell.ui_queue.put(self._render_scan_rows)
            self.shell.logs.append(f"扫描完成，发现 {len(results)} 台 DG-Lab 设备")

        fut.add_done_callback(_done)

    def _render_scan_rows(self) -> None:
        refs = self._cards.get("ble")
        if refs is None:
            return
        host = refs["scan_host"]
        host.Children.Clear()
        results = self._scan_results
        refs["scan_caption"].Text = (
            f"已发现 {len(results)} 台 · {time.strftime('%H:%M:%S')}；点击行选中后连接"
            if results else "未发现 DG-Lab 设备：请确认设备已开机且未被手机 App 占用")
        if not results:
            host.Children.Append(W.box(height=40, padding=Thickness(10, 0, 10, 0),
                                       child=W.text("（空）", size=12, color="text3", v="center"),
                                       h="stretch"))
            return
        for i, dev in enumerate(results):
            if i:
                host.Children.Append(W.divider())
            host.Children.Append(self._scan_row(dev, i))

    def _scan_row(self, dev: dict, index: int):
        address = dev.get("address", "")
        selected = address == self._selected_scan

        def _pick(sender, args, addr=address):
            self._selected_scan = addr
            self._render_scan_rows()

        mark = W.box(
            width=14, height=14, corner=7,
            border=theme.brush("accent" if selected else "text3"),
            border_thickness=1.5,
            background=theme.brush("accent") if selected else None,
            v="center", h="center")
        info = W.stack(spacing=1, v="center")
        info.Children.Append(W.text(dev.get("name", "?"), size=13, bold=W.SEMIBOLD))
        kind = dev.get("kind_label", dev.get("kind", "?"))
        rssi = dev.get("rssi")
        info.Children.Append(W.text(
            f"{kind} · {address}" + (f" · RSSI {rssi}" if rssi is not None else ""),
            size=11, color="text3"))
        g = W.grid(W.fixed(26), W.star(1), W.auto())
        g.ColumnSpacing = 10
        g.Children.Append(W.put(mark, 0))
        g.Children.Append(W.put(info, 1))
        g.Children.Append(W.put(W.pill(
            "已选中" if selected else "可连接",
            "accent_text" if selected else "text3",
            "accent_soft" if selected else "track"), 2))
        row = W.box(height=48, padding=Thickness(10, 0, 10, 0), child=g)
        button = W.button(row, h="stretch")
        button.HorizontalContentAlignment = W._HALIGN["stretch"]
        button.Click += _pick
        return button

    def _connect_ble(self) -> None:
        address = self._selected_scan
        if not address:
            self.shell.logs.append("请先在列表中点击选择设备")
            return
        for dev in self._scan_results:
            if dev.get("address") == address:
                self.shell.submit(self.shell.engine.ble_connect(dev["address"], dev["kind"]))
                return
        for dev in self.shell.engine.saved_device_list():
            if dev.get("address") == address:
                self.shell.submit(self.shell.engine.ble_reconnect_saved(address))
                return
        self.shell.logs.append("所选设备不在扫描结果或已保存记录中，请重新扫描")

    def _update_saved(self) -> None:
        refs = self._cards.get("ble")
        if refs is None:
            return
        saved = self.shell.engine.saved_device_list()
        sig = (tuple(d.get("address") for d in saved), self._selected_scan)
        host = refs["saved_rows_host"]
        if refs.get("saved_sig") == sig and host.Children.Size > 0:
            return
        refs["saved_sig"] = sig
        host.Children.Clear()
        if not saved:
            host.Children.Append(W.box(
                height=40, padding=Thickness(10, 0, 10, 0),
                child=W.text("暂无保存记录：连接成功的设备会自动记录",
                             size=12, color="text3", v="center"),
                h="stretch"))
            return
        for i, dev in enumerate(saved):
            if i:
                host.Children.Append(W.divider())
            host.Children.Append(self._saved_row(dev))

    def _saved_row(self, dev: dict):
        address = dev.get("address", "")
        selected = address == self._selected_scan

        def _pick(sender, args, target=address):
            self._selected_scan = target
            self._update_saved()
            self._update_devices()

        g = W.grid(W.fixed(26), W.star(1), W.auto())
        g.ColumnSpacing = 10
        mark = W.box(
            width=14, height=14, corner=7,
            border=theme.brush("accent" if selected else "text3"),
            border_thickness=1.5,
            background=theme.brush("accent") if selected else None,
            v="center", h="center")
        g.Children.Append(W.put(mark, 0))
        names = W.stack(spacing=1, v="center")
        names.Children.Append(W.text(dev.get("name", "?"), size=13,
                                     bold=W.SEMIBOLD, trimming=True))
        names.Children.Append(W.text(
            f"{dev.get('kind_label', dev.get('kind', '?'))} · {address}",
            size=11, color="text3", trimming=True))
        g.Children.Append(W.put(names, 1))
        g.Children.Append(W.put(W.pill(
            "已选中" if selected else "已保存",
            "accent_text" if selected else "text3",
            "accent_soft" if selected else "track"), 2))
        row = W.box(height=48, padding=Thickness(10, 0, 10, 0), child=g)
        button = W.button(row, h="stretch")
        button.HorizontalContentAlignment = W._HALIGN["stretch"]
        button.Click += _pick
        return button

    def _forget_saved(self) -> None:
        address = self._selected_scan
        if not address:
            self.shell.logs.append("请先在列表中点击选择设备")
            return
        if not any(d.get("address") == address
                   for d in self.shell.engine.saved_device_list()):
            self.shell.logs.append("所选设备没有保存记录")
            return
        self.shell.engine.forget_device(address)

    def _disconnect_selected(self) -> None:
        address = self._selected_scan
        if not address:
            self.shell.logs.append("请先在扫描列表中点击选择要断开的设备")
            return

        async def _do():
            engine = self.shell.engine
            if not isinstance(engine._backend, BleClient):
                self.shell.logs.append("当前没有蓝牙直连设备")
                return
            await engine.ble_disconnect_device(address)
            self.shell.logs.append(f"已断开选中设备: {address}")

        self.shell.submit(_do())

    def _disconnect(self) -> None:
        async def _do():
            if self.shell.engine._backend is not None:
                await self.shell.engine._disconnect_backend()

        self.shell.submit(_do())

    def _estop(self) -> None:
        self.shell.submit(self.shell.engine.emergency_stop())

    def _show_qr(self, image, text: str) -> None:
        try:
            qr = qrcode.QRCode(box_size=8, border=2)
            qr.add_data(text)
            qr.make(fit=True)
            img: PILImage.Image = qr.make_image(fill_color="black",
                                               back_color="white").convert("RGB")
            buffer = io.BytesIO()
            img.save(buffer, "PNG")
            self.shell.set_image_bytes(image, buffer.getvalue())
        except Exception as exc:
            self.shell.logs.append(f"二维码生成失败: {exc!r}")

    def flush_config(self) -> None:
        pass


def _combo_item(content: str):
    from win32more.Microsoft.UI.Xaml.Controls import ComboBoxItem
    item = ComboBoxItem()
    item.Content = content
def _gap(el, left: float):
    if left:
        el.Margin = Thickness(left, 0, 0, 0)
    return el

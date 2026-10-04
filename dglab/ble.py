from __future__ import annotations

import asyncio
from typing import Any

from bleak import BleakClient, BleakScanner

from .monitor import WaveMonitor
from .state import EngineState, Slot, StateEvents
from .waves import (
    CONTINUOUS,
    CoyoteWaveform,
    FrameCycle,
    PULSE_STREAM,
    SILENT,
    frequency_to_xy,
    ovc_channel_pattern,
    resolve_wave_frames,
    trim_pulse_stream,
    wire_to_logical_freq,
)

V3_SERVICE = "0000180c-0000-1000-8000-00805f9b34fb"
V3_WRITE = "0000150a-0000-1000-8000-00805f9b34fb"
V3_NOTIFY = "0000150b-0000-1000-8000-00805f9b34fb"
V3_BATTERY = "00001500-0000-1000-8000-00805f9b34fb"

DEVICE_KINDS = {
    "47L121000": "coyote_v3",
    "47L127000": "ovc",
    "47L124000": "bmtr",
}
V2_NAME = "D-LAB ESTIM01"

KIND_LABELS = {
    "coyote_v2": "郊狼 Coyote V2",
    "coyote_v3": "郊狼 Coyote 3.0",
    "ovc": "负鼠 OVC 振动",
    "bmtr": "灵猫 BMTR 气压",
}
KIND_TYPE = {
    "coyote_v2": "COYOTE_020",
    "coyote_v3": "COYOTE_030",
    "ovc": "OVC_1",
    "bmtr": "BMTR_1",
}

BMTR_FF_SERVICE = "0000ff0a-0000-1000-8000-00805f9b34fb"
BMTR_FF_NOTIFY = "0000ff00-0000-1000-8000-00805f9b34fb"
BMTR_FF_WRITE = "0000ff01-0000-1000-8000-00805f9b34fb"

V2_BASE = "955A{:04x}-0FE2-F5AA-A094-84B8D4F3E8AD"
V2_PWM_AB2 = V2_BASE.format(0x1504)
V2_PWM_A34 = V2_BASE.format(0x1505)
V2_PWM_B34 = V2_BASE.format(0x1506)
V2_BATTERY = V2_BASE.format(0x1500)

CHANNELS = ("A", "B")
LOOP_INTERVAL = 0.1

OVC_B2_BODY = bytes.fromhex("FFFF00FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF0809")

LED_COLORS = {
    "off": 0x00,
    "yellow": 0x01,
    "magenta": 0x02,
    "purple": 0x03,
    "blue": 0x04,
    "cyan": 0x05,
    "green": 0x06,
}


def build_b0(seq: int, method: int, strength_a: int, strength_b: int,
             freq_a: list[int], wave_a: list[int],
             freq_b: list[int], wave_b: list[int]) -> bytes:
    frame = bytearray(20)
    frame[0] = 0xB0
    frame[1] = ((seq & 0x0F) << 4) | (method & 0x0F)
    frame[2] = strength_a & 0xFF
    frame[3] = strength_b & 0xFF
    frame[4:8] = bytes(max(0, min(255, f)) for f in freq_a)
    frame[8:12] = bytes(max(0, min(255, s)) for s in wave_a)
    frame[12:16] = bytes(max(0, min(255, f)) for f in freq_b)
    frame[16:20] = bytes(max(0, min(255, s)) for s in wave_b)
    return bytes(frame)


def build_ovc_b0(wave_a: list[int], wave_b: list[int]) -> bytes:
    frame = bytearray(20)
    frame[0] = 0xB0
    frame[8:12] = bytes(max(0, min(100, s)) for s in wave_a)
    frame[16:20] = bytes(max(0, min(100, s)) for s in wave_b)
    return bytes(frame)


def build_ovc_b3(a: int | None, b: int | None) -> bytes:
    return bytes(
        [0xB3, 0xFF if a is None else (max(0, min(200, a)) & 0xFF),
         0xFF if b is None else (max(0, min(200, b)) & 0xFF)]
    )


def build_ovc_b2(a: int, b: int) -> bytes:
    return bytes([0xB2]) + OVC_B2_BODY + bytes(
        [max(0, min(200, a)) & 0xFF, max(0, min(200, b)) & 0xFF]
    )


def build_ovc_50(enable_buttons: bool = True, color: int = 1) -> bytes:
    return bytes([0x50, color & 0xFF, 0x01 if enable_buttons else 0x00])


def build_coyote_50(color: int = 1) -> bytes:
    return bytes([0x50, color & 0xFF, 0x00]) + bytes(14)


def build_bmtr_50(enable_pressure: bool = True, color: int = 1) -> bytes:
    return bytes([0x50, color & 0xFF, 0xD0 if enable_pressure else 0x00]) + bytes(14)


def build_bmtr_66(reset_pressure: bool = False, orientation: int = 0) -> bytes:
    return bytes([0x66]) + bytes(9) + bytes([orientation & 0xFF]) + (
        (2).to_bytes(2, "big") if reset_pressure else (0).to_bytes(2, "big")
    )


def parse_bmtr_pressure(data: bytearray | bytes) -> float | None:
    if len(data) < 10 or data[0] != 0xD0:
        return None
    return int.from_bytes(data[8:10], "little", signed=True) / 100.0


def parse_ovc_buttons(data: bytearray | bytes) -> list[int]:
    if len(data) < 4 or data[0] != 0xD0:
        return []
    bitmap = int.from_bytes(data[2:4], "big")
    return [i for i in range(16) if bitmap & (1 << i)]


def pack_pwm_ab2(strength_a: int, strength_b: int) -> bytes:
    a = max(0, min(0x7FF, int(strength_a) * 7))
    b = max(0, min(0x7FF, int(strength_b) * 7))
    return ((a << 11) | b).to_bytes(3, "big")


def pack_xyz(x: int, y: int, z: int) -> bytes:
    return (((z & 0x1F) << 15) | ((y & 0x3FF) << 5) | (x & 0x1F)).to_bytes(3, "big")


class BleSession:
    def __init__(self, slot_id: str, kind: str, client: BleakClient):
        self.slot_id = slot_id
        self.kind = kind
        self.client = client
        self._targets = {"A": 0, "B": 0}
        self._sent: dict[str, int | None] = {"A": None, "B": None}
        self._actual = {"A": 0, "B": 0}
        self._awaiting_seq = 0
        self._await_since: int | None = None
        self.ticks = 0
        self._unparsed_logged = 0
        self._seq = 0
        self._screen = {"A": 0, "B": 0}
        self._cycles: dict[str, FrameCycle] = {"A": FrameCycle(), "B": FrameCycle()}
        self._pressed: set[int] = set()
        self.writer_task: asyncio.Task | None = None
        self.monitor = WaveMonitor()
        self.led_color = 0x01
        self.orientation = 1
        self.fire_task: asyncio.Task | None = None
        self._fire_saved: dict[str, int] = {}
        self._deliberate = False

    @property
    def device_type(self) -> str:
        return KIND_TYPE.get(self.kind, self.kind)

    def next_seq(self) -> int:
        self._seq = (self._seq % 15) + 1
        return self._seq


class BleClient:
    def __init__(
        self,
        events: StateEvents | None = None,
        *,
        soft_limit_a: int = 200,
        soft_limit_b: int = 200,
        freq_balance_a: int = 0,
        freq_balance_b: int = 0,
        strength_balance_a: int = 0,
        strength_balance_b: int = 0,
    ):
        self.events = events or StateEvents()
        self.soft_limits = {"A": soft_limit_a, "B": soft_limit_b}
        self.balance = (
            freq_balance_a, freq_balance_b, strength_balance_a, strength_balance_b,
        )
        self.state = EngineState(backend="ble")
        self.sessions: dict[str, BleSession] = {}
        self.dropped: set[str] = set()

    def _log(self, msg: str) -> None:
        self.events.emit("log", f"[BLE] {msg}")

    def _publish(self, status: str | None = None) -> None:
        if status is not None:
            self.state.status_text = status
        self.events.emit("state", self.state.copy())

    def _slot(self, session: BleSession) -> Slot:
        slot = self.state.slots.get(session.slot_id)
        if slot is None:
            slot = Slot(
                slot_id=session.slot_id,
                name=KIND_LABELS.get(session.kind, session.kind),
                type=session.device_type,
            )
            self.state.slots[session.slot_id] = slot
        return slot

    def _session(self, slot_id: str | None) -> BleSession:
        if slot_id and slot_id in self.sessions:
            return self.sessions[slot_id]
        if self.sessions:
            return next(iter(self.sessions.values()))
        raise RuntimeError("没有已连接的蓝牙设备")

    @staticmethod
    async def scan(timeout: float = 6.0) -> list[dict]:
        found: dict[str, dict] = {}
        devices = await BleakScanner.discover(timeout=timeout)
        for d in devices:
            name = d.name or ""
            kind = None
            for prefix, k in DEVICE_KINDS.items():
                if name.startswith(prefix):
                    kind = k
                    break
            if kind is None and name.strip().upper().startswith(V2_NAME):
                kind = "coyote_v2"
            if kind is None:
                continue
            found[d.address] = {
                "name": name,
                "address": d.address,
                "kind": kind,
                "kind_label": KIND_LABELS.get(kind, kind),
                "rssi": getattr(d, "rssi", None),
            }
        return list(found.values())

    async def connect(self, address: str, kind: str = "coyote_v3") -> None:
        if address in self.sessions:
            self._log(f"{address} 已连接，忽略重复连接")
            return
        self._log(f"连接 {address} ({KIND_LABELS.get(kind, kind)})")

        client = BleakClient(
            address, disconnected_callback=lambda _c: self._on_ble_drop(address)
        )
        await client.connect()
        session = BleSession(address, kind, client)
        self.sessions[address] = session
        self._slot(session)
        self.state.connected = True
        self.state.paired = True
        self.state.status_text = f"已连接 {len(self.sessions)} 台设备"
        self._publish()

        await client.start_notify(
            V3_NOTIFY, lambda sender, data, s=session: self._on_notify(s, sender, data)
        )
        if kind == "bmtr":
            try:
                await client.start_notify(
                    BMTR_FF_NOTIFY,
                    lambda sender, data, s=session: self._on_notify(s, sender, data),
                )
                self._log(f"{session.slot_id} 已订阅厂商通知 FF00")
            except Exception as exc:
                self._log(f"{session.slot_id} FF00 订阅失败: {exc!r}")
        try:
            battery = await client.read_gatt_char(V3_BATTERY)
            self._slot(session).battery = int(battery[0])
            self._log(f"{session.slot_id} 电量 {self._slot(session).battery}%")
        except Exception as exc:
            self._log(f"{session.slot_id} 电量读取失败: {exc!r}")
        try:
            await client.start_notify(
                V3_BATTERY, lambda sender, data, s=session: self._on_battery(s, sender, data)
            )
        except Exception:
            pass

        if kind == "coyote_v3":
            await self._send_bf(session)
        elif kind == "ovc":
            await self._write(session, build_ovc_50(enable_buttons=True))
        elif kind == "bmtr":
            enable = build_bmtr_50(enable_pressure=True)
            try:
                await self._write(session, enable)
                self._log(f"{session.slot_id} 0x50 气压上报使能已发送 "
                          f"({len(enable)}B: {enable.hex().upper()})")
            except Exception as exc:
                self._log(f"{session.slot_id} 0x50 使能写入失败: {exc!r}")
            session.writer_task = asyncio.create_task(self._bmtr_keepalive(session))
        elif kind == "coyote_v2":
            await client.start_notify(
                V2_PWM_AB2, lambda sender, data, s=session: self._on_v2_strength(s, sender, data)
            )

        if kind != "bmtr":
            silent = resolve_wave_frames(SILENT, session.device_type)
            session._cycles["A"].reset(silent)
            session._cycles["B"].reset(silent)
            session.writer_task = asyncio.create_task(self._writer_loop(session))
        self._publish()
        self.dropped.discard(address)
        self._log(f"{KIND_LABELS.get(kind, kind)} 已接入 (共 {len(self.sessions)} 台在线，默认「静默」波形)")

    def _on_ble_drop(self, address: str) -> None:
        session = self.sessions.get(address)
        if session is None or session._deliberate:
            return
        self.sessions.pop(address, None)
        self.state.slots.pop(address, None)
        self.dropped.add(address)
        if self.sessions:
            self.state.status_text = f"已连接 {len(self.sessions)} 台设备"
        else:
            self.state.paired = False
            self.state.status_text = "连接已断开"
        self._log(f"{address} 连接意外断开 (将自动重连)")
        self._publish()

    async def disconnect(self, slot_id: str | None = None) -> None:
        targets = [slot_id] if slot_id else list(self.sessions)
        for sid in targets:
            session = self.sessions.pop(sid, None)
            if session is None:
                continue
            session._deliberate = True
            self.dropped.discard(sid)
            if session.writer_task:
                session.writer_task.cancel()
            try:
                await session.client.disconnect()
            except Exception:
                pass
            self.state.slots.pop(sid, None)
            self._log(f"已断开 {sid}")
        if not self.sessions:
            self.state = EngineState(backend="ble", status_text="未连接")
        else:
            self.state.status_text = f"已连接 {len(self.sessions)} 台设备"
        self._publish()

    async def _bmtr_keepalive(self, session: BleSession) -> None:
        try:
            while True:
                await asyncio.sleep(1.0)
                if self.sessions.get(session.slot_id) is not session:
                    return
                await self._write(session, build_bmtr_50(enable_pressure=True,
                                                              color=session.led_color))
                self._log_d0_stats(session)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self._log(f"{session.slot_id} 使能保活失败: {exc!r}")

    def _log_d0_stats(self, session: BleSession) -> None:
        stats = session.__dict__.get("notify_stats", {})
        if not stats:
            self._log(f"{session.slot_id} D0接收统计(1s): 未收到任何通知帧")
            return
        parts = []
        for uuid, (count, last) in sorted(stats.items()):
            parts.append(f"{uuid}:{count}帧")
            if last:
                parts.append(f"最新[{uuid}] {last.hex().upper()}")
                pressure = parse_bmtr_pressure(last)
                if pressure is not None:
                    parts.append(f"气压 {pressure:.2f} kPa")
            else:
                parts.append(f"[{uuid}]无数据")
            stats[uuid][0] = 0
        self._log(f"{session.slot_id} D0接收统计(1s): " + " ".join(parts))

    async def _write(self, session: BleSession, data: bytes, char: str | None = None) -> None:
        characteristic = char or V3_WRITE
        try:
            await session.client.write_gatt_char(characteristic, data, response=False)
        except Exception:
            await session.client.write_gatt_char(characteristic, data, response=True)

    def _on_battery(self, session: BleSession, _sender: Any, data: bytearray) -> None:
        if data:
            self._slot(session).battery = int(data[0])
            self._publish()

    def _on_notify(self, session: BleSession, sender: Any, data: bytearray) -> None:
        if not data:
            return
        head = data[0]
        if session.kind == "bmtr":
            uuid = str(getattr(sender, "uuid", "")).split("-")[0].upper()[-8:]
            stats = session.__dict__.setdefault("notify_stats", {})
            entry = stats.setdefault(uuid, [0, b""])
            entry[0] += 1
            entry[1] = bytes(data)
            key = bytes(data).hex().upper()
            logged = session.__dict__.setdefault("_raw_logged", {})
            logged[key] = logged.get(key, 0) + 1
            if logged[key] <= 3:
                self._log(f"{session.slot_id} 通知帧({len(data)}B): {key}")
            pressure = parse_bmtr_pressure(data)
            if pressure is not None:
                self._slot(session).pressure = pressure
                self._publish()
            return
        if session.kind == "ovc":
            if head == 0xB3 and len(data) >= 3:
                session._actual = {"A": int(data[1]), "B": int(data[2])}
                if session._actual != session._targets:
                    session._targets = dict(session._actual)
                self._slot(session).strength = dict(session._actual)
                self._publish()
            elif head == 0xD0:
                self._handle_buttons(session, data)
            return
        if head == 0xB1 and len(data) >= 4:
            seq = data[1]
            session._actual = {"A": int(data[2]), "B": int(data[3])}
            if session._actual != session._targets:
                session._targets = dict(session._actual)
            self._slot(session).strength = dict(session._actual)
            if seq == session._awaiting_seq and seq != 0:
                session._awaiting_seq = 0
                session._await_since = None
            self._publish()

    def _on_v2_strength(self, session: BleSession, _sender: Any, data: bytearray) -> None:
        if len(data) < 3:
            return
        value = int.from_bytes(data[:3], "big")
        session._actual = {"A": (value >> 11) // 7, "B": (value & 0x7FF) // 7}
        if session._actual != session._targets:
            session._targets = dict(session._actual)
        self._slot(session).strength = dict(session._actual)
        self._publish()

    def _handle_buttons(self, session: BleSession, data: bytearray) -> None:
        pressed = set(parse_ovc_buttons(data))
        for bit in sorted(pressed - session._pressed):
            self._log(f"{session.slot_id} 按键按下: bit{bit}")
            self.events.emit("action", bit)
            self.events.emit("ovc_button", session.slot_id, bit)
        for bit in sorted(session._pressed - pressed):
            self._log(f"{session.slot_id} 按键抬起: bit{bit}")
            self.events.emit("ovc_button_up", session.slot_id, bit)
        session._pressed = pressed

    async def _send_bf(self, session: BleSession) -> None:
        fa, fb, sa, sb = self.balance
        frame = bytes([
            0xBF,
            self.soft_limits["A"] & 0xFF,
            self.soft_limits["B"] & 0xFF,
            fa & 0xFF, fb & 0xFF, sa & 0xFF, sb & 0xFF,
        ])
        await self._write(session, frame)
        self._log(f"{session.slot_id} BF 已写入: 软限制 A={self.soft_limits['A']} B={self.soft_limits['B']}")

    async def _writer_loop(self, session: BleSession) -> None:
        errors = 0
        try:
            while True:
                await asyncio.sleep(LOOP_INTERVAL)
                try:
                    session.ticks += 1
                    if session.kind == "coyote_v3":
                        await self._write_b0(session)
                    elif session.kind == "ovc":
                        await self._write_ovc(session)
                    else:
                        await self._write_v2(session)
                    errors = 0
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    errors += 1
                    if errors == 1 or errors % 30 == 0:
                        self._log(f"{session.slot_id} 输出循环错误 (连续 {errors}): {exc!r}")
                    if errors >= 90:
                        self._log(f"{session.slot_id} 连续错误过多，停止输出循环")
                        break
        except asyncio.CancelledError:
            pass

    def _v3_channel_fields(self, session: BleSession, ch: str) -> tuple[list[int], list[int], int, int]:
        freqs = [10, 10, 10, 10]
        strengths = [0, 0, 0, 0]
        cycle = session._cycles[ch]
        if cycle.frames:
            frame = cycle.next_frame()
            raw = bytes.fromhex(frame)
            freqs, strengths = list(raw[:4]), list(raw[4:])
        method_ch = 0b00
        strength_byte = session._targets[ch]
        if session._targets[ch] != session._sent[ch]:
            target = max(0, min(self.soft_limits[ch], session._targets[ch]))
            strength_byte = target
            method_ch = 0b11
            session._sent[ch] = target
        return freqs, strengths, method_ch, strength_byte

    async def _write_b0(self, session: BleSession) -> None:
        fa, sa, ma, va = self._v3_channel_fields(session, "A")
        fb, sb, mb, vb = self._v3_channel_fields(session, "B")
        seq, method = 0, 0b00
        if ma or mb:
            seq = session.next_seq()
            session._awaiting_seq = seq
            session._await_since = session.ticks
            method = (ma << 2) | mb
        await self._write(session, build_b0(seq, method, va, vb, fa, sa, fb, sb))
        session.monitor.record(sa, sb)

    async def _write_ovc(self, session: BleSession) -> None:
        wave_a, wave_b = [0, 0, 0, 0], [0, 0, 0, 0]
        for ch, out in (("A", wave_a), ("B", wave_b)):
            cycle = session._cycles[ch]
            if cycle.frames:
                out[:] = ovc_channel_pattern(cycle.next_frame())
        await self._write(session, build_ovc_b0(wave_a, wave_b))
        session.monitor.record(wave_a, wave_b)
        if (session._targets["A"], session._targets["B"]) != (
            session._screen.get("A"), session._screen.get("B")
        ):
            a = max(0, min(200, session._targets["A"]))
            b = max(0, min(200, session._targets["B"]))
            session._screen = {"A": a, "B": b}
            await self._write(session, build_ovc_b3(a, b))
            await self._write(session, build_ovc_b2(a, b))

    async def _write_v2(self, session: BleSession) -> None:
        await self._write(session, pack_pwm_ab2(session._targets["A"], session._targets["B"]), V2_PWM_AB2)
        segs_a, segs_b = [0, 0, 0, 0], [0, 0, 0, 0]
        for ch, char, segs in (("A", V2_PWM_B34, segs_a), ("B", V2_PWM_A34, segs_b)):
            cycle = session._cycles[ch]
            if not cycle.frames:
                continue
            frame = cycle.next_frame()
            raw = bytes.fromhex(frame)
            freqs, strengths = list(raw[:4]), list(raw[4:])
            segs[:] = strengths
            logical = wire_to_logical_freq(round(sum(freqs) / 4))
            strength_pct = sum(strengths) / 4
            x, y = frequency_to_xy(logical)
            z = round(max(0, min(100, strength_pct)) * 20 / 100)
            await self._write(session, pack_xyz(x, y, z), char)
        session.monitor.record(segs_a, segs_b)

    async def set_strength(self, channel: str, value: int, slot_id: str | None = None) -> None:
        session = self._session(slot_id)
        if session.kind == "bmtr":
            raise RuntimeError("灵猫是气压传感器，无输出通道")
        if session.kind == "ovc":
            value = max(0, min(200, int(value)))
        else:
            value = max(0, min(self.soft_limits[channel], int(value)))
        session._targets[channel] = value
        session._actual[channel] = value
        self._slot(session).strength[channel] = value
        self._publish()

    async def add_strength(self, channel: str, delta: int, slot_id: str | None = None) -> None:
        session = self._session(slot_id)
        current = self._slot(session).strength.get(channel, 0)
        await self.set_strength(channel, current + int(delta), session.slot_id)

    async def set_wave(self, channel: str, waveform: CoyoteWaveform | str | list[str],
                       slot_id: str | None = None) -> None:
        session = self._session(slot_id)
        if session.kind == "bmtr":
            raise RuntimeError("灵猫是气压传感器，无波形输出")
        frames = resolve_wave_frames(waveform, session.device_type)
        session._cycles[channel].reset(frames)
        self._log(f"{session.slot_id} 通道 {channel} 波形已更新 ({len(frames)} 帧)"
                  + ("（外部脉冲流：帧随模块推送追加）"
                     if waveform == PULSE_STREAM else ""))

    async def push_pulse_frame(self, slot_id: str, channel: str, frame: str) -> None:
        """外部脉冲流：模块推入的一帧 (100ms) 追加到该通道播放队列尾部。

        仅在引擎选中脉冲流波形时被调用；设备按 100ms/帧消费，模块按
        0.1s 节奏推送即实时成流。超长从头裁剪，未知设备 / 灵猫静默忽略。
        """
        session = self.sessions.get(slot_id)
        if session is None or session.kind == "bmtr":
            return
        if channel not in session._cycles:
            return
        frames = session._cycles[channel].frames
        frames.append(frame)
        trim_pulse_stream(frames)

    async def clear_wave(self, channel: str | None = None, slot_id: str | None = None) -> None:
        sessions = [self.sessions[slot_id]] if slot_id and slot_id in self.sessions \
            else list(self.sessions.values())
        for session in sessions:
            if session.kind == "bmtr":
                continue
            for ch in CHANNELS:
                if channel in (None, ch):
                    session._cycles[ch].reset([])

    async def reset_pressure(self, slot_id: str | None = None) -> None:
        session = self._session(slot_id)
        if session.kind != "bmtr":
            raise RuntimeError("仅灵猫支持气压清零")
        await self._write(session, build_bmtr_66(reset_pressure=True))
        self._log(f"{session.slot_id} 气压读值已清零")

    async def fire(self, slot_id: str | None = None, duration_s: float = 1.0,
                   value: int | None = None) -> None:
        session = self._session(slot_id)
        if session.kind == "bmtr":
            raise RuntimeError("灵猫是气压传感器，无输出通道")
        cap = value if value is not None else 200
        if session.fire_task is not None and not session.fire_task.done():
            session.fire_task.cancel()

        saved = dict(self._slot(session).strength)
        for ch in CHANNELS:
            session._targets[ch] = cap
            session._actual[ch] = cap
            self._slot(session).strength[ch] = cap
        self._publish()

        async def _restore() -> None:
            try:
                await asyncio.sleep(duration_s)
                for ch in CHANNELS:
                    session._targets[ch] = saved.get(ch, 0)
                    session._actual[ch] = saved.get(ch, 0)
                self._slot(session).strength = dict(session._actual)
                self._publish()
                self._log(f"{session.slot_id} 开火结束，强度恢复 {saved}")
            except asyncio.CancelledError:
                pass

        session.fire_task = asyncio.create_task(_restore())
        self._log(f"{session.slot_id} 一键开火 {duration_s}s 强度 {cap}")

    async def set_led(self, color: str | int, slot_id: str | None = None) -> None:
        session = self._session(slot_id)
        if session.kind not in ("ovc", "bmtr", "coyote_v3"):
            raise RuntimeError("该设备不支持 LED 颜色设置 (仅郊狼/负鼠/灵猫)")
        byte = color if isinstance(color, int) else LED_COLORS.get(color)
        if byte is None:
            raise RuntimeError(f"未知颜色: {color}")
        session.led_color = byte
        if session.kind == "ovc":
            await self._write(session, build_ovc_50(enable_buttons=True, color=byte))
        elif session.kind == "bmtr":
            await self._write(session, build_bmtr_50(enable_pressure=True, color=byte))
        else:
            await self._write(session, build_coyote_50(color=byte))
        self._log(f"{session.slot_id} LED 颜色 → {color}")

    async def bmtr_flip(self, slot_id: str | None = None) -> None:
        session = self._session(slot_id)
        if session.kind != "bmtr":
            raise RuntimeError("仅灵猫支持屏幕翻转")
        session.orientation = 3 if session.orientation == 1 else 1
        await self._write(session, build_bmtr_66(reset_pressure=False,
                                                 orientation=session.orientation))
        self._log(f"{session.slot_id} 屏幕方向 → {session.orientation}")

    async def emergency_stop(self) -> None:
        for session in list(self.sessions.values()):
            if session.kind == "bmtr":
                continue
            try:
                await self.clear_wave(slot_id=session.slot_id)
                for ch in CHANNELS:
                    session._targets[ch] = 0
                    session._actual[ch] = 0
                self._slot(session).strength = {"A": 0, "B": 0}
            except Exception as exc:
                self._log(f"{session.slot_id} 急停失败: {exc!r}")
        self._publish()
        self._log(f"急停已执行 ({len(self.sessions)} 台设备)")

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


def family_of(device_type: str) -> str:
    t = (device_type or "").upper()
    if t.startswith("OVC"):
        return "OVC"
    if t.startswith("BMTR"):
        return "BMTR"
    return "COYOTE"


@dataclass
class Slot:
    slot_id: str = ""
    name: str = ""
    type: str = ""
    strength: dict[str, int] = field(default_factory=lambda: {"A": 0, "B": 0})
    strength_limit: dict[str, int] = field(default_factory=lambda: {"A": 200, "B": 200})
    battery: int | None = None
    channel_status: dict[str, int] = field(default_factory=lambda: {"A": 0, "B": 0})
    pressure: float | None = None
    edge_state: int | None = None
    props: dict = field(default_factory=dict)
    slot_state: dict = field(default_factory=dict)

    @property
    def is_output_device(self) -> bool:
        return not self.type.upper().startswith("BMTR")

    def summary(self) -> str:
        bat = f"{self.battery}%" if self.battery is not None else "--"
        if not self.is_output_device:
            pressure = f"{self.pressure:.2f}" if self.pressure is not None else "--"
            return f"{self.name or self.type or self.slot_id}  气压 {pressure} kPa  电量 {bat}"
        return (
            f"{self.name or self.type or self.slot_id}  "
            f"A={self.strength['A']}/{self.strength_limit['A']} "
            f"B={self.strength['B']}/{self.strength_limit['B']}  电量 {bat}"
        )


@dataclass
class EngineState:
    backend: str = "none"
    connected: bool = False
    paired: bool = False
    status_text: str = "未连接"
    client_id: str = ""
    target_id: str = ""
    qr_text: str = ""
    slots: dict[str, Slot] = field(default_factory=dict)
    active_slot: str = ""
    last_action: int | None = None

    def active(self) -> Slot | None:
        return self.slots.get(self.active_slot)

    def copy(self) -> "EngineState":
        slots = {k: Slot(**{**vars(v)}) for k, v in self.slots.items()}
        return EngineState(
            backend=self.backend,
            connected=self.connected,
            paired=self.paired,
            status_text=self.status_text,
            client_id=self.client_id,
            target_id=self.target_id,
            qr_text=self.qr_text,
            slots=slots,
            active_slot=self.active_slot,
            last_action=self.last_action,
        )


class StateEvents:
    def __init__(self) -> None:
        self._subs: dict[str, list[Callable]] = {}

    def on(self, event: str, cb: Callable) -> Callable:
        self._subs.setdefault(event, []).append(cb)
        return cb

    def off(self, event: str, cb: Callable) -> None:
        subs = self._subs.get(event)
        if subs is None:
            return
        try:
            subs.remove(cb)
        except ValueError:
            pass

    def emit(self, event: str, *args) -> None:
        for cb in list(self._subs.get(event, ())):
            try:
                cb(*args)
            except Exception:
                pass

    def remove_all(self) -> None:
        self._subs.clear()

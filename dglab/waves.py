from __future__ import annotations

import math
from dataclasses import dataclass

from .official_waveforms import COYOTE_WAVEFORMS, CoyoteWaveform
from .official_waveforms_ovc import OVC_WAVEFORMS, OvcWaveform

__all__ = [
    "SILENT",
    "SILENT_FRAMES",
    "PULSE_STREAM",
    "pulse_frame",
    "COYOTE_WAVEFORMS",
    "CoyoteWaveform",
    "OVC_WAVEFORMS",
    "OvcWaveform",
    "WIRE_FREQ_MIN",
    "WIRE_FREQ_MAX",
    "logical_to_wire_freq",
    "wire_to_logical_freq",
    "frequency_to_xy",
    "parse_frame",
    "build_frame",
    "frames_duration_ms",
    "cycle_frame",
    "FrameCycle",
    "ovc_channel_pattern",
    "waveform_dict_for",
    "wave_order",
]

WIRE_FREQ_MIN = 10
WIRE_FREQ_MAX = 240


def logical_to_wire_freq(logical: int) -> int:
    logical = max(10, min(1000, int(logical)))
    if logical <= 100:
        return logical
    if logical <= 600:
        return (logical - 100) // 5 + 100
    return (logical - 600) // 10 + 200


def wire_to_logical_freq(wire: int) -> int:
    wire = max(WIRE_FREQ_MIN, min(WIRE_FREQ_MAX, int(wire)))
    if wire <= 100:
        return wire
    if wire <= 200:
        return (wire - 100) * 5 + 100
    return (wire - 200) * 10 + 600


def frequency_to_xy(logical_freq: int) -> tuple[int, int]:
    freq = max(10, min(1000, int(logical_freq)))
    x = max(1, round(math.sqrt(freq / 1000) * 15))
    y = max(0, freq - x)
    return x, y


def parse_frame(frame: str) -> tuple[list[int], list[int]]:
    frame = frame.strip().lower()
    if len(frame) != 16:
        raise ValueError(f"frame must be 16 hex chars, got {len(frame)}: {frame!r}")
    raw = bytes.fromhex(frame)
    return list(raw[:4]), list(raw[4:])


def build_frame(freqs, strengths) -> str:
    out = bytearray()
    for f in freqs:
        out.append(max(0, min(255, int(f))))
    for s in strengths:
        out.append(max(0, min(255, int(s))))
    return out.hex()


def frames_duration_ms(frames: list[str]) -> int:
    return len(frames) * 100


def cycle_frame(frames: list[str], tick: int) -> str:
    if not frames:
        return build_frame([WIRE_FREQ_MIN] * 4, [0] * 4)
    return frames[tick % len(frames)]


@dataclass
class FrameCycle:
    frames: list[str] = None
    tick: int = 0

    def __post_init__(self):
        if self.frames is None:
            self.frames = []

    def next_frame(self) -> str:
        frame = cycle_frame(self.frames, self.tick)
        self.tick = (self.tick + 1) % max(1, len(self.frames))
        return frame

    def reset(self, frames: list[str] | None = None) -> None:
        if frames is not None:
            self.frames = list(frames)
        self.tick = 0


def ovc_channel_pattern(frame: str) -> list[int]:
    raw = bytes.fromhex(frame)
    return list(raw[4:8])


def waveform_dict_for(device_type: str) -> dict:
    if device_type.upper().startswith("OVC"):
        return OVC_WAVEFORMS
    return COYOTE_WAVEFORMS


def resolve_wave_frames(waveform: "CoyoteWaveform | OvcWaveform | str | list[str]",
                        device_type: str = "COYOTE_030") -> list[str]:
    if isinstance(waveform, list):
        return list(waveform)
    if waveform == SILENT:
        return list(SILENT_FRAMES)
    if waveform == PULSE_STREAM:
        # 脉冲流无静态帧表：循环从空起步，帧由模块推送时逐帧追加
        return []
    if waveform == CONTINUOUS:
        if device_type.upper().startswith("OVC"):
            return [build_frame([0x0A] * 4, [100] * 4)]
        return list(CONTINUOUS_FRAMES)
    table = waveform_dict_for(device_type)
    return list(table[waveform]["raw"])


CONTINUOUS = "__CONTINUOUS__"
SILENT = "__SILENT__"
# 外部脉冲流：波形不由内置发生器产生，而由联动模块按 0.1s 节奏推送频率数据
# （每帧 100ms）。推流采用「最新帧替换」语义：每推一帧，播放列表即替换为
# 该帧——推送与消费同速时「追加历史」会让播放指针越落越后（频率严重滞后）；
# 模块停推即以最后一帧循环（保持最后频率）。
PULSE_STREAM = "__PULSE_STREAM__"
CONTINUOUS_FRAMES = [build_frame([40, 40, 40, 40], [100, 100, 100, 100])]
SILENT_FRAMES = [build_frame([10, 10, 10, 10], [0, 0, 0, 0])]


def pulse_frame(frequency: int, level: int = 100) -> str:
    """逻辑频率 (10-1000) + 电平 (0-100) → 一帧 100ms 脉冲（四段同值）。

    联动模块经 ``ctx.push_pulse_stream`` 推流时由引擎逐次构建；
    电平 0 即该帧静音（波形成形仍保留频率）。郊狼由设备按频率字节
    生成载波；负鼠（振动）无载波语义，须用 :func:`pulse_frame_vibration`。
    """
    wire = logical_to_wire_freq(frequency)
    amp = max(0, min(100, int(level)))
    return build_frame([wire] * 4, [amp] * 4)


def pulse_frame_vibration(frequency: int, level: int, t_start: float) -> str:
    """负鼠（振动）脉冲帧：把频率渲染成**振幅方波图案**（相位跨帧连续）。

    振动设备没有频率载波——只按图案振幅振动，若四段恒为满幅则输出是
    一条恒定直线。故把「频率」显式合成进图案：振动速率 = 频率/100
    （逻辑 10-1000 → 0.1-10 Hz 通断振动），通相振幅 = level、断相 = 0；
    相位取绝对时间，跨帧连续（图案在帧间滚动而非每帧重置）。
    """
    rate = max(0.1, min(20.0, float(frequency) / 100.0))
    amp = max(0, min(100, int(level)))
    segs = []
    for j in range(4):
        t = t_start + j * 0.025
        segs.append(amp if ((t * rate) % 1.0) < 0.5 else 0)
    wire = logical_to_wire_freq(frequency)
    return build_frame([wire] * 4, segs)


def wave_order(family: str = "COYOTE") -> list[str]:
    """设备家族可用的波形枚举序列（静默/持续在前，供步进与直接跳变使用）。

    外部脉冲流追加在末尾（内置波形序号保持稳定，追加不改变既有配置的
    波形下标语义）。
    """
    if family == "OVC":
        return ([SILENT, CONTINUOUS] + [w.value for w in OvcWaveform]
                + [PULSE_STREAM])
    return ([SILENT, CONTINUOUS] + [w.value for w in CoyoteWaveform]
            + [PULSE_STREAM])

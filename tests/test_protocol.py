from __future__ import annotations

import unittest
from urllib.parse import quote, unquote

from dglab.ble import build_b0, pack_pwm_ab2, pack_xyz
from dglab.official_waveforms import COYOTE_WAVEFORMS, CoyoteWaveform
from dglab.socket_v3 import SocketV3Client, build_v3_qr
from dglab.socket_v4 import SocketV4Client, _merge_patch, build_v4_qr
from dglab.state import StateEvents
from dglab.waves import (
    FrameCycle,
    build_frame,
    cycle_frame,
    frequency_to_xy,
    logical_to_wire_freq,
    parse_frame,
    wire_to_logical_freq,
)
from dglab.params import _clamp as _to_int, _truthy


class WaveTests(unittest.TestCase):
    def test_freq_roundtrip(self):
        for logical in (10, 50, 100, 101, 300, 600, 601, 800, 1000):
            wire = logical_to_wire_freq(logical)
            self.assertTrue(10 <= wire <= 240, (logical, wire))
            back = wire_to_logical_freq(wire)
            self.assertAlmostEqual(logical, back, delta=max(1, logical // 100),
                                   msg=(logical, wire, back))

    def test_frame_roundtrip(self):
        frame = build_frame([10, 20, 30, 40], [0, 50, 100, 7])
        freqs, strengths = parse_frame(frame)
        self.assertEqual(freqs, [10, 20, 30, 40])
        self.assertEqual(strengths, [0, 50, 100, 7])

    def test_cycle(self):
        frames = ["0A0A0A0A00000000", "0A0A0A0A64646464"]
        cycle = FrameCycle(frames=frames)
        self.assertEqual(cycle.next_frame(), frames[0])
        self.assertEqual(cycle.next_frame(), frames[1])
        self.assertEqual(cycle.next_frame(), frames[0])

    def test_cycle_frame_empty(self):
        frame = cycle_frame([], 5)
        self.assertEqual(len(frame), 16)

    def test_xy(self):
        x, y = frequency_to_xy(1000)
        self.assertEqual(x + y, 1000)
        x, y = frequency_to_xy(10)
        self.assertTrue(x >= 1)

    def test_official_waveforms(self):
        for wave in CoyoteWaveform:
            raw = COYOTE_WAVEFORMS[wave]["raw"]
            self.assertTrue(len(raw) >= 1, wave)
            for frame in raw:
                freqs, strengths = parse_frame(frame)
                for f in freqs:
                    self.assertTrue(0 <= f <= 240, (wave, frame))
                for s in strengths:
                    self.assertTrue(0 <= s <= 100, (wave, frame))


class SocketV4Tests(unittest.TestCase):
    def test_qr_format(self):
        relay = "wss://trex.dungeon-lab.cn/v4"
        qr = build_v4_qr(relay, "ctrl-123")
        self.assertTrue(qr.startswith("https://dungeon-lab.cn/s/?v=1&action=socket&url="))
        embedded = unquote(qr.split("url=", 1)[1])
        self.assertEqual(embedded, "wss://trex.dungeon-lab.cn/v4/?tid=ctrl-123")

    def test_merge_patch(self):
        self.assertEqual(
            _merge_patch({"a": 1, "b": {"x": 1}}, {"b": {"y": 2}}),
            {"a": 1, "b": {"x": 1, "y": 2}},
        )
        self.assertEqual(_merge_patch(5, None), 5)
        self.assertEqual(_merge_patch({"a": 1}, None), {"a": 1})
        self.assertEqual(_merge_patch(None, None), None)

    def test_slot_from_device(self):
        events = StateEvents()
        client = SocketV4Client(events=events)
        client._replace_devices(
            "app-1",
            [
                {
                    "slotId": "slot-a",
                    "name": "设备 A",
                    "type": "COYOTE_030",
                    "props": {
                        "intensityA": 11,
                        "intensityB": 7,
                        "power": 88,
                        "channelAStatus": 2,
                    },
                    "slotState": {
                        "channelA": {"intensityMax": 100},
                        "channelB": {"intensityMax": 35},
                    },
                }
            ],
        )
        state = client.state
        self.assertEqual(state.active_slot, "slot-a")
        slot = state.slots["slot-a"]
        self.assertEqual(slot.strength, {"A": 11, "B": 7})
        self.assertEqual(slot.battery, 88)
        self.assertEqual(slot.strength_limit, {"A": 100, "B": 35})

    def test_hello_and_client_attach(self):
        client = SocketV4Client(events=StateEvents())
        client._handle_frame({"type": "hello", "clientId": "ctrl-1"})
        self.assertEqual(client.state.client_id, "ctrl-1")
        client._handle_frame({"type": "client_attached", "clientId": "app-1"})
        self.assertTrue(client.state.paired)
        self.assertEqual(client.state.target_id, "app-1")
        client._handle_frame({"type": "client_disconnected", "clientId": "app-1"})
        self.assertFalse(client.state.paired)


class SocketV3Tests(unittest.TestCase):
    def test_qr_format(self):
        qr = build_v3_qr("wss://ws.dungeon-lab.cn/", "abc-123")
        self.assertEqual(
            qr,
            "https://www.dungeon-lab.com/app-download.php#DGLAB-SOCKET#wss://ws.dungeon-lab.cn/abc-123",
        )

    def test_strength_feedback_plus_separator(self):
        client = SocketV3Client(events=StateEvents())
        client._handle_frame({"type": "bind", "clientId": "me", "targetId": "", "message": "targetId"})
        client._handle_frame({"type": "bind", "clientId": "me", "targetId": "app", "message": "200"})
        self.assertTrue(client.state.paired)
        client._handle_app_message("strength-11+7+100+35")
        slot = client.state.slots["coyote"]
        self.assertEqual(slot.strength, {"A": 11, "B": 7})
        self.assertEqual(slot.strength_limit, {"A": 100, "B": 35})

    def test_strength_feedback_dash_separator(self):
        client = SocketV3Client(events=StateEvents())
        client._handle_app_message("strength-11-7-100-35")
        slot = client.state.slots["coyote"]
        self.assertEqual(slot.strength, {"A": 11, "B": 7})

    def test_feedback_button(self):
        client = SocketV3Client(events=StateEvents())
        seen = []
        client.events.on("action", lambda a: seen.append(a))
        client._handle_app_message("feedback-3")
        self.assertEqual(seen, [3])


class BleTests(unittest.TestCase):
    def test_b0_frame(self):
        frame = build_b0(
            0, 0, 0, 0,
            [0x0A, 0x0A, 0x0A, 0x0A], [0x00, 0x0A, 0x14, 0x1E],
            [0, 0, 0, 0], [0, 0, 0, 101],
        )
        self.assertEqual(len(frame), 20)
        self.assertEqual(frame[0], 0xB0)
        self.assertEqual(frame[1], 0x00)
        self.assertEqual(
            frame.hex().upper(),
            "B00000000A0A0A0A000A141E0000000000000065",
        )

    def test_b0_seq_and_method(self):
        frame = build_b0(3, 0b1100, 5, 10, [10] * 4, [0] * 4, [10] * 4, [0] * 4)
        self.assertEqual(frame[1], (3 << 4) | 0b1100)

    def test_pwm_ab2(self):
        data = pack_pwm_ab2(10, 20)
        value = int.from_bytes(data, "big")
        self.assertEqual(value >> 11, 10 * 7)
        self.assertEqual(value & 0x7FF, 20 * 7)

    def test_pack_xyz(self):
        data = pack_xyz(5, 95, 20)
        value = int.from_bytes(data, "big")
        self.assertEqual(value & 0x1F, 5)
        self.assertEqual((value >> 5) & 0x3FF, 95)
        self.assertEqual((value >> 15) & 0x1F, 20)


class ParamHelperTests(unittest.TestCase):
    def test_helpers(self):
        self.assertEqual(_to_int(150, 0, 200), 150)
        self.assertEqual(_to_int(-5, 0, 200), 0)
        self.assertEqual(_to_int(999, 0, 200), 200)
        self.assertTrue(_truthy(True))
        self.assertTrue(_truthy(1))
        self.assertFalse(_truthy(0))
        self.assertFalse(_truthy(False))


if __name__ == "__main__":
    unittest.main()

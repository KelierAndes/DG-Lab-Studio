from __future__ import annotations

import asyncio
import json
import socket
import unittest

import websockets

from dglab.ble import (
    build_bmtr_50,
    build_bmtr_66,
    build_ovc_b0,
    build_ovc_b2,
    build_ovc_b3,
    build_ovc_50,
    parse_bmtr_pressure,
    parse_ovc_buttons,
)
from dglab.official_waveforms_ovc import OVC_WAVEFORMS, OvcWaveform
from dglab.relay_v3 import RelayV3Server
from dglab.relay_v4 import RelayV4Server
from dglab.socket_v3 import SocketV3Client
from dglab.socket_v4 import SocketV4Client
from dglab.state import StateEvents
from dglab.waves import CONTINUOUS, ovc_channel_pattern, resolve_wave_frames
from dglab.official_waveforms_ovc import OvcWaveform as OvcEnum


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class OvcWaveTests(unittest.TestCase):
    def test_official_ovc_frames(self):
        for wave in OvcEnum:
            raw = OVC_WAVEFORMS[wave]["raw"]
            self.assertTrue(len(raw) >= 1, wave)
            for frame in raw:
                self.assertEqual(len(frame), 16, wave)
                self.assertEqual(frame[:8].lower(), "0a0a0a0a", wave)
                strengths = ovc_channel_pattern(frame)
                for s in strengths:
                    self.assertTrue(0 <= s <= 100, (wave, frame))

    def test_resolve_for_device_type(self):
        frames = resolve_wave_frames(OvcWaveform.ALARM, "OVC_1")
        self.assertTrue(frames)
        self.assertEqual(frames[0][8:16].lower(), "64646464")
        with self.assertRaises(KeyError):
            resolve_wave_frames(OvcWaveform.ALARM, "COYOTE_030")
        self.assertEqual(resolve_wave_frames(CONTINUOUS, "OVC_1")[0][8:].lower(), "64646464")
        self.assertEqual(len(resolve_wave_frames(CONTINUOUS, "COYOTE_030")[0]), 16)


class BleFrameTests(unittest.TestCase):
    def test_ovc_b0(self):
        frame = build_ovc_b0([10, 20, 30, 40], [100, 50, 0, 100])
        self.assertEqual(len(frame), 20)
        self.assertEqual(frame[0], 0xB0)
        self.assertEqual(bytes(frame[1:8]), bytes(7))
        self.assertEqual(bytes(frame[8:12]), bytes([10, 20, 30, 40]))
        self.assertEqual(bytes(frame[12:16]), bytes(4))
        self.assertEqual(bytes(frame[16:20]), bytes([100, 50, 0, 100]))

    def test_ovc_b3_none_keeps_channel(self):
        self.assertEqual(build_ovc_b3(160, None), bytes([0xB3, 0xA0, 0xFF]))
        self.assertEqual(build_ovc_b3(None, 200), bytes([0xB3, 0xFF, 0xC8]))

    def test_ovc_b2(self):
        frame = build_ovc_b2(10, 20)
        self.assertEqual(len(frame), 24)
        self.assertEqual(frame[0], 0xB2)
        self.assertEqual(frame[22], 10)
        self.assertEqual(frame[23], 20)

    def test_ovc_50(self):
        self.assertEqual(build_ovc_50(True), bytes([0x50, 0x01, 0x01]))

    def test_bmtr_frames(self):
        frame = build_bmtr_50(True)
        self.assertEqual(len(frame), 17)
        self.assertEqual(frame[0], 0x50)
        self.assertEqual(frame[2], 0xD0)
        reset = build_bmtr_66(reset_pressure=True)
        self.assertEqual(len(reset), 13)
        self.assertEqual(reset[0], 0x66)
        self.assertEqual(reset[11:13], b"\x00\x02")

    def test_bmtr_pressure_parse(self):
        data = bytearray(15)
        data[0] = 0xD0
        data[8:10] = (790).to_bytes(2, "little")
        self.assertAlmostEqual(parse_bmtr_pressure(data), 7.9)
        self.assertIsNone(parse_bmtr_pressure(b"\x00" * 10))

    def test_ovc_buttons(self):
        data = bytearray(16)
        data[0] = 0xD0
        data[2:4] = (0b101 << 8).to_bytes(2, "big")
        self.assertEqual(parse_ovc_buttons(data), [8, 10])


class RelayV4Tests(unittest.TestCase):
    def test_roundtrip(self):
        asyncio.run(self._roundtrip())

    async def _roundtrip(self):
        events = StateEvents()
        logs: list[str] = []
        events.on("log", lambda m: logs.append(m))
        port = free_port()
        relay = RelayV4Server(port=port, events=events)
        await relay.start()
        try:
            client = SocketV4Client(f"ws://127.0.0.1:{port}", events=events)
            await client.connect()
            self.assertTrue(client.state.client_id, "hello/targetId missing")

            async with websockets.connect(f"ws://127.0.0.1:{port}/?tid={client.state.client_id}") as app:
                hello = json.loads(await asyncio.wait_for(app.recv(), 5))
                self.assertEqual(hello["type"], "hello")
                app_id = hello["clientId"]
                attached = json.loads(await asyncio.wait_for(app.recv(), 5))
                self.assertEqual(attached["type"], "controller_attached")

                await asyncio.sleep(0.2)
                self.assertTrue(client.state.paired)
                self.assertEqual(client.state.target_id, app_id)

                await app.send(json.dumps({
                    "type": "message",
                    "data": {"t": "ev", "ev": "devices.snapshot",
                             "devices": [{"slotId": "s1", "name": "负鼠", "type": "OVC_1",
                                          "props": {"intensityA": 3, "power": 55}}]},
                }))
                await asyncio.sleep(0.3)
                slot = client.state.slots.get("s1")
                self.assertIsNotNone(slot)
                self.assertEqual(slot.type, "OVC_1")
                self.assertEqual(slot.strength["A"], 3)
                self.assertEqual(slot.battery, 55)

                fut = asyncio.ensure_future(client.add_intensity("A", 5))
                got_frame = json.loads(await asyncio.wait_for(app.recv(), 5))
                while not (got_frame["data"].get("m") == "device.op"
                           and got_frame["data"]["data"].get("t") == 3):
                    result = ({"devices": []}
                              if got_frame["data"].get("m") == "devices.get"
                              else {"type": 0, "reason": "completed"})
                    await app.send(json.dumps({
                        "type": "message",
                        "data": {"t": "resp", "reqId": got_frame["data"]["reqId"],
                                 "result": result},
                    }))
                    got_frame = json.loads(await asyncio.wait_for(app.recv(), 5))
                self.assertEqual(got_frame["type"], "message")
                self.assertEqual(got_frame["data"]["m"], "device.op")
                self.assertEqual(got_frame["data"]["data"]["t"], 3)
                await app.send(json.dumps({
                    "type": "message",
                    "data": {"t": "resp", "reqId": got_frame["data"]["reqId"], "result": {}},
                }))
                await asyncio.wait_for(fut, 5)

            await asyncio.sleep(0.4)
            self.assertFalse(client.state.paired)
            await client.disconnect()
        finally:
            await relay.stop()


class RelayV3Tests(unittest.TestCase):
    def test_roundtrip(self):
        asyncio.run(self._roundtrip())

    async def _roundtrip(self):
        events = StateEvents()
        port = free_port()
        relay = RelayV3Server(port=port, events=events)
        await relay.start()
        try:
            client = SocketV3Client(f"ws://127.0.0.1:{port}", events=events)
            await client.connect()
            self.assertTrue(client.state.client_id, "bind targetId missing")

            async with websockets.connect(
                f"ws://127.0.0.1:{port}/{client.state.client_id}"
            ) as app:
                app_bind = json.loads(await asyncio.wait_for(app.recv(), 5))
                self.assertEqual(app_bind["type"], "bind")
                self.assertEqual(app_bind["message"], "targetId")
                pair_frame = json.loads(await asyncio.wait_for(app.recv(), 5))
                self.assertEqual(pair_frame["type"], "bind")
                self.assertEqual(pair_frame["message"], "200")
                self.assertEqual(pair_frame["clientId"], client.state.client_id)

                await asyncio.sleep(0.2)
                self.assertTrue(client.state.paired)

                await client.set_strength("A", 35)
                frame = json.loads(await asyncio.wait_for(app.recv(), 5))
                self.assertEqual(frame["type"], "msg")
                self.assertEqual(frame["message"], "strength-1+2+35")

                await app.send(json.dumps({
                    "type": "msg", "clientId": client.state.client_id,
                    "targetId": app_bind["clientId"],
                    "message": "strength-11-7-100-35",
                }))
                await asyncio.sleep(0.3)
                slot = client.state.slots["coyote"]
                self.assertEqual(slot.strength, {"A": 11, "B": 7})
                self.assertEqual(slot.strength_limit, {"A": 100, "B": 35})

                from dglab.official_waveforms import COYOTE_WAVEFORMS, CoyoteWaveform
                await client.send_wave("A", CoyoteWaveform.BUBBLE, 1.0)
                got_pulse = None
                for _ in range(3):
                    frame = json.loads(await asyncio.wait_for(app.recv(), 3))
                    if isinstance(frame.get("message"), str) and frame["message"].startswith("pulse-A:"):
                        got_pulse = frame["message"]
                        break
                self.assertIsNotNone(got_pulse)
                frames = json.loads(got_pulse.split(":", 1)[1])
                self.assertTrue(all(len(f) == 16 for f in frames))

            await asyncio.sleep(0.4)
            self.assertFalse(client.state.paired)
            await client.disconnect()
        finally:
            await relay.stop()


if __name__ == "__main__":
    unittest.main()

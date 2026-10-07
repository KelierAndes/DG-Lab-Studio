from __future__ import annotations

import asyncio
import sys
import time

sys.path.insert(0, ".")

from modules.alice_cradle.server import GameDataServer  # noqa: E402


class FakeSlot:
    def __init__(self):
        self.name = "COYOTE-03 (stub)"
        self.type = "COYOTE"
        self.battery = 88
        self.strength = {"A": 0, "B": 0}
        self.strength_limit = {"A": 200, "B": 200}
        self.channel_status = {"A": 0, "B": 0}
        self.pressure = None
        self.edge_state = None
        self.is_output_device = True


class FakeState:
    def __init__(self):
        self.slots = {"stub": FakeSlot()}
        self.paired = True
        self.connected = True


class FakeCtx:
    def __init__(self):
        self.settings: dict = {}
        self.state = FakeState()

    def log(self, msg):
        print(f"{time.strftime('%H:%M:%S')} [hub] {msg}", flush=True)

    def resolve_slot(self, slot_id=None, family=None, output_only=False):
        return "stub"

    def get_state(self):
        return self.state

    def wave_order(self, family="COYOTE"):
        return ["静默", "呼吸", "波浪"]

    def wave_selection(self) -> dict:
        return {"A": "呼吸", "B": ""}

    async def set_strength(self, channel, value, slot_id=None):
        self.state.slots["stub"].strength[channel] = value
        print(f"{time.strftime('%H:%M:%S')} [stub] 通道 {channel} 强度 -> {value}",
              flush=True)

    async def zap(self, channel, seconds=1.0, slot_id=None):
        print(f"{time.strftime('%H:%M:%S')} [stub] 通道 {channel} 开火 {seconds:.1f}s",
              flush=True)

    async def set_wave(self, channel, name, slot_id=None):
        print(f"{time.strftime('%H:%M:%S')} [stub] 通道 {channel} 波形 -> {name}",
              flush=True)

    async def fire_start(self, slot_id=None):
        print(f"{time.strftime('%H:%M:%S')} [stub] 开火开始", flush=True)

    async def fire_stop(self, slot_id=None):
        print(f"{time.strftime('%H:%M:%S')} [stub] 开火停止", flush=True)

    async def emergency_stop(self):
        print(f"{time.strftime('%H:%M:%S')} [stub] 急停", flush=True)


async def main() -> None:
    ctx = FakeCtx()
    srv = GameDataServer(ctx, {
        "port": 8920, "family": "COYOTE",
        "mappings": [{"target": "in_strength_a",
                      "expr": "{HP}/{HPmax}*200"}],
    })
    original = srv._route

    async def traced(method, path, body):
        status, payload = await original(method, path, body)
        note = ""
        if method == "POST":
            note = f" body={body.decode('utf-8', 'replace')}"
        print(f"{time.strftime('%H:%M:%S')} [req] {method} {path} -> {status}"
              f"{note}", flush=True)
        return status, payload

    srv._route = traced
    await srv.start()
    print("stub ready: http://127.0.0.1:8920  POST/GET /data  (Ctrl+C 退出)",
          flush=True)
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await srv.stop()


if __name__ == "__main__":
    asyncio.run(main())

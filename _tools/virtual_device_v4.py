"""虚拟 DG-Lab 设备（Socket V4 被控方），用于宣传素材录制。

接入 DGStudio 本地中继，模拟郊狼 / 负鼠 / 灵猫三台设备：
应答 devices.get、上报 devices.snapshot、处理 device.op（强度/临时/脉冲/清空）、
周期回传强度反馈 / 电量 / 气压，让「控制」页卡片与实时波形有真实联动观感。

用法：
    python virtual_device_v4.py --url ws://127.0.0.1:9998 [--tid XXXXXXXX] [--log LOGFILE]

--tid 缺省时从 --log（dgstudio.log）读取最近一条 targetId=，连接断开后会自动重读重连。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import random
import re
import sys
import time

import websockets

CHANNELS = ("A", "B")

# slotId, 名称, 类型, 初始强度 A/B, 强度上限 A/B, 电量
DEVICES = [
    {"slotId": "cy01", "name": "郊狼 Coyote 3.0", "type": "COYOTE_030",
     "strength": {"A": 0, "B": 0}, "limit": {"A": 100, "B": 100}, "power": 78},
    {"slotId": "ovc01", "name": "负鼠 OVC 振动", "type": "OVC_1",
     "strength": {"A": 0, "B": 0}, "limit": {"A": 200, "B": 200}, "power": 55},
    {"slotId": "bmtr01", "name": "灵猫 BMTR 气压", "type": "BMTR_1",
     "strength": {"A": 0, "B": 0}, "limit": {"A": 0, "B": 0}, "power": 66},
]


def dev_by_sid(sid: str) -> dict | None:
    for dev in DEVICES:
        if dev["slotId"] == sid:
            return dev
    return None


def dev_record(dev: dict) -> dict:
    if dev["type"].startswith("BMTR"):
        props = {"pressure": round(dev.get("pressure", 0.0), 2), "power": dev["power"],
                 "channelAStatus": True, "channelBStatus": False}
    else:
        props = {"intensityA": dev["strength"]["A"], "intensityB": dev["strength"]["B"],
                 "power": dev["power"],
                 "channelAStatus": True, "channelBStatus": True}
    return {
        "slotId": dev["slotId"], "name": dev["name"], "type": dev["type"],
        "props": props,
        "slotState": {
            "channelA": {"intensityMax": dev["limit"]["A"]},
            "channelB": {"intensityMax": dev["limit"]["B"]},
        },
    }


class VirtualDevice:
    def __init__(self, url: str, tid: str | None, log_path: str | None, temp_hold: float = 0.0):
        self.url = url
        self.tid = tid
        self.log_path = log_path
        self.temp_hold = temp_hold
        self.ws = None
        self.pressure_phase = 0.0
        self.start_ts = time.monotonic()

    def log(self, msg: str) -> None:
        print(f"[虚拟设备] {msg}", flush=True)

    def read_tid_from_log(self) -> str | None:
        if not self.log_path:
            return None
        try:
            with open(self.log_path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
        except OSError:
            return None
        for line in reversed(lines):
            m = re.search(r"targetId=([0-9a-fA-F]+)", line)
            if m:
                return m.group(1)
        return None

    async def send(self, data: dict) -> None:
        if self.ws is not None:
            await self.ws.send(json.dumps({"type": "message", "data": data}, ensure_ascii=False))

    async def send_resp(self, req_id: str, result: dict) -> None:
        await self.send({"t": "resp", "reqId": req_id, "result": result})

    async def announce(self) -> None:
        await self.send({"t": "ev", "ev": "devices.snapshot", "devices": [dev_record(d) for d in DEVICES]})
        self.log(f"已上报 {len(DEVICES)} 台虚拟设备")

    async def patch_props(self, sid: str, props: dict) -> None:
        await self.send({"t": "ev", "ev": "slots.patch",
                         "slots": [{"slotId": sid, "props": props}]})

    async def handle_req(self, data: dict) -> None:
        m = str(data.get("m") or "")
        req_id = str(data.get("reqId") or data.get("requestId") or "")
        payload = data.get("data") or {}
        if m == "devices.get":
            await self.send_resp(req_id, {"devices": [dev_record(d) for d in DEVICES]})
            return
        if m == "device.op.clear":
            for dev in DEVICES:
                dev["strength"] = {"A": 0, "B": 0}
            for dev in DEVICES:
                if not dev["type"].startswith("BMTR"):
                    await self.patch_props(dev["slotId"],
                                           {"intensityA": 0, "intensityB": 0})
            self.log("队列清空，全部强度归零")
            await self.send_resp(req_id, {})
            return
        if m != "device.op":
            await self.send_resp(req_id, {})
            return
        sid = str(payload.get("s") or "")
        dev = dev_by_sid(sid)
        op_t = payload.get("t")
        channel = CHANNELS[int(payload.get("c", 0))] if str(payload.get("c", 0)) in ("0", "1") else "A"
        if dev is None or dev["type"].startswith("BMTR"):
            await self.send_resp(req_id, {})
            return
        if op_t == 3:  # 加减强度
            delta = float(payload.get("v", 0))
            old = dev["strength"][channel]
            dev["strength"][channel] = max(0, min(dev["limit"][channel], old + int(delta)))
            self.log(f"{sid} 通道 {channel} 强度 {old} → {dev['strength'][channel]} (Δ{delta:g})")
        elif op_t == 7:  # 直接设置 / 归零
            value = int(float(payload.get("v", 0)))
            old = dev["strength"][channel]
            dev["strength"][channel] = max(0, min(dev["limit"][channel], value))
            self.log(f"{sid} 通道 {channel} 强度 {old} → {dev['strength'][channel]} (直设)")
        elif op_t == 4:  # 临时强度（开火）
            value = int(float(payload.get("v", 0)))
            duration_ms = int(payload.get("d", 1000))
            self.log(f"{sid} 通道 {channel} 临时强度 {value} / {duration_ms}ms (开火)")
            asyncio.create_task(self._temp_strength(dev, channel, value, duration_ms))
        elif op_t == 0:  # 脉冲帧
            frames = payload.get("v") or []
            self.log(f"{sid} 通道 {channel} 脉冲 {len(frames)} 帧")
        await self.send_resp(req_id, {})
        if op_t in (3, 7):  # 临时强度(t=4)由 _temp_strength 任务负责回传，避免立刻被当前值覆盖
            await self.patch_props(sid, {"intensityA": dev["strength"]["A"],
                                         "intensityB": dev["strength"]["B"]})

    async def _temp_strength(self, dev: dict, channel: str, value: int, duration_ms: int) -> None:
        await self.patch_props(dev["slotId"], {f"intensity{channel}": value})
        hold_s = self.temp_hold or max(0.05, duration_ms / 1000)
        await asyncio.sleep(hold_s)
        await self.patch_props(dev["slotId"], {f"intensity{channel}": dev["strength"][channel]})

    async def telemetry_loop(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            try:
                self.pressure_phase += 0.35
                base = 18 + 12 * math.sin(self.pressure_phase)
                noise = random.uniform(-1.2, 1.2)
                pressure = max(0.0, min(60.0, base + noise))
                bmtr = dev_by_sid("bmtr01")
                if bmtr is not None:
                    bmtr["pressure"] = pressure
                    await self.patch_props("bmtr01", {"pressure": round(pressure, 2)})
                if (time.monotonic() - self.start_ts) % 20 < 1.0:
                    for dev in DEVICES:
                        if dev["power"] > 5:
                            dev["power"] -= 1
                            await self.patch_props(dev["slotId"], {"power": dev["power"]})
            except Exception as exc:
                self.log(f"遥测异常: {exc!r}")

    async def session(self) -> None:
        assert self.ws is not None
        hello = json.loads(await asyncio.wait_for(self.ws.recv(), 10))
        if hello.get("type") != "hello":
            raise RuntimeError(f"预期 hello，收到: {hello}")
        my_id = hello.get("clientId")
        await self.ws.send(json.dumps({"type": "pong"}))
        attach = json.loads(await asyncio.wait_for(self.ws.recv(), 10))
        if attach.get("type") != "controller_attached":
            raise RuntimeError(f"配对失败: {attach}")
        self.log(f"被控方 {my_id} 已接入控制方 {attach.get('clientId')}")
        await self.announce()
        telemetry = asyncio.create_task(self.telemetry_loop())
        try:
            async for raw in self.ws:
                try:
                    frame = json.loads(raw)
                except Exception:
                    continue
                ftype = frame.get("type")
                if ftype == "heartbeat":
                    continue
                if ftype == "ping":
                    await self.ws.send(json.dumps({"type": "pong"}))
                    continue
                if ftype == "message" and isinstance(frame.get("data"), dict):
                    data = frame["data"]
                    if data.get("t") == "req":
                        await self.handle_req(data)
        finally:
            telemetry.cancel()

    async def run(self) -> None:
        while True:
            if not self.tid and self.log_path:
                self.tid = self.read_tid_from_log()
            if not self.tid:
                self.log("尚未读到 targetId，3s 后重试 (等待 DGStudio 建立 V4 连接)…")
                await asyncio.sleep(3)
                continue
            url = f"{self.url.rstrip('/')}/?tid={self.tid}"
            try:
                async with websockets.connect(url, max_size=2**22) as ws:
                    self.ws = ws
                    self.log(f"已连接 {url}")
                    await self.session()
            except Exception as exc:
                self.log(f"连接结束: {exc!r}，3s 后重连")
            self.ws = None
            self.tid = None  # 重读日志，跟随控制方重连后的新 tid
            await asyncio.sleep(3)


def main() -> int:
    parser = argparse.ArgumentParser(description="DGStudio 虚拟设备 (Socket V4)")
    parser.add_argument("--url", default="ws://127.0.0.1:9998")
    parser.add_argument("--tid", default=None)
    parser.add_argument("--log", default=None, help="dgstudio.log 路径，用于自动读取 targetId")
    parser.add_argument("--temp-hold", type=float, default=0.0,
                        help="临时强度（开火）保压秒数，0 = 按请求时长；仅录制素材时使用")
    args = parser.parse_args()
    try:
        asyncio.run(VirtualDevice(args.url, args.tid, args.log, args.temp_hold).run())
    except KeyboardInterrupt:
        print("已退出", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

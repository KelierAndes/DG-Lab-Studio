"""界面桥自检：起侧车、握手、鉴权、取状态与模块，并跑一次会失败的调用看错误通道。

用法：python _tools/bridge_probe.py            # 打印 JSON 报告
      python _tools/bridge_probe.py --seconds 8  # 多听几秒推送
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time

from websockets.asyncio.client import connect

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 变量表自检用名：加完改名再删掉，不留在存档里。
PROBE_VAR = "probeVar"


async def probe(seconds: float) -> dict:
    report: dict = {"steps": [], "pushes": [], "replies": [], "errors": []}
    proc = subprocess.Popen(
        [sys.executable, "-m", "bridge.server", "--handshake"],
        cwd=ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )

    def step(name, ok, detail=""):
        report["steps"].append({"step": name, "ok": bool(ok), "detail": str(detail)[:200]})

    try:
        deadline = time.time() + 25
        hello = None
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            line = line.strip()
            if line.startswith("{") and '"bridge"' in line:
                hello = json.loads(line)
                break
        if not hello:
            step("handshake", False, "没读到握手行")
            report["stderr"] = (proc.stderr.read() or "")[-2000:]
            return report
        step("handshake", True, f"port={hello['port']}")

        async with connect(f"ws://127.0.0.1:{hello['port']}/ws") as ws:
            seq = 0
            pending: dict[int, str] = {}

            async def call(method, params=None):
                nonlocal seq
                seq += 1
                pending[seq] = method
                await ws.send(json.dumps({"id": seq, "method": method, "params": params or {}}))
                return seq

            await call("bridge.auth", {"token": hello["token"]})
            stop = time.time() + seconds
            while time.time() < stop:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, stop - time.time()))
                except asyncio.TimeoutError:
                    break
                msg = json.loads(raw)
                if "event" in msg:
                    report["pushes"].append({"event": msg["event"], "size": len(raw)})
                    if msg["event"] == "snapshot":
                        report["snapshot"] = msg["data"]
                    continue
                method = pending.pop(msg.get("id"), "?")
                report["replies"].append({"method": method, "ok": msg.get("ok"), "error": msg.get("error")})
                if method == "bridge.auth":
                    if not msg.get("ok"):
                        step("auth", False, msg.get("error"))
                        return report
                    step("auth", True)
                    await call("engine.state")
                    await call("engine.modules_list")
                    await call("engine.config_get")
                    await call("engine.set_strength", {"channel": "A", "value": 20})
                    await call("engine.nope")
                    await call("flow.vars", {"page": "input"})
                if method == "engine.state":
                    report["state_result_keys"] = sorted((msg.get("result") or {}).keys())
                if method == "engine.modules_list":
                    report["modules"] = [m.get("id") for m in (msg.get("result") or [])]
                if method == "engine.config_get":
                    cfg = msg.get("result") or {}
                    report["config_keys"] = sorted(cfg.keys())
                    report["relay"] = cfg.get("relay")
                    report["waveforms"] = len(cfg.get("waveforms") or [])
                if method == "flow.vars":
                    report["vars_before"] = msg.get("result")
                    await call("flow.var_add", {"name": PROBE_VAR, "page": "input"})
                if method == "flow.var_add":
                    rows = (msg.get("result") or {}).get("renamable") or []
                    mine = next((r for r in rows if r.get("name") == PROBE_VAR), {})
                    report["var_added"] = mine
                    step("var.card", bool(mine.get("card")), mine.get("card"))
                    await call("flow.var_dir", {"name": PROBE_VAR, "dir": "in", "page": "input"})
                if method == "flow.var_dir":
                    rows = (msg.get("result") or {}).get("renamable") or []
                    mine = next((r for r in rows if r.get("name") == PROBE_VAR), {})
                    report["var_dir_in"] = mine
                    await call("flow.var_rename",
                               {"name": PROBE_VAR, "new": f"{PROBE_VAR}2", "page": "input"})
                if method == "flow.var_rename":
                    rows = (msg.get("result") or {}).get("renamable") or []
                    report["var_renamed"] = next((r for r in rows if r.get("name") == f"{PROBE_VAR}2"), {})
                    await call("flow.var_remove", {"name": f"{PROBE_VAR}2", "page": "input"})
                if method == "flow.var_remove":
                    rows = (msg.get("result") or {}).get("renamable") or []
                    step("var.cleanup", not any(r.get("name") == f"{PROBE_VAR}2" for r in rows),
                        f"残留 {len(rows)} 行")
                if not pending:
                    break

        step("close", proc.poll() is None, "侧车仍在运行")
        return report
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=6.0)
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    report = asyncio.run(probe(args.seconds))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if all(s["ok"] for s in report["steps"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())

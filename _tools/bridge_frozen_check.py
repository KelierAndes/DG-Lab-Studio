"""对冻结侧车 exe 跑一遍桥自检：握手、鉴权、几个方法调用。

用法：python _tools/bridge_frozen_check.py [dist/DGStudio/DGStudio-bridge.exe]
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time

from websockets.asyncio.client import connect

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_EXE = os.path.join(ROOT, "build", "pyi_test", "DGStudio", "DGStudio-bridge.exe")


async def check(exe: str) -> dict:
    home = os.path.dirname(os.path.abspath(exe))
    hello_path = os.path.join(home, "_probe_handshake.json")
    if os.path.isfile(hello_path):
        os.remove(hello_path)
    proc = subprocess.Popen([exe, "--handshake-file", hello_path, "--parent", str(os.getpid())],
                            cwd=home, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8")
    report: dict = {"exe": exe, "steps": [], "replies": []}
    try:
        hello = None
        deadline = time.time() + 40
        while time.time() < deadline:
            if os.path.isfile(hello_path):
                try:
                    with open(hello_path, encoding="utf-8") as fp:
                        text = fp.read().strip()
                    if text:
                        hello = json.loads(text)
                        break
                except (OSError, json.JSONDecodeError):
                    pass
            if proc.poll() is not None:
                break
            time.sleep(0.2)
        report["steps"].append({"step": "handshake", "ok": bool(hello), "detail": str(hello)[:120]})
        if not hello:
            return report

        seq = 0
        pending: dict[int, str] = {}
        calls = ["engine.state", "engine.modules_list", "engine.config_get", "flow.state",
                 "flow.vars", "settings.info", "bindings.get", "detectors.state",
                 "modules.deps", "flow.nodes_move", "engine.channels", "engine.qr",
                 "engine.nope"]
        # 这几个方法要带模块号，缺了就直接报错，测不出方法本身通不通。
        params = {"page": "input", "module_id": "vision_link", "channel": "v4",
                  "moves": {}}
        async with connect(f"ws://127.0.0.1:{hello['port']}/ws") as ws:
            async def call(method, params=None):
                nonlocal seq
                seq += 1
                pending[seq] = method
                await ws.send(json.dumps({"id": seq, "method": method, "params": params or {}}))

            await call("bridge.auth", {"token": hello["token"]})
            # 同一条连接上的消息按序处理，鉴权排在最前，后面这些可以一次发出。
            for name in calls:
                await call(name, params)
            stop = time.time() + 60
            while time.time() < stop and pending:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=max(0.2, stop - time.time()))
                except Exception:
                    break
                msg = json.loads(raw)
                if "event" in msg:
                    continue
                method = pending.pop(msg.get("id"), "?")
                result = msg.get("result")
                size = len(json.dumps(result, ensure_ascii=False)) if result is not None else 0
                report["replies"].append({"method": method, "ok": msg.get("ok"),
                                          "error": msg.get("error"), "size": size})
        report["steps"].append({"step": "alive", "ok": proc.poll() is None,
                                "detail": f"exit={proc.returncode}"})
        return report
    finally:
        try:
            proc.kill()
        except Exception:
            pass
        if os.path.isfile(hello_path):
            os.remove(hello_path)


def main() -> int:
    exe = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_EXE
    sys.stdout.reconfigure(encoding="utf-8")
    report = asyncio.run(check(exe))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if all(s["ok"] for s in report["steps"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())

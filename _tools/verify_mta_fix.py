"""验证引擎线程 COM 套间与 bleak 的兼容性(蓝牙扫描失败根因)。

场景1: 音频模块先初始化(PortAudio WASAPI → CoInitialize(NULL) = STA)→
       bleak assert_mta 探测消息泵失败 → BleakError(复现用户报错)。
场景2: 线程先固定为 MTA(app.py _run_loop 的修复)→ 音频初始化被
       RPC_E_CHANGED_MODE 挡掉、套间保持 MTA → bleak 通过。
"""
import asyncio
import ctypes
import threading


def apartment_type() -> str:
    t, q = ctypes.c_int(), ctypes.c_int()
    hr = ctypes.windll.ole32.CoGetApartmentType(ctypes.byref(t), ctypes.byref(q))
    if hr != 0:
        return f"未初始化(hr=0x{hr & 0xFFFFFFFF:08X})"
    return {0: "STA", 1: "MTA", 2: "NA", 3: "MAIN_STA"}.get(t.value, str(t.value))


def scenario(name: str, fix_mta_first: bool, with_audio: bool) -> None:
    result: dict = {}

    def run() -> None:
        async def main() -> None:
            if fix_mta_first:
                ctypes.windll.ole32.CoInitializeEx(None, 0x0)  # COINIT_MULTITHREADED
            if with_audio:
                import pyaudiowpatch as pyaudio
                # 真实场景中音频流在蓝牙扫描期间保持打开,故此处不 terminate
                pa = pyaudio.PyAudio()
                result["pa"] = pa
            result["apt"] = apartment_type()
            from bleak.backends.winrt.util import assert_mta
            from bleak.exc import BleakError
            try:
                await asyncio.wait_for(assert_mta(), timeout=2.0)
                result["bleak"] = "通过"
            except BleakError as exc:
                result["bleak"] = f"BleakError: {exc}"
            except Exception as exc:
                result["bleak"] = f"{type(exc).__name__}: {exc}"

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(main())
        finally:
            loop.close()

    t = threading.Thread(target=run, name=name, daemon=True)
    t.start()
    t.join(30)
    print(f"[{name}] 套间={result.get('apt')} bleak={result.get('bleak')}")


if __name__ == "__main__":
    scenario("复现:音频先初始化(修复前)", fix_mta_first=False, with_audio=True)
    scenario("验证:MTA 先初始化(修复后)", fix_mta_first=True, with_audio=True)

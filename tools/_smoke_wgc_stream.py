"""smoke test: WGC 集成进 ADBController 后能否正常 screenshot()

跑法（Windows）：
    set GAMEBOT_CAPTURE=wgc
    python tools/_smoke_wgc_stream.py emulator-5564
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 在 import 前设置 env
os.environ["GAMEBOT_CAPTURE"] = "wgc"

from backend.automation.adb_lite import ADBController


async def main():
    serial = sys.argv[1] if len(sys.argv) > 1 else "emulator-5564"
    adb = r"D:\leidian\LDPlayer9\adb.exe"

    ctl = ADBController(serial, adb)
    print(f"[1/4] setup_minicap() -> WGCStream...")
    ok = ctl.setup_minicap()
    print(f"  ok={ok}, _stream={type(ctl._stream).__name__ if ctl._stream else None}")

    if not ok:
        print("  FAIL: setup")
        sys.exit(1)

    print(f"[2/4] sleep 1s 让首帧到位...")
    await asyncio.sleep(1)

    print(f"[3/4] screenshot()...")
    t0 = time.perf_counter()
    frame = await ctl.screenshot()
    dt = (time.perf_counter() - t0) * 1000
    if frame is None:
        print(f"  FAIL: screenshot returned None ({dt:.0f}ms)")
        sys.exit(1)
    print(f"  shape={frame.shape}, dtype={frame.dtype}, mean={frame.mean():.1f}, latency={dt:.0f}ms")

    # 保存验证
    import cv2
    out_path = "captures/wgc_stream_smoke.png"
    cv2.imwrite(out_path, frame)
    print(f"  saved {out_path}")

    print(f"[4/4] 10 次连续 screenshot 测延迟分布...")
    latencies = []
    for i in range(10):
        await asyncio.sleep(0.1)
        t0 = time.perf_counter()
        f = await ctl.screenshot()
        dt = (time.perf_counter() - t0) * 1000
        latencies.append(dt)
    avg = sum(latencies) / len(latencies)
    print(f"  10 calls: avg={avg:.1f}ms min={min(latencies):.1f}ms max={max(latencies):.1f}ms")

    if avg < 50:
        print("  [OK] avg < 50ms")
    else:
        print(f"  [WARN] avg {avg:.0f}ms > 50ms")

    if ctl._stream:
        ctl._stream.stop()


if __name__ == "__main__":
    asyncio.run(main())

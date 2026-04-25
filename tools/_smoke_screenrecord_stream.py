"""smoke test: ScreenrecordStream 集成到 ADBController 后能否正常 screenshot()"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.automation.adb_lite import ADBController


async def main():
    serial = sys.argv[1] if len(sys.argv) > 1 else "emulator-5554"
    adb = r"D:\leidian\LDPlayer9\adb.exe"

    ctl = ADBController(serial, adb)
    print(f"[1/4] setup_minicap() -> ScreenrecordStream...")
    ok = ctl.setup_minicap()
    print(f"  ok={ok}, _stream={ctl._stream}, available={ctl._stream.available if ctl._stream else None}")

    print(f"[2/4] sleep 1.5s 让首帧到位...")
    await asyncio.sleep(1.5)

    print(f"[3/4] screenshot()...")
    import time
    t0 = time.perf_counter()
    frame = await ctl.screenshot()
    dt = (time.perf_counter() - t0) * 1000
    if frame is None:
        print(f"  FAIL: screenshot returned None ({dt:.0f}ms)")
        sys.exit(1)
    print(f"  shape={frame.shape}, mean={frame.mean():.1f}, latency={dt:.0f}ms")

    print(f"[4/4] 5 次连续 screenshot 测延迟...")
    latencies = []
    for i in range(5):
        await asyncio.sleep(0.3)
        t0 = time.perf_counter()
        frame = await ctl.screenshot()
        dt = (time.perf_counter() - t0) * 1000
        latencies.append(dt)
        print(f"  call#{i+1}: {dt:.0f}ms ({'ok' if frame is not None else 'NULL'})")

    avg = sum(latencies) / len(latencies)
    print(f"\nresult: avg={avg:.0f}ms min={min(latencies):.0f}ms max={max(latencies):.0f}ms")
    if avg < 100:
        print("[OK] screenshot avg latency < 100ms")
    else:
        print(f"[WARN] screenshot avg latency {avg:.0f}ms > 100ms target")

    if ctl._stream:
        ctl._stream.stop()


if __name__ == "__main__":
    asyncio.run(main())

"""Phase A 验证：UE4 + LDPlayer SurfaceFlinger 路径能否拿到非黑画面

跑法（Windows，LDPlayer 6 实例已打开）:
    python tools/test_capture_path.py

输出 captures/inst_<idx>.png 6 张图，每张打印 mean 像素值。
mean > 30 = 非黑 = scrcpy 路径可走。全黑 = 必须回退 vpn-app 集成。

依赖：
    pip install av numpy opencv-python
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

OUT_DIR = Path("captures")
OUT_DIR.mkdir(exist_ok=True)

# LDPlayer 9 默认 5554/5556/5558/5560/5562/5564
SERIALS = [f"emulator-{5554 + i*2}" for i in range(6)]
DURATION = 12  # 秒（静态画面下 H.264 IDR 间隔可能很长，要给足时间）
TARGET_FRAME = 5   # 取第 N 帧（早点拿，画面静态时编码器懒）


def adb_path() -> str:
    """LDPlayer 9 默认 adb 路径，找不到就用系统 adb"""
    candidates = [
        r"C:\leidian\LDPlayer9\adb.exe",
        r"D:\leidian\LDPlayer9\adb.exe",
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return "adb"


def capture_one(adb: str, serial: str, idx: int) -> tuple[bool, str]:
    """对一个 serial 跑 screenrecord pipe，取 1 帧解码保存"""
    out_png = OUT_DIR / f"inst_{idx}_{serial}.png"

    # 启 adb shell screenrecord 输出 h264 到 stdout
    cmd = [
        adb, "-s", serial, "exec-out",
        "screenrecord",
        f"--time-limit={DURATION}",
        "--output-format=h264",
        "--bit-rate", "2000000",
        "-",
    ]

    h264_path = OUT_DIR / f"inst_{idx}_{serial}.h264"

    try:
        # 直接落盘成 .h264 文件（避免 PyAV 不能 seek pipe 的问题）
        with open(h264_path, "wb") as f:
            proc = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE,
                                  timeout=DURATION + 5)
    except subprocess.TimeoutExpired:
        return False, "screenrecord timeout"
    except FileNotFoundError:
        return False, f"adb not found: {adb}"

    if not h264_path.exists() or h264_path.stat().st_size < 1000:
        return False, f"h264 too small: {h264_path.stat().st_size if h264_path.exists() else 0} bytes"

    try:
        import av
    except ImportError:
        return False, "PyAV not installed (pip install av)"

    try:
        container = av.open(str(h264_path), format="h264", mode="r")
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"

        frame_n = 0
        captured = None
        for packet in container.demux(stream):
            for frame in packet.decode():
                frame_n += 1
                if frame_n >= TARGET_FRAME or (captured is None and frame_n >= 5):
                    captured = frame.to_ndarray(format="bgr24")
                    if frame_n >= TARGET_FRAME:
                        break
            if captured is not None and frame_n >= TARGET_FRAME:
                break

        container.close()
    except Exception as e:
        return False, f"decode error: {e} (h264 size={h264_path.stat().st_size})"

    if captured is None:
        return False, f"got 0 frames in {DURATION}s"

    import cv2
    cv2.imwrite(str(out_png), captured)
    mean = float(captured.mean())
    h, w = captured.shape[:2]
    return mean > 30, f"shape={w}x{h} mean={mean:.1f} png={out_png}"


def main():
    adb = adb_path()
    print(f"adb = {adb}")
    print(f"output dir = {OUT_DIR.absolute()}")
    print(f"target frame = {TARGET_FRAME} ({DURATION}s recording)")
    print()

    results = []
    for idx, serial in enumerate(SERIALS):
        print(f"[{serial}] capturing...", flush=True)
        t0 = time.perf_counter()
        ok, msg = capture_one(adb, serial, idx)
        dt = time.perf_counter() - t0
        marker = "OK " if ok else "BAD"
        print(f"  [{marker}] {msg} ({dt:.1f}s)")
        results.append((serial, ok, msg))

    print()
    print("=== SUMMARY ===")
    n_ok = sum(1 for _, ok, _ in results if ok)
    print(f"  {n_ok}/{len(SERIALS)} non-black")
    for serial, ok, msg in results:
        print(f"  {'OK ' if ok else 'BAD'} {serial}: {msg}")

    print()
    if n_ok == len(SERIALS):
        print("VERDICT: PASS — scrcpy/screenrecord path viable on UE4. Proceed to Phase B.")
        sys.exit(0)
    elif n_ok > 0:
        print("VERDICT: PARTIAL — some instances OK, some not. Investigate per-instance LDPlayer config.")
        sys.exit(1)
    else:
        print("VERDICT: FAIL — all black. ACE likely marks display secure. Need Phase D (vpn-app integration).")
        sys.exit(2)


if __name__ == "__main__":
    main()

"""WGC POC: 抓 1 个 LDPlayer 窗口的画面，保存 PNG + 统计。

用法（Windows）:
    python tools/_test_wgc_capture.py             # 默认抓 雷电模拟器-5
    python tools/_test_wgc_capture.py 雷电模拟器-3
"""
from __future__ import annotations

import sys
import time
import threading
from pathlib import Path

import cv2
import numpy as np
import win32gui

OUT_DIR = Path("captures")
OUT_DIR.mkdir(exist_ok=True)


def find_window(title: str) -> int:
    target = [0]
    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        t = win32gui.GetWindowText(hwnd)
        if t == title:
            target[0] = hwnd
            return False
        return True
    win32gui.EnumWindows(cb, None)
    return target[0]


def main():
    title = sys.argv[1] if len(sys.argv) > 1 else "雷电模拟器-5"
    hwnd = find_window(title)
    if not hwnd:
        print(f"[FAIL] 找不到窗口 {title!r}")
        sys.exit(1)
    print(f"target window: {title!r} HWND=0x{hwnd:08x}")

    # 大小
    rect = win32gui.GetWindowRect(hwnd)
    print(f"  rect: {rect}, size: {rect[2]-rect[0]}x{rect[3]-rect[1]}")

    from windows_capture import WindowsCapture

    capture = WindowsCapture(
        cursor_capture=False,
        draw_border=False,
        window_hwnd=hwnd,
    )

    state = {
        "frame_count": 0,
        "first_frame_at": None,
        "last_arr": None,
        "saved": False,
        "lock": threading.Lock(),
    }

    @capture.event
    def on_frame_arrived(frame, capture_control):
        try:
            # frame.frame_buffer 是 BGRA np.ndarray
            buf = frame.frame_buffer
            with state["lock"]:
                state["frame_count"] += 1
                if state["first_frame_at"] is None:
                    state["first_frame_at"] = time.time()
                state["last_arr"] = buf.copy()
            if not state["saved"]:
                # BGRA → BGR
                bgr = cv2.cvtColor(buf, cv2.COLOR_BGRA2BGR)
                # 用 ASCII 文件名（中文 path 给 Remote Agent download endpoint 麻烦）
                idx = title.replace("雷电模拟器", "0").replace("-", "")
                if not idx:
                    idx = "0"
                out_png = OUT_DIR / f"wgc_inst_{idx}.png"
                cv2.imwrite(str(out_png), bgr)
                with state["lock"]:
                    state["saved"] = True
                print(f"  [first frame] shape={buf.shape} dtype={buf.dtype} mean={buf.mean():.1f} → {out_png}")
            if state["frame_count"] >= 30:
                capture_control.stop()
        except Exception as e:
            print(f"on_frame_arrived err: {e}")

    @capture.event
    def on_closed():
        print(f"  [closed]")

    print("starting capture...")
    t_start = time.perf_counter()
    capture.start_free_threaded()

    # 等 5 秒或拿到 30 帧
    while time.perf_counter() - t_start < 5:
        time.sleep(0.1)
        with state["lock"]:
            if state["frame_count"] >= 30:
                break

    elapsed = time.perf_counter() - t_start
    fc = state["frame_count"]
    fps = fc / elapsed if elapsed > 0 else 0
    print(f"\n[STATS] {fc} frames in {elapsed:.2f}s ({fps:.1f} fps)")

    if state["last_arr"] is not None:
        last = state["last_arr"]
        print(f"  last frame: shape={last.shape} mean={last.mean():.1f} (>30 = 非黑)")
        if last.mean() > 30:
            print("  [OK] 非黑画面，WGC 工作正常")
        else:
            print("  [BAD] 黑画面，需检查窗口状态")
    else:
        print("  [FAIL] 没拿到任何帧")


if __name__ == "__main__":
    main()

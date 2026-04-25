"""测共享内存 reader：从 Local\\GameBotCap_<PID> 读 frame，保存 PNG"""
import mmap
import struct
import sys
import time
from pathlib import Path

import cv2
import numpy as np

SHM_MAGIC = 0x42476843
SHM_HEADER_BYTES = 32
SHM_MAX_W = 2560
SHM_MAX_H = 1440
SHM_TOTAL_BYTES = SHM_HEADER_BYTES + SHM_MAX_W * SHM_MAX_H * 4


def read_frame(pid: int):
    name = f"GameBotCap_{pid}"  # Windows mmap 不要 Local\\ 前缀
    try:
        # mmap.mmap with tagname (Windows-only) opens existing named mapping
        m = mmap.mmap(-1, SHM_TOTAL_BYTES, tagname=name, access=mmap.ACCESS_READ)
    except Exception as e:
        print(f"open shm {name!r} failed: {e}")
        return None

    hdr = m[:SHM_HEADER_BYTES]
    magic, frame_n, w, h, ts, stride, _r0, _r1 = struct.unpack("<IIIIIIII", hdr)
    print(f"  magic=0x{magic:08X} frame_n=0x{frame_n:08X} w={w} h={h} ts={ts}ms stride={stride}")
    if magic != SHM_MAGIC:
        print(f"  magic 不对（应 0x{SHM_MAGIC:08X}），共享内存未初始化")
        m.close()
        return None
    if w == 0 or h == 0:
        print("  width/height = 0，hook 还没抓帧")
        m.close()
        return None
    if frame_n & 0x80000000:
        print(f"  frame_n 高位 = 1，写帧进行中（重读）")
        time.sleep(0.05)
        hdr = m[:SHM_HEADER_BYTES]
        _, frame_n, w, h, _, stride, _, _ = struct.unpack("<IIIIIIII", hdr)
        if frame_n & 0x80000000:
            print(f"  仍在写中")
            m.close()
            return None

    frame_bytes = w * h * 4
    raw = bytes(m[SHM_HEADER_BYTES:SHM_HEADER_BYTES + frame_bytes])
    arr = np.frombuffer(raw, dtype=np.uint8).reshape((h, w, 4))
    m.close()
    return arr  # RGBA


def main():
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 42060
    out_dir = Path("captures")
    out_dir.mkdir(exist_ok=True)

    print(f"读 PID {pid} 共享内存...")
    arr = read_frame(pid)
    if arr is None:
        sys.exit(1)

    print(f"  shape={arr.shape} mean={arr.mean():.1f}")

    # GL 帧通常是上下颠倒的（OpenGL 原点左下，图像左上）
    flipped = np.flipud(arr)
    # RGBA → BGR 给 OpenCV 保存
    bgr = cv2.cvtColor(flipped, cv2.COLOR_RGBA2BGR)
    out = out_dir / f"dxhook_pid{pid}.png"
    cv2.imwrite(str(out), bgr)
    print(f"  saved {out}")

    # 没翻转的版本
    bgr2 = cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
    out2 = out_dir / f"dxhook_pid{pid}_noflip.png"
    cv2.imwrite(str(out2), bgr2)
    print(f"  saved {out2} (未翻转)")


if __name__ == "__main__":
    main()

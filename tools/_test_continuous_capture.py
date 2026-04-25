"""验证：长流 screenrecord raw h264 + PyAV codec 持续解码

跑法（Windows）：
    python tools/_test_continuous_capture.py emulator-5554 10
        从 5554 实例持续抓 10 秒，每 0.5 秒尝试 get_frame() 看延迟
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np

OUT_DIR = Path("captures")
OUT_DIR.mkdir(exist_ok=True)


def adb_path() -> str:
    candidates = [
        r"C:\leidian\LDPlayer9\adb.exe",
        r"D:\leidian\LDPlayer9\adb.exe",
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return "adb"


class ScreenrecordStream:
    """长流 screenrecord raw h264 + PyAV codec context 解 NALU。

    架构：subprocess.Popen(adb shell screenrecord ... -) → 后台线程读 stdout
    → 喂给 av.codec.CodecContext("h264") → 解出 frame → 存 _latest_frame。
    """

    def __init__(self, serial: str, adb: str = "adb",
                 bitrate: int = 4_000_000, time_limit: int = 170):
        self.serial = serial
        self.adb = adb
        self.bitrate = bitrate
        self.time_limit = time_limit
        self._proc: subprocess.Popen | None = None
        self._reader_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._latest_frame: np.ndarray | None = None
        self._lock = threading.Lock()
        self._frame_count = 0
        self._first_frame_at: float | None = None

    def start(self):
        cmd = [
            self.adb, "-s", self.serial, "exec-out",
            "screenrecord",
            f"--time-limit={self.time_limit}",
            "--output-format=h264",
            "--bit-rate", str(self.bitrate),
            "-",
        ]
        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()

    def _read_loop(self):
        """方案：累积 bytes 到 BytesIO + av.open() 周期性解码新数据"""
        import av
        from io import BytesIO

        buf = BytesIO()
        last_decode_pos = 0  # 上次 av.open 时 buf 大小（字节）
        total_bytes = 0
        chunks_seen = 0
        decode_attempts = 0
        try:
            while not self._stop.is_set():
                if self._proc.stdout is None:
                    break
                chunk = self._proc.stdout.read(8192)
                if not chunk:
                    print(f"[reader {self.serial}] stdout EOF after {total_bytes} bytes / {chunks_seen} chunks", flush=True)
                    break
                total_bytes += len(chunk)
                chunks_seen += 1
                buf.write(chunk)

                # 累积超过 16KB 后尝试解码一次
                if buf.tell() - last_decode_pos < 16384:
                    continue
                last_decode_pos = buf.tell()
                decode_attempts += 1

                # 拷贝 buf 内容到新 BytesIO（av.open 会消费/seek 它）
                buf.seek(0)
                copy = BytesIO(buf.read())
                copy.seek(0)
                # 把 buf 文件指针留在 end，方便后续继续 write
                buf.seek(0, 2)

                if chunks_seen == 1:
                    print(f"[reader {self.serial}] chunk #1 first32hex={chunk[:32].hex()}", flush=True)

                try:
                    container = av.open(copy, format="h264", mode="r")
                    stream = container.streams.video[0]
                    n = 0
                    last_arr = None
                    for frame in container.decode(stream):
                        n += 1
                        last_arr = frame.to_ndarray(format="bgr24")
                    container.close()
                    if last_arr is not None:
                        with self._lock:
                            self._latest_frame = last_arr
                            self._frame_count += n
                            if self._first_frame_at is None:
                                self._first_frame_at = time.time()
                        if decode_attempts <= 3 or decode_attempts % 10 == 0:
                            print(f"[reader {self.serial}] decode#{decode_attempts}: {n} frames extracted, total={self._frame_count}", flush=True)
                except Exception as e:
                    if decode_attempts <= 3:
                        print(f"[reader {self.serial}] decode#{decode_attempts} err: {e}", flush=True)

            err = self._proc.stderr.read() if self._proc.stderr else b""
            if err:
                print(f"[reader {self.serial}] stderr: {err[:300]}", flush=True)
            print(f"[reader {self.serial}] DONE chunks={chunks_seen} bytes={total_bytes} frames={self._frame_count}", flush=True)
        except Exception as e:
            print(f"[reader {self.serial}] fatal: {e}", flush=True)

    def get_frame(self) -> np.ndarray | None:
        with self._lock:
            return self._latest_frame.copy() if self._latest_frame is not None else None

    def stats(self) -> dict:
        with self._lock:
            return {
                "frame_count": self._frame_count,
                "first_frame_at": self._first_frame_at,
                "has_frame": self._latest_frame is not None,
            }

    def stop(self):
        self._stop.set()
        if self._proc:
            try:
                self._proc.kill()
            except Exception:
                pass
        if self._reader_thread:
            self._reader_thread.join(timeout=2)


def main():
    if len(sys.argv) < 2:
        print("usage: python _test_continuous_capture.py <serial> [duration_s]")
        sys.exit(1)
    serial = sys.argv[1]
    duration = float(sys.argv[2]) if len(sys.argv) > 2 else 10.0

    adb = adb_path()
    print(f"adb = {adb}")
    print(f"serial = {serial}, duration = {duration}s")

    stream = ScreenrecordStream(serial, adb)
    t_start = time.perf_counter()
    stream.start()

    print("[main] waiting for first frame...")
    first_frame_t = None
    poll_results = []
    last_frame_count = 0
    while time.perf_counter() - t_start < duration:
        time.sleep(0.5)
        elapsed = time.perf_counter() - t_start
        f = stream.get_frame()
        s = stream.stats()
        if f is not None and first_frame_t is None:
            first_frame_t = elapsed
            print(f"  [first frame] @ {elapsed:.2f}s: shape={f.shape} mean={f.mean():.1f}")
            import cv2
            cv2.imwrite(str(OUT_DIR / f"continuous_{serial}_first.png"), f)
        # 计算从上次 poll 到现在新增了多少帧
        delta = s["frame_count"] - last_frame_count
        last_frame_count = s["frame_count"]
        poll_results.append((elapsed, s["frame_count"], delta, s["has_frame"]))

    print(f"\n[final stats] {stream.stats()}")
    f = stream.get_frame()
    if f is not None:
        import cv2
        cv2.imwrite(str(OUT_DIR / f"continuous_{serial}_last.png"), f)
        print(f"  last frame: shape={f.shape} mean={f.mean():.1f}")

    print(f"\n[poll log] (every 0.5s)")
    print(f"  {'time':>6} {'frames':>7} {'delta':>5} has_frame")
    for elapsed, count, delta, has in poll_results:
        print(f"  {elapsed:>6.2f} {count:>7} {delta:>5}  {has}")

    stream.stop()
    if first_frame_t is not None:
        print(f"\n[OK] First frame latency: {first_frame_t:.2f}s")
        print(f"[OK] Total frames in {duration}s: {stream.stats()['frame_count']}")
    else:
        print("\n[FAIL] No frame received")


if __name__ == "__main__":
    main()

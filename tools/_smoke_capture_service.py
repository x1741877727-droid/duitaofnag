"""smoke test: 跑 CaptureService 全链路（一个实例）

跑法（Windows）:
    python tools/_smoke_capture_service.py emulator-5564

前置：apk 已装 + appops PROJECT_MEDIA allow 已设
"""
import os
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path


ADB = r"D:\leidian\LDPlayer9\adb.exe"
PORT_BASE = 1418
SOCKET_NAME = "fmcapture"
PKG = "com.fightmaster.vpn"


def adb(serial, *args):
    cmd = [ADB, "-s", serial, *args]
    r = subprocess.run(cmd, capture_output=True, timeout=15)
    return r.returncode, r.stdout.decode("utf-8", errors="replace"), r.stderr.decode("utf-8", errors="replace")


def main():
    serial = sys.argv[1] if len(sys.argv) > 1 else "emulator-5564"
    port = PORT_BASE
    out_dir = Path("captures")
    out_dir.mkdir(exist_ok=True)

    print(f"[1/6] kill 上次 CaptureService（防 socket 冲突）")
    adb(serial, "shell", "am", "force-stop", PKG)
    time.sleep(1)

    print(f"[2/6] appops 授权 PROJECT_MEDIA")
    adb(serial, "shell", "appops", "set", PKG, "android:project_media", "allow")

    print(f"[3/6] 广播 CAPTURE_START")
    rc, out, err = adb(serial, "shell", "am", "broadcast",
                       "-a", "com.fightmaster.vpn.CAPTURE_START",
                       "-n", f"{PKG}/.CommandReceiver")
    print(f"  rc={rc} out={out.strip()[:80]}")

    print(f"[4/6] 等 CaptureService 起 socket（2s）")
    time.sleep(2)

    print(f"[5/6] adb forward tcp:{port} localabstract:{SOCKET_NAME}")
    rc, out, err = adb(serial, "forward", f"tcp:{port}", f"localabstract:{SOCKET_NAME}")
    print(f"  rc={rc}")

    print(f"[6/6] connect + 读 H.264 packets...")
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(8)
    try:
        s.connect(("127.0.0.1", port))
    except Exception as e:
        print(f"  FAIL connect: {e}")
        return
    print(f"  connected")

    received = []
    t0 = time.perf_counter()
    try:
        while time.perf_counter() - t0 < 5:
            hdr = b""
            while len(hdr) < 4:
                c = s.recv(4 - len(hdr))
                if not c:
                    print(f"  EOF after {len(received)} packets")
                    break
                hdr += c
            if len(hdr) != 4:
                break
            L = struct.unpack(">I", hdr)[0]
            if L == 0 or L > 5_000_000:
                print(f"  bad length: {L}")
                break
            payload = b""
            while len(payload) < L:
                c = s.recv(L - len(payload))
                if not c:
                    break
                payload += c
            received.append(payload)
            if len(received) <= 5 or len(received) % 20 == 0:
                first8 = payload[:8].hex()
                print(f"  pkt #{len(received)} L={L} first8={first8}")
            if len(received) >= 60:
                break
    except socket.timeout:
        print(f"  socket timeout after {len(received)} packets")
    finally:
        s.close()

    elapsed = time.perf_counter() - t0
    print(f"\n[STATS] {len(received)} packets in {elapsed:.1f}s "
          f"({len(received)/elapsed:.1f} pkt/s, avg {sum(len(p) for p in received)/max(len(received),1):.0f} B/pkt)")

    # 用 PyAV 解第 1 帧验画面正确
    if received:
        try:
            import av
            codec = av.codec.CodecContext.create("h264", "r")
            codec.thread_type = "AUTO"
            decoded = 0
            saved = False
            for p in received:
                packet = av.Packet(p)
                try:
                    frames = codec.decode(packet)
                except Exception as e:
                    print(f"  decode err: {e}")
                    frames = []
                for fr in frames:
                    decoded += 1
                    if not saved:
                        arr = fr.to_ndarray(format="bgr24")
                        import cv2
                        out_png = out_dir / f"capture_{serial}.png"
                        cv2.imwrite(str(out_png), arr)
                        print(f"\n[FRAME] shape={arr.shape} mean={arr.mean():.1f} png={out_png}")
                        saved = True
            print(f"  decoded {decoded} frames")
        except ImportError:
            print("  PyAV not installed")

    print(f"\n[CLEANUP] CAPTURE_STOP + remove forward")
    adb(serial, "shell", "am", "broadcast",
        "-a", "com.fightmaster.vpn.CAPTURE_STOP",
        "-n", f"{PKG}/.CommandReceiver")
    adb(serial, "forward", "--remove", f"tcp:{port}")


if __name__ == "__main__":
    main()

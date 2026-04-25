"""扫描 Ld9BoxHeadless.exe 内存里 libtersafe.so 的 arm64 代码标识字节"""
import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from host_memscan import scan_process_memory, find_ldplayer_vbox_pids

# 24 字节签名：tss_sdk_ischeatpacket 函数内的 magic constants 加载序列
# 位于 Ghidra 地址 0x002b8ee0 (file offset 0x001b8ee0)
SIGNATURE = bytes.fromhex("F9B99A52DAF49A52F5229D5276198352F954A672DA89A372")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int, required=False)
    ap.add_argument("--timeout", type=float, default=90)
    args = ap.parse_args()

    pid = args.pid
    if not pid:
        procs = find_ldplayer_vbox_pids()
        pid = procs[0]["pid"]
        print(f"AUTO pid={pid}")

    print(f"Scanning pid={pid} for tss_sdk_ischeatpacket signature...")
    print(f"signature ({len(SIGNATURE)} bytes): {SIGNATURE.hex()}")
    result = scan_process_memory(pid, [SIGNATURE],
                                 max_findings=20, timeout=args.timeout)
    print(f"Findings: {len(result['findings'])}")
    for f in result["findings"]:
        addr = f["addr"]
        print(f"  match @ 0x{addr:016x}")
        # Function entry = match address - 0x20 (24 bytes before the magic constants)
        func_entry = addr - 0x20
        print(f"  → function entry: 0x{func_entry:016x}")
    print(f"\nStats: {result['stats']}")


if __name__ == "__main__":
    main()

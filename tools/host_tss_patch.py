"""Patch tss_sdk_ischeatpacket 使其永远返回 0 (not cheat)

ARM64 汇编 "mov x0, #0; ret" 字节 = 00 00 80 D2 C0 03 5F D6

用法:
  python host_tss_patch.py --pid 22680             # 扫描 + 单次 patch
  python host_tss_patch.py --pid 22680 --loop 10   # 每 10s 重新 patch（防 JIT 还原）
"""
import argparse
import sys
import os
import time

sys.path.insert(0, os.path.dirname(__file__))
from host_memscan import scan_process_memory, find_ldplayer_vbox_pids
from host_write_lib import open_write_handle, write_at, close_handle

# tss_sdk_ischeatpacket 函数 + 0x20 处的 24 字节 magic-constants 唯一签名
SIGNATURE = bytes.fromhex("F9B99A52DAF49A52F5229D5276198352F954A672DA89A372")
SIG_OFFSET_FROM_ENTRY = 0x20

# ARM64: mov x0, #0 ; ret
RETURN_ZERO_PATCH = bytes.fromhex("000080D2C0035FD6")


def find_func_entry(pid: int, timeout: float = 60.0) -> int:
    """返回 tss_sdk_ischeatpacket 的运行时地址，找不到返回 0"""
    result = scan_process_memory(pid, [SIGNATURE],
                                 max_findings=5, timeout=timeout)
    if not result["findings"]:
        return 0
    sig_addr = result["findings"][0]["addr"]
    return sig_addr - SIG_OFFSET_FROM_ENTRY


def patch_func(pid: int, func_entry: int, dry_run: bool = False) -> bool:
    """写入 ret-zero patch"""
    if dry_run:
        print(f"  [DRY] would write {RETURN_ZERO_PATCH.hex()} → 0x{func_entry:x}")
        return True
    h = open_write_handle(pid)
    try:
        ok, n, err = write_at(h, func_entry, RETURN_ZERO_PATCH)
        if ok:
            print(f"  ✓ patched 0x{func_entry:x} with {RETURN_ZERO_PATCH.hex()}")
            return True
        else:
            print(f"  ✗ patch failed at 0x{func_entry:x}: {err}")
            return False
    finally:
        close_handle(h)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int, default=None)
    ap.add_argument("--loop", type=float, default=0.0,
                    help="循环 patch 间隔秒数（防 JIT 缓存还原；0=单次）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--scan-timeout", type=float, default=60.0)
    args = ap.parse_args()

    pid = args.pid
    if not pid:
        procs = find_ldplayer_vbox_pids()
        if not procs:
            print("ERROR: no Ld9Box* found")
            sys.exit(1)
        pid = procs[0]["pid"]
        print(f"AUTO pid={pid}")

    print(f"Target pid={pid}, loop={args.loop}s, dry_run={args.dry_run}")

    iteration = 0
    known_entry = 0
    while True:
        iteration += 1
        print(f"\n=== iter {iteration} @ {time.strftime('%H:%M:%S')} ===", flush=True)

        # Re-scan in first iteration; later iterations reuse address unless stale
        if iteration == 1 or known_entry == 0:
            t0 = time.time()
            known_entry = find_func_entry(pid, args.scan_timeout)
            t1 = time.time()
            if known_entry == 0:
                print(f"  ✗ signature not found in memory (scan {t1-t0:.1f}s)")
                if args.loop <= 0:
                    sys.exit(2)
                time.sleep(args.loop)
                continue
            print(f"  ✓ function entry: 0x{known_entry:x}  (scan {t1-t0:.1f}s)")

        # Apply patch
        ok = patch_func(pid, known_entry, args.dry_run)
        if not ok:
            # Maybe address is stale; re-scan next iteration
            known_entry = 0

        if args.loop <= 0:
            break
        time.sleep(args.loop)


if __name__ == "__main__":
    main()

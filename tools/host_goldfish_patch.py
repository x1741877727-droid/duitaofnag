"""host_goldfish_patch.py — 从宿主机持续覆写模拟器进程里的 'goldfish' 字符串

依据 2026-04-19 用户方案 A：通过 Host WriteProcessMemory 修改
LDPlayer 的 Ld9BoxHeadless.exe 进程内存里所有 'goldfish' 出现处，
替换为看起来像真机 SoC 名字的字符串（同长度保证结构不坏）。

ACE 在 Android VM 内部扫不到 Windows host 写入动作。

用法:
  python host_goldfish_patch.py --pid 22680             # 扫描并单次覆写全部
  python host_goldfish_patch.py --pid 22680 --limit 5   # 只写前 5 个（小测）
  python host_goldfish_patch.py --pid 22680 --loop 5    # 每 5s 持续覆写
  python host_goldfish_patch.py --pid 22680 --dry-run   # 只看不改
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from host_memscan import scan_process_memory, find_pid_for_instance, find_ldplayer_vbox_pids
from host_write_lib import open_write_handle, write_at, close_handle


# 替换目标：同长度 (8 字节)，看起来像 Qualcomm Snapdragon 芯片代号
REPLACE_MAP = {
    b"goldfish": b"qcom8916",
}


def find_goldfish_hits(pid: int, timeout: float = 60.0):
    keywords = list(REPLACE_MAP.keys())
    result = scan_process_memory(pid, keywords,
                                 max_findings=500, timeout=timeout)
    return result["findings"]


def patch_once(pid: int, findings: list, dry_run: bool, limit: int = 0):
    h = open_write_handle(pid) if not dry_run else None
    try:
        stats = {"total": 0, "success": 0, "failed": 0}
        for i, f in enumerate(findings):
            if limit > 0 and i >= limit:
                break
            kw = f["keyword"].encode("utf-8") if isinstance(f["keyword"], str) else f["keyword"]
            new = None
            for k, v in REPLACE_MAP.items():
                if k == kw:
                    new = v
                    break
            if new is None:
                continue
            addr = f["addr"]
            stats["total"] += 1
            if dry_run:
                print(f"  [DRY] would write {new!r} → 0x{addr:x}", flush=True)
                stats["success"] += 1
                continue
            ok, n, err = write_at(h, addr, new)
            if ok:
                stats["success"] += 1
                if stats["success"] <= 5 or stats["success"] % 20 == 0:
                    print(f"  ✓ 0x{addr:x}", flush=True)
            else:
                stats["failed"] += 1
                if stats["failed"] <= 10:
                    print(f"  ✗ 0x{addr:x}: {err}", flush=True)
        return stats
    finally:
        if h:
            close_handle(h)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int, default=None)
    ap.add_argument("--instance", type=int, default=None)
    ap.add_argument("--loop", type=float, default=0.0,
                    help="循环覆写间隔秒数（0 = 单次）")
    ap.add_argument("--limit", type=int, default=0,
                    help="只写前 N 个位置（0 = 全部）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--scan-timeout", type=float, default=60.0)
    args = ap.parse_args()

    pid = args.pid
    if pid is None and args.instance is not None:
        pid = find_pid_for_instance(args.instance)
    if pid is None:
        procs = find_ldplayer_vbox_pids()
        if not procs:
            print("ERROR: no Ld[V|9]Box*.exe found", flush=True)
            sys.exit(1)
        pid = procs[0]["pid"]
        print(f"AUTO pid={pid}", flush=True)

    print(f"Target pid={pid}")
    print(f"REPLACE_MAP: {REPLACE_MAP}")
    print(f"dry_run={args.dry_run} loop={args.loop}s limit={args.limit}", flush=True)

    iteration = 0
    while True:
        iteration += 1
        t0 = time.time()
        print(f"\n=== iter {iteration} @ {time.strftime('%H:%M:%S')} ===", flush=True)
        try:
            findings = find_goldfish_hits(pid, args.scan_timeout)
        except PermissionError as e:
            print(f"scan failed: {e}")
            break
        except Exception as e:
            print(f"scan error: {e}")
            findings = []

        print(f"found {len(findings)} hits, patching...", flush=True)
        stats = patch_once(pid, findings, args.dry_run, args.limit)
        t1 = time.time()
        print(f"iter {iteration} stats: {stats} elapsed={t1-t0:.2f}s", flush=True)

        if args.loop <= 0:
            break
        sleep_s = max(0, args.loop - (t1 - t0))
        print(f"sleeping {sleep_s:.1f}s...", flush=True)
        time.sleep(sleep_s)


if __name__ == "__main__":
    main()

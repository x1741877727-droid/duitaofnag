"""host_emu_fingerprint_patch.py — 扩展版：覆盖所有已知模拟器特征字符串

基于 P1+P2 方案：
- P1: build.prop/kernel 派生字符串（ranchu/qemu/vbox86 等）
- P2 fallback: libhoudini/houdini 等 ARM 翻译层字符串静态覆写

原则：**等长替换**（不改结构）+ **不动函数入口字节**（避免 .text CRC 自检）

用法:
  python host_emu_fingerprint_patch.py --pid 22680             # 单次
  python host_emu_fingerprint_patch.py --pid 22680 --loop 30   # 持续（推荐）
"""
import argparse
import sys
import os
import time

sys.path.insert(0, os.path.dirname(__file__))
from host_memscan import scan_process_memory, find_pid_for_instance, find_ldplayer_vbox_pids
from host_write_lib import open_write_handle, write_at, close_handle


# 等长替换 map — 每个 key/value 必须完全等长
# 替换目标：真机看起来合理的等长字符串；如果没有自然替换，用空字节 \0 填充
def _pad(s: bytes, tgt_len: int) -> bytes:
    if len(s) > tgt_len:
        return s[:tgt_len]
    return s + b"\x00" * (tgt_len - len(s))


REPLACE_MAP = {
    # === 已验证安全的（之前 173 处 goldfish 没导致游戏崩溃）===
    b"goldfish": b"qcom8916",       # 8 → 8

    # === P1 build.prop / kernel 派生（新加）===
    b"ranchu":   _pad(b"qcom8m", 6),             # 6 → 6
    b"generic":  b"msmnile",                     # 7 → 7（Qualcomm SM8150 真实代号）
    b"vbox86":   _pad(b"qcom64", 6),             # 6 → 6
    b"android_x86":  b"android_arm",             # 11 → 11
    b"ro.kernel.qemu": b"ro.kerneldebug",        # 14 → 14（普通真机也有的 prop）

    # === P2 fallback: houdini/翻译层（静态 substring 覆写，不动链接器表）===
    b"libhoudini": b"libbinder\x00",             # 10 → 10 (libbinder 是真机常见 lib)
    b"houdini":    b"binding",                   # 7 → 7

    # === 路径类（模拟器独有文件名，真机不存在，纯 null 化）===
    b"qemu_pipe":  _pad(b"", 9),                 # 9 → 9 nulls
    b"qemud":      _pad(b"", 5),                 # 5 → 5 nulls
    b"init.svc.qemud": _pad(b"", 14),            # 14 → nulls

    # === 内存调试库（纯模拟器 debug 库名，null 化）===
    b"libc_malloc_debug_qemu.so": _pad(b"", 25),
}


def find_hits(pid: int, timeout: float):
    keywords = list(REPLACE_MAP.keys())
    r = scan_process_memory(pid, keywords, max_findings=2000, timeout=timeout)
    return r["findings"]


def apply_patches(pid: int, findings: list, dry_run: bool):
    if dry_run:
        print(f"[DRY] would patch {len(findings)} hits")
        return {"total": len(findings), "success": len(findings), "failed": 0}
    h = open_write_handle(pid)
    stats = {"total": 0, "success": 0, "failed": 0}
    by_kw = {}
    try:
        for f in findings:
            kw_raw = f["keyword"]
            kw = kw_raw.encode("utf-8") if isinstance(kw_raw, str) else kw_raw
            new = REPLACE_MAP.get(kw)
            if new is None:
                continue
            assert len(new) == len(kw), f"length mismatch for {kw!r} → {new!r}"
            addr = f["addr"]
            stats["total"] += 1
            ok, n, err = write_at(h, addr, new)
            if ok:
                stats["success"] += 1
                by_kw[kw] = by_kw.get(kw, 0) + 1
            else:
                stats["failed"] += 1
    finally:
        close_handle(h)
    return stats, by_kw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int, default=None)
    ap.add_argument("--instance", type=int, default=None)
    ap.add_argument("--loop", type=float, default=0.0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--scan-timeout", type=float, default=90.0)
    args = ap.parse_args()

    pid = args.pid
    if not pid and args.instance is not None:
        pid = find_pid_for_instance(args.instance)
    if not pid:
        procs = find_ldplayer_vbox_pids()
        if not procs:
            print("ERROR: no LD process")
            sys.exit(1)
        pid = procs[0]["pid"]
        print(f"AUTO pid={pid}")

    print(f"Target pid={pid}, loop={args.loop}s, dry_run={args.dry_run}")
    print(f"REPLACE_MAP entries: {len(REPLACE_MAP)}")
    for k in REPLACE_MAP.keys():
        print(f"  - {k}  (len={len(k)})")

    it = 0
    while True:
        it += 1
        t0 = time.time()
        print(f"\n=== iter {it} @ {time.strftime('%H:%M:%S')} ===", flush=True)
        try:
            findings = find_hits(pid, args.scan_timeout)
        except Exception as e:
            print(f"scan error: {e}")
            findings = []
        print(f"found {len(findings)} hits", flush=True)
        if args.dry_run:
            stats = {"total": len(findings), "success": len(findings), "failed": 0}
            by_kw = {}
        else:
            stats, by_kw = apply_patches(pid, findings, args.dry_run)
        t1 = time.time()
        print(f"iter {it} stats: {stats} elapsed={t1-t0:.1f}s", flush=True)
        if by_kw:
            for k, v in by_kw.items():
                print(f"  - {k!r}: {v}")
        if args.loop <= 0:
            break
        sleep_s = max(0, args.loop - (t1 - t0))
        time.sleep(sleep_s)


if __name__ == "__main__":
    main()

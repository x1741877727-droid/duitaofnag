"""
游戏进程内存扫描 — 搜索玩家名字等字符串
通过 ADB 在模拟器上执行，不注入游戏进程

用法:
  python tools/memscan.py --adb D:\\leidian\\LDPlayer9\\adb.exe --serial emulator-5556 --keyword 冰雾阴阳
"""

import argparse
import subprocess
import platform
import re
import sys
import time

_SF = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0


def adb_cmd(adb_path, serial, *args):
    cmd = [adb_path, "-s", serial] + list(args)
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=30, creationflags=_SF)
        return r.stdout
    except Exception as e:
        print(f"  ADB error: {e}")
        return b""


def get_pid(adb_path, serial, package):
    out = adb_cmd(adb_path, serial, "shell", f"su 0 pidof {package}")
    pid = out.decode().strip()
    return int(pid) if pid.isdigit() else None


def get_rw_regions(adb_path, serial, pid):
    """获取所有 rw-p 内存区域"""
    out = adb_cmd(adb_path, serial, "shell", f"su 0 cat /proc/{pid}/maps")
    regions = []
    for line in out.decode(errors="replace").splitlines():
        if "rw-p" not in line:
            continue
        parts = line.split()
        addr_range = parts[0]
        start_hex, end_hex = addr_range.split("-")
        start = int(start_hex, 16)
        end = int(end_hex, 16)
        size = end - start
        # 跳过太小或太大的区域
        if size < 4096 or size > 16 * 1024 * 1024:
            continue
        name = parts[-1] if len(parts) > 5 else ""
        regions.append((start, end, size, addr_range, name))
    return regions


def scan_region(adb_path, serial, pid, start, size, keyword_bytes):
    """扫描单个内存区域"""
    # 用 dd 读取区域，通过 stdout 传回
    skip_blocks = start // 4096
    count_blocks = size // 4096
    if count_blocks == 0:
        return []

    out = adb_cmd(
        adb_path, serial, "shell",
        f"su 0 dd if=/proc/{pid}/mem bs=4096 skip={skip_blocks} count={count_blocks} 2>/dev/null"
    )
    if not out:
        return []

    # 搜索 UTF-8 编码的关键词
    findings = []
    pos = 0
    while True:
        idx = out.find(keyword_bytes, pos)
        if idx == -1:
            break
        # 提取上下文（前后各 64 字节）
        ctx_start = max(0, idx - 32)
        ctx_end = min(len(out), idx + len(keyword_bytes) + 64)
        context = out[ctx_start:ctx_end]
        # 尝试解码上下文
        try:
            ctx_str = context.decode("utf-8", errors="replace")
        except:
            ctx_str = repr(context)
        findings.append({
            "offset": start + idx,
            "hex_addr": f"0x{start + idx:x}",
            "context": ctx_str,
        })
        pos = idx + 1
    return findings


def main():
    import io, os
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description="游戏进程内存扫描")
    parser.add_argument("--adb", default=r"D:\leidian\LDPlayer9\adb.exe")
    parser.add_argument("--serial", default="emulator-5556")
    parser.add_argument("--package", default="com.tencent.tmgp.pubgmhd")
    parser.add_argument("--keyword", required=True, help="要搜索的字符串")
    args = parser.parse_args()

    keyword_bytes = args.keyword.encode("utf-8")
    print(f"关键词: {args.keyword} (UTF-8: {keyword_bytes.hex()})")

    # 获取 PID
    pid = get_pid(args.adb, args.serial, args.package)
    if not pid:
        print("错误: 游戏进程未找到")
        return
    print(f"游戏 PID: {pid}")

    # 获取内存区域
    regions = get_rw_regions(args.adb, args.serial, pid)
    print(f"可扫描区域: {len(regions)} 个")

    total_size = sum(r[2] for r in regions)
    print(f"总扫描大小: {total_size / 1024 / 1024:.1f} MB")

    # 开始扫描
    t0 = time.time()
    all_findings = []
    scanned = 0
    for i, (start, end, size, addr_range, name) in enumerate(regions):
        scanned += size
        if (i + 1) % 50 == 0:
            pct = scanned * 100 // total_size
            print(f"  进度: {pct}% ({i+1}/{len(regions)})")

        findings = scan_region(args.adb, args.serial, pid, start, size, keyword_bytes)
        if findings:
            print(f"\n  *** 在 {addr_range} ({name}) 找到 {len(findings)} 处 ***")
            for f in findings:
                print(f"    地址: {f['hex_addr']}")
                try:
                    print(f"    上下文: {f['context'][:120]}")
                except UnicodeEncodeError:
                    print(f"    上下文: {f['context'][:120].encode('utf-8', errors='replace')}")
            all_findings.extend(findings)

    elapsed = time.time() - t0
    print(f"\n扫描完成: {elapsed:.1f}秒, 找到 {len(all_findings)} 处")

    # 检查游戏是否还活着
    pid2 = get_pid(args.adb, args.serial, args.package)
    if pid2 == pid:
        print("✓ 游戏进程正常（未被检测）")
    else:
        print("✗ 游戏进程已退出（可能被检测到）")


if __name__ == "__main__":
    main()

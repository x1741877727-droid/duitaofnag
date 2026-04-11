"""
快速内存扫描 — 一次性 dump 整块内存到 Windows 再搜索
避免反复调用 su/dd，不会弹权限

用法:
  python tools/fastscan.py --keyword 冰雾阴阳
  python tools/fastscan.py --keyword 冰雾阴阳 --keyword2 傲娇0v0
"""

import argparse
import subprocess
import platform
import sys
import time
import io

_SF = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0


def adb(serial, adb_path, *args):
    cmd = [adb_path, "-s", serial] + list(args)
    r = subprocess.run(cmd, capture_output=True, timeout=30, creationflags=_SF)
    return r.stdout


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

    parser = argparse.ArgumentParser(description="快速游戏内存扫描")
    parser.add_argument("--adb", default=r"D:\leidian\LDPlayer9\adb.exe")
    parser.add_argument("--serial", default="emulator-5556")
    parser.add_argument("--package", default="com.tencent.tmgp.pubgmhd")
    parser.add_argument("--keyword", required=True, help="搜索关键词1")
    parser.add_argument("--keyword2", default="", help="搜索关键词2（可选）")
    args = parser.parse_args()

    # 获取 PID
    pid = adb(args.serial, args.adb, "shell", "su 0 pidof " + args.package).decode().strip()
    if not pid.isdigit():
        print(f"错误: 游戏未运行")
        return
    print(f"PID: {pid}")

    # 获取内存映射
    maps_raw = adb(args.serial, args.adb, "shell", f"su 0 cat /proc/{pid}/maps").decode(errors='replace')

    # 筛选 rw-p 区域，按大小分组
    regions = []
    for line in maps_raw.splitlines():
        if 'rw-p' not in line:
            continue
        parts = line.split()
        s, e = parts[0].split('-')
        start = int(s, 16)
        end = int(e, 16)
        size = end - start
        if size < 4096 or size > 8 * 1024 * 1024:
            continue
        regions.append((start, end, size, s, e))

    print(f"可扫描区域: {len(regions)} 个")

    kw1 = args.keyword.encode('utf-8')
    kw2 = args.keyword2.encode('utf-8') if args.keyword2 else None
    found = []

    # 按批次处理：每次读取多个连续区域，一次 dd 调用
    # 关键优化：合并相邻区域，减少 dd 调用次数
    batch_size = 0
    batch_start = 0
    batches = []
    MAX_BATCH = 4 * 1024 * 1024  # 每批最多 4MB

    for start, end, size, s_hex, e_hex in regions:
        if batch_size == 0:
            batch_start = start
            batch_size = size
        elif start == batch_start + batch_size and batch_size + size <= MAX_BATCH:
            # 连续区域，合并
            batch_size += size
        else:
            batches.append((batch_start, batch_size))
            batch_start = start
            batch_size = size
    if batch_size > 0:
        batches.append((batch_start, batch_size))

    print(f"合并为 {len(batches)} 个批次")

    t0 = time.time()
    for i, (bstart, bsize) in enumerate(batches):
        if (i + 1) % 100 == 0:
            print(f"  进度: {i+1}/{len(batches)}")

        skip = bstart // 4096
        count = bsize // 4096
        if count == 0:
            continue

        # 单次 adb exec-out + su 0 dd，数据直接到 Windows 内存
        try:
            data = adb(args.serial, args.adb, "exec-out",
                       f"su 0 dd if=/proc/{pid}/mem bs=4096 skip={skip} count={count} 2>/dev/null")
        except Exception:
            continue

        if not data:
            continue

        # 在 Windows 内存中搜索
        pos = 0
        while True:
            idx = data.find(kw1, pos)
            if idx == -1:
                break
            addr = bstart + idx
            # 提取上下文
            cs = max(0, idx - 32)
            ce = min(len(data), idx + len(kw1) + 96)
            ctx = data[cs:ce]
            # 清理上下文显示
            ctx_str = ""
            for b in ctx:
                if b >= 0x20:
                    ctx_str += chr(b) if b < 0x80 else ctx[ctx.index(b):ctx.index(b)+1].decode('utf-8', errors='replace')
                elif b == 0:
                    ctx_str += "|"
            try:
                ctx_str = ctx[cs - cs:].decode('utf-8', errors='replace').replace('\x00', '|')
            except:
                ctx_str = repr(ctx[:60])

            found.append((addr, ctx_str))
            print(f"  *** FOUND @0x{addr:x}: {ctx_str[:150]}")
            pos = idx + 1

        if kw2:
            pos = 0
            while True:
                idx = data.find(kw2, pos)
                if idx == -1:
                    break
                addr = bstart + idx
                cs = max(0, idx - 32)
                ce = min(len(data), idx + len(kw2) + 96)
                try:
                    ctx_str = data[cs:ce].decode('utf-8', errors='replace').replace('\x00', '|')
                except:
                    ctx_str = repr(data[cs:ce][:60])
                found.append((addr, ctx_str))
                print(f"  *** FOUND @0x{addr:x}: {ctx_str[:150]}")
                pos = idx + 1

    elapsed = time.time() - t0
    print(f"\n扫描完成: {elapsed:.1f}秒, 找到 {len(found)} 处")

    # 检查游戏存活
    pid2 = adb(args.serial, args.adb, "shell", "su 0 pidof " + args.package).decode().strip()
    if pid2 == pid:
        print("✓ 游戏进程正常")
    else:
        print("✗ 游戏进程已退出！")


if __name__ == "__main__":
    main()

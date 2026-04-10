"""通过 adb exec-out + /proc/pid/mem 搜索游戏内存"""
import subprocess, sys, os

ADB = r"D:\leidian\LDPlayer9\adb.exe"
SERIAL = "emulator-5556"

def adb_text(cmd):
    r = subprocess.run([ADB, "-s", SERIAL, "shell", cmd],
                      capture_output=True, text=True, timeout=30)
    return r.stdout.strip()

def adb_binary(shell_cmd):
    """用 exec-out 获取二进制输出"""
    r = subprocess.run([ADB, "-s", SERIAL, "exec-out", shell_cmd],
                      capture_output=True, timeout=60)
    return r.stdout

pid = adb_text("pidof com.tencent.tmgp.pubgmhd").split()[0]
print(f"PID: {pid}", flush=True)

# 解析 maps
maps = adb_text(f"cat /proc/{pid}/maps | grep rw-p")
segments = []
for line in maps.split('\n'):
    if not line.strip():
        continue
    addr_range = line.split()[0]
    start_s, end_s = addr_range.split('-')
    start = int(start_s, 16)
    end = int(end_s, 16)
    size = end - start
    if size >= 65536 and size <= 0x4000000:  # 64KB ~ 64MB
        segments.append((start, end, size))

print(f"Segments to scan: {len(segments)}", flush=True)

search_term = sys.argv[1].encode() if len(sys.argv) > 1 else b"Advertisings"
print(f"Searching for: {search_term}", flush=True)

total_hits = 0
for idx, (start, end, size) in enumerate(segments):
    if idx % 50 == 0:
        print(f"  scanning {idx}/{len(segments)}...", flush=True)

    # 用 dd + exec-out 获取二进制数据
    # 关键: 用 bs=1 skip=<addr> 的方式, 对大地址改用管道 seek
    # 实际上 exec-out + dd 的 skip 也有 32-bit 限制...
    # 但如果 start < 2^63 可以试试直接用大数

    # 用 cat + head + tail 配合 /proc/pid/mem 不行 (不支持 seek)

    # 用一个 trick: 在设备上用 shell 的 exec + read
    # 或者: 对低地址段直接 dd, 高地址段用不同策略

    if start > 0x7FFFFFFF:
        # 64-bit 地址: 用 shell 写小脚本调用 pread
        # 设备上没有工具... 只能跳过 :(
        # 除非能让 dd 工作
        # 试试 toybox dd 的 skip_bytes 参数 (Android 9+)
        cmd = f"toybox dd if=/proc/{pid}/mem bs=65536 count={size//65536} skip={start} iflag=skip_bytes 2>/dev/null"
    else:
        # 32-bit 地址: 普通 dd
        if start % 4096 == 0:
            cmd = f"dd if=/proc/{pid}/mem bs=4096 skip={start//4096} count={size//4096} 2>/dev/null"
        else:
            cmd = f"dd if=/proc/{pid}/mem bs=1 skip={start} count={size} 2>/dev/null"

    try:
        data = adb_binary(cmd)
        if not data:
            continue

        # 搜索
        pos = 0
        while True:
            pos = data.find(search_term, pos)
            if pos == -1:
                break
            addr = start + pos
            # 获取上下文
            ctx_start = max(0, pos - 32)
            ctx_end = min(len(data), pos + 200)
            ctx = data[ctx_start:ctx_end]
            # 转为可见字符
            ctx_str = ''.join(chr(b) if 32 <= b < 127 else '.' for b in ctx)
            print(f"FOUND 0x{addr:x} in seg 0x{start:x}-0x{end:x}", flush=True)
            print(f"  {ctx_str[:200]}", flush=True)
            total_hits += 1
            pos += 1
            if total_hits > 50:
                break
    except Exception as e:
        pass

    if total_hits > 50:
        break

print(f"\nTOTAL: {total_hits} hits", flush=True)

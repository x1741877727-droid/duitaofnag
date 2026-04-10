"""通过 /proc/pid/mem 直接扫描游戏内存 — 不用 Frida"""
import subprocess, struct, sys

ADB = r"D:\leidian\LDPlayer9\adb.exe"
SERIAL = "emulator-5556"

def adb(cmd):
    r = subprocess.run([ADB, "-s", SERIAL, "shell", cmd],
                      capture_output=True, text=True, timeout=30)
    return r.stdout.strip()

# 获取游戏 PID
pid = adb("pidof com.tencent.tmgp.pubgmhd").split()[0]
print(f"PID: {pid}")

# 写一个设备端的扫描脚本 — 直接在 Android 上用 shell 读 /proc/pid/mem
# 用 dd + grep 太慢, 写一个 C-style 的 binary scanner 用 Python
# 但设备上没有 Python... 用 toybox/busybox 的 od + grep 也太慢

# 最好的方式: 把相关内存 dump 到本地再扫描
# 先读 maps 找到大的 rw 堆段
maps = adb(f"cat /proc/{pid}/maps | grep 'rw-p' | grep -v '\\[' | head -100")
print(f"Found rw segments")

# 解析 maps, 找出大的堆段
segments = []
for line in maps.split('\n'):
    if not line.strip():
        continue
    parts = line.split()
    addr_range = parts[0]
    start_s, end_s = addr_range.split('-')
    start = int(start_s, 16)
    end = int(end_s, 16)
    size = end - start
    # 只要大于 1MB 小于 128MB 的段 (堆的主要部分)
    if size > 0x100000 and size < 0x8000000:
        segments.append((start, end, size))

print(f"Large rw segments: {len(segments)}")
total = sum(s[2] for s in segments)
print(f"Total size: {total/1024/1024:.1f} MB")

# 搜索策略: 用 dd + xxd 在设备上搜索特征字节
# 特征: "Advertisings" (活动配置的 JSON key)
# 或者 "ShowActivityUI" (UE4 函数名)
# 在设备上直接用 grep -boa 搜索二进制文件

print("\n=== 搜索活动弹窗特征 ===")

# 方法: 用 grep 直接搜索 /proc/pid/mem 的特定段
# grep -c 计数匹配
patterns = {
    "Advertisings": "Advertisings",
    "ShowActivity": "ShowActivity",
    "actStartDate": "actStartDate",
    "popupConfig": "popupConfig",
    "isShow": "isShow",
    "showType": "showType",
}

for name, pat in patterns.items():
    # 在每个大段中搜索
    for start, end, size in segments[:20]:
        result = adb(f"dd if=/proc/{pid}/mem bs=4096 skip={start//4096} count={size//4096} 2>/dev/null | grep -boa '{pat}' | head -5")
        if result:
            print(f"[FOUND] '{name}' in segment 0x{start:x}-0x{end:x}:")
            for line in result.split('\n')[:3]:
                offset_in_seg = int(line.split(':')[0])
                actual_addr = start + offset_in_seg
                print(f"  offset={offset_in_seg} addr=0x{actual_addr:x}")
                # 读取周围内容
                ctx = adb(f"dd if=/proc/{pid}/mem bs=1 skip={actual_addr} count=200 2>/dev/null | strings | head -5")
                if ctx:
                    print(f"  context: {ctx[:200]}")

print("\n=== 完成 ===")

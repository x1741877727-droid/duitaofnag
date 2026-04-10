"""纯 ADB 内存扫描 — 不用 Frida, 不注入任何代码"""
import subprocess, sys, os, struct

ADB = r"D:\leidian\LDPlayer9\adb.exe"
SERIAL = "emulator-5556"
PID = "8615"
DUMP_DIR = r"C:\Users\Administrator\Desktop\memdump"

os.makedirs(DUMP_DIR, exist_ok=True)

def adb_shell(cmd):
    r = subprocess.run([ADB, "-s", SERIAL, "shell", cmd],
                      capture_output=True, timeout=30)
    return r.stdout

def adb_shell_text(cmd):
    r = subprocess.run([ADB, "-s", SERIAL, "shell", cmd],
                      capture_output=True, text=True, timeout=30)
    return r.stdout.strip()

mode = sys.argv[1] if len(sys.argv) > 1 else "scan"
print(f"PID: {PID}, mode: {mode}", flush=True)

# 解析 maps
maps = adb_shell_text(f"cat /proc/{PID}/maps | grep rw-p")
segments = []
for line in maps.split('\n'):
    if not line.strip():
        continue
    addr_range = line.split()[0]
    start_s, end_s = addr_range.split('-')
    start = int(start_s, 16)
    end = int(end_s, 16)
    size = end - start
    if size > 65536 and size < 0x8000000:
        segments.append((start, end, size))

print(f"Large rw segments: {len(segments)}", flush=True)

if mode == "scan":
    # 逐段 dump 到设备 tmpfs 再搜索
    search_terms = [b"Advertisings", b"isShow", b"actStartDate", b"popupConfig", b"ShowActivityUI"]

    for idx, (start, end, size) in enumerate(segments):
        if idx > 60:
            break
        # dump 到设备 tmpfs
        # 用 dd 从 /proc/pid/mem seek 到指定位置
        # 注意: seek 用字节, 不能超过 shell 整数限制
        # 对 64-bit 地址, 用 skip_bytes (如果可用) 或计算 bs/skip

        # 如果地址太大, 用 bs=4096 + skip
        if start % 4096 == 0:
            skip_blocks = start // 4096
            count_blocks = size // 4096
            dump_cmd = f"dd if=/proc/{PID}/mem bs=4096 skip={skip_blocks} count={count_blocks} 2>/dev/null | grep -c 'Advertisings\\|isShow\\|actStartDate\\|popupConfig'"
        else:
            continue

        try:
            result = adb_shell_text(dump_cmd)
            hits = int(result.strip()) if result.strip().isdigit() else 0
        except:
            hits = 0

        if hits > 0:
            print(f"HIT! seg[{idx}] 0x{start:x}-0x{end:x} size={size} hits={hits}", flush=True)

            # dump 到设备 tmp 文件
            dump_on_device = f"/data/local/tmp/seg_{idx}.bin"
            adb_shell_text(f"dd if=/proc/{PID}/mem bs=4096 skip={start//4096} count={size//4096} of={dump_on_device} 2>/dev/null")

            # 搜索具体位置
            grep_result = adb_shell_text(f"grep -boa 'Advertisings\\|isShow\\|actStartDate\\|popupConfig' {dump_on_device} | head -20")
            if grep_result:
                for line in grep_result.split('\n'):
                    if ':' in line:
                        offset_str, word = line.split(':', 1)
                        offset = int(offset_str)
                        actual_addr = start + offset
                        # 读取上下文
                        ctx = adb_shell_text(f"dd if={dump_on_device} bs=1 skip={offset} count=300 2>/dev/null | strings -n 4 | head -5")
                        print(f"  0x{actual_addr:x}: {word}", flush=True)
                        print(f"    {ctx[:200]}", flush=True)

            # pull 到 Windows 用于 diff
            local_path = os.path.join(DUMP_DIR, f"seg_{idx}_0x{start:x}.bin")
            subprocess.run([ADB, "-s", SERIAL, "pull", dump_on_device, local_path],
                          capture_output=True, timeout=30)
            print(f"  saved: {local_path}", flush=True)

            # 清理
            adb_shell_text(f"rm {dump_on_device}")

elif mode == "dump_before":
    # dump 所有有活动数据的段 (用于 diff)
    print("Dumping before state...", flush=True)
    # 先 scan 找到有数据的段
    hit_segs = []
    for idx, (start, end, size) in enumerate(segments):
        if idx > 60 or start % 4096 != 0:
            continue
        try:
            result = adb_shell_text(f"dd if=/proc/{PID}/mem bs=4096 skip={start//4096} count={size//4096} 2>/dev/null | grep -c 'isShow\\|popupConfig'")
            hits = int(result.strip()) if result.strip().isdigit() else 0
        except:
            hits = 0
        if hits > 0:
            hit_segs.append((idx, start, end, size, hits))
            print(f"  seg[{idx}] 0x{start:x} hits={hits}", flush=True)

    # dump 这些段
    for idx, start, end, size, hits in hit_segs:
        fn = os.path.join(DUMP_DIR, f"before_{idx}_0x{start:x}.bin")
        adb_shell_text(f"dd if=/proc/{PID}/mem bs=4096 skip={start//4096} count={size//4096} of=/data/local/tmp/d.bin 2>/dev/null")
        subprocess.run([ADB, "-s", SERIAL, "pull", "/data/local/tmp/d.bin", fn], capture_output=True, timeout=60)
        print(f"  saved {fn} ({size} bytes)", flush=True)

    # 保存段信息
    with open(os.path.join(DUMP_DIR, "seg_info.txt"), 'w') as f:
        for idx, start, end, size, hits in hit_segs:
            f.write(f"{idx}\t{start}\t{end}\t{size}\n")
    print(f"Done, {len(hit_segs)} segments", flush=True)

elif mode == "dump_after":
    # dump 相同的段 (勾选后)
    print("Dumping after state...", flush=True)
    seg_info = open(os.path.join(DUMP_DIR, "seg_info.txt")).readlines()
    for line in seg_info:
        idx, start, end, size = line.strip().split('\t')
        idx, start, end, size = int(idx), int(start), int(end), int(size)
        fn = os.path.join(DUMP_DIR, f"after_{idx}_0x{start:x}.bin")
        adb_shell_text(f"dd if=/proc/{PID}/mem bs=4096 skip={start//4096} count={size//4096} of=/data/local/tmp/d.bin 2>/dev/null")
        subprocess.run([ADB, "-s", SERIAL, "pull", "/data/local/tmp/d.bin", fn], capture_output=True, timeout=60)
        print(f"  saved {fn}", flush=True)
    print("Done", flush=True)

elif mode == "diff":
    # 对比 before/after
    seg_info = open(os.path.join(DUMP_DIR, "seg_info.txt")).readlines()
    for line in seg_info:
        idx, start, end, size = line.strip().split('\t')
        idx, start, end, size = int(idx), int(start), int(end), int(size)
        bf = os.path.join(DUMP_DIR, f"before_{idx}_0x{start:x}.bin")
        af = os.path.join(DUMP_DIR, f"after_{idx}_0x{start:x}.bin")
        if not os.path.exists(bf) or not os.path.exists(af):
            continue
        d1 = open(bf, 'rb').read()
        d2 = open(af, 'rb').read()
        minlen = min(len(d1), len(d2))
        changes = []
        for i in range(minlen):
            if d1[i] != d2[i]:
                changes.append((i, d1[i], d2[i]))

        if changes:
            print(f"\nseg[{idx}] 0x{start:x}: {len(changes)} changed bytes", flush=True)
            zero_to_one = [(i, a, b) for i, a, b in changes if a == 0 and b == 1]
            if zero_to_one:
                print(f"  0->1 changes: {len(zero_to_one)}", flush=True)
                for off, old, new in zero_to_one[:20]:
                    addr = start + off
                    print(f"    0x{addr:x}: {old} -> {new}", flush=True)
            for off, old, new in changes[:30]:
                addr = start + off
                print(f"  0x{addr:x}: {old} -> {new}", flush=True)

print("\n=== DONE ===", flush=True)

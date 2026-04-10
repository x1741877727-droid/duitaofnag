"""持续搜索 isShowToday，找到后分析并写入"""
import subprocess, time, sys, re

ADB = r"D:\leidian\LDPlayer9\adb.exe"
SERIAL = "emulator-5556"

def adb(cmd):
    r = subprocess.run([ADB, "-s", SERIAL, "shell", cmd],
                      capture_output=True, text=True, timeout=30)
    return r.stdout.strip()

def memscan(pid, pattern):
    return adb(f"/data/local/tmp/memscan {pid} search {pattern}")

def memwrite(pid, addr_hex, bytes_hex):
    return adb(f"/data/local/tmp/memscan {pid} write {addr_hex} {bytes_hex}")

# 获取 PID
pid = adb("ps -A | grep pubgmhd | grep -v xg_vip | grep -v tga | head -1").split()[1]
print(f"PID: {pid}", flush=True)

# 循环搜索直到找到
print("等待活动数据加载到内存...", flush=True)
for attempt in range(60):
    result = memscan(pid, "isShowToday")
    lines = result.split('\n')
    found_lines = [l for l in lines if l.startswith('FOUND')]
    total_line = [l for l in lines if l.startswith('TOTAL')]

    total = 0
    if total_line:
        m = re.search(r'TOTAL: (\d+)', total_line[0])
        if m:
            total = int(m.group(1))

    print(f"[{attempt}] hits={total}", flush=True)

    if total > 0:
        print(f"\n=== 找到 {total} 个 isShowToday ===", flush=True)
        for line in found_lines:
            print(line, flush=True)

        # 对每个找到的地址，读取上下文，找到 bool 值
        # isShowToday 后面通常跟着 ":true" 或 ":false" 或者一个 bool 字节
        # 在 UE4 Lua 环境中，isShowToday 是一个 property name
        # 它的值存在对象的某个偏移处

        # 先看看这些地址的上下文
        for line in found_lines:
            addr_match = re.search(r'FOUND 0x([0-9a-f]+)', line)
            if addr_match:
                addr = addr_match.group(1)
                # 读取该地址后面的 256 字节
                ctx = adb(f"/data/local/tmp/memscan {pid} search isShowToday 2>/dev/null | grep -A1 'FOUND 0x{addr}'")
                # 从上下文里找 "isShowToday:" 后面跟的值
                ctx_line = [l for l in lines if f'0x{addr}' in l]
                if ctx_line:
                    # 找 CTX 行
                    idx = lines.index(ctx_line[0])
                    if idx + 1 < len(lines):
                        ctx_data = lines[idx + 1]
                        print(f"\n地址 0x{addr} 上下文:", flush=True)
                        print(f"  {ctx_data[:300]}", flush=True)

        # 现在尝试搜索 "isShowToday:" 带冒号的精确模式
        # 找到后把紧跟的值从 true 改为 false
        print("\n搜索精确模式...", flush=True)
        result2 = memscan(pid, "isShowToday:")
        lines2 = result2.split('\n')
        found2 = [l for l in lines2 if l.startswith('FOUND')]
        print(f"isShowToday: 精确匹配: {len(found2)}", flush=True)
        for l in found2:
            print(l, flush=True)

        # 也搜索 isShowNotice
        result3 = memscan(pid, "isShowNotice")
        lines3 = result3.split('\n')
        found3 = [l for l in lines3 if l.startswith('FOUND')]
        print(f"\nisShowNotice 匹配: {len(found3)}", flush=True)
        for l in found3:
            print(l, flush=True)

        break

    time.sleep(5)

print("\n=== 完成 ===", flush=True)

"""
host_ace_sniff.py — 从宿主机扫模拟器内存，捕获游戏 ACE 协议明文上报

原理:
  游戏 app 在调用 SSL_write 加密发送前，ACE 协议明文包已经在内存 buffer 里。
  从 Windows 宿主机 ReadProcessMemory 读 Ld9BoxHeadless.exe，
  按 ACE 包的二进制指纹搜索，dump 明文。

ACE 包结构 (已确认):
  pos 0-1:   01 00          协议头（固定）
  pos 2-5:   [len 4B]       总长度
  pos 6-9:   [seq 4B]
  pos 10-13: [session_id 4B]
  pos 14-17: 00 00 08 53    tag 常量（固定）← 指纹
  pos 18+:   payload

用法:
  python tools/host_ace_sniff.py --duration 30    # 扫 30 秒
  python tools/host_ace_sniff.py --out ace.jsonl  # dump 到文件
"""

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
import struct
import sys
import time

# 复用 host_memscan 的 Windows API
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from host_memscan import (
    OpenProcess, CloseHandle, ReadProcessMemory, VirtualQueryEx,
    MEMORY_BASIC_INFORMATION,
    PROCESS_VM_READ, PROCESS_QUERY_INFORMATION, MEM_COMMIT,
    PAGE_NOACCESS, PAGE_GUARD,
    find_ldplayer_vbox_pids, find_pid_for_instance,
    CHUNK_SIZE,
)

# ACE 指纹: pos 14-17 固定 = 00 00 08 53
ACE_TAG = b"\x00\x00\x08\x53"
# 对应的完整 ACE 头 pattern: 01 00 00 00 [?] [flag] ?? ?? ?? ?? [4B sid] 00 00 08 53
# 我们用 tag 反向找 pos 0


def dump_ace_packets(pid, duration=30, max_packets=500, out_fp=None):
    """扫描进程内存，找 ACE 协议明文包，dump 到文件/stdout"""
    handle = OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not handle:
        err = ctypes.get_last_error()
        raise PermissionError(f"OpenProcess 失败 (error={err})，需要管理员权限")

    seen_hashes = set()  # 去重（相同包不重复 dump）
    found = 0
    t_start = time.time()

    try:
        while time.time() - t_start < duration and found < max_packets:
            cycle_start = time.time()
            packets = _scan_once(handle)
            for pkt in packets:
                # 用前 32 字节 hash 去重
                h = hash(bytes(pkt['data'][:32]))
                if h in seen_hashes:
                    continue
                seen_hashes.add(h)
                found += 1

                record = {
                    "ts": round(time.time(), 3),
                    "addr": f"0x{pkt['addr']:x}",
                    "length": pkt['length'],
                    "session_id": pkt['session_id'].hex(),
                    "seq": pkt['seq'].hex(),
                    "flag": f"0x{pkt['flag']:02x}",
                    "data_hex": pkt['data'].hex(),
                }
                line = json.dumps(record, ensure_ascii=False)
                if out_fp:
                    out_fp.write(line + "\n")
                    out_fp.flush()
                print(line)

            # 下一轮扫描前等一秒（避免 CPU 100%）
            elapsed = time.time() - cycle_start
            if elapsed < 1.0:
                time.sleep(1.0 - elapsed)
    finally:
        CloseHandle(handle)

    return found


def _scan_once(handle):
    """扫一轮，返回所有 ACE 包"""
    packets = []
    mbi = MEMORY_BASIC_INFORMATION()
    mbi_size = ctypes.sizeof(mbi)

    buf = ctypes.create_string_buffer(CHUNK_SIZE)
    bytes_read = ctypes.c_size_t(0)

    addr = 0
    max_addr = (1 << 47) - 1

    while addr < max_addr:
        result = VirtualQueryEx(handle, ctypes.c_void_p(addr), ctypes.byref(mbi), mbi_size)
        if result == 0:
            break

        region_base = mbi.BaseAddress or addr
        region_size = mbi.RegionSize
        next_addr = region_base + region_size

        if (mbi.State != MEM_COMMIT
                or mbi.Protect & PAGE_NOACCESS
                or mbi.Protect & PAGE_GUARD
                or mbi.Protect == 0):
            addr = next_addr
            continue

        offset = 0
        while offset < region_size:
            chunk_addr = region_base + offset
            to_read = min(CHUNK_SIZE, region_size - offset)

            ok = ReadProcessMemory(handle, ctypes.c_void_p(chunk_addr),
                                    buf, to_read, ctypes.byref(bytes_read))
            if not ok or bytes_read.value == 0:
                offset += to_read
                continue

            n = bytes_read.value
            data = buf.raw[:n]

            # 搜 tag pattern
            pos = 0
            while True:
                idx = data.find(ACE_TAG, pos)
                if idx < 0:
                    break
                # ACE tag 在 pos 14-17，所以 ACE 头在 idx-14
                hdr_idx = idx - 14
                if hdr_idx < 0:
                    pos = idx + 1
                    continue

                # 验证 ACE 头: pos 0-1 = 01 00, pos 2-3 = 00 00
                if (data[hdr_idx] != 0x01 or data[hdr_idx + 1] != 0x00
                        or data[hdr_idx + 2] != 0x00 or data[hdr_idx + 3] != 0x00):
                    pos = idx + 1
                    continue

                # 读长度 pos 4（单字节，ACE 包 <= 255）
                pkt_len = data[hdr_idx + 4]
                # 合理长度范围
                if pkt_len < 30 or pkt_len > 255:
                    pos = idx + 1
                    continue
                # flag 白名单（已知 ACE c2s/s2c flag）
                flag = data[hdr_idx + 5]
                if flag not in (0x01, 0x02, 0x03, 0x04, 0x07, 0x08, 0x0c, 0x0e):
                    pos = idx + 1
                    continue

                # 防越界
                if hdr_idx + pkt_len > n:
                    pos = idx + 1
                    continue

                # 提取完整包
                pkt_data = data[hdr_idx:hdr_idx + pkt_len]
                packets.append({
                    "addr": chunk_addr + hdr_idx,
                    "length": pkt_len,
                    "flag": data[hdr_idx + 5],
                    "seq": bytes(data[hdr_idx + 6:hdr_idx + 10]),
                    "session_id": bytes(data[hdr_idx + 10:hdr_idx + 14]),
                    "data": pkt_data,
                })
                pos = idx + 14

            offset += to_read

        addr = next_addr

    return packets


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int)
    parser.add_argument("--instance", type=int)
    parser.add_argument("--duration", type=float, default=30.0, help="扫描秒数")
    parser.add_argument("--out", help="jsonl 输出文件")
    parser.add_argument("--max-packets", type=int, default=500)
    args = parser.parse_args()

    pid = args.pid
    if pid is None and args.instance is not None:
        pid = find_pid_for_instance(args.instance)
    if pid is None:
        procs = find_ldplayer_vbox_pids()
        if len(procs) == 1:
            pid = procs[0]["pid"]
        elif len(procs) > 1:
            print(f"多个模拟器进程: {procs}，用 --pid 指定")
            sys.exit(1)
        else:
            print("找不到 Ld9BoxHeadless 进程")
            sys.exit(1)

    print(f"PID={pid} 扫描 {args.duration}s，找 ACE 明文上报...")

    out_fp = open(args.out, "w", encoding="utf-8") if args.out else None
    try:
        found = dump_ace_packets(pid, args.duration, args.max_packets, out_fp)
        print(f"\n共捕获 {found} 个唯一 ACE 包")
    finally:
        if out_fp:
            out_fp.close()
            print(f"→ 保存到 {args.out}")


if __name__ == "__main__":
    main()

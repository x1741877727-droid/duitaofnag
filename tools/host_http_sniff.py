"""
host_http_sniff.py — 从宿主机扫模拟器内存，捕获游戏 HTTP/HTTPS 请求明文

原理:
  游戏 app 在 SSL_write 加密前，HTTP 请求明文在 buffer 里。
  搜 `GET /`、`POST /`、`PUT /` 前缀 + `HTTP/1.1` 锚点 = 独特指纹。

重点关注:
  - /ace/* /beacon/* /crashsight/* /report/* /tdm/* /trace/* 路径
  - Host: 里含 anticheatexpert / crashsight / beacon / trace / ops.gp
"""

import argparse
import ctypes
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from host_memscan import (
    OpenProcess, CloseHandle, ReadProcessMemory, VirtualQueryEx,
    MEMORY_BASIC_INFORMATION,
    PROCESS_VM_READ, PROCESS_QUERY_INFORMATION, MEM_COMMIT,
    PAGE_NOACCESS, PAGE_GUARD,
    find_ldplayer_vbox_pids, find_pid_for_instance,
    CHUNK_SIZE,
)

# 可疑关键词（封号嫌疑最大的上报路径）
SUSPICIOUS_KEYWORDS = [
    b"/ace", b"/beacon", b"/crashsight", b"/report", b"/tdm",
    b"/trace", b"/rqd", b"/pandora", b"/wpa",
    b"anticheatexpert", b"crashsight", b"beacon.qq",
    b"trace.qq", b"tdm.qq", b"ops.gp", b"rqd.",
]


def scan_http_requests(handle, duration=30, max_requests=500):
    """扫进程内存找 HTTP 请求明文"""
    seen_hashes = set()
    results = []
    t_start = time.time()
    mbi = MEMORY_BASIC_INFORMATION()
    mbi_size = ctypes.sizeof(mbi)
    buf = ctypes.create_string_buffer(CHUNK_SIZE)
    bytes_read = ctypes.c_size_t(0)

    while time.time() - t_start < duration and len(results) < max_requests:
        addr = 0
        max_addr = (1 << 47) - 1

        while addr < max_addr:
            if len(results) >= max_requests:
                break
            if time.time() - t_start > duration:
                break

            r = VirtualQueryEx(handle, ctypes.c_void_p(addr), ctypes.byref(mbi), mbi_size)
            if r == 0:
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
                if len(results) >= max_requests:
                    break
                chunk_addr = region_base + offset
                to_read = min(CHUNK_SIZE, region_size - offset)
                ok = ReadProcessMemory(handle, ctypes.c_void_p(chunk_addr),
                                        buf, to_read, ctypes.byref(bytes_read))
                if not ok or bytes_read.value == 0:
                    offset += to_read
                    continue
                n = bytes_read.value
                data = buf.raw[:n]

                # 搜 HTTP request starts
                for pattern in (b"GET /", b"POST /", b"PUT /", b"DELETE /"):
                    pos = 0
                    while True:
                        i = data.find(pattern, pos)
                        if i < 0:
                            break
                        # 校验：后面 4KB 内应该有 HTTP/1.1 + Host:
                        end = data.find(b"\r\n\r\n", i, i + 8192)
                        if end < 0:
                            pos = i + 1
                            continue
                        req = data[i:end + 4]
                        if b"HTTP/1." not in req[:500]:
                            pos = i + 1
                            continue
                        if b"Host:" not in req and b"host:" not in req:
                            pos = i + 1
                            continue
                        h = hash(req[:128])
                        if h in seen_hashes:
                            pos = i + 1
                            continue
                        seen_hashes.add(h)

                        # 提取 method + URL + Host
                        first_line = req.split(b"\r\n", 1)[0].decode("ascii", "replace")
                        host_match = re.search(rb"[Hh]ost:\s*([^\r\n]+)", req)
                        host = host_match.group(1).decode("ascii", "replace").strip() if host_match else "?"

                        # 提取 body（若 POST）
                        body_start = end + 4
                        body_end = min(body_start + 1024, len(data))
                        body = data[body_start:body_end]

                        # 检查是否可疑
                        lower_req = req.lower()
                        suspicious = any(kw in lower_req for kw in SUSPICIOUS_KEYWORDS)

                        record = {
                            "ts": round(time.time(), 3),
                            "addr": f"0x{chunk_addr + i:x}",
                            "suspicious": suspicious,
                            "method_url": first_line[:200],
                            "host": host,
                            "req_len": len(req),
                            "headers": req.decode("ascii", "replace")[:2000],
                            "body_hex": body[:200].hex() if body else "",
                        }
                        results.append(record)
                        pos = end + 4

                offset += to_read
            addr = next_addr

        # Sleep before next scan pass
        time.sleep(1.0)

    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int)
    parser.add_argument("--instance", type=int)
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--out", help="jsonl 文件")
    parser.add_argument("--suspicious-only", action="store_true",
                        help="只输出可疑的（包含 ace/beacon/crashsight 等关键词）")
    args = parser.parse_args()

    pid = args.pid
    if pid is None and args.instance is not None:
        pid = find_pid_for_instance(args.instance)
    if pid is None:
        procs = find_ldplayer_vbox_pids()
        if len(procs) >= 1:
            pid = procs[0]["pid"]
        else:
            print("找不到 Ld9BoxHeadless")
            sys.exit(1)

    print(f"PID={pid} 扫 {args.duration}s 找 HTTP 明文...")

    h = OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not h:
        print(f"OpenProcess 失败 err={ctypes.get_last_error()}")
        sys.exit(1)
    try:
        results = scan_http_requests(h, args.duration)
    finally:
        CloseHandle(h)

    if args.suspicious_only:
        results = [r for r in results if r["suspicious"]]

    out_fp = open(args.out, "w", encoding="utf-8") if args.out else None
    print(f"\n=== 共捕获 {len(results)} 个 HTTP 请求 ===")
    sus_count = sum(1 for r in results if r["suspicious"])
    print(f"  其中可疑（上报类）: {sus_count}")
    print()

    for r in results:
        marker = "🔴" if r["suspicious"] else "  "
        line = f"{marker} [{r['method_url'][:100]}] Host={r['host'][:50]} len={r['req_len']}"
        print(line)
        if out_fp:
            out_fp.write(json.dumps(r, ensure_ascii=False) + "\n")

    if out_fp:
        out_fp.close()
        print(f"\n→ 详细保存到 {args.out}")


if __name__ == "__main__":
    main()

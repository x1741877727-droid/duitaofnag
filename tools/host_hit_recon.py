"""host_hit_recon.py — 扫描 LDPlayer 游戏进程内存里的 HIT/ACE 指纹字符串

基于 host_memscan.py。只搜索，不修改。
"""
import argparse
import sys
import os
import json

sys.path.insert(0, os.path.dirname(__file__))
from host_memscan import (
    scan_process_memory,
    find_ldplayer_vbox_pids,
    find_pid_for_instance,
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--instance", type=int, default=None, help="LDPlayer 实例索引")
    ap.add_argument("--pid", type=int, default=None, help="PID 直接指定")
    ap.add_argument("--extra", nargs="*", default=[],
                    help="追加搜索关键词（UTF-8）")
    ap.add_argument("--timeout", type=float, default=120.0)
    ap.add_argument("--max", type=int, default=40, help="每关键词最多匹配数")
    args = ap.parse_args()

    pid = args.pid
    if pid is None and args.instance is not None:
        pid = find_pid_for_instance(args.instance)
    if pid is None:
        procs = find_ldplayer_vbox_pids()
        if not procs:
            print("ERROR: 找不到 LdVBoxHeadless.exe")
            sys.exit(1)
        pid = procs[0]["pid"]
        print(f"AUTO pid={pid} from {procs[0]}")

    # 已知的 HIT/ACE 相关字节序列
    # 32-char hex 设备指纹（从 2026-04-19 六花 pcap 提取）
    known_hashes = [
        b"9B1399A262FF933B25FC7495668EC325",  # 最近抓到的 hash（pcap 里出现）
    ]
    # QIMEI / 设备 ID 相关字符串
    qimei_strings = [
        b"qimei",
        b"QIMEI",
        b"qimei36",
        b"imei",
    ]
    # 模拟器特征字符串（如果能找到说明 ACE 能扫到 → 危险信号）
    emu_fingerprints = [
        b"LDPlayer",
        b"ldplayer",
        b"goldfish",
        b"vbox",
        b"nox",
    ]
    # HIT 上报域名 / 地址（上下文定位）
    hit_markers = [
        b"anticheatexpert",
        b"crashsight",
        b"08 53",  # ACE 协议 tag (ASCII literal)
    ]

    all_keywords = known_hashes + qimei_strings + emu_fingerprints + hit_markers
    # 也可 UTF-16-LE 编码（Windows 常见）
    utf16_extras = []
    for s in known_hashes + qimei_strings:
        try:
            utf16_extras.append(s.decode('utf-8').encode('utf-16-le'))
        except:
            pass
    # 去重
    all_keywords = list({k: None for k in all_keywords + utf16_extras}.keys())
    # 用户追加
    for e in args.extra:
        all_keywords.append(e.encode('utf-8'))

    print(f"=== 扫描 PID={pid} 内存（{len(all_keywords)} 个关键词，{args.timeout}s 超时）===")
    for kw in all_keywords:
        print(f"  - {kw[:40]!r}")

    result = scan_process_memory(
        pid, all_keywords,
        max_findings=args.max * len(all_keywords),
        timeout=args.timeout,
    )

    print(f"\n=== Stats ===")
    print(json.dumps(result.get("stats", {}), indent=2))

    print(f"\n=== Findings ({len(result['findings'])}) ===")
    by_kw = {}
    for f in result["findings"]:
        kw = f.get("keyword", "?")
        by_kw.setdefault(kw, []).append(f)
    for kw, hits in by_kw.items():
        print(f"\n### {kw!r}  × {len(hits)}")
        for h in hits[:args.max]:
            addr = h.get("addr", 0)
            ctx = h.get("context", "")
            print(f"  @0x{addr:016x}  ctx={ctx[:80]!r}")


if __name__ == "__main__":
    main()

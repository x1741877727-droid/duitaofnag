"""Scan Ld9BoxHeadless.exe 内存找六花云端响应明文（JSON/protobuf/rule config 等）"""
import argparse
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))
from host_memscan import scan_process_memory, find_ldplayer_vbox_pids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int, default=None)
    ap.add_argument("--timeout", type=float, default=180)
    args = ap.parse_args()
    pid = args.pid or find_ldplayer_vbox_pids()[0]["pid"]
    print(f"pid={pid}")

    # 六花 server 响应里可能出现的关键词（JSON / protobuf / rule)
    keywords = [
        # JSON 典型
        b'"rules":[',
        b'"rule":',
        b'"domain":',
        b'"ip":',
        b'"action":',
        b'"proxy":',
        b'"direct":',
        b'"reject":',
        b'"modify":',
        b'"search":',
        b'"pattern":',
        b'"replace":',
        b'"cheat"',
        b'"ban"',
        b'"detect"',
        # 中文关键词
        b'\xe5\xb0\x81\xe5\x8c\x85',  # 封包
        b'\xe8\xa7\x84\xe5\x88\x99',  # 规则
        b'\xe9\xbb\x91\xe5\x90\x8d\xe5\x8d\x95',  # 黑名单
        b'\xe6\xa3\x80\xe6\xb5\x8b',  # 检测
        b'\xe5\xa4\x96\xe6\x8c\x82',  # 外挂
        # URL/配置
        b'gitee.com/',
        b'/raw/master/',
        b'[RoutingRule]',
        b'DOMAIN-KEYWORD',
        b'IP-CIDR,',
        # ACE相关
        b'anticheatexpert',
        b'crashsight',
    ]

    print(f"Scanning {len(keywords)} keywords...")
    result = scan_process_memory(pid, keywords, max_findings=500, timeout=args.timeout)
    findings = result["findings"]
    print(f"Findings: {len(findings)}")
    print(f"Stats: {result['stats']}")

    # Group by keyword
    import collections
    by_kw = collections.defaultdict(list)
    for f in findings:
        kw_raw = f["keyword"]
        kw_str = kw_raw.decode('utf-8', errors='replace') if isinstance(kw_raw, bytes) else kw_raw
        by_kw[kw_str].append(f)

    for kw, hits in by_kw.items():
        print(f"\n=== {kw!r}  × {len(hits)} ===")
        for h in hits[:5]:
            print(f"  @0x{h['addr']:x}  ctx={h.get('context','')[:100]!r}")


if __name__ == "__main__":
    main()

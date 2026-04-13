#!/usr/bin/env python3
"""
packet_analyzer.py — 离线分析 game_proxy 抓取的封包数据

用法:
  python packet_analyzer.py --capture-dir captures/session_001 --stats
  python packet_analyzer.py --capture-dir captures/session_001 --timeline
  python packet_analyzer.py --capture-dir captures/session_001 --rules rules.json --rule-test
  python packet_analyzer.py --capture-dir captures/session_001 --group-types
  python packet_analyzer.py --capture-dir captures/session_001 --rules rules.json --show-modifications
"""

import argparse
import json
import os
import sys
from collections import defaultdict, Counter
from pathlib import Path

# 导入 game_proxy 中的规则类
_script_dir = os.path.dirname(os.path.abspath(__file__))
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)


def load_rules(rules_path: str):
    """从 JSON 加载规则"""
    from game_proxy import PacketRule
    with open(rules_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    rules = []
    for r in data.get("rules", []):
        rule = PacketRule(
            name=r["name"],
            enabled=r.get("enabled", True),
            search=[(s["pos"], s["val"]) for s in r.get("search", [])],
            modify=[(m["pos"], m["val"]) for m in r.get("modify", [])],
            header_match=bytes.fromhex(r["header"]) if r.get("header") else None,
            length_min=r.get("length_min", 0),
            length_max=r.get("length_max", 0),
            action=r.get("action", "replace"),
        )
        rules.append(rule)
    return rules


def load_capture(capture_dir: str) -> list[dict]:
    """加载抓包目录中的所有封包"""
    packets = []
    capture_path = Path(capture_dir)

    for conn_dir in sorted(capture_path.iterdir()):
        if not conn_dir.is_dir() or not conn_dir.name.startswith("conn_"):
            continue

        # 读取连接元数据
        meta_path = conn_dir / "meta.json"
        if not meta_path.exists():
            continue
        with open(meta_path) as f:
            conn_meta = json.load(f)

        # 读取所有封包
        for bin_file in sorted(conn_dir.glob("*.bin")):
            json_file = bin_file.with_suffix(".json")
            pkt_meta = {}
            if json_file.exists():
                with open(json_file) as f:
                    pkt_meta = json.load(f)

            with open(bin_file, "rb") as f:
                data = f.read()

            packets.append({
                "conn_id": conn_meta["conn_id"],
                "dst_addr": conn_meta["dst_addr"],
                "dst_port": conn_meta["dst_port"],
                "conn_start": conn_meta.get("start_time", 0),
                "direction": pkt_meta.get("direction", bin_file.stem.split("_")[0]),
                "sequence": pkt_meta.get("sequence", 0),
                "timestamp": pkt_meta.get("timestamp", 0),
                "length": len(data),
                "data": data,
                "file": str(bin_file),
            })

    # 按时间排序
    packets.sort(key=lambda p: (p["timestamp"], p["conn_id"], p["sequence"]))
    return packets


def cmd_stats(packets: list[dict]):
    """封包统计"""
    if not packets:
        print("没有封包数据")
        return

    total = len(packets)
    c2s = [p for p in packets if p["direction"] == "send"]
    s2c = [p for p in packets if p["direction"] == "recv"]
    mod = [p for p in packets if p["direction"] == "send_mod"]

    print(f"=== 封包统计 ===")
    print(f"总封包数: {total}")
    print(f"  客户端→服务器 (c2s): {len(c2s)}")
    print(f"  服务器→客户端 (s2c): {len(s2c)}")
    if mod:
        print(f"  修改后封包: {len(mod)}")
    print()

    # 连接统计
    conns = defaultdict(list)
    for p in packets:
        conns[p["conn_id"]].append(p)
    print(f"连接数: {len(conns)}")
    for conn_id, pkts in sorted(conns.items()):
        addr = pkts[0]["dst_addr"]
        port = pkts[0]["dst_port"]
        nc2s = sum(1 for p in pkts if p["direction"] == "send")
        ns2c = sum(1 for p in pkts if p["direction"] == "recv")
        print(f"  {conn_id}: {addr}:{port}  c2s={nc2s} s2c={ns2c}")
    print()

    # 大小分布
    sizes = [p["length"] for p in packets if p["direction"] in ("send", "recv")]
    buckets = [(0, 64), (64, 256), (256, 1024), (1024, 4096), (4096, 65536)]
    print("大小分布:")
    for lo, hi in buckets:
        count = sum(1 for s in sizes if lo <= s < hi)
        if count:
            print(f"  {lo:>5}-{hi:<5}: {count:>5} 个 ({100*count/len(sizes):.1f}%)")
    print()

    # 协议头统计（前 2 字节）
    header_counts = Counter()
    for p in packets:
        if p["direction"] in ("send", "recv") and len(p["data"]) >= 2:
            header_counts[p["data"][:2].hex()] += 1
    print("协议头（前2字节）频率:")
    for header, count in header_counts.most_common(20):
        print(f"  {header}: {count} 次")


def cmd_timeline(packets: list[dict]):
    """时间线视图"""
    if not packets:
        print("没有封包数据")
        return

    t0 = packets[0]["timestamp"]
    print(f"=== 时间线 (T0={packets[0].get('timestamp', 0):.3f}) ===")
    print(f"{'时间':>8}  {'连接':>12}  {'方向':>5}  {'长度':>6}  前32字节hex")
    print("-" * 100)

    for p in packets:
        if p["direction"] == "send_mod":
            continue  # 跳过修改版本
        dt = p["timestamp"] - t0 if t0 > 0 else 0
        direction = "→" if p["direction"] == "send" else "←"
        addr = f"{p['dst_addr']}:{p['dst_port']}"
        hex_head = p["data"][:32].hex() if len(p["data"]) >= 32 else p["data"].hex()
        print(f"{dt:>8.3f}  {p['conn_id']:>12}  {direction:>5}  {p['length']:>6}  {hex_head}")


def cmd_rule_test(packets: list[dict], rules_path: str):
    """对每个封包测试所有规则"""
    rules = load_rules(rules_path)
    print(f"=== 规则测试 ({len(rules)} 条规则, {len(packets)} 个封包) ===\n")

    # 只测试 c2s 和 s2c（跳过 mod）
    test_packets = [p for p in packets if p["direction"] in ("send", "recv")]

    match_count = Counter()
    total_matches = 0

    for p in test_packets:
        data = p["data"]
        for rule in rules:
            matched, reason = rule.match_debug(data)
            if matched:
                match_count[rule.name] += 1
                total_matches += 1
                print(f"  ✓ [{rule.name}] 命中 {p['direction']} "
                      f"{p['conn_id']} seq={p['sequence']} "
                      f"len={p['length']} head={data[:16].hex()}")

    print(f"\n--- 汇总 ---")
    print(f"总封包: {len(test_packets)}, 匹配: {total_matches}")
    if match_count:
        for name, count in match_count.most_common():
            print(f"  [{name}]: {count} 次")
    else:
        print("  没有任何规则匹配！")
        print("\n--- 诊断：每条规则第一个失败原因 ---")
        for rule in rules:
            # 找到最接近匹配的封包
            best_reason = None
            for p in test_packets[:100]:  # 检查前 100 个封包
                matched, reason = rule.match_debug(p["data"])
                if not matched:
                    if best_reason is None or reason.startswith("pos"):
                        best_reason = reason
                else:
                    best_reason = "matched (but not in first 100)"
                    break
            print(f"  [{rule.name}]: {best_reason or 'no packets'}")


def cmd_group_types(packets: list[dict]):
    """按封包类型分组"""
    if not packets:
        print("没有封包数据")
        return

    print("=== 封包类型分组 ===\n")

    # 按前 3 字节分组（对 4366 协议是 magic + subtype）
    type_groups = defaultdict(list)
    for p in packets:
        if p["direction"] == "send_mod":
            continue
        data = p["data"]
        if len(data) >= 3:
            type_key = data[:3].hex()
        elif len(data) >= 2:
            type_key = data[:2].hex()
        else:
            type_key = data.hex() if data else "empty"
        type_groups[type_key].append(p)

    for type_key, pkts in sorted(type_groups.items(), key=lambda x: -len(x[1])):
        directions = Counter(p["direction"] for p in pkts)
        sizes = [p["length"] for p in pkts]
        min_sz, max_sz, avg_sz = min(sizes), max(sizes), sum(sizes) / len(sizes)
        dir_str = ", ".join(f"{d}={c}" for d, c in directions.most_common())
        print(f"  {type_key}: {len(pkts):>5} 个  "
              f"大小={min_sz}-{max_sz} (avg {avg_sz:.0f})  "
              f"方向: {dir_str}")

        # 4366 协议详细解析
        if type_key.startswith("4366"):
            subtypes = Counter()
            for p in pkts:
                if len(p["data"]) >= 7:
                    # 假设: 4366(2) + subtype(1) + length(4)
                    subtype = p["data"][2]
                    payload_len = int.from_bytes(p["data"][3:7], "big")
                    subtypes[f"0x{subtype:02x}"] += 1
            if subtypes:
                print(f"         4366 子类型: {dict(subtypes.most_common())}")
    print()


def cmd_show_modifications(packets: list[dict], rules_path: str):
    """模拟规则修改，展示 before/after diff"""
    rules = load_rules(rules_path)
    test_packets = [p for p in packets if p["direction"] in ("send", "recv")]

    print(f"=== 模拟修改 ({len(rules)} 条规则) ===\n")

    modified_count = 0
    for p in test_packets:
        data = p["data"]
        for rule in rules:
            if rule.matches(data):
                modified = rule.apply(data)
                if modified != data:
                    modified_count += 1
                    print(f"规则 [{rule.name}] @ {p['conn_id']} seq={p['sequence']} "
                          f"({p['direction']}) len={len(data)}")
                    # 找出差异位置
                    diffs = []
                    for i in range(min(len(data), len(modified))):
                        if data[i] != modified[i]:
                            diffs.append(f"  pos {i}: 0x{data[i]:02x} → 0x{modified[i]:02x}")
                    for d in diffs:
                        print(d)
                    print(f"  原始: {data[:32].hex()}")
                    print(f"  修改: {modified[:32].hex()}")
                    print()
                break  # 第一条匹配就停

    if modified_count == 0:
        print("没有封包会被修改（规则全部不匹配）")


def main():
    parser = argparse.ArgumentParser(description="离线封包分析工具")
    parser.add_argument("--capture-dir", required=True, help="抓包目录")
    parser.add_argument("--rules", help="规则 JSON 文件")
    parser.add_argument("--stats", action="store_true", help="显示统计信息")
    parser.add_argument("--timeline", action="store_true", help="时间线视图")
    parser.add_argument("--rule-test", action="store_true", help="规则匹配测试")
    parser.add_argument("--group-types", action="store_true", help="按类型分组")
    parser.add_argument("--show-modifications", action="store_true", help="模拟修改展示")
    parser.add_argument("--all", action="store_true", help="运行所有分析")
    args = parser.parse_args()

    if not os.path.isdir(args.capture_dir):
        print(f"错误: 目录不存在: {args.capture_dir}")
        return

    print(f"加载抓包数据: {args.capture_dir}")
    packets = load_capture(args.capture_dir)
    print(f"共 {len(packets)} 个封包\n")

    run_all = args.all or not any([args.stats, args.timeline, args.rule_test,
                                    args.group_types, args.show_modifications])

    if args.stats or run_all:
        cmd_stats(packets)
        print()

    if args.timeline or run_all:
        cmd_timeline(packets)
        print()

    if args.group_types or run_all:
        cmd_group_types(packets)
        print()

    if (args.rule_test or run_all) and args.rules:
        cmd_rule_test(packets, args.rules)
        print()

    if (args.show_modifications or run_all) and args.rules:
        cmd_show_modifications(packets, args.rules)


if __name__ == "__main__":
    main()

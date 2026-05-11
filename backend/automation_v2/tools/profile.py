"""profile.py — decisions.jsonl perf 聚合, 每 phase × 6 段 ms 分位数.

用途: 回归对比 (v2 vs v1, 灰度前后), 看 P2 round_total p50/p95 是否达标 < 200ms.

用法:
    python -m backend.automation_v2.tools.profile <session_dir>
    python -m backend.automation_v2.tools.profile <session_dir> --phase P2
    python -m backend.automation_v2.tools.profile <session_dir> --inst 3
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from collections import defaultdict


def percentile(arr: list[float], p: float) -> float:
    """简单分位数 (不要 numpy, replay 工具尽量无依赖)."""
    if not arr:
        return 0.0
    s = sorted(arr)
    k = (len(s) - 1) * p
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def main() -> int:
    ap = argparse.ArgumentParser(description="聚合 decisions.jsonl perf")
    ap.add_argument("session_dir")
    ap.add_argument("--phase", default=None)
    ap.add_argument("--inst", type=int, default=None)
    args = ap.parse_args()

    path = Path(args.session_dir) / "decisions.jsonl"
    if not path.exists():
        print(f"[profile] {path} 不存在", file=sys.stderr)
        return 1

    # phase → seg_name → [ms 列表]
    buckets: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    outcome_count: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    total_count = 0

    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if args.phase and e.get("phase") != args.phase:
                continue
            if args.inst is not None and e.get("inst") != args.inst:
                continue
            ph = e.get("phase", "?")
            ms = e.get("ms", {})
            for seg in ("capture", "yolo_q", "yolo", "decide", "tap_q", "tap", "round_total"):
                v = ms.get(seg, 0)
                if v > 0:
                    buckets[ph][seg].append(v)
            outcome_count[ph][e.get("outcome", "?")] += 1
            total_count += 1

    print(f"[profile] {total_count} decisions across {len(buckets)} phases\n")
    for ph in sorted(buckets.keys()):
        print(f"═══ {ph} ═══")
        print(f"{'seg':<12}{'count':>7}{'p50':>9}{'p95':>9}{'p99':>9}{'max':>9}")
        for seg in ("capture", "yolo_q", "yolo", "decide", "tap_q", "tap", "round_total"):
            arr = buckets[ph].get(seg, [])
            if not arr:
                continue
            print(f"{seg:<12}{len(arr):>7}"
                  f"{percentile(arr, 0.5):>8.1f}ms"
                  f"{percentile(arr, 0.95):>8.1f}ms"
                  f"{percentile(arr, 0.99):>8.1f}ms"
                  f"{max(arr):>8.1f}ms")
        print("outcomes:")
        for outcome, n in sorted(outcome_count[ph].items(), key=lambda x: -x[1]):
            print(f"  {outcome:<24} {n}")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())

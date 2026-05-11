"""replay.py — 按 trace_id 回放决策, 显示完整 6 段 ms breakdown.

用途: 用户报"5:23 时 inst 3 popup 关 5 秒慢" → grep decisions.jsonl trace_id → replay 看慢段.

用法:
    python -m backend.automation_v2.tools.replay <session_dir> <trace_id>
    python -m backend.automation_v2.tools.replay <session_dir> --slow N    # 列出 round_total > N ms 的 top
    python -m backend.automation_v2.tools.replay <session_dir> --inst 3    # 仅 inst 3 决策
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def load_jsonl(path: Path) -> list[dict]:
    """读 decisions.jsonl, 容错坏行."""
    out: list[dict] = []
    if not path.exists():
        print(f"[replay] decisions.jsonl 不存在: {path}", file=sys.stderr)
        return out
    with path.open(encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"[replay] line {i} 坏 json: {e}", file=sys.stderr)
    return out


def fmt_breakdown(entry: dict) -> str:
    """格式化 6 段 ms 成 ASCII bar."""
    ms = entry.get("ms", {})
    total = ms.get("round_total", 0)
    segs = [
        ("capture", ms.get("capture", 0)),
        ("yolo_q",  ms.get("yolo_q", 0)),
        ("yolo",    ms.get("yolo", 0)),
        ("decide",  ms.get("decide", 0)),
        ("tap_q",   ms.get("tap_q", 0)),
        ("tap",     ms.get("tap", 0)),
    ]
    lines = []
    for name, v in segs:
        pct = (v / total * 40) if total > 0 else 0
        bar = "█" * int(pct)
        lines.append(f"  {name:<8} {v:>7.1f}ms  {bar}")
    return "\n".join(lines)


def show_one(entry: dict) -> None:
    """完整打印一条决策."""
    print("─" * 70)
    print(f"trace_id   {entry.get('trace_id', '?')}")
    print(f"ts         {entry.get('ts', 0)}")
    print(f"inst       {entry.get('inst', '?')}")
    print(f"phase      {entry.get('phase', '?')} R{entry.get('round', '?')}")
    print(f"outcome    {entry.get('outcome', '?')}")
    print(f"tap        {entry.get('tap_xy', None)} target={entry.get('tap_target', '')} "
          f"conf={entry.get('conf', 0)}")
    print(f"dets_count {entry.get('dets_count', 0)}")
    print(f"note       {entry.get('note', '')}")
    print("ms breakdown:")
    print(fmt_breakdown(entry))
    total = entry.get("ms", {}).get("round_total", 0)
    print(f"  {'TOTAL':<8} {total:>7.1f}ms")


def main() -> int:
    ap = argparse.ArgumentParser(description="回放 decision.jsonl trace")
    ap.add_argument("session_dir", help="session 目录 (含 decisions.jsonl)")
    ap.add_argument("trace_id", nargs="?", help="trace_id (12-char), 不传则需 --slow / --inst")
    ap.add_argument("--slow", type=float, default=None,
                    help="列出 round_total > N ms 的决策 (top 20)")
    ap.add_argument("--inst", type=int, default=None,
                    help="仅看 instance N")
    ap.add_argument("--phase", default=None, help="仅看 phase (e.g. P2)")
    args = ap.parse_args()

    path = Path(args.session_dir) / "decisions.jsonl"
    entries = load_jsonl(path)
    if not entries:
        return 1
    print(f"[replay] loaded {len(entries)} decisions from {path}")

    if args.trace_id:
        hits = [e for e in entries if e.get("trace_id", "").startswith(args.trace_id)]
        if not hits:
            print(f"[replay] no match for trace_id={args.trace_id}")
            return 1
        for e in hits:
            show_one(e)
        return 0

    # filter
    filtered = entries
    if args.inst is not None:
        filtered = [e for e in filtered if e.get("inst") == args.inst]
    if args.phase:
        filtered = [e for e in filtered if e.get("phase") == args.phase]

    if args.slow is not None:
        slow = [e for e in filtered if e.get("ms", {}).get("round_total", 0) > args.slow]
        slow.sort(key=lambda e: -e.get("ms", {}).get("round_total", 0))
        print(f"[replay] slow > {args.slow}ms: {len(slow)} 条 (top 20)")
        for e in slow[:20]:
            ms = e.get("ms", {}).get("round_total", 0)
            print(f"  {ms:>7.1f}ms  trace={e.get('trace_id','?')[:12]} "
                  f"inst{e.get('inst','?')} {e.get('phase','?')} R{e.get('round','?')} "
                  f"{e.get('outcome','?')}")
        return 0

    print("[replay] 传 trace_id 或 --slow N 来过滤")
    return 0


if __name__ == "__main__":
    sys.exit(main())

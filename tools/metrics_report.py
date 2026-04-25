"""
跑完一次自动化后，分析 logs/<session>/metrics.jsonl 给出简报。

用法：
    python tools/metrics_report.py logs/20260423_HHMMSS/metrics.jsonl
    python tools/metrics_report.py logs/  # 自动选最新会话
"""
import json
import os
import sys
from collections import defaultdict
from statistics import mean, median


def load(path):
    if os.path.isdir(path):
        # 选最新会话
        sessions = sorted([d for d in os.listdir(path)
                          if os.path.isdir(os.path.join(path, d))])
        if not sessions:
            print(f"no sessions in {path}")
            sys.exit(1)
        path = os.path.join(path, sessions[-1], "metrics.jsonl")
    if not os.path.isfile(path):
        print(f"not found: {path}")
        sys.exit(1)
    print(f"解析: {path}\n")
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                records.append(json.loads(line))
            except Exception:
                pass
    return records


def pct(lst, p):
    if not lst:
        return 0
    lst = sorted(lst)
    idx = int(len(lst) * p / 100)
    return lst[min(idx, len(lst) - 1)]


def fmt_ms(v):
    return f"{v:.1f}ms"


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "logs/"
    records = load(path)

    # 按 action 分组
    by_action = defaultdict(list)
    for r in records:
        by_action[r["action"]].append(r)

    print(f"总记录数: {len(records)}\n")
    print(f"{'action':<20} {'count':>6} {'mean':>10} {'p50':>10} {'p95':>10} {'p99':>10} {'max':>10}")
    print("-" * 80)
    for action, recs in sorted(by_action.items()):
        durs = [r.get("dur_ms", 0) for r in recs]
        if not durs:
            continue
        print(f"{action:<20} {len(recs):>6} "
              f"{fmt_ms(mean(durs)):>10} "
              f"{fmt_ms(pct(durs,50)):>10} "
              f"{fmt_ms(pct(durs,95)):>10} "
              f"{fmt_ms(pct(durs,99)):>10} "
              f"{fmt_ms(max(durs)):>10}")

    # Phase 摘要
    if "phase" in by_action:
        print("\n== Phase 明细 ==")
        by_phase = defaultdict(list)
        for r in by_action["phase"]:
            by_phase[r.get("name", "?")].append(r)
        print(f"{'phase':<20} {'count':>6} {'mean':>10} {'ok%':>6}")
        print("-" * 50)
        for name, recs in sorted(by_phase.items()):
            durs = [r.get("dur_ms", 0) for r in recs]
            oks = sum(1 for r in recs if r.get("result") == "ok")
            print(f"{name:<20} {len(recs):>6} {fmt_ms(mean(durs)):>10} "
                  f"{100*oks/len(recs):>5.0f}%")

    # 模板命中率
    if "template_match" in by_action:
        print("\n== 模板命中率（Top 10 按次数） ==")
        by_tpl = defaultdict(lambda: {"hit": 0, "miss": 0, "durs": []})
        for r in by_action["template_match"]:
            tpl = r.get("tpl", "?")
            if r.get("hit"):
                by_tpl[tpl]["hit"] += 1
            else:
                by_tpl[tpl]["miss"] += 1
            by_tpl[tpl]["durs"].append(r.get("dur_ms", 0))
        rows = sorted(by_tpl.items(),
                      key=lambda kv: -(kv[1]["hit"] + kv[1]["miss"]))[:10]
        print(f"{'tpl':<35} {'total':>6} {'hit%':>6} {'mean_ms':>10}")
        print("-" * 60)
        for tpl, d in rows:
            total = d["hit"] + d["miss"]
            hit_rate = 100 * d["hit"] / total if total else 0
            print(f"{tpl:<35} {total:>6} {hit_rate:>5.0f}% "
                  f"{mean(d['durs']) if d['durs'] else 0:>9.1f}")

    # 截图后端
    if "screenshot" in by_action:
        print("\n== 截图后端分布 ==")
        by_backend = defaultdict(list)
        for r in by_action["screenshot"]:
            by_backend[r.get("backend", "?")].append(r.get("dur_ms", 0))
        for b, durs in by_backend.items():
            print(f"  {b}: {len(durs)} 次, mean={mean(durs):.1f}ms, "
                  f"p95={pct(durs,95):.1f}ms")

    # 系统指标（CPU / 内存 / 线程）
    if "sys" in by_action:
        print("\n== 系统指标 ==")
        samples = by_action["sys"]
        proc_cpu = [r.get("proc_cpu", 0) for r in samples]
        proc_rss = [r.get("proc_rss_mb", 0) for r in samples]
        threads = [r.get("proc_threads", 0) for r in samples]
        sys_cpu = [r.get("sys_cpu", 0) for r in samples]
        sys_mem = [r.get("sys_mem_pct", 0) for r in samples]
        print(f"  采样数: {len(samples)} ({len(samples)*2}s)")
        print(f"  进程 CPU%:   mean={mean(proc_cpu):5.1f}  p95={pct(proc_cpu,95):5.1f}  max={max(proc_cpu):5.1f}")
        print(f"  进程 RSS MB: mean={mean(proc_rss):5.0f}  max={max(proc_rss):5.0f}")
        print(f"  进程 线程:   max={max(threads)}")
        print(f"  全机 CPU%:   mean={mean(sys_cpu):5.1f}  p95={pct(sys_cpu,95):5.1f}")
        print(f"  全机 MEM%:   mean={mean(sys_mem):5.1f}  max={max(sys_mem):5.1f}")

    # 事件循环 lag
    if "loop_lag" in by_action:
        print("\n== 事件循环延迟（threshold=50ms 以上的才记录）==")
        lags = [r.get("lag_ms", 0) for r in by_action["loop_lag"]]
        print(f"  触发次数: {len(lags)}")
        if lags:
            print(f"  lag_ms:   mean={mean(lags):.1f}  p50={pct(lags,50):.1f}  "
                  f"p95={pct(lags,95):.1f}  max={max(lags):.1f}")
            big = [l for l in lags if l > 500]
            if big:
                print(f"  ⚠️ lag>500ms 次数: {len(big)}（严重卡顿，可能是 OCR 阻塞 loop）")


if __name__ == "__main__":
    main()

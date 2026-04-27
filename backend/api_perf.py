"""
/api/perf/* — 性能监控 (整机 + per-instance).

数据源:
  整机: psutil cpu/mem/process_count
  每实例: 从 metrics.jsonl 读最近 N 秒的 record() 条目, 聚合成
          tier_ms (模板/记忆/YOLO/文字/视觉模型 各自平均) + adb_screenshot_ms + fps

不破坏 metrics.py 现有结构, 只读不写.
"""
from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)

router = APIRouter()


def _global_perf() -> dict:
    """整机 CPU / 内存 / 进程数 (不阻塞, ~1ms)."""
    try:
        import psutil
        vm = psutil.virtual_memory()
        return {
            "cpu_percent": round(psutil.cpu_percent(interval=None), 1),
            "mem_percent": round(vm.percent, 1),
            "mem_used_mb": round(vm.used / (1024 * 1024)),
            "mem_total_mb": round(vm.total / (1024 * 1024)),
            "process_count": len(psutil.pids()),
        }
    except Exception as e:
        return {"error": str(e)}


def _read_metrics_tail(path: Path, max_lines: int = 5000) -> list[dict]:
    """读 metrics.jsonl 最后 N 行 (从尾部反向读); 文件可能很大."""
    if not path.is_file():
        return []
    out: list[dict] = []
    try:
        # 简单做法: 全读 split (metrics.jsonl 每会话一文件, 通常 < 50MB)
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            lines = fh.readlines()
        for line in lines[-max_lines:]:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    except Exception as e:
        logger.debug(f"[perf] read metrics err: {e}")
    return out


def _current_metrics_path() -> Optional[Path]:
    try:
        from .automation.decision_log import get_recorder
        rec = get_recorder()
        sess = rec.root()
        if sess is None:
            return None
        return sess.parent / "metrics.jsonl"
    except Exception:
        return None


# 中文术语映射
_TIER_CN = {
    "template_match": "模板",
    "yolo_detect": "YOLO",
    "ocr": "文字",
    "vlm": "视觉模型",
    "memory_query": "记忆",
}


def _aggregate_per_instance(records: list[dict], window_s: float) -> dict:
    """从 metrics records 聚合每实例 tier 耗时 / 帧率 / 截屏延迟."""
    now = time.time()
    cutoff = now - window_s
    by_inst: dict[int, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    by_inst_phase: dict[int, str] = {}
    by_inst_round: dict[int, int] = {}
    by_inst_frame_ts: dict[int, list[float]] = defaultdict(list)

    for r in records:
        ts = float(r.get("ts", 0))
        if ts < cutoff:
            continue
        inst = r.get("instance_idx")
        # 没显式 instance_idx 时用 inst 字段
        if inst is None:
            inst = r.get("instance")
        if inst is None:
            inst = r.get("inst")
        try:
            inst = int(inst) if inst is not None else None
        except (ValueError, TypeError):
            inst = None
        if inst is None:
            continue
        action = r.get("action", "")
        dur = float(r.get("dur_ms", 0) or 0)

        # tier 耗时
        tier_cn = _TIER_CN.get(action)
        if tier_cn and dur > 0:
            by_inst[inst][tier_cn].append(dur)

        # ADB 截屏
        if action in ("adb_screenshot", "screenshot") and dur > 0:
            by_inst[inst]["adb_screenshot_ms"].append(dur)

        # 帧时间戳
        if action.startswith("phase_round") or action == "phase_step":
            by_inst_frame_ts[inst].append(ts)

        # 当前阶段 / 轮
        if "phase" in r:
            by_inst_phase[inst] = str(r.get("phase", ""))
        if "round" in r:
            try:
                by_inst_round[inst] = int(r["round"])
            except (ValueError, TypeError):
                pass

    out: dict[int, dict[str, Any]] = {}
    for inst, kv in by_inst.items():
        tier_ms: dict[str, dict[str, float]] = {}
        for k, vs in kv.items():
            if k.startswith("adb_") or k == "adb_screenshot_ms":
                continue
            if not vs:
                continue
            arr = sorted(vs)
            tier_ms[k] = {
                "count": len(arr),
                "avg": round(sum(arr) / len(arr), 1),
                "p95": round(arr[min(len(arr) - 1, int(len(arr) * 0.95))], 1),
                "max": round(arr[-1], 1),
            }
        adb = kv.get("adb_screenshot_ms") or []
        adb_avg = round(sum(adb) / len(adb), 1) if adb else 0
        ts_list = sorted(by_inst_frame_ts.get(inst, []))
        fps = 0
        if len(ts_list) >= 2:
            span = ts_list[-1] - ts_list[0]
            if span > 0:
                fps = round((len(ts_list) - 1) / span, 2)
        # 健康度: fps < 1.5 → slow; tier max > 200 → warn
        health = "ok"
        any_slow = any(t.get("max", 0) > 200 for t in tier_ms.values())
        if fps > 0 and fps < 1.5:
            health = "slow"
        elif any_slow:
            health = "slow"
        out[inst] = {
            "fps": fps,
            "adb_screenshot_ms": adb_avg,
            "tier_ms": tier_ms,
            "phase": by_inst_phase.get(inst, ""),
            "round": by_inst_round.get(inst, 0),
            "health": health,
        }
    return out


def _bottleneck_top(records: list[dict], n: int = 5) -> list[dict]:
    """最近 5 帧最慢的环节 (跨 instance)."""
    items = []
    for r in records:
        action = r.get("action", "")
        if action not in _TIER_CN:
            continue
        dur = float(r.get("dur_ms", 0) or 0)
        if dur < 50:  # 50ms 以下不算瓶颈
            continue
        items.append({
            "ts": float(r.get("ts", 0)),
            "instance": r.get("instance_idx") or r.get("instance"),
            "phase": r.get("phase", ""),
            "round": r.get("round", 0),
            "tier": _TIER_CN[action],
            "duration_ms": round(dur, 1),
        })
    items.sort(key=lambda x: -x["duration_ms"])
    return items[:n]


# ─── /api/perf/snapshot ───


@router.get("/api/perf/snapshot")
async def perf_snapshot(window: float = Query(30.0, ge=1, le=600)):
    """整机 + 每实例当前 (最近 window 秒) 性能快照."""
    g = _global_perf()
    path = _current_metrics_path()
    records = _read_metrics_tail(path) if path else []
    inst_perf = _aggregate_per_instance(records, window_s=window)
    bottleneck = _bottleneck_top(records[-200:], n=5) if records else []
    return {
        "ts": time.time(),
        "global": g,
        "instances": inst_perf,
        "bottleneck": bottleneck,
        "metrics_path": str(path) if path else "",
        "metrics_records_count": len(records),
        "window_s": window,
    }


# ─── /api/perf/series ───


@router.get("/api/perf/series")
async def perf_series(key: str = Query(...), window: float = Query(300, ge=10, le=3600)):
    """时间序列 (用于画曲线): cpu_percent / mem_percent / yolo_avg_ms / fps_inst{N}.

    返回 [(ts, value), ...].
    当前 cpu/mem 这种是从 metrics.jsonl 的 sys_sample 记录读.
    """
    path = _current_metrics_path()
    if not path:
        return {"key": key, "points": []}
    records = _read_metrics_tail(path)
    now = time.time()
    cutoff = now - window
    pts = []
    for r in records:
        ts = float(r.get("ts", 0))
        if ts < cutoff:
            continue
        if r.get("action") == "sys_sample":
            if key == "cpu_percent" and "sys_cpu" in r:
                pts.append([ts, float(r["sys_cpu"])])
            elif key == "mem_percent" and "sys_mem_pct" in r:
                pts.append([ts, float(r["sys_mem_pct"])])
            elif key == "process_mem_mb" and "proc_mem_mb" in r:
                pts.append([ts, float(r["proc_mem_mb"])])
    return {"key": key, "points": pts}

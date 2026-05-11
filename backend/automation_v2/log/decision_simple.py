"""DecisionSimple — JSONL 1 行/决策, 7 时间戳 → 6 段 ms 落盘.

- 同步写, 不走 ThreadPoolExecutor (省 v1 的 100-300ms 排队)
- 12 实例共享 1 lock, 行缓冲, < 1ms/行
- 6 实例 × 1.5 决策/秒 × 10h = 32 万 × 250 byte = ~80 MB/天 (vs v1 16 GB/天, -99.5%)

强复现: grep trace_id → 看完整 6 段 ms (capture/yolo_q/yolo/decide/tap_q/tap).
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Optional, Protocol

logger = logging.getLogger(__name__)


class DecisionLogProto(Protocol):
    """Protocol — DecisionSimple / DecisionDetailed 都实现."""

    def record(self, **kwargs) -> None: ...
    def close(self) -> None: ...


class DecisionSimple:
    """JSONL 文件, append-only. 每决策 1 行, ~250 byte."""

    def __init__(self, session_dir: Path):
        session_dir = Path(session_dir)
        session_dir.mkdir(parents=True, exist_ok=True)
        self.path = session_dir / "decisions.jsonl"
        self._lock = threading.Lock()
        # buffering=1 行缓冲, write 后立刻 flush, 不依赖 GC
        self._fp = open(self.path, "a", buffering=1, encoding="utf-8")
        logger.info(f"[dlog/simple] writing → {self.path}")

    def record(self, *,
               inst: int,
               phase: str,
               round_idx: int,
               outcome: str,
               # 7 时间戳 (perf_counter 绝对值, 相减后是相对秒)
               t_round_start: float,
               t_capture_done: float,
               t_yolo_start: float,
               t_yolo_done: float,
               t_decide: float,
               t_tap_send: float,
               t_tap_done: float,
               tap: Optional[tuple[int, int]] = None,
               tap_target: str = "",
               conf: float = 0.0,
               trace_id: str = "",
               dets_count: int = 0,
               note: str = "") -> None:
        """落盘 1 行 JSON. perf_counter 时间戳, 落盘前算成 6 段 ms."""
        base = t_round_start
        # 时间戳缺失时 (e.g. phase 跳过 yolo) 算 0ms 段, 不报错
        def seg(t_to: float, t_from: float) -> float:
            if t_to == 0.0 or t_from == 0.0:
                return 0.0
            return round((t_to - t_from) * 1000, 1)

        entry = {
            "ts": time.time(),                  # wall clock, 排查时人读
            "trace_id": trace_id,
            "inst": inst,
            "phase": phase,
            "round": round_idx,
            "outcome": outcome,
            "tap_target": tap_target,
            "tap_xy": list(tap) if tap else None,
            "conf": round(conf, 3),
            "dets_count": dets_count,
            "ms": {
                "capture":     seg(t_capture_done, base),
                "yolo_q":      seg(t_yolo_start, t_capture_done),
                "yolo":        seg(t_yolo_done, t_yolo_start),
                "decide":      seg(t_decide, t_yolo_done),
                "tap_q":       seg(t_tap_send, t_decide),
                "tap":         seg(t_tap_done, t_tap_send),
                "round_total": seg(t_tap_done or t_decide, base),
            },
            "note": note[:200],   # 截短防 1KB+ note 撑爆 1 行
        }
        line = json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._lock:
            try:
                self._fp.write(line)
            except Exception as e:
                logger.debug(f"[dlog/simple] write err: {e}")

    def close(self) -> None:
        with self._lock:
            try:
                self._fp.close()
            except Exception:
                pass

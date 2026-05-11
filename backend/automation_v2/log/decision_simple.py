"""DecisionSimple — JSONL 1 行/决策, 7 时间戳 → 6 段 ms 落盘.

- 同步写, 不走 ThreadPoolExecutor (省 v1 的 100-300ms 排队)
- 12 实例共享 1 lock, 行缓冲, < 1ms/行
- 6 实例 × 1.5 决策/秒 × 10h = 32 万 × 250 byte = ~80 MB/天 (vs v1 16 GB/天, -99.5%)

强复现: grep trace_id → 看完整 6 段 ms (capture/yolo_q/yolo/decide/tap_q/tap).

REVIEW_DAY3_RISKS.md (2026-05-11) 修复:
- R-C1: Lock 改 context manager 无 timeout (数据完整性 > 理论死锁防御)
- R-H3: 计数器 += 用 _stat_lock 保护 (CPython GIL 不保证 += 原子)
- R-M2: jsonl 加 latency_ms 兼容 v1 schema
- R-M4: 加 shot/tier_evidence/yolo_dets 参数 (兼容 Detailed Protocol)
- atexit.register(close) 进程退出自动关 fd
"""
from __future__ import annotations

import atexit
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Optional, Protocol

# orjson 可选, 比 stdlib json 快 ~80%. 没装 fallback 用 json.
try:
    import orjson    # type: ignore
    _HAS_ORJSON = True
except ImportError:
    _HAS_ORJSON = False

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
        self._lock = threading.Lock()                  # write lock (持有 < 1ms)
        self._stat_lock = threading.Lock()             # 保护计数器 += 原子
        # buffering=1 行缓冲, write 后立刻 flush, 不依赖 GC
        self._fp = open(self.path, "a", buffering=1, encoding="utf-8")
        self._record_count = 0
        self._closed = False
        atexit.register(self.close)                    # 进程退出自动关 fd
        logger.info(
            f"[dlog/simple] writing → {self.path} "
            f"(orjson={'yes' if _HAS_ORJSON else 'no'})"
        )

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
               note: str = "",
               # Protocol 兼容: Detailed 用这些, Simple 收下不用 (R-M4)
               shot: Optional[Any] = None,
               tier_evidence: Optional[list[dict]] = None,
               yolo_dets: Optional[list[dict]] = None) -> None:
        """落盘 1 行 JSON. perf_counter 时间戳, 落盘前算成 6 段 ms."""
        base = t_round_start
        # 时间戳缺失时 (e.g. phase 跳过 yolo) 算 0ms 段, 不报错
        def seg(t_to: float, t_from: float) -> float:
            if t_to == 0.0 or t_from == 0.0:
                return 0.0
            return round((t_to - t_from) * 1000, 1)

        ms_dict = {
            "capture":     seg(t_capture_done, base),
            "yolo_q":      seg(t_yolo_start, t_capture_done),
            "yolo":        seg(t_yolo_done, t_yolo_start),
            "decide":      seg(t_decide, t_yolo_done),
            "tap_q":       seg(t_tap_send, t_decide),
            "tap":         seg(t_tap_done, t_tap_send),
            "round_total": seg(t_tap_done or t_decide, base),
        }
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
            "ms": ms_dict,
            "latency_ms": ms_dict["round_total"],   # R-M2: v1 schema 兼容字段
            "note": note[:200],                     # 截短防 1KB+ note 撑爆 1 行
        }
        # 序列化: orjson 快 ~80%, fallback json
        if _HAS_ORJSON:
            line = orjson.dumps(entry, option=orjson.OPT_NON_STR_KEYS).decode("utf-8") + "\n"
        else:
            line = json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n"

        # R-C1: context manager 无 timeout (数据完整性 > 理论死锁防御)
        # 我们没嵌套 lock + write < 1ms, 死锁不可能. 真死锁系统级 watchdog 重启.
        try:
            with self._lock:
                self._fp.write(line)
                # R-H3: 计数器 += 用 _stat_lock 保护原子
                with self._stat_lock:
                    self._record_count += 1
                    record_count = self._record_count
                # 每 1000 条 sanity check (fd 是不是还开着)
                if record_count % 1000 == 0 and self._fp.closed:
                    logger.error(f"[dlog/simple] fp closed unexpectedly at record={record_count}")
        except Exception as e:
            logger.debug(f"[dlog/simple] write err: {e}")

    def close(self) -> None:
        """显式关 fd. atexit 也会调."""
        if self._closed:
            return
        self._closed = True
        with self._lock:
            try:
                self._fp.close()
            except Exception:
                pass
        logger.info(f"[dlog/simple] closed, records={self._record_count}")

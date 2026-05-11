"""DecisionDetailed — 详细决策日志, env GAMEBOT_DETAILED_LOG=1 启用.

REVIEW_DECISIONLOG.md 推荐架构:
- JSONL 决策元数据 (同步追加, 含 tier_evidence) → 单文件
- input.jpg 异步 imwrite 池 (砍 yolo_annot / tap_annot 等注解图)
- ThreadPoolExecutor max_workers=min(12, cpu_count)
- pool.shutdown(wait=True) 在 close() 时干净退出
- imwrite 异常降级 sync write
- fd 监控 (每 1000 条 check, 防长跑泄漏)

性能 (vs Day 1 简版):
- 单 record() 调用: 66 µs (decision JSONL) + 5 µs (imwrite submit) = 71 µs
- imwrite 本身 150-250ms 异步, 不阻塞主 round
- 12 实例 18 决策/秒 lock 串行: ~1.2 ms/秒 aggregate, 可忽略

存储 (vs Day 1 简版):
- 简版: ~80 MB/天 (decision.jsonl only)
- 详版: ~150 MB/天 (decision.jsonl + input_*.jpg × 32 万)
- v1 详版: ~16 GB/天 (-99%)
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# orjson 可选 fallback
try:
    import orjson    # type: ignore
    _HAS_ORJSON = True
except ImportError:
    _HAS_ORJSON = False


class DecisionDetailed:
    """决策详细日志. env GAMEBOT_DETAILED_LOG=1 时启用 (vs DecisionSimple)."""

    def __init__(
        self,
        session_dir: Path,
        *,
        img_pool_workers: Optional[int] = None,
        img_quality: int = 70,
    ):
        session_dir = Path(session_dir)
        session_dir.mkdir(parents=True, exist_ok=True)
        self.session_dir = session_dir
        self.path = session_dir / "decisions_detailed.jsonl"
        self.img_dir = session_dir / "img"
        self.img_dir.mkdir(exist_ok=True)
        self.img_quality = img_quality

        # JSONL 同步写
        self._lock = threading.Lock()
        self._fp = open(self.path, "a", buffering=1, encoding="utf-8")

        # 图异步池: 12 worker (足够 12 实例并发 imwrite, 不阻塞主 round)
        if img_pool_workers is None:
            img_pool_workers = min(12, (os.cpu_count() or 4))
        self._img_pool = ThreadPoolExecutor(
            max_workers=img_pool_workers,
            thread_name_prefix="dlog-img",
        )

        # 监控状态
        self._record_count = 0
        self._lock_timeout_count = 0
        self._imwrite_fail_count = 0

        logger.info(
            f"[dlog/detailed] writing → {self.path} "
            f"(img_pool={img_pool_workers}, orjson={'yes' if _HAS_ORJSON else 'no'})"
        )

    def record(
        self,
        *,
        # 决策核心 (跟 DecisionSimple 兼容)
        inst: int,
        phase: str,
        round_idx: int,
        outcome: str,
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
        # 详版独有: 截图 + 详细证据
        shot: Optional[Any] = None,                # np.ndarray
        tier_evidence: Optional[list[dict]] = None,
        yolo_dets: Optional[list[dict]] = None,
    ) -> None:
        """落盘 1 行 JSON (元数据 + tier_evidence) + 异步 imwrite shot."""

        # 1. 异步写 input.jpg (不阻塞主 round)
        img_filename: Optional[str] = None
        if shot is not None:
            img_filename = f"{trace_id or self._record_count}.jpg"
            img_path = self.img_dir / img_filename
            try:
                self._img_pool.submit(self._write_image_safe, img_path, shot)
            except RuntimeError as e:
                # pool 关闭后还在 submit → 降级同步写
                logger.debug(f"[dlog/detailed] pool err {e}, fallback sync")
                self._write_image_safe(img_path, shot)

        # 2. 同步追加 JSONL (决策元数据 + tier_evidence)
        base = t_round_start

        def seg(t_to: float, t_from: float) -> float:
            if t_to == 0.0 or t_from == 0.0:
                return 0.0
            return round((t_to - t_from) * 1000, 1)

        entry = {
            "ts": time.time(),
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
            "note": note[:500],   # 详版允许更长 note
            # 详版独有
            "input_image": img_filename,
            "tier_evidence": tier_evidence or [],
            "yolo_dets": yolo_dets or [],
        }

        if _HAS_ORJSON:
            line = orjson.dumps(entry, option=orjson.OPT_NON_STR_KEYS).decode("utf-8") + "\n"
        else:
            line = json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n"

        # Lock with timeout 防理论死锁
        acquired = self._lock.acquire(timeout=5.0)
        if not acquired:
            self._lock_timeout_count += 1
            if self._lock_timeout_count <= 5:
                logger.error(f"[dlog/detailed] lock timeout (5s), dropping trace={trace_id}")
            return
        try:
            self._fp.write(line)
            self._record_count += 1
            # 每 1000 条 sanity check
            if self._record_count % 1000 == 0:
                if self._fp.closed:
                    logger.error(f"[dlog/detailed] fp closed at record={self._record_count}")
                # 监控 imwrite 失败率
                fail_rate = self._imwrite_fail_count / self._record_count if self._record_count > 0 else 0
                if fail_rate > 0.05:
                    logger.warning(
                        f"[dlog/detailed] imwrite 失败率高: "
                        f"{self._imwrite_fail_count}/{self._record_count} ({fail_rate:.1%})"
                    )
        except Exception as e:
            logger.debug(f"[dlog/detailed] write err: {e}")
        finally:
            self._lock.release()

    def _write_image_safe(self, path: Path, img: Any) -> None:
        """imwrite with try-except + 失败计数. pool worker 中调用."""
        try:
            import cv2
            cv2.imwrite(str(path), img, [cv2.IMWRITE_JPEG_QUALITY, self.img_quality])
        except Exception as e:
            self._imwrite_fail_count += 1
            logger.debug(f"[dlog/detailed] imwrite {path.name} fail: {e}")

    def close(self) -> None:
        """退出时调. JSONL fd 关 + pool.shutdown 干净退出."""
        with self._lock:
            try:
                self._fp.close()
            except Exception:
                pass
        try:
            self._img_pool.shutdown(wait=True)
        except Exception:
            try:
                self._img_pool.shutdown(wait=False)
            except Exception:
                pass
        logger.info(
            f"[dlog/detailed] closed. records={self._record_count} "
            f"imwrite_fails={self._imwrite_fail_count} "
            f"lock_timeouts={self._lock_timeout_count}"
        )


def make_decision_log(session_dir: Path) -> Any:
    """工厂函数: 根据 env 自动选 simple/detailed.

    GAMEBOT_DETAILED_LOG=1 → DecisionDetailed
    其他 → DecisionSimple (默认)
    """
    from .decision_simple import DecisionSimple

    if os.environ.get("GAMEBOT_DETAILED_LOG", "0") == "1":
        return DecisionDetailed(session_dir)
    return DecisionSimple(session_dir)

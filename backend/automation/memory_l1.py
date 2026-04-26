"""
L1 Frame Memory — phash + SQLite 复读机 (Tier 1)

设计：
- 见过这个画面 (phash 距离 < max_dist) → 复用历史 action 坐标
- 跨实例共享 (同一台 PC 一个 db)
- 简化置信度账本：连续失败 ≥5 次且 fail > succ → 该记忆失效
- v2 后做：BK-tree 索引 / L2 embedding / 服务端 Memory

数据库默认 user dir / memory.db, 跟 popup_rules 同位置.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional, Tuple

import numpy as np

from .adb_lite import phash, phash_distance
from .recognizer import Hit, Tier

logger = logging.getLogger(__name__)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS frame_action (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_name TEXT NOT NULL,
    phash TEXT NOT NULL,
    action_x INTEGER NOT NULL,
    action_y INTEGER NOT NULL,
    action_w INTEGER DEFAULT 0,
    action_h INTEGER DEFAULT 0,
    hit_count INTEGER DEFAULT 1,
    success_count INTEGER DEFAULT 1,
    fail_count INTEGER DEFAULT 0,
    last_seen_ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_target ON frame_action(target_name);
"""


class FrameMemory:
    """L1 Memory: phash + SQLite 复读机."""

    def __init__(self, db_path: str | Path):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._db.executescript(_SCHEMA)
        self._db.commit()
        logger.info(f"[memory_l1] db: {self._db_path}")

    def query(
        self,
        frame: np.ndarray,
        target_name: str,
        max_dist: int = 5,
    ) -> Optional[Hit]:
        """phash 距离 ≤ max_dist 即命中. 返回置信度最高的(距离最近的)那条."""
        try:
            cur_phash = phash(frame)
        except Exception as e:
            logger.warning(f"[memory_l1] phash failed: {e}")
            return None

        with self._lock:
            best = None
            best_dist = max_dist + 1
            for row in self._db.execute(
                "SELECT phash, action_x, action_y, action_w, action_h, "
                "       hit_count, success_count, fail_count "
                "FROM frame_action WHERE target_name = ?",
                (target_name,),
            ):
                stored_phash_str, x, y, w, h, hit_cnt, succ, fail = row
                dist = phash_distance(cur_phash, int(stored_phash_str))
                if dist <= max_dist and dist < best_dist:
                    best_dist = dist
                    best = (x, y, w, h, hit_cnt, succ, fail)

            if best is None:
                return None

            x, y, w, h, hit_cnt, succ, fail = best
            # 置信度账本：失败 ≥5 次且失败多于成功 → 这记忆已失效
            if fail >= 5 and fail > succ:
                return None

            conf = max(0.5, 1.0 - best_dist / (max_dist + 1))
            return Hit(
                tier=Tier.MEMORY,
                label=target_name,
                confidence=conf,
                cx=int(x),
                cy=int(y),
                w=int(w),
                h=int(h),
                note=f"dist={best_dist} hits={hit_cnt} s/f={succ}/{fail}",
            )

    def remember(
        self,
        frame: np.ndarray,
        target_name: str,
        action_xy: Tuple[int, int],
        size_wh: Tuple[int, int] = (0, 0),
        success: bool = True,
    ) -> None:
        """记录一次 action. 同坐标 + phash 接近 (距离 < 3) 视为更新, 否则新增."""
        try:
            cur_phash = phash(frame)
        except Exception:
            return

        x, y = action_xy
        w, h = size_wh
        ts = int(time.time())

        with self._lock:
            existing_id = None
            for row in self._db.execute(
                "SELECT id, phash FROM frame_action "
                "WHERE target_name = ? AND ABS(action_x - ?) < 30 AND ABS(action_y - ?) < 30",
                (target_name, x, y),
            ):
                rid, stored_phash_str = row
                if phash_distance(cur_phash, int(stored_phash_str)) < 3:
                    existing_id = rid
                    break

            if existing_id is not None:
                if success:
                    self._db.execute(
                        "UPDATE frame_action SET "
                        "hit_count = hit_count + 1, "
                        "success_count = success_count + 1, "
                        "last_seen_ts = ? WHERE id = ?",
                        (ts, existing_id),
                    )
                else:
                    self._db.execute(
                        "UPDATE frame_action SET "
                        "fail_count = fail_count + 1, "
                        "last_seen_ts = ? WHERE id = ?",
                        (ts, existing_id),
                    )
            else:
                self._db.execute(
                    "INSERT INTO frame_action "
                    "(target_name, phash, action_x, action_y, action_w, action_h, "
                    " hit_count, success_count, fail_count, last_seen_ts) "
                    "VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
                    (
                        target_name,
                        str(cur_phash),
                        x,
                        y,
                        w,
                        h,
                        1 if success else 0,
                        0 if success else 1,
                        ts,
                    ),
                )
            self._db.commit()

    def stats(self) -> dict:
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*), COALESCE(SUM(hit_count),0), "
                "COALESCE(SUM(success_count),0), COALESCE(SUM(fail_count),0) "
                "FROM frame_action"
            ).fetchone()
            return {
                "rows": row[0] or 0,
                "hits": row[1] or 0,
                "succ": row[2] or 0,
                "fail": row[3] or 0,
            }

    def close(self) -> None:
        with self._lock:
            try:
                self._db.close()
            except Exception:
                pass

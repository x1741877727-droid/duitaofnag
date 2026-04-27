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
    last_seen_ts INTEGER NOT NULL,
    snapshot_path TEXT DEFAULT '',
    note TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_target ON frame_action(target_name);
"""

_MIGRATIONS = [
    "ALTER TABLE frame_action ADD COLUMN snapshot_path TEXT DEFAULT ''",
    "ALTER TABLE frame_action ADD COLUMN note TEXT DEFAULT ''",
]


class FrameMemory:
    """L1 Memory: phash + SQLite 复读机."""

    def __init__(self, db_path: str | Path):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._db.executescript(_SCHEMA)
        # 兼容旧 db: ALTER 加列 (重复加忽略)
        for m in _MIGRATIONS:
            try:
                self._db.execute(m)
            except sqlite3.OperationalError:
                pass
        self._db.commit()
        # 帧快照目录
        self._snap_dir = self._db_path.parent / "snapshots"
        self._snap_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"[memory_l1] db: {self._db_path}, snapshots: {self._snap_dir}")

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

        # 预先准备 snapshot 写入的 helper
        def _save_snapshot() -> str:
            """落盘当前帧 + 红圈点击位置, 返回相对文件名 (失败返 '')."""
            try:
                import cv2
                annot = frame.copy()
                cv2.circle(annot, (int(x), int(y)), 36, (0, 0, 255), 3)
                cv2.circle(annot, (int(x), int(y)), 6, (0, 0, 255), -1)
                fname = f"{target_name}_{int(time.time() * 1000)}.jpg"
                fpath = self._snap_dir / fname
                if cv2.imwrite(str(fpath), annot, [cv2.IMWRITE_JPEG_QUALITY, 70]):
                    return fname
            except Exception as e:
                logger.debug(f"[memory_l1] snapshot save err: {e}")
            return ""

        with self._lock:
            existing_id = None
            existing_snap = ""
            for row in self._db.execute(
                "SELECT id, phash, snapshot_path FROM frame_action "
                "WHERE target_name = ? AND ABS(action_x - ?) < 30 AND ABS(action_y - ?) < 30",
                (target_name, x, y),
            ):
                rid, stored_phash_str, snap_p = row
                if phash_distance(cur_phash, int(stored_phash_str)) < 3:
                    existing_id = rid
                    existing_snap = snap_p or ""
                    break

            if existing_id is not None:
                # 旧记录无 snapshot → 趁此次写一份 (保证"所有记忆都有快照")
                if success and not existing_snap:
                    new_snap = _save_snapshot()
                    if new_snap:
                        self._db.execute(
                            "UPDATE frame_action SET snapshot_path = ? WHERE id = ?",
                            (new_snap, existing_id),
                        )
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
                # 没有已存记录:
                #   success=True → 新建 (学到一条新成功)
                #   success=False → *不新建* (避免坐标污染 -- 这次失败的坐标
                #     下次同 phash 还会被 query 当成"历史成功" 复用)
                if not success:
                    return
                snap_rel = _save_snapshot()
                self._db.execute(
                    "INSERT INTO frame_action "
                    "(target_name, phash, action_x, action_y, action_w, action_h, "
                    " hit_count, success_count, fail_count, last_seen_ts, snapshot_path) "
                    "VALUES (?, ?, ?, ?, ?, ?, 1, 1, 0, ?, ?)",
                    (target_name, str(cur_phash), x, y, w, h, ts, snap_rel),
                )
            self._db.commit()

    def list_all(self, target: str = "", limit: int = 500) -> list[dict]:
        """列出全部记录 (供前端记忆库浏览). target 空 = 全部 target."""
        with self._lock:
            sql = (
                "SELECT id, target_name, phash, action_x, action_y, action_w, action_h, "
                "       hit_count, success_count, fail_count, last_seen_ts, snapshot_path, note "
                "FROM frame_action"
            )
            params: tuple = ()
            if target:
                sql += " WHERE target_name = ?"
                params = (target,)
            sql += " ORDER BY last_seen_ts DESC LIMIT ?"
            params = params + (int(limit),)
            rows = list(self._db.execute(sql, params))
        out = []
        for r in rows:
            (rid, tgt, ph, x, y, w, h, hits, succ, fail,
             ts, snap, note) = r
            total = (succ or 0) + (fail or 0)
            rate = (succ / total) if total > 0 else 0.0
            out.append({
                "id": int(rid),
                "target_name": tgt,
                "phash": str(ph),
                "action_x": int(x), "action_y": int(y),
                "action_w": int(w), "action_h": int(h),
                "hit_count": int(hits or 0),
                "success_count": int(succ or 0),
                "fail_count": int(fail or 0),
                "last_seen_ts": int(ts or 0),
                "snapshot_path": snap or "",
                "note": note or "",
                "success_rate": round(rate, 3),
            })
        return out

    def get_by_id(self, rid: int) -> Optional[dict]:
        with self._lock:
            row = self._db.execute(
                "SELECT id, target_name, phash, action_x, action_y, action_w, action_h, "
                "       hit_count, success_count, fail_count, last_seen_ts, snapshot_path, note "
                "FROM frame_action WHERE id = ?",
                (int(rid),),
            ).fetchone()
        if not row:
            return None
        (rid_, tgt, ph, x, y, w, h, hits, succ, fail,
         ts, snap, note) = row
        return {
            "id": int(rid_), "target_name": tgt, "phash": str(ph),
            "action_x": int(x), "action_y": int(y),
            "action_w": int(w), "action_h": int(h),
            "hit_count": int(hits or 0), "success_count": int(succ or 0),
            "fail_count": int(fail or 0), "last_seen_ts": int(ts or 0),
            "snapshot_path": snap or "", "note": note or "",
        }

    def snapshot_path(self, rid: int) -> Optional[Path]:
        rec = self.get_by_id(rid)
        if rec is None or not rec.get("snapshot_path"):
            return None
        p = self._snap_dir / rec["snapshot_path"]
        return p if p.is_file() else None

    def delete_by_id(self, rid: int) -> bool:
        with self._lock:
            cur = self._db.execute(
                "SELECT snapshot_path FROM frame_action WHERE id = ?",
                (int(rid),),
            ).fetchone()
            if not cur:
                return False
            self._db.execute("DELETE FROM frame_action WHERE id = ?", (int(rid),))
            self._db.commit()
            # 顺手删 snapshot
            snap = cur[0]
            if snap:
                try:
                    (self._snap_dir / snap).unlink()
                except Exception:
                    pass
        return True

    def mark_fail(self, rid: int) -> Optional[dict]:
        """+1 失败计数. 用户在前端按 '点错了' 时调."""
        with self._lock:
            self._db.execute(
                "UPDATE frame_action SET fail_count = fail_count + 1, "
                "last_seen_ts = ? WHERE id = ?",
                (int(time.time()), int(rid)),
            )
            self._db.commit()
        return self.get_by_id(rid)

    def find_similar(self, rid: int, max_dist: int = 5) -> list[dict]:
        """phash 距离 ≤ max_dist 的其他条目 (帮你找冗余 / 误标)."""
        rec = self.get_by_id(rid)
        if not rec:
            return []
        try:
            ref_ph = int(rec["phash"])
        except Exception:
            return []
        out = []
        with self._lock:
            for row in self._db.execute(
                "SELECT id, phash, action_x, action_y, hit_count, success_count, fail_count "
                "FROM frame_action WHERE id != ? AND target_name = ?",
                (int(rid), rec["target_name"]),
            ):
                rid2, ph2, x2, y2, hits, succ, fail = row
                try:
                    d = phash_distance(ref_ph, int(ph2))
                except Exception:
                    continue
                if d <= max_dist:
                    out.append({
                        "id": int(rid2),
                        "phash_dist": d,
                        "action_xy": [int(x2), int(y2)],
                        "hits": int(hits or 0),
                        "succ": int(succ or 0),
                        "fail": int(fail or 0),
                    })
        return sorted(out, key=lambda r: r["phash_dist"])

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

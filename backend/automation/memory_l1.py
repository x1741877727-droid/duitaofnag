"""
L1 Frame Memory v2 — phash + 多维 hash + BK-tree + LRU + 蓄水池写策略 + 滑动窗口置信度

工业化路线:
  Tier 1.1 滑动窗口置信度 + TTL 自动归档    — 老记忆自动失效, 不再永久污染查询
  Tier 1.2 蓄水池写策略                      — 同 phash 累计 N 次成功才落库, 防止单次误击成为长期记忆
  Tier 1.3 后台去重 (dedup)                  — phash 距离 < 2 + 坐标差 < 10px 合并
  Tier 2.4 BK-tree 内存索引                  — phash 近邻搜索 O(log N)
  Tier 2.5 LRU 热缓存                        — 最近命中直接返回, 不查库
  Tier 3.6 多 hash 联合 (phash + dhash + 4-quadrant dhash) — 抗误命中
  Tier 3.7 embedding 字段 (BLOB)            — 已留接口, 模型 plug 后启用 (默认空)

API 兼容:
  query(frame, target_name, max_dist) -> Optional[Hit]
  remember(frame, target_name, action_xy, size_wh, success) -> None
"""

from __future__ import annotations

import json
import logging
import sqlite3
import struct
import threading
import time
from collections import OrderedDict, defaultdict
from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np

from .adb_lite import phash, phash_distance
from .recognizer import Hit, Tier

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────
# 配置常量 (实测调优后再改)
# ────────────────────────────────────────────────────────────────────
PENDING_CONFIRMATION_COUNT = 3       # 蓄水池: 同 phash 累计 N 次 success 才落库
PENDING_PHASH_TOL = 3                # 蓄水池中聚合时 phash 距离容差
PENDING_XY_TOL = 30                  # 蓄水池中聚合时坐标容差
PENDING_TTL_S = 300                  # 蓄水池条目最大留存时间 (5min 没确认就丢)

HISTORY_WINDOW = 20                  # 滑动窗口: 最近 N 次 attempt
CONFIDENCE_THRESHOLD = 0.5           # 滑动窗口胜率低于此值认为记忆失效
ARCHIVE_TTL_DAYS = 30                # N 天没用 → archived

LRU_CAPACITY = 64                    # 每 target 热缓存条数
LRU_DIST_TOL = 2                     # LRU key 用 phash, 容差 ≤ 2 视为命中

DHASH_DIST_THRESHOLD = 12            # dhash 距离阈值 (跟 phash dist 5 大致同档次, 64-bit)
QHASH_DIST_THRESHOLD = 8             # 单象限 hash 距离阈值
QHASH_AGREE_MIN = 3                  # 4 象限至少 3 个匹配才算 multi-hash 通过


# ────────────────────────────────────────────────────────────────────
# Multi-hash 工具 (Tier 3.6)
# ────────────────────────────────────────────────────────────────────

def _dhash(img: np.ndarray) -> int:
    """Difference hash 64-bit. 缩到 9x8 灰度, 对每行相邻像素比大小. 跟 phash 互补.
    DCT-phash 看整体亮度分布, dhash 看边缘走向, 两个一起更鲁棒."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    small = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
    h = 0
    for r in range(8):
        for c in range(8):
            if small[r, c] > small[r, c + 1]:
                h |= 1 << (r * 8 + c)
    return h


def _quadrant_dhashes(img: np.ndarray) -> Tuple[int, int, int, int]:
    """把图等分 4 象限, 各算一个 dhash. 局部细节微变 → 1-2 个象限 hash 变, 全图 phash 可能不变.
    多帧相同弹窗带不同红点 → phash 距离接近, 但红点所在象限 dhash 距离会跳."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    h, w = gray.shape
    mid_y, mid_x = h // 2, w // 2
    quads = [
        gray[:mid_y, :mid_x],   # Q0 左上
        gray[:mid_y, mid_x:],   # Q1 右上
        gray[mid_y:, :mid_x],   # Q2 左下
        gray[mid_y:, mid_x:],   # Q3 右下
    ]
    return tuple(_dhash(q) for q in quads)  # type: ignore


def _hamming(a: int, b: int) -> int:
    return bin(int(a) ^ int(b)).count("1")


# ────────────────────────────────────────────────────────────────────
# BK-tree (Tier 2.4) — 近邻搜索, 跟 phash hamming 距离配套
# https://en.wikipedia.org/wiki/BK-tree
# ────────────────────────────────────────────────────────────────────

class _BKNode:
    __slots__ = ("hash_val", "payloads", "children")

    def __init__(self, hash_val: int, payload):
        self.hash_val = hash_val
        self.payloads = [payload]
        self.children: dict = {}    # dist -> _BKNode

    def add(self, hash_val: int, payload):
        d = _hamming(self.hash_val, hash_val)
        if d == 0:
            # 同 hash 不同 payload (e.g. 不同 row id)
            self.payloads.append(payload)
            return
        if d in self.children:
            self.children[d].add(hash_val, payload)
        else:
            self.children[d] = _BKNode(hash_val, payload)

    def remove_payload(self, payload) -> bool:
        """返回 True 如果 self 还有 payload, False 如果 self 节点应被删 (无 payload 留)."""
        if payload in self.payloads:
            self.payloads.remove(payload)
        return bool(self.payloads) or bool(self.children)


class BKTree:
    def __init__(self):
        self.root: Optional[_BKNode] = None
        self._lock = threading.Lock()

    def add(self, hash_val: int, payload):
        with self._lock:
            if self.root is None:
                self.root = _BKNode(hash_val, payload)
            else:
                self.root.add(hash_val, payload)

    def find(self, query_hash: int, max_dist: int) -> list:
        """返回 [(dist, payload), ...] 排序后."""
        if self.root is None:
            return []
        out: list = []
        stack = [self.root]
        with self._lock:
            while stack:
                node = stack.pop()
                d = _hamming(node.hash_val, query_hash)
                if d <= max_dist:
                    for p in node.payloads:
                        out.append((d, p))
                lo, hi = max(0, d - max_dist), d + max_dist
                for cd, child in node.children.items():
                    if lo <= cd <= hi:
                        stack.append(child)
        out.sort(key=lambda x: x[0])
        return out

    def remove_payload(self, payload):
        """O(n) 全树扫. 不频繁调, 仅 dedup/delete 时用."""
        with self._lock:
            if self.root is None:
                return
            stack = [(None, None, self.root)]
            while stack:
                parent, branch_d, node = stack.pop()
                if payload in node.payloads:
                    node.payloads.remove(payload)
                    # 节点空了无视, 留空节点 OK (BK-tree 容忍空节点占位)
                for cd, child in list(node.children.items()):
                    stack.append((node, cd, child))


# ────────────────────────────────────────────────────────────────────
# LRU 热缓存 (Tier 2.5)
# ────────────────────────────────────────────────────────────────────

class LRUCache:
    """key=phash (近似命中容差 ≤ LRU_DIST_TOL), value=(record_id, tap_xy, conf)."""

    def __init__(self, capacity: int = LRU_CAPACITY):
        self.capacity = capacity
        self.data: "OrderedDict[int, tuple]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, query_hash: int, dist_tol: int = LRU_DIST_TOL):
        with self._lock:
            for k in list(self.data.keys()):
                if _hamming(k, query_hash) <= dist_tol:
                    v = self.data.pop(k)
                    self.data[k] = v   # bump to MRU
                    return v
            return None

    def put(self, hash_val: int, value):
        with self._lock:
            if hash_val in self.data:
                self.data.pop(hash_val)
            self.data[hash_val] = value
            if len(self.data) > self.capacity:
                self.data.popitem(last=False)

    def invalidate_all(self):
        with self._lock:
            self.data.clear()


# ────────────────────────────────────────────────────────────────────
# Schema + Migrations
# ────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS frame_action (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_name TEXT NOT NULL,
    phash TEXT NOT NULL,
    dhash TEXT DEFAULT '',
    qhash_0 TEXT DEFAULT '',
    qhash_1 TEXT DEFAULT '',
    qhash_2 TEXT DEFAULT '',
    qhash_3 TEXT DEFAULT '',
    embedding BLOB,
    action_x INTEGER NOT NULL,
    action_y INTEGER NOT NULL,
    action_w INTEGER DEFAULT 0,
    action_h INTEGER DEFAULT 0,
    hit_count INTEGER DEFAULT 1,
    success_count INTEGER DEFAULT 1,
    fail_count INTEGER DEFAULT 0,
    history_json TEXT DEFAULT '[]',
    last_seen_ts INTEGER NOT NULL,
    archived INTEGER DEFAULT 0,
    snapshot_path TEXT DEFAULT '',
    note TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_target ON frame_action(target_name);
CREATE INDEX IF NOT EXISTS idx_archived ON frame_action(archived);
"""

_MIGRATIONS = [
    "ALTER TABLE frame_action ADD COLUMN snapshot_path TEXT DEFAULT ''",
    "ALTER TABLE frame_action ADD COLUMN note TEXT DEFAULT ''",
    "ALTER TABLE frame_action ADD COLUMN dhash TEXT DEFAULT ''",
    "ALTER TABLE frame_action ADD COLUMN qhash_0 TEXT DEFAULT ''",
    "ALTER TABLE frame_action ADD COLUMN qhash_1 TEXT DEFAULT ''",
    "ALTER TABLE frame_action ADD COLUMN qhash_2 TEXT DEFAULT ''",
    "ALTER TABLE frame_action ADD COLUMN qhash_3 TEXT DEFAULT ''",
    "ALTER TABLE frame_action ADD COLUMN embedding BLOB",
    "ALTER TABLE frame_action ADD COLUMN history_json TEXT DEFAULT '[]'",
    "ALTER TABLE frame_action ADD COLUMN archived INTEGER DEFAULT 0",
]


# ────────────────────────────────────────────────────────────────────
# 主类
# ────────────────────────────────────────────────────────────────────

class FrameMemory:
    """L1 Memory v2: phash + 多 hash + BK-tree + LRU + 蓄水池.

    - query(): 4-stage filter (LRU → BK-tree → multi-hash verify → confidence filter)
    - remember(): pending buffer; 同 phash 累计 N 次 success 才落库
    - 后台 archive_old() / dedup() 可定期跑
    """

    def __init__(self, db_path: str | Path):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._db.executescript(_SCHEMA)
        for m in _MIGRATIONS:
            try:
                self._db.execute(m)
            except sqlite3.OperationalError:
                pass
        self._db.commit()

        self._snap_dir = self._db_path.parent / "snapshots"
        self._snap_dir.mkdir(parents=True, exist_ok=True)

        # Tier 2.4: BK-tree per target_name
        self._bktree: dict[str, BKTree] = defaultdict(BKTree)
        # Tier 2.5: LRU per target_name
        self._lru: dict[str, LRUCache] = defaultdict(lambda: LRUCache(LRU_CAPACITY))
        # Tier 1.2: pending buffer (in-memory)
        # key=(target, phash 桶 ≈ phash // 2^small) → list[(phash, x, y, ts)]
        self._pending: dict[str, list] = defaultdict(list)

        # 启动时把现有 (非 archived) 记录加进 BK-tree
        self._rebuild_bktrees()
        logger.info(f"[memory_l1 v2] db={self._db_path}, BKTree 索引建好 ({sum(len(t.find(0,64) or []) for t in self._bktree.values())} payload)")

    # ──────── 内部辅助 ────────

    def _rebuild_bktrees(self):
        """启动时 / archive 后调. 重建所有 target 的 BKTree."""
        with self._lock:
            self._bktree.clear()
            for row in self._db.execute(
                "SELECT id, target_name, phash FROM frame_action WHERE archived = 0"
            ):
                rid, tgt, phs = row
                try:
                    self._bktree[tgt].add(int(phs), int(rid))
                except Exception as e:
                    logger.debug(f"[memory_l1] bk add err id={rid}: {e}")

    def _compute_all_hashes(self, frame: np.ndarray) -> tuple:
        """返回 (phash, dhash, qhash0, qhash1, qhash2, qhash3). 失败任一字段返 0."""
        try:
            ph = phash(frame)
        except Exception:
            ph = 0
        try:
            dh = _dhash(frame)
        except Exception:
            dh = 0
        try:
            q0, q1, q2, q3 = _quadrant_dhashes(frame)
        except Exception:
            q0 = q1 = q2 = q3 = 0
        return ph, dh, q0, q1, q2, q3

    @staticmethod
    def _multi_hash_verify(cur, stored, phash_dist) -> Tuple[bool, str]:
        """双重核验: phash 已通过, 再看 dhash + 4 象限有几个达标.
        cur, stored: (phash, dhash, q0, q1, q2, q3) 元组.
        返回 (是否通过, 解释字符串)."""
        cph, cdh, cq0, cq1, cq2, cq3 = cur
        sph, sdh, sq0, sq1, sq2, sq3 = stored
        # 旧记录 dhash/qhash 全 0 → legacy mode, 仅 phash 判定
        if sdh == 0 and sq0 == 0 and sq1 == 0 and sq2 == 0 and sq3 == 0:
            return True, f"legacy(phash={phash_dist})"
        # dhash 距离
        d_dh = _hamming(cdh, sdh)
        if d_dh > DHASH_DIST_THRESHOLD:
            return False, f"dhash={d_dh}>{DHASH_DIST_THRESHOLD}"
        # 象限投票
        agree = sum(
            1 for c, s in [(cq0, sq0), (cq1, sq1), (cq2, sq2), (cq3, sq3)]
            if _hamming(c, s) <= QHASH_DIST_THRESHOLD
        )
        if agree < QHASH_AGREE_MIN:
            return False, f"qhash agree={agree}<{QHASH_AGREE_MIN}"
        return True, f"phash={phash_dist} dhash={d_dh} qhash_agree={agree}/4"

    def _confidence_from_history(self, history_json: str, fallback_succ: int, fallback_fail: int) -> float:
        """滑动窗口胜率. history_json 空 → 用 fallback (老数据)."""
        try:
            history = json.loads(history_json or "[]")
        except Exception:
            history = []
        if not history:
            tot = fallback_succ + fallback_fail
            return (fallback_succ / tot) if tot > 0 else 1.0
        # 取最近 N 次, 加时间衰减权重 (越近权重越高)
        recent = history[-HISTORY_WINDOW:]
        now = time.time()
        weighted_succ = 0.0
        weighted_total = 0.0
        for entry in recent:
            ts = entry.get("ts", 0)
            ok = 1 if entry.get("ok") else 0
            age_days = max(0, (now - ts) / 86400)
            w = 1.0 / (1.0 + age_days * 0.1)   # 半衰期约 10 天
            weighted_succ += ok * w
            weighted_total += w
        return (weighted_succ / weighted_total) if weighted_total > 0 else 0.0

    @staticmethod
    def _append_history(history_json: str, ok: bool) -> str:
        try:
            history = json.loads(history_json or "[]")
        except Exception:
            history = []
        history.append({"ts": int(time.time()), "ok": bool(ok)})
        if len(history) > HISTORY_WINDOW * 2:
            history = history[-HISTORY_WINDOW:]
        return json.dumps(history, separators=(",", ":"))

    # ──────── 蓄水池 (Tier 1.2) ────────

    def _pending_add(self, target: str, phs: int, x: int, y: int) -> Optional[dict]:
        """累计同 phash + 同坐标的 success. 累计 ≥ N → 返回 commit 信号 (含次数)."""
        now = time.time()
        bucket = self._pending[target]
        # 清掉过期 entry
        bucket[:] = [e for e in bucket if now - e["ts_first"] < PENDING_TTL_S]
        # 找接近 entry
        for e in bucket:
            if (_hamming(e["phash"], phs) <= PENDING_PHASH_TOL
                    and abs(e["x"] - x) < PENDING_XY_TOL
                    and abs(e["y"] - y) < PENDING_XY_TOL):
                e["count"] += 1
                e["x"] = (e["x"] + x) // 2     # 平均化抗噪
                e["y"] = (e["y"] + y) // 2
                if e["count"] >= PENDING_CONFIRMATION_COUNT:
                    bucket.remove(e)
                    return {"phash": phs, "x": x, "y": y, "count": e["count"]}
                return None
        # 新建
        bucket.append({
            "phash": phs, "x": x, "y": y, "count": 1, "ts_first": now,
        })
        return None

    # ──────── 公开 API ────────

    def query(
        self,
        frame: np.ndarray,
        target_name: str,
        max_dist: int = 5,
    ) -> Optional[Hit]:
        """4 阶段过滤: LRU → BKTree → multi-hash verify → 置信度.
        返回最佳 Hit 或 None."""
        if frame is None or frame.size == 0:
            return None
        try:
            cur_hashes = self._compute_all_hashes(frame)
        except Exception as e:
            logger.warning(f"[memory_l1] hash 计算失败: {e}")
            return None
        cur_phash = cur_hashes[0]

        # ---- Stage 1: LRU 热缓存 ----
        cached = self._lru[target_name].get(cur_phash, dist_tol=LRU_DIST_TOL)
        if cached is not None:
            rid, x, y, conf = cached
            return Hit(
                tier=Tier.MEMORY, label=target_name, confidence=conf,
                cx=x, cy=y, w=0, h=0,
                note=f"LRU hit id={rid}",
            )

        # ---- Stage 2: BKTree 候选 ----
        bk = self._bktree.get(target_name)
        if bk is None or bk.root is None:
            return None
        candidates = bk.find(cur_phash, max_dist)   # [(dist, rid), ...]
        if not candidates:
            return None

        # ---- Stage 3: 拉数据 + multi-hash verify + 置信度 ----
        ids = [str(rid) for _, rid in candidates[:20]]   # 限前 20 候选
        in_clause = ",".join(["?"] * len(ids))
        with self._lock:
            rows = list(self._db.execute(
                f"SELECT id, phash, dhash, qhash_0, qhash_1, qhash_2, qhash_3, "
                f"       action_x, action_y, action_w, action_h, "
                f"       success_count, fail_count, history_json, archived, last_seen_ts "
                f"FROM frame_action WHERE id IN ({in_clause})",
                ids,
            ))
        # rid → row 字典
        by_id = {r[0]: r for r in rows}

        best = None       # (conf_score, rid, x, y, w, h, note)
        for dist, rid in candidates:
            row = by_id.get(rid)
            if row is None:
                continue
            (_, ph_s, dh_s, q0_s, q1_s, q2_s, q3_s,
             x, y, w, h, succ, fail, hist_json, archived, last_ts) = row

            if archived:
                continue
            # multi-hash 二重验证
            stored_hashes = (
                int(ph_s) if ph_s else 0,
                int(dh_s) if dh_s else 0,
                int(q0_s) if q0_s else 0,
                int(q1_s) if q1_s else 0,
                int(q2_s) if q2_s else 0,
                int(q3_s) if q3_s else 0,
            )
            ok, reason = self._multi_hash_verify(cur_hashes, stored_hashes, dist)
            if not ok:
                continue
            # 滑动窗口置信度
            conf = self._confidence_from_history(hist_json or "[]", succ or 0, fail or 0)
            if conf < CONFIDENCE_THRESHOLD:
                continue
            # 综合得分: phash 距离越近 + 置信度越高 越好
            # 简单线性: score = (max_dist - dist) / max_dist * 0.5 + conf * 0.5
            score = (1.0 - dist / max(1, max_dist)) * 0.5 + conf * 0.5
            note = f"id={rid} dist={dist} {reason} conf={conf:.2f}"
            if best is None or score > best[0]:
                best = (score, rid, x, y, w, h, note)

        if best is None:
            return None

        score, rid, x, y, w, h, note = best
        # 写入 LRU (用当前 phash 而不是 stored, 下次相似 phash 来直接命中)
        self._lru[target_name].put(cur_phash, (rid, int(x), int(y), float(score)))
        return Hit(
            tier=Tier.MEMORY, label=target_name, confidence=float(score),
            cx=int(x), cy=int(y), w=int(w), h=int(h),
            note=note,
        )

    def remember(
        self,
        frame: np.ndarray,
        target_name: str,
        action_xy: Tuple[int, int],
        size_wh: Tuple[int, int] = (0, 0),
        success: bool = True,
    ) -> None:
        """记录一次 action.
        - success=True: 走蓄水池, 同 phash + 同坐标累计 ≥ N 次才落库
        - success=False: 直接 UPDATE 同 phash 现有记录的 history (累计失败), 不新增
        """
        if frame is None or frame.size == 0:
            return
        try:
            ph, dh, q0, q1, q2, q3 = self._compute_all_hashes(frame)
        except Exception:
            return
        x, y = action_xy
        w, h = size_wh
        ts = int(time.time())

        with self._lock:
            # 找 phash 距离 < 3 + 坐标接近的现有记录 (老逻辑保持)
            existing = None
            for row in self._db.execute(
                "SELECT id, phash, snapshot_path, history_json, archived "
                "FROM frame_action "
                "WHERE target_name = ? AND ABS(action_x - ?) < ? AND ABS(action_y - ?) < ?",
                (target_name, x, PENDING_XY_TOL, y, PENDING_XY_TOL),
            ):
                rid, ph_s, snap, hist_j, arch = row
                if _hamming(int(ph_s), ph) < 3:
                    existing = (rid, snap or "", hist_j or "[]", arch)
                    break

            if existing is not None:
                rid, existing_snap, hist_j, arch = existing
                new_history = self._append_history(hist_j, success)
                if success:
                    # 旧记录无快照 → 趁机补
                    new_snap = self._save_snapshot(frame, target_name, x, y) if not existing_snap else existing_snap
                    self._db.execute(
                        "UPDATE frame_action SET hit_count=hit_count+1, "
                        "success_count=success_count+1, last_seen_ts=?, "
                        "history_json=?, archived=0, snapshot_path=? "
                        "WHERE id=?",
                        (ts, new_history, new_snap, rid),
                    )
                else:
                    self._db.execute(
                        "UPDATE frame_action SET fail_count=fail_count+1, "
                        "last_seen_ts=?, history_json=? WHERE id=?",
                        (ts, new_history, rid),
                    )
                self._db.commit()
                # 标的记忆改了, 让 LRU 失效
                self._lru[target_name].invalidate_all()
                return

            # 没有 existing → success=False 不落库 (老逻辑); success=True 走蓄水池
            if not success:
                return
            commit = self._pending_add(target_name, ph, x, y)
            if commit is None:
                logger.debug(
                    f"[memory_l1] pending {target_name}@({x},{y}) phash={ph:#x} "
                    f"累计中, 待 ≥{PENDING_CONFIRMATION_COUNT} 次才落库"
                )
                return

            # 蓄水池满了 → 真落库
            snap_rel = self._save_snapshot(frame, target_name, x, y)
            history_init = self._append_history("[]", True)
            cursor = self._db.execute(
                "INSERT INTO frame_action "
                "(target_name, phash, dhash, qhash_0, qhash_1, qhash_2, qhash_3, "
                " action_x, action_y, action_w, action_h, "
                " hit_count, success_count, fail_count, history_json, "
                " last_seen_ts, snapshot_path) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "        ?, ?, 0, ?, ?, ?)",
                (target_name, str(ph), str(dh), str(q0), str(q1), str(q2), str(q3),
                 commit["x"], commit["y"], w, h,
                 commit["count"], commit["count"], history_init, ts, snap_rel),
            )
            new_id = cursor.lastrowid
            self._db.commit()
            # 加进 BK-tree
            self._bktree[target_name].add(ph, int(new_id))
            # 失效 LRU (新记录可能更优)
            self._lru[target_name].invalidate_all()
            logger.info(
                f"[memory_l1] 蓄水池 commit: id={new_id} target={target_name} "
                f"@({commit['x']},{commit['y']}) phash={ph:#x} count={commit['count']}"
            )

    def _save_snapshot(self, frame: np.ndarray, target: str, x: int, y: int) -> str:
        try:
            annot = frame.copy()
            cv2.circle(annot, (int(x), int(y)), 36, (0, 0, 255), 3)
            cv2.circle(annot, (int(x), int(y)), 6, (0, 0, 255), -1)
            fname = f"{target}_{int(time.time() * 1000)}.jpg"
            fpath = self._snap_dir / fname
            if cv2.imwrite(str(fpath), annot, [cv2.IMWRITE_JPEG_QUALITY, 70]):
                return fname
        except Exception as e:
            logger.debug(f"[memory_l1] snapshot 落盘失败: {e}")
        return ""

    # ──────── 后台维护 (Tier 1.1 + Tier 1.3) ────────

    def archive_old(self, ttl_days: int = ARCHIVE_TTL_DAYS) -> int:
        """N 天没用过 → archived. 返回 archived 条数. 不删, 仍可查 list_all(include_archived=True)."""
        cutoff = int(time.time()) - ttl_days * 86400
        with self._lock:
            cur = self._db.execute(
                "UPDATE frame_action SET archived=1 WHERE archived=0 AND last_seen_ts < ?",
                (cutoff,),
            )
            n = cur.rowcount
            self._db.commit()
        if n:
            self._rebuild_bktrees()
            self._lru.clear()
            logger.info(f"[memory_l1] archive_old: {n} 条 > {ttl_days} 天未用 → archived")
        return n

    def dedup(self, phash_tol: int = 2, xy_tol: int = 10) -> int:
        """phash 距离 ≤ 2 + 坐标差 < 10px 的合并: 累加 hit/success/fail. 返回合并条数."""
        with self._lock:
            rows = list(self._db.execute(
                "SELECT id, target_name, phash, action_x, action_y, "
                "       hit_count, success_count, fail_count, history_json "
                "FROM frame_action WHERE archived=0 ORDER BY id"
            ))
            merged = 0
            seen: dict = {}   # (target, phash 桶) -> (id, x, y)
            to_delete: set = set()
            for row in rows:
                rid, tgt, ph_s, x, y, hits, succ, fail, hist_j = row
                ph = int(ph_s)
                # 找已 seen 的兼容条目
                hit_existing = None
                for (other_id, ox, oy, ot, oph) in seen.values():
                    if (ot == tgt and _hamming(ph, oph) <= phash_tol
                            and abs(ox - x) < xy_tol and abs(oy - y) < xy_tol):
                        hit_existing = other_id
                        break
                if hit_existing is None:
                    seen[rid] = (rid, x, y, tgt, ph)
                    continue
                # 合并到 hit_existing
                self._db.execute(
                    "UPDATE frame_action SET "
                    "hit_count = hit_count + ?, "
                    "success_count = success_count + ?, "
                    "fail_count = fail_count + ?, "
                    "last_seen_ts = MAX(last_seen_ts, ?) "
                    "WHERE id = ?",
                    (hits, succ, fail, int(time.time()), hit_existing),
                )
                to_delete.add(rid)
                merged += 1

            for rid in to_delete:
                self._db.execute("DELETE FROM frame_action WHERE id=?", (rid,))
            self._db.commit()

        if merged:
            self._rebuild_bktrees()
            self._lru.clear()
            logger.info(f"[memory_l1] dedup: 合并 {merged} 条")
        return merged

    def stats(self, target: str = "") -> dict:
        """运营 dashboard 数据."""
        with self._lock:
            sql = "SELECT COUNT(*), SUM(hit_count), SUM(success_count), SUM(fail_count) FROM frame_action WHERE archived=0"
            params = ()
            if target:
                sql += " AND target_name=?"
                params = (target,)
            row = self._db.execute(sql, params).fetchone()
            n, hits, succ, fail = row or (0, 0, 0, 0)
            arch_row = self._db.execute(
                "SELECT COUNT(*) FROM frame_action WHERE archived=1" + (" AND target_name=?" if target else ""),
                params,
            ).fetchone()
            arch = arch_row[0] if arch_row else 0
        total = (succ or 0) + (fail or 0)
        return {
            "active": int(n or 0),
            "archived": int(arch or 0),
            "total_hits": int(hits or 0),
            "total_attempts": total,
            "global_success_rate": round((succ or 0) / total, 3) if total else 0.0,
            "lru_size": sum(len(c.data) for c in self._lru.values()),
            "pending_size": sum(len(b) for b in self._pending.values()),
            "bktree_targets": len(self._bktree),
        }

    # ──────── 兼容老 API (前端记忆库浏览用) ────────

    def list_all(self, target: str = "", limit: int = 500, include_archived: bool = False) -> list[dict]:
        with self._lock:
            sql = (
                "SELECT id, target_name, phash, action_x, action_y, action_w, action_h, "
                "       hit_count, success_count, fail_count, last_seen_ts, snapshot_path, "
                "       note, history_json, archived "
                "FROM frame_action"
            )
            wheres: list[str] = []
            params: list = []
            if not include_archived:
                wheres.append("archived = 0")
            if target:
                wheres.append("target_name = ?")
                params.append(target)
            if wheres:
                sql += " WHERE " + " AND ".join(wheres)
            sql += " ORDER BY last_seen_ts DESC LIMIT ?"
            params.append(int(limit))
            rows = list(self._db.execute(sql, params))
        out = []
        for r in rows:
            (rid, tgt, ph, x, y, w, h, hits, succ, fail,
             ts, snap, note, hist, arch) = r
            total = (succ or 0) + (fail or 0)
            window_conf = self._confidence_from_history(hist or "[]", succ or 0, fail or 0)
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
                "success_rate": round((succ / total) if total else 0, 3),
                "window_confidence": round(window_conf, 3),
                "archived": bool(arch),
            })
        return out

    def get_by_id(self, rid: int) -> Optional[dict]:
        with self._lock:
            row = self._db.execute(
                "SELECT id, target_name, phash, action_x, action_y, action_w, action_h, "
                "       hit_count, success_count, fail_count, last_seen_ts, snapshot_path, "
                "       note, history_json, archived "
                "FROM frame_action WHERE id = ?",
                (int(rid),),
            ).fetchone()
        if not row:
            return None
        (rid_, tgt, ph, x, y, w, h, hits, succ, fail,
         ts, snap, note, hist, arch) = row
        return {
            "id": int(rid_), "target_name": tgt, "phash": str(ph),
            "action_x": int(x), "action_y": int(y),
            "action_w": int(w), "action_h": int(h),
            "hit_count": int(hits or 0), "success_count": int(succ or 0),
            "fail_count": int(fail or 0), "last_seen_ts": int(ts or 0),
            "snapshot_path": snap or "", "note": note or "",
            "history_json": hist or "[]",
            "archived": bool(arch),
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
                "SELECT snapshot_path, target_name, phash FROM frame_action WHERE id = ?",
                (int(rid),),
            ).fetchone()
            if not cur:
                return False
            self._db.execute("DELETE FROM frame_action WHERE id = ?", (int(rid),))
            self._db.commit()
            snap, tgt, _ = cur
            if snap:
                try:
                    (self._snap_dir / snap).unlink()
                except Exception:
                    pass
            # 从索引摘掉 (BKTree.remove_payload + LRU 全清, 简单粗暴)
            if tgt in self._bktree:
                self._bktree[tgt].remove_payload(int(rid))
            self._lru[tgt].invalidate_all()
        return True

    def mark_fail(self, rid: int) -> Optional[dict]:
        with self._lock:
            row = self._db.execute(
                "SELECT history_json FROM frame_action WHERE id=?", (int(rid),),
            ).fetchone()
            if not row:
                return None
            new_history = self._append_history(row[0] or "[]", False)
            self._db.execute(
                "UPDATE frame_action SET fail_count = fail_count + 1, "
                "history_json=?, last_seen_ts = ? WHERE id = ?",
                (new_history, int(time.time()), int(rid)),
            )
            self._db.commit()
        # mark_fail 后置信度可能掉, LRU 失效
        for cache in self._lru.values():
            cache.invalidate_all()
        return self.get_by_id(rid)

    def find_similar(self, rid: int, max_dist: int = 5) -> list[dict]:
        rec = self.get_by_id(rid)
        if rec is None:
            return []
        try:
            ph = int(rec["phash"])
        except Exception:
            return []
        bk = self._bktree.get(rec["target_name"])
        if bk is None:
            return []
        cands = bk.find(ph, max_dist)
        out = []
        for d, other_id in cands:
            if other_id == rid:
                continue
            other = self.get_by_id(other_id)
            if other:
                other["phash_dist"] = d
                out.append(other)
        return out

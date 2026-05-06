"""Stage 2 — per-instance 持久化状态.

设计: **事件驱动写盘**, 不用定时器. 写盘点:
  1. Phase enter:        load 旧状态 / 不存在则 fresh + save
  2. P5 baseline 建立:    save (known_slot_ids 写 baseline 几个机器号)
  3. P5 verify 成功:      save (known_slot_ids append 真人 player_id)
  4. P5 kick 成功:        save (kicked_ids 加 got_id)
  5. Phase exit:          save (最终状态)

写盘原子: 写到 .tmp → os.replace 改名 (POSIX/Windows 都原子, 防写半截).
读盘失败: 文件损坏 / JSON 解析错 → 视为不存在, 返回 None (caller 走 fresh).

每个 instance 一个文件: %APPDATA%/GameBot/state/instance_{N}.json

未来 Stage 3 闪退恢复 流程:
  recover() → InstanceState.load(idx) → 看 phase 字段 → 决定从哪续
  P5 续: known_slot_ids 直接当 baseline, 当前 slot cx 跟 known 比对找新真人.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from .user_paths import user_state_dir

logger = logging.getLogger(__name__)


@dataclass
class KnownSlot:
    """已知队员的 slot ↔ id 映射. 闪退恢复后用来分辨"谁是新真人"."""
    cx: int
    cy: int
    player_id: Optional[str] = None  # None = baseline 机器号 (没 OCR 过)
    is_baseline: bool = False         # baseline 阶段记录的, 还是 verify 后记录的
    verified_at: float = 0.0          # OCR ID 那一刻的时间戳

    def to_dict(self) -> dict:
        return {"cx": self.cx, "cy": self.cy, "player_id": self.player_id,
                "is_baseline": self.is_baseline, "verified_at": self.verified_at}

    @classmethod
    def from_dict(cls, d: dict) -> "KnownSlot":
        return cls(
            cx=int(d.get("cx", 0)),
            cy=int(d.get("cy", 0)),
            player_id=d.get("player_id"),
            is_baseline=bool(d.get("is_baseline", False)),
            verified_at=float(d.get("verified_at", 0.0)),
        )


@dataclass
class InstanceState:
    """每个 instance 一份持久化状态."""
    instance_idx: int
    phase: str = ""                                    # 当前 phase 名 (P0/P1/P2/P3a/P3b/P4/P5)
    phase_started_at: float = 0.0
    phase_round: int = 0

    # 业务参数
    expected_id: Optional[str] = None                  # P5 等待的目标真人 ID
    role: str = "unknown"                               # captain / member
    squad_id: str = ""                                  # 关联到 squad_state (Stage 4)
    selected_map: Optional[str] = None                  # P4 选了哪张图

    # P5 关键 — 闪退后用这个判断"哪些 slot 是新真人"
    known_slot_ids: list[KnownSlot] = field(default_factory=list)
    kicked_ids: list[str] = field(default_factory=list)  # JSON 不支持 set, 用 list

    # Meta
    schema_version: int = 1
    last_save_ts: float = 0.0

    # ───────────── 序列化 ─────────────

    def to_dict(self) -> dict:
        d = asdict(self)
        # KnownSlot list 用 dict 形式
        d["known_slot_ids"] = [k.to_dict() for k in self.known_slot_ids]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "InstanceState":
        ks_raw = d.pop("known_slot_ids", []) or []
        state = cls(**d)
        state.known_slot_ids = [KnownSlot.from_dict(k) for k in ks_raw]
        return state

    # ───────────── 持久化 ─────────────

    @classmethod
    def file_path(cls, instance_idx: int) -> Path:
        return user_state_dir() / f"instance_{instance_idx}.json"

    @classmethod
    def fresh(cls, instance_idx: int, **kwargs: Any) -> "InstanceState":
        """创建一个全新的状态对象 (不写盘, caller 自己 save)."""
        return cls(instance_idx=instance_idx, **kwargs)

    @classmethod
    def load(cls, instance_idx: int) -> Optional["InstanceState"]:
        """读盘. 文件不存在 / 损坏 / schema 不匹配 → None.

        坏文件不抛异常, 写日志返 None (caller 走 fresh 兜底).
        """
        p = cls.file_path(instance_idx)
        if not p.is_file():
            return None
        try:
            with p.open("r", encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"[state] load instance_{instance_idx} 失败 (文件损坏?): {e}")
            return None
        try:
            return cls.from_dict(d)
        except Exception as e:
            logger.warning(f"[state] instance_{instance_idx} schema 不兼容: {e}")
            return None

    def save_atomic(self) -> None:
        """原子写盘: 写 .tmp → os.replace.

        os.replace 在 Windows / POSIX 都原子, 不会出现"写到一半被读到"的状态.
        """
        self.last_save_ts = time.time()
        p = self.file_path(self.instance_idx)
        tmp = p.with_suffix(p.suffix + ".tmp")
        try:
            tmp.parent.mkdir(parents=True, exist_ok=True)
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
            os.replace(str(tmp), str(p))
        except OSError as e:
            logger.warning(f"[state] save instance_{self.instance_idx} 失败: {e}")

    @classmethod
    def delete(cls, instance_idx: int) -> bool:
        """删 state 文件 (用户 reset / 测试用). 不存在也返 True."""
        p = cls.file_path(instance_idx)
        try:
            if p.is_file():
                p.unlink()
            return True
        except OSError as e:
            logger.warning(f"[state] delete instance_{instance_idx} 失败: {e}")
            return False

    # ───────────── 便利 helpers ─────────────

    def add_baseline_slots(self, positions: list[tuple[int, int]]) -> None:
        """P5 baseline 建立时调用. 只在 known_slot_ids 为空时初始化, 防重复 append."""
        if self.known_slot_ids:
            return  # 已有 baseline, 不覆盖 (闪退恢复后 enter 时也走这里, 不能清掉历史)
        for cx, cy in positions:
            self.known_slot_ids.append(KnownSlot(
                cx=int(cx), cy=int(cy),
                player_id=None, is_baseline=True, verified_at=time.time(),
            ))

    def record_verified_slot(self, cx: int, cy: int, player_id: str) -> None:
        """P5 verify 成功后调用. 同 cx 已有 baseline → 升级为 verified, 否则 append."""
        # NMS 距离 50px 内视为同 slot, 升级 player_id
        for k in self.known_slot_ids:
            if abs(k.cx - cx) < 50 and abs(k.cy - cy) < 50:
                k.player_id = player_id
                k.is_baseline = False
                k.verified_at = time.time()
                return
        self.known_slot_ids.append(KnownSlot(
            cx=int(cx), cy=int(cy),
            player_id=player_id, is_baseline=False, verified_at=time.time(),
        ))

    def record_kick(self, player_id: str) -> None:
        if player_id and player_id not in self.kicked_ids:
            self.kicked_ids.append(player_id)

    def remove_slot_near(self, cx: int, cy: int) -> None:
        """队员退队 (lobby slot 数减少) 时调用. 移除 known_slot_ids 中近这个位置的."""
        self.known_slot_ids = [
            k for k in self.known_slot_ids
            if not (abs(k.cx - cx) < 50 and abs(k.cy - cy) < 50)
        ]

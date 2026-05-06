"""Stage 4 — per-squad 持久化状态 + 跨实例协调.

设计:
  - 一组队伍 (1 队长 + N 队员) 共享一个 squad_state.json 文件
  - 文件路径: %APPDATA%/GameBot/state/squad_{group_id}.json
  - 跟 instance_state 互补: instance_state 是 per-instance 自己的 phase 数据,
    squad_state 是跨实例共享的"队伍" 元数据 (队长心跳 / 队伍码 / valid)

跨实例通信靠**文件 + 时间戳**, 不用 socket / IPC:
  - 队长每 5s 写一次 leader_last_heartbeat
  - 队员定时读, 看 last_heartbeat 距 now > 15s = 队长挂了
  - 队伍码刷新 (队长 P3a 生成新码 → 写文件 + 改 valid=True + bump ts)
    队员读到 valid 变 True 且 ts 比自己上次记录的新 → 重新加入

跟现有 runner_service._team_schemes (in-memory dict) 互补关系:
  _team_schemes / _team_events 是同一进程内 captain → member 通信, 进程重启就丢
  squad_state 是持久化版本, 跨进程重启也能续

读写 race:
  - 心跳 + 队伍码 valid 用 atomic write (.tmp → rename)
  - 单写多读模式 — 队长唯一 writer, 队员只读
  - 队长闪退后, 队员可能短暂看到旧心跳, 15s 超时阈值容忍这个延迟
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from .user_paths import user_state_dir

logger = logging.getLogger(__name__)


# 心跳超时阈值 — 队员认为"队长已挂"的最小静默时长.
# 队长心跳间隔 5s, 阈值 15s 给 3 倍容忍 (网络/磁盘 IO 偶尔慢).
LEADER_HEARTBEAT_TIMEOUT_S = 15.0
LEADER_HEARTBEAT_INTERVAL_S = 5.0


@dataclass
class SquadState:
    """一个队伍的共享状态."""
    group_id: str
    leader_instance: int = -1
    member_instances: list[int] = field(default_factory=list)

    # 队伍码 (team_code 是 P3a 生成的二维码内容, 队员 P3b 用)
    team_code: str = ""
    team_code_valid: bool = False
    team_code_generated_at: float = 0.0

    # 队长心跳 (liveness 信号, 周期写 — 这次"周期"合理因为本质是定时存活证明)
    leader_alive: bool = False
    leader_last_heartbeat: float = 0.0

    # 业务 phase (整队级, 跟 instance phase 不一定同步)
    squad_phase: str = "P0"

    # Meta
    schema_version: int = 1
    last_save_ts: float = 0.0

    # ───────────── 序列化 ─────────────

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SquadState":
        # Schema 演化容错: 过滤未知字段 (磁盘旧/新版本)
        known = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in d.items() if k in known}
        return cls(**filtered)

    # ───────────── 持久化 ─────────────

    @classmethod
    def file_path(cls, group_id: str) -> Path:
        # group_id 可能含特殊字符, 简单清洗
        safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in group_id)
        return user_state_dir() / f"squad_{safe}.json"

    @classmethod
    def fresh(cls, group_id: str, leader_instance: int = -1,
              member_instances: Optional[list] = None) -> "SquadState":
        return cls(
            group_id=group_id,
            leader_instance=leader_instance,
            member_instances=list(member_instances or []),
        )

    @classmethod
    def load(cls, group_id: str) -> Optional["SquadState"]:
        p = cls.file_path(group_id)
        if not p.is_file():
            return None
        try:
            with p.open("r", encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"[squad] load {group_id} 失败: {e}")
            return None
        try:
            return cls.from_dict(d)
        except Exception as e:
            logger.warning(f"[squad] {group_id} schema 不兼容: {e}")
            return None

    @classmethod
    def load_or_fresh(cls, group_id: str, leader_instance: int = -1,
                       member_instances: Optional[list] = None) -> "SquadState":
        state = cls.load(group_id)
        if state is None:
            state = cls.fresh(group_id, leader_instance, member_instances)
            state.save_atomic()
        return state

    def save_atomic(self) -> None:
        self.last_save_ts = time.time()
        p = self.file_path(self.group_id)
        tmp = p.with_suffix(p.suffix + ".tmp")
        try:
            tmp.parent.mkdir(parents=True, exist_ok=True)
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
            os.replace(str(tmp), str(p))
        except OSError as e:
            logger.warning(f"[squad] save {self.group_id} 失败: {e}")

    @classmethod
    def delete(cls, group_id: str) -> bool:
        p = cls.file_path(group_id)
        try:
            if p.is_file():
                p.unlink()
            return True
        except OSError as e:
            logger.warning(f"[squad] delete {group_id} 失败: {e}")
            return False

    # ───────────── 业务 helpers ─────────────

    def is_leader_alive(self,
                         timeout_s: float = LEADER_HEARTBEAT_TIMEOUT_S) -> bool:
        """队员调: 心跳是否在容忍窗口内.

        leader_alive=False (闪退主动标记的) → False
        last_heartbeat 距 now > timeout_s → False
        否则 → True
        """
        if not self.leader_alive:
            return False
        if self.leader_last_heartbeat <= 0:
            return False
        return time.time() - self.leader_last_heartbeat <= timeout_s

    def mark_leader_crashed(self) -> None:
        """recovery.py 在 captain 检测到闪退时调."""
        self.leader_alive = False
        self.team_code_valid = False
        self.save_atomic()

    def update_team_code(self, new_code: str) -> None:
        """captain P3a 生成新码后调."""
        self.team_code = new_code
        self.team_code_valid = bool(new_code)
        self.team_code_generated_at = time.time()
        self.leader_alive = True  # 既然能写新码, 队长肯定还活着
        self.save_atomic()

    def heartbeat(self) -> None:
        """captain 周期性调 (e.g. 每 5s) — 唯一合理的"周期写"用例."""
        self.leader_alive = True
        self.leader_last_heartbeat = time.time()
        self.save_atomic()

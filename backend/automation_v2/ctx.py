"""RunContext — 每实例 1 个, 所有 phase 共享.

- 时间戳: ctx.new_round() 起新 trace, ctx.mark("event") 记 t_event
- 黑名单: ctx.add_blacklist(x,y,ttl), ctx.is_blacklisted(x,y) — TTL 自动过期
- 注入资源: yolo/ocr/matcher/adb/log 都是 Protocol, 换实现不破上层

砍 v1 phase_base.RunContext 的 15+ 字段 (pending_memory_writes / lobby_posterior /
pending_verify / carryover_shot / persistent_state ...), 业务真要时再加.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, NamedTuple, Optional

# numpy 在运行时才用, 类型注解用字符串避免启动时强依赖
# (跑单元测试 / 不装 numpy 也能 import ctx)


class BlacklistEntry(NamedTuple):
    x: int
    y: int
    expires_at: float   # time.perf_counter() 截止点


@dataclass
class RunContext:
    """每实例 1 个. enter() 时 reset_phase_state(); 跨 phase 数据 (role / scheme) 保留."""

    # ── 注入资源 (Protocol 接口, 见 perception/action/log) ──
    yolo: Any = None             # YoloProto
    ocr: Any = None              # OcrProto
    matcher: Any = None          # MatcherProto
    adb: Any = None              # AdbTapProto
    log: Any = None              # DecisionLogProto

    # ── 配置 ──
    instance_idx: int = -1
    role: str = "unknown"                  # captain / member / unknown
    game_scheme_url: Optional[str] = None

    # ── 每轮帧缓存 ──
    current_shot: Optional[Any] = None     # np.ndarray, 不强引用
    phase_round: int = 0
    phase_started_at: float = 0.0

    # ── 当前 trace ──
    trace_id: str = ""
    _ts: dict[str, float] = field(default_factory=dict)

    # ── 黑名单 ──
    _blacklist: list[BlacklistEntry] = field(default_factory=list)

    # ── P2 大厅判定 ──
    lobby_streak: int = 0

    # ─────────── 时间戳 API ───────────
    def new_round(self) -> None:
        """每 round 起新 trace + 重置时间戳."""
        self.trace_id = uuid.uuid4().hex[:12]
        self._ts = {}
        self.mark("round_start")

    def mark(self, event: str) -> None:
        """记 t_<event> 到当前 trace. 落盘由 DecisionLog 一次性写."""
        self._ts[f"t_{event}"] = time.perf_counter()

    def ts(self, event: str, default: float = 0.0) -> float:
        return self._ts.get(f"t_{event}", default)

    def ts_snapshot(self) -> dict[str, float]:
        return dict(self._ts)

    # ─────────── 黑名单 API ───────────
    def add_blacklist(self, x: int, y: int, ttl: float = 3.0) -> None:
        self._blacklist.append(BlacklistEntry(x, y, time.perf_counter() + ttl))

    def is_blacklisted(self, x: int, y: int, radius: int = 30) -> bool:
        """坐标 (x,y) ±radius 范围内有未过期黑名单返 True. 顺手清过期项."""
        now = time.perf_counter()
        self._blacklist = [e for e in self._blacklist if e.expires_at > now]
        return any(
            abs(e.x - x) < radius and abs(e.y - y) < radius
            for e in self._blacklist
        )

    # ─────────── phase 切换 ───────────
    def reset_phase_state(self) -> None:
        """进入新 phase 时 enter() 调. 清黑名单 + round 计数 + frame 缓存."""
        self._blacklist.clear()
        self.phase_round = 0
        self.phase_started_at = time.perf_counter()
        self.current_shot = None
        self.trace_id = ""
        self._ts.clear()
        self.lobby_streak = 0

"""
v3 Phase 基础抽象 — FSM + 三层架构核心.

设计目的:
  替代 v2 yolo_dismisser.dismiss_all 911 行单函数 + 8+ 散落状态变量
  把 "感知 / 决策 / 执行 / 状态" 四件事分到不同对象, 主循环只做编排.

四个核心抽象:
  - PhaseResult  : Handler 返回什么 (NEXT/RETRY/WAIT/FAIL/GAME_RESTART/DONE)
  - PhaseAction  : Handler 想做什么 (tap/wait/noop, 纯数据不直接 IO)
  - PhaseStep    : Handler 一帧的产出 (PhaseResult + 可选 PhaseAction)
  - RunContext   : 跨 phase 共享可变状态 (memory / blacklist / timers / ...)
  - PhaseHandler : ABC 接口, enter / handle_frame / exit / on_failure

数据流:
  RunnerFSM.loop:
    shot = await device.screenshot()
    ctx.current_shot = shot
    step = await handler.handle_frame(ctx)   # 纯逻辑, 不直接 tap
    if step.action: await ActionExecutor.apply(ctx, step.action)
    next_state = transition_table[(state, step.result)]
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Literal, Optional

import numpy as np


# ─────────────── 枚举 / 数据类型 ───────────────


class PhaseResult(Enum):
    """Handler 一帧的结果. RunnerFSM 据此走转移表."""
    NEXT = auto()           # 当前 phase 完成, 进下一 phase
    RETRY = auto()           # 同 phase 再来一帧 (handle_frame 会再调)
    WAIT = auto()            # 配合 PhaseStep.wait_seconds, 等待 N 秒后 RETRY
    FAIL = auto()            # 当前 phase 失败 → runner_service 会 _PhaseError 重试
    GAME_RESTART = auto()    # 严重 → kill 游戏, 从 P0 重启
    DONE = auto()            # 整个 FSM 跑完 (P3b/P4 出口)


@dataclass
class PhaseAction:
    """Handler 想做什么. 不直接 IO, 由 ActionExecutor 实施.

    kind:
      tap         - 点击 (x, y) 后 sleep seconds, 可选 expectation 验证
      wait        - sleep seconds 不动作
      noop        - 啥也不做 (返回 RETRY 时常用)
    """
    kind: Literal["tap", "wait", "noop"] = "noop"
    x: int = 0
    y: int = 0
    seconds: float = 0.0
    label: str = ""                       # state_expectation 的 label key
    expectation: Optional[str] = None     # 期望效果 label (state_expectation 注册过的)
    payload: dict = field(default_factory=dict)  # phase-specific 上下文 (如 yolo_before)


@dataclass
class PhaseStep:
    """handle_frame 一帧的完整产出."""
    result: PhaseResult
    action: Optional[PhaseAction] = None
    wait_seconds: float = 0.0
    note: str = ""                        # 决策注释 (写进 decision_log)


# ─────────────── RunContext ─────────────────


@dataclass
class RunContext:
    """跨 phase 共享的可变状态. 每实例 1 个.

    字段分组 + ownership:
      [注入资源 / 不可变] device, matcher, recognizer, yolo, memory, ...
      [配置 / 不可变]    instance_idx, account, settings
      [跨 phase 共享]    role, game_scheme_url
      [P2 owned]         blacklist_coords, pending_memory_writes,
                         last_tap_xy, same_target_count, empty_dets_streak,
                         login_first_seen_ts, lobby_confirm_count, popups_closed
      [phase 计时]       phase_started_at, phase_round
      [帧缓存 / 每轮]    current_shot, current_phash

    每 PhaseHandler.enter() 必须调 reset_phase_state() 清干净 P2/计时/帧缓存,
    不变跨 phase 的 (role / game_scheme_url) 不动.
    """

    # 注入资源 (RunnerFSM 构造时设, 之后只读)
    device: Any                           # ADBController
    matcher: Any                          # ScreenMatcher
    recognizer: Any                       # Recognizer (5 层 early-exit)
    runner: Any = None                    # SingleInstanceRunner (helper 方法调用源)
    yolo: Optional[Any] = None            # YoloDismisser (per-instance session, v2-9)
    memory: Optional[Any] = None          # FrameMemory
    lobby_detector: Optional[Any] = None  # LobbyQuadDetector
    decision_recorder: Optional[Any] = None  # decision_log._Recorder

    # 配置
    instance_idx: int = -1
    account: Any = None
    settings: Any = None

    # 跨 phase 数据 (只在特定 phase 写, 其他 phase 只读)
    role: Literal["leader", "follower", "unknown"] = "unknown"
    game_scheme_url: Optional[str] = None  # P3a 写, P3b 读

    # P2 owned 会话状态 (每 phase enter 重置)
    blacklist_coords: list = field(default_factory=list)        # [(x, y)]
    pending_memory_writes: list = field(default_factory=list)   # [(frame, (x,y), label)]
    last_tap_xy: tuple = (0, 0)
    same_target_count: int = 0
    empty_dets_streak: int = 0
    login_first_seen_ts: Optional[float] = None
    lobby_confirm_count: int = 0
    popups_closed: int = 0

    # phase 计时
    phase_started_at: float = 0.0
    phase_round: int = 0

    # 帧缓存 (RunnerFSM._loop_phase 每轮写)
    current_shot: Optional[np.ndarray] = None
    current_phash: str = ""

    def reset_phase_state(self) -> None:
        """每 PhaseHandler.enter() 必调. 清 P2/计时/帧缓存, 跨 phase 数据不动."""
        self.blacklist_coords.clear()
        self.pending_memory_writes.clear()
        self.last_tap_xy = (0, 0)
        self.same_target_count = 0
        self.empty_dets_streak = 0
        self.login_first_seen_ts = None
        self.lobby_confirm_count = 0
        self.popups_closed = 0
        self.phase_started_at = time.perf_counter()
        self.phase_round = 0
        self.current_shot = None
        self.current_phash = ""

    def is_blacklisted(self, x: int, y: int, radius: int = 30) -> bool:
        """坐标是否在会话黑名单内 (距离 < radius)"""
        return any(
            abs(x - bx) < radius and abs(y - by) < radius
            for (bx, by) in self.blacklist_coords
        )


# ─────────────── PhaseHandler ABC ───────────────


class PhaseHandler(ABC):
    """每个 phase 一个子类. RunnerFSM 调度.

    生命周期:
      1. enter(ctx)    - 进入 phase, 默认调 ctx.reset_phase_state()
      2. handle_frame(ctx) - 每帧调一次, 返回 PhaseStep
                              直到 result ∈ {NEXT, FAIL, GAME_RESTART, DONE} 或 max_rounds
      3. exit(ctx, result) - 退出 phase, 默认 noop
      4. on_failure(ctx, exc) - 抛异常时调, 默认返回 FAIL
    """

    name: str = "<phase>"
    max_rounds: int = 60                   # 超过 → 自动 FAIL
    round_interval_s: float = 0.5          # 每帧间隔 (RETRY 时)

    async def enter(self, ctx: RunContext) -> None:
        """进入 phase. 默认重置 phase-owned 状态."""
        ctx.reset_phase_state()

    @abstractmethod
    async def handle_frame(self, ctx: RunContext) -> PhaseStep:
        """每帧调一次. 读 ctx.current_shot, 返回 PhaseStep.

        不要在这里直接调 device.tap — 通过 PhaseAction 让 ActionExecutor 实施.
        这样 4 道防线 (phash 验证 / state_expectation / 黑名单) 集中在 executor 里.
        """

    async def exit(self, ctx: RunContext, result: PhaseResult) -> None:
        """退出 phase 时调. 可在此 commit 缓冲 (如 P2 大厅时 commit pending_memory)."""
        pass

    async def on_failure(self, ctx: RunContext, exc: Exception) -> PhaseResult:
        """handle_frame 抛异常时调. 默认 → FAIL."""
        import logging
        logging.getLogger(__name__).warning(
            f"[{self.name}] handle_frame 异常: {exc}", exc_info=True
        )
        return PhaseResult.FAIL

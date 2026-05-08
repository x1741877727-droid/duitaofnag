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
    outcome_hint: str = ""                # 细粒度 outcome (P2 用: lobby_confirmed_quad / no_target / loop_blocked / tapped / login_timeout_fail / lobby_pending_X)


# ─────────────── RunContext ─────────────────


@dataclass
class RunContext:
    """跨 phase 共享的可变状态. 每实例 1 个.

    字段分组 + ownership:
      [注入资源 / 不可变] device, matcher, yolo, memory, ...
      [配置 / 不可变]    instance_idx, account, settings
      [跨 phase 共享]    role, game_scheme_url
      [P2 owned]         blacklist_coords, pending_memory_writes,
                         last_tap_xy, empty_dets_streak,
                         login_first_seen_ts, lobby_confirm_count, popups_closed
      [phase 计时]       phase_started_at, phase_round
      [帧缓存 / 每轮]    current_shot, current_phash

    每 PhaseHandler.enter() 必须调 reset_phase_state() 清干净 P2/计时/帧缓存,
    不变跨 phase 的 (role / game_scheme_url) 不动.
    """

    # 注入资源 (RunnerFSM 构造时设, 之后只读)
    device: Any                           # ADBController
    matcher: Any                          # ScreenMatcher
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

    # P5 owned (等待玩家阶段, 测试页 / 后续主 loop API 注入)
    expected_id: Optional[str] = None              # 10 位 player ID, P5 入口前由调用方注入
    team_slot_baseline: list = field(default_factory=list)  # P5 入口时 4 slot OCR 字数 [c1,c2,c3,c4]
    kicked_ids: set = field(default_factory=set)   # 已踢出的捣乱者 ID, 防重复 tap 同一人

    # P2 owned 会话状态 (每 phase enter 重置)
    blacklist_coords: list = field(default_factory=list)        # [(x, y)]
    pending_memory_writes: list = field(default_factory=list)   # [(frame, (x,y), label)]
    last_tap_xy: tuple = (0, 0)
    empty_dets_streak: int = 0
    login_first_seen_ts: Optional[float] = None
    lobby_confirm_count: int = 0
    lobby_posterior: float = 0.5      # 贝叶斯早退: P(在大厅 | 历史观测), 阈 0.92 退出
    popups_closed: int = 0
    # 死屏判定 (2026-04-30 改成时间预算): 区分真死屏 vs loading 中
    last_phash_int: int = 0           # 上一轮 phash, 用于对比是否变化
    no_target_started_ts: float = 0.0    # no_target 起点时间戳 (一旦有 tap 重置)
    phash_stuck_started_ts: float = 0.0  # phash 开始卡住的时间戳 (变化即重置)

    # phase 计时
    phase_started_at: float = 0.0
    phase_round: int = 0

    # 帧缓存 (RunnerFSM._loop_phase 每轮写)
    current_shot: Optional[np.ndarray] = None
    current_phash: str = ""

    # 帧复用: ActionExecutor._do_tap 拿到 shot_after 后写这里, 下一轮
    # _loop_phase 看时效内 (<200ms) 直接当作 current_shot 用, 省一次 screencap.
    # P2 burst mode (wait_seconds=0) 收益最大. 超时 / 没设 fallback 自拍.
    carryover_shot: Optional[np.ndarray] = None
    carryover_phash: int = 0
    carryover_ts: float = 0.0     # time.perf_counter() 写入时刻

    # 推迟 verify: _do_tap 完只 stash, 下一轮 perceive 跑完后用 YOLO 结果判定.
    # 替代旧版同步 wait_for_change polling + state_expectation.verify, 省 100-300ms/tap.
    # dict: {xy: (cx,cy), label: str, shot_before, expectation: str}
    pending_verify: Optional[Any] = None

    # 决策记录 (RunnerFSM._loop_phase 每轮 new_decision, finalize 时清; phase 中可 add_tier)
    current_decision: Optional[Any] = None

    # Stage 2 持久化状态 (PhaseHandler.enter 自动 load + 挂这里, exit 自动 save).
    # phase 业务可直接改 ctx.persistent_state.<field> + ctx.persistent_state.save_atomic()
    # 在关键事件触发时立即落盘 (事件驱动, 不靠 timer).
    persistent_state: Optional[Any] = None

    def reset_phase_state(self) -> None:
        """每 PhaseHandler.enter() 必调. 清 P2/计时/帧缓存, 跨 phase 数据不动."""
        self.blacklist_coords.clear()
        self.pending_memory_writes.clear()
        self.last_tap_xy = (0, 0)
        self.empty_dets_streak = 0
        self.login_first_seen_ts = None
        self.lobby_confirm_count = 0
        self.lobby_posterior = 0.5
        self.popups_closed = 0
        self.last_phash_int = 0
        self.no_target_started_ts = 0.0
        self.phash_stuck_started_ts = 0.0
        self.phase_started_at = time.perf_counter()
        self.phase_round = 0
        self.current_shot = None
        self.current_phash = ""
        self.carryover_shot = None
        self.carryover_phash = 0
        self.carryover_ts = 0.0
        self.pending_verify = None

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
    name_cn: str = ""                      # 中文名 (如 "加速器校验")
    description: str = ""                  # 一句话: 这个 phase 在做什么 (中控台展示)
    flow_steps: list[str] = []             # 步骤分解 (按序; 中控台展示)
    max_rounds: int = 60                   # 超过 round 数 → 自动 FAIL
    max_seconds: "float | None" = None     # 超过 N 秒 → 自动 FAIL (优先级高, 覆盖 max_rounds)
    round_interval_s: float = 0.5          # 每帧间隔 (RETRY 时)

    async def enter(self, ctx: RunContext) -> None:
        """进入 phase. 默认重置 phase-owned 状态 + load 持久化状态.

        Stage 2 钩子: 自动 load InstanceState (闪退恢复用), 挂 ctx.persistent_state.
        子类可以覆盖 enter, 但要 super().enter(ctx) 让 ctx.persistent_state 就位.
        """
        ctx.reset_phase_state()
        # Stage 2: load 持久化状态, 挂到 ctx (子类 phase 写自己的字段)
        try:
            from .instance_state import InstanceState
            state = InstanceState.load(ctx.instance_idx)
            if state is None:
                state = InstanceState.fresh(ctx.instance_idx)
            # 写当前 phase 信息
            state.phase = self.name
            import time as _time
            state.phase_started_at = _time.time()
            state.phase_round = 0
            # role / squad_id 跨 phase 沿用 (phase 自己写覆盖)
            ctx.persistent_state = state
            state.save_atomic()
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug(
                f"[{self.name}] persistent_state init err: {e}")
            ctx.persistent_state = None

    @abstractmethod
    async def handle_frame(self, ctx: RunContext) -> PhaseStep:
        """每帧调一次. 读 ctx.current_shot, 返回 PhaseStep.

        不要在这里直接调 device.tap — 通过 PhaseAction 让 ActionExecutor 实施.
        这样 4 道防线 (phash 验证 / state_expectation / 黑名单) 集中在 executor 里.
        """

    async def exit(self, ctx: RunContext, result: PhaseResult) -> None:
        """退出 phase 时调. 可在此 commit 缓冲 (如 P2 大厅时 commit pending_memory).

        Stage 2 钩子: 自动 save 持久化状态 (最终值落盘, 闪退恢复读这个).
        """
        # Stage 2: 持久化最终状态
        state = getattr(ctx, "persistent_state", None)
        if state is not None:
            try:
                state.save_atomic()
            except Exception as e:
                import logging
                logging.getLogger(__name__).debug(
                    f"[{self.name}] persistent_state save_atomic err: {e}")

    async def on_failure(self, ctx: RunContext, exc: Exception) -> PhaseResult:
        """handle_frame 抛异常时调. 默认 → FAIL."""
        import logging
        logging.getLogger(__name__).warning(
            f"[{self.name}] handle_frame 异常: {exc}", exc_info=True
        )
        return PhaseResult.FAIL

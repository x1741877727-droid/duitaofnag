"""
v3 RunnerFSM — 顶层 phase FSM 调度.

替代 v2 single_runner.run() 的串接调用 (phase_accelerator → phase_launch_game → ...).
状态转移用静态表 + role 分支动态判定, 异常翻译成 _PhaseError / _GameCrashError
让 runner_service 走重试 / game_restart 链路.

主循环:
  for state in run():
    handler = handlers[state]
    handler.enter(ctx)
    for round in range(max_rounds):
      ctx.current_shot = await device.screenshot()
      step = await handler.handle_frame(ctx)
      if step.action: await ActionExecutor.apply(ctx, step.action)
      result = step.result
      if result in (NEXT, FAIL, GAME_RESTART, DONE): break
      if result == WAIT: sleep(step.wait_seconds)
      else: sleep(handler.round_interval_s)
    handler.exit(ctx, result)
    state = next_state(state, result)
"""

from __future__ import annotations

import asyncio
import logging
import time
from enum import Enum, auto
from typing import Optional

from .action_executor import ActionExecutor
from .phase_base import PhaseHandler, PhaseResult, PhaseStep, RunContext

logger = logging.getLogger(__name__)


class FsmState(Enum):
    IDLE = auto()
    P0_ACCELERATOR = auto()
    P1_LAUNCH = auto()
    P2_DISMISS = auto()
    P3A_TEAM_CREATE = auto()      # leader only
    P3B_TEAM_JOIN = auto()        # follower only
    P4_MAP_SETUP = auto()         # leader only, after P3A
    P5_WAIT_PLAYERS = auto()      # 等真人入队 (测试页可单独跑; 主 loop _TRANSITIONS 暂不接, 走 API 推 expected_id 后再开)
    DONE = auto()
    ERROR = auto()


# 静态转移表 — (from_state, result) → next_state
# 角色分支 (P2 → P3A vs P3B) 在 _next_state() 里动态判 ctx.role.
_TRANSITIONS: dict[tuple[FsmState, PhaseResult], FsmState] = {
    (FsmState.IDLE, PhaseResult.NEXT): FsmState.P0_ACCELERATOR,
    (FsmState.P0_ACCELERATOR, PhaseResult.NEXT): FsmState.P1_LAUNCH,
    (FsmState.P1_LAUNCH, PhaseResult.NEXT): FsmState.P2_DISMISS,
    # P2_DISMISS → P3A/P3B (动态判 role)
    (FsmState.P3A_TEAM_CREATE, PhaseResult.NEXT): FsmState.P4_MAP_SETUP,
    (FsmState.P3B_TEAM_JOIN, PhaseResult.NEXT): FsmState.DONE,
    (FsmState.P4_MAP_SETUP, PhaseResult.NEXT): FsmState.DONE,
}


_RESULT_OUTCOME = {
    PhaseResult.NEXT: "phase_next",
    PhaseResult.RETRY: "retry",
    PhaseResult.WAIT: "wait",
    PhaseResult.FAIL: "phase_fail",
    PhaseResult.GAME_RESTART: "game_restart",
    PhaseResult.DONE: "phase_done",
}


def _result_to_outcome(r: PhaseResult) -> str:
    return _RESULT_OUTCOME.get(r, str(r))


class _PhaseFailError(Exception):
    """RunnerFSM 内部异常, 翻译成 runner_service 的 _PhaseError."""
    def __init__(self, state: FsmState, reason: str):
        self.state = state
        self.reason = reason
        super().__init__(f"{state.name}: {reason}")


class _GameRestartError(Exception):
    """严重错误, 翻译成 runner_service 的 _GameCrashError."""
    def __init__(self, state: FsmState, reason: str):
        self.state = state
        self.reason = reason
        super().__init__(f"{state.name}: {reason}")


class RunnerFSM:
    """顶层 phase FSM. 1 实例 1 个."""

    def __init__(
        self,
        ctx: RunContext,
        handlers: dict[FsmState, PhaseHandler],
        on_phase_change: Optional[callable] = None,  # (FsmState, FsmState) -> None
    ):
        self._ctx = ctx
        self._handlers = handlers
        self._state = FsmState.IDLE
        self._on_phase_change = on_phase_change

    @property
    def state(self) -> FsmState:
        return self._state

    async def run(self) -> bool:
        """主循环. 跑到 DONE 返回 True, ERROR / 超时返回 False.

        异常翻译 (供 runner_service 接住):
          PhaseResult.FAIL → raise _PhaseFailError
          PhaseResult.GAME_RESTART → raise _GameRestartError
        runner_service 会 catch 这两个 (或换成 _PhaseError / _GameCrashError) 重试.
        """
        self._set_state(FsmState.P0_ACCELERATOR)
        while self._state not in (FsmState.DONE, FsmState.ERROR):
            handler = self._handlers.get(self._state)
            if handler is None:
                logger.error(f"[FSM] 缺 handler for {self._state.name}")
                self._set_state(FsmState.ERROR)
                break

            logger.info(f"[FSM] 进入 {self._state.name} (handler={handler.name})")
            await handler.enter(self._ctx)
            try:
                final_result = await self._loop_phase(handler)
            except Exception as e:
                final_result = await handler.on_failure(self._ctx, e)
            await handler.exit(self._ctx, final_result)
            logger.info(
                f"[FSM] 离开 {self._state.name} → result={final_result.name} "
                f"({handler.name}, {self._ctx.phase_round} 轮)"
            )

            # 翻译为异常给 runner_service (在 ERROR 状态前)
            if final_result == PhaseResult.FAIL:
                raise _PhaseFailError(self._state, "phase fail")
            if final_result == PhaseResult.GAME_RESTART:
                raise _GameRestartError(self._state, "game restart requested")

            # 转移
            next_state = self._next_state(self._state, final_result)
            self._set_state(next_state)

        return self._state == FsmState.DONE

    async def _loop_phase(self, handler: PhaseHandler) -> PhaseResult:
        """跑一个 phase 直到出 NEXT/FAIL/GAME_RESTART/DONE 或超 max_rounds."""
        from .decision_log import get_recorder
        recorder = get_recorder()

        # 帧复用: 上一轮 ActionExecutor._do_tap 写 carryover_shot/phash, 这里 200ms
        # 时效内直接复用, 省一次 screencap (~80ms) + phash (~5ms). 超时 fallback 自拍.
        CARRYOVER_MAX_AGE_S = 0.2

        for rnd in range(handler.max_rounds):
            self._ctx.phase_round = rnd + 1
            # 每轮开始计时, 末尾打印各步耗时
            _t0 = time.perf_counter()
            _perf = {"shot": 0.0, "phash": 0.0, "new_dec": 0.0,
                     "handle": 0.0, "apply": 0.0, "finalize": 0.0, "sleep": 0.0,
                     "carryover": 0}

            # 试图复用上一轮 tap 后的帧
            shot = None
            phash_int = 0
            if (self._ctx.carryover_shot is not None
                and (time.perf_counter() - self._ctx.carryover_ts) <= CARRYOVER_MAX_AGE_S):
                shot = self._ctx.carryover_shot
                phash_int = self._ctx.carryover_phash
                _perf["carryover"] = 1
                # 消费一次, 防下一轮再用 (避免 stale)
                self._ctx.carryover_shot = None
                self._ctx.carryover_phash = 0
                self._ctx.carryover_ts = 0.0
            else:
                # carryover 没有 / 超时 → 自拍
                _t = time.perf_counter()
                try:
                    shot = await self._ctx.device.screenshot()
                except Exception as e:
                    logger.debug(f"[{handler.name}] 截图失败: {e}")
                    shot = None
                _perf["shot"] = (time.perf_counter() - _t) * 1000

            self._ctx.current_shot = shot
            if shot is None:
                await asyncio.sleep(0.3)
                continue

            # phash: carryover 已经带了; 否则自己算
            phash_str = ""
            if phash_int == 0:
                _t = time.perf_counter()
                try:
                    from .adb_lite import phash as _phash
                    phash_int = _phash(shot)
                except Exception:
                    pass
                _perf["phash"] = (time.perf_counter() - _t) * 1000
            phash_str = f"0x{int(phash_int):016x}" if phash_int else ""
            self._ctx.current_phash = phash_str
            decision = None
            _t = time.perf_counter()
            try:
                decision = recorder.new_decision(
                    instance=self._ctx.instance_idx,
                    phase=handler.name,
                    round_idx=self._ctx.phase_round,
                )
                decision.set_input(shot, phash_str, q=70)
                self._ctx.current_decision = decision
            except Exception as e:
                logger.debug(f"[{handler.name}] new_decision err: {e}")
                self._ctx.current_decision = decision
            _perf["new_dec"] = (time.perf_counter() - _t) * 1000

            # 调 handler 决策 (perceive + decide)
            step: Optional[PhaseStep] = None
            handle_exc: Optional[Exception] = None
            _t = time.perf_counter()
            try:
                step = await handler.handle_frame(self._ctx)
            except Exception as e:
                handle_exc = e
                logger.warning(f"[{handler.name}] handle_frame 异常: {e}", exc_info=True)
            _perf["handle"] = (time.perf_counter() - _t) * 1000

            # 实施 action (tap + verify)
            _t = time.perf_counter()
            if step is not None and step.action is not None:
                try:
                    await ActionExecutor.apply(self._ctx, step.action)
                except Exception as e:
                    logger.warning(f"[{handler.name}] ActionExecutor 异常: {e}")
            _perf["apply"] = (time.perf_counter() - _t) * 1000

            # finalize 决策记录
            _t = time.perf_counter()
            try:
                outcome = ""
                note = ""
                if step is not None:
                    note = step.note or ""
                    if step.outcome_hint:
                        outcome = step.outcome_hint
                    else:
                        outcome = _result_to_outcome(step.result)
                else:
                    outcome = "phase_exception"
                    note = repr(handle_exc) if handle_exc else ""
                if decision is not None:
                    decision.finalize(outcome=outcome, note=note)
            except Exception as e:
                logger.debug(f"[{handler.name}] finalize err: {e}")
            finally:
                self._ctx.current_decision = None
            _perf["finalize"] = (time.perf_counter() - _t) * 1000

            # handle_frame 抛异常 → on_failure
            if handle_exc is not None:
                return await handler.on_failure(self._ctx, handle_exc)

            if step.note:
                logger.info(f"[{handler.name}/R{rnd + 1}] {step.note}")

            # 一轮 perf 汇总 (终态前先打印)
            _round_total = (time.perf_counter() - _t0) * 1000
            inst_idx = getattr(self._ctx, 'instance_idx', '?')
            logger.info(
                f"[PERF/{handler.name}/R{rnd + 1}/inst{inst_idx}] "
                f"total={_round_total:.0f}ms "
                f"shot={_perf['shot']:.0f} phash={_perf['phash']:.0f} "
                f"new_dec={_perf['new_dec']:.0f} handle={_perf['handle']:.0f} "
                f"apply={_perf['apply']:.0f} finalize={_perf['finalize']:.0f}"
                f"{' [carryover]' if _perf['carryover'] else ''}"
            )

            # 终态 → 立即返回
            if step.result in (
                PhaseResult.NEXT, PhaseResult.FAIL,
                PhaseResult.GAME_RESTART, PhaseResult.DONE,
            ):
                return step.result

            # WAIT 模式: sleep 指定秒数
            _t_sleep = time.perf_counter()
            if step.result == PhaseResult.WAIT:
                await asyncio.sleep(max(0.0, step.wait_seconds))
            else:
                # RETRY 默认间隔
                await asyncio.sleep(handler.round_interval_s)
            _perf["sleep"] = (time.perf_counter() - _t_sleep) * 1000

        # max_rounds 用完 → FAIL
        logger.warning(
            f"[{handler.name}] 超 max_rounds={handler.max_rounds} → FAIL"
        )
        return PhaseResult.FAIL

    def _next_state(self, cur: FsmState, result: PhaseResult) -> FsmState:
        """转移. 角色分支动态判."""
        if result == PhaseResult.DONE:
            return FsmState.DONE

        # P2 → P3A (leader) / P3B (follower)
        if cur == FsmState.P2_DISMISS and result == PhaseResult.NEXT:
            role = self._ctx.role
            if role == "leader":
                return FsmState.P3A_TEAM_CREATE
            elif role == "follower":
                return FsmState.P3B_TEAM_JOIN
            else:
                # role 未知 → 直接 DONE (单实例 / 测试场景)
                logger.warning(f"[FSM] P2 → 未知 role={role}, 直接 DONE")
                return FsmState.DONE

        # 其他静态表
        key = (cur, result)
        if key in _TRANSITIONS:
            return _TRANSITIONS[key]

        logger.error(f"[FSM] 无转移: ({cur.name}, {result.name}) → ERROR")
        return FsmState.ERROR

    def _set_state(self, new_state: FsmState) -> None:
        old = self._state
        self._state = new_state
        if old != new_state and self._on_phase_change:
            try:
                self._on_phase_change(old, new_state)
            except Exception as e:
                logger.debug(f"[FSM] on_phase_change err: {e}")

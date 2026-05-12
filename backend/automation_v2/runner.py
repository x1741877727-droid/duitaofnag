"""SingleRunner — 顶层 phase loop. 每实例 1 个 asyncio task.

替代 v1 single_runner.py 1735 行 + runner_fsm.py 354 行 ≈ 2089 行.

REVIEW_DAY3_ARCH.md + REVIEW_DAY3_RISKS.md 调整后实现:
- 每 round 调 middleware.before_round (邀请/网络/crash 检测)
- handle_frame 异常 try-except (R-RA1 之前缺), 不杀 12 个 task 中的其他 11 个
- max_seconds 守门 (phase 超时强制 FAIL)
- 每 phase enter/exit 调 state.on_phase_enter/exit (recovery)
- 每 round 7 时间戳 mark + decision.record() 落盘
- runner 启动前 yolo/ocr.warmup() (避免 cold start)

PHASE_ORDER 跟 v1 一致, role=captain 走 P3a, role=member 走 P3b.
"""
from __future__ import annotations

import asyncio
import logging
import time
import traceback
from typing import Any, Optional

from .ctx import RunContext
from .phase_base import PhaseResult, PhaseStep

logger = logging.getLogger(__name__)


# Phase 顺序 — captain 走 P3a, member 走 P3b
PHASE_ORDER_CAPTAIN = ["P0", "P1", "P2", "P3a", "P4", "P5"]
PHASE_ORDER_MEMBER = ["P0", "P1", "P2", "P3b", "P5"]


class SingleRunner:
    """1 实例 1 个. 跑顺序 phase, NEXT 进下一个, FAIL 返 False."""

    def __init__(
        self,
        ctx: RunContext,
        phases: dict[str, Any],
        middlewares: Optional[list[Any]] = None,
        state_adapter: Optional[Any] = None,
        phase_order: Optional[list[str]] = None,
        on_phase_change: Optional[Any] = None,
    ):
        self.ctx = ctx
        self.phases = phases             # {"P0": P0Accel(), ...}
        self.middlewares = middlewares or []
        self.state = state_adapter       # InstanceStateAdapter (None 时不持久化恢复)
        # phase_order: 显式传 → 用 (Day 4 灰度截短到 P4); 不传 → 按 role 默认全跑
        self.phase_order = phase_order
        # on_phase_change: callable(phase_name: str) — runner_service 推 inst.state 用
        self.on_phase_change = on_phase_change

    async def run(self) -> bool:
        """跑完整 session: P0 → P1 → ... → P5. 返 True = 成功, False = FAIL.

        recovery: 启动时 state.get_recovery_phase() 决定从哪开始 (skip 已完成 phase).
        """
        if self.phase_order is not None:
            order = self.phase_order
        else:
            order = (
                PHASE_ORDER_CAPTAIN if self.ctx.role == "captain"
                else PHASE_ORDER_MEMBER
            )
        # Recovery: 从上次中断的 phase 开始
        recovery_phase = None
        if self.state is not None:
            try:
                recovery_phase = self.state.get_recovery_phase()
            except Exception as e:
                logger.warning(f"[runner/inst{self.ctx.instance_idx}] recovery err: {e}")
        skip_until: Optional[str] = recovery_phase

        for phase_name in order:
            if skip_until and phase_name != skip_until:
                logger.info(
                    f"[runner/inst{self.ctx.instance_idx}] skip {phase_name} "
                    f"(recovery 到 {skip_until})"
                )
                continue
            skip_until = None    # 后续不再 skip

            handler = self.phases.get(phase_name)
            if handler is None:
                logger.error(
                    f"[runner/inst{self.ctx.instance_idx}] no handler for {phase_name}"
                )
                return False

            logger.info(f"[runner/inst{self.ctx.instance_idx}] → {phase_name}")
            if self.on_phase_change:
                try:
                    self.on_phase_change(phase_name)
                except Exception as e:
                    logger.debug(f"[runner/inst{self.ctx.instance_idx}] on_phase_change err: {e}")
            ok = await self._run_phase(handler)
            if not ok:
                logger.warning(
                    f"[runner/inst{self.ctx.instance_idx}] {phase_name} FAIL, 整个 session 退出"
                )
                return False
        return True

    async def _run_phase(self, handler: Any) -> bool:
        """跑一个 phase 直到 NEXT/DONE/FAIL/GAME_RESTART/max_seconds.

        异常处理 (R-RA1):
        - handle_frame 抛 → 落 decision.outcome=exception, 不破 runner 主 loop
        - middleware 抛 → 同上
        - adb.screenshot 抛 → 落 'no_shot', RETRY

        max_seconds 守门:
        - phase.max_seconds=60 → 超时强制 FAIL, runner 退出整 session
        """
        # enter
        try:
            if hasattr(handler, "enter"):
                await handler.enter(self.ctx)
            else:
                self.ctx.reset_phase_state()
        except Exception as e:
            logger.warning(
                f"[{handler.name}/inst{self.ctx.instance_idx}] enter err: {e}",
                exc_info=True,
            )
            return False
        self.ctx.phase_started_at = time.perf_counter()
        if self.state:
            try:
                self.state.on_phase_enter(self.ctx, handler.name)
            except Exception as e:
                logger.debug(f"[state] on_phase_enter err: {e}")

        round_interval = getattr(handler, "round_interval_s", 0.5)
        max_seconds = getattr(handler, "max_seconds", 60.0)
        # 启用对当前 phase 有效的 middleware (enable_for 过滤)
        active_mws = [
            mw for mw in self.middlewares
            if mw.enable_for(handler.name)
        ]

        try:
            while True:
                # ── round 起始: 新 trace + screenshot ──
                self.ctx.phase_round += 1
                self.ctx.new_round()    # 设 trace_id + t_round_start

                shot = None
                try:
                    shot = await self.ctx.adb.screenshot()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.debug(f"[{handler.name}] screenshot err: {e}")
                self.ctx.mark("capture_done")
                self.ctx.current_shot = shot

                # ── middleware: before_round (邀请/网络/crash 检测) ──
                middleware_intercept = False
                middleware_note = ""
                for mw in active_mws:
                    try:
                        result = await mw.before_round(self.ctx, shot)
                        if result.intercept:
                            middleware_intercept = True
                            middleware_note = f"[mw:{mw.name}] {result.note}"
                            break    # 第一个 intercept 截断, 不再调后面 mw
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        logger.debug(f"[mw/{mw.name}] before_round err: {e}")

                # ── handle_frame (业务) ──
                step: Optional[PhaseStep] = None
                handle_exc: Optional[Exception] = None
                if middleware_intercept:
                    # middleware 已处理 (e.g. 关了邀请), 当前 round RETRY
                    step = PhaseStep(
                        result=PhaseResult.RETRY,
                        note=middleware_note,
                        outcome_hint="middleware",
                    )
                else:
                    try:
                        step = await handler.handle_frame(self.ctx)
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        handle_exc = e
                        logger.warning(
                            f"[{handler.name}/inst{self.ctx.instance_idx}/R{self.ctx.phase_round}] "
                            f"handle_frame 异常: {e}",
                            exc_info=True,
                        )

                # ── action (tap) ──
                if step and step.action and step.action.kind == "tap":
                    self.ctx.mark("tap_send")
                    try:
                        await self.ctx.adb.tap(step.action.x, step.action.y)
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        logger.debug(f"[{handler.name}] tap err: {e}")
                    self.ctx.mark("tap_done")
                else:
                    # 无 tap: 时间戳填空段, DecisionLog 写 0ms
                    self.ctx.mark("tap_send")
                    self.ctx.mark("tap_done")

                # ── 落决策 (即使异常也落) ──
                self._record_decision(handler.name, step, handle_exc)

                # ── 终态判断 ──
                if step is None and handle_exc is not None:
                    # handle_frame 抛异常 → 退当前 phase
                    logger.warning(
                        f"[{handler.name}/inst{self.ctx.instance_idx}] phase 异常退出"
                    )
                    return False

                if step.result == PhaseResult.NEXT or step.result == PhaseResult.DONE:
                    if step.note:
                        logger.info(
                            f"[{handler.name}/inst{self.ctx.instance_idx}/R{self.ctx.phase_round}] "
                            f"{step.note}"
                        )
                    return True
                if step.result == PhaseResult.FAIL:
                    logger.warning(
                        f"[{handler.name}/inst{self.ctx.instance_idx}/R{self.ctx.phase_round}] "
                        f"FAIL: {step.note}"
                    )
                    return False
                if step.result == PhaseResult.GAME_RESTART:
                    logger.warning(
                        f"[{handler.name}/inst{self.ctx.instance_idx}] GAME_RESTART: {step.note}"
                    )
                    # GAME_RESTART 当前简化为 FAIL, 让上层 runner_service 决定重启策略
                    return False

                # ── max_seconds 守门 ──
                elapsed = time.perf_counter() - self.ctx.phase_started_at
                if max_seconds and elapsed >= max_seconds:
                    logger.warning(
                        f"[{handler.name}/inst{self.ctx.instance_idx}] "
                        f"max_seconds={max_seconds}s 到, 强制 FAIL"
                    )
                    return False

                # ── RETRY: log + sleep ──
                if step.note and self.ctx.phase_round % 10 == 0:
                    logger.debug(
                        f"[{handler.name}/inst{self.ctx.instance_idx}/R{self.ctx.phase_round}] "
                        f"{step.note}"
                    )

                # WAIT 用 step.wait_seconds, 否则 phase round_interval
                sleep_s = step.wait_seconds if step.result == PhaseResult.WAIT else round_interval
                try:
                    await asyncio.sleep(sleep_s)
                except asyncio.CancelledError:
                    raise
        finally:
            # exit (无论 NEXT / FAIL / 异常)
            try:
                if hasattr(handler, "exit"):
                    await handler.exit(self.ctx)
            except Exception as e:
                logger.debug(f"[{handler.name}] exit err: {e}")
            # state save
            if self.state:
                try:
                    self.state.on_phase_exit(self.ctx, handler.name, "completed")
                except Exception as e:
                    logger.debug(f"[state] on_phase_exit err: {e}")
            # middleware 收尾
            for mw in active_mws:
                try:
                    await mw.after_phase(self.ctx)
                except Exception as e:
                    logger.debug(f"[mw/{mw.name}] after_phase err: {e}")

    def _record_decision(
        self,
        phase_name: str,
        step: Optional[PhaseStep],
        handle_exc: Optional[Exception],
    ) -> None:
        """每 round 落一条决策 (即使异常也落, 强复现)."""
        if self.ctx.log is None:
            return
        try:
            ts = self.ctx.ts_snapshot()
            tap = None
            tap_target = ""
            conf = 0.0
            if step and step.action and step.action.kind == "tap":
                tap = (step.action.x, step.action.y)
                tap_target = step.action.target
                conf = step.action.conf

            outcome = "exception"
            note = ""
            if handle_exc:
                note = f"{type(handle_exc).__name__}: {handle_exc}"[:400]
            elif step:
                outcome = step.outcome_hint or step.result.value
                note = step.note

            # detailed 模式: shot 透传, DecisionDetailed 异步 imwrite → img/<trace_id>.jpg
            # simple 模式: shot kwarg 被忽略 (DecisionSimple.record 接 **kwargs 吃掉)
            self.ctx.log.record(
                inst=self.ctx.instance_idx,
                phase=phase_name,
                round_idx=self.ctx.phase_round,
                outcome=outcome,
                t_round_start=ts.get("t_round_start", 0.0),
                t_capture_done=ts.get("t_capture_done", 0.0),
                t_yolo_start=ts.get("t_yolo_start", 0.0),
                t_yolo_done=ts.get("t_yolo_done", 0.0),
                t_decide=ts.get("t_decide", ts.get("t_yolo_done", 0.0)),
                t_tap_send=ts.get("t_tap_send", 0.0),
                t_tap_done=ts.get("t_tap_done", 0.0),
                tap=tap,
                tap_target=tap_target,
                conf=conf,
                trace_id=self.ctx.trace_id,
                dets_count=0,
                note=note,
                shot=self.ctx.current_shot,
            )
        except Exception as e:
            logger.debug(f"[runner] _record_decision err: {e}")

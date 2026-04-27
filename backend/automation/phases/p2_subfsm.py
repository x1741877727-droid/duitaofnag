"""
v3 P2 SubFSM — 把 perception + policy + 守门规则 粘起来.

每帧的子状态机:
  PERCEIVE → CHECK_LOBBY ↘ EXIT_OK (大厅确认 2 帧)
                          ↘ CHECK_LOGIN ↘ EXIT_FAIL (登录 60s 超时 → game_restart)
                                         ↘ DECIDE → TAP (return WAIT, executor 实施)
                                                   ↘ NONE → empty_streak++ → 死屏判定

实际不显式 enumerate 子状态, 用顺序 if/return 表达 (单帧内一次性流转完).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from ..action_executor import ActionExecutor
from ..phase_base import PhaseAction, PhaseResult, PhaseStep, RunContext
from .p2_perception import Perception, perceive
from .p2_policy import decide

logger = logging.getLogger(__name__)


# 守门常量
LOBBY_CONFIRM_NEEDED = 2          # 连续 N 帧四元判大厅 → 确认
LOGIN_TIMEOUT_SECONDS = 60.0      # 登录页停留超过此 → game_restart
EMPTY_STREAK_LIMIT = 12           # 连续 N 帧无目标 + 不在大厅 → game_restart (死屏)
SAME_TARGET_LIMIT = 3             # 连续 N 帧选中同一目标 → 加黑名单


class P2SubFSM:
    """P2 dismiss_popups 的子状态机. 每帧 step() 一次, 返回 PhaseStep."""

    async def step(self, ctx: RunContext) -> PhaseStep:
        # 1. 跑 perception
        p: Perception = await perceive(ctx)
        rnd = ctx.phase_round

        # log dets 概览 (跟 v2 等价, 便于排查 YOLO 漏检)
        if p.yolo_dets_raw:
            tops = ", ".join(
                f"{d.name}({d.conf:.2f})@({d.cx},{d.cy})"
                for d in p.yolo_dets_raw[:3]
            )
            logger.info(f"[P2/R{rnd}] dets={len(p.yolo_dets_raw)} top: {tops}")
        else:
            logger.info(f"[P2/R{rnd}] dets=0 (画面无 close_x/action_btn)")

        # 2. 大厅守门 (优先, 防被 close_x 拽出来)
        if p.quad_lobby_confirmed:
            ctx.lobby_confirm_count += 1
            if ctx.lobby_confirm_count >= LOBBY_CONFIRM_NEEDED:
                n = ActionExecutor.commit_pending_memory(ctx)
                if n:
                    logger.info(f"[P2/R{rnd}] 🧠 Memory commit {n} 条 (P2 success)")
                return PhaseStep(
                    PhaseResult.NEXT,
                    note=f"大厅确认 {ctx.lobby_confirm_count}/{LOBBY_CONFIRM_NEEDED}, "
                         f"关闭 {ctx.popups_closed} 弹窗 · {p.quad_note}",
                )
            return PhaseStep(
                PhaseResult.WAIT,
                wait_seconds=0.3,
                note=f"大厅判定 {ctx.lobby_confirm_count}/{LOBBY_CONFIRM_NEEDED} ({p.quad_note})",
            )
        else:
            ctx.lobby_confirm_count = 0

        # 3. 登录页守门 (60s 超时 → game_restart)
        if p.login_template_hit is not None:
            if ctx.login_first_seen_ts is None:
                ctx.login_first_seen_ts = time.time()
                logger.info(
                    f"[P2/R{rnd}] 见登录页 → 开始 {LOGIN_TIMEOUT_SECONDS:.0f}s 计时"
                )
            else:
                elapsed = time.time() - ctx.login_first_seen_ts
                if elapsed >= LOGIN_TIMEOUT_SECONDS:
                    return PhaseStep(
                        PhaseResult.GAME_RESTART,
                        note=f"自动登录 {elapsed:.0f}s 仍在登录页 → game_restart",
                    )
        else:
            if ctx.login_first_seen_ts is not None:
                logger.info(f"[P2/R{rnd}] 离开登录页 (登录成功) → 重置计时器")
            ctx.login_first_seen_ts = None

        # 4. 决策 — 选下一动作
        action = decide(p, ctx)

        # 5. 没目标 → empty_streak++
        if action is None:
            ctx.empty_dets_streak += 1
            # 大厅模板命中 + 持续 3 轮无目标 → 兜底判大厅成功
            # (修 quad 不通过但确实在大厅的 case)
            if (ctx.empty_dets_streak >= 3
                    and p.lobby_template_hit is not None):
                n = ActionExecutor.commit_pending_memory(ctx)
                if n:
                    logger.info(f"[P2/R{rnd}] 🧠 Memory commit {n} 条 (兜底大厅)")
                return PhaseStep(
                    PhaseResult.NEXT,
                    note=f"大厅 (兜底: 连续{ctx.empty_dets_streak}轮无目标 + 模板命中) "
                         f"· 关闭 {ctx.popups_closed} 弹窗",
                )
            # 持续无目标 + 不在大厅 → 死屏 → game_restart
            if ctx.empty_dets_streak > EMPTY_STREAK_LIMIT:
                return PhaseStep(
                    PhaseResult.GAME_RESTART,
                    note=f"连续 {ctx.empty_dets_streak} 轮无目标 + 非大厅 → 死屏",
                )
            return PhaseStep(
                PhaseResult.RETRY,
                note=f"无目标 (streak={ctx.empty_dets_streak})",
            )

        ctx.empty_dets_streak = 0

        # 6. 防死循环 — 同一目标连续 3 次 → 加黑名单
        if _same_target(action, ctx):
            ctx.same_target_count += 1
            if ctx.same_target_count >= SAME_TARGET_LIMIT:
                if not ctx.is_blacklisted(action.x, action.y):
                    ctx.blacklist_coords.append((action.x, action.y))
                    logger.warning(
                        f"[P2/R{rnd}] 同坐标 ({action.x},{action.y}) 连击 "
                        f"{ctx.same_target_count} 次 → 加黑名单"
                    )
                ctx.same_target_count = 0
                return PhaseStep(
                    PhaseResult.RETRY,
                    note=f"同坐标连击 → 加黑名单, 重选",
                )
        else:
            ctx.same_target_count = 0

        # 7. 真 tap — 返回 WAIT, executor 处理 verify + 缓冲 memory
        ctx.popups_closed += 1
        return PhaseStep(
            PhaseResult.WAIT,
            action=action,
            wait_seconds=0.3,
            note=f"tap {action.label}({action.x},{action.y})",
        )


def _same_target(action: PhaseAction, ctx: RunContext) -> bool:
    """是否跟上次 tap 同一目标 (距离 < 20px)."""
    last_x, last_y = ctx.last_tap_xy
    return abs(action.x - last_x) < 20 and abs(action.y - last_y) < 20

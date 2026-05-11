"""P5 — 等真人玩家进队.

用户确认: P5 (1101 行 v1 复杂逻辑) **现状保留**, 不动业务逻辑.
v2 包装: 委托 flows/wait_players.py (skeleton, 业务接入时直接导 v1 P5WaitPlayers).

v1 reference: backend/automation/phases/p5_wait_players.py
"""
from __future__ import annotations

import logging

from ..ctx import RunContext
from ..phase_base import PhaseStep, step_done, step_fail

logger = logging.getLogger(__name__)


class P5WaitPlayers:
    name = "P5"
    max_seconds = 600.0   # 等真人 10 分钟超时
    round_interval_s = 2.0

    async def enter(self, ctx: RunContext) -> None:
        ctx.reset_phase_state()

    async def handle_frame(self, ctx: RunContext) -> PhaseStep:
        ctx.mark("yolo_start"); ctx.mark("yolo_done"); ctx.mark("decide")

        try:
            from ..flows.wait_players import run_wait_players
            ok = await run_wait_players(ctx)
        except ImportError:
            logger.info(f"[P5/inst{ctx.instance_idx}] flows/wait_players 未实现, skeleton DONE")
            return step_done(note="P5 skeleton (业务未接入)", outcome_hint="skeleton")
        except Exception as e:
            logger.error(f"[P5/inst{ctx.instance_idx}] wait_players 异常: {e}", exc_info=True)
            return step_fail(note=f"wait_players 异常: {e}")

        if ok:
            return step_done(note="P5 完成 (真人就位)", outcome_hint="players_ready")
        return step_fail(note="wait_players 返回 False", outcome_hint="players_timeout")

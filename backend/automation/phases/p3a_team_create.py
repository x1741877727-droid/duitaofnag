"""
v3 P3a — 队长建队伍, 拿 game scheme URL.

策略: 薄壳包装 single_runner.phase_team_create (现有 OCR 7 步流程).
不拆 sub-FSM (P3a 没有 P2 那种"反复点错"问题, 拆解收益不大).

完成 → ctx.game_scheme_url 写入, runner_service._team_schemes 由 single_runner 写.
"""

from __future__ import annotations

import logging

from ..phase_base import PhaseHandler, PhaseResult, PhaseStep, RunContext

logger = logging.getLogger(__name__)


class P3aTeamCreateHandler(PhaseHandler):
    name = "P3a"
    max_rounds = 1               # 内部一次性跑完 (phase_team_create 同步)
    round_interval_s = 1.0

    async def handle_frame(self, ctx: RunContext) -> PhaseStep:
        runner = ctx.runner
        if runner is None:
            return PhaseStep(PhaseResult.FAIL, note="ctx.runner 未注入")

        try:
            scheme = await runner.phase_team_create()
        except Exception as e:
            logger.error(f"[P3a] phase_team_create 异常: {e}")
            return PhaseStep(PhaseResult.FAIL, note=f"team_create 异常: {e}")

        if scheme:
            ctx.game_scheme_url = scheme
            return PhaseStep(
                PhaseResult.NEXT,
                note=f"队伍创建成功, scheme={scheme[:50]}...",
            )
        return PhaseStep(PhaseResult.FAIL, note="队伍创建失败")

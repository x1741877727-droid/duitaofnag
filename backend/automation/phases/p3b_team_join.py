"""
v3 P3b — 队员通过队长 scheme 加入队伍.

策略: 薄壳包装 single_runner.phase_team_join.
scheme 来自 ctx.game_scheme_url (由 runner_service 在 P3 入口前从 _team_schemes 取).
"""

from __future__ import annotations

import logging

from ..phase_base import PhaseHandler, PhaseResult, PhaseStep, RunContext

logger = logging.getLogger(__name__)


class P3bTeamJoinHandler(PhaseHandler):
    name = "P3b"
    max_rounds = 1
    round_interval_s = 1.0

    async def handle_frame(self, ctx: RunContext) -> PhaseStep:
        runner = ctx.runner
        if runner is None:
            return PhaseStep(PhaseResult.FAIL, note="ctx.runner 未注入")

        scheme = ctx.game_scheme_url
        if not scheme:
            return PhaseStep(
                PhaseResult.FAIL,
                note="game_scheme_url 为空 (队长还没创建队伍 / runner_service 没注入)",
            )

        try:
            ok = await runner.phase_team_join(scheme)
        except Exception as e:
            logger.error(f"[P3b] phase_team_join 异常: {e}")
            return PhaseStep(PhaseResult.FAIL, note=f"team_join 异常: {e}")

        if ok:
            return PhaseStep(PhaseResult.DONE, note="加入队伍成功")
        return PhaseStep(PhaseResult.FAIL, note="加入队伍失败")

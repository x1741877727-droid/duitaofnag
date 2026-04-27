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

        from ..recorder_helpers import record_signal_tier
        decision = ctx.current_decision
        scheme = ctx.game_scheme_url
        if not scheme:
            record_signal_tier(decision, name="加入流程", hit=False, tier_idx=3,
                               note="scheme 为空")
            return PhaseStep(
                PhaseResult.FAIL,
                note="game_scheme_url 为空 (队长还没创建队伍 / runner_service 没注入)",
                outcome_hint="team_join_no_scheme",
            )

        try:
            ok = await runner.phase_team_join(scheme)
        except Exception as e:
            record_signal_tier(decision, name="加入流程", hit=False, tier_idx=3,
                               note=f"phase_team_join 异常: {e}")
            logger.error(f"[P3b] phase_team_join 异常: {e}")
            return PhaseStep(PhaseResult.FAIL, note=f"team_join 异常: {e}",
                             outcome_hint="team_join_exception")

        if ok:
            record_signal_tier(decision, name="加入流程", hit=True, tier_idx=3,
                               note=f"加入成功 scheme={scheme[:32]}")
            return PhaseStep(PhaseResult.DONE, note="加入队伍成功",
                             outcome_hint="team_join_ok")
        record_signal_tier(decision, name="加入流程", hit=False, tier_idx=3,
                           note="phase_team_join 返回 False")
        return PhaseStep(PhaseResult.FAIL, note="加入队伍失败",
                         outcome_hint="team_join_fail")

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
    name_cn = "队长创建"
    description = "队长建队伍 + 拿 game scheme URL (P3b 队员加入用). OCR 7 步流程, 薄壳包装现有 phase_team_create."
    flow_steps = [
        "找组队按钮 (OCR 大厅左下区)",
        "点开组队界面",
        "切到「组队码」tab",
        "点「分享队伍码」",
        "OCR 截 scheme URL",
        "回到大厅 (close 队伍面板)",
        "写 ctx.game_scheme_url, runner_service 同步给 follower 实例",
    ]
    max_rounds = 1               # 内部一次性跑完 (phase_team_create 同步)
    round_interval_s = 1.0

    async def handle_frame(self, ctx: RunContext) -> PhaseStep:
        runner = ctx.runner
        if runner is None:
            return PhaseStep(PhaseResult.FAIL, note="ctx.runner 未注入")

        from ..recorder_helpers import record_signal_tier
        from ..screen_classifier import ScreenKind, wait_for_kind
        decision = ctx.current_decision

        # 入口守门: 必须在大厅才能开始组队流程. 否则 phase_team_create 在 popup
        # 上 OCR "组队"按钮必失败, FAIL 信息不清楚. 这里先确认 LOBBY, 不在就给清晰诊断.
        kind = await wait_for_kind(ctx, accept=(ScreenKind.LOBBY,),
                                   max_attempts=5, interval_s=1.0)
        if kind != ScreenKind.LOBBY:
            record_signal_tier(decision, name="入口守门", hit=False, tier_idx=2,
                               note=f"P3a 入口非 LOBBY (kind={kind.name})")
            return PhaseStep(PhaseResult.FAIL,
                             note=f"P3a 入口守门失败: 5s 内仍非大厅 (kind={kind.name})",
                             outcome_hint=f"entry_not_lobby_{kind.value}")
        record_signal_tier(decision, name="入口守门", hit=True, tier_idx=2,
                           note=f"P3a 入口确认 LOBBY")

        try:
            scheme = await runner.phase_team_create()
        except Exception as e:
            record_signal_tier(decision, name="OCR流程", hit=False, tier_idx=3,
                               note=f"phase_team_create 异常: {e}")
            logger.error(f"[P3a] phase_team_create 异常: {e}")
            return PhaseStep(PhaseResult.FAIL, note=f"team_create 异常: {e}",
                             outcome_hint="team_create_exception")

        if scheme:
            ctx.game_scheme_url = scheme
            record_signal_tier(decision, name="OCR流程", hit=True, tier_idx=3,
                               note=f"队伍创建成功 scheme={scheme[:48]}")
            return PhaseStep(
                PhaseResult.NEXT,
                note=f"队伍创建成功, scheme={scheme[:50]}...",
                outcome_hint="team_create_ok",
            )
        record_signal_tier(decision, name="OCR流程", hit=False, tier_idx=3,
                           note="队伍创建失败 (scheme 为空)")
        return PhaseStep(PhaseResult.FAIL, note="队伍创建失败",
                         outcome_hint="team_create_fail")

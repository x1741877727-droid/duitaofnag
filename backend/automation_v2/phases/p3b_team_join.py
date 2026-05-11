"""P3b — 队员通过队长 scheme 加入队伍.

v2 设计:
- am start scheme://... 跳转游戏内组队界面, 等 UI 加入
- 薄壳委托 flows/team_join.py (skeleton)

v1 reference: backend/automation/phases/p3b_team_join.py
"""
from __future__ import annotations

import logging

from ..ctx import RunContext
from ..phase_base import PhaseStep, step_done, step_fail, step_retry

logger = logging.getLogger(__name__)


class P3bTeamJoin:
    name = "P3b"
    # member 进 P3b 时 captain 可能还在跑 P3a (OCR 找按钮慢/popup), 给 120s buffer.
    # captain 进 P4 立刻 emit scheme_ready, master 即时 broadcast 给 member workers.
    max_seconds = 120.0
    round_interval_s = 1.0

    async def enter(self, ctx: RunContext) -> None:
        ctx.reset_phase_state()

    async def handle_frame(self, ctx: RunContext) -> PhaseStep:
        ctx.mark("yolo_start"); ctx.mark("yolo_done"); ctx.mark("decide")

        scheme = ctx.game_scheme_url
        if not scheme:
            # runner_service 后台 watcher 会异步写 scheme. retry 等, 不直接 FAIL.
            # max_seconds=30 (在 class 上设) 内拿不到才超时 FAIL.
            return step_retry(note="等 captain scheme", outcome_hint="wait_scheme")

        # TODO 业务: am start scheme + UI 确认加入
        try:
            from ..flows.team_join import run_team_join
            ok = await run_team_join(ctx, scheme)
        except ImportError:
            logger.info(f"[P3b/inst{ctx.instance_idx}] flows/team_join 未实现, skeleton DONE")
            return step_done(note="P3b skeleton (业务未接入)", outcome_hint="skeleton")
        except Exception as e:
            logger.error(f"[P3b/inst{ctx.instance_idx}] team_join 异常: {e}", exc_info=True)
            return step_fail(note=f"team_join 异常: {e}")

        if ok:
            return step_done(note=f"加入成功 scheme={scheme[:32]}", outcome_hint="team_join_ok")
        return step_fail(note="team_join 返回 False", outcome_hint="team_join_fail")

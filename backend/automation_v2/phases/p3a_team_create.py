"""P3a — 队长建队伍, 拿 game scheme URL.

v2 设计:
- P3a 流程业务复杂 (OCR 7 步), 重写收益低
- v2 phase 做薄壳: 委托给 flows/team_create.py 跑业务
- flows/team_create.py 当前是 TODO skeleton, 业务接入时再填

v1 reference: backend/automation/phases/p3a_team_create.py (薄壳 → single_runner.phase_team_create)
"""
from __future__ import annotations

import logging

from ..ctx import RunContext
from ..phase_base import PhaseStep, step_next, step_fail

logger = logging.getLogger(__name__)


class P3aTeamCreate:
    name = "P3a"
    max_seconds = 60.0
    round_interval_s = 1.0

    async def enter(self, ctx: RunContext) -> None:
        ctx.reset_phase_state()

    async def handle_frame(self, ctx: RunContext) -> PhaseStep:
        # 时间戳填空 (P3a 一次性跑完, 不依赖 yolo/tap)
        ctx.mark("yolo_start"); ctx.mark("yolo_done"); ctx.mark("decide")

        # TODO 业务接入: 跑 OCR 7 步流程, 拿 scheme URL
        # 当前 skeleton 模式: 直接 NEXT (跳过 P3a, 让业务实现后再填)
        # 业务接入时:
        #   from ..flows.team_create import run_team_create
        #   scheme = await run_team_create(ctx)
        #   if scheme: ctx.game_scheme_url = scheme; return step_next(...)
        #   return step_fail(...)
        try:
            from ..flows.team_create import run_team_create
            scheme = await run_team_create(ctx)
        except ImportError:
            logger.info(f"[P3a/inst{ctx.instance_idx}] flows/team_create 未实现, skeleton NEXT")
            return step_next(note="P3a skeleton (业务未接入)", outcome_hint="skeleton")
        except Exception as e:
            logger.error(f"[P3a/inst{ctx.instance_idx}] team_create 异常: {e}", exc_info=True)
            return step_fail(note=f"team_create 异常: {e}")

        if scheme:
            ctx.game_scheme_url = scheme
            return step_next(
                note=f"队伍创建成功 scheme={scheme[:48]}",
                outcome_hint="team_create_ok",
            )
        return step_fail(note="team_create 返回空 scheme", outcome_hint="team_create_fail")

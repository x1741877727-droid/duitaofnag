"""flows/team_join — v1 phase_team_join 薄壳 wrapper.

v1 实现: backend/automation/single_runner.py:1595

历史: 试过 instance_idx × 0.4s stagger 防 ADB 队列撞, 实测无效 —
单实例独占调 am start 也要 19s. 慢的不是 ADB, 是 PUBG 处理 deep link
跳转组队界面的内部延迟. 撤回 stagger.
"""
from __future__ import annotations

import logging

from ..ctx import RunContext

logger = logging.getLogger(__name__)


async def run_team_join(ctx: RunContext, scheme: str) -> bool:
    """队员通过 scheme 加入队伍. 返 True=成功."""
    if ctx.v1_runner is None:
        logger.warning(f"[flow/team_join inst{ctx.instance_idx}] ctx.v1_runner 未注入")
        return False
    try:
        return bool(await ctx.v1_runner.phase_team_join(scheme))
    except Exception as e:
        logger.error(f"[flow/team_join inst{ctx.instance_idx}] v1 phase_team_join 抛: {e}",
                     exc_info=True)
        return False

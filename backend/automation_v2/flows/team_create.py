"""flows/team_create — v1 phase_team_create 薄壳 wrapper.

Day 4 灰度策略 (Option B): 不重写 v1 600 行 OCR 流程, 走 ctx.v1_runner.phase_team_create()
直接拿 scheme URL. Day 5+ 真重构时再独立这套.

v1 实现: backend/automation/single_runner.py:1062
依赖 ctx.v1_runner: SingleInstanceRunner 实例 (runner_service 装 v2 ctx 时注入)
"""
from __future__ import annotations

import logging
from typing import Optional

from ..ctx import RunContext

logger = logging.getLogger(__name__)


async def run_team_create(ctx: RunContext) -> Optional[str]:
    """跑队长建队伍流程, 返回 scheme URL (None=失败)."""
    if ctx.v1_runner is None:
        logger.warning(f"[flow/team_create inst{ctx.instance_idx}] ctx.v1_runner 未注入")
        return None
    try:
        return await ctx.v1_runner.phase_team_create()
    except Exception as e:
        logger.error(f"[flow/team_create inst{ctx.instance_idx}] v1 phase_team_create 抛: {e}",
                     exc_info=True)
        return None

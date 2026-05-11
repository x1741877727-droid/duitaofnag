"""flows/map_setup — v1 phase_map_setup 薄壳 wrapper.

v1 实现: backend/automation/single_runner.py:442
"""
from __future__ import annotations

import logging

from ..ctx import RunContext

logger = logging.getLogger(__name__)


async def run_map_setup(ctx: RunContext) -> bool:
    """选模式 + 选地图 + 准备 + 开始. 返 True=成功."""
    if ctx.v1_runner is None:
        logger.warning(f"[flow/map_setup inst{ctx.instance_idx}] ctx.v1_runner 未注入")
        return False
    try:
        return bool(await ctx.v1_runner.phase_map_setup())
    except Exception as e:
        logger.error(f"[flow/map_setup inst{ctx.instance_idx}] v1 phase_map_setup 抛: {e}",
                     exc_info=True)
        return False

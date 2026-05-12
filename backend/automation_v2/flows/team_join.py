"""flows/team_join — v1 phase_team_join 薄壳 wrapper.

v1 实现: backend/automation/single_runner.py:1595

性能优化: 多 member 并发 am start scheme://, ADB server 串行化导致
单调用从 ~500ms 阻塞到 ~20s. 按 instance_idx 错开 0.4s/实例
避免 4 个 ADB subprocess 同时 fork 撞 server 队列.
"""
from __future__ import annotations

import asyncio
import logging

from ..ctx import RunContext

logger = logging.getLogger(__name__)

# am start stagger: 每实例错开 0.4s, 4 个 member 最坏 inst_idx=6 → 2.4s 延迟,
# 换来单 am start 从 20s → ~1s (实测 v1 单实例时 am start ~500ms).
AM_START_STAGGER_PER_IDX = 0.4


async def run_team_join(ctx: RunContext, scheme: str) -> bool:
    """队员通过 scheme 加入队伍. 返 True=成功."""
    if ctx.v1_runner is None:
        logger.warning(f"[flow/team_join inst{ctx.instance_idx}] ctx.v1_runner 未注入")
        return False

    # 按 instance_idx 错开 am start, 防多实例同时 fork ADB subprocess 撞队列
    stagger = ctx.instance_idx * AM_START_STAGGER_PER_IDX
    if stagger > 0:
        logger.info(f"[flow/team_join inst{ctx.instance_idx}] stagger {stagger:.1f}s 防 ADB 撞队列")
        await asyncio.sleep(stagger)

    try:
        return bool(await ctx.v1_runner.phase_team_join(scheme))
    except Exception as e:
        logger.error(f"[flow/team_join inst{ctx.instance_idx}] v1 phase_team_join 抛: {e}",
                     exc_info=True)
        return False

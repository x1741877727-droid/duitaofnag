"""PUBG crash 检测 middleware — adb pidof 探活.

骨架版本 (Day 3): 接口完整, 业务逻辑 TODO.

REVIEW_DAY3_WATCHDOG.md: PUBG 突然崩退到桌面 → P1-P5 业务永远跑不下去.
检测方法: adb shell pidof com.tencent.tmgp.pubgmhd 返空 → crash.

接入业务时填:
1. 检测频率 (每 N round? 每 N 秒?)
2. crash 处理: 抛 GAME_RESTART exception 让 runner_fsm 走 GAME_RESTART 路径
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from ..ctx import RunContext
from .base import BeforeRoundResult

logger = logging.getLogger(__name__)

PUBG_PACKAGE = "com.tencent.tmgp.pubgmhd"
# 不每 round 都查 (adb shell 100ms 开销), 节流 10s 一次
_CHECK_INTERVAL_S = 10.0


class CrashCheckMiddleware:
    """检测 PUBG crash. 只对 P1-P5 启用 (P0 还没启游戏)."""

    name = "crash_check"

    def __init__(self):
        self._last_check_ts: dict[int, float] = {}

    def enable_for(self, phase_name: str) -> bool:
        """P0 还没启游戏, 不检查. P1-P5 都检查."""
        return phase_name != "P0"

    async def before_round(self, ctx: RunContext, shot) -> BeforeRoundResult:
        """每 _CHECK_INTERVAL_S 秒 adb pidof 探活."""
        now = time.time()
        last = self._last_check_ts.get(ctx.instance_idx, 0)
        if now - last < _CHECK_INTERVAL_S:
            return BeforeRoundResult(intercept=False)
        self._last_check_ts[ctx.instance_idx] = now

        # 检测 PUBG 进程 (TODO 业务接入)
        alive = await self._check_pubg_alive(ctx)
        if alive:
            return BeforeRoundResult(intercept=False)

        # crash 检测到 → 业务怎么处理? (TODO)
        logger.warning(f"[middleware/crash] inst{ctx.instance_idx} PUBG 进程不存在!")
        return BeforeRoundResult(intercept=False, note="pubg_crashed")

    async def after_phase(self, ctx: RunContext) -> None:
        pass

    # ─────────── 业务接入点 (TODO) ───────────

    async def _check_pubg_alive(self, ctx: RunContext) -> bool:
        """检查 PUBG 进程是否在跑.

        TODO (业务接入):
            rc, out = await ctx.adb.shell(f"pidof {PUBG_PACKAGE}", timeout=2.0)
            return rc == 0 and out.strip() != ""

        当前 stub 返 True (默认活, 不影响业务).
        """
        return True

"""
v3 P0 — 启动 FightMaster VPN + 4 信号校验.

策略 (沿用 v2):
  1. 已连接 → NEXT (秒过)
  2. 广播启动 → 8s 内连上 → NEXT
  3. UI 启动 (3 次重试, 每次 10s 等待) → NEXT
  4. 全失败 → FAIL (runner_service 重试)

不写新 VPN 检测逻辑, 直接复用 single_runner._check_vpn_connected (4 信号).
"""

from __future__ import annotations

import asyncio
import logging
import time

from ..phase_base import PhaseAction, PhaseHandler, PhaseResult, PhaseStep, RunContext

logger = logging.getLogger(__name__)


class P0AcceleratorHandler(PhaseHandler):
    """启动 FightMaster VPN 并确认连接 (4 信号校验)."""

    name = "P0"
    max_rounds = 1                # 一帧内同步执行完 (内部 loop 等待)
    round_interval_s = 0.5

    async def handle_frame(self, ctx: RunContext) -> PhaseStep:
        runner = ctx.runner
        if runner is None:
            return PhaseStep(PhaseResult.FAIL, note="ctx.runner 未注入")

        # ── 快速路径: VPN 已连接 ──
        if await runner._check_vpn_connected():
            logger.info("[P0] FightMaster 已连接 ✓ 跳过启动")
            return PhaseStep(PhaseResult.NEXT, note="vpn already connected")

        # ── 广播启动 (1-8s 快路径) ──
        try:
            await runner._start_vpn()
        except Exception as e:
            logger.warning(f"[P0] _start_vpn 异常: {e}")
        if await runner._wait_vpn_connected(timeout=8):
            logger.info("[P0] FightMaster 广播启动成功 ✓")
            return PhaseStep(PhaseResult.NEXT, note="vpn broadcast ok")

        # ── UI 启动 (3 次重试) ──
        logger.warning("[P0] 广播启动失败, 切换 UI 模式")
        for retry in range(3):
            if retry > 0:
                logger.info(f"[P0] UI 模式第 {retry + 1} 次重试")
                try:
                    await runner._stop_vpn()
                except Exception:
                    pass
                await asyncio.sleep(2)

            try:
                await runner._start_vpn_via_ui()
            except Exception as e:
                logger.warning(f"[P0] _start_vpn_via_ui 异常: {e}")
                continue

            if await runner._wait_vpn_connected(timeout=10):
                logger.info(f"[P0] FightMaster UI 启动成功 ✓ (retry={retry})")
                return PhaseStep(PhaseResult.NEXT, note=f"vpn ui ok (retry={retry})")

        logger.error("[P0] 所有方式均失败")
        return PhaseStep(PhaseResult.FAIL, note="vpn 全部失败")

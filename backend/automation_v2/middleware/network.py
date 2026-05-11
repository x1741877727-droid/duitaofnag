"""网络异常 middleware — 网络断开 / 服务器繁忙 / 排队等弹窗.

骨架版本 (Day 3): 接口完整, 业务逻辑 TODO.

REVIEW_DAY3_WATCHDOG.md: 网络断开 PUBG 会弹"服务器异常请重试"等, 业务卡死.
检测: YOLO 'network_error' class / OCR "网络/连接/重试" 关键词.
处理: tap 重试按钮 / 退到大厅重连.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from ..ctx import RunContext
from .base import BeforeRoundResult

logger = logging.getLogger(__name__)


class NetworkErrorMiddleware:
    """网络异常弹窗自动处理. 所有 phase 启用."""

    name = "network_error"

    def __init__(self):
        self._last_dismiss_ts: dict[int, float] = {}

    def enable_for(self, phase_name: str) -> bool:
        return True

    async def before_round(self, ctx: RunContext, shot) -> BeforeRoundResult:
        if shot is None:
            return BeforeRoundResult(intercept=False)

        # 节流 1s
        now = time.time()
        if now - self._last_dismiss_ts.get(ctx.instance_idx, 0) < 1.0:
            return BeforeRoundResult(intercept=False)

        # 检测网络弹窗 (TODO)
        err = await self._detect_network_error(ctx, shot)
        if err is None:
            return BeforeRoundResult(intercept=False)

        ok = await self._dismiss_network_error(ctx, err)
        self._last_dismiss_ts[ctx.instance_idx] = now

        if ok:
            logger.info(f"[middleware/network] inst{ctx.instance_idx} 关闭网络异常弹窗")
            return BeforeRoundResult(intercept=True, note="network error dismissed")
        return BeforeRoundResult(intercept=False, note="network error detected but failed")

    async def after_phase(self, ctx: RunContext) -> None:
        pass

    # ─────────── 业务接入点 (TODO) ───────────

    async def _detect_network_error(self, ctx: RunContext, shot) -> Optional[dict]:
        """检测网络异常弹窗.

        TODO (业务接入):
        - YOLO 检测 'network_error_dialog' class
        - OCR 关键词: "网络"/"连接"/"重试"/"服务器繁忙"
        """
        return None

    async def _dismiss_network_error(self, ctx: RunContext, err: dict) -> bool:
        """关闭/重试.

        TODO (业务接入):
        - tap "重试" 按钮 OR "返回大厅" 按钮 (取决业务策略)
        """
        return False

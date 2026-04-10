"""
GuardedADB — 截图守卫层
包装 ADBController，每次截图自动检测并清除弹窗遮罩。
所有阶段代码零修改，透明拦截。
"""

import asyncio
import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


class GuardedADB:
    """
    透明代理 ADBController。
    screenshot() 返回前自动检查遮罩层，有弹窗就点掉再返回干净画面。
    dismiss_popups 阶段可以临时关闭守卫（它自己处理弹窗）。
    """

    def __init__(self, adb, dismisser, matcher=None):
        self._adb = adb
        self._dismisser = dismisser
        self._matcher = matcher
        self.guard_enabled = True

    async def screenshot(self) -> Optional[np.ndarray]:
        shot = await self._adb.screenshot()
        if shot is None or not self.guard_enabled:
            return shot

        for attempt in range(3):
            if not self._dismisser._has_overlay(shot):
                return shot
            target = self._dismisser._find_close_target(shot, self._matcher)
            if not target:
                return shot  # 有遮罩但找不到关闭目标，原样返回
            x, y, method = target
            logger.info(f"[守卫] 自动清除弹窗: {method} @ ({x},{y})")
            await self._adb.tap(x, y)
            await asyncio.sleep(0.8)
            shot = await self._adb.screenshot()
            if shot is None:
                return None
        return shot

    # ── 透传所有其他方法 ──

    async def tap(self, x: int, y: int):
        await self._adb.tap(x, y)

    async def key_event(self, key: str):
        await self._adb.key_event(key)

    async def start_app(self, package: str, activity: str = ""):
        await self._adb.start_app(package, activity)

    async def stop_app(self, package: str):
        await self._adb.stop_app(package)

    async def get_clipboard(self) -> str:
        return await self._adb.get_clipboard()

    async def set_clipboard(self, text: str):
        await self._adb.set_clipboard(text)

    async def open_url(self, url: str):
        await self._adb.open_url(url)

    # 暴露底层属性（runner_service 需要）
    @property
    def serial(self):
        return self._adb.serial

    @property
    def adb_path(self):
        return self._adb.adb_path

"""
Handler 基类
所有状态处理器的公共接口和工具方法
"""

import asyncio
import logging
import time
from typing import Optional

import numpy as np

from ..adb.controller import ADBController
from ..recognition.pipeline import RecognitionPipeline

logger = logging.getLogger(__name__)


class HandlerResult:
    """Handler 执行结果"""

    def __init__(self, trigger: str, data: Optional[dict] = None, error: str = ""):
        """
        Args:
            trigger: 下一个状态机触发器名称（如 "login_ok", "banned"）
            data: 附加数据（传给协调器或下一个 handler）
            error: 错误信息
        """
        self.trigger = trigger
        self.data = data or {}
        self.error = error

    @property
    def success(self) -> bool:
        return not self.error


class BaseHandler:
    """
    Handler 基类
    提供截图、等待、超时检测等公共方法
    """

    def __init__(self, ctrl: ADBController, pipeline: RecognitionPipeline,
                 instance_index: int, timeout: float = 30.0,
                 poll_interval: float = 1.0):
        self.ctrl = ctrl
        self.pipeline = pipeline
        self.instance_index = instance_index
        self.timeout = timeout
        self.poll_interval = poll_interval

    async def execute(self) -> HandlerResult:
        """
        子类实现：执行状态处理逻辑
        返回 HandlerResult 指示下一步动作
        """
        raise NotImplementedError

    async def take_screenshot(self) -> Optional[np.ndarray]:
        """截图"""
        return await self.ctrl.screenshot()

    async def tap(self, x: int, y: int):
        """点击"""
        return await self.ctrl.tap(x, y)

    async def wait_and_poll(self, check_fn, timeout: Optional[float] = None,
                            interval: Optional[float] = None):
        """
        循环截图+检测，直到 check_fn 返回非 None 或超时
        Args:
            check_fn: async fn(screenshot) -> result or None
            timeout: 超时秒数
            interval: 轮询间隔
        Returns:
            check_fn 的返回值，超时返回 None
        """
        timeout = timeout or self.timeout
        interval = interval or self.poll_interval
        start = time.time()

        while time.time() - start < timeout:
            img = await self.take_screenshot()
            if img is None:
                await asyncio.sleep(interval)
                continue

            result = await check_fn(img)
            if result is not None:
                return result

            await asyncio.sleep(interval)

        return None

    def log(self, msg: str, level: str = "info"):
        """带实例前缀的日志"""
        prefix = f"[实例{self.instance_index}]"
        getattr(logger, level)(f"{prefix} {msg}")

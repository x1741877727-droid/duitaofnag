"""
PopupDismisser — 弹窗循环清理模块
基于真实截图分析的弹窗处理策略：模板X → OCR关键词 → 点击屏幕中央
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional, Protocol

import numpy as np

from .screen_matcher import MatchHit, ScreenMatcher

logger = logging.getLogger(__name__)


class ADBDevice(Protocol):
    """ADB设备接口（duck typing）"""
    async def screenshot(self) -> Optional[np.ndarray]: ...
    async def tap(self, x: int, y: int): ...
    async def key_event(self, key: str): ...


@dataclass
class DismissResult:
    """弹窗清理结果"""
    success: bool           # 是否成功到达大厅
    popups_closed: int      # 关闭的弹窗数量
    loops: int              # 循环次数
    duration_ms: int        # 耗时
    final_state: str        # 最终状态: "lobby" | "timeout" | "error"


# OCR关键词 → 点击策略
# (关键词, 优先级, 是否先勾选"不再显示")
OCR_KEYWORDS_DISMISS = [
    # 先勾选"不再显示"类选项
    ("今日内不再弹出", 10, True),
    ("不再提醒", 10, True),
    ("不再显示", 10, True),
    # 然后点击关闭类按钮
    ("确定", 5, False),
    ("同意", 5, False),
    ("暂不需要", 5, False),
    ("领取", 4, False),
    ("点击屏幕继续", 3, False),
    ("点击继续", 3, False),
]


class PopupDismisser:
    """
    弹窗循环清理器

    策略:
    1. 截图 → 检测是否在大厅 → 是则退出
    2. 模板匹配X按钮 → 命中则点击
    3. 模板匹配操作按钮（确定/同意/加入等）→ 命中则点击
    4. （可选）OCR扫描关键词 → 命中则点击对应位置
    5. 都没命中 → 点击屏幕中央（兜底）
    6. 重复，最多max_loops次
    """

    def __init__(
        self,
        matcher: ScreenMatcher,
        max_loops: int = 20,
        lobby_confirm_count: int = 3,
        interval: float = 1.0,
        ocr_reader=None,  # 可选的OCR模块
    ):
        self.matcher = matcher
        self.max_loops = max_loops
        self.lobby_confirm_count = lobby_confirm_count
        self.interval = interval
        self.ocr = ocr_reader

    async def dismiss_all(self, device: ADBDevice) -> DismissResult:
        """
        执行弹窗清理循环，直到到达大厅或超时

        Args:
            device: ADB设备（需要 screenshot/tap 方法）

        Returns:
            DismissResult
        """
        start = time.time()
        lobby_count = 0
        popups_closed = 0

        for loop in range(self.max_loops):
            # 1. 截图
            screenshot = await device.screenshot()
            if screenshot is None:
                logger.warning(f"[弹窗清理] 第{loop+1}轮: 截图失败")
                await asyncio.sleep(self.interval)
                continue

            # 2. 检测大厅
            if self.matcher.is_at_lobby(screenshot):
                lobby_count += 1
                logger.info(f"[弹窗清理] 检测到大厅 ({lobby_count}/{self.lobby_confirm_count})")
                if lobby_count >= self.lobby_confirm_count:
                    duration = int((time.time() - start) * 1000)
                    logger.info(f"[弹窗清理] 完成! 关闭{popups_closed}个弹窗, {loop+1}轮, {duration}ms")
                    return DismissResult(True, popups_closed, loop + 1, duration, "lobby")
                await asyncio.sleep(self.interval)
                continue
            else:
                lobby_count = 0

            # 3. 模板匹配X按钮
            x_hit = self.matcher.find_close_button(screenshot)
            if x_hit:
                logger.info(f"[弹窗清理] 匹配到X按钮: {x_hit.name} ({x_hit.confidence:.2f}) @ ({x_hit.cx},{x_hit.cy})")
                await device.tap(x_hit.cx, x_hit.cy)
                popups_closed += 1
                await asyncio.sleep(self.interval)
                continue

            # 4. 模板匹配操作按钮
            btn_hit = self.matcher.find_action_button(screenshot)
            if btn_hit:
                logger.info(f"[弹窗清理] 匹配到按钮: {btn_hit.name} ({btn_hit.confidence:.2f}) @ ({btn_hit.cx},{btn_hit.cy})")
                await device.tap(btn_hit.cx, btn_hit.cy)
                popups_closed += 1
                await asyncio.sleep(self.interval)
                continue

            # 5. 检测"点击屏幕继续"模板
            click_cont = self.matcher.match_one(screenshot, "text_click_continue", threshold=0.70)
            if click_cont:
                logger.info(f"[弹窗清理] 检测到'点击屏幕继续'")
                await device.tap(640, 400)  # 屏幕中央
                popups_closed += 1
                await asyncio.sleep(0.5)  # 这类弹窗切换快
                continue

            # 6. 兜底：交替点击不同位置
            # 注意：游戏内不能用返回键，会弹"退出游戏"对话框
            # 对于无X按钮的弹窗（如摸金杯），尝试点击弹窗外围区域关闭
            fallback_positions = [
                (640, 400),   # 屏幕中央
                (100, 650),   # 左下角（弹窗外）
                (1200, 650),  # 右下角（弹窗外）
                (640, 680),   # 底部中央
            ]
            pos = fallback_positions[loop % len(fallback_positions)]
            logger.debug(f"[弹窗清理] 第{loop+1}轮: 无匹配, 点击{pos}")
            await device.tap(pos[0], pos[1])
            await asyncio.sleep(self.interval)

        duration = int((time.time() - start) * 1000)
        logger.warning(f"[弹窗清理] 超时! {self.max_loops}轮后仍未到大厅")
        return DismissResult(False, popups_closed, self.max_loops, duration, "timeout")

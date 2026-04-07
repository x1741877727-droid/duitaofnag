"""
弹窗处理器
循环检测并关闭所有弹窗（活动公告、广告、提示框）
每次版本更新弹窗样式不同，以 LLM 视觉为主力
"""

import asyncio

from .base import BaseHandler, HandlerResult


class PopupHandler(BaseHandler):
    """
    DISMISS_POPUPS 状态处理器
    - 循环截图 → 检测弹窗 → 关闭
    - 连续 N 次未检测到弹窗 → 认为清理完成
    """

    def __init__(self, *args, max_no_popup_count: int = 3, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_no_popup_count = max_no_popup_count  # 连续几次无弹窗认为清理完成

    async def execute(self) -> HandlerResult:
        self.log("开始清理弹窗...")
        no_popup_count = 0
        total_closed = 0

        while no_popup_count < self.max_no_popup_count:
            img = await self.take_screenshot()
            if img is None:
                await asyncio.sleep(self.poll_interval)
                continue

            # 用管道检测弹窗（模板匹配 → LLM）
            result = await self.pipeline.detect_popup(img)

            if result.success and result.click_target:
                x, y = result.click_target
                source = result.level.value
                self.log(f"检测到弹窗 ({source})，点击关闭 ({x}, {y})")
                await self.tap(x, y)
                total_closed += 1
                no_popup_count = 0
                # 关闭后等一下让动画消失
                await asyncio.sleep(0.8)
            else:
                no_popup_count += 1
                self.log(f"未检测到弹窗 ({no_popup_count}/{self.max_no_popup_count})")
                await asyncio.sleep(self.poll_interval)

            # 超时保护
            if self._check_timeout():
                self.log(f"弹窗清理超时，已关闭 {total_closed} 个", "warn")
                break

        self.log(f"弹窗清理完成，共关闭 {total_closed} 个")
        return HandlerResult(
            trigger="popups_cleared",
            data={"popups_closed": total_closed},
        )

    def _check_timeout(self) -> bool:
        import time
        # 弹窗清理给更长的超时
        return False  # 由 wait_and_poll 的外层超时控制

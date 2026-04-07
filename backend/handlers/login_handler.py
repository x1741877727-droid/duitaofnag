"""
登录检测处理器
检查游戏是否自动登录成功，失败则告警
"""

from .base import BaseHandler, HandlerResult


class LoginHandler(BaseHandler):
    """
    LOGIN_CHECK 状态处理器
    - 等待游戏自动登录完成
    - 检测是否进入大厅
    - 如果卡在登录页面超时，报错
    """

    async def execute(self) -> HandlerResult:
        self.log("检查自动登录...")

        async def check_login(img):
            # 1. 模板匹配大厅特征 → 登录成功
            result = self.pipeline.template.match_any(img, category="lobby")
            if result and result.matched:
                return "lobby"

            # 2. 模板匹配登录页面特征 → 还在登录
            result = self.pipeline.template.match_any(img, category="login")
            if result and result.matched:
                return None  # 继续等待

            # 3. LLM 判断当前状态
            state_info = await self.pipeline.detect_state(img)
            if state_info.success and state_info.data.get("state") == "lobby":
                return "lobby"

            # 检查是否有错误弹窗
            popup = await self.pipeline.detect_popup(img)
            if popup.success and popup.click_target:
                # 有弹窗，可能是登录错误提示
                self.log(f"检测到弹窗，尝试关闭", "warn")
                x, y = popup.click_target
                await self.tap(x, y)

            return None

        result = await self.wait_and_poll(check_login, timeout=self.timeout)

        if result == "lobby":
            self.log("自动登录成功，已进入大厅")
            return HandlerResult(trigger="login_ok")
        else:
            self.log("自动登录超时，可能需要手动登录", "error")
            return HandlerResult(
                trigger="unknown_error",
                error="自动登录超时",
            )

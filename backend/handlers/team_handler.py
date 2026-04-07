"""
组队处理器
Captain: 打开好友列表 → 生成组队二维码 → 通知协调器
Member: 等待协调器传递链接 → ADB intent 加入队伍
"""

import asyncio
from typing import Optional

from .base import BaseHandler, HandlerResult

try:
    from pyzbar.pyzbar import decode as decode_qr
except ImportError:
    decode_qr = None


class TeamCreateHandler(BaseHandler):
    """
    TEAM_CREATE 状态处理器（Captain）
    1. 点击好友列表
    2. 找到生成组队二维码按钮并点击
    3. 截图 → 解码二维码 → 提取链接
    4. 返回链接给协调器分发
    """

    async def execute(self) -> HandlerResult:
        self.log("开始创建队伍...")

        # 1. 点击好友列表入口
        img = await self.take_screenshot()
        if img is None:
            return HandlerResult(trigger="unknown_error", error="截图失败")

        # 模板匹配好友列表按钮
        friend_btn = await self.pipeline.find_and_click(
            img,
            template_key="lobby/friend_list",
            text_keyword="好友",
        )
        if friend_btn.success and friend_btn.click_target:
            await self.tap(*friend_btn.click_target)
            await asyncio.sleep(1.0)
        else:
            # LLM 兜底
            self.log("使用 LLM 查找好友列表入口")
            question = (
                "这是游戏大厅截图。请找到好友列表或社交按钮的位置。"
                '回复 JSON: {"click_x": x, "click_y": y}'
            )
            llm = await self.pipeline.llm.analyze(img, "custom", question)
            if llm.success and llm.parsed:
                x = llm.parsed.get("click_x", 0)
                y = llm.parsed.get("click_y", 0)
                if x and y:
                    await self.tap(x, y)
                    await asyncio.sleep(1.0)

        # 2. 找到并点击"生成组队二维码"按钮
        img = await self.take_screenshot()
        if img is None:
            return HandlerResult(trigger="unknown_error", error="截图失败")

        qr_btn = await self.pipeline.find_and_click(
            img,
            template_key="team/generate_qr",
            text_keyword="二维码",
        )
        if qr_btn.success and qr_btn.click_target:
            await self.tap(*qr_btn.click_target)
            await asyncio.sleep(1.5)  # 等待二维码生成

        # 3. 截图解码二维码
        qr_url = await self._decode_qr_from_screen()
        if qr_url:
            self.log(f"组队二维码解码成功: {qr_url[:50]}...")
            return HandlerResult(
                trigger="team_created",
                data={"qr_url": qr_url},
            )
        else:
            self.log("二维码解码失败", "error")
            return HandlerResult(
                trigger="unknown_error",
                error="无法解码组队二维码",
            )

    async def _decode_qr_from_screen(self) -> Optional[str]:
        """截图并解码二维码"""
        img = await self.take_screenshot()
        if img is None:
            return None

        if decode_qr is None:
            self.log("pyzbar 未安装，使用 LLM 读取二维码", "warn")
            # LLM fallback
            question = (
                "这张截图中有一个组队二维码。请读取二维码中包含的链接/URL。"
                '回复 JSON: {"url": "二维码内容"}'
            )
            result = await self.pipeline.llm.analyze(img, "custom", question)
            if result.success and result.parsed:
                return result.parsed.get("url", "")
            return None

        # pyzbar 解码
        import cv2
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        decoded = decode_qr(gray)
        for obj in decoded:
            data = obj.data.decode("utf-8", errors="ignore")
            if data.startswith("http") or "team" in data.lower() or "join" in data.lower():
                return data

        # 没找到有效链接，尝试返回第一个解码结果
        if decoded:
            return decoded[0].data.decode("utf-8", errors="ignore")

        return None


class TeamJoinHandler(BaseHandler):
    """
    TEAM_JOIN 状态处理器（Member）
    等待协调器传递组队链接 → ADB intent 打开链接加入队伍
    """

    def __init__(self, *args, join_url: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self.join_url = join_url

    async def execute(self) -> HandlerResult:
        if not self.join_url:
            self.log("等待组队链接...", "warn")
            return HandlerResult(trigger="unknown_error", error="未收到组队链接")

        self.log(f"通过链接加入队伍: {self.join_url[:50]}...")

        # 通过 ADB intent 打开链接
        await self.ctrl.open_url(self.join_url)
        await asyncio.sleep(2.0)

        # 验证是否成功加入
        async def check_joined(img):
            # 检测组队界面特征
            result = self.pipeline.template.match_any(img, category="team")
            if result and result.matched:
                return True

            # LLM 确认
            state = await self.pipeline.detect_state(img)
            if state.success and state.data.get("state") == "team":
                return True

            return None

        joined = await self.wait_and_poll(check_joined, timeout=15)

        if joined:
            self.log("成功加入队伍")
            return HandlerResult(trigger="team_joined")
        else:
            self.log("加入队伍超时", "error")
            return HandlerResult(trigger="unknown_error", error="加入队伍超时")

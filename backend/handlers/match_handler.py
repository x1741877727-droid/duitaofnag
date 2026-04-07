"""
匹配处理器
等待匹配、检测匹配状态、处理匹配超时
还有等待真人玩家和准备检查
"""

import asyncio

from .base import BaseHandler, HandlerResult


class WaitPlayersHandler(BaseHandler):
    """
    WAIT_PLAYERS 状态处理器
    循环检测是否有真人玩家加入组队
    """

    def __init__(self, *args, required_player_count: int = 2, **kwargs):
        super().__init__(*args, **kwargs)
        self.required_count = required_player_count

    async def execute(self) -> HandlerResult:
        self.log(f"等待 {self.required_count} 名真人玩家加入...")

        async def check_players(img):
            # 用 LLM 检测队伍中人数
            question = (
                f"这是游戏组队界面。请数一下队伍中有多少名玩家（包括机器人和真人）。"
                f"回复 JSON: {{\"player_count\": 数量, \"ready_count\": 已准备数量}}"
            )
            result = await self.pipeline.llm.analyze(img, "custom", question)
            if result.success and result.parsed:
                count = result.parsed.get("player_count", 0)
                # 队伍中应该有: bot 数量 + 真人数量
                # 这里简化判断：总人数 >= 所需即可
                if count >= self.required_count + 1:  # +1 是自己
                    return True
            return None

        result = await self.wait_and_poll(check_players, timeout=self.timeout)

        if result:
            self.log("真人玩家已加入")
            return HandlerResult(trigger="players_joined")
        else:
            self.log("等待玩家超时", "warn")
            return HandlerResult(
                trigger="unknown_error",
                error="等待真人玩家超时",
            )


class ReadyCheckHandler(BaseHandler):
    """
    READY_CHECK 状态处理器
    检查所有队友是否已准备
    """

    async def execute(self) -> HandlerResult:
        self.log("检查队友准备状态...")

        async def check_ready(img):
            question = (
                "这是游戏组队界面。检查所有队友的准备状态。"
                "回复 JSON: {\"all_ready\": true/false, "
                "\"not_ready\": [\"未准备的玩家名\"], "
                "\"disconnected\": [\"掉线的玩家名\"]}"
            )
            result = await self.pipeline.llm.analyze(img, "custom", question)
            if result.success and result.parsed:
                if result.parsed.get("all_ready"):
                    return "ready"

                not_ready = result.parsed.get("not_ready", [])
                disconnected = result.parsed.get("disconnected", [])
                if disconnected:
                    self.log(f"有玩家掉线: {disconnected}", "warn")
                if not_ready:
                    self.log(f"有玩家未准备: {not_ready}")

            return None

        result = await self.wait_and_poll(check_ready, timeout=self.timeout)

        if result == "ready":
            self.log("全员就绪")
            return HandlerResult(trigger="all_ready")
        else:
            self.log("准备检查超时", "warn")
            return HandlerResult(
                trigger="unknown_error",
                error="准备检查超时，可能有玩家未准备或掉线",
            )


class MatchingHandler(BaseHandler):
    """
    MATCHING 状态处理器
    点击匹配按钮后等待匹配结果
    """

    def __init__(self, *args, match_timeout: int = 60, **kwargs):
        super().__init__(*args, **kwargs)
        self.match_timeout = match_timeout

    async def execute(self) -> HandlerResult:
        self.log("开始匹配...")

        # 点击匹配按钮
        img = await self.take_screenshot()
        if img is None:
            return HandlerResult(trigger="unknown_error", error="截图失败")

        match_btn = await self.pipeline.find_and_click(
            img,
            template_key="lobby/btn_match",
            text_keyword="开始",
        )
        if match_btn.success and match_btn.click_target:
            await self.tap(*match_btn.click_target)
            self.log("已点击匹配按钮")
        else:
            self.log("未找到匹配按钮，尝试 LLM", "warn")
            question = (
                "这是游戏组队界面。请找到开始匹配/开始游戏按钮的位置。"
                '回复 JSON: {"click_x": x, "click_y": y}'
            )
            llm = await self.pipeline.llm.analyze(img, "custom", question)
            if llm.success and llm.parsed:
                x = llm.parsed.get("click_x", 0)
                y = llm.parsed.get("click_y", 0)
                if x and y:
                    await self.tap(x, y)

        # 等待匹配结果
        async def check_match_result(img):
            # 检测加载界面（匹配成功进入加载）
            loading = self.pipeline.template.match_any(img, category="loading")
            if loading and loading.matched:
                return "matched"

            # 检测匹配中特征
            matching = self.pipeline.template.match_any(img, category="matching")
            if matching and matching.matched:
                return None  # 还在匹配中，继续等待

            # LLM 判断
            state = await self.pipeline.detect_state(img)
            if state.success:
                game_state = state.data.get("state", "")
                if game_state in ("loading", "ingame"):
                    return "matched"
                if game_state == "lobby":
                    return "cancelled"  # 匹配被取消了

            return None

        result = await self.wait_and_poll(
            check_match_result,
            timeout=self.match_timeout,
        )

        if result == "matched":
            self.log("匹配成功！")
            return HandlerResult(trigger="match_found")
        elif result == "cancelled":
            self.log("匹配被取消", "warn")
            return HandlerResult(trigger="match_timeout", data={"reason": "cancelled"})
        else:
            self.log("匹配超时", "warn")
            return HandlerResult(trigger="match_timeout", data={"reason": "timeout"})

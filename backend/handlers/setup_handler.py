"""
赛前设置处理器（Captain 专用）
选择游戏模式、地图，关闭自动补位
先检查当前设置是否正确，不正确才修改
"""

from .base import BaseHandler, HandlerResult


class SetupHandler(BaseHandler):
    """
    SETUP 状态处理器（仅 Captain）
    1. 检查当前模式是否正确 → 不对则切换
    2. 检查当前地图是否正确 → 不对则切换
    3. 检查自动补位是否关闭 → 没关则关闭
    """

    def __init__(self, *args, target_mode: str = "", target_map: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self.target_mode = target_mode
        self.target_map = target_map

    async def execute(self) -> HandlerResult:
        self.log("检查赛前设置...")

        img = await self.take_screenshot()
        if img is None:
            return HandlerResult(trigger="unknown_error", error="截图失败")

        # 1. 检查模式
        if self.target_mode:
            await self._check_and_set_mode(img)

        # 2. 检查地图
        if self.target_map:
            await self._check_and_set_map(img)

        # 3. 检查自动补位
        await self._check_auto_fill(img)

        self.log("赛前设置完成")
        return HandlerResult(trigger="setup_done")

    async def _check_and_set_mode(self, img):
        """检查并设置游戏模式"""
        # OCR 读取当前模式文字
        result = await self.pipeline.read_text(img, keyword=self.target_mode)
        if result.success and result.data.get("found"):
            self.log(f"模式已正确: {self.target_mode}")
            return

        # 模式不对，需要切换
        self.log(f"需要切换模式到: {self.target_mode}")

        # 尝试模板匹配模式选择按钮
        mode_btn = self.pipeline.template.match_any(img, category="setup_mode")
        if mode_btn and mode_btn.matched:
            await self.tap(mode_btn.x, mode_btn.y)
            await self._wait_short()

            # 在模式列表中找目标模式
            img2 = await self.take_screenshot()
            if img2 is not None:
                loc = self.pipeline.ocr.find_text_location(img2, self.target_mode)
                if loc:
                    await self.tap(loc[0], loc[1])
                    self.log(f"模式已切换到: {self.target_mode}")
                    return

        # fallback: LLM 辅助
        self.log("使用 LLM 辅助切换模式")
        question = (
            f"这是游戏大厅截图。我需要选择 '{self.target_mode}' 模式。"
            f"请告诉我应该点击哪里来选择这个模式。"
            f"回复 JSON: {{\"click_x\": x, \"click_y\": y, \"steps\": \"操作步骤描述\"}}"
        )
        llm_result = await self.pipeline.llm.analyze(img, "custom", question)
        if llm_result.success and llm_result.parsed:
            x = llm_result.parsed.get("click_x", 0)
            y = llm_result.parsed.get("click_y", 0)
            if x and y:
                await self.tap(x, y)

    async def _check_and_set_map(self, img):
        """检查并设置地图"""
        result = await self.pipeline.read_text(img, keyword=self.target_map)
        if result.success and result.data.get("found"):
            self.log(f"地图已正确: {self.target_map}")
            return

        self.log(f"需要切换地图到: {self.target_map}")

        map_btn = self.pipeline.template.match_any(img, category="setup_map")
        if map_btn and map_btn.matched:
            await self.tap(map_btn.x, map_btn.y)
            await self._wait_short()

            img2 = await self.take_screenshot()
            if img2 is not None:
                loc = self.pipeline.ocr.find_text_location(img2, self.target_map)
                if loc:
                    await self.tap(loc[0], loc[1])
                    self.log(f"地图已切换到: {self.target_map}")
                    return

        self.log("使用 LLM 辅助切换地图")
        question = (
            f"这是游戏截图。我需要选择 '{self.target_map}' 地图。"
            f"回复 JSON: {{\"click_x\": x, \"click_y\": y}}"
        )
        llm_result = await self.pipeline.llm.analyze(img, "custom", question)
        if llm_result.success and llm_result.parsed:
            x = llm_result.parsed.get("click_x", 0)
            y = llm_result.parsed.get("click_y", 0)
            if x and y:
                await self.tap(x, y)

    async def _check_auto_fill(self, img):
        """检查并关闭自动补位"""
        # 模板匹配自动补位开关
        toggle = self.pipeline.template.match_any(img, category="setup_autofill")
        if toggle and toggle.matched:
            # 检测开关状态（需要模板区分"开"和"关"）
            # 如果有 "autofill_on" 模板命中 → 需要关闭
            on_result = self.pipeline.template.match_one(img, "setup_autofill/toggle_on")
            if on_result.matched:
                self.log("自动补位已开启，正在关闭")
                await self.tap(on_result.x, on_result.y)
                return

        self.log("自动补位检查完成")

    async def _wait_short(self):
        """短暂等待 UI 动画"""
        import asyncio
        await asyncio.sleep(0.5)

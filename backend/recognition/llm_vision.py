"""
LLM 视觉分析模块
封装 Gemini 逆向 API，截图→结构化 JSON 操作指令
"""

import base64
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class LLMVisionResult:
    """LLM 视觉分析结果"""
    success: bool
    raw_response: str           # 原始 LLM 回复
    parsed: Optional[dict]      # 解析后的 JSON 结构
    latency_ms: float           # 响应延迟（毫秒）
    error: str = ""


# 预定义 prompt 模板
PROMPTS = {
    "detect_popup": (
        "分析这张游戏截图。画面上是否有弹窗（包括活动公告、广告、提示框等）？\n"
        "如果有弹窗，找到关闭按钮的位置。\n"
        "严格按以下 JSON 格式回复，不要输出任何其他内容：\n"
        '{"has_popup": true/false, "popup_type": "描述弹窗类型", '
        '"close_x": 关闭按钮x坐标, "close_y": 关闭按钮y坐标}\n'
        "坐标基于 1280x720 分辨率。如果没有弹窗，close_x 和 close_y 设为 0。"
    ),
    "analyze_state": (
        "分析这张游戏截图，判断当前处于哪个游戏状态。\n"
        "可能的状态: login(登录页), lobby(大厅), team(组队界面), "
        "matching(匹配中), loading(加载中), ingame(游戏内), "
        "result(结算), error(错误/弹窗), unknown(无法判断)\n"
        "严格按以下 JSON 格式回复，不要输出任何其他内容：\n"
        '{"state": "状态名", "confidence": 0-1的置信度, '
        '"description": "简要描述画面内容", "suggested_action": "建议的下一步操作"}'
    ),
    "read_opponent": (
        "分析这张游戏匹配结果截图，读取对手队伍的信息。\n"
        "尝试识别对手队伍中所有玩家的名称或ID。\n"
        "严格按以下 JSON 格式回复，不要输出任何其他内容：\n"
        '{"opponents": ["玩家1名称", "玩家2名称", ...], '
        '"readable": true/false, "notes": "备注"}'
    ),
    "custom": None,  # 自定义 prompt
}


class LLMVision:
    """
    LLM 视觉分析器
    通过用户自建的 Gemini 逆向 API 发送截图获取分析结果
    """

    def __init__(self, api_url: str, api_key: str = "", mock: bool = False):
        """
        Args:
            api_url: Gemini 逆向 API 地址
            api_key: API Key（如果需要）
            mock: mock 模式
        """
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.mock = mock

    async def analyze(self, image: np.ndarray, prompt_key: str = "detect_popup",
                      custom_prompt: str = "") -> LLMVisionResult:
        """
        发送截图给 LLM 分析
        Args:
            image: 截图 (BGR)
            prompt_key: 预定义 prompt 名称
            custom_prompt: 自定义 prompt（prompt_key="custom" 时使用）
        Returns:
            LLMVisionResult
        """
        start = time.time()

        if self.mock:
            return self._mock_analyze(prompt_key, start)

        prompt = PROMPTS.get(prompt_key, custom_prompt)
        if prompt is None:
            prompt = custom_prompt
        if not prompt:
            return LLMVisionResult(
                success=False, raw_response="", parsed=None,
                latency_ms=0, error="prompt 为空",
            )

        # 编码图片为 base64
        img_b64 = self._encode_image(image)

        try:
            response_text = await self._call_api(prompt, img_b64)
            parsed = self._parse_json_response(response_text)
            latency = (time.time() - start) * 1000

            return LLMVisionResult(
                success=True,
                raw_response=response_text,
                parsed=parsed,
                latency_ms=latency,
            )
        except Exception as e:
            latency = (time.time() - start) * 1000
            logger.error(f"LLM 视觉分析失败: {e}")
            return LLMVisionResult(
                success=False, raw_response="", parsed=None,
                latency_ms=latency, error=str(e),
            )

    async def detect_popup(self, image: np.ndarray) -> dict:
        """快捷方法：检测弹窗"""
        result = await self.analyze(image, "detect_popup")
        if result.success and result.parsed:
            return result.parsed
        return {"has_popup": False, "close_x": 0, "close_y": 0}

    async def analyze_state(self, image: np.ndarray) -> dict:
        """快捷方法：分析游戏状态"""
        result = await self.analyze(image, "analyze_state")
        if result.success and result.parsed:
            return result.parsed
        return {"state": "unknown", "confidence": 0}

    async def read_opponents(self, image: np.ndarray) -> dict:
        """快捷方法：读取对手信息"""
        result = await self.analyze(image, "read_opponent")
        if result.success and result.parsed:
            return result.parsed
        return {"opponents": [], "readable": False}

    async def ask(self, image: np.ndarray, question: str) -> str:
        """通用问答：发送截图+自由提问"""
        result = await self.analyze(image, "custom", custom_prompt=question)
        return result.raw_response if result.success else f"错误: {result.error}"

    # --- 内部方法 ---

    def _encode_image(self, image: np.ndarray) -> str:
        """将图片编码为 base64 字符串"""
        _, buffer = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return base64.b64encode(buffer).decode("utf-8")

    async def _call_api(self, prompt: str, image_b64: str) -> str:
        """
        调用 Gemini 逆向 API
        这里使用通用的 HTTP 请求格式，实际需要根据用户 API 的接口调整
        """
        import aiohttp

        # 构造请求体（通用 Gemini 格式，用户可能需要调整）
        payload = {
            "contents": [{
                "parts": [
                    {"text": prompt},
                    {
                        "inline_data": {
                            "mime_type": "image/jpeg",
                            "data": image_b64,
                        }
                    }
                ]
            }],
        }

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.api_url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise Exception(f"API 返回 {resp.status}: {body[:200]}")

                data = await resp.json()
                # 尝试从常见响应格式中提取文本
                return self._extract_text(data)

    def _extract_text(self, response_data: dict) -> str:
        """从 API 响应中提取文本内容"""
        # Gemini 格式
        if "candidates" in response_data:
            candidates = response_data["candidates"]
            if candidates and "content" in candidates[0]:
                parts = candidates[0]["content"].get("parts", [])
                if parts:
                    return parts[0].get("text", "")

        # OpenAI 兼容格式
        if "choices" in response_data:
            choices = response_data["choices"]
            if choices:
                msg = choices[0].get("message", {})
                return msg.get("content", "")

        # 直接文本
        if "text" in response_data:
            return response_data["text"]
        if "response" in response_data:
            return response_data["response"]

        return json.dumps(response_data)

    def _parse_json_response(self, text: str) -> Optional[dict]:
        """从 LLM 回复中提取 JSON"""
        text = text.strip()

        # 直接尝试解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 尝试从 markdown 代码块提取
        if "```json" in text:
            start = text.index("```json") + 7
            end = text.index("```", start)
            try:
                return json.loads(text[start:end].strip())
            except (json.JSONDecodeError, ValueError):
                pass

        if "```" in text:
            start = text.index("```") + 3
            end = text.index("```", start)
            try:
                return json.loads(text[start:end].strip())
            except (json.JSONDecodeError, ValueError):
                pass

        # 尝试提取第一个 {...}
        brace_start = text.find("{")
        brace_end = text.rfind("}")
        if brace_start != -1 and brace_end > brace_start:
            try:
                return json.loads(text[brace_start:brace_end + 1])
            except json.JSONDecodeError:
                pass

        logger.warning(f"无法从 LLM 回复中解析 JSON: {text[:200]}")
        return None

    def _mock_analyze(self, prompt_key: str, start_time: float) -> LLMVisionResult:
        """Mock 模式返回模拟结果"""
        import asyncio

        mock_responses = {
            "detect_popup": {
                "has_popup": True,
                "popup_type": "活动公告",
                "close_x": 720,
                "close_y": 50,
            },
            "analyze_state": {
                "state": "lobby",
                "confidence": 0.92,
                "description": "游戏大厅主界面",
                "suggested_action": "可以开始组队",
            },
            "read_opponent": {
                "opponents": ["MockPlayer1", "MockPlayer2", "MockPlayer3"],
                "readable": True,
                "notes": "mock 数据",
            },
        }

        parsed = mock_responses.get(prompt_key, {"mock": True, "prompt_key": prompt_key})
        latency = (time.time() - start_time) * 1000

        logger.info(f"[MOCK] LLM 视觉分析 ({prompt_key}): {json.dumps(parsed, ensure_ascii=False)}")

        return LLMVisionResult(
            success=True,
            raw_response=json.dumps(parsed, ensure_ascii=False),
            parsed=parsed,
            latency_ms=latency,
        )

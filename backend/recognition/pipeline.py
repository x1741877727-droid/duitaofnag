"""
三级识别管道
模板匹配 → OCR → LLM 视觉，命中即返回
"""

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

import numpy as np

from .cache import LLMCache
from .llm_vision import LLMVision
from .ocr_reader import OCRReader, OCRResponse
from .template_matcher import MatchResult, TemplateMatcher

logger = logging.getLogger(__name__)


class RecognitionLevel(Enum):
    """识别级别"""
    TEMPLATE = "template"   # 模板匹配
    OCR = "ocr"             # OCR 文字识别
    LLM = "llm"             # LLM 视觉
    NONE = "none"           # 未识别


@dataclass
class PipelineResult:
    """管道识别结果"""
    level: RecognitionLevel     # 命中的识别级别
    success: bool               # 是否识别成功

    # 模板匹配结果（level=TEMPLATE 时有值）
    template_match: Optional[MatchResult] = None

    # OCR 结果（level=OCR 时有值）
    ocr_response: Optional[OCRResponse] = None

    # LLM 结果（level=LLM 时有值）
    llm_result: Optional[dict] = None

    # 通用数据（各级别都可以填充）
    data: dict = None
    latency_ms: float = 0

    def __post_init__(self):
        if self.data is None:
            self.data = {}

    @property
    def click_target(self) -> Optional[tuple[int, int]]:
        """提取点击坐标（如果有）"""
        if self.template_match and self.template_match.matched:
            return (self.template_match.x, self.template_match.y)
        if self.llm_result:
            x = self.llm_result.get("close_x", 0)
            y = self.llm_result.get("close_y", 0)
            if x and y:
                return (x, y)
        return None


class RecognitionPipeline:
    """
    三级识别管道编排器
    优先级: 模板匹配(快) → OCR(中) → LLM(慢)
    """

    def __init__(self, template_matcher: TemplateMatcher,
                 ocr_reader: OCRReader,
                 llm_vision: LLMVision,
                 cache: Optional[LLMCache] = None):
        self.template = template_matcher
        self.ocr = ocr_reader
        self.llm = llm_vision
        self.cache = cache

    async def detect_popup(self, screenshot: np.ndarray) -> PipelineResult:
        """
        检测弹窗：模板匹配已知弹窗 → LLM 识别未知弹窗
        （弹窗检测跳过 OCR，直接模板→LLM）
        """
        start = time.time()

        # 1. 模板匹配已知弹窗关闭按钮
        result = self.template.match_any(screenshot, category="popup")
        if result and result.matched:
            return PipelineResult(
                level=RecognitionLevel.TEMPLATE,
                success=True,
                template_match=result,
                latency_ms=(time.time() - start) * 1000,
            )

        # 2. LLM 识别（带缓存）
        llm_result = await self._llm_with_cache(screenshot, "detect_popup")
        if llm_result and llm_result.get("has_popup"):
            return PipelineResult(
                level=RecognitionLevel.LLM,
                success=True,
                llm_result=llm_result,
                latency_ms=(time.time() - start) * 1000,
            )

        return PipelineResult(
            level=RecognitionLevel.NONE,
            success=False,
            data={"has_popup": False},
            latency_ms=(time.time() - start) * 1000,
        )

    async def detect_state(self, screenshot: np.ndarray,
                           expected_templates: Optional[list[str]] = None) -> PipelineResult:
        """
        检测游戏状态
        先用模板匹配已知状态特征 → OCR 读取状态文字 → LLM 综合判断
        """
        start = time.time()

        # 1. 模板匹配
        if expected_templates:
            for key in expected_templates:
                result = self.template.match_one(screenshot, key)
                if result.matched:
                    return PipelineResult(
                        level=RecognitionLevel.TEMPLATE,
                        success=True,
                        template_match=result,
                        data={"detected_by": key},
                        latency_ms=(time.time() - start) * 1000,
                    )

        # 2. LLM 状态分析
        llm_result = await self._llm_with_cache(screenshot, "analyze_state")
        if llm_result and llm_result.get("state") != "unknown":
            return PipelineResult(
                level=RecognitionLevel.LLM,
                success=True,
                llm_result=llm_result,
                data={"state": llm_result.get("state")},
                latency_ms=(time.time() - start) * 1000,
            )

        return PipelineResult(
            level=RecognitionLevel.NONE,
            success=False,
            latency_ms=(time.time() - start) * 1000,
        )

    async def read_text(self, screenshot: np.ndarray,
                        roi: Optional[tuple[int, int, int, int]] = None,
                        keyword: Optional[str] = None) -> PipelineResult:
        """
        读取文字：OCR 为主 → OCR 失败用 LLM
        """
        start = time.time()

        # 1. OCR
        ocr_resp = self.ocr.recognize(screenshot, roi=roi)
        if ocr_resp.results:
            if keyword:
                found = ocr_resp.find_text(keyword)
                if found:
                    return PipelineResult(
                        level=RecognitionLevel.OCR,
                        success=True,
                        ocr_response=ocr_resp,
                        data={"found": found.text, "x": found.center_x, "y": found.center_y},
                        latency_ms=(time.time() - start) * 1000,
                    )
            else:
                return PipelineResult(
                    level=RecognitionLevel.OCR,
                    success=True,
                    ocr_response=ocr_resp,
                    data={"full_text": ocr_resp.full_text},
                    latency_ms=(time.time() - start) * 1000,
                )

        # 2. LLM fallback
        question = "请读取这张图片中所有可见的文字内容。"
        if keyword:
            question = f"请在这张图片中查找包含 '{keyword}' 的文字，返回其位置坐标。"
        llm_resp = await self.llm.ask(screenshot, question)
        if llm_resp:
            return PipelineResult(
                level=RecognitionLevel.LLM,
                success=True,
                data={"llm_text": llm_resp},
                latency_ms=(time.time() - start) * 1000,
            )

        return PipelineResult(
            level=RecognitionLevel.NONE,
            success=False,
            latency_ms=(time.time() - start) * 1000,
        )

    async def verify_player_id(self, screenshot: np.ndarray,
                               expected_id: str,
                               roi: Optional[tuple[int, int, int, int]] = None) -> PipelineResult:
        """
        校验玩家 ID：OCR 读取 → 比对
        """
        start = time.time()

        # OCR 读 ID
        read_id = self.ocr.read_player_id(screenshot, roi=roi)

        if read_id:
            matched = expected_id in read_id or read_id in expected_id
            return PipelineResult(
                level=RecognitionLevel.OCR,
                success=True,
                data={"read_id": read_id, "expected_id": expected_id, "matched": matched},
                latency_ms=(time.time() - start) * 1000,
            )

        # LLM fallback
        question = (
            f"请读取这张游戏截图中玩家的 ID 或名称。"
            f"我需要确认是否为 '{expected_id}'。"
            f"回复 JSON: {{\"player_id\": \"读取到的ID\", \"is_match\": true/false}}"
        )
        llm_result = await self.llm.analyze(screenshot, "custom", question)
        if llm_result.success and llm_result.parsed:
            return PipelineResult(
                level=RecognitionLevel.LLM,
                success=True,
                llm_result=llm_result.parsed,
                data=llm_result.parsed,
                latency_ms=(time.time() - start) * 1000,
            )

        return PipelineResult(
            level=RecognitionLevel.NONE,
            success=False,
            data={"read_id": "", "expected_id": expected_id, "matched": False},
            latency_ms=(time.time() - start) * 1000,
        )

    async def find_and_click(self, screenshot: np.ndarray,
                             template_key: Optional[str] = None,
                             text_keyword: Optional[str] = None,
                             llm_question: Optional[str] = None) -> PipelineResult:
        """
        通用查找+点击：按优先级找到目标位置
        可以指定模板名、文字关键词、或 LLM 问题
        """
        start = time.time()

        # 1. 模板匹配
        if template_key:
            result = self.template.match_one(screenshot, template_key, multi_scale=True)
            if result.matched:
                return PipelineResult(
                    level=RecognitionLevel.TEMPLATE,
                    success=True,
                    template_match=result,
                    latency_ms=(time.time() - start) * 1000,
                )

        # 2. OCR 找关键词位置
        if text_keyword:
            loc = self.ocr.find_text_location(screenshot, text_keyword)
            if loc:
                return PipelineResult(
                    level=RecognitionLevel.OCR,
                    success=True,
                    data={"keyword": text_keyword, "x": loc[0], "y": loc[1]},
                    latency_ms=(time.time() - start) * 1000,
                )

        # 3. LLM
        if llm_question:
            llm_result = await self._llm_with_cache(screenshot, "custom")
            if llm_result:
                return PipelineResult(
                    level=RecognitionLevel.LLM,
                    success=True,
                    llm_result=llm_result,
                    latency_ms=(time.time() - start) * 1000,
                )

        return PipelineResult(
            level=RecognitionLevel.NONE,
            success=False,
            latency_ms=(time.time() - start) * 1000,
        )

    # --- 内部方法 ---

    async def _llm_with_cache(self, image: np.ndarray, prompt_key: str) -> Optional[dict]:
        """带缓存的 LLM 调用"""
        # 查缓存
        if self.cache:
            cached = self.cache.get(image, prompt_key)
            if cached is not None:
                return cached

        # 调 LLM
        result = await self.llm.analyze(image, prompt_key)
        if result.success and result.parsed:
            # 写缓存
            if self.cache:
                self.cache.put(image, prompt_key, result.parsed)
            return result.parsed

        return None

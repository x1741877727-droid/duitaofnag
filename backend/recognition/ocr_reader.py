"""
OCR 文字识别模块
基于 PaddleOCR，优化中文 + 数字识别
支持 ROI 裁剪提升速度和准确度
"""

import logging
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class OCRResult:
    """单条 OCR 识别结果"""
    text: str               # 识别文字
    confidence: float       # 置信度
    box: list[list[int]]    # 文字框坐标 [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
    center_x: int           # 文字框中心 x
    center_y: int           # 文字框中心 y


@dataclass
class OCRResponse:
    """OCR 识别响应"""
    results: list[OCRResult]    # 所有识别结果
    full_text: str              # 全文拼接

    def find_text(self, keyword: str) -> Optional[OCRResult]:
        """查找包含关键词的结果"""
        for r in self.results:
            if keyword in r.text:
                return r
        return None

    def find_exact(self, text: str) -> Optional[OCRResult]:
        """精确匹配文字"""
        for r in self.results:
            if r.text.strip() == text.strip():
                return r
        return None

    def contains(self, keyword: str) -> bool:
        """全文是否包含关键词"""
        return keyword in self.full_text


class OCRReader:
    """
    OCR 识别器
    延迟初始化 PaddleOCR（首次调用时加载模型）
    """

    def __init__(self, lang: str = "ch", use_gpu: bool = False, mock: bool = False):
        """
        Args:
            lang: 识别语言，"ch" 中文，"en" 英文
            use_gpu: 是否使用 GPU
            mock: mock 模式
        """
        self.lang = lang
        self.use_gpu = use_gpu
        self.mock = mock
        self._ocr = None
        self._initialized = False

    def _ensure_initialized(self):
        """延迟初始化 PaddleOCR"""
        if self._initialized:
            return

        if self.mock:
            self._initialized = True
            logger.info("[MOCK] OCR 初始化完成")
            return

        try:
            from paddleocr import PaddleOCR
            self._ocr = PaddleOCR(
                use_angle_cls=True,  # 文字方向检测
                lang=self.lang,
                use_gpu=self.use_gpu,
                show_log=False,      # 关闭 PaddleOCR 内部日志
            )
            self._initialized = True
            logger.info("PaddleOCR 初始化完成")
        except ImportError:
            logger.error("PaddleOCR 未安装，请运行: pip install paddleocr paddlepaddle")
            raise
        except Exception as e:
            logger.error(f"PaddleOCR 初始化失败: {e}")
            raise

    def recognize(self, image: np.ndarray,
                  roi: Optional[tuple[int, int, int, int]] = None) -> OCRResponse:
        """
        识别图片中的文字
        Args:
            image: 输入图片 (BGR)
            roi: 可选搜索区域 (x, y, w, h)，裁剪后识别
        Returns:
            OCRResponse
        """
        self._ensure_initialized()

        # ROI 裁剪
        roi_offset_x, roi_offset_y = 0, 0
        if roi:
            rx, ry, rw, rh = roi
            image = image[ry:ry+rh, rx:rx+rw]
            roi_offset_x, roi_offset_y = rx, ry

        if self.mock:
            return self._mock_recognize(image)

        try:
            result = self._ocr.ocr(image, cls=True)
            return self._parse_result(result, roi_offset_x, roi_offset_y)
        except Exception as e:
            logger.error(f"OCR 识别失败: {e}")
            return OCRResponse(results=[], full_text="")

    def recognize_text_only(self, image: np.ndarray,
                            roi: Optional[tuple[int, int, int, int]] = None) -> str:
        """简化接口：只返回全文"""
        resp = self.recognize(image, roi)
        return resp.full_text

    def find_text_location(self, image: np.ndarray, keyword: str,
                           roi: Optional[tuple[int, int, int, int]] = None) -> Optional[tuple[int, int]]:
        """
        在图片中查找包含关键词的文字位置
        Returns:
            (x, y) 文字框中心坐标，未找到返回 None
        """
        resp = self.recognize(image, roi)
        result = resp.find_text(keyword)
        if result:
            return (result.center_x, result.center_y)
        return None

    def read_player_id(self, image: np.ndarray,
                       roi: Optional[tuple[int, int, int, int]] = None) -> str:
        """
        读取玩家 ID（针对游戏内 ID 格式优化）
        通常是数字或数字+字母组合
        """
        resp = self.recognize(image, roi)
        # 过滤：保留含数字的结果
        for r in resp.results:
            text = r.text.strip()
            if any(c.isdigit() for c in text):
                # 清理常见的 OCR 误识别
                cleaned = text.replace("O", "0").replace("l", "1").replace("I", "1")
                return cleaned
        return ""

    def _parse_result(self, raw_result, offset_x: int = 0, offset_y: int = 0) -> OCRResponse:
        """解析 PaddleOCR 原始结果"""
        results = []
        texts = []

        if not raw_result or not raw_result[0]:
            return OCRResponse(results=[], full_text="")

        for line in raw_result[0]:
            box = line[0]       # [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
            text = line[1][0]   # 识别文字
            conf = line[1][1]   # 置信度

            # 加上 ROI 偏移
            adjusted_box = [[int(p[0] + offset_x), int(p[1] + offset_y)] for p in box]

            # 计算中心点
            xs = [p[0] for p in adjusted_box]
            ys = [p[1] for p in adjusted_box]
            cx = sum(xs) // len(xs)
            cy = sum(ys) // len(ys)

            results.append(OCRResult(
                text=text,
                confidence=float(conf),
                box=adjusted_box,
                center_x=cx,
                center_y=cy,
            ))
            texts.append(text)

        return OCRResponse(results=results, full_text=" ".join(texts))

    def _mock_recognize(self, image: np.ndarray) -> OCRResponse:
        """Mock 模式返回模拟结果"""
        h, w = image.shape[:2]
        logger.debug(f"[MOCK] OCR 识别图片 {w}x{h}")

        mock_results = [
            OCRResult(
                text="模拟识别文字",
                confidence=0.95,
                box=[[10, 10], [200, 10], [200, 40], [10, 40]],
                center_x=105,
                center_y=25,
            ),
            OCRResult(
                text="玩家ID: 12345678",
                confidence=0.92,
                box=[[10, 50], [250, 50], [250, 80], [10, 80]],
                center_x=130,
                center_y=65,
            ),
        ]

        return OCRResponse(
            results=mock_results,
            full_text="模拟识别文字 玩家ID: 12345678",
        )

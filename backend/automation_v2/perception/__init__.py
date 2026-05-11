"""感知层 — YOLO / OCR / 模板匹配, 全部 ROI optional.

- yolo.Yolo: ONNX 推理, ROI optional, per-instance session (12 实例并发)
- ocr.OCR: OpenVINO AsyncInferQueue 真异步 (12 实例并发 -83% 延迟)
- matcher.Matcher: cv2.matchTemplate, 单 scale, GIL 释放真并行

接口稳定 (typing.Protocol), 换实现 (ONNX→TensorRT / RapidOCR→PaddleOCR) 不破上层.
"""
from .yolo import Detection, Roi, Yolo, YoloProto
from .ocr import OcrHit, OCR, OcrProto
from .matcher import MatchHit, Matcher, MatcherProto

__all__ = [
    "Detection", "Roi", "Yolo", "YoloProto",
    "OcrHit", "OCR", "OcrProto",
    "MatchHit", "Matcher", "MatcherProto",
]

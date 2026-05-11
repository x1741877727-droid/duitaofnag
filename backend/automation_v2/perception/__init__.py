"""感知层 — YOLO / OCR / 模板匹配, 全部 ROI optional.

- yolo.Yolo: ONNX 推理, ROI optional, per-instance session (12 实例并发)
- ocr.OCR: OpenVINO async, det/cls/rec 按需调用 (P5 mode='rec_only' 提速 70%)
- matcher.Matcher: cv2.matchTemplate, 单 scale (LDPlayer 960×540 锁定)

接口稳定 (typing.Protocol), 换实现 (ONNX→TensorRT / RapidOCR→PaddleOCR) 不破上层.
"""
from .yolo import Detection, Roi, Yolo

__all__ = ["Detection", "Roi", "Yolo"]

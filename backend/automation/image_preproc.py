"""图像预处理 — OCR / YOLO 前的可选增强.

按 methods 顺序应用. RapidOCR 输入需要 3 通道, 单通道操作完成后转回 BGR.

调用方:
  - api_roi.py: 用户在 OCR 调试页实测
  - ocr_dismisser._ocr_roi_named: 生产读 roi.yaml.preprocessing 应用
"""
from __future__ import annotations

import cv2
import numpy as np


VALID_METHODS = ("grayscale", "clahe", "binarize", "sharpen", "invert")


def apply_preprocessing(img: np.ndarray, methods: list) -> np.ndarray:
    """按 methods 列表顺序应用图像预处理.

    Args:
        img: BGR numpy 数组
        methods: ["grayscale" | "clahe" | "binarize" | "sharpen" | "invert"]

    Returns:
        处理后的 BGR 图像 (始终 3 通道)
    """
    if not methods:
        return img
    out = img.copy()
    for m in methods:
        if m == "grayscale":
            if len(out.shape) == 3:
                gray = cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)
                out = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        elif m == "clahe":
            # 仅在 LAB 的 L 通道做 CLAHE, 保留色彩
            if len(out.shape) == 3:
                lab = cv2.cvtColor(out, cv2.COLOR_BGR2LAB)
                l, a, b = cv2.split(lab)
                clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
                l_eq = clahe.apply(l)
                out = cv2.cvtColor(cv2.merge((l_eq, a, b)), cv2.COLOR_LAB2BGR)
        elif m == "binarize":
            gray = cv2.cvtColor(out, cv2.COLOR_BGR2GRAY) if len(out.shape) == 3 else out
            _, bin_img = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            out = cv2.cvtColor(bin_img, cv2.COLOR_GRAY2BGR)
        elif m == "sharpen":
            kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]], dtype=np.float32)
            out = cv2.filter2D(out, -1, kernel)
        elif m == "invert":
            out = cv2.bitwise_not(out)
        # 未知 method 静默跳过
    return out

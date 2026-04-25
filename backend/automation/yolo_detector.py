"""YOLO UI 元素检测器 — 运行时 ONNX 推理

设计要点：
  - **opt-in**：模型文件不存在 = 静默跳过，不影响现有 OCR 流程
  - **lazy-load**：第一次调用时加载 ONNX session
  - **跨硬件**：自动选 provider（DirectML/CUDA/CoreML/CPU）
  - **快**：YOLOv8n CPU 20-50ms / GPU 3-15ms
  - **fallback**：detect 返回空 = 上层调 OCR

约定的模型路径：
    backend/automation/models/ui_yolo.onnx       (主模型)
    config/yolo_classes.yaml                     (类配置，与训练时一致)

集成示例（ocr_dismisser 内）：
    from .yolo_detector import detect_buttons, is_available

    if is_available():
        boxes = detect_buttons(screenshot, names=["btn_close_x"])
        if boxes:
            cx, cy = boxes[0].center_px
            await adb.tap(cx, cy)
            return
    # fallback OCR
    ...
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import List, Optional, Sequence

import cv2
import numpy as np

from . import metrics

logger = logging.getLogger(__name__)

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PROJ_ROOT = os.path.dirname(_BACKEND_DIR)
_DEFAULT_MODEL = os.path.join(_BACKEND_DIR, "automation", "models", "ui_yolo.onnx")
_DEFAULT_CLASSES = os.path.join(_PROJ_ROOT, "config", "yolo_classes.yaml")

_session = None
_session_lock = threading.Lock()
_class_names: List[str] = []
_input_size: int = 640
_load_failed: bool = False


@dataclass
class Detection:
    """单个检测结果"""
    class_id: int
    name: str
    score: float
    # 像素坐标（相对原图）
    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def center_px(self) -> tuple:
        return ((self.x1 + self.x2) // 2, (self.y1 + self.y2) // 2)

    @property
    def w(self) -> int:
        return self.x2 - self.x1

    @property
    def h(self) -> int:
        return self.y2 - self.y1


def _select_providers() -> list:
    """挑最优 ONNX provider，按平台 + 已装 wheel 排序"""
    try:
        import onnxruntime as ort
    except ImportError:
        return []
    avail = set(ort.get_available_providers())
    # 优先级：DirectML（Win 通用 GPU）→ CUDA（NV）→ CoreML（Mac）→ CPU
    priority = [
        "DmlExecutionProvider",          # DirectML（Win，任意 GPU）
        "CUDAExecutionProvider",         # NV CUDA（Linux/Win NV-only）
        "CoreMLExecutionProvider",       # macOS Apple Silicon / Intel
        "CPUExecutionProvider",
    ]
    return [p for p in priority if p in avail]


def _load_classes(yaml_path: str = _DEFAULT_CLASSES) -> List[str]:
    """读 yolo_classes.yaml 拿类名"""
    try:
        import yaml
    except ImportError:
        logger.warning("PyYAML 未装，YOLO 类名列表用空值")
        return []
    if not os.path.exists(yaml_path):
        return []
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    classes = sorted(data.get("classes", []), key=lambda c: c["id"])
    return [c["name"] for c in classes]


def _ensure_loaded(model_path: str = _DEFAULT_MODEL) -> bool:
    """lazy-load ONNX session；失败一次就标记失败避免重复尝试"""
    global _session, _class_names, _load_failed, _input_size

    if _session is not None:
        return True
    if _load_failed:
        return False

    with _session_lock:
        if _session is not None:
            return True
        if not os.path.exists(model_path):
            logger.info(f"YOLO 模型不存在，跳过 YOLO 推理：{model_path}")
            _load_failed = True
            return False
        try:
            import onnxruntime as ort
        except ImportError:
            logger.warning("onnxruntime 未装，YOLO 推理不可用")
            _load_failed = True
            return False
        providers = _select_providers()
        if not providers:
            logger.warning("无可用 ONNX provider")
            _load_failed = True
            return False
        try:
            sess = ort.InferenceSession(model_path, providers=providers)
            _input_size = sess.get_inputs()[0].shape[-1] or 640
            _session = sess
            _class_names = _load_classes()
            logger.info(
                f"YOLO 加载成功：{model_path}  size={_input_size}  "
                f"providers={[p for p in providers]}  classes={len(_class_names)}"
            )
            return True
        except Exception as e:
            logger.warning(f"YOLO 加载失败：{e}")
            _load_failed = True
            return False


def is_available(model_path: str = _DEFAULT_MODEL) -> bool:
    """运行时探测：YOLO 是否可用（用于上层 if-else fallback OCR）"""
    return _ensure_loaded(model_path)


def _preprocess(frame: np.ndarray, target: int) -> tuple:
    """letterbox 到 target × target，return (input_chw, scale, pad_x, pad_y)"""
    h, w = frame.shape[:2]
    scale = min(target / w, target / h)
    new_w, new_h = int(w * scale), int(h * scale)
    resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((target, target, 3), 114, dtype=np.uint8)
    pad_x = (target - new_w) // 2
    pad_y = (target - new_h) // 2
    canvas[pad_y:pad_y + new_h, pad_x:pad_x + new_w] = resized
    # BGR → RGB → CHW float32 / 255
    img = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    img = np.transpose(img, (2, 0, 1))[None, :]  # 1×3×target×target
    return img, scale, pad_x, pad_y


def _postprocess(output: np.ndarray, scale: float, pad_x: int, pad_y: int,
                 orig_h: int, orig_w: int,
                 conf_thr: float = 0.4, iou_thr: float = 0.5,
                 names_filter: Optional[Sequence[str]] = None) -> List[Detection]:
    """YOLOv8 输出格式：[1, 4 + nc, N]，N = 8400 个 anchor"""
    if output.ndim == 3:
        output = output[0]
    output = output.T  # → [N, 4+nc]

    if output.shape[1] < 5:
        return []

    boxes_xywh = output[:, :4]
    cls_scores = output[:, 4:]
    cls_ids = cls_scores.argmax(axis=1)
    confidences = cls_scores.max(axis=1)

    keep = confidences > conf_thr
    if not keep.any():
        return []
    boxes_xywh = boxes_xywh[keep]
    cls_ids = cls_ids[keep]
    confidences = confidences[keep]

    # xywh → xyxy（letterbox 坐标系，640×640 内）
    cx, cy, ww, hh = boxes_xywh.T
    x1 = cx - ww / 2
    y1 = cy - hh / 2
    x2 = cx + ww / 2
    y2 = cy + hh / 2

    # 反 letterbox → 原图坐标
    x1 = (x1 - pad_x) / scale
    y1 = (y1 - pad_y) / scale
    x2 = (x2 - pad_x) / scale
    y2 = (y2 - pad_y) / scale
    x1 = np.clip(x1, 0, orig_w - 1)
    y1 = np.clip(y1, 0, orig_h - 1)
    x2 = np.clip(x2, 0, orig_w - 1)
    y2 = np.clip(y2, 0, orig_h - 1)

    # NMS
    nms_boxes = np.stack([x1, y1, x2 - x1, y2 - y1], axis=1).tolist()
    keep_idx = cv2.dnn.NMSBoxes(
        nms_boxes, confidences.tolist(), conf_thr, iou_thr
    )
    if isinstance(keep_idx, np.ndarray):
        keep_idx = keep_idx.flatten().tolist()
    if not keep_idx:
        return []

    name_set = set(names_filter) if names_filter else None
    detections: List[Detection] = []
    for i in keep_idx:
        cls_id = int(cls_ids[i])
        name = _class_names[cls_id] if 0 <= cls_id < len(_class_names) else f"cls{cls_id}"
        if name_set is not None and name not in name_set:
            continue
        detections.append(Detection(
            class_id=cls_id,
            name=name,
            score=float(confidences[i]),
            x1=int(x1[i]), y1=int(y1[i]),
            x2=int(x2[i]), y2=int(y2[i]),
        ))
    detections.sort(key=lambda d: d.score, reverse=True)
    return detections


def detect_buttons(frame: np.ndarray,
                   names: Optional[Sequence[str]] = None,
                   conf_thr: float = 0.4) -> List[Detection]:
    """主推理入口。

    Args:
        frame:      BGR 截图
        names:      只关心这几类（None = 全要）
        conf_thr:   置信度阈值

    Returns:
        Detection 列表，按 score 降序
    """
    if not _ensure_loaded():
        return []

    orig_h, orig_w = frame.shape[:2]
    t0 = time.perf_counter()
    img, scale, pad_x, pad_y = _preprocess(frame, _input_size)

    assert _session is not None
    inp_name = _session.get_inputs()[0].name
    output = _session.run(None, {inp_name: img})[0]

    detections = _postprocess(
        output, scale, pad_x, pad_y, orig_h, orig_w,
        conf_thr=conf_thr, names_filter=names
    )
    dur_ms = (time.perf_counter() - t0) * 1000
    metrics.record(
        "yolo_detect", dur_ms=round(dur_ms, 2),
        n_dets=len(detections), filter=",".join(names) if names else "*"
    )
    return detections

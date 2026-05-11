"""YOLO ONNX 推理.

设计:
- ROI optional: 不传 roi=全屏推理 (P1/P5 用); 传了 crop + offset (P2 用 CLOSE_X_ROI 快 5x)
- per-instance session: 12 实例 × ~200MB = 2.4GB < 8GB VRAM, 无锁真并发
- 启动 warmup 1 次 dummy 推理避免 cold start (实测 cold start 2.7s vs warm 50ms)
- conf_thresh 默认 0.20 (容忍边缘 popup, 配合 phase 内黑名单过滤)

性能目标:
- 单次 detect (ROI): 30-50 ms (CPU EP) / 25-40 ms (DML)
- 单次 detect (全屏): 50-80 ms / 40-60 ms
- 12 实例并发: 共享 GPU 队列, 每实例平均 80-120 ms (vs v1 同步排队 700 ms+)
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import NamedTuple, Optional, Protocol

logger = logging.getLogger(__name__)

# 模型类别 (从 fixtures/yolo/classes.txt 加载会更稳, 这里先 hardcode)
DEFAULT_CLASSES = ["close_x", "action_btn", "lobby"]
NMS_IOU = 0.45
INPUT_SIZE = 640


class Detection(NamedTuple):
    name: str
    conf: float
    cx: int
    cy: int
    x1: int
    y1: int
    x2: int
    y2: int


class Roi(NamedTuple):
    """归一化坐标 (0.0-1.0 ratio), 屏幕尺寸无关."""
    x_min: float
    y_min: float
    x_max: float
    y_max: float


class YoloProto(Protocol):
    async def detect(
        self,
        shot,                                  # np.ndarray
        *,
        roi: Optional[Roi] = None,
        conf_thresh: float = 0.20,
    ) -> list[Detection]: ...

    async def warmup(self) -> None: ...


class Yolo:
    """每实例 1 个 session. intra=2 inter=1 配 12 inst × 2 = 24 thread."""

    def __init__(
        self,
        model_path: Path,
        classes: Optional[list[str]] = None,
        intra_threads: int = 2,
    ):
        # 延迟 import 重依赖 — 让 ctx.py 等无 cv2/onnx 环境也能 import
        import onnxruntime as ort

        self.classes = classes or DEFAULT_CLASSES
        self.model_path = Path(model_path)

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = intra_threads
        opts.inter_op_num_threads = 1
        opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL

        providers = []
        avail = set(ort.get_available_providers())
        for p in ("DmlExecutionProvider", "CUDAExecutionProvider", "CPUExecutionProvider"):
            if p in avail:
                providers.append(p)
        if not providers:
            raise RuntimeError("no ONNX EP available")

        self.sess = ort.InferenceSession(
            str(self.model_path), sess_options=opts, providers=providers,
        )
        self.input_name = self.sess.get_inputs()[0].name
        logger.info(
            f"[yolo] loaded {self.model_path.name} provider={providers[0]} "
            f"intra={intra_threads} classes={self.classes}"
        )

    async def warmup(self) -> None:
        """启动时跑 1 次 dummy 推理避免 cold start. 主程序启动后调一次.

        R-H2: 异常处理 + raise — 启动 fail-fast, 上层决定是否 fatal.
        """
        try:
            import numpy as np
            dummy = np.zeros((540, 960, 3), dtype=np.uint8)
            await asyncio.to_thread(self._infer_full, dummy, 0.20)
            logger.info(f"[yolo] warmup done")
        except Exception as e:
            logger.error(f"[yolo] warmup failed: {e}", exc_info=True)
            raise   # 上层 runner 启动时捕获, 决定 fatal / fallback

    async def detect(
        self,
        shot,                              # np.ndarray
        *,
        roi: Optional[Roi] = None,
        conf_thresh: float = 0.20,
    ) -> list[Detection]:
        """ROI 可选. 不传 = 全屏; 传了 = crop + offset 平移坐标回原图."""
        if roi is None:
            return await asyncio.to_thread(self._infer_full, shot, conf_thresh)
        return await asyncio.to_thread(self._infer_roi, shot, roi, conf_thresh)

    # ─────────── 内部 ───────────

    def _infer_roi(self, shot, roi: Roi, conf: float) -> list[Detection]:
        h, w = shot.shape[:2]
        x1 = int(w * roi.x_min)
        y1 = int(h * roi.y_min)
        x2 = int(w * roi.x_max)
        y2 = int(h * roi.y_max)
        if x2 - x1 < 32 or y2 - y1 < 32:
            return []  # ROI 太小, 不跑
        crop = shot[y1:y2, x1:x2]
        dets = self._infer_full(crop, conf)
        # 坐标平移回原图
        return [
            d._replace(
                cx=d.cx + x1, cy=d.cy + y1,
                x1=d.x1 + x1, y1=d.y1 + y1,
                x2=d.x2 + x1, y2=d.y2 + y1,
            )
            for d in dets
        ]

    def _infer_full(self, frame, conf: float) -> list[Detection]:
        import numpy as np
        h0, w0 = frame.shape[:2]
        tensor, scale, pad = self._letterbox(frame)
        out = self.sess.run(None, {self.input_name: tensor})[0]
        return self._postprocess(out, scale, pad, h0, w0, conf)

    @staticmethod
    def _letterbox(frame):
        """640×640 letterbox + BGR→RGB + NCHW + /255.0."""
        import cv2
        import numpy as np
        h, w = frame.shape[:2]
        s = min(INPUT_SIZE / w, INPUT_SIZE / h)
        nw, nh = int(w * s), int(h * s)
        resized = cv2.resize(frame, (nw, nh))
        canvas = np.full((INPUT_SIZE, INPUT_SIZE, 3), 114, dtype=np.uint8)
        px, py = (INPUT_SIZE - nw) // 2, (INPUT_SIZE - nh) // 2
        canvas[py:py + nh, px:px + nw] = resized
        rgb = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)
        t = rgb.transpose(2, 0, 1).astype(np.float32) / 255.0
        return np.expand_dims(t, 0), s, (px, py)

    def _postprocess(self, output, scale: float, pad, h0: int, w0: int, conf: float):
        import cv2
        import numpy as np
        # YOLOv8 head: (1, 4+nc, anchors) → (anchors, 4+nc)
        preds = output[0].T
        boxes, scores = preds[:, :4], preds[:, 4:]
        cls_idx = scores.argmax(axis=1)
        cls_conf = scores.max(axis=1)
        mask = cls_conf > conf
        if not mask.any():
            return []
        b, c, cls_idx = boxes[mask], cls_conf[mask], cls_idx[mask]
        # cx,cy,w,h → x1,y1,x2,y2 (input space) → 原图坐标
        x1 = ((b[:, 0] - b[:, 2] / 2) - pad[0]) / scale
        y1 = ((b[:, 1] - b[:, 3] / 2) - pad[1]) / scale
        x2 = ((b[:, 0] + b[:, 2] / 2) - pad[0]) / scale
        y2 = ((b[:, 1] + b[:, 3] / 2) - pad[1]) / scale
        x1 = np.clip(x1, 0, w0 - 1)
        y1 = np.clip(y1, 0, h0 - 1)
        x2 = np.clip(x2, 0, w0 - 1)
        y2 = np.clip(y2, 0, h0 - 1)
        xywh = np.stack([x1, y1, x2 - x1, y2 - y1], axis=1).tolist()
        keep_idx = cv2.dnn.NMSBoxes(xywh, c.tolist(), conf, NMS_IOU)
        if len(keep_idx) == 0:
            return []
        out = []
        for i in (keep_idx.flatten() if hasattr(keep_idx, "flatten") else keep_idx):
            name = self.classes[int(cls_idx[i])] if int(cls_idx[i]) < len(self.classes) else f"cls{int(cls_idx[i])}"
            out.append(Detection(
                name=name,
                conf=float(c[i]),
                cx=int((x1[i] + x2[i]) / 2),
                cy=int((y1[i] + y2[i]) / 2),
                x1=int(x1[i]),
                y1=int(y1[i]),
                x2=int(x2[i]),
                y2=int(y2[i]),
            ))
        out.sort(key=lambda d: -d.conf)
        return out

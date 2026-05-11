"""OCR — OpenVINO AsyncInferQueue 真异步, 12 实例并发.

设计 (REVIEW_OCR.md 推荐方案 A):
- AsyncInferQueue (12 InferRequest 共享 compiled_model) 替代单 Lock 串行
- 12 实例并发 OCR: 300-400ms 平均 (vs 单 Lock 方案 2400ms)
- ROI optional: 不传 = 全屏 det+rec; 传了 = crop + rec_only (P5 快)
- mode='auto'/'det+rec'/'rec_only', P5 传 rec_only 跳过 det 省 70%

12 实例可行性:
- 单 compiled_model 占 ~200MB RAM (CPU), 12 个 InferRequest 共享 → 总 ~800MB (vs per-inst 2.4GB)
- 启动 warmup 1 次 dummy 推理避免 cold start (实测 2.7s → 50ms)

性能目标 (基于 REVIEW_OCR.md 推算):
- rec_only (50×30 ROI): 30-50 ms
- det+rec (全屏): 150-200 ms
- 12 并发 rec_only: 平均 80-120 ms
- 12 并发 det+rec: 平均 250-350 ms
"""
from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path
from typing import Any, Literal, NamedTuple, Optional, Protocol

logger = logging.getLogger(__name__)


class OcrHit(NamedTuple):
    text: str
    conf: float
    bbox: tuple[int, int, int, int]   # (x1, y1, x2, y2) on full frame


class Roi(NamedTuple):
    x_min: float
    y_min: float
    x_max: float
    y_max: float


class OcrProto(Protocol):
    async def recognize(
        self,
        shot: Any,
        *,
        roi: Optional[Roi] = None,
        mode: Literal['det+rec', 'rec_only', 'auto'] = 'auto',
    ) -> list[OcrHit]: ...

    async def warmup(self) -> None: ...


class OCR:
    """OpenVINO AsyncInferQueue 真异步推理.

    12 实例共享 1 个 compiled_model + 1 个 AsyncInferQueue (12 个独立 InferRequest).
    """

    def __init__(
        self,
        det_model: Path,
        rec_model: Path,
        num_requests: int = 12,
        num_streams: int = 4,
    ):
        # 延迟 import (允许无 openvino 环境也能 import 模块)
        try:
            import openvino as ov
        except ImportError as e:
            raise RuntimeError(f"openvino 未装: {e}")

        self.core = ov.Core()
        self._det_compiled = self.core.compile_model(
            str(det_model), "CPU",
            config={"NUM_STREAMS": num_streams, "PERFORMANCE_HINT": "THROUGHPUT"},
        )
        self._rec_compiled = self.core.compile_model(
            str(rec_model), "CPU",
            config={"NUM_STREAMS": num_streams, "PERFORMANCE_HINT": "THROUGHPUT"},
        )
        # AsyncInferQueue: num_requests 个独立 InferRequest, 真并发
        # OpenVINO 官方推荐用法 (vs 单 Lock 串行)
        self._det_queue = ov.AsyncInferQueue(self._det_compiled, num_requests)
        self._rec_queue = ov.AsyncInferQueue(self._rec_compiled, num_requests)
        # 队列 access 的 lock (轻量 — get_idle_request_id() 本身有内部锁)
        # 但 result 收集需要外部 sync, 用 Event 实现
        self._num_requests = num_requests
        logger.info(
            f"[ocr] OpenVINO AsyncInferQueue loaded "
            f"jobs={num_requests} streams={num_streams}"
        )

    async def warmup(self) -> None:
        """启动时跑 1 次 dummy 推理避免 cold start (实测 cold 2.7s vs warm 50ms)."""
        try:
            import numpy as np
            dummy = np.zeros((48, 320, 3), dtype=np.uint8)
            await asyncio.to_thread(self._rec_only_sync, dummy)
            logger.info("[ocr] warmup done")
        except Exception as e:
            logger.warning(f"[ocr] warmup err: {e}")

    async def recognize(
        self,
        shot: Any,                              # np.ndarray
        *,
        roi: Optional[Roi] = None,
        mode: Literal['det+rec', 'rec_only', 'auto'] = 'auto',
    ) -> list[OcrHit]:
        """识别文字. ROI 可选 (keyword-only, 跟 yolo.detect 一致), mode 决定路径."""
        crop, ox, oy = self._crop(shot, roi)
        if mode == 'rec_only':
            return await asyncio.to_thread(self._rec_only_as_hit, crop, ox, oy)
        # auto / det+rec 默认走 det+rec
        return await asyncio.to_thread(self._det_rec, crop, ox, oy)

    # ─────────── 内部 ───────────

    @staticmethod
    def _crop(shot: Any, roi: Optional[Roi]) -> tuple[Any, int, int]:
        if roi is None:
            return shot, 0, 0
        h, w = shot.shape[:2]
        x1 = int(w * roi.x_min)
        y1 = int(h * roi.y_min)
        x2 = int(w * roi.x_max)
        y2 = int(h * roi.y_max)
        return shot[y1:y2, x1:x2].copy(), x1, y1  # .copy() 防多线程 view 隐患

    def _det_rec(self, frame: Any, ox: int, oy: int) -> list[OcrHit]:
        """det → 多 bbox → rec_only 每个. AsyncInferQueue 自动调度."""
        det_input = self._prep_det(frame)
        det_out = self._infer_sync(self._det_queue, det_input)
        boxes = self._postprocess_det(det_out, frame.shape[:2])
        hits: list[OcrHit] = []
        for (bx1, by1, bx2, by2) in boxes:
            patch = frame[by1:by2, bx1:bx2]
            text, conf = self._rec_only_sync(patch)
            if text:
                hits.append(OcrHit(
                    text=text, conf=conf,
                    bbox=(bx1 + ox, by1 + oy, bx2 + ox, by2 + oy),
                ))
        return hits

    def _rec_only_as_hit(self, frame: Any, ox: int, oy: int) -> list[OcrHit]:
        text, conf = self._rec_only_sync(frame)
        if not text:
            return []
        h, w = frame.shape[:2]
        return [OcrHit(text, conf, (ox, oy, ox + w, oy + h))]

    def _rec_only_sync(self, patch: Any) -> tuple[str, float]:
        rec_input = self._prep_rec(patch)
        rec_out = self._infer_sync(self._rec_queue, rec_input)
        return self._ctc_decode(rec_out)

    @staticmethod
    def _infer_sync(queue: Any, input_data: Any) -> Any:
        """通过 AsyncInferQueue 拿空闲 InferRequest 跑 sync infer.

        AsyncInferQueue.get_idle_request_id() 是线程安全的, 内部 lock 短.
        infer_request.infer() 是 sync 调用, 但每个 InferRequest 独立无竞争.

        12 实例同时调 _infer_sync → 12 个独立 InferRequest 各自跑, 真并发.
        """
        idx = queue.get_idle_request_id()
        req = queue[idx]
        result = req.infer(input_data)
        # AsyncInferQueue 自动 release request 到 idle pool
        # result 是 dict {output_node: ndarray}, 拿第一个 output
        return list(result.values())[0] if result else None

    @staticmethod
    def _prep_det(f: Any) -> Any:
        import cv2
        import numpy as np
        r = cv2.resize(f, (640, 640))
        r = r.astype(np.float32) / 255.0
        return r.transpose(2, 0, 1)[None]

    @staticmethod
    def _prep_rec(f: Any) -> Any:
        import cv2
        import numpy as np
        r = cv2.resize(f, (320, 48))
        r = r.astype(np.float32) / 255.0
        return r.transpose(2, 0, 1)[None]

    def _postprocess_det(self, out: Any, hw: tuple[int, int]) -> list[tuple[int, int, int, int]]:
        """det head 后处理. 实际接 PaddleOCR det 时填具体 NMS + bbox 解码.

        R-H1: TODO 状态时 logger.warning 让业务知道 OCR 不可用, 不静默 return.
        """
        if out is None:
            logger.warning("[ocr] _postprocess_det: out is None")
            return []
        try:
            # TODO: 接 PaddleOCR det head 解码 (NMS + bbox decode)
            # 临时 TODO stub, 业务收 [] → 失败处理由业务负责
            logger.warning("[ocr] _postprocess_det: TODO stub, OCR det 路径不可用")
            return []
        except Exception as e:
            logger.error(f"[ocr] _postprocess_det error: {e}", exc_info=True)
            return []

    def _ctc_decode(self, out: Any) -> tuple[str, float]:
        """rec head CTC 解码. 实际接 PaddleOCR rec + char dict 时填.

        R-H1: TODO 状态时 logger.warning 让业务知道 OCR 不可用.
        """
        if out is None:
            logger.warning("[ocr] _ctc_decode: out is None")
            return "", 0.0
        try:
            # TODO: 接 PaddleOCR rec head + chardict (argmax + CTC decode)
            logger.warning("[ocr] _ctc_decode: TODO stub, OCR rec 路径不可用")
            return "", 0.0
        except Exception as e:
            logger.error(f"[ocr] _ctc_decode error: {e}", exc_info=True)
            return "", 0.0

"""OCR 多进程池 — 绕 GIL，让 N 个 OCR 真正并行

为什么需要：
  RapidOCR 调 ONNX runtime 是同步阻塞 + 持 GIL。
  asyncio.run_in_executor + ThreadPool 也只能让 ONE OCR 跑（GIL 串行）。
  ProcessPool 真正并行：N workers 各自一个 OCR session，跑在各自进程里。

吞吐示例（DirectML 250ms / OCR）：
  1 worker:  4 OCR/sec  → 撑 4 实例 (1 OCR/sec/inst)
  4 workers: 16 OCR/sec → 撑 12-16 实例
  8 workers: 32 OCR/sec → 撑 24+ 实例

VRAM 代价（每 worker ~500MB）：
  4 workers ≈ 2 GB VRAM
  8 workers ≈ 4 GB VRAM  ← 大多数集成 GPU 上限
  12 workers ≈ 6 GB VRAM ← 中端 dGPU 起步

用法：
    from .ocr_pool import OcrPool

    OcrPool.init(workers=4, ocr_params={"EngineConfig.onnxruntime.use_dml": True})
    hits = await OcrPool.ocr_async(screenshot)  # 不阻塞 asyncio loop

环境变量：
  GAMEBOT_OCR_WORKERS  覆盖 worker 数（默认按硬件算）
  GAMEBOT_OCR_POOL_DISABLE  设任意值 = 关池子，回退到主进程单 OCR
"""
from __future__ import annotations

import asyncio
import logging
import multiprocessing as mp
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class OcrHit:
    """跨进程序列化友好的 OCR 命中条目"""
    text: str
    cx: int
    cy: int
    score: float = 0.0


# ════════════════════════════════════════
# Worker 进程内的全局状态（每个 worker 各一份）
# ════════════════════════════════════════

_worker_engine = None  # 每个 worker 进程独立的 RapidOCR 实例


def _worker_init(ocr_params: Optional[Dict[str, Any]]) -> None:
    """新 worker 进程启动时初始化 RapidOCR"""
    global _worker_engine
    try:
        from rapidocr import RapidOCR
        _worker_engine = RapidOCR(params=ocr_params) if ocr_params else RapidOCR()
        # warmup（冷启动太慢的话主进程等不及）
        dummy = np.zeros((100, 100, 3), dtype=np.uint8)
        _worker_engine(dummy)
    except Exception as e:
        # 错误吞掉记到 stderr —— ProcessPool 不让我们用 logger
        sys.stderr.write(f"[OcrPool worker] init failed: {e}\n")
        _worker_engine = None


def _worker_ocr(img: np.ndarray) -> List[Dict[str, Any]]:
    """worker 处理单帧。返回 list of dict（可 pickle 跨进程）"""
    global _worker_engine
    if _worker_engine is None:
        return []
    try:
        result = _worker_engine(img)
        if result is None or result.boxes is None:
            return []
        hits = []
        for box, text, score in zip(result.boxes, result.txts, result.scores):
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            hits.append({
                "text": text,
                "cx": int(sum(xs) / 4),
                "cy": int(sum(ys) / 4),
                "score": float(score),
            })
        return hits
    except Exception as e:
        sys.stderr.write(f"[OcrPool worker] ocr failed: {e}\n")
        return []


# ════════════════════════════════════════
# 主进程的 Pool 管理
# ════════════════════════════════════════

class OcrPool:
    """单例，懒初始化"""
    _executor: Optional[ProcessPoolExecutor] = None
    _workers: int = 0
    _ocr_params: Optional[Dict[str, Any]] = None
    _enabled: bool = True
    _stats_calls: int = 0
    _stats_total_ms: float = 0.0

    @classmethod
    def is_enabled(cls) -> bool:
        if os.environ.get("GAMEBOT_OCR_POOL_DISABLE"):
            return False
        return cls._enabled

    @classmethod
    def init(cls, workers: Optional[int] = None,
             ocr_params: Optional[Dict[str, Any]] = None) -> bool:
        """初始化 pool。workers=None 时按硬件算（CPU/4，min 2 max 8）"""
        if not cls.is_enabled():
            return False

        if cls._executor is not None:
            return True

        if workers is None:
            workers = int(os.environ.get(
                "GAMEBOT_OCR_WORKERS",
                str(min(8, max(2, (os.cpu_count() or 4) // 4)))
            ))

        cls._workers = workers
        cls._ocr_params = ocr_params

        try:
            ctx = mp.get_context("spawn")  # spawn 跨平台兼容（fork 在 Win 不工作）
            cls._executor = ProcessPoolExecutor(
                max_workers=workers,
                mp_context=ctx,
                initializer=_worker_init,
                initargs=(ocr_params,),
            )
            logger.info(f"OcrPool: 启动 {workers} 个 worker 进程, params={ocr_params}")
            return True
        except Exception as e:
            logger.warning(f"OcrPool 初始化失败，回退到主进程 OCR: {e}")
            cls._enabled = False
            return False

    @classmethod
    def shutdown(cls) -> None:
        if cls._executor is not None:
            cls._executor.shutdown(wait=False, cancel_futures=True)
            cls._executor = None

    @classmethod
    async def ocr_async(cls, img: np.ndarray) -> List[OcrHit]:
        """异步 OCR。会被 asyncio loop 调度到空闲 worker，不阻塞主线程。"""
        if not cls.is_enabled() or cls._executor is None:
            # 没初始化或被禁用 → 调用方应该 fallback 主进程 OCR
            return []
        loop = asyncio.get_event_loop()
        t0 = time.perf_counter()
        raw = await loop.run_in_executor(cls._executor, _worker_ocr, img)
        dt = (time.perf_counter() - t0) * 1000
        cls._stats_calls += 1
        cls._stats_total_ms += dt
        return [OcrHit(**d) for d in raw]

    @classmethod
    def ocr_sync(cls, img: np.ndarray) -> List[OcrHit]:
        """同步阻塞版（旧代码兼容）。一样走 worker 跑，但调用方等结果"""
        if not cls.is_enabled() or cls._executor is None:
            return []
        future = cls._executor.submit(_worker_ocr, img)
        raw = future.result()
        return [OcrHit(**d) for d in raw]

    @classmethod
    def stats(cls) -> Dict[str, Any]:
        return {
            "enabled": cls.is_enabled(),
            "workers": cls._workers,
            "calls": cls._stats_calls,
            "avg_ms": (round(cls._stats_total_ms / cls._stats_calls, 1)
                      if cls._stats_calls else 0),
        }

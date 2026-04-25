"""帧 diff + LRU OCR 缓存装饰器（Phase 0 加速）

原理：
  每次 OCR 前先算 ROI 的 16×16 灰度指纹（≤1ms），命中缓存直接返回。
  TTL 内同一帧不重复跑 OCR，loading/popup/静止大厅命中率 60-95%。

用法（已在 ocr_dismisser 接入）：
    from .ocr_cache import cached

    class OcrDismisser:
        @cached
        def _ocr_roi(self, screenshot, x1, y1, x2, y2, scale=2):
            # 原代码不动
            ...

调参（环境变量覆盖）：
    OCR_CACHE_TTL_SEC      默认 2.0
    OCR_CACHE_MAX_SIZE     默认 256
    OCR_CACHE_DISABLE      设任意值 = 关缓存（A/B 对比时用）

观测：
    metrics.summary() 里的 actions.ocr_roi.count 反映**真实**调用数；
    缓存命中通过 metrics.record('ocr_roi_cache_hit') 单独打点。
"""
from __future__ import annotations

import hashlib
import os
import time
from collections import OrderedDict
from typing import Any, Tuple

import cv2
import numpy as np

from . import metrics

# ────────────── 配置 ──────────────
_TTL_SEC: float = float(os.environ.get("OCR_CACHE_TTL_SEC", "2.0"))
_MAX_SIZE: int = int(os.environ.get("OCR_CACHE_MAX_SIZE", "256"))
_DISABLED: bool = bool(os.environ.get("OCR_CACHE_DISABLE"))

# ────────────── 状态 ──────────────
_CACHE: "OrderedDict[Tuple[Any, ...], Tuple[float, Any]]" = OrderedDict()
_HITS: int = 0
_MISSES: int = 0


def _fingerprint(crop: np.ndarray) -> bytes:
    """16×16 灰度指纹，约 0.3ms。

    抗压缩噪声（已 INTER_AREA 降采样），但对真实内容变化敏感。
    """
    if crop.size == 0:
        return b""
    small = cv2.resize(crop, (16, 16), interpolation=cv2.INTER_AREA)
    if small.ndim == 3:
        small = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    return hashlib.blake2b(small.tobytes(), digest_size=8).digest()


def cached(fn):
    """装饰 _ocr_roi(self, screenshot, x1, y1, x2, y2, scale=2)。

    被装饰函数的签名必须保持上面这个形状，
    因为缓存 key = (x1, y1, x2, y2, scale, fingerprint(crop))。
    """
    def wrap(self, screenshot, x1, y1, x2, y2, scale: int = 2):
        global _HITS, _MISSES

        if _DISABLED:
            return fn(self, screenshot, x1, y1, x2, y2, scale=scale)

        h, w = screenshot.shape[:2]
        crop = screenshot[int(h * y1):int(h * y2), int(w * x1):int(w * x2)]
        if crop.size == 0:
            return []

        fp = _fingerprint(crop)
        key = (round(x1, 4), round(y1, 4), round(x2, 4), round(y2, 4), int(scale), fp)
        now = time.time()

        cached_entry = _CACHE.get(key)
        if cached_entry is not None and now - cached_entry[0] < _TTL_SEC:
            _CACHE.move_to_end(key)
            _HITS += 1
            metrics.record("ocr_roi_cache_hit",
                           roi=f"{x1:.2f},{y1:.2f},{x2:.2f},{y2:.2f}",
                           scale=scale, age_ms=round((now - cached_entry[0]) * 1000, 1))
            return cached_entry[1]

        result = fn(self, screenshot, x1, y1, x2, y2, scale=scale)
        _CACHE[key] = (now, result)
        if len(_CACHE) > _MAX_SIZE:
            _CACHE.popitem(last=False)
        _MISSES += 1
        return result

    wrap.__wrapped__ = fn  # type: ignore[attr-defined]
    return wrap


def stats() -> dict:
    """命中统计（暴露给 /api/health 用）"""
    total = _HITS + _MISSES
    return {
        "hits": _HITS,
        "misses": _MISSES,
        "hit_rate": round(_HITS / total, 3) if total else 0.0,
        "size": len(_CACHE),
        "ttl_sec": _TTL_SEC,
        "max_size": _MAX_SIZE,
        "disabled": _DISABLED,
    }


def clear() -> None:
    """测试 / A-B 对比时手动清空"""
    global _HITS, _MISSES
    _CACHE.clear()
    _HITS = 0
    _MISSES = 0

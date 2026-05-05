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

import os
import time
from collections import OrderedDict
from typing import Any, List, Tuple

import cv2
import numpy as np

from . import metrics
from .adb_lite import phash, phash_distance

# ────────────── 配置 ──────────────
_TTL_SEC: float = float(os.environ.get("OCR_CACHE_TTL_SEC", "2.0"))
_MAX_SIZE: int = int(os.environ.get("OCR_CACHE_MAX_SIZE", "256"))
_DISABLED: bool = bool(os.environ.get("OCR_CACHE_DISABLE"))
# pHash Hamming 距离阈值：<= 此值 = 视为"同一帧"。游戏背景小动画通常 1-3 bit
_PHASH_THRESHOLD: int = int(os.environ.get("OCR_CACHE_PHASH_THRESHOLD", "4"))

# ────────────── 状态 ──────────────
# CACHE 结构：list of (key_extra, phash, timestamp, result)
# key_extra: ROI 用 (x1,y1,x2,y2,scale)；full-frame 用 "__full__"
_CACHE: "List[Tuple[Any, int, float, Any]]" = []
_HITS: int = 0
_MISSES: int = 0


def _fingerprint_phash(img: np.ndarray) -> int:
    """64-bit pHash（DCT-based），抗背景小动画 + 抗压缩噪声"""
    if img.size == 0:
        return 0
    return phash(img)


def _lookup(key_extra: Any, fp: int, now: float) -> Any:
    """在 _CACHE 里找 key_extra 相同 + phash 距离 <= 阈值 + 未过期 的项。
    LRU：命中后挪到末尾。
    """
    for idx in range(len(_CACHE) - 1, -1, -1):
        ke, fp_old, ts, result = _CACHE[idx]
        if now - ts >= _TTL_SEC:
            continue
        if ke != key_extra:
            continue
        if phash_distance(fp, fp_old) <= _PHASH_THRESHOLD:
            # 移到末尾（LRU）
            _CACHE.append(_CACHE.pop(idx))
            return result, ts
    return None


def _store(key_extra: Any, fp: int, now: float, result: Any) -> None:
    _CACHE.append((key_extra, fp, now, result))
    # 简单容量限制
    if len(_CACHE) > _MAX_SIZE:
        _CACHE.pop(0)


def cached(fn):
    """装饰 _ocr_roi(self, screenshot, x1, y1, x2, y2, scale=2)。
    用 ROI crop 的 pHash 做 fuzzy 匹配（Hamming <= _PHASH_THRESHOLD）。
    """
    def wrap(self, screenshot, x1, y1, x2, y2, scale: int = 2):
        global _HITS, _MISSES

        if _DISABLED:
            return fn(self, screenshot, x1, y1, x2, y2, scale=scale)

        h, w = screenshot.shape[:2]
        crop = screenshot[int(h * y1):int(h * y2), int(w * x1):int(w * x2)]
        if crop.size == 0:
            return []

        fp = _fingerprint_phash(crop)
        key_extra = (round(x1, 4), round(y1, 4), round(x2, 4), round(y2, 4), int(scale))
        now = time.time()

        hit = _lookup(key_extra, fp, now)
        if hit is not None:
            result, ts = hit
            _HITS += 1
            metrics.record("ocr_roi_cache_hit",
                           roi=f"{x1:.2f},{y1:.2f},{x2:.2f},{y2:.2f}",
                           scale=scale, age_ms=round((now - ts) * 1000, 1))
            return result

        result = fn(self, screenshot, x1, y1, x2, y2, scale=scale)
        _store(key_extra, fp, now, result)
        _MISSES += 1
        return result

    wrap.__wrapped__ = fn  # type: ignore[attr-defined]
    return wrap


def cached_full(fn):
    """装饰 _ocr_all(self, screenshot) — 全屏 OCR 缓存（pHash fuzzy）。
    游戏 popup 时背景动画通常使 pHash 变 1-3 bit，<=4 阈值能命中。
    """
    def wrap(self, screenshot):
        global _HITS, _MISSES

        if _DISABLED:
            return fn(self, screenshot)
        if screenshot is None or screenshot.size == 0:
            return fn(self, screenshot)

        fp = _fingerprint_phash(screenshot)
        key_extra = "__full__"
        now = time.time()

        hit = _lookup(key_extra, fp, now)
        if hit is not None:
            result, ts = hit
            _HITS += 1
            metrics.record("ocr_full_cache_hit",
                           age_ms=round((now - ts) * 1000, 1))
            return result

        result = fn(self, screenshot)
        _store(key_extra, fp, now, result)
        _MISSES += 1
        return result

    wrap.__wrapped__ = fn  # type: ignore[attr-defined]
    return wrap


def lookup_full(screenshot: np.ndarray):
    """全屏 OCR cache lookup. 给 async 路径用 (sync 路径走 @cached_full 装饰器).
    返回 (result, fp, now) 三元组:
      - cache hit  → (result, fp, now)  调用方用 result
      - cache miss → (None, fp, now)    调用方拿 fp/now 跑 OCR 后 store_full
      - 不可缓存   → (None, None, None) 调用方跳过 cache
    保持与 cached_full 完全相同的 key/phash/TTL 语义.
    """
    global _HITS
    if _DISABLED or screenshot is None or screenshot.size == 0:
        return (None, None, None)
    fp = _fingerprint_phash(screenshot)
    now = time.time()
    hit = _lookup("__full__", fp, now)
    if hit is not None:
        result, ts = hit
        _HITS += 1
        metrics.record("ocr_full_cache_hit",
                       age_ms=round((now - ts) * 1000, 1))
        return (result, fp, now)
    return (None, fp, now)


def store_full(fp: int, now: float, result) -> None:
    """配合 lookup_full 写回 cache. fp/now 必须来自同一次 lookup_full 返回值."""
    global _MISSES
    if _DISABLED or fp is None:
        return
    _store("__full__", fp, now, result)
    _MISSES += 1


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
        "phash_threshold": _PHASH_THRESHOLD,
        "disabled": _DISABLED,
    }


def clear() -> None:
    """测试 / A-B 对比时手动清空"""
    global _HITS, _MISSES
    _CACHE.clear()
    _HITS = 0
    _MISSES = 0

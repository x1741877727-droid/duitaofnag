"""智能等待原语 — 替代固定 sleep, 自适应快慢机.

核心思路:
  传统: tap → sleep 200ms 死等 → 下一帧
  智能: tap → 50ms 间隔抓帧 → 检测到变化立即返 (快机 ~100ms)
        / 一直没变化等到 max_wait_ms 上限 (慢机给足时间)

两个原语:
  - wait_for_change(prev_phash, ...): 等画面变化 (tap 后等 UI 反应)
  - wait_for_stable(...): 等画面稳定 (载入完成)

  二者复用同一抓帧 + phash 计算循环, 只是退出条件不同.

phash 距离阈值经验:
  < 5  画面基本没动 (同一帧 hash 容差)
  5-15 局部变化 (按钮按下 / 闪烁)
  >15  显著变化 (面板出现 / 消失)
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable, Optional

import numpy as np

logger = logging.getLogger(__name__)


def _hamming(a: int, b: int) -> int:
    """phash 64-bit Hamming 距离."""
    return bin(int(a) ^ int(b)).count("1")


async def wait_for_change(
    grab_fn: Callable[[], Awaitable[Optional[np.ndarray]]],
    phash_fn: Callable[[np.ndarray], int],
    *,
    prev_phash: int,
    change_threshold: int = 10,
    poll_ms: int = 50,
    max_wait_ms: int = 500,
    min_wait_ms: int = 0,
) -> tuple[Optional[np.ndarray], int, float, bool]:
    """tap 后等画面变化. 检测到 phash 距离 ≥ change_threshold 立即返.

    Args:
      grab_fn:        async 抓帧, 返 BGR ndarray 或 None
      phash_fn:       sync phash 算 (int 64-bit)
      prev_phash:     tap 前的 phash, 比较基准
      change_threshold: phash 距离 ≥ 此值视为"变了"
      poll_ms:        轮询间隔
      max_wait_ms:    最长等待 (慢机兜底)
      min_wait_ms:    最少等待 (防游戏 0ms 假动画)

    Returns:
      (last_frame, last_phash, elapsed_ms, changed)
      changed=True 时是真检测到变化; False 是超时退出.
    """
    t0 = time.perf_counter()
    last_frame = None
    last_phash = prev_phash

    if min_wait_ms > 0:
        await asyncio.sleep(min_wait_ms / 1000)

    while True:
        elapsed = (time.perf_counter() - t0) * 1000
        if elapsed >= max_wait_ms:
            return last_frame, last_phash, elapsed, False

        try:
            frame = await grab_fn()
        except Exception as e:
            logger.debug(f"[wait] grab err: {e}")
            await asyncio.sleep(poll_ms / 1000)
            continue
        if frame is None:
            await asyncio.sleep(poll_ms / 1000)
            continue

        last_frame = frame
        try:
            ph = phash_fn(frame)
            if ph:
                last_phash = ph
                if _hamming(prev_phash, ph) >= change_threshold:
                    elapsed = (time.perf_counter() - t0) * 1000
                    return last_frame, last_phash, elapsed, True
        except Exception:
            pass

        # 不够时间发起下次抓帧 → sleep 剩余 poll_ms
        await asyncio.sleep(poll_ms / 1000)


async def wait_for_stable(
    grab_fn: Callable[[], Awaitable[Optional[np.ndarray]]],
    phash_fn: Callable[[np.ndarray], int],
    *,
    stable_threshold: int = 5,
    stable_count: int = 2,
    poll_ms: int = 80,
    max_wait_ms: int = 1500,
) -> tuple[Optional[np.ndarray], int, float, bool]:
    """等画面稳定 (load 完成). 连续 stable_count 次 phash 距离 < threshold 退出.

    Args:
      stable_threshold: 连续帧之间 phash 距离 < 此值算"稳定"
      stable_count:     连续多少次稳定才退出 (默认 2)
      poll_ms:          轮询间隔 (默认 80, 慢一点节流)
      max_wait_ms:      最长等待

    Returns:
      (last_frame, last_phash, elapsed_ms, stable)
    """
    t0 = time.perf_counter()
    prev_phash = 0
    last_frame = None
    last_phash = 0
    consec_stable = 0

    while True:
        elapsed = (time.perf_counter() - t0) * 1000
        if elapsed >= max_wait_ms:
            return last_frame, last_phash, elapsed, False

        try:
            frame = await grab_fn()
        except Exception:
            await asyncio.sleep(poll_ms / 1000)
            continue
        if frame is None:
            await asyncio.sleep(poll_ms / 1000)
            continue

        last_frame = frame
        try:
            ph = phash_fn(frame)
            if ph:
                if prev_phash and _hamming(prev_phash, ph) < stable_threshold:
                    consec_stable += 1
                    if consec_stable >= stable_count:
                        elapsed = (time.perf_counter() - t0) * 1000
                        return last_frame, ph, elapsed, True
                else:
                    consec_stable = 0
                prev_phash = ph
                last_phash = ph
        except Exception:
            pass

        await asyncio.sleep(poll_ms / 1000)

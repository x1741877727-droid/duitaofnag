"""flows/team_join — v2 极简实现.

v1 phase_team_join 实测 ~22s 慢, 拆分:
  - am start ADB 调用: 单实例手动测 99ms, v1 里 19s ← 慢点
  - OCR 验证: 1-2s 正常

v1 慢的真因: phase_team_join 里有 dbg.log_screenshot (sync cv2.imwrite),
screenshot_collector.collect (sync PNG write + phash), decision_log set_input
等同步写盘. 默认 ThreadPoolExecutor 被这些任务卡住, run_in_executor 跑
am start 时排队等 18s.

v2 实现: 跳过所有 debug 写盘开销, 直接 am start + OCR ROI poll. 目标 < 3s.
业务逻辑 (轮询"取消准备") 跟 v1 一样.
"""
from __future__ import annotations

import asyncio
import logging

from ..ctx import RunContext

logger = logging.getLogger(__name__)

# 轮询参数 (照搬 v1)
POLL_INTERVAL = 0.5
MAX_ATTEMPTS = 20
READY_KEYWORDS = ("取消", "准备")


async def run_team_join(ctx: RunContext, scheme: str) -> bool:
    """队员通过 scheme 加入队伍. 极简版, 不写 v1 debug. 返 True=成功."""
    raw_adb = ctx.adb._adb if hasattr(ctx.adb, "_adb") else ctx.adb
    loop = asyncio.get_event_loop()

    # ── 1. am start scheme:// ──
    import time
    t0 = time.perf_counter()
    try:
        output = await loop.run_in_executor(
            None, raw_adb._cmd, "shell",
            f"am start -a android.intent.action.VIEW -d '{scheme}'",
        )
    except Exception as e:
        logger.error(f"[flow/team_join inst{ctx.instance_idx}] am start 抛: {e}")
        return False
    out_short = (output or "").strip()
    ms = (time.perf_counter() - t0) * 1000
    logger.info(f"[flow/team_join inst{ctx.instance_idx}] am start {ms:.0f}ms: {out_short[:80]}")
    if "Error" in out_short or not out_short:
        return False

    # ── 2. 轮询 OCR "取消准备" 验证已加入 ──
    # ocr_dismisser 是 v1 类, 但我们只调它的 _ocr_roi_named (无 debug 副作用)
    from backend.automation.ocr_dismisser import OcrDismisser
    ocr = OcrDismisser()
    try:
        from backend.automation.roi_config import all_names as _all_roi
        has_ready_roi = "team_ready_btn" in set(_all_roi())
    except Exception:
        has_ready_roi = False

    for attempt in range(1, MAX_ATTEMPTS + 1):
        await asyncio.sleep(POLL_INTERVAL)
        try:
            shot = await ctx.adb.screenshot()
        except Exception as e:
            logger.debug(f"[flow/team_join inst{ctx.instance_idx}] screenshot err: {e}")
            continue
        if shot is None:
            continue
        try:
            if has_ready_roi:
                hits = await asyncio.to_thread(ocr._ocr_roi_named, shot, "team_ready_btn")
            else:
                hits = await ocr._ocr_all_async(shot)
        except Exception as e:
            logger.debug(f"[flow/team_join inst{ctx.instance_idx}] OCR err: {e}")
            continue
        for h in hits:
            t = getattr(h, "text", "") or ""
            if READY_KEYWORDS[0] in t and READY_KEYWORDS[1] in t:
                logger.info(
                    f"[flow/team_join inst{ctx.instance_idx}] 加入完成 "
                    f"({attempt}×{POLL_INTERVAL}s, ROI={'team_ready_btn' if has_ready_roi else 'fullscreen'})"
                )
                return True

    logger.warning(f"[flow/team_join inst{ctx.instance_idx}] 轮询 {MAX_ATTEMPTS} 次超时")
    return False

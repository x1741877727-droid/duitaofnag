"""P3a — 队长建队伍 (v2 真实现, event-driven).

设计: 每 round 完整跑完 1 个 sub_step (tap + 内部 poll verify 直到结果),
而不是 "tap → 等 round_interval → 下 round verify". 总耗时跟 UI 渲染速度对齐,
永远等结果不等死时间.

5 子步 (每 round 1 个):
  open    → tap 组队按钮 (22, 277) + 等"组队码"文字出现     ~0.5-1.5s
  tab     → tap 组队码 tab (309, 520) + 等"二维码组队"出现  ~0.3-1s
  qr      → tap 二维码组队 (156, 435) + 等 QR 解出非空      ~0.5-1.5s
  decode  → QR 解码 + HTTP fetch scheme                      ~1-2s
  close   → tap 空白 (720, 270) 关 panel                     ~0.3s

每 sub_step 内部:
  await adb.tap(x, y)             # 发 tap (200-300ms)
  await asyncio.sleep(0.05)       # 给 UI 一点缓冲
  while elapsed < timeout (1.5s):
    shot = await screenshot()      # 截图 (~3ms)
    if verify(shot): return True   # 立刻进下一步
    await asyncio.sleep(0.05)      # 短轮询
  # timeout → 重 tap (retry 1 次)
  # 还失败 → step_fail

预期 P3a 总: ~3-4s (vs 旧 round-based 7-8s).
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Optional, Callable, Awaitable

from ..ctx import RunContext
from ..perception.yolo import Roi
from ..phase_base import (
    PhaseStep, PhaseAction,
    step_next, step_retry, step_fail,
)

logger = logging.getLogger(__name__)

# ─────────── 固定坐标 (LDPlayer 960×540, 历史 26000+ 决策统计) ───────────
FIXED_TAPS = {
    "open":  (22, 277),     # 主菜单"组队"按钮 (26/32 = 81% 一致)
    "tab":   (309, 520),    # 面板底部"组队码" tab (20/30 = 67%)
    "qr":    (156, 435),    # "二维码组队"入口 (30/30 = 100% 一致)
}

# Tap 后验证关键词 (OCR 找到 = tap 成功)
VERIFY_KEYWORDS = {
    "open": ["组队码", "二维码"],
    "tab":  ["二维码组队"],
}

# ─────────── 守门参数 ───────────
TAP_VERIFY_TIMEOUT = 1.5        # 单次 tap 后 poll verify 最大等待
TAP_VERIFY_RETRY = 2            # tap 失败重试次数 (含初次共 3 次)
POLL_INTERVAL = 0.05            # poll 间隔
INITIAL_WAIT_AFTER_TAP = 0.05   # tap 后等 UI 开始响应的最小时间
QR_DECODE_RETRY = 5

# ROI
QR_DECODE_CROP_ROI = (0.30, 0.20, 0.85, 0.80)
QR_DECODE_SCALE = 2
VERIFY_OCR_ROI = (0.10, 0.40, 0.90, 1.0)


class P3aTeamCreate:
    name = "P3a"
    max_seconds = 30.0
    round_interval_s = 0.05   # round 之间几乎无 sleep, event-driven 内部已 poll

    async def enter(self, ctx: RunContext) -> None:
        ctx.reset_phase_state()
        ctx._p3a = {
            "sub_step": "open",
            "qr_url": "",
        }

    async def handle_frame(self, ctx: RunContext) -> PhaseStep:
        st = ctx._p3a
        sub = st["sub_step"]

        # 时间戳: round_start 已 set, mark capture_done 因为我们用最新 shot
        # yolo_start/done 在每个 sub_step 内部 mark

        if sub == "open":
            return await self._step_open_close_panel_open(ctx)
        if sub == "tab":
            return await self._step_tab(ctx)
        if sub == "qr":
            return await self._step_qr(ctx)
        if sub == "decode":
            return await self._step_decode(ctx)
        if sub == "close":
            return await self._step_close(ctx)
        if sub == "done":
            ctx.mark("yolo_start"); ctx.mark("yolo_done"); ctx.mark("decide")
            scheme_short = (ctx.game_scheme_url or "")[:48]
            return step_next(
                note=f"P3a 完成 scheme={scheme_short}",
                outcome_hint="team_create_ok",
            )

        ctx.mark("yolo_start"); ctx.mark("yolo_done"); ctx.mark("decide")
        return step_fail(note=f"P3a unknown sub_step={sub}", outcome_hint="bug")

    # ════════════════════════════════════════
    # 子步骤 (event-driven, 每 round 跑完一个)
    # ════════════════════════════════════════

    async def _step_open_close_panel_open(self, ctx: RunContext) -> PhaseStep:
        """Tap 组队按钮 → 等"组队码"文字 (面板打开)."""
        coord = FIXED_TAPS["open"]
        kw = VERIFY_KEYWORDS["open"]
        ok, elapsed_ms, last_note = await self._tap_then_poll_ocr(
            ctx, coord[0], coord[1], "p3a_open", kw,
            timeout=TAP_VERIFY_TIMEOUT, retry=TAP_VERIFY_RETRY,
        )
        if ok:
            ctx._p3a["sub_step"] = "tab"
            return step_retry(
                note=f"P3a[open] panel 开 ({elapsed_ms:.0f}ms) → tab",
                outcome_hint="open_ok",
            )
        return step_fail(
            note=f"P3a[open] timeout {elapsed_ms:.0f}ms: {last_note}",
            outcome_hint="open_fail",
        )

    async def _step_tab(self, ctx: RunContext) -> PhaseStep:
        coord = FIXED_TAPS["tab"]
        kw = VERIFY_KEYWORDS["tab"]
        ok, elapsed_ms, last_note = await self._tap_then_poll_ocr(
            ctx, coord[0], coord[1], "p3a_tab", kw,
            timeout=TAP_VERIFY_TIMEOUT, retry=TAP_VERIFY_RETRY,
        )
        if ok:
            ctx._p3a["sub_step"] = "qr"
            return step_retry(
                note=f"P3a[tab] 切到组队码 ({elapsed_ms:.0f}ms) → qr",
                outcome_hint="tab_ok",
            )
        return step_fail(
            note=f"P3a[tab] timeout {elapsed_ms:.0f}ms: {last_note}",
            outcome_hint="tab_fail",
        )

    async def _step_qr(self, ctx: RunContext) -> PhaseStep:
        """Tap 二维码组队 → poll QR 解码出非空 = 成功 (二维码已显示)."""
        coord = FIXED_TAPS["qr"]
        st = ctx._p3a

        async def verify_qr(shot) -> bool:
            qr_url = await asyncio.to_thread(self._try_decode_qr, shot)
            if qr_url:
                st["qr_url"] = qr_url
                return True
            return False

        ok, elapsed_ms, last_note = await self._tap_then_poll(
            ctx, coord[0], coord[1], "p3a_qr",
            verify_fn=verify_qr,
            timeout=2.0,    # QR 显示动画 ~0.5-1s
            retry=TAP_VERIFY_RETRY,
        )
        if ok:
            st["sub_step"] = "decode"
            return step_retry(
                note=f"P3a[qr] QR 解出 ({elapsed_ms:.0f}ms) → decode",
                outcome_hint="qr_ok",
            )
        return step_fail(
            note=f"P3a[qr] timeout {elapsed_ms:.0f}ms",
            outcome_hint="qr_fail",
        )

    async def _step_decode(self, ctx: RunContext) -> PhaseStep:
        """QR 已解出 (st['qr_url']), 这步只跑 HTTP fetch scheme."""
        ctx.mark("yolo_start"); ctx.mark("yolo_done")
        st = ctx._p3a
        qr_url = st["qr_url"]
        if not qr_url:
            ctx.mark("decide")
            return step_fail(note="P3a[decode] qr_url 空", outcome_hint="decode_no_url")

        try:
            scheme = await asyncio.to_thread(self._fetch_scheme, qr_url)
        except Exception as e:
            logger.warning(f"P3a[decode] fetch err: {e}")
            scheme = ""

        ctx.mark("decide")
        if not scheme:
            return step_fail(
                note=f"P3a[decode] fetch scheme 空",
                outcome_hint="fetch_fail",
            )

        ctx.game_scheme_url = scheme
        st["sub_step"] = "close"
        return step_retry(
            note=f"P3a[decode] scheme={scheme[:48]} → close",
            outcome_hint="scheme_ok",
        )

    async def _step_close(self, ctx: RunContext) -> PhaseStep:
        """关 panel: tap 屏幕右下空白. 用户实测有效, 不严格验证."""
        ctx.mark("yolo_start"); ctx.mark("yolo_done")

        # tap 空白
        ctx.mark("tap_send")
        try:
            await ctx.adb.tap(720, 270)
        except Exception as e:
            logger.debug(f"P3a[close] tap err: {e}")
        ctx.mark("tap_done")
        # 给 UI 一点关闭动画时间
        await asyncio.sleep(0.2)

        ctx._p3a["sub_step"] = "done"
        ctx.mark("decide")
        return step_retry(
            note="P3a[close] tap (720,270) 关 panel → done",
            outcome_hint="close_done",
        )

    # ════════════════════════════════════════
    # Event-driven helper: tap + poll verify
    # ════════════════════════════════════════

    async def _tap_then_poll(
        self,
        ctx: RunContext,
        x: int, y: int, tap_target: str,
        verify_fn: Callable[..., Awaitable[bool]],
        timeout: float = 1.5,
        retry: int = 2,
    ) -> tuple[bool, float, str]:
        """
        Tap 一次 → poll verify 直到成功或 timeout. 失败重试 retry 次.

        Returns:
            (success, total_elapsed_ms, last_note)
        """
        t0 = time.perf_counter()
        for attempt in range(retry + 1):  # initial + retry
            # tap
            ctx.mark("tap_send")
            try:
                await ctx.adb.tap(x, y)
            except Exception as e:
                logger.debug(f"[{tap_target}] tap err: {e}")
            ctx.mark("tap_done")

            await asyncio.sleep(INITIAL_WAIT_AFTER_TAP)

            # poll verify
            ctx.mark("yolo_start")
            poll_t0 = time.perf_counter()
            while time.perf_counter() - poll_t0 < timeout:
                try:
                    shot = await ctx.adb.screenshot()
                except Exception:
                    shot = None
                if shot is not None:
                    try:
                        ok = await verify_fn(shot)
                    except Exception as e:
                        logger.debug(f"[{tap_target}] verify err: {e}")
                        ok = False
                    if ok:
                        ctx.mark("yolo_done")
                        ctx.mark("decide")
                        return (True, (time.perf_counter() - t0) * 1000,
                                f"attempt {attempt+1} verify ok")
                await asyncio.sleep(POLL_INTERVAL)
            ctx.mark("yolo_done")

            if attempt < retry:
                logger.debug(f"[{tap_target}] attempt {attempt+1} timeout, retry")
                await asyncio.sleep(0.2)   # 给 UI 多点时间再 retry

        ctx.mark("decide")
        return (False, (time.perf_counter() - t0) * 1000,
                f"{retry + 1} attempts timeout")

    async def _tap_then_poll_ocr(
        self,
        ctx: RunContext,
        x: int, y: int, tap_target: str,
        keywords: list,
        timeout: float = 1.5,
        retry: int = 2,
    ) -> tuple[bool, float, str]:
        """tap 后 poll OCR 找 keywords. Wraps _tap_then_poll."""
        async def verify(shot) -> bool:
            try:
                hits = await ctx.ocr.recognize(shot, roi=Roi(*VERIFY_OCR_ROI))
            except Exception:
                hits = []
            for h in hits:
                text = h.text if hasattr(h, "text") else (
                    h.get("text", "") if isinstance(h, dict) else ""
                )
                for kw in keywords:
                    if kw in text:
                        return True
            return False

        return await self._tap_then_poll(
            ctx, x, y, tap_target, verify, timeout, retry,
        )

    # ════════════════════════════════════════
    # QR 解码 + HTTP fetch (跟 v1 一致)
    # ════════════════════════════════════════

    def _try_decode_qr(self, shot) -> str:
        if shot is None:
            return ""
        try:
            import cv2
        except Exception:
            return ""

        h_img, w_img = shot.shape[:2]
        x1, y1, x2, y2 = QR_DECODE_CROP_ROI
        crop = shot[int(h_img * y1):int(h_img * y2),
                    int(w_img * x1):int(w_img * x2)]
        if crop is None or crop.size == 0:
            return ""

        big = cv2.resize(
            crop, (0, 0), fx=QR_DECODE_SCALE, fy=QR_DECODE_SCALE,
            interpolation=cv2.INTER_CUBIC,
        )
        gray = cv2.cvtColor(big, cv2.COLOR_BGR2GRAY)

        try:
            from pyzbar import pyzbar as _pyzbar
            has_pyzbar = True
        except Exception:
            _pyzbar = None
            has_pyzbar = False

        if has_pyzbar:
            for prep in ("gray", "otsu", "adaptive"):
                try:
                    if prep == "gray":
                        img = gray
                    elif prep == "otsu":
                        _, img = cv2.threshold(gray, 0, 255,
                                               cv2.THRESH_BINARY + cv2.THRESH_OTSU)
                    else:
                        img = cv2.adaptiveThreshold(
                            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                            cv2.THRESH_BINARY, 21, 5,
                        )
                    res = _pyzbar.decode(img)
                    if res:
                        d = res[0].data.decode("utf-8", errors="ignore")
                        if d:
                            return d
                except Exception:
                    continue

        # cv2 fallback
        for prep in ("otsu", "hard128"):
            try:
                if prep == "otsu":
                    _, img = cv2.threshold(gray, 0, 255,
                                           cv2.THRESH_BINARY + cv2.THRESH_OTSU)
                else:
                    _, img = cv2.threshold(gray, 128, 255, cv2.THRESH_BINARY)
                d, _, _ = cv2.QRCodeDetector().detectAndDecode(img)
                if d:
                    return d
            except Exception:
                continue

        return ""

    def _fetch_scheme(self, qr_url: str) -> str:
        import urllib.request
        req = urllib.request.Request(
            qr_url,
            headers={"User-Agent": "Mozilla/5.0 (Linux; Android 7.1.2)"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        m = re.search(r'(pubgmhd\d+://[^"\']+)', html)
        return m.group(1) if m else ""

"""P3a — 队长建队伍 (v2 真实现, 不再调 v1 600 行).

设计 (5 子步 + 状态机, 每 round 1 动作):
  open    → tap 组队按钮 (22, 277)        固定坐标
  tab     → tap 组队码 tab (309, 520)     固定坐标
  qr      → tap 二维码组队 (156, 435)     固定坐标
  decode  → 截屏 QR 解码 + HTTP fetch scheme
  close   → 关闭组队面板 (yolo close_x 动态)

每个 tap 步骤的双重护栏:
  Tap 前: yolo 扫一眼有没有 close_x 弹窗 (50ms), 有先关 (intercept). 50ms 内未挡 → tap.
  Tap 后: 下一 round 截图找"该出现的标志" (OCR/QR). 没找到 → retry 重 tap. 3 次 FAIL.

坐标来源: 26000+ 历史 decision 日志统计中位数 + 最高频 (>80% 一致).
v1 26 秒 → v2 目标 12-18 秒. 砍掉:
  - v1 OCR 找按钮 3-5 次尝试 (单步 1-2s) → 固定坐标 + 验证标志 (单步 0.3-0.6s)
  - v1 sub-step 内部 sleep 0.3-0.5s 多次 → runner round_interval 一次, retry 兜底

注: yolo team_create_btn class 训练好后可启用替代固定坐标 (UI 改版鲁棒).
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional

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
    "tab":   (309, 520),    # 面板底部"组队码" tab (20/30 = 67%, x 偶尔 ±15)
    "qr":    (156, 435),    # "二维码组队"入口 (30/30 = 100% 一致)
}

# ─────────── Tap 后验证标志 (OCR 关键词) ───────────
VERIFY_KEYWORDS = {
    "open":  ["组队码", "二维码"],     # 面板打开后底部应有这些字
    "tab":   ["二维码组队"],            # 切到组队码 tab 后中间有"二维码组队"按钮
    "qr":    [],                        # qr 步用 QR 检测代替 OCR
}

# ─────────── 重试 / 守门 ───────────
TAP_VERIFY_RETRY = 3            # tap 后验证失败重 tap 次数
QR_DECODE_RETRY = 5             # QR 解码尝试次数 (跟 v1 一致)
POPUP_INTERCEPT_LIMIT = 5       # 单 sub_step 内 popup 拦截上限 (防死循环)

# ─────────── ROI ───────────
QR_DECODE_CROP_ROI = (0.30, 0.20, 0.85, 0.80)   # QR 大致区域
QR_DECODE_SCALE = 2                              # crop 后放大倍数

VERIFY_OCR_ROI = (0.10, 0.50, 0.85, 1.0)         # OCR 验证 ROI (panel 范围, 砍半节省 ~500ms)
POPUP_CHECK_ROI = (0.50, 0.0, 1.0, 0.5)          # popup yolo 检测 ROI (右上 + 顶部)


class P3aTeamCreate:
    name = "P3a"
    max_seconds = 45.0
    round_interval_s = 0.2   # 0.3 → 0.2 砍 7 tap × 100ms = 700ms

    async def enter(self, ctx: RunContext) -> None:
        ctx.reset_phase_state()
        # 状态机字段挂在 ctx 上 (跨 round, runner 不重置)
        ctx._p3a = {
            "sub_step": "open",          # open → tab → qr → decode → close → done
            "tap_done": False,           # 当前 sub_step 是否已 tap
            "tap_retry": 0,              # 当前 sub_step 验证失败重 tap 次数
            "popup_intercept_count": 0,  # popup 拦截累计 (防死循环)
            "qr_decode_attempts": 0,     # qr decode 尝试次数
            "qr_url": "",                # 解出的二维码 URL
        }

    async def handle_frame(self, ctx: RunContext) -> PhaseStep:
        shot = ctx.current_shot
        if shot is None:
            ctx.mark("yolo_start"); ctx.mark("yolo_done"); ctx.mark("decide")
            return step_retry(note="P3a: no shot")

        st = ctx._p3a
        sub = st["sub_step"]

        # ── 1. Tap 前弹窗检查: 只在 "open" 子步 (panel 未打开) 跑.
        # 进入 tab/qr 后 panel 已打开, yolo close_x 检出的多半是 panel 自带 X,
        # 误识为 popup 会把 panel 关掉 → 用户感受"组队码开关循环" (bug 已修).
        # decode/close 本来就不 tap, 不查.
        if sub == "open":
            ctx.mark("yolo_start")
            popup_tap = await self._find_popup(ctx, shot)
            ctx.mark("yolo_done")
            if popup_tap and st["popup_intercept_count"] < POPUP_INTERCEPT_LIMIT:
                x, y = popup_tap
                ctx.add_blacklist(x, y, ttl=3.0)
                st["popup_intercept_count"] += 1
                st["tap_done"] = False
                ctx.mark("decide")
                return step_retry(
                    note=f"P3a[open]: popup intercept @({x},{y}) "
                         f"({st['popup_intercept_count']}/{POPUP_INTERCEPT_LIMIT})",
                    outcome_hint="popup_intercept",
                    action=PhaseAction(kind="tap", x=x, y=y, target="popup_close"),
                )
        else:
            ctx.mark("yolo_start"); ctx.mark("yolo_done")

        # ── 2. 按 sub_step 分支 ──
        if sub == "decode":
            return await self._step_decode(ctx, shot)
        if sub == "close":
            return await self._step_close(ctx, shot)
        if sub == "done":
            ctx.mark("decide")
            scheme_short = (ctx.game_scheme_url or "")[:40]
            return step_next(
                note=f"P3a 完成 scheme={scheme_short}",
                outcome_hint="team_create_ok",
            )

        # open / tab / qr 三个固定 tap 步
        return await self._step_fixed_tap(ctx, shot, sub)

    # ════════════════════════════════════════
    # 子步骤实现
    # ════════════════════════════════════════

    async def _step_fixed_tap(
        self, ctx: RunContext, shot, sub: str,
    ) -> PhaseStep:
        """open / tab / qr 共用: tap 固定坐标 → 下 round 验证."""
        st = ctx._p3a
        coord = FIXED_TAPS[sub]
        keywords = VERIFY_KEYWORDS[sub]

        if not st["tap_done"]:
            # 第一次进, 还没 tap → tap 固定坐标
            st["tap_done"] = True
            ctx.mark("decide")
            return step_retry(
                note=f"P3a[{sub}]: fixed tap@({coord[0]},{coord[1]})",
                outcome_hint=f"tap_{sub}",
                action=PhaseAction(
                    kind="tap", x=coord[0], y=coord[1],
                    target=f"p3a_{sub}_fixed", conf=1.0,
                ),
            )

        # 已 tap → 验证: 找 keywords (sub=qr 用 QR 检测)
        verified = False
        if sub == "qr":
            try:
                qr_data = await asyncio.to_thread(self._try_decode_qr, shot)
            except Exception as e:
                logger.debug(f"P3a[qr] decode probe err: {e}")
                qr_data = ""
            if qr_data:
                st["qr_url"] = qr_data
                verified = True
        else:
            verified = await self._ocr_contains_any(ctx, shot, keywords)

        ctx.mark("decide")
        if verified:
            next_sub = {"open": "tab", "tab": "qr", "qr": "decode"}[sub]
            st["sub_step"] = next_sub
            st["tap_done"] = False
            st["tap_retry"] = 0
            st["popup_intercept_count"] = 0
            note = f"P3a[{sub}] verify OK → {next_sub}"
            if sub == "qr":
                note += f" (qr_url len={len(st['qr_url'])})"
            return step_retry(note=note, outcome_hint=f"verify_ok_{sub}")

        # 验证失败 → 是否重 tap?
        st["tap_retry"] += 1
        if st["tap_retry"] >= TAP_VERIFY_RETRY:
            return step_fail(
                note=f"P3a[{sub}]: verify fail 3 次, FAIL",
                outcome_hint=f"verify_fail_{sub}",
            )
        # 重 tap (下 round 进, 因为 tap_done 设 False)
        st["tap_done"] = False
        return step_retry(
            note=f"P3a[{sub}] verify miss, retry tap "
                 f"({st['tap_retry']}/{TAP_VERIFY_RETRY})",
            outcome_hint=f"verify_miss_{sub}",
        )

    async def _step_decode(self, ctx: RunContext, shot) -> PhaseStep:
        """QR 解码 + HTTP fetch scheme. 多策略尝试."""
        st = ctx._p3a
        st["qr_decode_attempts"] += 1

        qr_url = st["qr_url"]
        if not qr_url:
            try:
                qr_url = await asyncio.to_thread(self._try_decode_qr, shot)
            except Exception as e:
                logger.debug(f"P3a[decode] err: {e}")
                qr_url = ""

        if not qr_url:
            if st["qr_decode_attempts"] >= QR_DECODE_RETRY:
                ctx.mark("decide")
                return step_fail(
                    note=f"P3a[decode] {QR_DECODE_RETRY} 次全 miss",
                    outcome_hint="qr_decode_fail",
                )
            ctx.mark("decide")
            return step_retry(
                note=f"P3a[decode] miss "
                     f"{st['qr_decode_attempts']}/{QR_DECODE_RETRY}",
                outcome_hint="qr_decoding",
            )

        # 解到 URL → HTTP fetch scheme
        try:
            scheme = await asyncio.to_thread(self._fetch_scheme, qr_url)
        except Exception as e:
            logger.warning(f"P3a[decode] fetch scheme err: {e}")
            scheme = ""

        ctx.mark("decide")
        if not scheme:
            return step_fail(
                note=f"P3a[decode] fetch scheme 空 (qr_url={qr_url[:40]})",
                outcome_hint="fetch_fail",
            )

        ctx.game_scheme_url = scheme
        st["sub_step"] = "close"
        st["tap_done"] = False
        st["tap_retry"] = 0
        st["popup_intercept_count"] = 0
        return step_retry(
            note=f"P3a[decode] OK scheme={scheme[:48]}",
            outcome_hint="scheme_ok",
        )

    async def _step_close(self, ctx: RunContext, shot) -> PhaseStep:
        """关闭组队面板. 简单粗暴: 点 1 次空白 → done (不严格验证).

        历史教训:
        - yolo 'lobby' class 看到角色画面就命中 — 误判 (panel 挂着也算)
        - 模板 lobby_start_btn 用户实测不可靠 — UI 版本 / 像素差异
        - 强行严格验证 → 永远 done 不了, 卡死

        用户原话: '我代码点空白已经把 panel 关掉了, 你一直不点击模式切换'.
        采纳: 点空白 1 次 = panel 关掉 = done. P4 接手, 自己 tap 模式名.
        """
        st = ctx._p3a
        ctx.mark("yolo_start"); ctx.mark("yolo_done")

        if not st.get("blank_done", False):
            st["blank_done"] = True
            ctx.mark("decide")
            return step_retry(
                note="P3a[close] tap 空白 (720, 270) 关 panel",
                outcome_hint="tap_blank",
                action=PhaseAction(
                    kind="tap", x=720, y=270,
                    target="blank_close_panel", conf=0.5,
                ),
            )

        # 已点过空白, 直接 done. P4 接手.
        st["sub_step"] = "done"
        ctx.mark("decide")
        return step_retry(
            note="P3a[close] 空白点过, panel 关掉, → P4",
            outcome_hint="back_to_lobby",
        )

    # ════════════════════════════════════════
    # 工具方法
    # ════════════════════════════════════════

    async def _find_popup(self, ctx: RunContext, shot) -> Optional[tuple]:
        """tap 前轻量 yolo 检测: 有 close_x 弹窗就返 (cx, cy), 没返 None.
        ROI 限右上 + 顶部 (popup 通常在这), 30-50ms."""
        try:
            dets = await ctx.yolo.detect(
                shot, roi=Roi(*POPUP_CHECK_ROI), conf_thresh=0.40,
            )
        except Exception as e:
            logger.debug(f"P3a popup check err: {e}")
            return None
        for d in dets:
            if d.name == "close_x" and d.conf >= 0.50:
                if not ctx.is_blacklisted(d.cx, d.cy):
                    return (d.cx, d.cy)
        return None

    async def _ocr_contains_any(
        self, ctx: RunContext, shot, keywords: list,
    ) -> bool:
        """OCR 中下半屏看有没有任何 keyword. 200ms 内返."""
        if not keywords:
            return False
        try:
            hits = await ctx.ocr.recognize(shot, roi=Roi(*VERIFY_OCR_ROI))
        except Exception as e:
            logger.debug(f"P3a OCR verify err: {e}")
            return False
        for h in hits:
            text = h.text if hasattr(h, "text") else (
                h.get("text", "") if isinstance(h, dict) else ""
            )
            for kw in keywords:
                if kw in text:
                    return True
        return False

    def _try_decode_qr(self, shot) -> str:
        """对当前帧 crop ROI + 跑 5 策略 QR 解码 (跟 v1 一致)."""
        if shot is None:
            return ""
        try:
            import cv2
            import numpy as np  # noqa: F401  (cv2 内部用)
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

        # ① pyzbar 灰度
        if has_pyzbar:
            try:
                res = _pyzbar.decode(gray)
                if res:
                    d = res[0].data.decode("utf-8", errors="ignore")
                    if d:
                        return d
            except Exception:
                pass
        # ② pyzbar + OTSU
        if has_pyzbar:
            try:
                _, otsu = cv2.threshold(gray, 0, 255,
                                        cv2.THRESH_BINARY + cv2.THRESH_OTSU)
                res = _pyzbar.decode(otsu)
                if res:
                    d = res[0].data.decode("utf-8", errors="ignore")
                    if d:
                        return d
            except Exception:
                pass
        # ③ pyzbar + adaptive
        if has_pyzbar:
            try:
                adapt = cv2.adaptiveThreshold(
                    gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                    cv2.THRESH_BINARY, 21, 5,
                )
                res = _pyzbar.decode(adapt)
                if res:
                    d = res[0].data.decode("utf-8", errors="ignore")
                    if d:
                        return d
            except Exception:
                pass
        # ④ cv2 + OTSU
        try:
            _, otsu = cv2.threshold(gray, 0, 255,
                                    cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            d, _, _ = cv2.QRCodeDetector().detectAndDecode(otsu)
            if d:
                return d
        except Exception:
            pass
        # ⑤ cv2 + hard 128
        try:
            _, hard = cv2.threshold(gray, 128, 255, cv2.THRESH_BINARY)
            d, _, _ = cv2.QRCodeDetector().detectAndDecode(hard)
            if d:
                return d
        except Exception:
            pass
        return ""

    def _fetch_scheme(self, qr_url: str) -> str:
        """HTTP GET qr_url, 从 HTML 里 regex 提 pubgmhd<digits>://... scheme."""
        import urllib.request
        req = urllib.request.Request(
            qr_url,
            headers={"User-Agent": "Mozilla/5.0 (Linux; Android 7.1.2)"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        m = re.search(r'(pubgmhd\d+://[^"\']+)', html)
        return m.group(1) if m else ""

"""
分层弹窗清理器
速度优先：模板匹配(20ms) → 颜色检测(5ms) → RapidOCR(100ms) → 固定坐标(0ms)
"""

import asyncio
import logging
from dataclasses import dataclass

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# ── 关键词配置 ──

# 勾选类（点了不关闭，只打勾，之后还需要找X）
CHECKBOX_KEYWORDS = ["今日内不再弹出", "今日不再弹出", "不再弹出", "不再提醒"]

# 关闭类（点了直接关闭弹窗）
CLOSE_KEYWORDS = ["关闭"]

# 确认类（点了关闭弹窗）
CONFIRM_KEYWORDS = ["确定", "确认", "知道了", "我知道了", "同意", "已了解",
                    "不需要", "暂不", "跳过"]

# 大厅标志
LOBBY_KEYWORDS = ["开始游戏"]

# 弹窗指标（有这些说明还有弹窗遮挡）
POPUP_INDICATORS = ["活动", "公告", "更新", "奖励", "限时", "立即前往",
                    "前往观看", "邀请"]

# 固定X按钮位置（从实测中收集，按优先级排序）
FIXED_X_POSITIONS = [
    (1217, 92),   # 活动弹窗右上X（琉璃星纱、新春共创赛等）
    (1227, 47),   # 活动详情页右上X（老六日等）
    (1092, 97),   # 公告弹窗X
]


@dataclass
class OcrHit:
    text: str
    cx: int
    cy: int
    confidence: float


@dataclass
class DismissResult:
    success: bool
    popups_closed: int
    final_state: str
    rounds: int


class OcrDismisser:
    """分层弹窗清理器：模板 → 颜色 → OCR → 固定坐标"""

    def __init__(self, max_rounds: int = 25, interval: float = 1.5):
        self.max_rounds = max_rounds
        self.interval = interval
        self._ocr = None

    # ── 层1: 模板匹配（由外部matcher提供，这里不重复） ──

    # ── 层2: 颜色检测 — 在右上角找X按钮颜色簇 ──

    def _find_x_by_color(self, screenshot: np.ndarray) -> tuple[int, int] | None:
        """在右上1/4区域找白色/浅灰色X按钮的像素簇"""
        h, w = screenshot.shape[:2]
        # 只扫右上角 (弹窗X几乎都在这里)
        roi = screenshot[0:h//3, w*2//3:]
        ox, oy = w*2//3, 0

        # 白色/浅灰X按钮: 高亮像素在暗背景上
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        # 找亮度>200的像素（X按钮通常是白色）
        _, bright = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)
        # 找轮廓
        contours, _ = cv2.findContours(bright, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in contours:
            area = cv2.contourArea(c)
            x, y, cw, ch = cv2.boundingRect(c)
            # X按钮大约 15x15 到 40x40 像素
            if 100 < area < 2000 and 0.5 < cw/max(ch,1) < 2.0:
                cx = ox + x + cw // 2
                cy = oy + y + ch // 2
                return (cx, cy)
        return None

    # ── 层3: RapidOCR ──

    def _get_ocr(self):
        if self._ocr is None:
            logger.info("初始化 RapidOCR ...")
            from rapidocr import RapidOCR
            self._ocr = RapidOCR()
            logger.info("RapidOCR 初始化完成")
        return self._ocr

    def ocr_screen(self, screenshot: np.ndarray) -> list[OcrHit]:
        """RapidOCR识别，返回文字列表"""
        if not isinstance(screenshot, np.ndarray):
            return []
        ocr = self._get_ocr()
        result = ocr(screenshot)
        hits = []
        if result and result.boxes is not None:
            for box, text, conf in zip(result.boxes, result.txts, result.scores):
                # box: [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                cx = int(sum(xs) / 4)
                cy = int(sum(ys) / 4)
                hits.append(OcrHit(text=text, cx=cx, cy=cy, confidence=conf))
        return hits

    # ── 工具方法 ──

    def _find_keyword_hit(self, hits: list[OcrHit], keywords: list[str]) -> OcrHit | None:
        for kw in keywords:
            for hit in hits:
                if kw in hit.text:
                    return hit
        return None

    def _has_any_keyword(self, hits: list[OcrHit], keywords: list[str]) -> bool:
        all_text = " ".join(h.text for h in hits)
        return any(kw in all_text for kw in keywords)

    def _is_at_lobby(self, hits: list[OcrHit]) -> bool:
        has_lobby = self._has_any_keyword(hits, LOBBY_KEYWORDS)
        has_popup = self._has_any_keyword(hits, POPUP_INDICATORS)
        return has_lobby and not has_popup

    # ── 主循环 ──

    async def dismiss_all(self, device, matcher=None) -> DismissResult:
        popups_closed = 0
        no_change_count = 0

        for rnd in range(self.max_rounds):
            shot = await device.screenshot()
            if shot is None:
                await asyncio.sleep(1)
                continue

            # ━━ 层1: 模板匹配找X ━━ (~20ms)
            if matcher:
                x_hit = matcher.find_close_button(shot)
                if x_hit and x_hit.confidence > 0.80:
                    logger.info(f"[R{rnd+1}] 模板X: {x_hit.name} {x_hit.confidence:.2f} @ ({x_hit.cx},{x_hit.cy})")
                    await device.tap(x_hit.cx, x_hit.cy)
                    popups_closed += 1
                    no_change_count = 0
                    await asyncio.sleep(self.interval)
                    continue

            # ━━ 层2: 颜色检测找X ━━ (~5ms)
            color_pos = self._find_x_by_color(shot)
            if color_pos:
                logger.info(f"[R{rnd+1}] 颜色X @ {color_pos}")
                await device.tap(color_pos[0], color_pos[1])
                popups_closed += 1
                no_change_count = 0
                await asyncio.sleep(self.interval)
                continue

            # ━━ 层3: OCR识别文字 ━━ (~100-200ms)
            hits = self.ocr_screen(shot)
            all_text = " | ".join(f"{h.text}" for h in hits[:12])
            logger.info(f"[R{rnd+1}] OCR: {all_text}")

            # 检查大厅
            if self._is_at_lobby(hits):
                if matcher and matcher.is_at_lobby(shot):
                    logger.info(f"[R{rnd+1}] 到达大厅 ✓ (关闭{popups_closed}个)")
                    return DismissResult(True, popups_closed, "lobby", rnd + 1)
                if not matcher:
                    return DismissResult(True, popups_closed, "lobby", rnd + 1)

            # 复选框 → 勾选后点X
            cb = self._find_keyword_hit(hits, CHECKBOX_KEYWORDS)
            if cb:
                logger.info(f"[R{rnd+1}] 勾选: '{cb.text}' @ ({cb.cx},{cb.cy})")
                await device.tap(cb.cx, cb.cy)
                await asyncio.sleep(0.5)
                # 勾选后用模板找X
                shot2 = await device.screenshot()
                if shot2 is not None and matcher:
                    x2 = matcher.find_close_button(shot2)
                    if x2:
                        await device.tap(x2.cx, x2.cy)
                        popups_closed += 1
                        no_change_count = 0
                        await asyncio.sleep(self.interval)
                        continue
                # 兜底：点固定X位置
                await device.tap(1217, 92)
                popups_closed += 1
                no_change_count = 0
                await asyncio.sleep(self.interval)
                continue

            # 关闭类文字
            close = self._find_keyword_hit(hits, CLOSE_KEYWORDS)
            if close:
                logger.info(f"[R{rnd+1}] 点关闭: '{close.text}' @ ({close.cx},{close.cy})")
                await device.tap(close.cx, close.cy)
                popups_closed += 1
                no_change_count = 0
                await asyncio.sleep(self.interval)
                continue

            # 确认类文字
            confirm = self._find_keyword_hit(hits, CONFIRM_KEYWORDS)
            if confirm:
                logger.info(f"[R{rnd+1}] 点确认: '{confirm.text}' @ ({confirm.cx},{confirm.cy})")
                await device.tap(confirm.cx, confirm.cy)
                popups_closed += 1
                no_change_count = 0
                await asyncio.sleep(self.interval)
                continue

            # ━━ 层4: 固定坐标轮换 ━━
            no_change_count += 1
            pos = FIXED_X_POSITIONS[no_change_count % len(FIXED_X_POSITIONS)]
            logger.info(f"[R{rnd+1}] 固定坐标: {pos}")
            await device.tap(pos[0], pos[1])
            await asyncio.sleep(self.interval)

            if no_change_count >= 5 and matcher:
                if matcher.is_at_lobby(shot):
                    return DismissResult(True, popups_closed, "lobby_forced", rnd + 1)

        logger.warning(f"弹窗清理超时 ({self.max_rounds}轮)")
        return DismissResult(False, popups_closed, "timeout", self.max_rounds)

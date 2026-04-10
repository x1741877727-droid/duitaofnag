"""
OCR驱动的弹窗清理器
不依赖模板穷举，通过识别屏幕文字来决定点击哪里。
逻辑：
  1. OCR识别全屏文字
  2. 有"关闭类"文字 → 点击它
  3. 有"确认类"文字 → 点击它
  4. 有"开始游戏"且无弹窗文字 → 判定在大厅
  5. 都没有 → 点击屏幕中央，等待下一轮
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# 关闭类关键词（优先级从高到低）
CLOSE_KEYWORDS = [
    "今日内不再弹出",
    "今日不再弹出",
    "不再弹出",
    "关闭",
    "×",  # X符号
    "x",  # 小写x
]

# 确认/跳过类关键词
CONFIRM_KEYWORDS = [
    "确定",
    "确认",
    "知道了",
    "我知道了",
    "同意",
    "已了解",
    "不需要",
    "暂不",
    "跳过",
    "领取",  # 有些弹窗是"领取奖励"后就关了
]

# 大厅标志关键词
LOBBY_KEYWORDS = [
    "开始游戏",
]

# 弹窗标志关键词（如果同时出现这些，说明还有弹窗）
POPUP_INDICATORS = [
    "活动",
    "公告",
    "更新",
    "奖励",
    "限时",
    "立即前往",
    "前往观看",
    "分享",
    "邀请",
]


@dataclass
class OcrHit:
    """OCR识别结果"""
    text: str
    cx: int  # 文字区域中心x
    cy: int  # 文字区域中心y
    confidence: float


@dataclass
class DismissResult:
    success: bool
    popups_closed: int
    final_state: str
    rounds: int


class OcrDismisser:
    """基于OCR的弹窗清理器"""

    def __init__(self, max_rounds: int = 25, interval: float = 2.0):
        self.max_rounds = max_rounds
        self.interval = interval
        self._ocr = None

    def _get_ocr(self):
        """懒加载EasyOCR（首次调用会下载模型，约30秒）"""
        if self._ocr is None:
            logger.info("初始化 EasyOCR ...")
            import easyocr
            self._ocr = easyocr.Reader(['ch_sim', 'en'], gpu=False, verbose=False)
            logger.info("EasyOCR 初始化完成")
        return self._ocr

    def ocr_screen(self, screenshot) -> list[OcrHit]:
        """对截图做OCR，返回识别到的文字列表"""
        import numpy as np
        ocr = self._get_ocr()

        if not isinstance(screenshot, np.ndarray):
            return []

        # EasyOCR 返回 [(bbox, text, confidence), ...]
        # bbox = [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
        results = ocr.readtext(screenshot)
        hits = []
        for (box, text, conf) in results:
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            cx = int(sum(xs) / 4)
            cy = int(sum(ys) / 4)
            hits.append(OcrHit(text=text, cx=cx, cy=cy, confidence=conf))
        return hits

    def _find_keyword_hit(self, hits: list[OcrHit], keywords: list[str]) -> OcrHit | None:
        """在OCR结果中查找包含关键词的文字，返回第一个匹配"""
        for kw in keywords:
            for hit in hits:
                if kw in hit.text:
                    return hit
        return None

    def _has_any_keyword(self, hits: list[OcrHit], keywords: list[str]) -> bool:
        """OCR结果中是否包含任一关键词"""
        all_text = " ".join(h.text for h in hits)
        return any(kw in all_text for kw in keywords)

    def _is_at_lobby(self, hits: list[OcrHit]) -> bool:
        """判断是否在大厅（有"开始游戏"且无弹窗指标）"""
        has_lobby = self._has_any_keyword(hits, LOBBY_KEYWORDS)
        has_popup = self._has_any_keyword(hits, POPUP_INDICATORS)
        return has_lobby and not has_popup

    async def dismiss_all(self, device, matcher=None) -> DismissResult:
        """
        循环清理弹窗直到到达大厅。

        Args:
            device: ADBController实例
            matcher: ScreenMatcher实例（可选，用于辅助大厅检测）
        """
        popups_closed = 0
        no_change_count = 0
        last_action = ""

        for round_num in range(self.max_rounds):
            shot = await device.screenshot()
            if shot is None:
                await asyncio.sleep(1)
                continue

            # OCR识别
            hits = self.ocr_screen(shot)
            all_text = " | ".join(f"{h.text}({h.cx},{h.cy})" for h in hits[:15])
            logger.info(f"[弹窗R{round_num+1}] OCR: {all_text}")

            # 1. 检查是否到大厅
            if self._is_at_lobby(hits):
                # 二次确认：用模板匹配验证
                if matcher and matcher.is_at_lobby(shot):
                    logger.info(f"[弹窗R{round_num+1}] 到达大厅 ✓ (关闭{popups_closed}个弹窗)")
                    return DismissResult(True, popups_closed, "lobby", round_num + 1)
                # 模板也没有也可能是对的（模板不够全）
                if not matcher:
                    logger.info(f"[弹窗R{round_num+1}] OCR判定在大厅 ✓")
                    return DismissResult(True, popups_closed, "lobby", round_num + 1)

            # 2. 优先找"关闭类"文字
            close_hit = self._find_keyword_hit(hits, CLOSE_KEYWORDS)
            if close_hit:
                logger.info(f"[弹窗R{round_num+1}] 点击关闭: '{close_hit.text}' @ ({close_hit.cx},{close_hit.cy})")
                await device.tap(close_hit.cx, close_hit.cy)
                popups_closed += 1
                no_change_count = 0
                await asyncio.sleep(self.interval)
                continue

            # 3. 找"确认类"文字
            confirm_hit = self._find_keyword_hit(hits, CONFIRM_KEYWORDS)
            if confirm_hit:
                logger.info(f"[弹窗R{round_num+1}] 点击确认: '{confirm_hit.text}' @ ({confirm_hit.cx},{confirm_hit.cy})")
                await device.tap(confirm_hit.cx, confirm_hit.cy)
                popups_closed += 1
                no_change_count = 0
                await asyncio.sleep(self.interval)
                continue

            # 4. 模板匹配兜底：找X按钮
            if matcher:
                x_hit = matcher.find_close_button(shot)
                if x_hit:
                    logger.info(f"[弹窗R{round_num+1}] 模板X: '{x_hit.name}' @ ({x_hit.cx},{x_hit.cy})")
                    await device.tap(x_hit.cx, x_hit.cy)
                    popups_closed += 1
                    no_change_count = 0
                    await asyncio.sleep(self.interval)
                    continue

            # 5. 什么都没找到 → 轮换点击不同位置
            no_change_count += 1
            positions = [
                (640, 400),   # 屏幕中央
                (100, 650),   # 左下
                (1200, 650),  # 右下
                (640, 680),   # 底部中央
            ]
            pos = positions[no_change_count % len(positions)]
            logger.info(f"[弹窗R{round_num+1}] 无匹配, 点击{pos}")
            await device.tap(pos[0], pos[1])
            await asyncio.sleep(self.interval)

            # 连续5轮无变化，可能已经在大厅但检测不到
            if no_change_count >= 5:
                logger.warning(f"[弹窗R{round_num+1}] 连续{no_change_count}轮无匹配")
                # 强制检查大厅
                if matcher and matcher.is_at_lobby(shot):
                    return DismissResult(True, popups_closed, "lobby_forced", round_num + 1)

        logger.warning(f"弹窗清理超时 ({self.max_rounds}轮)")
        return DismissResult(False, popups_closed, "timeout", self.max_rounds)

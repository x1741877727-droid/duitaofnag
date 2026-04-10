"""
暴力弹窗清理器
核心思路：不去"找"按钮，而是每轮直接点击所有已知关闭位置，然后检查是否到大厅。
速度：每轮 ~3秒（点击300ms + 等待2s + 截图检查500ms）
"""

import asyncio
import logging
from dataclasses import dataclass

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# ── 所有已知的弹窗X按钮/关闭按钮位置（1280×720） ──
# 从实测中收集，每轮全部点一遍，点到空白处无害
CLOSE_POSITIONS = [
    (1092, 97),   # 公告弹窗X
    (1217, 92),   # 活动弹窗X（琉璃星纱、新春共创赛等全屏活动）
    (1227, 47),   # 活动详情页X（老六日等小窗活动）
    (980, 183),   # 组队码面板等对话框X
    (640, 560),   # "今日内不再弹出"复选框位置（先勾选）
]

# 大厅标志关键词
LOBBY_KEYWORDS = ["开始游戏"]


@dataclass
class DismissResult:
    success: bool
    popups_closed: int
    final_state: str
    rounds: int


class OcrDismisser:
    """暴力弹窗清理器"""

    def __init__(self, max_rounds: int = 15, interval: float = 2.0):
        self.max_rounds = max_rounds
        self.interval = interval
        self._ocr = None

    def _get_ocr(self):
        if self._ocr is None:
            logger.info("初始化 RapidOCR ...")
            from rapidocr import RapidOCR
            self._ocr = RapidOCR()
            logger.info("RapidOCR 初始化完成")
        return self._ocr

    def _ocr_texts(self, screenshot: np.ndarray) -> list[str]:
        """OCR识别，只返回文字列表（不需要坐标）"""
        ocr = self._get_ocr()
        result = ocr(screenshot)
        if result and result.txts:
            return list(result.txts)
        return []

    def _check_lobby(self, screenshot: np.ndarray, matcher=None) -> bool:
        """检查是否在大厅"""
        # 方法1: 模板匹配（快，20ms）
        if matcher and matcher.is_at_lobby(screenshot):
            return True
        # 方法2: OCR找"开始游戏"（慢一点，200ms，但更可靠）
        texts = self._ocr_texts(screenshot)
        all_text = " ".join(texts)
        has_lobby = any(kw in all_text for kw in LOBBY_KEYWORDS)
        # 大厅判定：有"开始游戏"就算到了（弹窗遮挡下也能看到）
        if has_lobby:
            return True
        return False

    def _check_left_game(self, screenshot: np.ndarray) -> bool:
        """检查是否意外退出了游戏（到了加速器/登录页/桌面）"""
        texts = self._ocr_texts(screenshot)
        all_text = " ".join(texts)
        # 这些关键词说明不在游戏内了
        left_indicators = ["六花加速器", "QQ授权登录", "微信登录", "登录中"]
        return any(kw in all_text for kw in left_indicators)

    async def _tap_all_close_positions(self, device):
        """一口气点完所有关闭位置，不截图，纯速度"""
        for x, y in CLOSE_POSITIONS:
            await device.tap(x, y)
            await asyncio.sleep(0.15)  # 每次点击间隔150ms

    async def dismiss_all(self, device, matcher=None) -> DismissResult:
        """
        暴力清理弹窗：每轮点所有X位置 → 检查大厅 → 重复
        """
        for rnd in range(self.max_rounds):
            # ━━ 步骤1: 暴力点击所有关闭位置 ━━
            logger.info(f"[R{rnd+1}] 点击所有关闭位置 ({len(CLOSE_POSITIONS)}个)")
            await self._tap_all_close_positions(device)

            # ━━ 步骤2: 等待弹窗动画 ━━
            await asyncio.sleep(self.interval)

            # ━━ 步骤3: 截图检查状态 ━━
            shot = await device.screenshot()
            if shot is None:
                continue

            # 检查是否到大厅
            if self._check_lobby(shot, matcher):
                logger.info(f"[R{rnd+1}] ✓ 到达大厅！共{rnd+1}轮")
                return DismissResult(True, rnd + 1, "lobby", rnd + 1)

            # 检查是否意外退出游戏
            if self._check_left_game(shot):
                logger.warning(f"[R{rnd+1}] ✗ 已退出游戏，需重新启动")
                return DismissResult(False, rnd + 1, "left_game", rnd + 1)

            # 用OCR看看当前画面有什么
            texts = self._ocr_texts(shot)
            logger.info(f"[R{rnd+1}] 当前画面: {' | '.join(texts[:8])}")

        logger.warning(f"弹窗清理超时 ({self.max_rounds}轮)")
        return DismissResult(False, self.max_rounds, "timeout", self.max_rounds)

    # ── 供外部使用的OCR方法 ──

    def ocr_screen(self, screenshot: np.ndarray):
        """兼容旧接口，供phase_launch_game使用"""
        from dataclasses import dataclass

        @dataclass
        class OcrHit:
            text: str
            cx: int
            cy: int
            confidence: float

        ocr = self._get_ocr()
        result = ocr(screenshot)
        hits = []
        if result and result.boxes is not None:
            for box, text, conf in zip(result.boxes, result.txts, result.scores):
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                cx = int(sum(xs) / 4)
                cy = int(sum(ys) / 4)
                hits.append(OcrHit(text=text, cx=cx, cy=cy, confidence=conf))
        return hits

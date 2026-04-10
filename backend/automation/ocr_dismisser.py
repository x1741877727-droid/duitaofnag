"""
智能弹窗清理器 — 状态机驱动

核心思路：
  1. 每轮先判断当前状态（大厅/有弹窗/加载中/已退出游戏）
  2. 弹窗检测不靠模板穷举，而是检测"遮罩层"（弹窗共同特征）
  3. 找关闭目标分三级：模板X → OCR文字 → 区域扫描
  4. 每次操作后验证是否生效

弹窗共同特征：
  - 半透明暗色遮罩覆盖游戏画面
  - 中央或偏上有高亮面板
  - 关闭按钮在右上角或底部
"""

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum

import cv2
import numpy as np

logger = logging.getLogger(__name__)


class ScreenState(str, Enum):
    LOBBY = "lobby"           # 大厅，无弹窗
    POPUP = "popup"           # 有弹窗遮挡
    LOADING = "loading"       # 游戏加载中
    LOGIN = "login"           # 登录页
    LEFT_GAME = "left_game"   # 退出了游戏
    UNKNOWN = "unknown"


# OCR关键词配置
LOBBY_KEYWORDS = ["开始游戏"]
LOADING_KEYWORDS = ["正在检查更新", "正在加载", "加载中"]
LOGIN_KEYWORDS = ["QQ授权登录", "微信登录", "登录中"]
# 注意：游戏内底部状态栏也会显示"六花加速器[已连接]"，不能用它判断退出
# 只有加速器的独有文字才能判定退出了游戏
LEFT_GAME_KEYWORDS = ["CDN节点第", "六花官方通知"]

# 弹窗关闭文字（OCR识别后点击）
CLOSE_TEXT = ["关闭", "×"]
CONFIRM_TEXT = ["确定", "确认", "知道了", "我知道了", "同意", "暂不", "跳过", "不需要",
                "点击屏幕继续", "点击屏幕", "点击继续"]
CHECKBOX_TEXT = ["今日内不再弹出", "今日不再弹出", "不再弹出", "不再提醒"]


@dataclass
class DismissResult:
    success: bool
    popups_closed: int
    final_state: str
    rounds: int


class OcrDismisser:
    """状态机驱动的弹窗清理器"""

    def __init__(self, max_rounds: int = 20, interval: float = 1.5):
        self.max_rounds = max_rounds
        self.interval = interval
        self._ocr = None
        self._lobby_ref_brightness = None  # 大厅参考亮度

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # OCR引擎
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _get_ocr(self):
        if self._ocr is None:
            logger.info("初始化 RapidOCR ...")
            from rapidocr import RapidOCR
            self._ocr = RapidOCR()
            logger.info("RapidOCR 初始化完成")
        return self._ocr

    @dataclass
    class TextHit:
        text: str
        cx: int
        cy: int

    def _ocr_all(self, screenshot: np.ndarray) -> list:
        """OCR全屏，返回 [TextHit, ...]"""
        ocr = self._get_ocr()
        result = ocr(screenshot)
        hits = []
        if result and result.boxes is not None:
            for box, text, conf in zip(result.boxes, result.txts, result.scores):
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                cx = int(sum(xs) / 4)
                cy = int(sum(ys) / 4)
                hits.append(self.TextHit(text=text, cx=cx, cy=cy))
        return hits

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 状态检测
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _has_overlay(self, screenshot: np.ndarray) -> bool:
        """
        检测是否有弹窗遮罩层。
        原理：弹窗会在画面上叠加半透明黑色遮罩，
        导致屏幕边缘区域（遮罩可见处）整体变暗。
        """
        gray = cv2.cvtColor(screenshot, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape

        # 采样四个角落区域（遮罩最明显的地方）
        corners = [
            gray[0:60, 0:60],           # 左上
            gray[0:60, w-60:w],         # 右上
            gray[h-60:h, 0:60],         # 左下
            gray[h-60:h, w-60:w],       # 右下
        ]
        avg_corner_brightness = np.mean([c.mean() for c in corners])

        # 采样中央区域（弹窗面板通常更亮）
        center = gray[h//4:3*h//4, w//4:3*w//4]
        avg_center_brightness = center.mean()

        # 如果四角很暗（<50）且中央明显更亮 → 有遮罩
        has_dark_corners = avg_corner_brightness < 50
        center_brighter = avg_center_brightness > avg_corner_brightness + 40

        return has_dark_corners and center_brighter

    def detect_state(self, screenshot: np.ndarray, matcher=None, ocr_hits=None) -> ScreenState:
        """
        判断当前屏幕状态。
        先用快速方法（模板/亮度），不够再用OCR。
        """
        # 快速检查：模板匹配大厅
        if matcher:
            lobby_hit = matcher.find_any(screenshot, ["lobby_start_btn", "lobby_start_game"], threshold=0.85)
            if lobby_hit:
                # 有"开始游戏" — 检查是否有弹窗遮挡
                if self._has_overlay(screenshot):
                    return ScreenState.POPUP
                return ScreenState.LOBBY

        # 检查遮罩（不需要OCR，纯像素分析）
        if self._has_overlay(screenshot):
            return ScreenState.POPUP

        # 需要OCR来判断
        if ocr_hits is None:
            ocr_hits = self._ocr_all(screenshot)
        all_text = " ".join(h.text for h in ocr_hits)

        if any(kw in all_text for kw in LOBBY_KEYWORDS):
            return ScreenState.LOBBY
        if any(kw in all_text for kw in LEFT_GAME_KEYWORDS):
            return ScreenState.LEFT_GAME
        if any(kw in all_text for kw in LOGIN_KEYWORDS):
            return ScreenState.LOGIN
        if any(kw in all_text for kw in LOADING_KEYWORDS):
            return ScreenState.LOADING

        return ScreenState.UNKNOWN

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 关闭目标查找
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _find_close_target(self, screenshot: np.ndarray, matcher=None) -> tuple[int, int, str] | None:
        """
        找弹窗的关闭目标，返回 (x, y, 方法描述) 或 None
        优先级：模板X → OCR关闭文字 → OCR确认文字
        """
        # 级别1: 模板匹配找X按钮 (~20ms)
        if matcher:
            x_hit = matcher.find_close_button(screenshot)
            if x_hit and x_hit.confidence > 0.80:
                return (x_hit.cx, x_hit.cy, f"模板:{x_hit.name}")

        # 级别2: OCR找关闭类文字 (~200ms)
        hits = self._ocr_all(screenshot)

        # 先勾选复选框
        for h in hits:
            for kw in CHECKBOX_TEXT:
                if kw in h.text:
                    return (h.cx, h.cy, f"勾选:{h.text}")

        # 找关闭按钮
        for h in hits:
            for kw in CLOSE_TEXT:
                if kw in h.text:
                    return (h.cx, h.cy, f"关闭:{h.text}")

        # 找确认按钮
        for h in hits:
            for kw in CONFIRM_TEXT:
                if kw in h.text:
                    # "点击屏幕"类 → 点击屏幕中央而不是文字位置
                    if "屏幕" in kw or "继续" in kw:
                        return (640, 400, f"点击屏幕:{h.text}")
                    return (h.cx, h.cy, f"确认:{h.text}")

        # 级别3: 在右上角区域找小型高对比度元素（通用X检测）
        pos = self._find_x_shape(screenshot)
        if pos:
            return (pos[0], pos[1], "形状检测X")

        return None

    def _find_x_shape(self, screenshot: np.ndarray) -> tuple[int, int] | None:
        """
        在右上角用形态学找X形状的按钮。
        X按钮特征：小区域内有交叉线条，形成×形。
        """
        h, w = screenshot.shape[:2]
        # 只看右上角 1/4
        roi = screenshot[0:h//3, w//2:]
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)

        # 边缘检测
        edges = cv2.Canny(gray, 100, 200)

        # 找轮廓
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        for c in contours:
            area = cv2.contourArea(c)
            x, y, cw, ch = cv2.boundingRect(c)

            # X按钮通常 20x20 到 50x50
            if not (200 < area < 3000):
                continue
            if not (15 < cw < 60 and 15 < ch < 60):
                continue
            # 宽高比接近1:1
            if not (0.5 < cw / max(ch, 1) < 2.0):
                continue

            # 检查这个区域的"交叉"特征：中心像素比周围暗
            cx_local = x + cw // 2
            cy_local = y + ch // 2
            center_val = gray[cy_local, cx_local] if cy_local < gray.shape[0] and cx_local < gray.shape[1] else 255

            # X按钮在浅色背景上是深色线条
            surround = gray[max(0,y):y+ch, max(0,x):x+cw].mean()
            if center_val < surround - 20:  # 中心比周围暗
                return (w // 2 + cx_local, cy_local)

        return None

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 主循环
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def dismiss_all(self, device, matcher=None) -> DismissResult:
        """状态机驱动的弹窗清理"""
        popups_closed = 0
        last_state = None
        stuck_count = 0

        for rnd in range(self.max_rounds):
            shot = await device.screenshot()
            if shot is None:
                await asyncio.sleep(1)
                continue

            # ━━ 判断状态 ━━
            state = self.detect_state(shot, matcher)
            logger.info(f"[R{rnd+1}] 状态: {state.value}")

            if state == ScreenState.LOBBY:
                logger.info(f"[R{rnd+1}] ✓ 到达大厅！关闭了{popups_closed}个弹窗")
                return DismissResult(True, popups_closed, "lobby", rnd + 1)

            if state == ScreenState.LEFT_GAME:
                logger.warning(f"[R{rnd+1}] ✗ 已退出游戏")
                return DismissResult(False, popups_closed, "left_game", rnd + 1)

            if state == ScreenState.LOGIN:
                logger.info(f"[R{rnd+1}] 在登录页，等待自动登录...")
                await asyncio.sleep(3)
                continue

            if state == ScreenState.LOADING:
                logger.info(f"[R{rnd+1}] 加载中，等待...")
                await asyncio.sleep(2)
                continue

            # ━━ POPUP 或 UNKNOWN: 尝试关闭 ━━
            target = self._find_close_target(shot, matcher)

            if target:
                x, y, method = target
                logger.info(f"[R{rnd+1}] 点击: {method} @ ({x},{y})")
                await device.tap(x, y)
                popups_closed += 1
                stuck_count = 0

                # 如果是勾选复选框，紧接着再找X关闭
                if "勾选" in method:
                    await asyncio.sleep(0.5)
                    shot2 = await device.screenshot()
                    if shot2 is not None:
                        target2 = self._find_close_target(shot2, matcher)
                        if target2 and "勾选" not in target2[2]:
                            logger.info(f"[R{rnd+1}] 勾选后点: {target2[2]} @ ({target2[0]},{target2[1]})")
                            await device.tap(target2[0], target2[1])
            else:
                stuck_count += 1
                logger.info(f"[R{rnd+1}] 未找到关闭目标 (stuck={stuck_count})")

                # 卡住了 → 尝试点击屏幕中央（处理"点击任意位置继续"类弹窗）
                if stuck_count >= 2:
                    logger.info(f"[R{rnd+1}] 点击屏幕中央尝试跳过")
                    await device.tap(640, 400)

            # 检测是否卡在同一状态
            if state == last_state and stuck_count >= 4:
                logger.warning(f"[R{rnd+1}] 连续{stuck_count}轮卡住，强制检查大厅")
                if matcher and matcher.is_at_lobby(shot):
                    return DismissResult(True, popups_closed, "lobby_forced", rnd + 1)
            last_state = state

            await asyncio.sleep(self.interval)

        logger.warning(f"弹窗清理超时 ({self.max_rounds}轮)")
        return DismissResult(False, popups_closed, "timeout", self.max_rounds)

    # ── 兼容旧接口 ──

    def ocr_screen(self, screenshot: np.ndarray):
        """供phase_launch_game使用"""
        return self._ocr_all(screenshot)

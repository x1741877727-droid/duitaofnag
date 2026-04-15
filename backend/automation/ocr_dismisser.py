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
CLOSE_TEXT = ["关闭", "×", "✕", "X"]
CONFIRM_TEXT = ["确定", "确认", "知道了", "我知道了", "同意", "暂不", "跳过", "不需要",
                "领取见面礼",
                "点击屏幕继续", "点击屏幕", "点击继续",
                # 不常见但可能出现的弹窗
                "立即更新", "稍后更新",     # 版本更新
                "已了解", "已阅读",          # 公告/协议
                "取消", "返回",              # 误触退出确认
                "重新连接", "重试",          # 网络断开
                "继续游戏",                  # 防沉迷/实名
                "我已满18周岁",              # 实名认证
                "下次再说", "以后再说",      # 各种推荐弹窗
                ]
CHECKBOX_TEXT = ["今日内不再弹出", "今日不再弹出", "不再弹出", "不再提醒",
                 "不再显示", "下次不再提醒"]


@dataclass
class DismissResult:
    success: bool
    popups_closed: int
    final_state: str
    rounds: int


class OcrDismisser:
    """状态机驱动的弹窗清理器"""

    # 类级别共享 OCR 实例（所有 OcrDismisser 实例共用，只初始化一次）
    _shared_ocr = None

    def __init__(self, max_rounds: int = 20):
        self.max_rounds = max_rounds

    @classmethod
    def warmup(cls):
        """预热 OCR 引擎（启动时调用一次，避免运行中等待）"""
        if cls._shared_ocr is None:
            logger.info("预热 RapidOCR ...")
            from rapidocr import RapidOCR
            try:
                cls._shared_ocr = RapidOCR(det_limit_side_len=960, text_score=0.3)
            except TypeError:
                # 旧版 RapidOCR 不支持这些参数
                cls._shared_ocr = RapidOCR()
            logger.info("RapidOCR 预热完成")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # OCR引擎
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _get_ocr(self):
        if OcrDismisser._shared_ocr is None:
            OcrDismisser.warmup()
        return OcrDismisser._shared_ocr

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

    def _ocr_roi(self, screenshot: np.ndarray, x1: float, y1: float,
                 x2: float, y2: float, scale: int = 2) -> list:
        """裁剪 ROI 区域 + 放大后 OCR，提高小文字准确率

        坐标为比例 (0.0~1.0)，自动转换为像素。
        放大 scale 倍后识别，坐标映射回原图。

        用法：
          _ocr_roi(shot, 0, 0, 0.1, 1.0)  # 左侧栏 (0~10% 宽度)
          _ocr_roi(shot, 0, 0.9, 1.0, 1.0)  # 底部 tab (90~100% 高度)
        """
        h, w = screenshot.shape[:2]
        px1, py1 = int(w * x1), int(h * y1)
        px2, py2 = int(w * x2), int(h * y2)
        crop = screenshot[py1:py2, px1:px2]

        if crop.size == 0:
            return []

        # 放大提高小文字识别率
        if scale > 1:
            crop = cv2.resize(crop, (0, 0), fx=scale, fy=scale,
                              interpolation=cv2.INTER_CUBIC)

        ocr = self._get_ocr()
        result = ocr(crop)
        hits = []
        if result and result.boxes is not None:
            for box, text, conf in zip(result.boxes, result.txts, result.scores):
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                # 映射回原图坐标
                cx = int(sum(xs) / 4 / scale) + px1
                cy = int(sum(ys) / 4 / scale) + py1
                hits.append(self.TextHit(text=text, cx=cx, cy=cy))
        return hits

    @staticmethod
    def fuzzy_match(text: str, keyword: str, max_distance: int = 1) -> bool:
        """模糊匹配：编辑距离 <= max_distance 视为匹配

        解决 OCR 常见误识别：
          "组队" → "如WB", "确定" → "确宝", "关闭" → "关内"
        """
        # 先精确匹配（快速路径）
        if keyword in text:
            return True

        # 滑动窗口模糊匹配
        klen = len(keyword)
        for i in range(max(0, len(text) - klen - max_distance),
                       min(len(text), len(text) - klen + max_distance + 1)):
            window = text[i:i + klen]
            if len(window) != klen:
                continue
            dist = sum(1 for a, b in zip(window, keyword) if a != b)
            if dist <= max_distance:
                return True
        return False

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
                        sh, sw = screenshot.shape[:2]
                        return (sw // 2, sh // 2, f"点击屏幕:{h.text}")
                    return (h.cx, h.cy, f"确认:{h.text}")

        # 级别3: 形状检测 — 仅在确认有遮罩层时才用（防止大厅页面误触）
        if self._has_overlay(screenshot):
            pos = self._find_x_shape(screenshot)
            if pos:
                return (pos[0], pos[1], "形状检测X")

        return None

    def _find_x_shape(self, screenshot: np.ndarray) -> tuple[int, int] | None:
        """
        在右上区域找关闭按钮（X 形状或圆形 ⊗ 按钮）。
        搜索范围：右半屏幕的上半部分。
        两种模式：
          1. 方形 X 交叉线条（游戏内常见）
          2. 圆形深色按钮（系统/SDK弹窗常见，如"家长提示"的 ⊗）
        """
        h, w = screenshot.shape[:2]
        # 搜索右半屏幕上 2/3 区域
        roi = screenshot[0:h*2//3, w//3:]
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        roi_offset_x = w // 3

        # ── 方法1: 边缘轮廓找方形X ──
        edges = cv2.Canny(gray, 100, 200)
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        for c in contours:
            area = cv2.contourArea(c)
            x, y, cw, ch = cv2.boundingRect(c)
            if not (200 < area < 3000):
                continue
            if not (15 < cw < 60 and 15 < ch < 60):
                continue
            if not (0.5 < cw / max(ch, 1) < 2.0):
                continue

            cx_local = x + cw // 2
            cy_local = y + ch // 2
            center_val = gray[cy_local, cx_local] if cy_local < gray.shape[0] and cx_local < gray.shape[1] else 255
            surround = gray[max(0,y):y+ch, max(0,x):x+cw].mean()
            if center_val < surround - 20:
                return (roi_offset_x + cx_local, cy_local)

        # ── 方法2: 霍夫圆检测找圆形关闭按钮 ──
        blurred = cv2.GaussianBlur(gray, (9, 9), 2)
        circles = cv2.HoughCircles(
            blurred, cv2.HOUGH_GRADIENT, dp=1.2,
            minDist=30, param1=100, param2=40,
            minRadius=12, maxRadius=35,
        )
        if circles is not None:
            for circle in circles[0]:
                cx, cy, r = int(circle[0]), int(circle[1]), int(circle[2])
                # 圆形X按钮特征：圆内整体较暗（深色按钮），或者圆内有X纹理
                region = gray[max(0,cy-r):cy+r, max(0,cx-r):cx+r]
                if region.size == 0:
                    continue
                inner_mean = region.mean()
                # 取圆周围一圈的平均亮度
                outer_y1, outer_y2 = max(0, cy-r*2), min(gray.shape[0], cy+r*2)
                outer_x1, outer_x2 = max(0, cx-r*2), min(gray.shape[1], cx+r*2)
                outer_mean = gray[outer_y1:outer_y2, outer_x1:outer_x2].mean()
                # 圆内比周围明显暗 = 深色关闭按钮
                if inner_mean < outer_mean - 30:
                    logger.info(f"圆形X检测: 圆心=({roi_offset_x+cx},{cy}) r={r} 内{inner_mean:.0f} 外{outer_mean:.0f}")
                    return (roi_offset_x + cx, cy)

        return None

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 主循环
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def dismiss_all(self, device, matcher=None) -> DismissResult:
        """
        状态机驱动的弹窗清理。
        速度优化：模板命中时快速连点(0.5s/轮)，只有需要OCR时才慢(2s/轮)
        大厅确认：连续3次检测到大厅且无遮罩才算真正到达（弹窗可能1-2秒后冒出来）
        """
        popups_closed = 0
        stuck_count = 0
        lobby_confirm = 0
        LOBBY_CONFIRM_NEEDED = 2

        for rnd in range(self.max_rounds):
            shot = await device.screenshot()
            if shot is None:
                await asyncio.sleep(0.3)
                continue

            # ━━ 快速路径: 模板匹配找X (~20ms) ━━
            if matcher:
                x_hit = matcher.find_close_button(shot)
                if x_hit and x_hit.confidence > 0.80:
                    logger.info(f"[R{rnd+1}] 快速关闭: {x_hit.name} @ ({x_hit.cx},{x_hit.cy})")
                    await device.tap(x_hit.cx, x_hit.cy)
                    popups_closed += 1
                    stuck_count = 0
                    lobby_confirm = 0
                    await asyncio.sleep(0.5)
                    continue

            # ━━ 快速路径: 模板匹配大厅 + 无遮罩 (~30ms) ━━
            if matcher and matcher.is_at_lobby(shot) and not self._has_overlay(shot):
                lobby_confirm += 1
                if lobby_confirm >= LOBBY_CONFIRM_NEEDED:
                    logger.info(f"[R{rnd+1}] ✓ 大厅确认{lobby_confirm}次，完成！关闭{popups_closed}个弹窗")
                    return DismissResult(True, popups_closed, "lobby", rnd + 1)
                logger.info(f"[R{rnd+1}] 大厅检测 ({lobby_confirm}/{LOBBY_CONFIRM_NEEDED})")
                await asyncio.sleep(0.3)  # 短等，快速二次确认
                continue

            # ━━ 慢速路径: 需要OCR分析（帧差跳过）━━
            from .adb_lite import phash, phash_distance
            h = phash(shot)
            if not hasattr(self, '_last_ph'):
                self._last_ph = 0
            if phash_distance(h, self._last_ph) < 4:
                logger.debug(f"[R{rnd+1}] 帧差跳过 OCR")
                await asyncio.sleep(0.5)
                continue
            self._last_ph = h

            state = self.detect_state(shot, matcher)
            logger.info(f"[R{rnd+1}] 状态: {state.value}")

            if state == ScreenState.LOBBY:
                lobby_confirm += 1
                if lobby_confirm >= LOBBY_CONFIRM_NEEDED:
                    logger.info(f"[R{rnd+1}] ✓ 大厅确认{lobby_confirm}次(OCR)，完成！")
                    return DismissResult(True, popups_closed, "lobby", rnd + 1)
                logger.info(f"[R{rnd+1}] 大厅检测OCR ({lobby_confirm}/{LOBBY_CONFIRM_NEEDED})")
                await asyncio.sleep(0.4)
                continue

            if state == ScreenState.LEFT_GAME:
                logger.warning(f"[R{rnd+1}] ✗ 已退出游戏")
                return DismissResult(False, popups_closed, "left_game", rnd + 1)

            if state == ScreenState.LOGIN:
                logger.info(f"[R{rnd+1}] 登录页，等待...")
                await asyncio.sleep(3)
                continue

            if state == ScreenState.LOADING:
                logger.info(f"[R{rnd+1}] 加载中...")
                await asyncio.sleep(2)
                continue

            # 非大厅状态 → 重置大厅计数
            lobby_confirm = 0

            # ━━ POPUP/UNKNOWN: OCR找关闭目标 ━━
            target = self._find_close_target(shot, matcher)
            if target:
                x, y, method = target
                logger.info(f"[R{rnd+1}] 点击: {method} @ ({x},{y})")
                await device.tap(x, y)
                popups_closed += 1
                stuck_count = 0

                # 勾选复选框后紧接找X
                if "勾选" in method:
                    await asyncio.sleep(0.5)
                    shot2 = await device.screenshot()
                    if shot2 is not None:
                        target2 = self._find_close_target(shot2, matcher)
                        if target2 and "勾选" not in target2[2]:
                            logger.info(f"[R{rnd+1}] 勾选后点: {target2[2]} @ ({target2[0]},{target2[1]})")
                            await device.tap(target2[0], target2[1])

                await asyncio.sleep(0.5)
                continue

            # ━━ 什么都没找到 ━━
            stuck_count += 1
            logger.info(f"[R{rnd+1}] 未找到目标 (stuck={stuck_count})")
            if stuck_count >= 2:
                sh, sw = shot.shape[:2]
                await device.tap(sw // 2, sh // 2)  # 点屏幕中央
            if stuck_count >= 4 and matcher and matcher.is_at_lobby(shot):
                return DismissResult(True, popups_closed, "lobby_forced", rnd + 1)

            await asyncio.sleep(0.6)

        logger.warning(f"弹窗清理超时 ({self.max_rounds}轮)")
        return DismissResult(False, popups_closed, "timeout", self.max_rounds)

    # ── 兼容旧接口 ──

    def ocr_screen(self, screenshot: np.ndarray):
        """供phase_launch_game使用"""
        return self._ocr_all(screenshot)

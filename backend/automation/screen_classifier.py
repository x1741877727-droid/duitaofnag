"""
P1.5 ScreenClassifier — 纯函数屏幕分类器.

输入: 一帧的 YOLO 检测结果 + 登录模板是否命中 + 帧亮度
输出: ScreenKind enum {LOBBY, POPUP, LOGIN, LOADING, UNKNOWN}

设计原则:
  - **纯函数**, 不调 ML 不调 IO. ML 调用在外层 wrapper (classify_from_frame).
    这样 fixture test 不进 emulator 也能验逻辑.
  - **优先级正确**: POPUP > LOGIN > LOBBY > LOADING > UNKNOWN.
    弹窗盖在大厅/登录上时, 不能误判 LOBBY/LOGIN — 弹窗优先关掉.
  - **未来扩展点**:
    - IN_GAME: 等 YOLO 加 in-game 类后扩.
    - LOADING: 当前用亮度兜底 (brightness < 30 = 加载黑屏); 真要稳, 训 loading_spinner 类.

跟现有 LobbyQuadDetector 的关系:
  LobbyQuadDetector = "我连续 N 帧都看到大厅 → 真在大厅" (有状态, 带 streak).
  ScreenClassifier  = "这一帧是什么屏幕" (无状态, 单帧判定).
  两者互补: classifier 给单帧分类, lobby_detector 给跨帧确认.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Iterable


class ScreenKind(str, Enum):
    LOBBY = "lobby"        # 干净大厅, 可触发组队 / 进游戏等动作
    POPUP = "popup"        # 有 close_x/dialog/action_btn — 需先关掉
    LOGIN = "login"        # 在登录页 (lobby_login_btn 模板命中)
    LOADING = "loading"    # 加载/黑屏中, 任何动作都点不到
    UNKNOWN = "unknown"    # 啥信号都没 — 留给上层守门 / 重试


# YOLO 类: 这些出现 = POPUP. conf >= POPUP_MIN_CONF 才算可信.
_POPUP_CLASSES = ("close_x", "dialog", "action_btn")
POPUP_MIN_CONF = 0.5

# YOLO 类: 这个出现 + conf 够 = LOBBY 候选.
_LOBBY_CLASS = "lobby"
LOBBY_MIN_CONF = 0.5

# 帧亮度: < 这值 → LOADING 兜底
LOADING_BRIGHTNESS_MAX = 30


def classify(
    yolo_dets: Iterable[Any],
    lobby_login_template_hit: bool,
    frame_brightness: float,
) -> ScreenKind:
    """单帧分类. 纯函数.

    yolo_dets: 任何带 .name (str) 和 .conf (float) 属性的对象列表.
    lobby_login_template_hit: matcher.match_one(frame, "lobby_login_btn") 是否命中.
    frame_brightness: 帧亮度均值 (0-255). 通常 cv2 灰度 mean.
    """
    has_popup = any(
        getattr(d, "name", "") in _POPUP_CLASSES
        and getattr(d, "conf", 0.0) >= POPUP_MIN_CONF
        for d in yolo_dets
    )
    if has_popup:
        return ScreenKind.POPUP

    if lobby_login_template_hit:
        return ScreenKind.LOGIN

    has_lobby = any(
        getattr(d, "name", "") == _LOBBY_CLASS
        and getattr(d, "conf", 0.0) >= LOBBY_MIN_CONF
        for d in yolo_dets
    )
    if has_lobby:
        return ScreenKind.LOBBY

    if frame_brightness < LOADING_BRIGHTNESS_MAX:
        return ScreenKind.LOADING

    return ScreenKind.UNKNOWN


# ─────────────── 便捷 wrapper (生产用) ───────────────


async def classify_from_frame(frame, yolo, matcher) -> ScreenKind:
    """从 raw frame 跑识别 + classify. 生产用.

    frame: numpy ndarray (BGR).
    yolo: YoloDismisser instance (有 .is_available() / .detect()).
    matcher: ScreenMatcher instance (有 .match_one_async()).

    任一识别失败回 UNKNOWN, 不抛异常 (上层守门接管).
    """
    import asyncio
    import cv2
    import numpy as np

    if frame is None:
        return ScreenKind.UNKNOWN

    # YOLO
    dets = []
    if yolo is not None and yolo.is_available():
        try:
            dets = await asyncio.to_thread(yolo.detect, frame)
        except Exception:
            dets = []

    # login 模板 (没 matcher 就跳过, 等于 False)
    login_hit = False
    if matcher is not None:
        try:
            h = await matcher.match_one_async(frame, "lobby_login_btn", threshold=0.8)
            login_hit = h is not None
        except Exception:
            pass

    # 帧亮度 (灰度 mean)
    try:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        brightness = float(np.mean(gray))
    except Exception:
        brightness = 128.0  # 中性, 不触发 LOADING

    return classify(dets, login_hit, brightness)

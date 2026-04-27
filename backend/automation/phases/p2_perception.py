"""
v3 P2 Perception 层 — 一帧多源识别融合, 纯函数, 无副作用.

输入:
  RunContext (含 current_shot + matcher + yolo + memory + lobby_detector)

输出:
  Perception dataclass (各源命中结果汇总)

设计:
  替代 v2 yolo_dismisser.dismiss_all 中散落的"模板大厅 → YOLO → quad → CTA → memory"
  各种 inline 调用. 这里集中跑一次, 输出统一 Perception 对象给 policy 层用.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

from ..phase_base import RunContext

logger = logging.getLogger(__name__)


# 登录页模板 (v2-6)
LOGIN_TEMPLATE_NAMES = ("lobby_login_btn", "lobby_login_btn_qq")

# close_x_* 模板系列 (v2 高准确率, YOLO 漏检兜底)
CLOSE_X_TEMPLATE_NAMES = (
    "close_x_announce", "close_x_dialog", "close_x_activity",
    "close_x_gold", "close_x_signin", "close_x_newplay",
    "close_x_return", "close_x_white_big",
)

# 关弹窗类按钮模板 (没右上 X 但有底部"确定/同意/不需要"按钮的弹窗)
# 这些都是关闭意图明确, 不会跳出大厅. 不能放: btn_confirm_map (P4 用), btn_join* (P3 用)
DISMISS_BTN_TEMPLATE_NAMES = (
    "btn_confirm",         # 通用"确定"
    "btn_confirm_privacy", # 隐私同意确定
    "btn_agree",           # 同意
    "btn_no_need",         # 不需要
)

# YOLO conf 阈值 (跟 yolo_dismisser 保持一致)
TAP_CONF_CLOSE = 0.50
TAP_CONF_ACTION = 0.50

# action_btn OCR nav 黑名单 (跳出大厅的危险词)
NAV_WORDS = (
    "前往", "参加", "进入", "查看活动", "去看看", "立即前往",
    "前往观赛", "去活动", "我要参加", "查看",
)


@dataclass
class Perception:
    """一帧多源识别融合结果."""
    # 大厅信号
    lobby_template_hit: Optional[Any] = None    # MatchHit, lobby_start_btn 模板命中
    quad_lobby_confirmed: bool = False          # 四元融合判大厅 (含 phash 稳定 1s)
    quad_note: str = ""                          # 四元判定细节 (失败原因)

    # 弹窗信号 (按优先级排序)
    yolo_close_xs: list = field(default_factory=list)     # [Detection], conf>0.5
    yolo_action_btns: list = field(default_factory=list)  # [Detection], conf>0.5
    template_close_x: Optional[Any] = None      # (template_name, MatchHit) — YOLO 漏检兜底
    template_dismiss_btn: Optional[Any] = None  # (template_name, MatchHit) — 无 X 但有"确定/同意"按钮的弹窗

    # 登录页信号
    login_template_hit: Optional[Any] = None    # MatchHit, lobby_login_btn

    # 历史复用
    memory_hit: Optional[Any] = None            # Hit (memory_l1 query 结果)

    # 帧标识
    phash_now: int = 0
    yolo_dets_raw: list = field(default_factory=list)  # 原始 YOLO 输出 (供 verify ctx 用)


async def perceive(ctx: RunContext) -> Perception:
    """跑一帧的所有识别源, 返回汇总 Perception.

    顺序:
      1. 模板 lobby_start_btn (5ms)
      2. 模板 lobby_login_btn / qq (登录页, 5ms)
      3. YOLO 推理 (30ms, 复用 ctx.yolo per-instance session)
      4. 四元融合大厅判定 (LobbyQuadDetector, 综合上面)
      5. 模板 close_x_* 兜底 (15ms, YOLO 漏检时)
      6. Memory L1 phash 查询 (1ms)
    """
    p = Perception()
    shot = ctx.current_shot
    if shot is None:
        return p

    matcher = ctx.matcher

    # 1. 大厅模板 (lobby_start_btn / lobby_start_game)
    if matcher is not None:
        for tn in ("lobby_start_btn", "lobby_start_game"):
            try:
                h = matcher.match_one(shot, tn, threshold=0.75)
            except Exception:
                h = None
            if h is not None:
                p.lobby_template_hit = h
                break

    # 2. 登录页模板
    if matcher is not None:
        for tn in LOGIN_TEMPLATE_NAMES:
            try:
                h = matcher.match_one(shot, tn, threshold=0.80)
            except Exception:
                h = None
            if h is not None:
                p.login_template_hit = h
                break

    # 3. YOLO 推理
    dets = []
    if ctx.yolo is not None:
        try:
            dets = ctx.yolo.detect(shot)
        except Exception as e:
            logger.debug(f"[perceive] yolo err: {e}")
            dets = []
    p.yolo_dets_raw = dets
    p.yolo_close_xs = [
        d for d in dets if getattr(d, "name", "") == "close_x" and d.conf > TAP_CONF_CLOSE
    ]
    p.yolo_action_btns = [
        d for d in dets if getattr(d, "name", "") == "action_btn" and d.conf > TAP_CONF_ACTION
    ]

    # 4. 四元融合大厅判定 (用 lobby_check.LobbyQuadDetector)
    if ctx.lobby_detector is not None:
        try:
            quad_r = ctx.lobby_detector.check(shot, matcher, dets)
            p.quad_lobby_confirmed = quad_r.is_lobby
            p.quad_note = quad_r.note
        except Exception as e:
            logger.debug(f"[perceive] lobby_quad err: {e}")

    # 5. 模板 close_x_* 兜底 (YOLO 漏检, 高准确率)
    if matcher is not None and not p.yolo_close_xs:
        for tn in CLOSE_X_TEMPLATE_NAMES:
            try:
                h = matcher.match_one(shot, tn, threshold=0.80)
            except Exception:
                h = None
            if h is not None:
                p.template_close_x = (tn, h)
                break

    # 5.5 模板 btn_confirm_* / btn_agree / btn_no_need 兜底 (没 X 但有底部"确定"按钮的弹窗)
    if matcher is not None and p.template_close_x is None and not p.yolo_close_xs:
        for tn in DISMISS_BTN_TEMPLATE_NAMES:
            try:
                h = matcher.match_one(shot, tn, threshold=0.80)
            except Exception:
                h = None
            if h is not None:
                p.template_dismiss_btn = (tn, h)
                break

    # 6. Memory L1 查询
    if ctx.memory is not None:
        try:
            mem_hit = ctx.memory.query(
                shot, target_name="dismiss_popups", max_dist=5,
            )
            p.memory_hit = mem_hit
        except Exception as e:
            logger.debug(f"[perceive] memory err: {e}")

    # phash (供 P2SubFSM 用作 phash 卡死检测)
    try:
        from ..adb_lite import phash as _phash
        p.phash_now = _phash(shot)
    except Exception:
        p.phash_now = 0

    return p

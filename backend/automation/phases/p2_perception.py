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

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional


# perceive 内部 5 个 to_thread 并发, 6 inst × 5 = 30 concurrent native calls.
# cv2.matchTemplate / ONNX / OpenVINO 在 30+ 并发下偶发 native crash (无 Python
# traceback, backend 直接 silent 死掉). 加 semaphore 限制全局同时 perceive 的
# 实例数 — 默认 3, 仍能并发但不至于把 native lib 撑爆.
_PERCEIVE_CONCURRENCY = int(os.environ.get("GAMEBOT_PERCEIVE_CONCURRENCY", "3"))
_perceive_sem: Optional[asyncio.Semaphore] = None


def _get_perceive_sem() -> asyncio.Semaphore:
    """懒初始化 — 必须在 asyncio loop 里 get."""
    global _perceive_sem
    if _perceive_sem is None:
        _perceive_sem = asyncio.Semaphore(_PERCEIVE_CONCURRENCY)
    return _perceive_sem

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
    "queding2",            # "系统内存过低提醒"等单按钮 dialog 的"确定" (用户裁)
    "queding",             # P4 也用, 但 P2 同样适用 — "确定"形状通用
    "btn_confirm",         # 通用"确定" (旧, 可能不存在)
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
    quad_template_conf: float = 0.0             # 大厅模板单帧 conf, 用于贝叶斯早退

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

    优化 (2026-04-30 #4 #5):
      - lobby_tpl + login_tpl + yolo 三组并发跑 (asyncio.gather + to_thread)
      - quad 命中大厅 → short-circuit 跳 login_tpl 后续 + close_x_tpl + dismiss_tpl
      - phash 算 1 次, 不再 quad 内 + perceive 末重复
      - memory query 也 to_thread 防 GIL 阻塞

    并发保护: 全局 semaphore 限制同时 perceive 的实例数 (默认 3),
    防 cv2/ONNX/OpenVINO 在 30+ 并发下 native crash 拖死整个 backend.
    """
    # 全局并发限制
    async with _get_perceive_sem():
        return await _perceive_locked(ctx)


async def _perceive_locked(ctx: RunContext) -> Perception:
    import time as _time
    _t0 = _time.perf_counter()
    _perf = {"parallel_block": 0.0, "quad": 0.0,
             "close_x_tpl": 0.0, "dismiss_btn_tpl": 0.0, "memory": 0.0, "phash": 0.0}
    inst_idx = getattr(ctx, 'instance_idx', '?')

    p = Perception()
    shot = ctx.current_shot
    if shot is None:
        return p

    matcher = ctx.matcher

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Block A 并发: lobby_tpl + login_tpl + YOLO + memory + phash
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def _run_lobby_tpl():
        if matcher is None: return None
        for tn in ("lobby_start_btn", "lobby_start_game"):
            try:
                h = matcher.match_one(shot, tn, threshold=0.75)
            except Exception:
                h = None
            if h is not None:
                return h
        return None

    def _run_login_tpl():
        if matcher is None: return None
        for tn in LOGIN_TEMPLATE_NAMES:
            try:
                h = matcher.match_one(shot, tn, threshold=0.80)
            except Exception:
                h = None
            if h is not None:
                return h
        return None

    def _run_yolo():
        if ctx.yolo is None: return []
        try:
            return ctx.yolo.detect(shot)
        except Exception:
            return []

    def _run_memory():
        if ctx.memory is None: return None
        try:
            return ctx.memory.query(shot, target_name="dismiss_popups", max_dist=5)
        except Exception:
            return None

    def _run_phash():
        try:
            from ..adb_lite import phash as _phash
            return _phash(shot)
        except Exception:
            return 0

    _t = _time.perf_counter()
    lobby_hit, login_hit, dets, mem_hit, ph_now = await asyncio.gather(
        asyncio.to_thread(_run_lobby_tpl),
        asyncio.to_thread(_run_login_tpl),
        asyncio.to_thread(_run_yolo),
        asyncio.to_thread(_run_memory),
        asyncio.to_thread(_run_phash),
    )
    _perf["parallel_block"] = (_time.perf_counter() - _t) * 1000

    p.lobby_template_hit = lobby_hit
    p.login_template_hit = login_hit
    p.yolo_dets_raw = dets or []
    p.yolo_close_xs = [d for d in p.yolo_dets_raw if getattr(d, "name", "") == "close_x" and d.conf > TAP_CONF_CLOSE]
    p.yolo_action_btns = [d for d in p.yolo_dets_raw if getattr(d, "name", "") == "action_btn" and d.conf > TAP_CONF_ACTION]
    p.memory_hit = mem_hit
    p.phash_now = ph_now or 0
    _perf["memory"] = 0.0  # 已并发, 不单独算
    _perf["phash"] = 0.0

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # quad 大厅判定 (依赖上面 dets, 不能并行)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    _t = _time.perf_counter()
    if ctx.lobby_detector is not None:
        try:
            quad_r = await asyncio.to_thread(
                ctx.lobby_detector.check, shot, matcher, p.yolo_dets_raw
            )
            p.quad_lobby_confirmed = quad_r.is_lobby
            p.quad_note = quad_r.note
            p.quad_template_conf = float(getattr(quad_r, "template_conf", 0.0) or 0.0)
        except Exception as e:
            logger.debug(f"[perceive] lobby_quad err: {e}")
    _perf["quad"] = (_time.perf_counter() - _t) * 1000

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # short-circuit: 已确认大厅就跳后续兜底 (#5)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    if p.quad_lobby_confirmed:
        # 已在大厅, 不需要找 close_x / dismiss_btn 模板
        _total = (_time.perf_counter() - _t0) * 1000
        logger.info(
            f"[PERF/perceive/inst{inst_idx}] total={_total:.0f}ms (quad-shortcut) "
            f"parallel={_perf['parallel_block']:.0f} quad={_perf['quad']:.0f}"
        )
        return p

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 模板 close_x_* 兜底 (YOLO 漏检时)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    _t = _time.perf_counter()
    if matcher is not None and not p.yolo_close_xs:
        def _run_close_x():
            for tn in CLOSE_X_TEMPLATE_NAMES:
                try:
                    h = matcher.match_one(shot, tn, threshold=0.80)
                except Exception:
                    h = None
                if h is not None:
                    return (tn, h)
            return None
        p.template_close_x = await asyncio.to_thread(_run_close_x)
    _perf["close_x_tpl"] = (_time.perf_counter() - _t) * 1000

    # 模板 btn_confirm_* 兜底 (没 X 但有"确定"按钮的弹窗)
    _t = _time.perf_counter()
    if matcher is not None and p.template_close_x is None and not p.yolo_close_xs:
        def _run_dismiss():
            for tn in DISMISS_BTN_TEMPLATE_NAMES:
                try:
                    h = matcher.match_one(shot, tn, threshold=0.80)
                except Exception:
                    h = None
                if h is not None:
                    return (tn, h)
            return None
        p.template_dismiss_btn = await asyncio.to_thread(_run_dismiss)
    _perf["dismiss_btn_tpl"] = (_time.perf_counter() - _t) * 1000

    _total = (_time.perf_counter() - _t0) * 1000
    logger.info(
        f"[PERF/perceive/inst{inst_idx}] total={_total:.0f}ms "
        f"parallel={_perf['parallel_block']:.0f} quad={_perf['quad']:.0f} "
        f"close_x_tpl={_perf['close_x_tpl']:.0f} dismiss_tpl={_perf['dismiss_btn_tpl']:.0f}"
    )

    return p

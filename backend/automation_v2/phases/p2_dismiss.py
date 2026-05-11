"""P2 — 清弹窗 → 大厅 (核心 phase, 12 实例并发的 popup→tap 瓶颈).

设计 (V2_PHASES.md, REVIEW_DAY3_ARCH.md):
- 砍 v1 的 5 路 perceive (lobby_tpl/login_tpl/yolo/memory/phash gather + quad)
- 砍 deferred verify / state_expectation / phash before-after / memory_l1
- 单 round 流程:
    1. ROI 优先 yolo close_x (右上区域, ~30ms)
    2. ROI 漏报 → 全屏 yolo fallback (~50ms)
    3. 选 conf 最高且不在黑名单的 close_x → tap (黑名单 TTL 3s)
    4. 无 close_x → action_btn fallback (确定/同意/知道了)
    5. 无目标 → lobby class >= 1 连续 2 帧 → NEXT
    6. 其他 → RETRY

性能目标: 单 round 150-300ms, popup→tap 端到端 < 1s
"""
from __future__ import annotations

import logging

from ..ctx import RunContext
from ..perception.yolo import Roi
from ..phase_base import (
    PhaseStep, PhaseResult, PhaseAction,
    step_next, step_retry,
)

logger = logging.getLogger(__name__)

# ROI 配置 (归一化 0-1, 跟分辨率无关)
CLOSE_X_ROI = Roi(0.65, 0.0, 1.0, 0.4)        # 右上区域 (公告 close x 常在这)
ACTION_BTN_ROI = Roi(0.0, 0.40, 1.0, 1.0)     # 下半屏 (确定/同意按钮)

# conf 阈值
CLOSE_X_CONF = 0.50
ACTION_BTN_CONF = 0.50    # action_btn 比 close_x 略严, 防误识别"QQ登录"等
LOBBY_CONF = 0.55

# 大厅连续 N 帧确认才退出 (防 popup 关闭瞬间 yolo 看穿背景误判)
LOBBY_STREAK_REQUIRED = 2


class P2Dismiss:
    """P2 清弹窗. ROI 优先 + 全屏 fallback."""

    name = "P2"
    max_seconds = 60.0
    round_interval_s = 0.2

    async def enter(self, ctx: RunContext) -> None:
        ctx.reset_phase_state()
        ctx.lobby_streak = 0

    async def handle_frame(self, ctx: RunContext) -> PhaseStep:
        shot = ctx.current_shot
        if shot is None:
            ctx.mark("yolo_start"); ctx.mark("yolo_done"); ctx.mark("decide")
            return step_retry(note="no shot")

        # ── 1. ROI 优先: close_x 在右上 ──
        ctx.mark("yolo_start")
        try:
            roi_dets = await ctx.yolo.detect(shot, roi=CLOSE_X_ROI, conf_thresh=0.20)
        except Exception as e:
            logger.debug(f"[P2] yolo ROI err: {e}")
            roi_dets = []

        close_xs = [
            d for d in roi_dets
            if d.name == "close_x" and d.conf >= CLOSE_X_CONF
        ]

        # ── 2. ROI 漏报 → 全屏 fallback ──
        full_dets = None
        if not close_xs:
            try:
                full_dets = await ctx.yolo.detect(shot, conf_thresh=0.20)
            except Exception as e:
                logger.debug(f"[P2] yolo 全屏 err: {e}")
                full_dets = []
            close_xs = [
                d for d in full_dets
                if d.name == "close_x" and d.conf >= CLOSE_X_CONF
            ]
        ctx.mark("yolo_done")

        # ── 3. 优先点 close_x (绕黑名单, conf 降序) ──
        for d in sorted(close_xs, key=lambda x: -x.conf):
            if not ctx.is_blacklisted(d.cx, d.cy):
                ctx.add_blacklist(d.cx, d.cy, ttl=3.0)
                ctx.mark("decide")
                ctx.lobby_streak = 0     # tap 了 popup, 重置 lobby 计数
                return step_retry(
                    note=f"tap close_x@({d.cx},{d.cy}) conf={d.conf:.2f}",
                    outcome_hint="tapped",
                    action=PhaseAction(
                        kind="tap", x=d.cx, y=d.cy,
                        target="close_x", conf=d.conf,
                    ),
                )

        # ── 4. action_btn fallback (下半屏的 "确定/同意" 类) ──
        try:
            action_dets = await ctx.yolo.detect(
                shot, roi=ACTION_BTN_ROI, conf_thresh=0.20,
            )
        except Exception as e:
            logger.debug(f"[P2] action_btn ROI err: {e}")
            action_dets = []
        actions = [
            d for d in action_dets
            if d.name == "action_btn" and d.conf >= ACTION_BTN_CONF
        ]
        for d in sorted(actions, key=lambda x: -x.conf):
            if not ctx.is_blacklisted(d.cx, d.cy):
                ctx.add_blacklist(d.cx, d.cy, ttl=3.0)
                ctx.mark("decide")
                ctx.lobby_streak = 0
                return step_retry(
                    note=f"tap action_btn@({d.cx},{d.cy}) conf={d.conf:.2f}",
                    outcome_hint="tapped",
                    action=PhaseAction(
                        kind="tap", x=d.cx, y=d.cy,
                        target="action_btn", conf=d.conf,
                    ),
                )

        # ── 5. 大厅检测: yolo lobby class >= 1 连续 N 帧 → NEXT ──
        # 用 full_dets (如果已跑过); 否则跑一次全屏 yolo 看 lobby
        dets_for_lobby = full_dets if full_dets is not None else roi_dets
        # ROI 检的 dets 不一定含 lobby (lobby 通常中下区域), 跑全屏补一次
        if full_dets is None and not any(d.name == "lobby" for d in dets_for_lobby):
            try:
                dets_for_lobby = await ctx.yolo.detect(shot, conf_thresh=0.20)
            except Exception:
                dets_for_lobby = []
        has_lobby = any(
            d.name == "lobby" and d.conf >= LOBBY_CONF for d in dets_for_lobby
        )

        ctx.mark("decide")

        if has_lobby:
            ctx.lobby_streak += 1
            if ctx.lobby_streak >= LOBBY_STREAK_REQUIRED:
                return step_next(
                    note=f"lobby 连续 {ctx.lobby_streak} 帧 → P3",
                    outcome_hint="lobby_confirmed",
                )
            return step_retry(
                note=f"lobby streak {ctx.lobby_streak}/{LOBBY_STREAK_REQUIRED}",
                outcome_hint="lobby_pending",
            )
        ctx.lobby_streak = 0
        return step_retry(
            note=f"R{ctx.phase_round}: 无目标 (dets={len(dets_for_lobby)})",
            outcome_hint="no_target",
        )

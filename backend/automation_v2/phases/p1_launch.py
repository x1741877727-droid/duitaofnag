"""P1 — am start 拉起 PUBG, 等任意可交互 UI.

设计 (V2_PHASES.md):
- 砍 motion_gate (dHash 跳静止帧, 收益 < 50ms 但代码 80 行)
- 砍 ScreenClassifier 接入 (V2 简单: 见 popup/lobby class 立刻 NEXT)
- 固定 0.3s round_interval, 90s timeout

业务接入点: GAME_PACKAGE (默认 PUBG 中国版).
"""
from __future__ import annotations

import logging

from ..ctx import RunContext
from ..phase_base import PhaseStep, PhaseResult, step_next, step_retry

logger = logging.getLogger(__name__)

GAME_PACKAGE = "com.tencent.tmgp.pubgmhd"

# 见到这些 YOLO class 中任一个就 NEXT
POPUP_CLASSES = {"close_x", "action_btn"}
LOBBY_CLASSES = {"lobby"}


class P1Launch:
    name = "P1"
    max_seconds = 90.0
    round_interval_s = 0.3

    def __init__(self, game_package: str = GAME_PACKAGE):
        self.game_package = game_package
        self._started = False

    async def enter(self, ctx: RunContext) -> None:
        """第一次进入时 am start PUBG. 后续 RETRY 不重启."""
        ctx.reset_phase_state()
        if not self._started:
            try:
                await ctx.adb.start_app(self.game_package)
                logger.info(f"[P1/inst{ctx.instance_idx}] am start {self.game_package}")
            except Exception as e:
                logger.warning(f"[P1/inst{ctx.instance_idx}] start_app err: {e}")
            self._started = True

    async def handle_frame(self, ctx: RunContext) -> PhaseStep:
        shot = ctx.current_shot
        if shot is None:
            ctx.mark("yolo_start"); ctx.mark("yolo_done"); ctx.mark("decide")
            return step_retry(note="no shot")

        # 全屏 yolo (P1 不预设 ROI, popup 位置随机)
        ctx.mark("yolo_start")
        try:
            dets = await ctx.yolo.detect(shot, conf_thresh=0.30)
        except Exception as e:
            logger.debug(f"[P1] yolo err: {e}")
            dets = []
        ctx.mark("yolo_done")

        has_popup = any(
            d.name in POPUP_CLASSES and d.conf >= 0.40 for d in dets
        )
        has_lobby = any(
            d.name in LOBBY_CLASSES and d.conf >= 0.55 for d in dets
        )

        ctx.mark("decide")

        if has_popup or has_lobby:
            kind = "POPUP" if has_popup else "LOBBY"
            return step_next(note=f"R{ctx.phase_round}: {kind} → P2",
                            outcome_hint=f"see_{kind.lower()}")
        return step_retry(note=f"R{ctx.phase_round}: 等 PUBG UI 出现 (dets={len(dets)})",
                          outcome_hint="loading")

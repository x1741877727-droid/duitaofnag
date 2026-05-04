"""
v3 P1 — 启动游戏并等待出现可交互 UI.

判定: 用 ScreenClassifier 单帧分类, ScreenKind ∈ {LOBBY, POPUP, LOGIN}
任一即 NEXT (UI 出来了, 后续 P2 / P3 处理). LOADING / UNKNOWN 等下一帧.

完全不跑 OCR (12 实例并发会爆 CPU 240s/分钟).
P1 不区分大厅 / 登录页 / 弹窗 — 那是 P2 的活.
"""

from __future__ import annotations

import logging

import time as _time

from ..phase_base import PhaseHandler, PhaseResult, PhaseStep, RunContext
from ..recorder_helpers import record_signal_tier

logger = logging.getLogger(__name__)


class P1LaunchHandler(PhaseHandler):
    """启动游戏 (am start) → 等任意可交互 UI 出现."""

    name = "P1"
    name_cn = "启动游戏"
    description = "am start 拉起游戏, 等任意可交互 UI 出现. 不区分大厅/弹窗/登录页, 都让 P2 处理."
    flow_steps = [
        "enter: am start 启动游戏包",
        "每帧 1.5s: 抓帧 + 跑识别",
        "ScreenClassifier 分类: LOBBY/POPUP/LOGIN 任一 → NEXT (UI 出来了)",
        "LOADING/UNKNOWN → 等下一帧",
        "60 轮 (~90s) 都没命中 → FAIL",
    ]
    max_rounds = 60               # 60 × 1.5s = 90s timeout
    round_interval_s = 1.5

    async def enter(self, ctx: RunContext) -> None:
        await super().enter(ctx)
        # 第一次进入时启动游戏 (后续 RETRY 不再重启)
        if ctx.runner is not None:
            try:
                from ..single_runner import GAME_PACKAGE
                await ctx.device.start_app(GAME_PACKAGE)
                logger.info("[P1] am start 游戏")
            except Exception as e:
                logger.warning(f"[P1] start_app 异常: {e}")

    async def handle_frame(self, ctx: RunContext) -> PhaseStep:
        shot = ctx.current_shot
        if shot is None:
            return PhaseStep(PhaseResult.RETRY)

        # 顺手采集训练数据
        try:
            from ..screenshot_collector import collect as _collect
            _collect(shot, tag="launch_game")
        except Exception:
            pass

        rnd = ctx.phase_round
        matcher = ctx.matcher
        decision = ctx.current_decision

        # P1.5 ScreenClassifier — 单帧分类 {LOBBY, POPUP, LOGIN, LOADING, UNKNOWN}.
        # P1 出口条件: 任何不是 LOADING/UNKNOWN 的状态 = "UI 出来了" → NEXT.
        # 替代旧 4 条件 (大厅模板 / close_x 模板 / 登录模板 / YOLO dets>0)
        # — classifier 内部已涵盖前 3 条 + YOLO popup/lobby 类信号.
        from ..screen_classifier import ScreenKind, classify_from_frame

        t0 = _time.perf_counter()
        kind = await classify_from_frame(shot, ctx.yolo, matcher)
        ms = (_time.perf_counter() - t0) * 1000

        if kind not in (ScreenKind.LOADING, ScreenKind.UNKNOWN):
            record_signal_tier(decision, name="classifier", hit=True, tier_idx=2,
                               note=f"ScreenKind={kind.name}",
                               duration_ms=ms)
            return PhaseStep(
                PhaseResult.NEXT,
                note=f"R{rnd}: 分类={kind.name} → done",
                outcome_hint=f"classify_{kind.value}",
            )

        record_signal_tier(decision, name="classifier", hit=False, tier_idx=2,
                           note=f"ScreenKind={kind.name} (loading/unknown, 等下一帧)",
                           duration_ms=ms)

        if rnd % 5 == 0:
            logger.info(f"[P1] R{rnd}: 等待中 (kind={kind.name})")
        return PhaseStep(PhaseResult.RETRY, outcome_hint=f"waiting_{kind.value}")

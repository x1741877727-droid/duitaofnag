"""
v3 P1 — 启动游戏并等待出现可交互 UI.

判定 P1 完成 (任一命中即 done):
  ① 模板 lobby_start_btn (大厅)
  ② 模板 close_x_* 系列 (公告 / 活动 / 对话框 弹窗)
  ③ 模板 lobby_login_btn / lobby_login_btn_qq (登录页)
  ④ YOLO 任意 dets > 0 (脱离加载黑屏)

完全不跑 OCR (12 实例并发会爆 CPU 240s/分钟).
P1 不区分大厅 vs 登录页 vs 弹窗, 都交给 P2 后续处理.
"""

from __future__ import annotations

import logging

import time as _time

from ..phase_base import PhaseHandler, PhaseResult, PhaseStep, RunContext
from ..recorder_helpers import record_signal_tier

logger = logging.getLogger(__name__)


CLOSE_X_TEMPLATE_NAMES = (
    "close_x_announce", "close_x_dialog", "close_x_activity",
    "close_x_gold", "close_x_signin", "close_x_newplay",
    "close_x_return", "close_x_white_big",
)
LOGIN_TEMPLATE_NAMES = ("lobby_login_btn", "lobby_login_btn_qq")


class P1LaunchHandler(PhaseHandler):
    """启动游戏 (am start) → 等任意可交互 UI 出现."""

    name = "P1"
    name_cn = "启动游戏"
    description = "am start 拉起游戏, 等任意可交互 UI 出现. 不区分大厅/弹窗/登录页, 都让 P2 处理."
    flow_steps = [
        "enter: am start 启动游戏包",
        "每帧 1.5s: 抓帧 + 跑识别",
        "命中任一 → NEXT: ① 大厅模板 (lobby_start_btn) ② close_x_* 模板 ③ 登录模板 ④ YOLO dets > 0",
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

        # ① 模板检测大厅
        # 改 v3: 包 to_thread, 不锁 main loop (旧 sync 调 6 实例 P1 串行 4-8s 阻塞)
        t0 = _time.perf_counter()
        if matcher and await matcher.is_at_lobby_async(shot):
            record_signal_tier(decision, name="模板", hit=True, tier_idx=0,
                               note="大厅模板命中 (lobby_start_btn / is_at_lobby)",
                               duration_ms=(_time.perf_counter() - t0) * 1000)
            return PhaseStep(PhaseResult.NEXT,
                             note=f"R{rnd}: 大厅模板命中 → done",
                             outcome_hint="lobby_template_hit")

        # ② 模板检测 close_x + 登录页
        # 改 v3: match_one_async, 12 个模板 × 50-100ms 不再串行卡 main loop
        if matcher:
            for tn in CLOSE_X_TEMPLATE_NAMES + LOGIN_TEMPLATE_NAMES:
                h = await matcher.match_one_async(shot, tn, threshold=0.80)
                if h:
                    record_signal_tier(decision, name="模板", hit=True, tier_idx=0,
                                       note=f"模板 {tn}({h.confidence:.2f}) 命中",
                                       duration_ms=(_time.perf_counter() - t0) * 1000)
                    return PhaseStep(
                        PhaseResult.NEXT,
                        note=f"R{rnd}: 模板 {tn}({h.confidence:.2f}) 命中 → done",
                        outcome_hint="popup_or_login_template_hit",
                    )
        record_signal_tier(decision, name="模板", hit=False, tier_idx=0,
                           note="大厅 / close_x / 登录页 模板均未命中",
                           duration_ms=(_time.perf_counter() - t0) * 1000)

        # ③ YOLO 推理 (任何 dets > 0 → 脱离加载黑屏)
        # 改 v3: to_thread, yolo onnx 推理 30-50ms 也不锁 main loop
        t1 = _time.perf_counter()
        if ctx.yolo is not None and ctx.yolo.is_available():
            try:
                import asyncio as _aio
                dets = await _aio.to_thread(ctx.yolo.detect, shot)
                if dets:
                    names = ",".join(f"{d.name}({d.conf:.2f})" for d in dets[:3])
                    record_signal_tier(decision, name="YOLO", hit=True, tier_idx=2,
                                       note=f"YOLO 检到 {len(dets)} 个 [{names}]",
                                       duration_ms=(_time.perf_counter() - t1) * 1000)
                    return PhaseStep(
                        PhaseResult.NEXT,
                        note=f"R{rnd}: YOLO 检到 {len(dets)} 个 [{names}] → done",
                        outcome_hint="yolo_dets_seen",
                    )
                record_signal_tier(decision, name="YOLO", hit=False, tier_idx=2,
                                   note="YOLO 0 dets",
                                   duration_ms=(_time.perf_counter() - t1) * 1000)
            except Exception as e:
                logger.debug(f"[P1] YOLO 推理失败: {e}")

        # 都没命中, 等下一帧
        if rnd % 5 == 0:
            logger.info(f"[P1] R{rnd}: 等待中 (无 UI 元素出现)")
        return PhaseStep(PhaseResult.RETRY, outcome_hint="waiting_ui")

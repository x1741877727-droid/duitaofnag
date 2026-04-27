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

from ..phase_base import PhaseHandler, PhaseResult, PhaseStep, RunContext

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

        # ① 模板检测大厅
        if matcher and matcher.is_at_lobby(shot):
            return PhaseStep(PhaseResult.NEXT,
                             note=f"R{rnd}: 大厅模板命中 → done")

        # ② 模板检测 close_x + 登录页
        if matcher:
            for tn in CLOSE_X_TEMPLATE_NAMES + LOGIN_TEMPLATE_NAMES:
                h = matcher.match_one(shot, tn, threshold=0.80)
                if h:
                    return PhaseStep(
                        PhaseResult.NEXT,
                        note=f"R{rnd}: 模板 {tn}({h.confidence:.2f}) 命中 → done",
                    )

        # ③ YOLO 推理 (任何 dets > 0 → 脱离加载黑屏)
        if ctx.yolo is not None and ctx.yolo.is_available():
            try:
                dets = ctx.yolo.detect(shot)
                if dets:
                    names = ",".join(f"{d.name}({d.conf:.2f})" for d in dets[:3])
                    return PhaseStep(
                        PhaseResult.NEXT,
                        note=f"R{rnd}: YOLO 检到 {len(dets)} 个 [{names}] → done",
                    )
            except Exception as e:
                logger.debug(f"[P1] YOLO 推理失败: {e}")

        # 都没命中, 等下一帧
        if rnd % 5 == 0:
            logger.info(f"[P1] R{rnd}: 等待中 (无 UI 元素出现)")
        return PhaseStep(PhaseResult.RETRY)

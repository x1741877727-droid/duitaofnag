"""
v3 P4 — 队长选模式 + 选地图 + 准备开打.

策略: 薄壳包装 single_runner.phase_map_setup (现有 OCR 全屏识别).
"""

from __future__ import annotations

import logging

from ..phase_base import PhaseHandler, PhaseResult, PhaseStep, RunContext

logger = logging.getLogger(__name__)


class P4MapSetupHandler(PhaseHandler):
    name = "P4"
    name_cn = "选地图开打"
    description = "队长选模式 + 选地图 + 准备开打. 薄壳包装现有 phase_map_setup. OCR 全屏识别."
    flow_steps = [
        "选模式 (默认 团队竞技, 可配 settings.target_mode)",
        "选地图 (默认 狙击团竞, 可配 settings.target_map)",
        "勾选「准备」按钮",
        "等队员都准备",
        "点「开始游戏」",
    ]
    max_rounds = 1
    round_interval_s = 1.0

    async def handle_frame(self, ctx: RunContext) -> PhaseStep:
        runner = ctx.runner
        if runner is None:
            return PhaseStep(PhaseResult.FAIL, note="ctx.runner 未注入")

        from ..recorder_helpers import record_signal_tier
        decision = ctx.current_decision
        try:
            ok = await runner.phase_map_setup()
        except Exception as e:
            record_signal_tier(decision, name="OCR地图", hit=False, tier_idx=3,
                               note=f"phase_map_setup 异常: {e}")
            logger.error(f"[P4] phase_map_setup 异常: {e}")
            return PhaseStep(PhaseResult.FAIL, note=f"map_setup 异常: {e}",
                             outcome_hint="map_setup_exception")

        if ok:
            record_signal_tier(decision, name="OCR地图", hit=True, tier_idx=3,
                               note="map_setup 完成 (模式选好 + 地图选好 + 准备开打)")
            return PhaseStep(PhaseResult.DONE, note="map_setup 完成",
                             outcome_hint="map_setup_ok")
        record_signal_tier(decision, name="OCR地图", hit=False, tier_idx=3,
                           note="map_setup 返回 False")
        return PhaseStep(PhaseResult.FAIL, note="map_setup 失败",
                         outcome_hint="map_setup_fail")

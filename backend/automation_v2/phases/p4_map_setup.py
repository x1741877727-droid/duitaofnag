"""P4 — 队长选模式 + 选地图 + 准备开打.

v2 设计:
- OCR 全屏识别模式/地图按钮, 点准备, 等队员准备, 点开始
- 薄壳委托 flows/map_setup.py (skeleton)

v1 reference: backend/automation/phases/p4_map_setup.py
"""
from __future__ import annotations

import logging

from ..ctx import RunContext
from ..phase_base import PhaseStep, step_done, step_fail

logger = logging.getLogger(__name__)


class P4MapSetup:
    name = "P4"
    max_seconds = 90.0
    round_interval_s = 1.0

    async def enter(self, ctx: RunContext) -> None:
        ctx.reset_phase_state()

    async def handle_frame(self, ctx: RunContext) -> PhaseStep:
        ctx.mark("yolo_start"); ctx.mark("yolo_done"); ctx.mark("decide")

        # TODO 业务: 选模式 + 选地图 + 准备 + 开始
        try:
            from ..flows.map_setup import run_map_setup
            ok = await run_map_setup(ctx)
        except ImportError:
            logger.info(f"[P4/inst{ctx.instance_idx}] flows/map_setup 未实现, skeleton DONE")
            return step_done(note="P4 skeleton (业务未接入)", outcome_hint="skeleton")
        except Exception as e:
            logger.error(f"[P4/inst{ctx.instance_idx}] map_setup 异常: {e}", exc_info=True)
            return step_fail(note=f"map_setup 异常: {e}")

        if ok:
            return step_done(note="map_setup 完成", outcome_hint="map_setup_ok")
        return step_fail(note="map_setup 返回 False", outcome_hint="map_setup_fail")

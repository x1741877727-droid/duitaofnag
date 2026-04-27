"""
v3 P2 Handler — 薄壳, 委托 P2SubFSM."""

from __future__ import annotations

from ..phase_base import PhaseHandler, PhaseStep, RunContext
from .p2_subfsm import P2SubFSM


class P2DismissHandler(PhaseHandler):
    """清弹窗 → 大厅. 25 轮 timeout → FAIL → runner_service 重试."""

    name = "P2"
    max_rounds = 25
    round_interval_s = 0.5

    def __init__(self):
        self._sub = P2SubFSM()

    async def handle_frame(self, ctx: RunContext) -> PhaseStep:
        return await self._sub.step(ctx)

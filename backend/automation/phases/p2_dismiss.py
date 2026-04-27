"""
v3 P2 Handler — 薄壳, 委托 P2SubFSM."""

from __future__ import annotations

from ..phase_base import PhaseHandler, PhaseStep, RunContext
from .p2_subfsm import P2SubFSM


class P2DismissHandler(PhaseHandler):
    """清弹窗 → 大厅. 25 轮 timeout → FAIL → runner_service 重试."""

    name = "P2"
    name_cn = "清弹窗"
    description = "反复关弹窗 → 进入大厅. 5 层识别 + 4 道防线 (黑名单 / 防死循环 / state_expectation / Memory 缓冲)."
    flow_steps = [
        "感知: 大厅模板 + 四元判大厅 + YOLO close_x/action_btn + 模板 close_x_* + 登录页 + 记忆 + phash",
        "守门 1: 四元大厅判 ≥ 2 帧 → NEXT",
        "守门 2: 登录页停留 > 60s → GAME_RESTART",
        "决策优先级: 记忆 > YOLO close_x > 模板 close_x → tap",
        "防死循环: 同坐标连击 ≥ 3 → 加黑名单",
        "兜底: 连续 3 轮无目标 + 大厅模板命中 → NEXT",
        "死屏: 无目标 > 12 轮 + 非大厅 → GAME_RESTART",
    ]
    max_rounds = 25
    round_interval_s = 0.5

    def __init__(self):
        self._sub = P2SubFSM()

    async def handle_frame(self, ctx: RunContext) -> PhaseStep:
        return await self._sub.step(ctx)

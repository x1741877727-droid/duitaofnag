"""InstanceStateAdapter — V2 RunContext 跟 v1 instance_state 适配.

REVIEW_DAY3_WATCHDOG.md: V2 应该每 phase enter/exit 显式 save_atomic, 闪退恢复更稳.

V2 不重新实现 instance_state, 复用 v1 模块 (`backend/automation/instance_state.py`).
本适配器干 2 件事:
1. phase enter 时: load_or_fresh, 把上次状态 (last_phase / scheme_url 等) 写回 ctx
2. phase exit 时: save_atomic, 持久化 RunContext 关键字段

恢复 (recovery) 逻辑:
- backend 启动时 runner 起 12 个 task
- 每个 task 启动前先 load_state, 决定从哪个 phase 开始
- e.g. 上次跑到 P3a 中途闪退, 重启时跳过 P0/P1/P2 直接进 P3a (节省 1-2 分钟)

骨架 Day 3: 接口完整, 业务接入点 TODO.
"""
from __future__ import annotations

import logging
from typing import Optional

from ..ctx import RunContext

logger = logging.getLogger(__name__)


class InstanceStateAdapter:
    """V2 ⇄ v1 instance_state 适配器. 每实例 1 个."""

    def __init__(self, instance_idx: int):
        self.instance_idx = instance_idx
        self._v1_state = None        # backend.automation.instance_state.InstanceState 对象
        # TODO: load v1 state
        # try:
        #     from backend.automation.instance_state import InstanceState
        #     self._v1_state = InstanceState.load(instance_idx) or InstanceState.fresh(instance_idx)
        # except Exception as e:
        #     logger.warning(f"[state/{instance_idx}] load fail: {e}")

    def get_recovery_phase(self) -> Optional[str]:
        """启动时调. 返从哪个 phase 开始 (None = 从 P0 开始).

        TODO 业务接入:
        - 看 _v1_state.phase, 如果是 "P3a" 且没 game_scheme_url, 退到 P0 (队伍解散了)
        - 如果是 "P4" 且 scheme 还 valid, 跳过 P0-P3 直接 P4
        - 闪退太久 (> 30min) 退到 P0 (PUBG 状态可能变了)
        """
        # if self._v1_state and self._v1_state.phase:
        #     return self._v1_state.phase
        return None

    def on_phase_enter(self, ctx: RunContext, phase_name: str) -> None:
        """phase enter 时调. 更新 state.phase + save."""
        # if self._v1_state is None: return
        # self._v1_state.phase = phase_name
        # self._v1_state.phase_started_at = ctx.phase_started_at
        # try:
        #     self._v1_state.save_atomic()
        # except Exception as e:
        #     logger.debug(f"[state/{self.instance_idx}] save {phase_name} fail: {e}")
        pass

    def on_phase_exit(self, ctx: RunContext, phase_name: str, result: str) -> None:
        """phase 退出时调. 记 result (NEXT/FAIL) + save."""
        # if self._v1_state is None: return
        # self._v1_state.last_phase_result = result
        # if ctx.game_scheme_url and not self._v1_state.game_scheme_url:
        #     self._v1_state.game_scheme_url = ctx.game_scheme_url
        # try:
        #     self._v1_state.save_atomic()
        # except Exception as e:
        #     logger.debug(f"[state/{self.instance_idx}] save exit fail: {e}")
        pass

    def clear_after_session(self) -> None:
        """完整跑完一局 (P5 DONE) 清状态."""
        # if self._v1_state:
        #     self._v1_state.clear()
        pass

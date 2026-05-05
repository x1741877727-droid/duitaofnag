"""v3 phase handlers — 每个 phase 一个 PhaseHandler 子类."""

from ..runner_fsm import FsmState
from .p0_accelerator import P0AcceleratorHandler
from .p1_launch import P1LaunchHandler
from .p2_dismiss import P2DismissHandler
from .p3a_team_create import P3aTeamCreateHandler
from .p3b_team_join import P3bTeamJoinHandler
from .p4_map_setup import P4MapSetupHandler
from .p5_wait_players import P5WaitPlayersHandler


def build_handlers() -> dict:
    """构造 RunnerFSM 用的 handlers 映射. 每实例 1 份 (每 handler 各 1 个).

    P5_WAIT_PLAYERS 不在这里 — 主 loop 暂不接 P5 (转移表 P4→DONE 保持),
    只在 api_runner_test 测试页跑. 后续主 loop 接 P5 时, 把 expected_id
    通过 RunnerService API 注入 ctx 后再加回来.
    """
    return {
        FsmState.P0_ACCELERATOR: P0AcceleratorHandler(),
        FsmState.P1_LAUNCH: P1LaunchHandler(),
        FsmState.P2_DISMISS: P2DismissHandler(),
        FsmState.P3A_TEAM_CREATE: P3aTeamCreateHandler(),
        FsmState.P3B_TEAM_JOIN: P3bTeamJoinHandler(),
        FsmState.P4_MAP_SETUP: P4MapSetupHandler(),
    }


__all__ = [
    "build_handlers",
    "P0AcceleratorHandler", "P1LaunchHandler", "P2DismissHandler",
    "P3aTeamCreateHandler", "P3bTeamJoinHandler", "P4MapSetupHandler",
    "P5WaitPlayersHandler",
]

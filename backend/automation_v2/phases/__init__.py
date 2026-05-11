"""Phase 实现 — P0-P5.

每个 phase 一个文件, <= 150 行业务.
P3a/P3b/P4/P5 是薄壳, 业务逻辑在 flows/* (skeleton, 业务接入时填).

export:
- P0Accel:        加速器探针
- P1Launch:       am start + 等 UI
- P2Dismiss:      清弹窗 (核心瓶颈 phase, ROI close_x + action_btn fallback)
- P3aTeamCreate:  队长建队 (skeleton)
- P3bTeamJoin:    队员加入 (skeleton)
- P4MapSetup:     选地图开打 (skeleton)
- P5WaitPlayers:  等真人 (skeleton)
"""
from .p0_accel import P0Accel
from .p1_launch import P1Launch
from .p2_dismiss import P2Dismiss
from .p3a_team_create import P3aTeamCreate
from .p3b_team_join import P3bTeamJoin
from .p4_map_setup import P4MapSetup
from .p5_wait_players import P5WaitPlayers

__all__ = [
    "P0Accel",
    "P1Launch",
    "P2Dismiss",
    "P3aTeamCreate",
    "P3bTeamJoin",
    "P4MapSetup",
    "P5WaitPlayers",
]

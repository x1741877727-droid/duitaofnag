"""Stage 3 — 闪退/重启恢复路由.

设计范围 (本 Stage 只做生产路径, dryrun 不动):
  - backend 进程重启时 (如崩溃 / 升级 / 手动重启), 不该总从 P0 重头跑
  - 利用 Stage 2 持久化的 InstanceState 决定从哪个 phase 起跑

不在本 Stage:
  - 游戏闪退检测 / 自动重启游戏 — runner_service 已有 _GameCrashError + _MAX_GAME_RESTARTS
  - 跨实例协调 (队长闪退 → 队员暂停) — Stage 4 squad_state
  - "队伍是否还在" 智能跳过 team_create — 等 squad_state 接好

入口:
  runner_service._run_instance 启动时调 decide_initial_phase, 不再写死 accelerator.
"""
from __future__ import annotations

import logging
from typing import Optional

from .instance_state import InstanceState

logger = logging.getLogger(__name__)


# state.phase (PhaseHandler.name) → 对应的生产 phase 名 (runner_service 用的字符串).
# 注意: state.phase 是 PhaseHandler.name="P0/P1/.../P5", 生产是 "accelerator/launch_game/...".
PHASE_NAME_MAP = {
    "P0": "accelerator",
    "P1": "launch_game",
    "P2": "dismiss_popups",
    "P3a": "team_create",
    "P3b": "team_join",
    "P4": "map_setup",
    "P5": "P5",                      # P5 暂未接入生产 main loop, dryrun-only
}


def decide_initial_phase(state: Optional[InstanceState]) -> str:
    """backend 启动时调, 决定 _run_instance 从哪个 phase 起跑.

    返回值是生产 phase 名 (accelerator / launch_game / dismiss_popups / team_create /
    team_join / map_setup / P5), 给 runner_service._run_instance 的 current_phase
    初值用.

    没 state / state.phase 空 → "accelerator" (fresh 启动).
    """
    if state is None or not state.phase:
        return "accelerator"

    crashed = state.phase
    resume_to = _resume_phase_for(crashed, state)
    logger.info(
        f"[recovery] state.phase={crashed} → resume from '{resume_to}' "
        f"(known_slot_ids={len(state.known_slot_ids)}, "
        f"kicked_ids={len(state.kicked_ids)})")
    return resume_to


def _resume_phase_for(crashed_phase: str, state: InstanceState) -> str:
    """每个 phase 的 resume 规则. 返回应起跑的生产 phase 名.

    Stage 3 v1 策略 (保守):
      P0/P1/P2:     "accelerator" — 重头无副作用, 加速器自检很快
      P3a/P3b:      "launch_game" — 跳过加速器 (多半还连着), 直奔游戏 + 大厅 → team 路由
      P4 选地图:     "launch_game" — 同上, 队伍可能服务端还在; Stage 4 后加 _is_team_intact 优化
      P5 等真人:     "launch_game" — 暂时同 P4; 等 P5 接入生产 main loop 后再细化
                                     (state.known_slot_ids 在 P5 enter 时由 Stage 2 自动 resume)
      其他 / 未知:    "accelerator" — 兜底

    Stage 4 改进点 (TODO):
      P3a 闪退 → 检查 squad_state.team_code_valid, 决定是否需要重新生成队伍码
      P3b 闪退 → 检查 squad_state.leader_alive + team_code_valid, 决定是否 WAIT_LEADER
      P4 闪退 → _is_team_intact 检查队伍还在 → 跳过 team 路由直奔 map_setup
    """
    if crashed_phase in ("P0", "P1", "P2"):
        return "accelerator"
    if crashed_phase in ("P3a", "P3b", "P4", "P5"):
        return "launch_game"
    return "accelerator"


async def is_team_intact(ctx) -> bool:
    """检测当前画面是否在大厅且队伍仍存在 (有队员卡片). 给 Stage 4 P4 / P5 的
    跳过 team_create 决策用.

    判定: 截图 → YOLO 检测 → 至少 2 个 lobby (自己 + 1 队员) = 队伍 intact.

    Stage 3 暂未启用 (resume 逻辑保守地走 launch_game), Stage 4 接入.
    """
    runner = getattr(ctx, "runner", None)
    if runner is None:
        return False
    try:
        shot = await runner.adb.screenshot()
        if shot is None:
            return False
        yolo = getattr(runner, "yolo_dismisser", None)
        if yolo is None or not yolo.is_available():
            return False
        import asyncio
        dets = await asyncio.to_thread(yolo.detect, shot) or []
        lobby_count = sum(1 for d in dets if getattr(d, "name", "") == "lobby")
        return lobby_count >= 2
    except Exception as e:
        logger.debug(f"[recovery] is_team_intact err: {e}")
        return False

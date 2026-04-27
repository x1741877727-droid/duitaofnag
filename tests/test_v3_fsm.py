"""
v3 FSM + PhaseHandler 单测.

跑法: python -X utf8 tests/test_v3_fsm.py
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import numpy as np

from backend.automation.phase_base import (
    PhaseAction, PhaseHandler, PhaseResult, PhaseStep, RunContext,
)
from backend.automation.action_executor import ActionExecutor
from backend.automation.runner_fsm import (
    FsmState, RunnerFSM, _PhaseFailError, _GameRestartError,
)


# ───────── stubs ─────────


def make_ctx(**kwargs) -> RunContext:
    """构造一个最简 RunContext 用于测试."""
    defaults = dict(
        device=AsyncMock(),
        matcher=MagicMock(),
        recognizer=MagicMock(),
        runner=None,
        yolo=None,
        memory=None,
        lobby_detector=None,
        decision_recorder=None,
        instance_idx=0,
    )
    defaults.update(kwargs)
    return RunContext(**defaults)


class _StaticHandler(PhaseHandler):
    """每帧返回固定 PhaseStep, 测 FSM 调度."""
    name = "TEST"
    max_rounds = 5
    round_interval_s = 0.01

    def __init__(self, sequence: list):
        super().__init__()
        self._seq = list(sequence)
        self._i = 0

    async def handle_frame(self, ctx):
        if self._i < len(self._seq):
            step = self._seq[self._i]
            self._i += 1
            return step
        return PhaseStep(PhaseResult.RETRY)


# ───────── tests ─────────


def test_phase_result_enum():
    assert PhaseResult.NEXT != PhaseResult.RETRY
    assert PhaseResult.GAME_RESTART.name == "GAME_RESTART"
    print("  ✓ phase_result_enum")


def test_run_context_reset():
    ctx = make_ctx()
    ctx.popups_closed = 5
    ctx.blacklist_coords.append((100, 100))
    ctx.role = "leader"             # 跨 phase 数据, reset 不动
    ctx.game_scheme_url = "abc"

    ctx.reset_phase_state()

    assert ctx.popups_closed == 0
    assert ctx.blacklist_coords == []
    assert ctx.role == "leader"     # 不动
    assert ctx.game_scheme_url == "abc"
    print("  ✓ run_context_reset")


def test_run_context_blacklist():
    ctx = make_ctx()
    ctx.blacklist_coords.append((100, 100))
    assert ctx.is_blacklisted(110, 110)         # 距离 14 < 30
    assert ctx.is_blacklisted(125, 125)         # 距离 25 < 30
    assert not ctx.is_blacklisted(140, 140)     # 距离 40 > 30
    print("  ✓ run_context_blacklist")


async def _test_action_executor_noop():
    ctx = make_ctx()
    act = PhaseAction(kind="noop")
    ok = await ActionExecutor.apply(ctx, act)
    assert ok
    print("  ✓ action_executor_noop")


async def _test_action_executor_wait():
    ctx = make_ctx()
    act = PhaseAction(kind="wait", seconds=0.05)
    t0 = time.perf_counter()
    await ActionExecutor.apply(ctx, act)
    elapsed = time.perf_counter() - t0
    assert elapsed >= 0.04
    print("  ✓ action_executor_wait")


async def _test_action_executor_tap_skips_blacklist():
    ctx = make_ctx()
    ctx.blacklist_coords.append((640, 360))
    ctx.device.tap = AsyncMock()
    act = PhaseAction(kind="tap", x=645, y=355)  # 距离<30, 黑名单内
    await ActionExecutor.apply(ctx, act)
    ctx.device.tap.assert_not_called()
    print("  ✓ action_executor_tap_skips_blacklist")


async def _test_runner_fsm_simple_path():
    """P0→P1→P2→DONE (单实例无角色, P2 之后 DONE)"""
    ctx = make_ctx()
    ctx.device.screenshot = AsyncMock(return_value=np.zeros((720, 1280, 3), dtype=np.uint8))
    ctx.role = "unknown"  # P2 → DONE 直接

    p0 = _StaticHandler([PhaseStep(PhaseResult.NEXT)])
    p1 = _StaticHandler([PhaseStep(PhaseResult.NEXT)])
    p2 = _StaticHandler([PhaseStep(PhaseResult.NEXT)])

    fsm = RunnerFSM(ctx, {
        FsmState.P0_ACCELERATOR: p0,
        FsmState.P1_LAUNCH: p1,
        FsmState.P2_DISMISS: p2,
    })
    ok = await fsm.run()
    assert ok
    assert fsm.state == FsmState.DONE
    print("  ✓ runner_fsm_simple_path (P0→P1→P2→DONE, role=unknown)")


async def _test_runner_fsm_leader_path():
    """leader: P0→P1→P2→P3a→P4→DONE"""
    ctx = make_ctx()
    ctx.device.screenshot = AsyncMock(return_value=np.zeros((720, 1280, 3), dtype=np.uint8))
    ctx.role = "leader"

    handlers = {
        FsmState.P0_ACCELERATOR: _StaticHandler([PhaseStep(PhaseResult.NEXT)]),
        FsmState.P1_LAUNCH: _StaticHandler([PhaseStep(PhaseResult.NEXT)]),
        FsmState.P2_DISMISS: _StaticHandler([PhaseStep(PhaseResult.NEXT)]),
        FsmState.P3A_TEAM_CREATE: _StaticHandler([PhaseStep(PhaseResult.NEXT)]),
        FsmState.P4_MAP_SETUP: _StaticHandler([PhaseStep(PhaseResult.DONE)]),
    }
    fsm = RunnerFSM(ctx, handlers)
    ok = await fsm.run()
    assert ok and fsm.state == FsmState.DONE
    print("  ✓ runner_fsm_leader_path (P0→P1→P2→P3A→P4→DONE)")


async def _test_runner_fsm_follower_path():
    """follower: P0→P1→P2→P3b→DONE"""
    ctx = make_ctx()
    ctx.device.screenshot = AsyncMock(return_value=np.zeros((720, 1280, 3), dtype=np.uint8))
    ctx.role = "follower"

    handlers = {
        FsmState.P0_ACCELERATOR: _StaticHandler([PhaseStep(PhaseResult.NEXT)]),
        FsmState.P1_LAUNCH: _StaticHandler([PhaseStep(PhaseResult.NEXT)]),
        FsmState.P2_DISMISS: _StaticHandler([PhaseStep(PhaseResult.NEXT)]),
        FsmState.P3B_TEAM_JOIN: _StaticHandler([PhaseStep(PhaseResult.DONE)]),
    }
    fsm = RunnerFSM(ctx, handlers)
    ok = await fsm.run()
    assert ok and fsm.state == FsmState.DONE
    print("  ✓ runner_fsm_follower_path (P0→P1→P2→P3B→DONE)")


async def _test_runner_fsm_phase_fail_raises():
    """phase 返回 FAIL → RunnerFSM 抛 _PhaseFailError 给 runner_service"""
    ctx = make_ctx()
    ctx.device.screenshot = AsyncMock(return_value=np.zeros((720, 1280, 3), dtype=np.uint8))
    handlers = {
        FsmState.P0_ACCELERATOR: _StaticHandler([PhaseStep(PhaseResult.FAIL, note="vpn fail")]),
    }
    fsm = RunnerFSM(ctx, handlers)
    raised = False
    try:
        await fsm.run()
    except _PhaseFailError as e:
        raised = True
        assert e.state == FsmState.P0_ACCELERATOR
    assert raised, "FAIL 应抛 _PhaseFailError"
    print("  ✓ runner_fsm_phase_fail_raises")


async def _test_runner_fsm_game_restart_raises():
    """phase 返回 GAME_RESTART → 抛 _GameRestartError"""
    ctx = make_ctx()
    ctx.device.screenshot = AsyncMock(return_value=np.zeros((720, 1280, 3), dtype=np.uint8))
    handlers = {
        FsmState.P0_ACCELERATOR: _StaticHandler([PhaseStep(PhaseResult.NEXT)]),
        FsmState.P1_LAUNCH: _StaticHandler([PhaseStep(PhaseResult.NEXT)]),
        FsmState.P2_DISMISS: _StaticHandler([PhaseStep(PhaseResult.GAME_RESTART, note="login stuck")]),
    }
    fsm = RunnerFSM(ctx, handlers)
    raised = False
    try:
        await fsm.run()
    except _GameRestartError as e:
        raised = True
        assert e.state == FsmState.P2_DISMISS
    assert raised
    print("  ✓ runner_fsm_game_restart_raises")


# ───────── runner ─────────


def main():
    sync_tests = [
        test_phase_result_enum,
        test_run_context_reset,
        test_run_context_blacklist,
    ]
    async_tests = [
        _test_action_executor_noop,
        _test_action_executor_wait,
        _test_action_executor_tap_skips_blacklist,
        _test_runner_fsm_simple_path,
        _test_runner_fsm_leader_path,
        _test_runner_fsm_follower_path,
        _test_runner_fsm_phase_fail_raises,
        _test_runner_fsm_game_restart_raises,
    ]
    total = len(sync_tests) + len(async_tests)
    print(f"\nRunning {total} tests for v3 FSM\n" + "=" * 60)
    failed = []
    for t in sync_tests:
        try:
            t()
        except AssertionError as e:
            failed.append((t.__name__, str(e)))
            print(f"  ✗ {t.__name__}: {e}")
        except Exception as e:
            failed.append((t.__name__, f"EX: {e}"))
            print(f"  ✗ {t.__name__} EX: {e}")
            import traceback; traceback.print_exc()
    for t in async_tests:
        try:
            asyncio.run(t())
        except AssertionError as e:
            failed.append((t.__name__, str(e)))
            print(f"  ✗ {t.__name__}: {e}")
        except Exception as e:
            failed.append((t.__name__, f"EX: {e}"))
            print(f"  ✗ {t.__name__} EX: {e}")
            import traceback; traceback.print_exc()
    print("=" * 60)
    if failed:
        print(f"\n{len(failed)}/{total} FAILED")
        sys.exit(1)
    print(f"\n{total}/{total} PASSED ✓")


if __name__ == "__main__":
    main()

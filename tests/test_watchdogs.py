"""
watchdogs.py 单元测试.

跑法: python -X utf8 tests/test_watchdogs.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import numpy as np

from backend.automation.watchdogs import (
    WatchState,
    WatchdogManager,
    vpn_watchdog,
    process_watchdog,
    popup_watchdog,
)


# ───────────── stubs ─────────────


class StubDetection:
    def __init__(self, cls, conf=0.9, bbox=None):
        self.cls = cls
        self.conf = conf
        self.bbox = bbox or [0, 0, 40, 40]


def make_frame(seed=0):
    rng = np.random.RandomState(seed)
    return rng.randint(0, 256, (720, 1280, 3), dtype=np.uint8)


# ───────────── WatchState 测试 ─────────────


def test_state_vpn_signals():
    s = WatchState(instance_idx=0)
    assert s.vpn_ok is True
    s.vpn_signal(False)
    assert not s.vpn_ok and s.vpn_fail_count == 1
    s.vpn_signal(False)
    assert s.vpn_fail_count == 2
    s.vpn_signal(True)
    assert s.vpn_ok and s.vpn_fail_count == 0
    print("  ✓ state_vpn_signals")


def test_state_game_signals():
    s = WatchState(instance_idx=0)
    s.game_signal(12345)
    assert s.game_running and s.game_pid == 12345
    s.game_signal(-1)
    assert not s.game_running and s.game_pid == -1
    print("  ✓ state_game_signals")


def test_state_phash_stall_detection():
    s = WatchState(instance_idx=0, stall_threshold_s=10.0)
    # 模拟 5s 一轮, 同 phash 不变 → 累计
    s.phash_signal(0xDEADBEEF12345678, interval_s=5.0)
    assert not s.suspected_stall
    s.phash_signal(0xDEADBEEF12345678, interval_s=5.0)  # 5s 不变
    assert not s.suspected_stall
    s.phash_signal(0xDEADBEEF12345678, interval_s=5.0)  # 10s 不变
    assert s.suspected_stall, "10s 不变应触发卡死"
    # 画面变了 → 重置
    s.phash_signal(0xCAFEBABE00000001, interval_s=5.0)
    assert not s.suspected_stall
    assert s.phash_unchanged_seconds == 0
    print("  ✓ state_phash_stall_detection")


# ───────────── Watchdog 任务测试 ─────────────


async def _run_for(seconds: float, *coros):
    """跑指定秒数后取消所有 coro"""
    tasks = [asyncio.create_task(c) for c in coros]
    try:
        await asyncio.sleep(seconds)
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _test_vpn_watchdog_periodic():
    """vpn_watchdog 应周期性调 check_fn 并写状态"""
    s = WatchState(instance_idx=0)
    calls = []

    async def check_fn():
        calls.append(1)
        return len(calls) % 2 == 1  # 奇数次 OK, 偶数次 down

    await _run_for(0.7, vpn_watchdog(s, check_fn, interval_s=0.2))
    assert len(calls) >= 3, f"应至少 3 次, 实际 {len(calls)}"
    # 最后一次决定状态
    print("  ✓ vpn_watchdog_periodic")


async def _test_process_watchdog_pid_lost():
    """pidof 返回 -1 → state.game_running=False"""
    s = WatchState(instance_idx=0)

    async def pidof_fn():
        return -1

    await _run_for(0.3, process_watchdog(s, pidof_fn, interval_s=0.1))
    assert not s.game_running
    print("  ✓ process_watchdog_pid_lost")


async def _test_process_watchdog_phash_stall():
    """同样 phash 反复 → suspected_stall"""
    s = WatchState(instance_idx=0, stall_threshold_s=0.3)
    f = make_frame(seed=7)

    async def pidof_fn():
        return 12345

    async def screenshot_fn():
        return f  # 固定一帧

    await _run_for(0.5, process_watchdog(s, pidof_fn, screenshot_fn, interval_s=0.1))
    assert s.game_running, "进程应仍在跑"
    assert s.suspected_stall, "画面不变应触发卡死"
    print("  ✓ process_watchdog_phash_stall")


async def _test_popup_watchdog_phase_skip():
    """phase=dismiss_popups 时 popup_watchdog 应跳过 (不调截图)"""
    s = WatchState(instance_idx=0, current_phase="dismiss_popups")
    screenshot_calls = []
    yolo_calls = []

    async def screenshot_fn():
        screenshot_calls.append(1)
        return make_frame()

    def yolo_fn(f):
        yolo_calls.append(1)
        return []

    await _run_for(0.4, popup_watchdog(s, screenshot_fn, yolo_fn, interval_s=0.1))
    assert len(screenshot_calls) == 0, "dismiss_popups phase 应完全跳过"
    print("  ✓ popup_watchdog_phase_skip")


async def _test_popup_watchdog_handler_called():
    """全管 phase + 检测到弹窗 → handler 被调"""
    s = WatchState(instance_idx=0, current_phase="lobby")
    handler_calls = []

    async def screenshot_fn():
        return make_frame()

    def yolo_fn(f):
        return [StubDetection("close_x", conf=0.85)]

    async def handler_fn(detections):
        handler_calls.append(detections)

    await _run_for(0.3, popup_watchdog(s, screenshot_fn, yolo_fn, handler_fn,
                                        interval_s=0.1))
    assert len(handler_calls) >= 1, "应触发 handler"
    assert s.last_popup_count == 1
    print("  ✓ popup_watchdog_handler_called")


async def _test_popup_watchdog_system_only():
    """team_create phase + 检测到 close_x 弹窗 → 不调 handler (system_only)"""
    s = WatchState(instance_idx=0, current_phase="team_create")
    handler_calls = []

    async def screenshot_fn():
        return make_frame()

    def yolo_fn(f):
        return [StubDetection("close_x", conf=0.85)]  # 普通弹窗, 不是系统级

    async def handler_fn(detections):
        handler_calls.append(detections)

    await _run_for(0.3, popup_watchdog(s, screenshot_fn, yolo_fn, handler_fn,
                                        interval_s=0.1))
    assert len(handler_calls) == 0, "system_only 模式不应调 handler"
    print("  ✓ popup_watchdog_system_only")


async def _test_manager_lifecycle():
    """WatchdogManager 启动 + 停止"""
    s = WatchState(instance_idx=3)
    mgr = WatchdogManager(s)

    async def vpn_check():
        return True

    async def pidof():
        return 999

    async def screenshot():
        return make_frame()

    def yolo(f):
        return []

    mgr.start_vpn(vpn_check, interval_s=0.1)
    mgr.start_process(pidof, screenshot, interval_s=0.1)
    mgr.start_popup(screenshot, yolo, interval_s=0.1)
    await asyncio.sleep(0.2)
    assert mgr.task_count() == 3, f"应 3 个 task, 实际 {mgr.task_count()}"
    await mgr.stop_all()
    assert mgr.task_count() == 0, "stop_all 后应 0 个"
    print("  ✓ manager_lifecycle")


# ───────────── runner ─────────────


def main():
    sync_tests = [
        test_state_vpn_signals,
        test_state_game_signals,
        test_state_phash_stall_detection,
    ]
    async_tests = [
        _test_vpn_watchdog_periodic,
        _test_process_watchdog_pid_lost,
        _test_process_watchdog_phash_stall,
        _test_popup_watchdog_phase_skip,
        _test_popup_watchdog_handler_called,
        _test_popup_watchdog_system_only,
        _test_manager_lifecycle,
    ]
    total = len(sync_tests) + len(async_tests)
    print(f"\nRunning {total} tests for watchdogs\n" + "=" * 60)

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

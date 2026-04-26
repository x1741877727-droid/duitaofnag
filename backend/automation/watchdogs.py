"""
v2 横切系统 — 独立后台 Watchdog.

3 个 watchdog 都是独立 asyncio task, 由 runner_service.start_all 启动 per-instance.
任何 phase 期间触发, 不阻塞主流水线.

设计:
- Watchdog 只写 InstanceWatchState 状态
- Phase 主循环 (第 4 刀实施) 每轮检查 state, 严重时 raise
- v2 第 2 刀只装上 watchdog + 写状态, phase 暂不消费 (走旧失败回退)
- 这样第 2 刀风险最小 — 装上不会破坏现有行为, 只新增观察能力

Watchdog 列表:
  1. VPN Watchdog       (5s 心跳, 4 信号校验)
  2. ProcessWatchdog    (5s pidof + phash 卡死检测合一)
  3. PopupWatchdog      (2s YOLO 检测后台 + phase 感知)
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

import numpy as np

logger = logging.getLogger(__name__)


# ──────────── 共享状态 ────────────


@dataclass
class WatchState:
    """每实例的 watchdog 共享状态. phase 主循环读, watchdog 写."""

    instance_idx: int

    # VPN
    vpn_ok: bool = True
    vpn_fail_count: int = 0
    vpn_last_check_ts: float = 0.0

    # 游戏进程 (pidof)
    game_running: bool = True
    game_pid: int = -1
    game_last_check_ts: float = 0.0

    # 卡死 (画面 phash 长时间不变 = 软挂)
    last_phash: Optional[int] = None
    phash_unchanged_seconds: float = 0.0
    suspected_stall: bool = False
    stall_threshold_s: float = 60.0  # 60s 不变即视为软挂

    # 弹窗 watchdog 检测到的"反向通知"
    detected_login_page: bool = False
    detected_crash_page: bool = False
    last_popup_count: int = 0
    last_popup_check_ts: float = 0.0

    # 当前 phase (PopupWatchdog 用来决策是否处理)
    current_phase: str = ""

    # ─── 状态写入 helpers ───

    def vpn_signal(self, ok: bool, note: str = "") -> None:
        self.vpn_ok = ok
        self.vpn_fail_count = 0 if ok else self.vpn_fail_count + 1
        self.vpn_last_check_ts = time.time()
        if not ok:
            logger.warning(
                f"[watchdog#{self.instance_idx}] VPN down "
                f"(fail#{self.vpn_fail_count}) {note}"
            )

    def game_signal(self, pid: int) -> None:
        if pid > 0:
            self.game_running = True
            self.game_pid = pid
        else:
            self.game_running = False
            self.game_pid = -1
            logger.warning(f"[watchdog#{self.instance_idx}] 游戏进程消失")
        self.game_last_check_ts = time.time()

    def phash_signal(self, ph: int, interval_s: float) -> None:
        from .adb_lite import phash_distance

        if self.last_phash is None:
            self.last_phash = ph
            self.phash_unchanged_seconds = 0.0
            return
        if phash_distance(self.last_phash, ph) < 3:
            self.phash_unchanged_seconds += interval_s
        else:
            self.last_phash = ph
            self.phash_unchanged_seconds = 0.0
        was_stall = self.suspected_stall
        self.suspected_stall = self.phash_unchanged_seconds >= self.stall_threshold_s
        if self.suspected_stall and not was_stall:
            logger.warning(
                f"[watchdog#{self.instance_idx}] 画面卡死 "
                f"{int(self.phash_unchanged_seconds)}s"
            )

    def popup_signal(self, count: int, has_login: bool = False, has_crash: bool = False) -> None:
        self.last_popup_count = count
        self.detected_login_page = has_login
        self.detected_crash_page = has_crash
        self.last_popup_check_ts = time.time()


# ──────────── Watchdog 任务 ────────────


async def vpn_watchdog(
    state: WatchState,
    check_vpn_async: Callable[[], Awaitable[bool]],
    interval_s: float = 5.0,
) -> None:
    """VPN Watchdog: 周期检测 4 信号. check_vpn_async 由 single_runner 提供."""
    logger.info(f"[vpn-wd#{state.instance_idx}] 启动 (interval={interval_s}s)")
    while True:
        try:
            ok = await check_vpn_async()
            state.vpn_signal(ok)
        except asyncio.CancelledError:
            logger.info(f"[vpn-wd#{state.instance_idx}] 取消")
            raise
        except Exception as e:
            state.vpn_signal(False, f"check err: {e}")
        await asyncio.sleep(interval_s)


async def process_watchdog(
    state: WatchState,
    pidof_async: Callable[[], Awaitable[int]],
    screenshot_async: Optional[Callable[[], Awaitable[Optional[np.ndarray]]]] = None,
    interval_s: float = 5.0,
) -> None:
    """进程 + 卡死合一. pidof_async 必填, screenshot_async 提供则启用 phash 卡死检测."""
    from .adb_lite import phash as compute_phash

    logger.info(
        f"[proc-wd#{state.instance_idx}] 启动 "
        f"(interval={interval_s}s, stall_check={'on' if screenshot_async else 'off'})"
    )
    while True:
        try:
            pid = await pidof_async()
            state.game_signal(pid)

            if state.game_running and screenshot_async is not None:
                try:
                    frame = await screenshot_async()
                    if frame is not None:
                        ph = compute_phash(frame)
                        state.phash_signal(ph, interval_s)
                except Exception as e:
                    logger.debug(f"[proc-wd#{state.instance_idx}] phash skip: {e}")
        except asyncio.CancelledError:
            logger.info(f"[proc-wd#{state.instance_idx}] 取消")
            raise
        except Exception as e:
            logger.warning(f"[proc-wd#{state.instance_idx}] err: {e}")
        await asyncio.sleep(interval_s)


# Phase 名 → 是否要 PopupWatchdog 处理弹窗
_POPUP_PHASE_POLICY = {
    "dismiss_popups": "skip",        # 主流程在做, 跳过
    "team_create": "system_only",    # 只清系统级 (网络断开 / 服务器维护)
    "map_setup": "system_only",
    # 其他 phase 默认 "all" (全管)
}


async def popup_watchdog(
    state: WatchState,
    screenshot_async: Callable[[], Awaitable[Optional[np.ndarray]]],
    yolo_detect_fn: Callable[[np.ndarray], list],
    handler_fn: Optional[Callable[[list], Awaitable[None]]] = None,
    interval_s: float = 2.0,
) -> None:
    """PopupWatchdog: 后台 2s YOLO 检测.

    yolo_detect_fn(frame) -> list[Detection (cls, conf, bbox)]
    handler_fn(detections) -> 可选, 处理弹窗的函数 (e.g. 调 yolo_dismisser._tap_close_x)

    phase 感知:
      dismiss_popups → 完全跳过 (主流程在做)
      team_create / map_setup → 只在检测到登录页 / 闪退页时反向通知
      其他 → 全管, handler 处理
    """
    logger.info(f"[popup-wd#{state.instance_idx}] 启动 (interval={interval_s}s)")
    while True:
        try:
            policy = _POPUP_PHASE_POLICY.get(state.current_phase, "all")
            if policy == "skip":
                await asyncio.sleep(interval_s)
                continue

            frame = await screenshot_async()
            if frame is None:
                await asyncio.sleep(interval_s)
                continue

            detections = yolo_detect_fn(frame) or []
            count = len(detections)

            # 关键：检测特殊页面（登录页 / 闪退页）— 这里只是占位标识
            # 实际识别留给后续 phase 接入 recognizer 时实现
            has_login = any(getattr(d, "cls", "") == "login_page" for d in detections)
            has_crash = any(getattr(d, "cls", "") == "crash_dialog" for d in detections)

            state.popup_signal(count, has_login=has_login, has_crash=has_crash)

            # system_only 策略下, 只反向通知, 不主动 tap
            if policy == "system_only":
                if has_login or has_crash:
                    logger.warning(
                        f"[popup-wd#{state.instance_idx}] 系统级页面 "
                        f"login={has_login} crash={has_crash}"
                    )
                await asyncio.sleep(interval_s)
                continue

            # 全管: 把 detections 交给 handler 处理 (e.g. yolo_dismisser)
            if handler_fn and count > 0:
                try:
                    await handler_fn(detections)
                except Exception as e:
                    logger.debug(f"[popup-wd#{state.instance_idx}] handler err: {e}")

        except asyncio.CancelledError:
            logger.info(f"[popup-wd#{state.instance_idx}] 取消")
            raise
        except Exception as e:
            logger.warning(f"[popup-wd#{state.instance_idx}] err: {e}")
        await asyncio.sleep(interval_s)


# ──────────── Manager: per-instance 一组 watchdog ────────────


class WatchdogManager:
    """一组 watchdog 的生命周期管理.

    用法:
        mgr = WatchdogManager(state)
        mgr.start_vpn(check_vpn_async)
        mgr.start_process(pidof_async, screenshot_async)
        mgr.start_popup(screenshot_async, yolo_detect, handler)
        ...
        await mgr.stop_all()
    """

    def __init__(self, state: WatchState):
        self.state = state
        self._tasks: list[asyncio.Task] = []

    def start_vpn(self, check_fn, interval_s: float = 5.0) -> asyncio.Task:
        task = asyncio.create_task(
            vpn_watchdog(self.state, check_fn, interval_s),
            name=f"vpn-wd#{self.state.instance_idx}",
        )
        self._tasks.append(task)
        return task

    def start_process(
        self,
        pidof_fn,
        screenshot_fn=None,
        interval_s: float = 5.0,
    ) -> asyncio.Task:
        task = asyncio.create_task(
            process_watchdog(self.state, pidof_fn, screenshot_fn, interval_s),
            name=f"proc-wd#{self.state.instance_idx}",
        )
        self._tasks.append(task)
        return task

    def start_popup(
        self,
        screenshot_fn,
        yolo_detect_fn,
        handler_fn=None,
        interval_s: float = 2.0,
    ) -> asyncio.Task:
        task = asyncio.create_task(
            popup_watchdog(
                self.state, screenshot_fn, yolo_detect_fn, handler_fn, interval_s
            ),
            name=f"popup-wd#{self.state.instance_idx}",
        )
        self._tasks.append(task)
        return task

    async def stop_all(self) -> None:
        for task in self._tasks:
            if not task.done():
                task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    def task_count(self) -> int:
        return sum(1 for t in self._tasks if not t.done())

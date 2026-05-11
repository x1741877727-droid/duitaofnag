"""WatchdogTask — asyncio 包装 v1 vm_watchdog (LDPlayer 死了自动重启).

设计 (REVIEW_DAY3_WATCHDOG.md):
- 全局 1 个 task, 跟 12 个 runner task 平级 (asyncio.gather)
- 30s 检测一次 ldconsole list2, 找 running=1 但 pid=-1 的死亡实例
- 死了 → ldconsole launch 重启
- 不抢 runner 资源 (独立 task)

V2 不重新实现, 复用 v1 vm_watchdog.py (核心逻辑) + 异步包装.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class WatchdogTask:
    """全局 vm_watchdog asyncio task. start() 启 / stop() 停."""

    def __init__(self, ldplayer_path: str, check_interval_s: float = 30.0):
        self.ldplayer_path = ldplayer_path
        self.check_interval_s = check_interval_s
        self._task: Optional[asyncio.Task] = None
        self._stop = False
        self._restart_counts: dict[int, int] = {}    # inst_idx → 重启次数
        self._max_restart_per_inst = 5

    def start(self) -> asyncio.Task:
        """启动 watchdog asyncio task. 返 task 供 gather."""
        if self._task is not None and not self._task.done():
            return self._task
        self._stop = False
        self._task = asyncio.create_task(self._loop(), name="vm_watchdog")
        logger.info(f"[watchdog] started, interval={self.check_interval_s}s")
        return self._task

    async def stop(self) -> None:
        """优雅停止."""
        self._stop = True
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info(f"[watchdog] stopped")

    async def _loop(self) -> None:
        """主 loop: 每 N 秒检查一次, 重启死亡实例."""
        try:
            while not self._stop:
                try:
                    dead = await asyncio.to_thread(self._scan_dead_instances)
                    for inst_idx in dead:
                        if self._restart_counts.get(inst_idx, 0) >= self._max_restart_per_inst:
                            logger.warning(
                                f"[watchdog] inst{inst_idx} restart 次数已达上限 "
                                f"{self._max_restart_per_inst}, 跳过"
                            )
                            continue
                        ok = await asyncio.to_thread(self._restart_instance, inst_idx)
                        if ok:
                            self._restart_counts[inst_idx] = self._restart_counts.get(inst_idx, 0) + 1
                            logger.info(
                                f"[watchdog] inst{inst_idx} 重启 "
                                f"(总{self._restart_counts[inst_idx]}/{self._max_restart_per_inst})"
                            )
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.warning(f"[watchdog] loop iter err: {e}")

                # 等下次 (允许 cancel)
                try:
                    await asyncio.sleep(self.check_interval_s)
                except asyncio.CancelledError:
                    raise
        except asyncio.CancelledError:
            logger.info("[watchdog] cancelled")
            raise

    # ─────────── v1 接入 ───────────

    def _scan_dead_instances(self) -> list[int]:
        """复用 v1 vm_watchdog 的扫描逻辑. 返死亡 inst_idx 列表.

        TODO Day 3+: 接 v1 backend.automation.vm_watchdog.get_watchdog().scan()
        临时 stub: 返空 list (无死亡).
        """
        # from backend.automation.vm_watchdog import get_watchdog
        # return get_watchdog().scan_dead_instances()
        return []

    def _restart_instance(self, inst_idx: int) -> bool:
        """复用 v1 vm_watchdog 的重启逻辑.

        TODO Day 3+: 接 v1 重启路径
            ldconsole = self.ldplayer_path + "/ldconsole.exe"
            subprocess.run([ldconsole, "launch", "--index", str(inst_idx)])

        临时 stub: 返 False.
        """
        return False

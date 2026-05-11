"""
VM watchdog — 监控 LDPlayer 实例死亡, 自动 ldconsole launch 重启.

触发条件: ldconsole list2 输出某 inst running=1 但 PID=-1 (vbox 崩了壳还在).
重启策略: launch + 等 startup_interval, 不抢资源.

跨机器一致, 不依赖具体硬件. 装到 backend 启动 hook 即可.

参考: docs/PERF_TUNING.md §4
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _find_ldconsole() -> Optional[str]:
    """探测 LDPlayer 9 的 ldconsole.exe 路径. 跨机器适配."""
    candidates = [
        r"D:\leidian\LDPlayer9\ldconsole.exe",
        r"C:\leidian\LDPlayer9\ldconsole.exe",
        r"E:\leidian\LDPlayer9\ldconsole.exe",
        r"D:\Program Files\leidian\LDPlayer9\ldconsole.exe",
        r"C:\Program Files\leidian\LDPlayer9\ldconsole.exe",
        os.path.expandvars(r"%ProgramFiles%\leidian\LDPlayer9\ldconsole.exe"),
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return None


class VmWatchdog:
    """每 N 秒扫一次 ldconsole list2, 发现 inst running=1 但 PID=-1 就 relaunch.

    使用:
        wd = VmWatchdog(interval=30)
        wd.start()  # 后台 task, 后端整个生命周期跑

        wd.stop()   # 关 backend 时调
    """

    def __init__(self, interval: int = 30, ldconsole_path: Optional[str] = None,
                 max_relaunch_per_inst: int = 5):
        self.interval = interval
        self.ldconsole = ldconsole_path or _find_ldconsole()
        # 防雪崩: 单实例累计重启次数上限, 超过就放弃 (可能配置坏了)
        self.max_relaunch = max_relaunch_per_inst
        self._relaunch_count: dict[int, int] = {}
        self._stop_event = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

    def start(self) -> None:
        if self.ldconsole is None:
            logger.warning("[vm_watchdog] 找不到 ldconsole.exe, 跳过 VM 监控")
            return
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._loop(), name="vm_watchdog")
        logger.info(f"[vm_watchdog] 启动, interval={self.interval}s, ldconsole={self.ldconsole}")

    def stop(self) -> None:
        self._stop_event.set()
        if self._task and not self._task.done():
            self._task.cancel()

    async def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._check_once()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[vm_watchdog] check 异常: {e}")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.interval)
            except asyncio.TimeoutError:
                pass

    async def _check_once(self) -> None:
        """跑一次 ldconsole list2 → 解析 → 死的 relaunch."""
        try:
            # ldconsole 中文输出可能是 GBK
            proc = await asyncio.create_subprocess_exec(
                self.ldconsole, "list2",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            text = stdout.decode("gbk", errors="replace")
        except Exception as e:
            logger.debug(f"[vm_watchdog] list2 调用失败: {e}")
            return

        # 输出格式: idx,name,top_window,bind_window,running,pid,vbox_pid,w,h,dpi
        # 例: 5,雷电模拟器-5,0,0,0,-1,-1,960,540,240   ← 整体死
        #     6,雷电模拟器-6,1,0,1,-1,-1,960,540,240   ← running=1 但 PID=-1 = vbox 崩
        for line in text.strip().splitlines():
            parts = line.split(",")
            if len(parts) < 6:
                continue
            try:
                idx = int(parts[0])
                running = int(parts[4])
                pid = int(parts[5])
            except ValueError:
                continue
            # 真死 = running 标 1 但进程 PID 是 -1 (vbox 崩, 壳没回收)
            # 注: running=0 是用户主动关的, 不重启
            if running == 1 and pid == -1:
                self._relaunch_count[idx] = self._relaunch_count.get(idx, 0) + 1
                if self._relaunch_count[idx] > self.max_relaunch:
                    logger.warning(
                        f"[vm_watchdog] inst{idx} 累计重启 {self._relaunch_count[idx]} 次 > {self.max_relaunch}, "
                        f"停止重试 (检查 LDPlayer 配置 / 硬件)"
                    )
                    continue
                logger.warning(
                    f"[vm_watchdog] inst{idx} VM 死 (running=1, pid=-1) → 自动 relaunch "
                    f"(第 {self._relaunch_count[idx]} 次)"
                )
                try:
                    subprocess.Popen(
                        [self.ldconsole, "launch", "--index", str(idx)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                except Exception as e:
                    logger.error(f"[vm_watchdog] inst{idx} launch 失败: {e}")
                # 给一次重启 15s 间隔, 避免连续 launch
                await asyncio.sleep(15)


# ────────── 单例 ──────────

_singleton: Optional[VmWatchdog] = None


def get_watchdog() -> VmWatchdog:
    global _singleton
    if _singleton is None:
        _singleton = VmWatchdog()
    return _singleton

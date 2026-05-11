"""ADB tap + screenshot + start_app — Protocol + 默认 subprocess 实现.

设计:
- AdbTap Protocol 稳定, 换实现 (PurePythonAdbTap / MaaTouchTap) 不破上层
- 默认 SubprocessAdbTap: subprocess.run adb shell input tap

性能:
- subprocess 单实例 ~80-150ms, 12 实例并发因 ADB server :5037 串行 ~200-300ms
- POC adb-shell pure-python 可降到 50-100ms (Day 7+ 评估, 不在本 plan)

用户指令 (2026-05-11): tap 暂不优化, 沿用 subprocess + 固定坐标 (不抖动)
"""
from __future__ import annotations

import asyncio
import logging
import platform
import subprocess
from pathlib import Path
from typing import Any, Optional, Protocol

logger = logging.getLogger(__name__)

# Windows 下隐藏 subprocess 的 cmd 窗口
_SUBPROCESS_FLAGS = (
    subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0
)


class AdbTapProto(Protocol):
    async def tap(self, x: int, y: int) -> None: ...
    async def screenshot(self) -> Any: ...   # np.ndarray
    async def start_app(self, package: str) -> None: ...


class SubprocessAdbTap:
    """subprocess.run adb shell — 默认实现.

    单 tap ~80-150ms (Windows fork 开销). 6/12 实例并发因 ADB server 串行较慢.
    优势: 简单稳定, 跟 v1 一致, 0 引入新风险.

    未来优化路径 (Day 7+):
    - adb-shell python lib (TCP 持久, 跳 fork, 50-100ms)
    - MaaTouch (Java app_process, 10ms 但坐标系/ABI 兼容性需先验证)
    """

    def __init__(self, serial: str, adb_path: str = "adb", *, timeout_s: float = 5.0):
        self.serial = serial
        self.adb = adb_path
        self.timeout_s = timeout_s

    async def tap(self, x: int, y: int) -> None:
        """固定坐标 tap (不抖动). 用户要求 (2026-05-11)."""
        await asyncio.to_thread(self._sync_tap, int(x), int(y))

    def _sync_tap(self, x: int, y: int) -> None:
        try:
            subprocess.run(
                [self.adb, "-s", self.serial, "shell",
                 "input", "tap", str(x), str(y)],
                check=False,
                capture_output=True,
                timeout=self.timeout_s,
                creationflags=_SUBPROCESS_FLAGS,
            )
        except subprocess.TimeoutExpired:
            logger.warning(f"[adb/{self.serial}] tap timeout x={x} y={y}")
        except Exception as e:
            logger.warning(f"[adb/{self.serial}] tap err: {e}")

    async def start_app(self, package: str) -> None:
        """am start. P1 用. monkey 比 am start 兼容更好."""
        await asyncio.to_thread(self._sync_start_app, package)

    def _sync_start_app(self, package: str) -> None:
        try:
            subprocess.run(
                [self.adb, "-s", self.serial, "shell",
                 "monkey", "-p", package,
                 "-c", "android.intent.category.LAUNCHER", "1"],
                check=False,
                capture_output=True,
                timeout=8.0,
                creationflags=_SUBPROCESS_FLAGS,
            )
        except Exception as e:
            logger.warning(f"[adb/{self.serial}] start_app {package} err: {e}")

    async def screenshot(self) -> Optional[Any]:
        """fallback: adb exec-out screencap.

        生产用 LDOpenGL fast-path 直接读 framebuffer (~3ms),
        adb exec-out screencap 慢 (~300-500ms), 仅作 fallback.
        """
        return await asyncio.to_thread(self._sync_screenshot)

    def _sync_screenshot(self) -> Optional[Any]:
        try:
            import cv2
            import numpy as np
            r = subprocess.run(
                [self.adb, "-s", self.serial, "exec-out", "screencap", "-p"],
                capture_output=True,
                timeout=5.0,
                creationflags=_SUBPROCESS_FLAGS,
            )
            if r.returncode != 0:
                return None
            arr = np.frombuffer(r.stdout, dtype=np.uint8)
            return cv2.imdecode(arr, cv2.IMREAD_COLOR)
        except Exception as e:
            logger.warning(f"[adb/{self.serial}] screenshot err: {e}")
            return None

    async def shell(self, cmd: str, *, timeout: float = 5.0) -> tuple[int, str]:
        """通用 adb shell. 返 (returncode, stdout)."""
        return await asyncio.to_thread(self._sync_shell, cmd, timeout)

    def _sync_shell(self, cmd: str, timeout: float) -> tuple[int, str]:
        try:
            r = subprocess.run(
                [self.adb, "-s", self.serial, "shell", cmd],
                capture_output=True,
                timeout=timeout,
                creationflags=_SUBPROCESS_FLAGS,
            )
            return r.returncode, r.stdout.decode("utf-8", errors="replace")
        except Exception as e:
            logger.warning(f"[adb/{self.serial}] shell '{cmd[:50]}' err: {e}")
            return -1, ""

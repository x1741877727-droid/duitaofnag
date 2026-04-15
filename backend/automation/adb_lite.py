"""
ADB 控制器 — 基于 subprocess 直接调用 adb 命令
专门为雷电模拟器优化，已在实机测试中验证通过。
"""

import asyncio
import logging
import platform
import subprocess
import time
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# Windows 下隐藏 subprocess 的 cmd 窗口
_SUBPROCESS_FLAGS = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0

# 全局信号量：限制并发 ADB 截图数（screencap 是最重的操作）
# 6个实例同时 screencap 会让 ADB 排队到 8 秒+，限制并发到 2 就能控制在 1 秒内
_screenshot_semaphore: Optional[asyncio.Semaphore] = None


def _get_semaphore() -> asyncio.Semaphore:
    global _screenshot_semaphore
    if _screenshot_semaphore is None:
        _screenshot_semaphore = asyncio.Semaphore(2)
    return _screenshot_semaphore


class ADBController:
    """
    ADB控制器 — 直接调用adb命令
    专门为雷电模拟器优化
    """

    def __init__(self, serial: str, adb_path: str = "adb"):
        self.serial = serial
        self.adb_path = adb_path
        self._proc_timeout = 10

    def _cmd(self, *args) -> str:
        """同步执行adb命令"""
        cmd = [self.adb_path, "-s", self.serial] + list(args)
        try:
            result = subprocess.run(
                cmd, capture_output=True, timeout=self._proc_timeout,
                creationflags=_SUBPROCESS_FLAGS,
            )
            # 尝试多种编码解码
            for enc in ("utf-8", "gbk"):
                try:
                    return result.stdout.decode(enc)
                except UnicodeDecodeError:
                    continue
            return result.stdout.decode("utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            logger.warning(f"ADB命令超时: {cmd}")
            return ""
        except Exception as e:
            logger.error(f"ADB命令失败: {cmd} -> {e}")
            return ""

    async def screenshot(self) -> Optional[np.ndarray]:
        """截图并返回numpy数组 (BGR)，信号量限制并发防止 ADB 排队"""
        async with _get_semaphore():
            loop = asyncio.get_event_loop()
            try:
                raw = await loop.run_in_executor(None, self._screenshot_sync)
                return raw
            except Exception as e:
                logger.error(f"截图失败: {e}")
                return None

    def _screenshot_sync(self) -> Optional[np.ndarray]:
        """同步截图"""
        t0 = time.perf_counter()
        cmd = [self.adb_path, "-s", self.serial, "exec-out", "screencap", "-p"]
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=10,
                                    creationflags=_SUBPROCESS_FLAGS)
            t1 = time.perf_counter()
            if result.returncode != 0:
                return None
            png_data = result.stdout
            if len(png_data) < 100:
                return None
            arr = np.frombuffer(png_data, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            t2 = time.perf_counter()
            adb_ms = (t1 - t0) * 1000
            decode_ms = (t2 - t1) * 1000
            if adb_ms > 500:
                logger.warning(f"[性能] 截图慢: ADB={adb_ms:.0f}ms decode={decode_ms:.0f}ms")
            return img
        except Exception:
            return None

    async def tap(self, x: int, y: int):
        """点击（带随机抖动）"""
        import random
        jx = x + random.randint(-3, 3)
        jy = y + random.randint(-3, 3)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, self._cmd, "shell", f"input tap {jx} {jy}"
        )

    async def key_event(self, key: str):
        """按键事件"""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, self._cmd, "shell", f"input keyevent {key}"
        )

    async def start_app(self, package: str, activity: str = ""):
        """启动应用"""
        if activity:
            component = f"{package}/{activity}"
            await self._async_cmd("shell", f"am start -n {component}")
        else:
            await self._async_cmd("shell", f"monkey -p {package} -c android.intent.category.LAUNCHER 1")

    async def stop_app(self, package: str):
        """强制停止应用"""
        await self._async_cmd("shell", f"am force-stop {package}")

    async def get_clipboard(self) -> str:
        """读取剪贴板"""
        loop = asyncio.get_event_loop()
        output = await loop.run_in_executor(
            None, self._cmd, "shell", "am broadcast -a clipper.get"
        )
        return output.strip()

    async def set_clipboard(self, text: str):
        """写入剪贴板"""
        await self._async_cmd("shell", f"am broadcast -a clipper.set -e text '{text}'")

    async def open_url(self, url: str):
        """通过intent打开URL"""
        await self._async_cmd("shell", f"am start -a android.intent.action.VIEW -d '{url}'")

    async def _async_cmd(self, *args) -> str:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._cmd, *args)

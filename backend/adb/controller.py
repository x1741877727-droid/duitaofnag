"""
ADB 操作控制器
封装截图、点击、滑动、文字输入等操作
支持 mock 模式用于 macOS 开发
"""

import asyncio
import io
import logging
import os
import random
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class TapResult:
    """点击操作结果"""
    x: int
    y: int
    success: bool
    timestamp: float


class ADBController:
    """
    单实例 ADB 控制器
    每个模拟器实例对应一个 controller
    """

    def __init__(self, serial: str, instance_index: int,
                 mock: bool = False, mock_screenshots_dir: str = ""):
        """
        Args:
            serial: ADB 连接地址，如 "127.0.0.1:5555"
            instance_index: 模拟器实例编号
            mock: 是否为 mock 模式
            mock_screenshots_dir: mock 模式下的截图目录
        """
        self.serial = serial
        self.instance_index = instance_index
        self.mock = mock
        self.mock_screenshots_dir = mock_screenshots_dir
        self._device = None  # adbutils device 对象
        self._mock_screenshot_index = 0
        self._connected = False

    async def connect(self) -> bool:
        """连接到 ADB 设备"""
        if self.mock:
            logger.info(f"[MOCK] 连接实例 {self.instance_index}: {self.serial}")
            self._connected = True
            return True

        try:
            # 动态导入 adbutils（仅 Windows 需要）
            import adbutils
            client = adbutils.AdbClient()
            # 连接到指定地址
            client.connect(self.serial, timeout=10)
            self._device = client.device(self.serial)
            # 验证连接
            self._device.shell("echo ok")
            self._connected = True
            logger.info(f"ADB 连接成功: 实例 {self.instance_index} ({self.serial})")
            return True
        except Exception as e:
            logger.error(f"ADB 连接失败: 实例 {self.instance_index} ({self.serial}): {e}")
            self._connected = False
            return False

    async def disconnect(self):
        """断开 ADB 连接"""
        self._device = None
        self._connected = False
        logger.info(f"ADB 断开: 实例 {self.instance_index}")

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def screenshot(self) -> Optional[np.ndarray]:
        """
        截取屏幕，返回 numpy 数组 (BGR 格式，OpenCV 标准)
        """
        if self.mock:
            return self._mock_screenshot()

        if not self._device:
            logger.error(f"实例 {self.instance_index} 未连接")
            return None

        try:
            # adbutils 截图返回 PIL Image
            img = self._device.screenshot()
            # 转为 numpy 数组 (RGB → BGR)
            import cv2
            arr = np.array(img)
            bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
            return bgr
        except Exception as e:
            logger.error(f"实例 {self.instance_index} 截图失败: {e}")
            return None

    def _mock_screenshot(self) -> Optional[np.ndarray]:
        """Mock 模式：从目录读取截图样本"""
        if not self.mock_screenshots_dir or not os.path.isdir(self.mock_screenshots_dir):
            # 没有样本目录，生成一张纯色图
            logger.debug(f"[MOCK] 实例 {self.instance_index} 生成空白截图")
            return np.zeros((720, 1280, 3), dtype=np.uint8)

        # 读取目录下的图片文件
        files = sorted([
            f for f in os.listdir(self.mock_screenshots_dir)
            if f.lower().endswith((".png", ".jpg", ".jpeg"))
        ])
        if not files:
            return np.zeros((720, 1280, 3), dtype=np.uint8)

        # 循环读取
        filepath = os.path.join(self.mock_screenshots_dir, files[self._mock_screenshot_index % len(files)])
        self._mock_screenshot_index += 1

        import cv2
        img = cv2.imread(filepath)
        if img is None:
            logger.warning(f"[MOCK] 无法读取截图: {filepath}")
            return np.zeros((720, 1280, 3), dtype=np.uint8)

        logger.debug(f"[MOCK] 实例 {self.instance_index} 读取截图: {filepath}")
        return img

    async def tap(self, x: int, y: int, duration_ms: int = 0) -> TapResult:
        """
        点击指定坐标
        Args:
            x, y: 屏幕坐标
            duration_ms: 长按时间（毫秒），0 为普通点击
        """
        # 添加微小随机偏移，模拟人类操作
        jitter_x = random.randint(-3, 3)
        jitter_y = random.randint(-3, 3)
        tx, ty = x + jitter_x, y + jitter_y

        if self.mock:
            logger.info(f"[MOCK] 实例 {self.instance_index} 点击 ({tx}, {ty})")
            return TapResult(tx, ty, True, time.time())

        try:
            if duration_ms > 0:
                self._device.shell(f"input swipe {tx} {ty} {tx} {ty} {duration_ms}")
            else:
                self._device.shell(f"input tap {tx} {ty}")
            logger.debug(f"实例 {self.instance_index} 点击 ({tx}, {ty})")
            return TapResult(tx, ty, True, time.time())
        except Exception as e:
            logger.error(f"实例 {self.instance_index} 点击失败: {e}")
            return TapResult(tx, ty, False, time.time())

    async def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300):
        """滑动操作"""
        if self.mock:
            logger.info(f"[MOCK] 实例 {self.instance_index} 滑动 ({x1},{y1}) → ({x2},{y2})")
            return True

        try:
            self._device.shell(f"input swipe {x1} {y1} {x2} {y2} {duration_ms}")
            logger.debug(f"实例 {self.instance_index} 滑动 ({x1},{y1}) → ({x2},{y2})")
            return True
        except Exception as e:
            logger.error(f"实例 {self.instance_index} 滑动失败: {e}")
            return False

    async def input_text(self, text: str):
        """输入文字（仅支持 ASCII，中文需要通过其他方式）"""
        if self.mock:
            logger.info(f"[MOCK] 实例 {self.instance_index} 输入文字: {text}")
            return True

        try:
            # 转义特殊字符
            escaped = text.replace(" ", "%s").replace("&", "\\&").replace("<", "\\<").replace(">", "\\>")
            self._device.shell(f"input text '{escaped}'")
            return True
        except Exception as e:
            logger.error(f"实例 {self.instance_index} 输入文字失败: {e}")
            return False

    async def key_event(self, keycode: int):
        """发送按键事件（如 HOME=3, BACK=4, ENTER=66）"""
        if self.mock:
            logger.info(f"[MOCK] 实例 {self.instance_index} 按键: {keycode}")
            return True

        try:
            self._device.shell(f"input keyevent {keycode}")
            return True
        except Exception as e:
            logger.error(f"实例 {self.instance_index} 按键失败: {e}")
            return False

    async def shell(self, command: str) -> str:
        """执行 ADB shell 命令"""
        if self.mock:
            logger.info(f"[MOCK] 实例 {self.instance_index} shell: {command}")
            return ""

        try:
            result = self._device.shell(command)
            return result
        except Exception as e:
            logger.error(f"实例 {self.instance_index} shell 命令失败: {e}")
            return ""

    async def start_app(self, package: str, activity: str = ""):
        """启动应用"""
        if activity:
            cmd = f"am start -n {package}/{activity}"
        else:
            cmd = f"monkey -p {package} -c android.intent.category.LAUNCHER 1"
        return await self.shell(cmd)

    async def force_stop_app(self, package: str):
        """强制停止应用"""
        return await self.shell(f"am force-stop {package}")

    async def open_url(self, url: str):
        """通过 ADB 打开 URL（用于组队链接跳转）"""
        logger.info(f"实例 {self.instance_index} 打开链接: {url}")
        return await self.shell(f"am start -a android.intent.action.VIEW -d '{url}'")

    async def set_wifi(self, enable: bool):
        """控制 WiFi 开关"""
        state = "enable" if enable else "disable"
        return await self.shell(f"svc wifi {state}")

    async def set_data(self, enable: bool):
        """控制移动数据开关"""
        state = "enable" if enable else "disable"
        return await self.shell(f"svc data {state}")

    async def disconnect_network(self):
        """断网（关闭 wifi + 数据）"""
        logger.info(f"实例 {self.instance_index} 断网")
        await self.set_wifi(False)
        await self.set_data(False)

    async def restore_network(self):
        """恢复网络"""
        logger.info(f"实例 {self.instance_index} 恢复网络")
        await self.set_wifi(True)
        await self.set_data(True)

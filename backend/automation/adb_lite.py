"""
ADB 控制器 — 基于 subprocess 直接调用 adb 命令
专门为雷电模拟器优化，已在实机测试中验证通过。

截图策略：
  1. 优先用 minicap 流式截图（~30ms，6个模拟器完全并行）
  2. minicap 不可用时回退到 screencap（信号量限流防排队）
"""

import asyncio
import logging
import os
import platform
import re
import socket
import struct
import subprocess
import threading
import time
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# Windows 下隐藏 subprocess 的 cmd 窗口
_SUBPROCESS_FLAGS = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0

# screencap 回退用信号量（minicap 不可用时）
_screenshot_semaphore: Optional[asyncio.Semaphore] = None


def _get_semaphore() -> asyncio.Semaphore:
    global _screenshot_semaphore
    if _screenshot_semaphore is None:
        _screenshot_semaphore = asyncio.Semaphore(2)
    return _screenshot_semaphore


# ====================================================================
# Minicap 流式截图
# ====================================================================

def _find_minicap_dir() -> str:
    """查找 minicap 二进制目录"""
    import sys as _sys
    if getattr(_sys, 'frozen', False):
        root = os.path.dirname(_sys.executable)
    else:
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    candidates = [
        os.path.join(root, "tools", "minicap"),
        os.path.join(root, "_internal", "tools", "minicap"),
    ]
    for d in candidates:
        if os.path.isfile(os.path.join(d, "minicap")):
            return d
    return candidates[0]


class MinicapStream:
    """Minicap 流式截图 — 后台线程持续读帧，screenshot() 直接取最新帧

    比 screencap 快 10-20x，且各实例完全并行无阻塞。
    """

    DEVICE_PATH = "/data/local/tmp/minicap"
    DEVICE_SO = "/data/local/tmp/minicap.so"

    def __init__(self, adb_path: str, serial: str, port: int):
        self._adb_path = adb_path
        self._serial = serial
        self._port = port
        self._sock: Optional[socket.socket] = None
        self._running = False
        self._latest_frame: Optional[np.ndarray] = None
        self._frame_lock = threading.Lock()
        self._reader_thread: Optional[threading.Thread] = None
        self._frame_time: float = 0  # 最新帧的时间戳

    def _adb(self, *args) -> str:
        cmd = [self._adb_path, "-s", self._serial] + list(args)
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=15,
                               creationflags=_SUBPROCESS_FLAGS)
            return r.stdout.decode("utf-8", errors="replace")
        except Exception:
            return ""

    def setup(self) -> bool:
        """推送 minicap → 启动进程 → 端口转发 → 连接读帧"""
        try:
            return self._do_setup()
        except Exception as e:
            logger.warning(f"[minicap] {self._serial} 初始化失败: {e}")
            return False

    def _do_setup(self) -> bool:
        # 1. 检查是否已推送
        check = self._adb("shell", f"ls {self.DEVICE_PATH} 2>/dev/null")
        if "minicap" not in check:
            minicap_dir = _find_minicap_dir()
            bin_path = os.path.join(minicap_dir, "minicap")
            so_path = os.path.join(minicap_dir, "minicap.so")
            if not os.path.isfile(bin_path):
                logger.warning(f"[minicap] 二进制不存在: {bin_path}")
                return False
            logger.info(f"[minicap] {self._serial} 推送二进制...")
            self._adb("push", bin_path, self.DEVICE_PATH)
            self._adb("push", so_path, self.DEVICE_SO)
            self._adb("shell", f"chmod 755 {self.DEVICE_PATH}")

        # 2. 获取分辨率
        wm = self._adb("shell", "wm size")
        m = re.search(r'(\d+)x(\d+)', wm)
        if not m:
            logger.warning(f"[minicap] {self._serial} 无法获取分辨率")
            return False
        w, h = m.group(1), m.group(2)

        # 3. 杀旧进程 + 启动新进程
        self._adb("shell", "pkill -f minicap 2>/dev/null")
        time.sleep(0.3)
        # 后台启动，-S = skip frames（只保留最新帧，低延迟）
        self._adb("shell",
                   f"LD_LIBRARY_PATH=/data/local/tmp "
                   f"nohup /data/local/tmp/minicap "
                   f"-P {w}x{h}@{w}x{h}/0 -S "
                   f"> /dev/null 2>&1 &")
        time.sleep(1)

        # 4. 端口转发
        self._adb("forward", f"tcp:{self._port}", "localabstract:minicap")
        time.sleep(0.3)

        # 5. 连接 socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3)
        try:
            sock.connect(("127.0.0.1", self._port))
        except (ConnectionRefusedError, socket.timeout) as e:
            logger.warning(f"[minicap] {self._serial} 连接失败: {e}")
            sock.close()
            return False

        # 6. 读 banner（24字节）
        banner = self._read_exact(sock, 24)
        vw = struct.unpack_from("<I", banner, 14)[0]
        vh = struct.unpack_from("<I", banner, 18)[0]
        logger.info(f"[minicap] {self._serial} 已连接 {vw}x{vh}")

        # 7. 启动后台读帧线程
        self._sock = sock
        self._sock.settimeout(5)
        self._running = True
        self._reader_thread = threading.Thread(
            target=self._frame_reader, daemon=True,
            name=f"minicap-{self._serial}"
        )
        self._reader_thread.start()
        return True

    def get_frame(self) -> Optional[np.ndarray]:
        """获取最新帧（~0ms，直接从内存读）"""
        with self._frame_lock:
            return self._latest_frame

    @property
    def available(self) -> bool:
        return self._running

    def _frame_reader(self):
        """后台线程：持续从 socket 读取 JPEG 帧并解码"""
        while self._running:
            try:
                # 4 字节帧长度（小端 uint32）
                length_data = self._read_exact(self._sock, 4)
                frame_len = struct.unpack("<I", length_data)[0]
                if frame_len == 0:
                    continue

                # 读 JPEG 数据
                jpeg_data = self._read_exact(self._sock, frame_len)
                if len(jpeg_data) < 2 or jpeg_data[:2] != b"\xff\xd8":
                    continue

                # 解码
                arr = np.frombuffer(jpeg_data, dtype=np.uint8)
                frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if frame is not None:
                    with self._frame_lock:
                        self._latest_frame = frame
                        self._frame_time = time.time()
            except Exception:
                logger.debug(f"[minicap] {self._serial} 读帧断开")
                self._running = False
                break

    def stop(self):
        """停止流"""
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
        self._adb("shell", "pkill -f minicap 2>/dev/null")
        self._adb("forward", "--remove", f"tcp:{self._port}")

    @staticmethod
    def _read_exact(sock: socket.socket, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("minicap socket closed")
            buf += chunk
        return buf


# ====================================================================
# ADB 控制器
# ====================================================================

class ADBController:
    """
    ADB控制器 — 直接调用adb命令
    专门为雷电模拟器优化
    """

    # minicap 端口基数（每个模拟器 +1）
    _MINICAP_BASE_PORT = 1313

    def __init__(self, serial: str, adb_path: str = "adb"):
        self.serial = serial
        self.adb_path = adb_path
        self._proc_timeout = 10
        self._minicap: Optional[MinicapStream] = None

    def setup_minicap(self) -> bool:
        """初始化 minicap 流式截图（启动时调用一次）

        返回 True 表示 minicap 可用，False 则回退到 screencap。
        """
        # 从 serial 推算端口：emulator-5554 → 5554 → port 1313+0
        try:
            emu_port = int(self.serial.split("-")[1])
            offset = (emu_port - 5554) // 2
        except (IndexError, ValueError):
            offset = 0
        port = self._MINICAP_BASE_PORT + offset

        stream = MinicapStream(self.adb_path, self.serial, port)
        if stream.setup():
            self._minicap = stream
            return True
        return False

    def _cmd(self, *args) -> str:
        """同步执行adb命令"""
        cmd = [self.adb_path, "-s", self.serial] + list(args)
        try:
            result = subprocess.run(
                cmd, capture_output=True, timeout=self._proc_timeout,
                creationflags=_SUBPROCESS_FLAGS,
            )
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
        """截图 — minicap 优先（~0ms），screencap 回退（信号量限流）"""
        # minicap 路径：直接从内存取最新帧
        if self._minicap and self._minicap.available:
            frame = self._minicap.get_frame()
            if frame is not None:
                return frame
            # 帧为空（可能刚启动），短暂等待
            await asyncio.sleep(0.1)
            frame = self._minicap.get_frame()
            if frame is not None:
                return frame

        # screencap 回退
        async with _get_semaphore():
            loop = asyncio.get_event_loop()
            try:
                return await loop.run_in_executor(None, self._screenshot_sync)
            except Exception as e:
                logger.error(f"截图失败: {e}")
                return None

    def _screenshot_sync(self) -> Optional[np.ndarray]:
        """同步截图（screencap 回退）"""
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


# ====================================================================
# pHash 帧差检测
# ====================================================================

def phash(img: np.ndarray) -> int:
    """计算感知哈希（64bit），用于快速判断画面是否变化

    缩小到 8x8 灰度 → DCT → 取低频 → 生成 64bit 哈希。~0.3ms。
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img
    resized = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA)
    dct = cv2.dct(np.float32(resized))
    dct_low = dct[:8, :8]
    med = np.median(dct_low)
    h = 0
    for i in range(8):
        for j in range(8):
            if dct_low[i, j] > med:
                h |= 1 << (i * 8 + j)
    return h


def phash_distance(h1: int, h2: int) -> int:
    """两个 pHash 的汉明距离（不同 bit 数）"""
    return bin(h1 ^ h2).count('1')

"""
ADB 控制器 — 基于 subprocess 直接调用 adb 命令
专门为雷电模拟器优化，已在实机测试中验证通过。

截图策略（2026-04-25 重构，UE4 兼容）：
  1. 优先用 ScreenrecordStream（adb shell screenrecord raw H.264 + PyAV，UE4 OK）
  2. 不可用时回退到 screencap（信号量限流防排队）
  - minicap 抓 UE4 黑帧（HWC overlay + secure flag），已弃用
"""

import asyncio
import io
import logging
import os
import platform
import subprocess
import threading
import time
from typing import Optional

import cv2
import numpy as np

from . import metrics

logger = logging.getLogger(__name__)

# Windows 下隐藏 subprocess 的 cmd 窗口
_SUBPROCESS_FLAGS = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0

# screencap 回退用信号量
_screenshot_semaphore: Optional[asyncio.Semaphore] = None


def _get_semaphore() -> asyncio.Semaphore:
    global _screenshot_semaphore
    if _screenshot_semaphore is None:
        _screenshot_semaphore = asyncio.Semaphore(2)
    return _screenshot_semaphore


# ====================================================================
# ScreenrecordStream — UE4 兼容截图（adb shell screenrecord + PyAV 解 H.264）
# ====================================================================

class ScreenrecordStream:
    """长流 screenrecord raw H.264 + PyAV 解码 → 后台线程持续刷新最新帧

    工作原理：
      1. subprocess.Popen(adb -s <serial> exec-out screenrecord
            --time-limit=170 --output-format=h264 --bit-rate=4M -)
      2. 后台 reader 线程把 stdout 字节累积到 BytesIO
      3. 累积 ≥16KB 后 av.open(BytesIO, format='h264') 解最新帧
      4. screenrecord 180s 上限，subprocess 退出后自动重启
      5. BytesIO 超 1MB 自动 trim 到末尾 256KB（保留下一个 IDR 重启解码）

    UE4 验证：6/6 LDPlayer 9 实例 + 和平精英非黑帧（2026-04-25 实测）。
    与 minicap 不同：走 SurfaceFlinger 系统级 screenrecord 路径，不受 HWC overlay 影响。
    """

    # screenrecord 默认 --time-limit=180s 强制断流，此处留 10s 余量给重启
    SCREENRECORD_TIME_LIMIT = 170
    SCREENRECORD_BIT_RATE = 4_000_000
    BUFFER_TRIM_THRESHOLD = 1_000_000  # 1MB 累积后 trim
    BUFFER_TRIM_KEEP = 256_000          # 保留末尾 256KB 等下一个 IDR
    DECODE_INTERVAL_BYTES = 16384       # 每累积 16KB 触发一次 decode

    def __init__(self, adb_path: str, serial: str):
        self._adb_path = adb_path
        self._serial = serial
        self._proc: Optional[subprocess.Popen] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._latest_frame: Optional[np.ndarray] = None
        self._frame_time: float = 0
        self._frame_lock = threading.Lock()
        self._frame_count = 0
        self._restart_count = 0
        self._available = False

    def setup(self) -> bool:
        """启动后台 reader 线程（subprocess 由 reader 自己管理）。"""
        try:
            import av  # noqa: F401  — 校验依赖
        except ImportError:
            logger.warning("[screenrecord] PyAV 未安装，无法用 UE4 截图")
            return False

        self._stop_event.clear()
        self._available = True
        self._reader_thread = threading.Thread(
            target=self._reader_loop, daemon=True,
            name=f"screenrecord-{self._serial}",
        )
        self._reader_thread.start()
        # 等待最多 2 秒拿到首帧
        for _ in range(20):
            if self._latest_frame is not None:
                return True
            time.sleep(0.1)
        # 没拿到首帧也认为可用（等业务调用时再 retry）
        logger.info(f"[screenrecord] {self._serial} reader 已启动（首帧暂未到）")
        return True

    def _spawn_subprocess(self) -> Optional[subprocess.Popen]:
        cmd = [
            self._adb_path, "-s", self._serial, "exec-out",
            "screenrecord",
            f"--time-limit={self.SCREENRECORD_TIME_LIMIT}",
            "--output-format=h264",
            "--bit-rate", str(self.SCREENRECORD_BIT_RATE),
            "-",
        ]
        try:
            return subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                creationflags=_SUBPROCESS_FLAGS,
            )
        except Exception as e:
            logger.warning(f"[screenrecord] {self._serial} subprocess 启动失败: {e}")
            return None

    def _reader_loop(self):
        """主循环：subprocess EOF 后自动重启，累积字节周期解码。"""
        import av

        while not self._stop_event.is_set():
            self._proc = self._spawn_subprocess()
            if self._proc is None:
                time.sleep(2)
                continue

            buf = io.BytesIO()
            last_decode_pos = 0
            session_frames = 0
            try:
                while not self._stop_event.is_set():
                    if self._proc.stdout is None:
                        break
                    chunk = self._proc.stdout.read(8192)
                    if not chunk:
                        break  # EOF — 出循环重启
                    buf.write(chunk)

                    if buf.tell() - last_decode_pos < self.DECODE_INTERVAL_BYTES:
                        continue
                    last_decode_pos = buf.tell()

                    # 拷贝到独立 BytesIO 给 av.open 用
                    buf.seek(0)
                    snapshot = io.BytesIO(buf.read())
                    snapshot.seek(0)
                    buf.seek(0, 2)

                    try:
                        container = av.open(snapshot, format="h264", mode="r")
                        if container.streams.video:
                            stream = container.streams.video[0]
                            stream.thread_type = "AUTO"
                            last_arr = None
                            decoded_n = 0
                            for frame in container.decode(stream):
                                last_arr = frame.to_ndarray(format="bgr24")
                                decoded_n += 1
                            if last_arr is not None:
                                with self._frame_lock:
                                    self._latest_frame = last_arr
                                    self._frame_time = time.time()
                                    self._frame_count += decoded_n
                                session_frames += decoded_n
                        container.close()
                    except Exception as e:
                        # decode 失败常见情况：没拿到完整 SPS/PPS，等下次累积
                        if session_frames == 0 and last_decode_pos > 100_000:
                            logger.debug(f"[screenrecord] {self._serial} 等首帧 decode_err={e}")

                    # 内存控制：超过 1MB 时 trim 末尾 256KB
                    if buf.tell() > self.BUFFER_TRIM_THRESHOLD:
                        buf.seek(0)
                        all_bytes = buf.read()
                        buf = io.BytesIO(all_bytes[-self.BUFFER_TRIM_KEEP:])
                        buf.seek(0, 2)
                        last_decode_pos = buf.tell()

            except Exception as e:
                logger.warning(f"[screenrecord] {self._serial} reader 异常: {e}")

            # subprocess 退出 → 重启（除非被 stop）
            try:
                self._proc.kill()
            except Exception:
                pass
            self._proc = None

            if self._stop_event.is_set():
                break
            self._restart_count += 1
            logger.info(f"[screenrecord] {self._serial} 重启 #{self._restart_count} (本轮 {session_frames} 帧)")
            time.sleep(0.2)

        self._available = False

    def get_frame(self) -> Optional[np.ndarray]:
        with self._frame_lock:
            return self._latest_frame

    @property
    def available(self) -> bool:
        return self._available and self._latest_frame is not None

    def stop(self):
        self._stop_event.set()
        self._available = False
        if self._proc:
            try:
                self._proc.kill()
            except Exception:
                pass
        if self._reader_thread:
            self._reader_thread.join(timeout=2)


# ====================================================================
# ADB 控制器
# ====================================================================

class ADBController:
    """
    ADB控制器 — 直接调用adb命令
    专门为雷电模拟器优化
    """

    def __init__(self, serial: str, adb_path: str = "adb"):
        self.serial = serial
        self.adb_path = adb_path
        self._proc_timeout = 10
        self._stream: Optional[ScreenrecordStream] = None

    def setup_minicap(self) -> bool:
        """[向后兼容名] 初始化截图流。

        - 默认：不启动流式截图，screenshot() 走 screencap（慢但稳定）
        - GAMEBOT_CAPTURE=screenrecord：启用 ScreenrecordStream（UE4 验证过但
          6 实例并发会压垮 SurfaceFlinger 导致游戏闪退，已知不稳定）
        - GAMEBOT_CAPTURE=mediaprojection：启用 vpn-app CaptureService（待实装）

        旧名 setup_minicap 保留是因为外部调用点很多。
        """
        backend = os.environ.get("GAMEBOT_CAPTURE", "").lower()

        if backend == "screenrecord":
            logger.warning("[capture] GAMEBOT_CAPTURE=screenrecord: 6 实例并发可能导致游戏闪退")
            stream = ScreenrecordStream(self.adb_path, self.serial)
            if stream.setup():
                self._stream = stream
                return True
            return False

        if backend == "mediaprojection":
            logger.warning("[capture] GAMEBOT_CAPTURE=mediaprojection: 待实装")
            # TODO: Phase D — vpn-app CaptureService 集成
            return False

        # 默认：直接回退 screencap
        logger.info("[capture] 使用 adb screencap（默认稳定方案）")
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
        """截图 — screenrecord 优先（UE4 兼容，~50ms），screencap 回退（信号量限流）"""
        with metrics.timed("screenshot") as tags:
            # ScreenrecordStream 路径
            if self._stream is not None:
                frame = self._stream.get_frame()
                if frame is not None:
                    tags["backend"] = "screenrecord"
                    tags["h"], tags["w"] = frame.shape[:2]
                    return frame
                # 帧为空（可能刚启动 / IDR 间隔长），短暂等待
                await asyncio.sleep(0.2)
                frame = self._stream.get_frame()
                if frame is not None:
                    tags["backend"] = "screenrecord_retry"
                    tags["h"], tags["w"] = frame.shape[:2]
                    return frame

            # screencap 回退（适用于 stream 未启动 / 永久死掉）
            async with _get_semaphore():
                loop = asyncio.get_event_loop()
                try:
                    frame = await loop.run_in_executor(None, self._screenshot_sync)
                    tags["backend"] = "screencap"
                    if frame is not None:
                        tags["h"], tags["w"] = frame.shape[:2]
                    return frame
                except Exception as e:
                    logger.error(f"截图失败: {e}")
                    tags["backend"] = "error"
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
        with metrics.timed("tap", x=jx, y=jy):
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

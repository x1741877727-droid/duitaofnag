"""
ADB 控制器 — 基于 subprocess 直接调用 adb 命令
专门为雷电模拟器优化，已在实机测试中验证通过。

截图策略（GAMEBOT_CAPTURE 环境变量）：
  - dxhook (生产推荐): 注入 hook DLL 到 Ld9BoxHeadless.exe，glReadPixels 抓
    GPU 帧到共享内存。1280x720 原生分辨率，~0ms get_frame，绕窗口/ACE
  - wgc: Windows Graphics Capture 抓 LDPlayer 窗口（窗口缩小会糊）
  - screenrecord: adb shell screenrecord（6 实例并发崩游戏）
  - 默认: adb screencap（慢但稳）
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
# WGCStream — Windows Graphics Capture 抓 LDPlayer 窗口（生产推荐）
# ====================================================================

# LDPlayer serial → 窗口标题映射
#   emulator-5554 → 雷电模拟器        (实例 0)
#   emulator-5556 → 雷电模拟器-1      (实例 1)
#   ...
#   emulator-5564 → 雷电模拟器-5      (实例 5)
def _ldplayer_window_title(serial: str) -> str:
    try:
        emu_port = int(serial.split("-")[1])
        idx = (emu_port - 5554) // 2
    except (IndexError, ValueError):
        idx = 0
    return "雷电模拟器" if idx == 0 else f"雷电模拟器-{idx}"


def _find_window_by_title(title: str) -> int:
    """EnumWindows 查 LDPlayer 主窗口 HWND（class=LDPlayerMainFrame）"""
    try:
        import win32gui
    except ImportError:
        logger.warning("[wgc] pywin32 未安装，无法找窗口")
        return 0
    target = [0]

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        if win32gui.GetWindowText(hwnd) == title:
            target[0] = hwnd
            return False
        return True

    try:
        win32gui.EnumWindows(cb, None)
    except Exception as e:
        logger.warning(f"[wgc] EnumWindows 失败: {e}")
    return target[0]


class WGCStream:
    """Windows Graphics Capture 抓 LDPlayer 窗口 → BGR ndarray

    架构：
      windows-capture (PyPI 包，Rust + WGC API)
        → on_frame_arrived 回调（free-threaded）
        → BGRA frame_buffer
        → BGRA → BGR 转换（cv2）
        → 存 _latest_frame
      screenshot() 调 get_frame() 直接拿内存中的最新帧

    优势 vs 其他方案:
      - 在 Windows 端 DXGI 层抓帧，**完全绕开 Android**
      - 无 ACE 接触面（capture 不进游戏进程也不进 Android）
      - 6 实例独立 capture session，无 SurfaceFlinger 资源竞争
      - 窗口被遮挡仍能抓（OBS Game Capture 同款 API）
    """

    # LDPlayer 9 自有 UI chrome 比例（client area 内画的，非 Windows 标题栏）
    # 实测 1318×754 窗口下：top toolbar 30px / right sidebar 56px
    LDPLAYER_TOP_RATIO = 30 / 754      # ≈ 4.0%
    LDPLAYER_RIGHT_RATIO = 56 / 1318   # ≈ 4.3%
    # 最小可用窗口尺寸（小于此值文字会糊到 OCR 失效）
    MIN_WINDOW_W = 1100
    MIN_WINDOW_H = 620

    def __init__(self, serial: str):
        self.serial = serial
        self.window_title = _ldplayer_window_title(serial)
        self._capture = None  # WindowsCapture 实例
        self._capture_control = None
        self._latest_frame: Optional[np.ndarray] = None  # BGR
        self._frame_time: float = 0
        self._frame_lock = threading.Lock()
        self._frame_count = 0
        self._available = False
        self._hwnd = 0
        # client area 在 WGC 帧里的偏移（Windows 标准非客户区 — 标题栏 + 边框）
        self._client_x = 0
        self._client_y = 0
        self._client_w = 0
        self._client_h = 0
        # 业务侧期望 720 高度（跟 Android 屏幕一致）
        self._target_h = 720

    def setup(self) -> bool:
        try:
            from windows_capture import WindowsCapture
        except ImportError:
            logger.warning("[wgc] windows-capture 未安装（pip install windows-capture）")
            return False

        self._hwnd = _find_window_by_title(self.window_title)
        if not self._hwnd:
            logger.warning(f"[wgc] {self.serial}: 找不到窗口 {self.window_title!r}")
            return False

        # 算 client area 在 WGC 帧里的偏移（去标题栏 + 边框）
        try:
            import win32gui
            cl = win32gui.GetClientRect(self._hwnd)            # (0, 0, cw, ch)
            cw, ch = cl[2] - cl[0], cl[3] - cl[1]
            cl_pt = win32gui.ClientToScreen(self._hwnd, (0, 0))  # client 左上 screen 坐标
            wr = win32gui.GetWindowRect(self._hwnd)              # 窗口 screen 矩形
            self._client_x = cl_pt[0] - wr[0]
            self._client_y = cl_pt[1] - wr[1]
            self._client_w = cw
            self._client_h = ch
            logger.info(f"[wgc] {self.serial}: client offset=({self._client_x},{self._client_y}) size={cw}x{ch} window={wr[2]-wr[0]}x{wr[3]-wr[1]}")
        except Exception as e:
            logger.warning(f"[wgc] {self.serial}: 算 client rect 失败: {e}（不 crop）")

        try:
            self._capture = WindowsCapture(
                cursor_capture=False,
                draw_border=False,
                window_hwnd=self._hwnd,
            )
        except Exception as e:
            logger.warning(f"[wgc] {self.serial}: WindowsCapture 创建失败: {e}")
            return False

        @self._capture.event
        def on_frame_arrived(frame, capture_control):
            try:
                buf = frame.frame_buffer  # BGRA ndarray, shape=(window_h, window_w, 4)
                # 1. crop 到 Windows client area（去标准标题栏 + 边框）
                if self._client_w > 0 and self._client_h > 0:
                    x, y, w, h = self._client_x, self._client_y, self._client_w, self._client_h
                    if y + h <= buf.shape[0] and x + w <= buf.shape[1]:
                        buf = buf[y:y+h, x:x+w]
                # 2. crop LDPlayer 自有 chrome（顶部工具栏 + 右侧侧栏）
                top = self.LDPLAYER_TOP_TOOLBAR
                right = self.LDPLAYER_RIGHT_SIDEBAR
                if buf.shape[0] > top + 100 and buf.shape[1] > right + 100:
                    buf = buf[top:, :buf.shape[1] - right]
                # 3. BGRA → BGR
                bgr = cv2.cvtColor(buf, cv2.COLOR_BGRA2BGR)
                # 4. resize 到目标高度（保持比例），跟 Android 1280x720 一致
                if bgr.shape[0] != self._target_h:
                    h, w = bgr.shape[:2]
                    new_w = int(w * self._target_h / h)
                    bgr = cv2.resize(bgr, (new_w, self._target_h))
                with self._frame_lock:
                    self._latest_frame = bgr
                    self._frame_time = time.time()
                    self._frame_count += 1
                    self._capture_control = capture_control
            except Exception as e:
                logger.debug(f"[wgc] {self.serial} on_frame err: {e}")

        @self._capture.event
        def on_closed():
            logger.info(f"[wgc] {self.serial} capture closed")
            self._available = False

        try:
            self._capture.start_free_threaded()
        except Exception as e:
            logger.warning(f"[wgc] {self.serial}: start 失败: {e}")
            return False

        self._available = True
        logger.info(f"[wgc] {self.serial}: 启动 (window={self.window_title!r} hwnd=0x{self._hwnd:08x})")

        # 等首帧最多 2s
        for _ in range(20):
            if self._latest_frame is not None:
                return True
            time.sleep(0.1)
        # 没拿到首帧也认为可用，下游 retry
        return True

    def get_frame(self) -> Optional[np.ndarray]:
        with self._frame_lock:
            return self._latest_frame

    @property
    def available(self) -> bool:
        return self._available and self._latest_frame is not None

    def stop(self):
        self._available = False
        if self._capture_control is not None:
            try:
                self._capture_control.stop()
            except Exception:
                pass
        self._capture = None
        self._capture_control = None


# ====================================================================
# DXHookStream — 注入 64-bit DLL 到 Ld9BoxHeadless 抓 GPU 帧（生产推荐）
# ====================================================================

# 共享内存协议（与 tools/dxhook/hook.c 同步）
_DXHOOK_SHM_MAGIC = 0x42476843  # 'GBhC'
_DXHOOK_SHM_HEADER_BYTES = 32
_DXHOOK_SHM_MAX_W = 2560
_DXHOOK_SHM_MAX_H = 1440
_DXHOOK_SHM_TOTAL = _DXHOOK_SHM_HEADER_BYTES + _DXHOOK_SHM_MAX_W * _DXHOOK_SHM_MAX_H * 4


def _find_dxhook_assets() -> Optional[tuple]:
    """定位 64-bit dll + injector 路径（开发模式 / PyInstaller frozen 都支持）"""
    import sys as _sys
    if getattr(_sys, 'frozen', False):
        roots = [
            os.path.dirname(_sys.executable),
            os.path.join(os.path.dirname(_sys.executable), "_internal"),
        ]
    else:
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        roots = [repo_root]
    for root in roots:
        dll = os.path.join(root, "tools", "dxhook", "build", "x64", "gamebot_hook.dll")
        injector = os.path.join(root, "tools", "dxhook", "build", "x64", "inject.exe")
        if os.path.isfile(dll) and os.path.isfile(injector):
            return dll, injector
    # 兼容已部署到 _internal/dxhook/ 的扁平布局
    for root in roots:
        dll = os.path.join(root, "dxhook", "gamebot_hook.dll")
        injector = os.path.join(root, "dxhook", "inject.exe")
        if os.path.isfile(dll) and os.path.isfile(injector):
            return dll, injector
    return None


def _serial_to_ldidx(serial: str) -> int:
    """emulator-5554 → 0, emulator-5556 → 1, ..."""
    try:
        port = int(serial.split("-")[1])
        return (port - 5554) // 2
    except (IndexError, ValueError):
        return 0


def _find_box_pid_for_idx(ld_idx: int) -> Optional[int]:
    """通过 cmdline `--comment leidianN` 找 Ld9BoxHeadless.exe PID"""
    try:
        import psutil
    except ImportError:
        return None
    # 实例 0 是 "leidian"，1+ 是 "leidian1"、"leidian2" ...
    target = "leidian" if ld_idx == 0 else f"leidian{ld_idx}"
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if proc.info['name'] != 'Ld9BoxHeadless.exe':
                continue
            cmd = ' '.join(proc.info['cmdline'] or [])
            # 用 token 边界匹配避免 leidian1 误匹配 leidian10
            for tok in cmd.split():
                if tok == target:
                    return proc.info['pid']
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None


class DXHookStream:
    """注入 hook DLL 到 LDPlayer 9 的 Ld9BoxHeadless.exe，glReadPixels 抓帧。

    工作流程：
      1. serial → LDPlayer instance idx → 找对应 Ld9BoxHeadless.exe PID（cmdline `--comment leidianN`）
      2. subprocess 跑 inject.exe pid dll_path
      3. 等共享内存 "Local\\GameBotCap_<PID>" 出现 + magic 校验通过
      4. mmap 该共享内存，每次 get_frame 读 header 拿最新帧

    优势 vs 其他方案：
      - 抓 GPU 渲染管线最深层（Ld9BoxHeadless 内部，比 dnplayer 早）
      - 1280x720 Android 原生分辨率，与窗口大小完全无关
      - hook 在 Windows 主机端，不进 Android，无 ACE 接触面
      - get_frame() 直接从内存读，~0ms latency
    """

    def __init__(self, serial: str):
        self.serial = serial
        self.ld_idx = _serial_to_ldidx(serial)
        self.box_pid: Optional[int] = None
        self._mmap: Optional["mmap.mmap"] = None
        self._latest_arr: Optional[np.ndarray] = None
        self._latest_frame_n = -1
        self._available = False

    def setup(self) -> bool:
        if platform.system() != "Windows":
            logger.warning("[dxhook] 只支持 Windows")
            return False

        try:
            import psutil  # noqa: F401
        except ImportError:
            logger.warning("[dxhook] psutil 未安装")
            return False

        assets = _find_dxhook_assets()
        if not assets:
            logger.warning("[dxhook] 找不到 gamebot_hook.dll / inject.exe（先 make all64）")
            return False
        dll_path, injector_path = assets

        self.box_pid = _find_box_pid_for_idx(self.ld_idx)
        if not self.box_pid:
            logger.warning(f"[dxhook] {self.serial}: 找不到 leidian{self.ld_idx} Ld9BoxHeadless 进程")
            return False

        # 运行注入器
        try:
            r = subprocess.run(
                [injector_path, str(self.box_pid), dll_path],
                capture_output=True, timeout=10,
                creationflags=_SUBPROCESS_FLAGS,
            )
            if r.returncode != 0:
                logger.warning(f"[dxhook] {self.serial}: inject 失败 rc={r.returncode} "
                               f"out={r.stdout.decode(errors='replace')[:200]}")
                return False
        except Exception as e:
            logger.warning(f"[dxhook] {self.serial}: 跑 inject.exe 失败: {e}")
            return False

        # 打开共享内存（DLL 在 hook_init_thread 异步建，最多等 5s）
        import mmap
        shm_name = f"GameBotCap_{self.box_pid}"
        for _ in range(50):
            try:
                self._mmap = mmap.mmap(-1, _DXHOOK_SHM_TOTAL,
                                        tagname=shm_name,
                                        access=mmap.ACCESS_READ)
                hdr = self._mmap[:_DXHOOK_SHM_HEADER_BYTES]
                magic, *_ = struct.unpack("<IIIIIIII", hdr)
                if magic == _DXHOOK_SHM_MAGIC:
                    break
            except Exception:
                pass
            time.sleep(0.1)
        else:
            logger.warning(f"[dxhook] {self.serial}: 共享内存 {shm_name} 未就绪")
            return False

        self._available = True
        logger.info(f"[dxhook] {self.serial} 启用 (box_pid={self.box_pid} shm={shm_name})")
        return True

    def get_frame(self) -> Optional[np.ndarray]:
        if not self._available or self._mmap is None:
            return None
        hdr = self._mmap[:_DXHOOK_SHM_HEADER_BYTES]
        magic, frame_n, w, h, _ts, _stride, _r0, _r1 = struct.unpack("<IIIIIIII", hdr)
        if magic != _DXHOOK_SHM_MAGIC or w == 0 or h == 0:
            return self._latest_arr  # 返回缓存的最后一帧
        if frame_n & 0x80000000:
            # 写入中，返回上一帧
            return self._latest_arr
        if frame_n == self._latest_frame_n and self._latest_arr is not None:
            return self._latest_arr  # 没新帧

        # 读新帧
        if w > _DXHOOK_SHM_MAX_W or h > _DXHOOK_SHM_MAX_H:
            return self._latest_arr
        nbytes = w * h * 4
        raw = bytes(self._mmap[_DXHOOK_SHM_HEADER_BYTES:_DXHOOK_SHM_HEADER_BYTES + nbytes])
        rgba = np.frombuffer(raw, dtype=np.uint8).reshape((h, w, 4))
        # OpenGL 原点左下，图像左上 → 上下翻转
        rgba = np.flipud(rgba)
        bgr = cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGR)
        # ascontiguousarray 防 numpy view 问题
        self._latest_arr = np.ascontiguousarray(bgr)
        self._latest_frame_n = frame_n
        return self._latest_arr

    @property
    def available(self) -> bool:
        return self._available

    def stop(self):
        self._available = False
        if self._mmap is not None:
            try:
                self._mmap.close()
            except Exception:
                pass
            self._mmap = None


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

        backend = GAMEBOT_CAPTURE 环境变量：
        - dxhook (生产推荐): 注入 64-bit DLL 到 Ld9BoxHeadless，glReadPixels 抓 GPU 帧
        - wgc: Windows Graphics Capture 抓 LDPlayer 窗口（受窗口尺寸影响）
        - screenrecord: adb shell screenrecord（6 实例崩游戏）
        - 默认 (空): screencap（慢但稳）

        旧名 setup_minicap 保留是因为外部调用点很多。
        """
        backend = os.environ.get("GAMEBOT_CAPTURE", "").lower()

        if backend == "dxhook":
            stream = DXHookStream(self.serial)
            if stream.setup():
                self._stream = stream
                logger.info(f"[capture] {self.serial} 启用 DXHook")
                return True
            logger.warning(f"[capture] {self.serial} DXHook 启动失败，回退 screencap")
            return False

        if backend == "wgc":
            stream = WGCStream(self.serial)
            if stream.setup():
                self._stream = stream
                logger.info(f"[capture] {self.serial} 启用 WGC")
                return True
            logger.warning(f"[capture] {self.serial} WGC 启动失败，回退 screencap")
            return False

        if backend == "screenrecord":
            logger.warning(f"[capture] {self.serial} screenrecord: 6 实例并发可能崩游戏")
            stream = ScreenrecordStream(self.adb_path, self.serial)
            if stream.setup():
                self._stream = stream
                return True
            return False

        # 默认：直接回退 screencap
        logger.info(f"[capture] {self.serial} 使用 adb screencap（默认稳定方案）")
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
        """截图 — stream 优先（WGC/screenrecord，~50ms），screencap 回退（信号量限流）"""
        with metrics.timed("screenshot") as tags:
            if self._stream is not None:
                frame = self._stream.get_frame()
                # 根据 stream 类型记 backend tag
                stream_name = type(self._stream).__name__.replace("Stream", "").lower()
                if frame is not None:
                    tags["backend"] = stream_name  # wgc / screenrecord
                    tags["h"], tags["w"] = frame.shape[:2]
                    return frame
                # 帧为空（刚启动 / IDR 间隔长），短暂等待
                await asyncio.sleep(0.2)
                frame = self._stream.get_frame()
                if frame is not None:
                    tags["backend"] = f"{stream_name}_retry"
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

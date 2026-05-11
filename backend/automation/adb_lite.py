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
import sys
import platform
import struct
import subprocess
import threading
import time
from typing import Optional

import cv2
import numpy as np

from . import metrics
from .screencap_ldopengl import LdopenglManager

logger = logging.getLogger(__name__)

# Windows 下隐藏 subprocess 的 cmd 窗口
_SUBPROCESS_FLAGS = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0

# screencap 回退用信号量
_screenshot_semaphore: Optional[asyncio.Semaphore] = None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MaaTouch — minitouch 协议 Java 实现, 解决 subprocess fork 慢
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 实测 (2026-05-11):
#   subprocess.run input tap 单实例 p50=125ms, 6 并发 p50=185ms (ADB server 串行)
#   MaaTouch stdin write+flush p50=0.02ms, 端到端 ~15-30ms
# 协议: minitouch text protocol (d/m/u/c/w/k/t)
# ABI: Java app_process, 跑在 ART, 不需要 native binary, LDPlayer x86_64 直接 work

import threading
from pathlib import Path

_MAATOUCH_REMOTE_PATH = "/data/local/tmp/maatouch"
_MAATOUCH_LOCAL_PATH: Optional[Path] = None    # lazy 探测
_MAATOUCH_PUSH_LOCK = threading.Lock()
_MAATOUCH_PUSHED_SERIALS: set = set()           # 已经 push 过的 serial


def _find_maatouch_jar() -> Optional[Path]:
    """找仓库内 bin/maatouch jar. 找不到返回 None."""
    global _MAATOUCH_LOCAL_PATH
    if _MAATOUCH_LOCAL_PATH is not None:
        return _MAATOUCH_LOCAL_PATH
    here = Path(__file__).resolve()
    # backend/automation/adb_lite.py -> repo root -> bin/maatouch
    for cand in [
        here.parent.parent.parent / "bin" / "maatouch",
        here.parent.parent / "bin" / "maatouch",
        Path.cwd() / "bin" / "maatouch",
    ]:
        if cand.is_file():
            _MAATOUCH_LOCAL_PATH = cand
            return cand
    return None


class MaaTouchController:
    """每实例一个持久 MaaTouch process, stdin 发 tap 命令.

    生命周期: lazy init (第一次 tap 启动), 死了自动重启.
    """

    def __init__(self, adb_path: str, serial: str):
        self.adb_path = adb_path
        self.serial = serial
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._banner: Optional[str] = None
        self._consecutive_fails = 0

    @staticmethod
    def push_jar(adb_path: str, serial: str) -> bool:
        """同步 push maatouch jar 到 LDPlayer (幂等). 返回是否 ready."""
        with _MAATOUCH_PUSH_LOCK:
            if serial in _MAATOUCH_PUSHED_SERIALS:
                return True
            local = _find_maatouch_jar()
            if local is None:
                logger.warning("[maatouch] bin/maatouch jar 找不到, 跳过 push")
                return False
            try:
                # 先 ls 看是否已存在 + size 一致
                check = subprocess.run(
                    [adb_path, "-s", serial, "shell", f"ls -la {_MAATOUCH_REMOTE_PATH}"],
                    capture_output=True, timeout=5, creationflags=_SUBPROCESS_FLAGS,
                )
                local_size = local.stat().st_size
                if check.returncode == 0 and str(local_size).encode() in check.stdout:
                    _MAATOUCH_PUSHED_SERIALS.add(serial)
                    logger.debug(f"[maatouch] {serial} jar 已存在 size={local_size}")
                    return True
                # push
                r = subprocess.run(
                    [adb_path, "-s", serial, "push", str(local), _MAATOUCH_REMOTE_PATH],
                    capture_output=True, timeout=15, creationflags=_SUBPROCESS_FLAGS,
                )
                if r.returncode != 0:
                    logger.warning(f"[maatouch] {serial} push fail: {r.stderr.decode('utf-8','replace')[:200]}")
                    return False
                subprocess.run(
                    [adb_path, "-s", serial, "shell", f"chmod 755 {_MAATOUCH_REMOTE_PATH}"],
                    capture_output=True, timeout=5, creationflags=_SUBPROCESS_FLAGS,
                )
                _MAATOUCH_PUSHED_SERIALS.add(serial)
                logger.info(f"[maatouch] {serial} pushed jar size={local_size}")
                return True
            except Exception as e:
                logger.warning(f"[maatouch] {serial} push 异常: {e}")
                return False

    def _ensure_proc(self) -> bool:
        """确保 maatouch process 在跑. 死了自动重启. 返回 True = ready."""
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return True
            # push jar 幂等
            if not MaaTouchController.push_jar(self.adb_path, self.serial):
                self._consecutive_fails += 1
                return False
            try:
                self._proc = subprocess.Popen(
                    [self.adb_path, "-s", self.serial, "shell",
                     f"CLASSPATH={_MAATOUCH_REMOTE_PATH} app_process / com.shxyke.MaaTouch.App"],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    bufsize=0, creationflags=_SUBPROCESS_FLAGS,
                )
            except Exception as e:
                logger.warning(f"[maatouch] {self.serial} Popen 失败: {e}")
                self._consecutive_fails += 1
                return False

            # 异步读 banner (不阻塞 tap)
            def _drain_banner():
                try:
                    for _ in range(3):
                        line = self._proc.stdout.readline() if self._proc else b""
                        if not line: break
                        s = line.decode('utf-8', 'replace').strip()
                        if s.startswith("^"):
                            self._banner = s
                            logger.info(f"[maatouch] {self.serial} banner: {s}")
                except Exception:
                    pass
            threading.Thread(target=_drain_banner, daemon=True).start()
            # 等 0.5s 让 process 起来 + 读 banner
            time.sleep(0.5)
            if self._proc.poll() is not None:
                err = b""
                try: err = self._proc.stderr.read(500) or b""
                except Exception: pass
                logger.warning(
                    f"[maatouch] {self.serial} 启动后立刻死 rc={self._proc.returncode} "
                    f"stderr={err.decode('utf-8','replace')[:300]}"
                )
                self._proc = None
                self._consecutive_fails += 1
                return False
            self._consecutive_fails = 0
            return True

    def tap(self, x: int, y: int) -> bool:
        """发 tap. 成功 True, 失败 False (调用方 fallback subprocess)."""
        if self._consecutive_fails > 5:
            return False  # 连续失败太多, 别再试了
        if not self._ensure_proc():
            return False
        try:
            cmd = f"d 0 {int(x)} {int(y)} 50\nc\nu 0\nc\n".encode()
            self._proc.stdin.write(cmd)
            self._proc.stdin.flush()
            return True
        except (BrokenPipeError, OSError, ValueError) as e:
            logger.warning(f"[maatouch] {self.serial} stdin write fail: {e}")
            with self._lock:
                if self._proc is not None:
                    try: self._proc.kill()
                    except Exception: pass
                    self._proc = None
            self._consecutive_fails += 1
            return False

    def close(self):
        with self._lock:
            if self._proc is not None:
                try:
                    self._proc.stdin.close()
                    self._proc.wait(timeout=2)
                except Exception:
                    try: self._proc.kill()
                    except Exception: pass
                self._proc = None


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
        # 实测: am start 启游戏 + system_server 响应可达 3-5s, 太激进的 timeout 会误杀.
        # 截图 (raw screencap) 在 6 实例并发时 1s+ 也常见.
        # 取中庸 8s: 失败检测够早, 慢命令也容得下.
        self._proc_timeout = 8
        self._stream: Optional[ScreenrecordStream] = None
        # Android display 尺寸 (来自 `wm size`). 用来把 dxhook 抓的 GL framebuffer
        # (可能是 LDPlayer 渲染缩放后的 606x341) resize 回 Android 显示空间 (960x540).
        # 否则下游用 shot 坐标 tap, ADB tap 期望的是 Android display 坐标 → tap 落错位置.
        # lazy: 第一次 screenshot 时查并缓存.
        self._device_w: Optional[int] = None
        self._device_h: Optional[int] = None
        # MaaTouch (持久 process tap, 实测端到端 ~25ms vs subprocess 125-185ms)
        # env GAMEBOT_USE_MAATOUCH=0 一键回退 subprocess
        self._maatouch = MaaTouchController(self.adb_path, self.serial)

    def setup_minicap(self) -> bool:
        """[向后兼容名] 初始化截图流。

        backend = GAMEBOT_CAPTURE 环境变量:
        - screenrecord: adb shell screenrecord 长流 (注: 6 实例并发可能崩游戏)
        - 其他/未设: 走 raw adb screencap 兜底 (默认稳定但慢 ~400ms)

        历史遗物 (已删除): dxhook / wgc — 窗口缩小/隐藏时崩, 项目废弃.

        **幂等**: 已经 setup 过 (self._stream != None) 直接返回 True.
        """
        if self._stream is not None:
            return True
        backend = os.environ.get("GAMEBOT_CAPTURE", "").lower()

        if backend == "screenrecord":
            logger.warning(f"[capture] {self.serial} screenrecord: 6 实例并发可能崩游戏")
            stream = ScreenrecordStream(self.adb_path, self.serial)
            if stream.setup():
                self._stream = stream
                return True
            return False

        # 默认: raw adb screencap (调用方直接走 _raw_screencap, 不创建 stream)
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

    def _ensure_device_size(self) -> tuple[int, int]:
        """查 Android display 尺寸 (`wm size`) 并缓存. 返回 (w, h).
        失败兜底 (960, 540) — LDPlayer 9 绝大多数实例就是这个."""
        if self._device_w and self._device_h:
            return self._device_w, self._device_h
        try:
            out = self._cmd("shell", "wm size") or ""
            # "Physical size: 960x540" 或 "Override size: ..."
            import re
            m = re.search(r'(\d+)\s*x\s*(\d+)', out)
            if m:
                self._device_w = int(m.group(1))
                self._device_h = int(m.group(2))
                logger.info(f"[adb] {self.serial} device size = {self._device_w}x{self._device_h}")
            else:
                self._device_w, self._device_h = 960, 540
                logger.warning(f"[adb] {self.serial} wm size 解析失败, fallback 960x540: {out[:80]!r}")
        except Exception as e:
            self._device_w, self._device_h = 960, 540
            logger.warning(f"[adb] {self.serial} wm size 查询失败 ({e}), fallback 960x540")
        return self._device_w, self._device_h

    def _normalize_to_device(self, frame: np.ndarray) -> np.ndarray:
        """如果 frame 尺寸 != Android display 尺寸, resize 到 device 尺寸.
        修 dxhook 抓 GL framebuffer 比 Android display 小 (606x341 vs 960x540) 导致
        下游坐标系跟 tap 期望的不一致的 bug."""
        if frame is None:
            return frame
        dw, dh = self._ensure_device_size()
        h, w = frame.shape[:2]
        if w == dw and h == dh:
            return frame
        return cv2.resize(frame, (dw, dh))

    async def screenshot(self) -> Optional[np.ndarray]:
        """截图 — 优先级: ldopengl > screenrecord (显式) > raw screencap.

        ldopengl: Win + 雷电 9.0.78+ 时直读 host framebuffer, 6 并发 avg 2.8ms (vs adb 205ms).
        screenrecord: 仅 GAMEBOT_CAPTURE=screenrecord 时走 stream.
        raw screencap: 兜底 (~80ms 单实例, 6 并发 200ms+).

        所有路径返回的 frame 都 normalize 到 Android display 尺寸 (`wm size`)."""
        capture_env = os.environ.get("GAMEBOT_CAPTURE", "").lower()
        use_stream = self._stream is not None and capture_env == "screenrecord"

        with metrics.timed("screenshot") as tags:
            # ── 优先 ldopengl (除非显式选别的 backend) ──
            if capture_env != "screenrecord" and capture_env != "adb":
                mgr = LdopenglManager.get()
                if mgr.is_available():
                    loop = asyncio.get_event_loop()
                    frame = await loop.run_in_executor(None, mgr.capture, self.serial)
                    if frame is not None:
                        tags["backend"] = "ldopengl"
                        frame = self._normalize_to_device(frame)
                        tags["h"], tags["w"] = frame.shape[:2]
                        return frame
                    # ldopengl 失败 → 继续 fallback (实例没启动 / disabled / 异常)

            # ── screenrecord stream (显式开启时) ──
            if use_stream:
                frame = self._stream.get_frame()
                stream_name = type(self._stream).__name__.replace("Stream", "").lower()
                if frame is not None:
                    tags["backend"] = stream_name
                    frame = self._normalize_to_device(frame)
                    tags["h"], tags["w"] = frame.shape[:2]
                    return frame
                await asyncio.sleep(0.2)
                frame = self._stream.get_frame()
                if frame is not None:
                    tags["backend"] = f"{stream_name}_retry"
                    frame = self._normalize_to_device(frame)
                    tags["h"], tags["w"] = frame.shape[:2]
                    return frame

            # ── 兜底: raw screencap ──
            loop = asyncio.get_event_loop()
            try:
                frame = await loop.run_in_executor(None, self._screenshot_sync)
                tags["backend"] = "screencap_raw"
                if frame is not None:
                    tags["h"], tags["w"] = frame.shape[:2]
                return frame
            except Exception as e:
                logger.error(f"截图失败: {e}")
                tags["backend"] = "error"
                return None

    def _screenshot_sync(self) -> Optional[np.ndarray]:
        """raw screencap: `adb exec-out screencap` (无 -p) 拿 12/16 字节 header + raw RGBA.
        ~80ms (vs PNG screencap 300-500ms). UE4 兼容, 跟 LDPlayer 窗口大小完全无关."""
        t0 = time.perf_counter()
        cmd = [self.adb_path, "-s", self.serial, "exec-out", "screencap"]
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=10,
                                    creationflags=_SUBPROCESS_FLAGS)
        except Exception as e:
            logger.error(f"raw screencap 失败 {self.serial}: {e}")
            return None
        if result.returncode != 0 or len(result.stdout) < 16:
            return None
        raw = result.stdout

        # screencap raw header: Android 9+ 是 16 字节 (w/h/fmt/colorSpace), 老版本 12 字节.
        # 像素 format=1 是 RGBA_8888 (4 字节/像素), w*h*4 + header_size 应 = 总长度.
        try:
            w16, h16, fmt16, _cs = struct.unpack("<IIII", raw[:16])
            if fmt16 == 1 and len(raw) - 16 == w16 * h16 * 4:
                w, h, pixel_off = w16, h16, 16
            else:
                w12, h12, fmt12 = struct.unpack("<III", raw[:12])
                if fmt12 != 1 or len(raw) - 12 != w12 * h12 * 4:
                    logger.warning(
                        f"raw screencap 头部不识别 {self.serial}: "
                        f"16B(w={w16} h={h16} fmt={fmt16}) "
                        f"12B(w={w12} h={h12} fmt={fmt12}) bytes={len(raw)}"
                    )
                    return None
                w, h, pixel_off = w12, h12, 12
        except Exception as e:
            logger.error(f"raw screencap 头部解析失败 {self.serial}: {e}")
            return None

        try:
            arr = np.frombuffer(raw, dtype=np.uint8, offset=pixel_off).reshape((h, w, 4))
            img = cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
        except Exception as e:
            logger.error(f"raw screencap 像素解析失败 {self.serial}: w={w} h={h} bytes={len(raw)} err={e}")
            return None

        elapsed = (time.perf_counter() - t0) * 1000
        if elapsed > 200:
            logger.warning(f"[性能] raw screencap 慢: {elapsed:.0f}ms ({w}×{h})")
        return img

    async def tap(self, x: int, y: int):
        """点击 (带随机抖动).

        优先级:
          1. MaaTouch 持久 process (env GAMEBOT_USE_MAATOUCH=1 默认开)
             实测 stdin write+flush 0.02ms, 端到端 ~25ms
          2. 持久 adb shell (env GAMEBOT_TAP_PERSISTENT=1, 默认关 — 之前撞 deadlock 撤回)
          3. subprocess.run input tap (fallback, 80-150ms 单, 6 并发 185ms 排队)
        """
        # 用户要求固定点击不抖动 (之前抖动 ±3px 偶尔点错)
        jx = int(x)
        jy = int(y)
        with metrics.timed("tap", x=jx, y=jy):
            # 1) MaaTouch (默认开). stdin write+flush ~0.02ms 不阻塞 event loop,
            # 不走 to_thread (避免 default executor 排队 100-500ms)
            if os.environ.get("GAMEBOT_USE_MAATOUCH", "1") != "0":
                if self._maatouch.tap(jx, jy):
                    return

            # 2) 持久 shell (实测有 deadlock, 默认关)
            if os.environ.get("GAMEBOT_TAP_PERSISTENT", "0") == "1":
                if await self._tap_via_persistent_shell(jx, jy):
                    return

            # 3) fallback: fork-per-tap
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, self._cmd, "shell", f"input tap {jx} {jy}"
            )

    async def _tap_via_persistent_shell(self, x: int, y: int) -> bool:
        """通过持久 adb shell stdin 发 tap. 成功返回 True, 失败返回 False (调用方 fallback)."""
        proc = getattr(self, "_persistent_shell", None)
        if proc is None or proc.returncode is not None:
            try:
                self._persistent_shell = await asyncio.create_subprocess_exec(
                    self.adb_path, "-s", self.serial, "shell",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    creationflags=_SUBPROCESS_FLAGS,
                )
                proc = self._persistent_shell
            except Exception as e:
                logger.warning(f"[adb] {self.serial} 启 persistent shell 失败: {e}")
                return False
        try:
            proc.stdin.write(f"input tap {x} {y}\n".encode())
            await proc.stdin.drain()
            return True
        except Exception as e:
            logger.debug(f"[adb] {self.serial} persistent shell 写失败 ({e}), reset")
            try: proc.terminate()
            except Exception: pass
            self._persistent_shell = None
            return False

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

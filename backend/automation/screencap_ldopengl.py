"""
ldopengl 直读 LDPlayer host framebuffer — 替代 adb screencap 主路径.

实测 (2026-05-10, Windows agent + 6 实例并发):
  - ldopengl: 每实例 avg 2.8ms / p99 3.8ms / 0 错误, 总 2141 FPS
  - adb screencap: 每实例 avg 205ms (并发劣化 49%), 总 30 FPS
  → 加速 73x

工作原理:
  Game (UE4) → OpenGL ES → Android guest → LDPlayer host OpenGL renderer → Win 窗口
                                                          ↑
                                          ldopengl64.dll 在这层抓 host 端 framebuffer
  → 不受 UE4 自渲染影响 (跟 minicap 不同, minicap 在 guest 端 hook SurfaceFlinger)

适用条件 (硬性):
  - Windows
  - LDPlayer 9 ≥ 9.0.78 (ldopengl64.dll 才存在)
  - 实例 sysboot=1 (运行中)

设计:
  - LdopenglManager 是 process 内单例 (默认全局)
  - 每实例一个 LdopenglClient + threading.Lock (COM 不跨线程)
  - playerpid 变化自动 reinit (实例重启场景)
  - lazy: 第一次调用时探测, 失败 cache "不可用", 后续直接 skip → fallback adb

借鉴: Alas (https://github.com/LmeSzinc/AzurLaneAutoScript) module/device/method/ldopengl.py
"""

from __future__ import annotations

import ctypes
import logging
import os
import platform
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# 默认 LDPlayer 9 安装位置 (按命中频率排序)
_DEFAULT_LD_DIRS = [
    r"D:\leidian\LDPlayer9",
    r"C:\leidian\LDPlayer9",
    r"D:\Program Files\LDPlayer\LDPlayer9",
    r"C:\Program Files\LDPlayer\LDPlayer9",
    r"E:\LDPlayer\LDPlayer9",
    r"D:\LDPlayer9",
]

_SUBPROCESS_FLAGS = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0


@dataclass
class _InstInfo:
    index: int
    sysboot: int  # 1=running
    player_pid: int
    width: int
    height: int


def _find_ld_dir() -> Optional[str]:
    """找 LDPlayer 9 安装目录, 必须含 ldconsole.exe + ldopengl64.dll."""
    env = os.environ.get("LDPLAYER_PATH")
    if env and _is_valid(env):
        return env
    for p in _DEFAULT_LD_DIRS:
        if _is_valid(p):
            return p
    return None


def _is_valid(p: str) -> bool:
    return (
        os.path.isdir(p)
        and os.path.isfile(os.path.join(p, "ldconsole.exe"))
        and os.path.isfile(os.path.join(p, "ldopengl64.dll"))
    )


def _serial_to_idx(serial: str) -> Optional[int]:
    """5555 → 0, 5557 → 1, 5559 → 2, ...
    127.0.0.1:5557 / emulator-5556 都支持."""
    m = re.search(r":(\d+)$", serial) or re.search(r"-(\d+)$", serial)
    if not m:
        return None
    port = int(m.group(1))
    if port % 2 == 1:  # adb 控制端口是奇数 (5555/5557), emulator-N 的 N 是偶数 (5554)
        port -= 1
    if 5554 <= port <= 5554 + 64:
        return (port - 5554) // 2
    return None


def _list_running_instances(ld_dir: str) -> list[_InstInfo]:
    """ldconsole list2 → 解析每行 10 列, 只返回运行中的."""
    try:
        out = subprocess.check_output(
            [os.path.join(ld_dir, "ldconsole.exe"), "list2"],
            stderr=subprocess.DEVNULL,
            timeout=5,
            creationflags=_SUBPROCESS_FLAGS,
        )
    except Exception as e:
        logger.warning(f"[ldopengl] ldconsole list2 失败: {e}")
        return []
    text = None
    for enc in ("utf-8", "gbk"):
        try:
            text = out.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        return []
    insts = []
    for line in text.strip().splitlines():
        parts = line.strip().split(",")
        if len(parts) != 10:
            continue
        try:
            insts.append(_InstInfo(
                index=int(parts[0]),
                sysboot=int(parts[4]),
                player_pid=int(parts[5]),
                width=int(parts[7]),
                height=int(parts[8]),
            ))
        except ValueError:
            continue
    return [i for i in insts if i.sysboot == 1]


# ─── COM virtual function table 包装 (照搬 Alas) ───────────────────

class _IScreenShotClass:
    """LDPlayer ldopengl IScreenShotClass 的 ctypes 包装.
    必须传 c_void_p 实例 (不能取 .value), COM virtual call 需要 this 指针."""

    def __init__(self, ptr: ctypes.c_void_p):
        self._ptr = ptr  # 保留 c_void_p 实例
        cap_t = ctypes.WINFUNCTYPE(ctypes.c_void_p)
        rel_t = ctypes.WINFUNCTYPE(None)
        self._cap = cap_t(1, "IScreenShotClass_Cap")
        self._release = rel_t(2, "IScreenShotClass_Release")

    def cap(self) -> int:
        return self._cap(self._ptr)

    def release(self):
        try:
            self._release(self._ptr)
        except Exception:
            pass

    def __del__(self):
        self.release()


# ─── per-instance client ───────────────────────────────────────────

class LdopenglClient:
    """单实例的 ldopengl 截图 client. 不跨线程: 每次 capture 必须持锁."""

    def __init__(self, ld_dir: str, inst: _InstInfo, lib: ctypes.WinDLL):
        self._lib = lib
        self.inst = inst
        self.lock = threading.Lock()
        ptr = ctypes.c_void_p(
            lib.CreateScreenShotInstance(inst.index, inst.player_pid)
        )
        if not ptr.value:
            raise RuntimeError(f"CreateScreenShotInstance 返回 NULL (idx={inst.index} pid={inst.player_pid})")
        self._shot = _IScreenShotClass(ptr)

    def capture(self) -> Optional[np.ndarray]:
        """抓帧. 返回 BGR ndarray (已 vertical flip), 失败返回 None."""
        w, h = self.inst.width, self.inst.height
        img_ptr = self._shot.cap()
        if not img_ptr:
            return None
        try:
            buf = ctypes.cast(
                img_ptr,
                ctypes.POINTER(ctypes.c_ubyte * (h * w * 3)),
            ).contents
            arr = np.ctypeslib.as_array(buf).reshape((h, w, 3))
            # ldopengl pointer 是 y 朝上, 翻转一次 (新 ndarray, 跟 ldopengl 内存解耦)
            arr = cv2.flip(arr, 0)
            # 强制 contiguous + Python heap copy: 下游 OCR/YOLO/ONNX 拿到的是稳定 buffer,
            # 不会因 ldopengl 下次 cap() 覆盖原 framebuffer 而悬挂
            return np.ascontiguousarray(arr).copy()
        except Exception as e:
            logger.warning(f"[ldopengl] capture decode 异常 #{self.inst.index}: {e}")
            return None

    def close(self):
        self._shot.release()


# ─── manager 单例 ────────────────────────────────────────────────

class LdopenglManager:
    """进程内单例. 缓存每个 instance 的 client, 自动重连."""

    _instance: Optional["LdopenglManager"] = None
    _instance_lock = threading.Lock()

    @classmethod
    def get(cls) -> "LdopenglManager":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def __init__(self):
        self._clients_lock = threading.Lock()
        self._clients: dict[int, LdopenglClient] = {}  # index → client
        self._disabled: set[int] = set()  # init 失败的 instance, 不再尝试
        self._ld_dir: Optional[str] = None
        self._lib: Optional[ctypes.WinDLL] = None
        self._available = self._init_lib()

    def _init_lib(self) -> bool:
        """检测平台 + 加载 ldopengl64.dll. 失败整体禁用."""
        if not platform.system().startswith("Windows"):
            logger.info("[ldopengl] 非 Windows 平台, 禁用")
            return False
        if os.environ.get("GAMEBOT_DISABLE_LDOPENGL", "").lower() in ("1", "true", "yes"):
            logger.info("[ldopengl] GAMEBOT_DISABLE_LDOPENGL 环境变量禁用")
            return False
        ld_dir = _find_ld_dir()
        if not ld_dir:
            logger.info("[ldopengl] 找不到 LDPlayer 9 安装 (含 ldopengl64.dll), 整体回退到 adb")
            return False
        self._ld_dir = ld_dir
        try:
            self._lib = ctypes.WinDLL(os.path.join(ld_dir, "ldopengl64.dll"))
            self._lib.CreateScreenShotInstance.restype = ctypes.c_void_p
            logger.info(f"[ldopengl] 加载成功: {ld_dir}")
            return True
        except Exception as e:
            logger.warning(f"[ldopengl] dll 加载失败 ({ld_dir}): {e}")
            return False

    def is_available(self) -> bool:
        return self._available

    def capture(self, serial: str) -> Optional[np.ndarray]:
        """按 serial 抓帧. 返回 BGR ndarray; None 表示失败 → 调用方应 fallback adb."""
        if not self._available:
            return None
        idx = _serial_to_idx(serial)
        if idx is None:
            return None
        if idx in self._disabled:
            return None

        client = self._get_or_create_client(idx)
        if client is None:
            return None

        with client.lock:
            try:
                img = client.capture()
                if img is None:
                    # NULL 帧 → 实例可能重启了, 清掉重建一次
                    self._evict(idx)
                return img
            except Exception as e:
                logger.warning(f"[ldopengl] capture #{idx} 失败 ({e}), 标记重连")
                self._evict(idx)
                return None

    def _get_or_create_client(self, idx: int) -> Optional[LdopenglClient]:
        """已有 client 就返回; 否则现场探测 + 创建."""
        with self._clients_lock:
            existing = self._clients.get(idx)
            if existing is not None:
                return existing

        # 现场探测: list2 拿真实 pid + size
        running = _list_running_instances(self._ld_dir)
        target = next((i for i in running if i.index == idx), None)
        if target is None:
            # 实例没跑, 不算 disabled (用户可能后续启动); 但本次失败
            return None

        try:
            client = LdopenglClient(self._ld_dir, target, self._lib)
        except Exception as e:
            logger.warning(f"[ldopengl] init #{idx} 失败 ({e}), disable 该实例")
            with self._clients_lock:
                self._disabled.add(idx)
            return None

        with self._clients_lock:
            # double check (并发可能两个线程都进来)
            existing = self._clients.get(idx)
            if existing is not None:
                client.close()
                return existing
            self._clients[idx] = client
            logger.info(f"[ldopengl] init #{idx} 成功 (pid={target.player_pid}, {target.width}x{target.height})")
            return client

    def _evict(self, idx: int):
        """清掉指定 instance 的 client (实例重启 / capture 失败时调用)."""
        with self._clients_lock:
            client = self._clients.pop(idx, None)
        if client is not None:
            client.close()

    def reset(self, idx: Optional[int] = None):
        """外部强制 reset. idx=None 清全部, 否则清指定."""
        if idx is None:
            with self._clients_lock:
                clients = list(self._clients.values())
                self._clients.clear()
                self._disabled.clear()
            for c in clients:
                c.close()
            logger.info("[ldopengl] reset all")
        else:
            self._evict(idx)
            with self._clients_lock:
                self._disabled.discard(idx)
            logger.info(f"[ldopengl] reset #{idx}")

"""
Vision Daemon — push-driven 后台 perception.

设计目标 (2026-05-10):
- 业务侧不再 pull-driven 等 detection (1.3s perceive 阻塞)
- 后台持续 capture + 智能 inference, phase 只读 cache
- 端到端: 画面变化 → cache → phase tick → tap, < 500ms

三层架构:
  L1 Capture: 后台 thread, 10 fps 抓 6 实例 ldopengl, motion gate 跳静止帧
  L2 Inference: 单 ONNX session (无 GPU lock), 队列消费, 批/单帧推理
  L3 Cache: per-instance FrameSlot, 业务读 read-only, daemon 写

安全设计 (吸取 motion gate 教训):
- 纯 numpy dHash, 不调 cv2.absdiff (motion gate 当时崩在这)
- ldopengl frame 已经 .copy() 出 (screencap_ldopengl.py:199), daemon 不二次 view
- inference 单 worker thread + single ONNX session, 不需要任何 lock (业界 ORT 文档保证)
- watchdog 每 5s 检查 thread 死活, 死了自动重启

env:
- GAMEBOT_VISION_DAEMON=0  (默认关, 验证后开)
- GAMEBOT_VISION_FPS=8     (capture fps, 默认 8)
- GAMEBOT_VISION_MOTION_THRESH=4  (dHash hamming 阈值)
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Motion Gate (纯 numpy, 跟 P1 motion gate 同实现)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_DHASH_GRID_H = 8
_DHASH_GRID_W = 9
def _motion_threshold() -> int:
    """优先 runtime_profile, env 兜底."""
    try:
        from .runtime_profile import get_profile
        return get_profile().daemon_motion_threshold
    except Exception:
        return int(os.environ.get("GAMEBOT_VISION_MOTION_THRESH", "6"))


def _fast_dhash(shot: np.ndarray) -> int:
    """64-bit dHash, 纯 numpy. 完全不调 cv2."""
    a = np.ascontiguousarray(shot)
    h, w = a.shape[:2]
    gray = (a[..., 0].astype(np.float32) * 0.114 +
            a[..., 1].astype(np.float32) * 0.587 +
            a[..., 2].astype(np.float32) * 0.299)
    h_step = h // _DHASH_GRID_H
    w_step = w // _DHASH_GRID_W
    h_trim = h_step * _DHASH_GRID_H
    w_trim = w_step * _DHASH_GRID_W
    g = gray[:h_trim, :w_trim].reshape(_DHASH_GRID_H, h_step, _DHASH_GRID_W, w_step).mean(axis=(1, 3))
    diff = (g[:, :-1] > g[:, 1:]).astype(np.uint8)
    bits = np.packbits(diff.flatten()).tobytes()
    return int.from_bytes(bits, 'big')


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count('1')


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# FrameSlot — daemon 写, phase 读 (read-only)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass
class FrameSlot:
    """每实例 cache 的一帧. daemon 写, phase 只读."""
    inst_idx: int
    frame: np.ndarray                          # 已 .copy() 的 BGR ndarray
    phash: int
    t_acquired: float                          # frame 抓取时间 (perf_counter)
    yolo_dets: list = field(default_factory=list)  # YoloDismisser.Detection list
    t_inferred: float = 0.0                    # inference 完成时间, 0 = 未推理过

    def age_ms(self) -> float:
        """frame 拿到后到现在的 ms"""
        return (time.perf_counter() - self.t_acquired) * 1000


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Vision Daemon
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class VisionDaemon:
    """单进程后台 perception daemon. 管 6 实例的 capture + inference + cache.

    用法:
        daemon = VisionDaemon.get()
        daemon.start([0, 1, 2, 3, 4, 6])
        # 业务侧:
        slot = daemon.snapshot(inst_idx, max_age_ms=200)
        if slot is not None and slot.yolo_dets:
            # 见目标 → tap
        # tap 后 invalidate cache:
        daemon.invalidate(inst_idx)
    """

    _instance: Optional["VisionDaemon"] = None
    _instance_lock = threading.Lock()

    @classmethod
    def get(cls) -> "VisionDaemon":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def __init__(self):
        self._target_indices: list[int] = []
        # capture fps 优先 runtime_profile, env 兜底
        try:
            from .runtime_profile import get_profile
            self._capture_fps = float(get_profile().daemon_target_fps)
        except Exception:
            self._capture_fps = float(os.environ.get("GAMEBOT_VISION_FPS", "8"))
        self._stop_event = threading.Event()

        # cache (per-instance latest FrameSlot), daemon 写 phase 读
        self._cache_lock = threading.Lock()
        self._cache: dict[int, FrameSlot] = {}

        # invalidate flag — tap 后 phase 调 invalidate, 下一轮 capture 强制 inference
        self._invalidate: dict[int, bool] = {}

        # inference queue: (inst_idx, frame, phash, t_acquired)
        self._infer_queue: queue.Queue = queue.Queue(maxsize=24)

        # threads
        self._capture_thread: Optional[threading.Thread] = None
        self._inference_thread: Optional[threading.Thread] = None
        self._watchdog_thread: Optional[threading.Thread] = None

        # ONNX session (single, shared across all instances)
        self._session = None
        self._input_name: Optional[str] = None
        self._input_shape = (640, 640)

        # stats
        self._stats_lock = threading.Lock()
        self._capture_count = 0
        self._infer_count = 0
        self._motion_skip_count = 0
        self._errors = 0
        # snapshot hit/miss (业务 _run_yolo 调用 snapshot 时累加)
        self._snapshot_hit_count = 0
        self._snapshot_miss_count = 0
        self._snapshot_miss_reasons: dict = {}  # {"too_old": 12, "no_cache": 5, ...}
        self._capture_lat_samples: list[float] = []  # 最近 N 次 capture latency
        self._infer_lat_samples: list[float] = []
        self._started_at = 0.0

    # ─────────── ONNX session 加载 ───────────

    def _load_session(self) -> bool:
        """加载 1 个共享 ONNX session. 复用 yolo_dismisser 的模型路径."""
        if self._session is not None:
            return True
        try:
            from .yolo_dismisser import _model_path
            import onnxruntime as ort
            path = _model_path()
            if not path.is_file():
                logger.warning(f"[vision_daemon] 模型文件不存在 {path}")
                return False
            available = set(ort.get_available_providers())
            providers = []
            # 默认 CPU EP (single session, 真并发安全)
            # env GAMEBOT_VISION_USE_DML=1 → 仍用 DML (单 session 不会 race)
            use_dml = os.environ.get("GAMEBOT_VISION_USE_DML", "0") == "1"
            if use_dml and "DmlExecutionProvider" in available:
                providers.append("DmlExecutionProvider")
            providers.append("CPUExecutionProvider")

            so = ort.SessionOptions()
            # single session, intra=2 已够 (后台异步, 不抢业务 CPU)
            so.intra_op_num_threads = 2
            so.inter_op_num_threads = 1
            so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            # 减少空闲时 CPU spin (后台运行不能抢核)
            so.add_session_config_entry("session.intra_op.allow_spinning", "0")

            self._session = ort.InferenceSession(str(path), sess_options=so, providers=providers)
            self._input_name = self._session.get_inputs()[0].name
            shape = self._session.get_inputs()[0].shape
            self._input_shape = (int(shape[2]), int(shape[3]))
            logger.info(
                f"[vision_daemon] ONNX session 加载: providers={self._session.get_providers()} "
                f"input={self._input_shape}"
            )
            return True
        except Exception as e:
            logger.error(f"[vision_daemon] session 加载失败: {e}")
            return False

    def _yolo_infer(self, frame: np.ndarray):
        """跑一次 yolo. 复用 YoloDismisser 的 preprocess/postprocess."""
        if self._session is None:
            return []
        try:
            from .yolo_dismisser import YoloDismisser
            tensor, scale, pad = YoloDismisser._preprocess(frame, input_shape=self._input_shape)
            outputs = self._session.run(None, {self._input_name: tensor})
            h, w = frame.shape[:2]
            return YoloDismisser._postprocess(outputs[0], scale, pad, h, w)
        except Exception as e:
            logger.warning(f"[vision_daemon] yolo infer 异常: {e}")
            return []

    # ─────────── lifecycle ───────────

    def start(self, target_indices: list[int]) -> bool:
        if self._capture_thread is not None and self._capture_thread.is_alive():
            logger.info("[vision_daemon] 已在运行")
            return True
        if not self._load_session():
            logger.error("[vision_daemon] session 加载失败, 不启动")
            return False
        self._target_indices = list(target_indices)
        self._stop_event.clear()
        self._started_at = time.perf_counter()

        self._capture_thread = threading.Thread(
            target=self._capture_loop, daemon=True, name="vision_capture")
        self._inference_thread = threading.Thread(
            target=self._inference_loop, daemon=True, name="vision_infer")
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop, daemon=True, name="vision_watchdog")
        self._capture_thread.start()
        self._inference_thread.start()
        self._watchdog_thread.start()
        logger.info(
            f"[vision_daemon] 启动 (instances={target_indices} fps={self._capture_fps} "
            f"motion_thresh={_motion_threshold()})"
        )
        return True

    def stop(self):
        self._stop_event.set()
        for t in (self._capture_thread, self._inference_thread, self._watchdog_thread):
            if t is not None and t.is_alive():
                t.join(timeout=3)
        logger.info("[vision_daemon] 停止")

    # ─────────── 业务接口 ───────────

    def snapshot(self, inst_idx: int, max_age_ms: float = 200) -> Optional[FrameSlot]:
        """业务读 cache. 太老返回 None (业务 fallback 走原 perceive).
        返回的 FrameSlot 是引用, 业务不能改它 (read-only)."""
        with self._cache_lock:
            slot = self._cache.get(inst_idx)
        miss_reason = None
        if slot is None:
            miss_reason = f"no_cache_inst{inst_idx}"
        elif slot.age_ms() > max_age_ms:
            miss_reason = f"too_old({slot.age_ms():.0f}ms)"
        elif slot.t_inferred == 0:
            miss_reason = "not_inferred_yet"
        with self._stats_lock:
            if miss_reason:
                self._snapshot_miss_count += 1
                self._snapshot_miss_reasons[miss_reason] = self._snapshot_miss_reasons.get(miss_reason, 0) + 1
            else:
                self._snapshot_hit_count += 1
        return slot if miss_reason is None else None

    def invalidate(self, inst_idx: int):
        """tap 后调用. 让 daemon 下一轮 capture 强制 inference (绕 motion gate)."""
        self._invalidate[inst_idx] = True

    def stats(self) -> dict:
        with self._stats_lock:
            now = time.perf_counter()
            uptime = max(now - self._started_at, 0.001)
            cap_lats = list(self._capture_lat_samples)
            inf_lats = list(self._infer_lat_samples)
        with self._cache_lock:
            cache_ages = [s.age_ms() for s in self._cache.values()]
        snap_total = self._snapshot_hit_count + self._snapshot_miss_count
        return {
            "uptime_s": round(uptime, 1),
            "capture_count": self._capture_count,
            "infer_count": self._infer_count,
            "motion_skip_count": self._motion_skip_count,
            "errors": self._errors,
            "capture_fps": round(self._capture_count / uptime, 2),
            "infer_fps": round(self._infer_count / uptime, 2),
            "skip_rate": round(self._motion_skip_count / max(self._capture_count, 1), 3),
            "cache_n": len(self._cache),
            "cache_age_p50_ms": round(np.percentile(cache_ages, 50), 1) if cache_ages else 0,
            "cache_age_p99_ms": round(np.percentile(cache_ages, 99), 1) if cache_ages else 0,
            "infer_p50_ms": round(np.percentile(inf_lats, 50), 1) if inf_lats else 0,
            "infer_p99_ms": round(np.percentile(inf_lats, 99), 1) if inf_lats else 0,
            "capture_p50_ms": round(np.percentile(cap_lats, 50), 1) if cap_lats else 0,
            "queue_depth": self._infer_queue.qsize(),
            # snapshot (业务侧 cache hit/miss)
            "snapshot_total": snap_total,
            "snapshot_hit": self._snapshot_hit_count,
            "snapshot_miss": self._snapshot_miss_count,
            "snapshot_hit_rate": round(self._snapshot_hit_count / max(snap_total, 1), 3),
            "snapshot_miss_reasons": dict(self._snapshot_miss_reasons),
            "alive": {
                "capture": self._capture_thread.is_alive() if self._capture_thread else False,
                "inference": self._inference_thread.is_alive() if self._inference_thread else False,
                "watchdog": self._watchdog_thread.is_alive() if self._watchdog_thread else False,
            }
        }

    # ─────────── capture loop ───────────

    def _capture_loop(self):
        """后台 thread, 10 fps 抓所有实例, motion gate 决定是否入 inference queue"""
        from .screencap_ldopengl import LdopenglManager
        mgr = LdopenglManager.get()
        if not mgr.is_available():
            logger.error("[vision_daemon] ldopengl 不可用, capture loop 退出")
            return

        interval = 1.0 / self._capture_fps
        last_phashes: dict[int, int] = {}

        while not self._stop_event.is_set():
            t_round = time.perf_counter()
            for inst_idx in self._target_indices:
                if self._stop_event.is_set():
                    break
                t_cap_start = time.perf_counter()
                serial = f"127.0.0.1:{5555 + inst_idx * 2}"
                try:
                    frame = mgr.capture(serial)
                except Exception as e:
                    self._errors += 1
                    logger.warning(f"[vision_daemon] capture #{inst_idx} 异常: {e}")
                    continue
                if frame is None:
                    continue

                cap_lat = (time.perf_counter() - t_cap_start) * 1000
                with self._stats_lock:
                    self._capture_count += 1
                    self._capture_lat_samples.append(cap_lat)
                    if len(self._capture_lat_samples) > 200:
                        self._capture_lat_samples.pop(0)

                # motion gate
                try:
                    cur_ph = _fast_dhash(frame)
                except Exception as e:
                    logger.debug(f"[vision_daemon] dhash 异常 #{inst_idx}: {e}")
                    cur_ph = 0

                last_ph = last_phashes.get(inst_idx)
                force = self._invalidate.pop(inst_idx, False)
                if last_ph is not None and not force and _hamming(cur_ph, last_ph) <= _motion_threshold():
                    # 画面没变, 跳 inference, 但更新 cache 的 t_acquired (不算"过期")
                    with self._cache_lock:
                        old = self._cache.get(inst_idx)
                        if old is not None:
                            old.t_acquired = time.perf_counter()
                            old.frame = frame  # 更新 frame 指针 (但 dets 还是旧的)
                    self._motion_skip_count += 1
                    continue

                last_phashes[inst_idx] = cur_ph
                # 入 inference queue (满了就丢旧的, 防积压)
                try:
                    self._infer_queue.put_nowait((inst_idx, frame, cur_ph, time.perf_counter()))
                except queue.Full:
                    # 满了 → 丢一个最老的, 再 put
                    try:
                        self._infer_queue.get_nowait()
                        self._infer_queue.put_nowait((inst_idx, frame, cur_ph, time.perf_counter()))
                    except Exception:
                        pass

            # 节流到目标 fps
            elapsed = time.perf_counter() - t_round
            sleep_s = max(0, interval - elapsed)
            if sleep_s > 0:
                self._stop_event.wait(sleep_s)

    # ─────────── inference loop ───────────

    def _inference_loop(self):
        """后台 thread, 单 ONNX session 串行消费 inference queue"""
        while not self._stop_event.is_set():
            try:
                item = self._infer_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            inst_idx, frame, phash, t_acq = item

            t_infer_start = time.perf_counter()
            try:
                dets = self._yolo_infer(frame)
            except Exception as e:
                self._errors += 1
                logger.warning(f"[vision_daemon] inference #{inst_idx} 异常: {e}")
                dets = []

            infer_lat = (time.perf_counter() - t_infer_start) * 1000
            t_inferred = time.perf_counter()

            with self._stats_lock:
                self._infer_count += 1
                self._infer_lat_samples.append(infer_lat)
                if len(self._infer_lat_samples) > 200:
                    self._infer_lat_samples.pop(0)

            with self._cache_lock:
                self._cache[inst_idx] = FrameSlot(
                    inst_idx=inst_idx,
                    frame=frame,
                    phash=phash,
                    t_acquired=t_acq,
                    yolo_dets=dets,
                    t_inferred=t_inferred,
                )

    # ─────────── watchdog ───────────

    def _watchdog_loop(self):
        """每 5s 检查 capture/inference thread 死活, 死了重启"""
        while not self._stop_event.is_set():
            self._stop_event.wait(5.0)
            if self._stop_event.is_set():
                break
            if self._capture_thread is not None and not self._capture_thread.is_alive():
                logger.error("[vision_daemon] capture thread 死了, 重启")
                self._capture_thread = threading.Thread(
                    target=self._capture_loop, daemon=True, name="vision_capture")
                self._capture_thread.start()
            if self._inference_thread is not None and not self._inference_thread.is_alive():
                logger.error("[vision_daemon] inference thread 死了, 重启")
                self._inference_thread = threading.Thread(
                    target=self._inference_loop, daemon=True, name="vision_infer")
                self._inference_thread.start()

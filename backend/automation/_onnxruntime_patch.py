"""onnxruntime + 中文 Windows 的 utf-8 兼容补丁.

背景:
  onnxruntime 在 Windows 上 C++ 端发出诊断/错误字符串时, 用 ACP 编码
  (中文系统 = GBK / cp936). pybind11 binding 尝试以 utf-8 strict 解码 →
  UnicodeDecodeError, 真正的 C++ 错误反而被遮蔽, 还会拖死整个 worker.

  这个 bug 触发链:
    1. DML provider 在虚拟显卡 / 个别输入上抛 C++ 异常 (或写诊断 log)
    2. 异常 / log 文本含非 utf-8 字节 (GBK)
    3. Python 端 self._sess.run() 或 set logger 时解码炸
    4. 异步 worker 死, asyncio 主循环卡住 → backend 整个挂

修复思路:
  1. 把 onnxruntime 默认 logger 等级抬到 WARNING, 减少触发面
  2. monkey-patch InferenceSession.run() — 抓 UnicodeDecodeError, 重试 1 次
     (大多数 DML 毛刺是瞬时的); 还失败就转成可读的 RuntimeError, 让上游
     可以 except 接住, 不再让原 traceback 把整个 thread 拖死

副作用:
  patch 放在 import 时执行, 全局生效, 不影响 ONNX 正常推理路径.
"""
from __future__ import annotations

import logging
import sys
import threading
import time

logger = logging.getLogger(__name__)

_PATCHED = False

# 同一 InferenceSession 多线程并发 run 在某些 EP (DML 共享 D3D12 / CPU 混用) 上会
# 在 C 层段错 — faulthandler 能 dump 出栈, Python 没 traceback. 实测崩点:
#   onnxruntime_inference_collection.py:321 → _safe_run → yolo_dismisser._infer
#   3 instance 并发跑 P2 perception → ThreadPoolExecutor 同时进 session.run → SIGSEGV
# 修复: 每个 session 挂一把 threading.Lock, 同 session 的 run 串行执行, 跨 session 仍并发.
# Lock 直接挂 session 实例 (不能 weakref _thread.lock), 不阻止 session GC.
_SESS_LOCK_ATTR = "_gb_run_lock"
_SESS_LOCKS_GUARD = threading.Lock()

# 2026-05-10 新增: 全局跨-session GPU lock. 只锁 GPU EP (DML/CUDA), CPU EP 不锁.
# 触发: yolo_dismisser v2-9 每实例 1 个 session, 6 实例并发 6 个 DML session
# 共享同一 D3D12 device, run() 时 device 状态 race → access violation.
# 实测崩点 (2026-05-10): 6 实例并发 + 数据/档案页拉缩略图制造 IO 抖动 → 必崩.
# Per-session lock 拦不住, 必须跨 session lock.
# 副作用: GPU YOLO 推理变成串行 (单次 ~30ms * 6 实例 = ~180ms/轮), 业务 1-2 Hz 够用.
_GPU_RUN_LOCK = threading.Lock()
_GPU_PROVIDERS = {"DmlExecutionProvider", "CUDAExecutionProvider"}


def _lock_for(session) -> threading.Lock:
    lk = getattr(session, _SESS_LOCK_ATTR, None)
    if lk is None:
        with _SESS_LOCKS_GUARD:
            lk = getattr(session, _SESS_LOCK_ATTR, None)
            if lk is None:
                lk = threading.Lock()
                try:
                    setattr(session, _SESS_LOCK_ATTR, lk)
                except (AttributeError, TypeError):
                    # InferenceSession 是 pybind 类, 个别版本禁止动态属性 — 退到 dict
                    _FALLBACK_LOCKS[id(session)] = lk
    return lk


_FALLBACK_LOCKS: "dict[int, threading.Lock]" = {}


class _NullCtx:
    """空 context manager — CPU EP 跳过 GPU lock 时用"""
    def __enter__(self): return None
    def __exit__(self, *a): return False


_NULL_CTX = _NullCtx()


def apply() -> None:
    """幂等应用 patch. 失败不影响主流程, 只 warn."""
    global _PATCHED
    if _PATCHED:
        return
    if sys.platform != "win32":
        _PATCHED = True
        return
    try:
        import onnxruntime as ort
    except Exception as e:
        logger.debug(f"[ort patch] 跳过 (onnxruntime 不可用): {e}")
        _PATCHED = True
        return

    # 1) 降低默认 logger severity (WARNING+), 减少 C++ 端写出 GBK 字符的机会
    try:
        ort.set_default_logger_severity(3)   # 0=verbose 1=info 2=warning 3=error 4=fatal
    except Exception as e:
        logger.debug(f"[ort patch] set_default_logger_severity 失败: {e}")

    # 2) monkey-patch InferenceSession.run — 容错 UnicodeDecodeError
    try:
        _orig_run = ort.InferenceSession.run

        def _safe_run(self, output_names, input_feed, run_options=None):
            # 1) 同 session 串行 (防同 session 多 thread C 层 race)
            # 2) GPU EP 全局 lock (防跨 session DML/CUDA D3D12/CUDA context race → SIGSEGV)
            #    用 cached attr 避免每次 run 都查 providers (开销微但累积)
            providers_cached = getattr(self, "_gb_providers_cached", None)
            if providers_cached is None:
                try:
                    providers_cached = set(self.get_providers())
                except Exception:
                    providers_cached = set()
                try:
                    setattr(self, "_gb_providers_cached", providers_cached)
                except (AttributeError, TypeError):
                    pass
            need_gpu_lock = bool(providers_cached & _GPU_PROVIDERS)

            lk = _lock_for(self)
            with lk:
                # 跨 session GPU lock (CPU EP 跳过, 不影响 CPU 并发)
                gpu_ctx = _GPU_RUN_LOCK if need_gpu_lock else _NULL_CTX
                with gpu_ctx:
                    try:
                        return _orig_run(self, output_names, input_feed, run_options)
                    except UnicodeDecodeError as e:
                        logger.warning(
                            f"[ort] UnicodeDecodeError (C++ msg GBK encoded): {e}; 重试 1 次"
                        )
                        time.sleep(0.03)
                        try:
                            return _orig_run(self, output_names, input_feed, run_options)
                        except UnicodeDecodeError as e2:
                            raise RuntimeError(
                                f"onnxruntime DML 推理失败 (中文 Windows utf-8 解码 bug): "
                                f"{e2}. 建议检查 GPU 驱动 / 显存 / 输入图像."
                            ) from None

        ort.InferenceSession.run = _safe_run
        logger.debug("[ort patch] InferenceSession.run 已包 utf-8 容错 + per-session lock")
    except Exception as e:
        logger.warning(f"[ort patch] monkey-patch run() 失败 (不致命): {e}")

    _PATCHED = True

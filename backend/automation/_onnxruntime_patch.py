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
import time

logger = logging.getLogger(__name__)

_PATCHED = False


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
            try:
                return _orig_run(self, output_names, input_feed, run_options)
            except UnicodeDecodeError as e:
                # C++ 端抛了异常或写了诊断文本, 字符串是 GBK, pybind 解码炸.
                # 真因看不到, 但多数是瞬时驱动毛刺 → 重试 1 次.
                logger.warning(
                    f"[ort] UnicodeDecodeError (C++ msg GBK encoded): {e}; 重试 1 次"
                )
                time.sleep(0.03)
                try:
                    return _orig_run(self, output_names, input_feed, run_options)
                except UnicodeDecodeError as e2:
                    # 还是炸 → 抬成普通 RuntimeError, 调用方可以 except + fallback,
                    # 不再让 UnicodeDecodeError 沿 traceback 上行污染上游.
                    raise RuntimeError(
                        f"onnxruntime DML 推理失败 (中文 Windows utf-8 解码 bug): "
                        f"{e2}. 建议检查 GPU 驱动 / 显存 / 输入图像."
                    ) from None

        ort.InferenceSession.run = _safe_run
        logger.info("[ort patch] InferenceSession.run 已包 utf-8 容错")
    except Exception as e:
        logger.warning(f"[ort patch] monkey-patch run() 失败 (不致命): {e}")

    _PATCHED = True

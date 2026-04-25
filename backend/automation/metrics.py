"""
结构化性能日志 — Task 0.1
每个关键操作点加耗时打点，输出 JSONL，供量化分析用。

用法：
    from backend.automation import metrics

    # Context manager
    with metrics.timed("ocr_roi", roi="lobby_popup"):
        result = ocr(frame)

    # Decorator (sync or async)
    @metrics.timed_decorator("screenshot")
    async def screenshot(self):
        ...

    # Direct record
    metrics.record("phase", name="dismiss_popups", result="ok", dur_ms=4850)

输出：logs/<session_id>/metrics.jsonl
"""
from __future__ import annotations

import atexit
import contextlib
import contextvars
import json
import os
import queue
import threading
import time
from collections import deque
from functools import wraps
from typing import Any, Dict, List, Optional

# 实例标签（从 runner_service 设置，沿用现有的 contextvar 机制）
# 若导入失败（比如独立调用），fallback 到空
try:
    from backend.runner_service import _current_instance as _inst_var  # type: ignore
except Exception:
    _inst_var = contextvars.ContextVar("metrics_inst", default=None)


# ====================================================================
# 后台写入线程 + 队列（不阻塞主路径）
# ====================================================================

_queue: "queue.Queue[Optional[Dict[str, Any]]]" = queue.Queue(maxsize=10000)
_writer_thread: Optional[threading.Thread] = None
_log_path: Optional[str] = None
_enabled: bool = True

# In-memory ring buffer for /api/health 聚合（独立于文件写入，无需 configure 即可工作）
_RECENT_MAX = 10000
_recent: "deque[Dict[str, Any]]" = deque(maxlen=_RECENT_MAX)


def configure(log_path: str, enabled: bool = True) -> None:
    """由 runner_service 在会话启动时调用"""
    global _log_path, _enabled, _writer_thread
    _log_path = log_path
    _enabled = enabled
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    if _writer_thread is None or not _writer_thread.is_alive():
        _writer_thread = threading.Thread(target=_writer_loop, daemon=True, name="metrics-writer")
        _writer_thread.start()
    atexit.register(_shutdown)


def _writer_loop() -> None:
    """后台写入：从队列取记录，批量 flush 到磁盘"""
    buf = []
    last_flush = time.monotonic()
    while True:
        try:
            item = _queue.get(timeout=0.5)
        except queue.Empty:
            item = None
        if item is not None:
            buf.append(item)
        now = time.monotonic()
        if buf and (len(buf) >= 50 or now - last_flush > 0.5):
            _flush(buf)
            buf = []
            last_flush = now


def _flush(records: list) -> None:
    if not _log_path:
        return
    try:
        with open(_log_path, "a", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
    except Exception:
        pass  # never throw from metrics


def _shutdown() -> None:
    """进程退出时 flush 剩余"""
    drained = []
    while True:
        try:
            item = _queue.get_nowait()
        except queue.Empty:
            break
        if item is not None:
            drained.append(item)
    if drained:
        _flush(drained)


# ====================================================================
# 记录入口
# ====================================================================

def record(action: str, **kwargs: Any) -> None:
    """写一条记录。kwargs 任意自定义字段。
    始终推入 in-memory ring buffer（供 /api/health 用），文件写仅在 configure 后启用。
    """
    rec: Dict[str, Any] = {
        "ts": round(time.time(), 3),
        "action": action,
    }
    inst = _inst_var.get() if hasattr(_inst_var, "get") else None
    if inst is not None:
        rec["inst"] = inst
    rec.update(kwargs)
    # 始终塞 in-memory（用于 health 端点聚合，不依赖 configure 调用）
    _recent.append(rec)
    if not _enabled or _log_path is None:
        return
    try:
        _queue.put_nowait(rec)
    except queue.Full:
        pass  # drop if overwhelmed


# ====================================================================
# timed context manager
# ====================================================================

@contextlib.contextmanager
def timed(action: str, **tags: Any):
    """
    用法：
        with metrics.timed("ocr_roi", roi="lobby_popup"):
            result = ocr(frame)
    """
    t0 = time.perf_counter()
    extra: Dict[str, Any] = {}
    try:
        yield extra  # 调用方可以 with m.timed(...) as m: m['hit']=True
    finally:
        dur_ms = round((time.perf_counter() - t0) * 1000, 2)
        record(action, dur_ms=dur_ms, **{**tags, **extra})


# ====================================================================
# decorator
# ====================================================================

def timed_decorator(action: str, extract_tags=None):
    """
    同步/异步 函数计时装饰器。
    extract_tags(result, args, kwargs) → dict 可选，从返回值额外提标签。
    """
    def wrap(fn):
        if _is_async(fn):
            @wraps(fn)
            async def aw(*args, **kwargs):
                t0 = time.perf_counter()
                try:
                    res = await fn(*args, **kwargs)
                    tags = extract_tags(res, args, kwargs) if extract_tags else {}
                    return res
                finally:
                    record(action, dur_ms=round((time.perf_counter() - t0) * 1000, 2),
                           **(tags if "tags" in locals() else {}))
            return aw
        else:
            @wraps(fn)
            def sw(*args, **kwargs):
                t0 = time.perf_counter()
                try:
                    res = fn(*args, **kwargs)
                    tags = extract_tags(res, args, kwargs) if extract_tags else {}
                    return res
                finally:
                    record(action, dur_ms=round((time.perf_counter() - t0) * 1000, 2),
                           **(tags if "tags" in locals() else {}))
            return sw
    return wrap


def _is_async(fn) -> bool:
    import inspect
    return inspect.iscoroutinefunction(fn)


# ====================================================================
# 系统性能采样（CPU / 内存 / 线程 / 事件循环）
# ====================================================================

_system_sampler_thread: Optional[threading.Thread] = None
_system_sampler_stop = threading.Event()


def start_system_sampler(interval: float = 2.0) -> None:
    """后台线程每 interval 秒采样 process CPU / RSS / 线程数 + 全机 CPU / 内存。
    三开 exe 卡顿时，这条日志能看到 CPU 瓶颈 / 内存涨没涨 / 线程爆没爆。
    """
    global _system_sampler_thread
    if _system_sampler_thread is not None and _system_sampler_thread.is_alive():
        return
    _system_sampler_stop.clear()
    _system_sampler_thread = threading.Thread(
        target=_system_sampler_loop, args=(interval,),
        daemon=True, name="metrics-sys"
    )
    _system_sampler_thread.start()


def _system_sampler_loop(interval: float) -> None:
    try:
        import psutil
    except ImportError:
        record("sys_error", msg="psutil not installed")
        return
    proc = psutil.Process()
    proc.cpu_percent(None)  # 第一次调用返回 0，先 prime
    psutil.cpu_percent(None)
    while not _system_sampler_stop.wait(interval):
        try:
            proc_cpu = proc.cpu_percent(None)       # 进程 CPU %（可 > 100 跨核）
            mem_info = proc.memory_info()
            threads = proc.num_threads()
            sys_cpu = psutil.cpu_percent(None)      # 全机 CPU %
            sys_mem = psutil.virtual_memory()
            record("sys",
                   proc_cpu=round(proc_cpu, 1),
                   proc_rss_mb=round(mem_info.rss / (1024 * 1024), 1),
                   proc_threads=threads,
                   sys_cpu=round(sys_cpu, 1),
                   sys_mem_pct=round(sys_mem.percent, 1),
                   sys_mem_available_mb=round(sys_mem.available / (1024 * 1024), 0),
                   )
        except Exception as e:
            record("sys_error", msg=str(e)[:200])


# ====================================================================
# /api/health 用：in-memory 聚合
# ====================================================================

def _percentile(sorted_vals: List[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * p
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return float(sorted_vals[f])
    return float(sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f))


def _stats(durations: List[float]) -> Dict[str, Any]:
    if not durations:
        return {"count": 0}
    s = sorted(durations)
    return {
        "count": len(s),
        "min_ms": round(s[0], 1),
        "p50_ms": round(_percentile(s, 0.5), 1),
        "p95_ms": round(_percentile(s, 0.95), 1),
        "p99_ms": round(_percentile(s, 0.99), 1),
        "max_ms": round(s[-1], 1),
        "avg_ms": round(sum(s) / len(s), 1),
    }


def summary(window_seconds: Optional[float] = None) -> Dict[str, Any]:
    """聚合 in-memory 缓冲。

    Args:
        window_seconds: 只看最近 N 秒的记录；None=用全部 in-memory ring（max 10000 条）

    Returns:
        {
            "window_seconds": <int|None>,
            "total_records": <int>,
            "actions": {
                "screenshot":     {count, p50_ms, p95_ms, p99_ms, ...},
                "ocr_full":       {...},
                "ocr_roi":        {...},
                "tap":            {...},
                "template_match": {..., "hit_rate": 0.84},
                "phase":          {..., "by_name": {dismiss_popups: {...}, ...}},
                "loop_lag":       {...},  # lag_ms 不是 dur_ms，count 至少能看出有没有阻塞
            },
            "sys_latest": {proc_cpu, proc_rss_mb, proc_threads, sys_cpu, sys_mem_pct} | None
        }
    """
    now = time.time()
    cutoff = now - window_seconds if window_seconds else 0.0
    by_action_dur: Dict[str, List[float]] = {}
    by_action_raw: Dict[str, List[Dict[str, Any]]] = {}
    total = 0
    for r in list(_recent):  # 拷贝避免迭代时被改
        if r.get("ts", 0) < cutoff:
            continue
        total += 1
        a = r.get("action", "?")
        by_action_raw.setdefault(a, []).append(r)
        # dur_ms 是大多数 action 的耗时字段；loop_lag 用 lag_ms
        dur = r.get("dur_ms")
        if dur is None and a == "loop_lag":
            dur = r.get("lag_ms")
        if isinstance(dur, (int, float)):
            by_action_dur.setdefault(a, []).append(float(dur))

    actions: Dict[str, Any] = {}
    for action, durs in by_action_dur.items():
        actions[action] = _stats(durs)

    # phase: 按 name 子分组
    if "phase" in by_action_raw:
        by_name: Dict[str, List[float]] = {}
        for r in by_action_raw["phase"]:
            d = r.get("dur_ms")
            if isinstance(d, (int, float)):
                by_name.setdefault(r.get("name", "?"), []).append(float(d))
        actions.setdefault("phase", {})["by_name"] = {
            n: _stats(v) for n, v in by_name.items()
        }

    # template_match: 命中率
    if "template_match" in by_action_raw:
        recs = by_action_raw["template_match"]
        hits = sum(1 for r in recs if r.get("hit"))
        actions.setdefault("template_match", {})["hit_rate"] = (
            round(hits / len(recs), 3) if recs else 0.0
        )

    # sys 最近一条快照
    sys_latest = None
    if by_action_raw.get("sys"):
        latest = by_action_raw["sys"][-1]
        sys_latest = {
            k: latest.get(k) for k in
            ("proc_cpu", "proc_rss_mb", "proc_threads", "sys_cpu", "sys_mem_pct")
        }

    return {
        "window_seconds": window_seconds,
        "total_records": total,
        "actions": actions,
        "sys_latest": sys_latest,
    }


# ====================================================================
# 事件循环延迟
# ====================================================================

async def event_loop_lag_monitor(interval: float = 0.5, threshold_ms: float = 50) -> None:
    """
    事件循环延迟监控：每 interval 秒让出一次，测量真实延迟 vs 预期。
    如果 OCR 同步调用阻塞了 loop，这里 lag 会飙到几百毫秒。

    在 runner_service 启动后 asyncio.create_task 一次。
    """
    import asyncio
    while True:
        t0 = time.perf_counter()
        await asyncio.sleep(interval)
        lag_ms = round((time.perf_counter() - t0 - interval) * 1000, 1)
        if lag_ms >= threshold_ms:
            record("loop_lag", lag_ms=lag_ms, interval=interval)

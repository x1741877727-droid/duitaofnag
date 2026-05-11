# V2 代码风险隐患审查 (Day 1+2, 1204 行)

> 审查时间: 2026-05-11
> 审查范围: backend/automation_v2/ 所有文件
> 用户诉求: Day 3 前必须修完 CRITICAL 风险

---

## 风险矩阵

| ID | 风险 | 文件 | 影响 | 概率 | 等级 |
|---|---|---|---|---|---|
| **R-C1** | lock.acquire(timeout=5s) 超时丢数据 | decision_simple.py:113-118, decision_detailed.py:163-167 | 长跑 24h 丢 10-50 条决策 | 低 | **CRITICAL** |
| **R-C2** | ThreadPoolExecutor 永不 shutdown | decision_detailed.py:67-70 | fd/thread 泄漏, 重启堆积 | 高 | **CRITICAL** |
| **R-H1** | OCR _postprocess_det/_ctc_decode 永远返空 | ocr.py:192-200 | OCR 完全失效, P3/P4/P5 失败 | 100% | **HIGH** |
| **R-H2** | yolo warmup 无异常处理 | yolo.py:97-102 | 启动 crash 实例瘫痪 | 低 | **HIGH** |
| **R-H3** | 计数器 += 非原子 (多线程竞争) | decision_simple.py:115, detailed.py:165,194 | metric 偏差, 监控误判 | 中 | **HIGH** |
| **R-H4** | shutdown(wait=True) 无 timeout 卡 exit | decision_detailed.py:205 | graceful shutdown 变 force kill | 低 | **HIGH** |
| **R-M1** | asyncio.to_thread 排队竞争 (72 vs 32 workers) | yolo/ocr/matcher/tap.py | 延迟 +50-150ms | 100% | **MEDIUM** |
| **R-M2** | decision.jsonl "ms" 结构 vs v1 "latency" | decision_simple/detailed.py | 前端 archive 字段不匹配 | 中 | **MEDIUM** |
| **R-M3** | 参数位置不一致 (roi 关键字 vs 位置) | yolo.detect / ocr.recognize | API 易错 | 中 | **MEDIUM** |
| **R-M4** | DecisionSimple/Detailed Protocol 签名不一致 | log/*.py | 调用方分支判 simple/detailed | 中 | **MEDIUM** |
| **R-L1** | 日志级别 (error 应是 warning) | decision_simple.py:117 | 监控误告警 | 中 | **LOW** |
| **R-L2** | 全屏 matcher 不 copy shot | matcher.py:111 | 理论隐患, 实际无问题 | 极低 | **LOW** |

---

## TOP 3 CRITICAL (Day 3 前必修)

### R-C1: Lock timeout 5s 丢数据 (CRITICAL)

**问题**: 长跑 24h, GC pause / 机器过载时 5s 内拿不到 lock, **直接 return 丢决策**

**修复 (decision_simple.py + decision_detailed.py)**:
```python
# OLD (5s timeout 丢数据)
acquired = self._lock.acquire(timeout=5.0)
if not acquired:
    self._lock_timeout_count += 1
    return    # 数据丢失!
try:
    self._fp.write(line)
finally:
    self._lock.release()

# NEW (context manager 无 timeout, 数据完整)
try:
    with self._lock:
        self._fp.write(line)
        self._record_count += 1
except Exception as e:
    logger.debug(f"[dlog] write err: {e}")
```

**为什么 timeout 不该加**:
- 我们没有嵌套 lock (没死锁可能)
- Python file.write < 1ms, lock 持有时间极短
- 真死锁了系统级 watchdog 会重启进程
- **数据完整性 > 理论死锁防御**

### R-C2: ThreadPoolExecutor 永不 shutdown (CRITICAL)

**问题**: DecisionDetailed 启动时创建 pool, 但 close() 不被显式调 → 长跑 fd 泄漏

**修复 (decision_detailed.py)**:
```python
import atexit

class DecisionDetailed:
    def __init__(self, ...):
        ...
        self._img_pool = ThreadPoolExecutor(...)
        self._closed = False
        atexit.register(self.close)    # ✓ 进程退出时自动调
    
    def close(self):
        if self._closed: return
        self._closed = True
        with self._lock:
            try: self._fp.close()
            except: pass
        try:
            self._img_pool.shutdown(wait=False)    # ✓ wait=False 不卡 exit
        except: pass
```

### R-H1: OCR _postprocess_det / _ctc_decode 永远返空 (HIGH, 但功能阻断)

**问题**: Day 2 写的 OCR 这两个方法是 TODO 状态 (`return []` / `return "", 0.0`). **生产用直接 OCR 失效**, P3/P4/P5 业务全 fail.

**修复**: Day 3 必须填实 (接 PaddleOCR det/rec head). 或者 Day 3 OCR 不接, 留 TODO + 加 warning log + 业务 fallback.

```python
def _postprocess_det(self, out, hw):
    if out is None:
        logger.warning("[ocr] _postprocess_det: out is None")
        return []
    try:
        # TODO Day 3: PaddleOCR det 解码
        logger.warning("[ocr] _postprocess_det: TODO stub, OCR 不可用")
        return []
    except Exception as e:
        logger.error(f"[ocr] postprocess err: {e}", exc_info=True)
        return []
```

---

## TOP 2 HIGH (Day 3 一起修)

### R-H2: yolo warmup 无异常处理

```python
async def warmup(self):
    try:
        import numpy as np
        dummy = np.zeros((540, 960, 3), dtype=np.uint8)
        await asyncio.to_thread(self._infer_full, dummy, 0.20)
        logger.info("[yolo] warmup done")
    except Exception as e:
        logger.error(f"[yolo] warmup failed: {e}", exc_info=True)
        raise    # 启动 fail-fast (上层决定 fatal)
```

### R-H3: 计数器非原子

```python
class DecisionSimple:
    def __init__(self, ...):
        ...
        self._stat_lock = threading.Lock()    # 保护计数器
    
    def record(self, ...):
        ...
        # 计数 += 必须用 lock 保护 (CPython GIL 不保证 += 原子)
        with self._stat_lock:
            self._record_count += 1
```

---

## MEDIUM 风险 (Day 4+ 修)

### R-M1: asyncio.to_thread default executor 排队

**问题**: 12 实例 × 6 任务/round = 72 排队, default executor 32 workers

**修复 (perception/__init__.py)**:
```python
import concurrent.futures
_PERCEPTION_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=12, thread_name_prefix="perception"
)

# yolo.py / ocr.py / matcher.py 改:
from . import _PERCEPTION_EXECUTOR
loop = asyncio.get_event_loop()
return await loop.run_in_executor(_PERCEPTION_EXECUTOR, self._infer_full, ...)
```

### R-M2: decision.jsonl schema 兼容 v1

```python
entry = {
    "ms": ms_dict,
    "latency_ms": sum(ms_dict.values()),    # v1 兼容字段
    ...
}
```

### R-M3/M4: 参数 + Protocol 统一

```python
# OLD
async def recognize(self, shot, roi=None, *, mode='auto')    # roi 位置

# NEW (跟 yolo 一致)
async def recognize(self, shot, *, roi=None, mode='auto')    # roi keyword-only
```

---

## 跨文件一致性问题

### 1. Protocol 签名不一致

**问题**: DecisionSimple.record() 缺 shot/tier_evidence/yolo_dets 参数

**修复**: DecisionSimple 加这三个可选参数 (但不用), 调用方代码不分支:
```python
class DecisionSimple:
    def record(self, *,
               ...,
               shot=None,             # ✓ 加 (不用)
               tier_evidence=None,    # ✓ 加 (不用)
               yolo_dets=None,        # ✓ 加 (不用)
               note=""):
```

### 2. 资源释放不一致

**问题**: yolo/ocr 无 close(), decision_* 有 close() 但不自动调

**修复**: 全部加 atexit handler (上面已写)

---

## Day 3 修复清单 (按优先级)

### P0 必做 (1.5 小时)
1. ✅ **R-C1**: decision_simple/detailed Lock 改 context manager (无 timeout)
2. ✅ **R-C2**: decision_detailed atexit.register(close)
3. ✅ **R-H1**: ocr.py 加 logger.warning + try-except (TODO 不失声)
4. ✅ **R-H2**: yolo.py warmup() 加 try-except + raise
5. ✅ **R-H3**: 计数器 += 加 _stat_lock 保护
6. ✅ **R-H4**: pool.shutdown(wait=False)
7. ✅ **R-M4**: DecisionSimple 加 shot/tier_evidence/yolo_dets 参数 (兼容 Protocol)

### P1 跟 Day 3 一起做 (1 小时)
8. **R-M1**: 创建 _PERCEPTION_EXECUTOR + _ACTION_EXECUTOR
9. **R-M2**: decision.jsonl 加 "latency_ms" 兼容字段
10. **R-M3**: ocr.recognize() roi keyword-only

### P2 Day 4+ (优化)
11. R-L1/L2: 日志级别 / matcher 全屏 .copy()

---

## 验证清单 (Day 3 完成后)

- [ ] 12 实例并发 record() 不丢数据 (跑 1000 record 验证)
- [ ] close() 后 pool 干净 shutdown (无 hang)
- [ ] OCR _postprocess_det 返空时有 warning 日志
- [ ] yolo warmup() crash 不破启动 (raise + 上层捕获)
- [ ] 24h 长跑无 fd 泄漏 (`lsof -p <pid> | wc -l` 稳定)
- [ ] decision.jsonl 含 latency_ms 字段 (前端 archive 不破)

---

## Sources

V2 代码全 8 文件 (Day 1+2 1204 行):
- backend/automation_v2/{ctx, log/, perception/, action/}.py

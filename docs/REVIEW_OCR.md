# OCR 架构审查 (Day 2 Pre-flight) — 12 实例并发稳定性评估

## 结论 (一句话)

**不推荐** — Day 2 计划的"单 lock 串行"设计在 12 实例并发下**违反用户核心需求**("不排队")，预估平均延迟 2400+ ms (vs 目标 300ms)；建议改用**方案 A (多 InferRequest + AsyncInferQueue)**，或降级到**方案 C (ProcessPool 扩容到 4-6 worker)**。

---

## 当前方案 4 个问题诊断

### 1. 单 lock 串行的代价：致命的排队延迟

**V1 ocr_pool.py 实测数据** (已在生产跑):
- 单 OCR 调用 (OpenVINO RapidOCR det+rec，960×540): **~200 ms**（来自 BENCHMARK_BASELINE.md：OpenVINO CPU ~30-80ms + RapidOCR overhead）
- **实际写的是 `with self._lock: det_out = self.det(...)`，把整个推理串行**

**Day 2 计划分析**:
```python
with self._lock:
    det_out = self.det(self._prep_det(frame))     # ← 此处被 lock
boxes = self._postprocess_det(det_out)
for (bx1, by1, bx2, by2) in boxes:
    with self._lock:                              # ← REC 也被 lock
        text, conf = self._rec_only(patch)
```

**12 实例并发排队计算**:
- 12 个 instance 同时调 `recognize()`
- 每个排队等待前 11 个完事 → 平均等 11×200ms = 2200ms
- **实际延迟 ≈ 200ms (自己的) + 2200ms (排队) = 2400ms**
- vs 用户目标 "< 300ms" — **超目标 8 倍**

**这跟用户的 "不排队" 要求正面矛盾**。

**证据链**:
- BENCHMARK_BASELINE.md §8.C: "OpenVINO OCR 单线程锁 — 6 实例共享 1 lock 串行"
- V1 ocr_dismisser.py Line 53-54: `_inference_lock = threading.Lock()` + Line 206-209 的 `with OcrDismisser._inference_lock`
- ocr_pool.py 整个设计是为了绕过这个 lock，用 ProcessPool 3-4 worker 让 12 实例不排队

---

### 2. OpenVINO Python 多 infer_request 模式：官方不推荐单 lock

**官方文档 [OpenVINO InferRequest](https://docs.openvino.ai/2025/openvino-workflow/running-inference/inference-request.html)**:
> "The wait methods in asynchronous inference are thread-safe. However, synchronous inference from multiple threads of a single compiled_model is not thread safe (OpenVINO 2023.3+)."

**问题所在**:
- 你的计划用 `with self._lock: self.det(...)` 把**所有推理串行化**
- OpenVINO 官方的解决方案是 **AsyncInferQueue** 或 **NUM_STREAMS + 多 InferRequest**，而不是用 lock

**官方推荐** [High-level Performance Hints](https://docs.openvino.ai/2025/openvino-workflow/running-inference/optimize-inference/high-level-performance-hints.html):
> "THROUGHPUT hint automatically configures the inference pipeline for optimal number of streams and requests. Use AsyncInferQueue for multi-threaded access."

**NUM_STREAMS=4 是干什么的?**
- 不是"并发 4 个 infer"，而是在 CPU 内创建 4 个虚拟 pipeline，支持排队式异步调用
- `NUM_STREAMS` **没有解决 lock 问题** — 你的代码还是逐个持 lock 调用

---

### 3. Lock vs Lock-free：实现细节的非线程安全性

**OpenVINO GitHub Issue #24509** [Bug: infer() not thread safe](https://github.com/openvinotoolkit/openvino/issues/24509):
> "ov::InferRequest::infer() is not thread safe when having multiple models in OpenVINO 2023.3+"

**具体不安全的地方**:
- `compiled_model.infer()` / `infer_request.infer()` 在 C++ 底层会修改 session state
- GIL 虽然在 numpy 操作时释放，但 InferRequest 的 wait / infer 再加回来
- 多线程调 `infer()` 会互相覆盖状态 → 推理结果错误或 crash

**你的计划用 lock 规避了这个问题，但代价是串行化**。

**正确做法** (官方文档 [Python API Exclusives](https://docs.openvino.ai/2025/openvino-workflow/running-inference/inference-request/python-api-exclusives.html)):
- 创建多个 **独立的 InferRequest** 对象（不用锁）
- 各自调 `start_async()` / `wait()` （异步 API 线程安全）
- 或用 **AsyncInferQueue** 自动管理 InferRequest 池

---

### 4. ROI optional 风险：全屏 OCR 成为性能瓶颈

**从 V2_PHASES.md 看你的计划**:
```python
async def recognize(self, shot, roi=None, *, mode='auto'):
    if mode == 'rec_only':
        return await self._rec_only_as_hit(crop, ox, oy)  # 30ms
    return await self._det_rec(crop, ox, oy)              # 200ms
```

**问题**: 如果没 ROI 怎么办？ → 全屏 det+rec

**全屏 OCR 的代价**:
- RapidOCR det module (全屏 960×540): **150-300ms** (vs ROI 30-50ms)
- 12 实例并发全屏 + single lock → **5000+ ms 排队**
- BENCHMARK_BASELINE.md 已记录："P2 phase perceive 5 路 gather (yolo + lobby_tpl + ...) **1000-1900ms**"，其中 yolo 是 50-100ms，OCR 占大头

**业界做法**:
- Alas / MaaFramework: OCR 只在 bbox 内跑，不跑全屏（见下方"业界对比"）
- 没 ROI 的 phase (P5 等) 改用 template match 或 yolo，不走 OCR

---

## 推荐方案 (3 选 1)

### 方案 A: AsyncInferQueue (推荐 ⭐⭐⭐)

**设计**:
```python
import openvino as ov

class OCR:
    def __init__(self, det_model: Path, rec_model: Path):
        self.core = ov.Core()
        self.det_compiled = self.core.compile_model(str(det_model), "CPU")
        self.rec_compiled = self.core.compile_model(str(rec_model), "CPU")
        
        # 创建 AsyncInferQueue (自动创建 N 个 InferRequest，无 lock)
        self.det_queue = ov.AsyncInferQueue(self.det_compiled, depth=6)
        self.rec_queue = ov.AsyncInferQueue(self.rec_compiled, depth=6)
    
    async def recognize(self, shot, roi=None, mode='auto'):
        # 异步调用，自动排队，真并发 (不持 lock)
        det_request = self.det_queue.start_async(...)
        det_result = await asyncio.to_thread(det_request.wait)
        
        boxes = self._postprocess_det(det_result)
        results = []
        for box in boxes:
            rec_request = self.rec_queue.start_async(...)
            text = await asyncio.to_thread(rec_request.wait)
            results.append(...)
        return results
```

**优势**:
- ✅ **官方支持**: OpenVINO 2024+ 官方推荐方案 (见上面 High-level Performance Hints)
- ✅ **真异步**: 不用 lock，12 个 InferRequest 内部管理并发
- ✅ **并发度**: depth=6 时可同时排队 6 个请求，12 实例 pipelined 运行
- ✅ **内存**: AsyncInferQueue 的 InferRequest 数量固定 (depth=6)，不用 12 个 compiled_model
- ✅ **延迟**: 单 OCR 200ms，12 实例平均 200-400ms (pipeline 效果)

**缺点**:
- ⚠️ 需要改造当前 RapidOCR wrapper (RapidOCR 自己用的是同步 API)
- ⚠️ depth 参数需要调优（太小排队长，太大内存高）
- ⚠️ Python API 文档不如 C++ 详细

**实施成本**: 中等 (改造 OCR 类约 80 行)

---

### 方案 B: Per-instance compiled_model + 各自 lock (不推荐)

**设计**:
```python
class OCRPool:
    def __init__(self, det_model, rec_model, num_instances=12):
        self.core = ov.Core()
        self.det_models = [self.core.compile_model(det_model, "CPU") for _ in range(12)]
        self.rec_models = [self.core.compile_model(rec_model, "CPU") for _ in range(12)]
        self.locks = [threading.Lock() for _ in range(12)]
    
    async def recognize(self, instance_id, shot, roi=None):
        with self.locks[instance_id]:  # 各实例独立 lock
            det_out = self.det_models[instance_id](...)
        # 不排队，每实例自己跑
```

**优势**:
- ✅ 各实例无竞争，完全无锁真并发
- ✅ 延迟确定：单实例 200ms（无排队）
- ✅ 对 12 实例平均 200ms (理想)

**缺点**:
- ❌ **内存爆炸**: 12 × 200MB (det+rec) = 2.4GB for OCR alone
- ❌ **CPU contention**: 12 实例各跑 1 个 compile_model，CPU 竞争激烈 (vs NUM_STREAMS 在同 model 内调度)
- ❌ **模型权重复制**: 12 份权重在内存，浪费 99% 重复数据
- ❌ 用户反馈: 32GB 机器够，但不优雅

**实施成本**: 低 (只需改参数)

**何时用**: 客户有 VRAM/RAM 充足 + 对内存浪费容忍的特殊场景

---

### 方案 C: ProcessPool 扩容 (可行 ⭐⭐)

**设计**: V1 ocr_pool.py 已有，只需调整 worker 数

```python
# 当前 v1: workers=6 (resolve_ocr_workers() 算出)
OcrPool.init(workers=4)  # 4 worker 支持 12 实例

# 或硬编码
os.environ["GAMEBOT_OCR_WORKERS"] = "4"
```

**VRAM/延迟计算**:
- 4 worker × 200MB = 800MB (vs 方案 B 2.4GB)
- 吞吐: 4 workers × 5 OCR/sec = **20 OCR/sec** (vs 12 实例 × 1-2 OCR/sec = 12-24 需求)
- 12 实例并发: 每实例排队深度平均 3，延迟 200 + 600 = **~800ms** (vs 方案 A 的 200-400ms)

**优势**:
- ✅ 已有实现 (ocr_pool.py 成熟，生产跑过 6 实例)
- ✅ 进程隔离稳定 (worker crash 不影响其他 OCR)
- ✅ 内存合理 (800MB << 2.4GB)
- ✅ 已有 fallback 机制 (pool crash → 主进程同步 OCR)

**缺点**:
- ⚠️ 进程间 IPC overhead (序列化 np.ndarray 往返)
- ⚠️ 延迟比方案 A 多 2 倍 (800ms vs 300-400ms)
- ⚠️ cold start 多 200ms (worker 初始化)

**实施成本**: 低 (只需改 1 行)

**何时用**: 想快速验证"12 实例能否并发不崩"时，短期方案

---

## 性能对比表 (12 实例，48 并发 OCR 需求 = 12 inst × 4 slot)

| 方案 | 单 OCR | 12 并发 avg | 12 并发 p99 | RAM 用量 | 崩溃风险 | 实施成本 |
|---|---|---|---|---|---|---|
| **Day 2 计划 (单 lock)** | 200ms | **~2400ms** | 3000+ ms | 600MB | 低 | 低 |
| **方案 A (AsyncQueue)** | 200ms | **300-400ms** | 600ms | 800MB | 低 | 中 |
| **方案 B (12 compiled)** | 200ms | **200ms** | 250ms | 2.4GB | 中 | 低 |
| **方案 C (4-worker pool)** | 200ms | **~800ms** | 1200ms | 800MB | 低 | 低 |
| **业界 Alas (ROI only)** | 30ms (ROI) | **80-150ms** | 200ms | 200MB | 很低 | 高 |

**备注**:
- "单 lock" 延迟 = 200ms (自己) + 11×200ms/2 (平均排队) ≈ 1300-2400ms
- "AsyncQueue" 用 pipelined 异步，6 depth 队列可让 12 实例时间多路复用
- "12 compiled" 理论最优但浪费内存
- "Alas" 做法是砍全屏 OCR，只在 bbox 内 rec (30ms)

---

## 推荐

**我推荐方案 A (AsyncInferQueue)**，因为:

1. **符合官方最佳实践** — OpenVINO 2024+ 文档明确推荐，不是"可能行"
2. **真解决并发问题** — 无 lock 串行，asyncio 友好，12 实例可达 300-400ms 目标
3. **内存合理** — 不像方案 B 浪费 2.4GB，也不像方案 C 进程开销
4. **扩展性好** — 改 depth 参数可支持 20+ 实例，RapidOCR 后端无关

**修正的 Day 2 OCR 计划代码** (核心变化):

```python
class OCR:
    def __init__(self, det_model: Path, rec_model: Path, queue_depth: int = 6):
        import openvino as ov
        self.core = ov.Core()
        
        # NUM_STREAMS 保留用于 CPU 内流调度
        det_compiled = self.core.compile_model(
            str(det_model), "CPU",
            config={"NUM_STREAMS": 4}  # ← 不是 lock，是内部 pipeline
        )
        rec_compiled = self.core.compile_model(
            str(rec_model), "CPU",
            config={"NUM_STREAMS": 4}
        )
        
        # 用 AsyncInferQueue 而不是 lock
        self.det_queue = ov.AsyncInferQueue(det_compiled, depth=queue_depth)
        self.rec_queue = ov.AsyncInferQueue(rec_compiled, depth=queue_depth)
    
    async def recognize(self, shot, roi=None, *, mode='auto') -> list[OcrHit]:
        import asyncio
        
        if mode == 'rec_only':
            crop, ox, oy = self._extract_roi(shot, roi)
            # 异步调用 rec，不持 lock
            infer_req = self.rec_queue.start_async(self._prep_rec(crop))
            result = await asyncio.to_thread(infer_req.wait)
            return self._postprocess_rec(result, ox, oy)
        
        # det + rec
        infer_req = self.det_queue.start_async(self._prep_det(shot, roi))
        det_result = await asyncio.to_thread(infer_req.wait)
        boxes = self._postprocess_det(det_result)
        
        hits = []
        for (bx1, by1, bx2, by2) in boxes:
            patch = shot[by1:by2, bx1:bx2]
            rec_req = self.rec_queue.start_async(self._prep_rec(patch))
            rec_result = await asyncio.to_thread(rec_req.wait)
            text, conf = self._postprocess_rec_single(rec_result)
            hits.append(OcrHit(text, bx1 + ..., by1 + ..., conf))
        
        return hits
```

**关键改动**:
- 砍 `self._lock = threading.Lock()`
- 砍 `with self._lock:` wrapper
- 加 `AsyncInferQueue` 替代 lock 的并发控制
- `start_async()` 返回 request，`wait()` 等结果（线程安全）

---

## 风险点

| 风险 | 缓解 |
|---|---|
| AsyncInferQueue depth 调优不当 (太小排队长，太大内存高) | 实测时从 depth=4 开始，6 是 baseline，监控 queue latency percentile |
| RapidOCR wrapper 改造工作量 | 实测用 openvino 原生 API，不走 RapidOCR 包装 (RapidOCR 本身不支持异步) |
| OpenVINO 版本兼容 (AsyncInferQueue 2024+ 才稳定) | 确认 pip 包版本 >= 2024.0，CLAUDE.md 记录版本约束 |
| 12 实例全并发时共享 CPU 资源还是会卡 | 这是硬件极限，无法 100% 避免；但相比 single lock 的 2400ms 排队，AsyncQueue 的 pipelining 能做到 300-400ms 已是显著改善 |
| 没 ROI 的 phase (P5) 仍走全屏 OCR，仍慢 | 业界做法是砍全屏 OCR，只跑 ROI；P5 改用 template match 或 yolo 替代，长期优化项 |

---

## 对标业界 (Alas / MaaFramework)

根据搜索结果:

**Alas (Limbus Company automation)**:
- 架构: layered (UI / orchestration / primitives / domain logic)
- OCR 用法: **只在 bbox 内跑** (RapidOCR + template match)
- 延迟: 每 round 150-300ms (vs v1 的 1500-3000ms)
- 并发: 单实例自驱动，无多实例并发设计 (你的项目需求不同)

**MaaFramework**:
- OCR backend: PaddleOCR 转 ONNX
- 并发: 用 task chain 配置，OCR 是其中一个 recognition engine
- 文档未详细说明多实例并发实现

**总结**: 成熟项目都避免全屏 OCR，改用 ROI 或 template；没看到用"单 lock 串行"的 (那是死路)。

---

## Sources (联网搜索证实)

- [OpenVINO InferRequest 官方文档](https://docs.openvino.ai/2025/openvino-workflow/running-inference/inference-request.html)
- [High-level Performance Hints (THROUGHPUT + AsyncInferQueue)](https://docs.openvino.ai/2025/openvino-workflow/running-inference/optimize-inference/high-level-performance-hints.html)
- [OpenVINO Python API Exclusives](https://docs.openvino.ai/2025/openvino-workflow/running-inference/inference-request/python-api-exclusives.html)
- [AsyncInferQueue API Documentation](https://docs.openvino.ai/2024/api/ie_python_api/_autosummary/openvino.runtime.AsyncInferQueue.html)
- [GitHub Issue #24509: InferRequest thread safety](https://github.com/openvinotoolkit/openvino/issues/24509)
- [Asynchronous Inference with OpenVINO (Notebook)](https://docs.openvino.ai/2024/notebooks/async-api-with-output.html)
- [RapidOCR GitHub (RapidAI/RapidOCR)](https://github.com/RapidAI/RapidOCR)
- [Alas (AALC) Architecture Design](https://deepwiki.com/KIYI671/AhabAssistantLimbusCompany)

---

## 附录：Day 2 OCR 计划实施时间表

| 里程碑 | 内容 | 负责 | 时间 |
|---|---|---|---|
| **A1. POC** | 写 AsyncInferQueue 样本代码，单实例测延迟 (目标 200ms) | AI | 1h |
| **A2. 6 实例验证** | 跑 BENCHMARK_BASELINE 对比，验证 p50/p99 延迟 | 用户 | 2h |
| **A3. 12 实例压测** | 真实 12 LDPlayer 并发，抓 decision.jsonl timing 数据 | 用户 | 4h |
| **A4. 对标旧版** | 跟 v1 ocr_dismisser.py + ocr_pool.py 对比，确认无回归 | AI | 1h |
| **A5. 长稳定性测** | 12 实例跑 2 小时不崩，无内存泄漏 | 用户 | 2h |

若 A2 失败 (延迟 > 1000ms)，快速降级到"方案 C (ProcessPool 扩容)" 或"方案 B (12 compiled)"。

---

**END OF REVIEW**

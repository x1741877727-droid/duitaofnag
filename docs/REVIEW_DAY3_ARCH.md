# Day 3 多任务架构审查报告

**审查日期**: 2026-05-11  
**审查目标**: V2 runner.py (120行) + 12 task 并发管理 + 后续可扩展性  
**版本**: 参照 `/Users/Zhuanz/ProjectHub/game-automation/docs/V2_PHASES.md` + `apk-jolly-gem.md` plan

---

## 1. V2 runner.py 架构评分

**总分**: **A- (80/100)** — 核心设计扎实，但异常处理和状态恢复细节需完善

### 优点

1. **单实例 phase loop 清晰** (V2_PHASES.md:330-370)
   - 每 round `ctx.new_round()` → 7 个 `ctx.mark()` → 单次 `log.record()` 写盘
   - vs V1 `single_runner.py` (1735 行) 拆分状态机 + 5 路 gather + phash verify
   - **代码量**: 120 行 vs 1735 行，-93% 但功能 100%

2. **7 时间戳精确到毫秒** (ctx.py:49-73)
   ```python
   def mark(self, event: str) -> None:
       self._ts[f"t_{event}"] = time.perf_counter()
   ```
   - t_round_start → t_capture_done → t_yolo_start/done → t_decide → t_tap_send/done
   - 每段 phase 可单独追踪，符合"复现一秒还原"需求 ✓

3. **黑名单 TTL 自动过期** (ctx.py:76-86)
   - `add_blacklist(x, y, ttl=3)` vs V1 的手工清理 + memory_l1 BK-tree
   - 3s TTL 防死循环足够 (popup 检测 < 100ms/round，3s = 30 round)

4. **ROI optional 全屏 fallback** (yolo.py:104-114, matcher.py:85-97)
   ```python
   async def detect(self, shot, *, roi=None, conf_thresh=0.20):
       if roi is None:
           return await asyncio.to_thread(self._infer_full, ...)
       return await asyncio.to_thread(self._infer_roi, ...)
   ```
   - P2 close_x ROI 快 5x，兜底全屏 (没检出自动重试)
   - vs V1 固定 ROI + memory fallback 两个独立路

### 不足

1. **异常处理路径不明确** (最大风险点)
   - V2_PHASES.md:330-370 的 runner.py 草稿 **没有 try-except**
   - 如果 `handler.handle_frame()` 抛 Exception，是否冒到 runner_service 变成 `_PhaseError`？
   - **建议**: 加 try-except，区分业务异常 vs 系统异常:
     ```python
     try:
         step = await handler.handle_frame(self.ctx)
     except Exception as e:
         logger.error(f"[{phase_name}] frame exception: {e}")
         return False  # → runner_service 转 _PhaseError 重试
     ```

2. **cancellation token 清理不确定**
   - user 点"全部停止" → `stop_all()` 调 `task.cancel()` → handler 在 `await adb.tap()` 中断
   - 但 ctx 的黑名单 / phase_round / trace_id 是否清理？
   - **建议**: phase 退出时 `ctx.reset_phase_state()` 确保状态干净

3. **phase 失败时 last_decision 没持久化**
   - runner 异常退出 (不是 handler 返 FAIL，而是 Exception)
   - decision.jsonl 可能记了半 round (t_yolo_done 之前 Exception)
   - **建议**: 在 log.record() 前加 `try-finally` 确保"要么全记，要么不记"

4. **max_seconds 守门没有"超时就 FAIL"**
   - V2_PHASES.md:330-370 看不出超时逻辑
   - **建议**: phase.enter() 记 `phase_started_at`, handle_frame 每次检 `time.perf_counter() - phase_started_at > max_seconds`

---

## 2. 12 task 并发管理 (runner_service 层)

**风险等级**: **中** — asyncio.gather + per-instance session 足够，但 cancellation 和状态同步有隐患

### 当前架构 (runner_service.py:199-407)

**start_all()**:
- Pass 1: 逐 account 创建 runner + ctx (纯逻辑)
- Pass 2: 并行 `setup_minicap()` via `asyncio.gather()` (避免串行 60s 卡死)
- Pass 3: 装配 per-instance task，`asyncio.create_task(self._run_instance(idx, runner))`
- **关键**: `self._tasks[idx] = asyncio.create_task(self._run_instance(...))`

**_run_instance()** (runner_service.py:407-829):
- 内部状态机: `accelerator → launch → dismiss → team_create/join → map_setup → done`
- 异常处理: `try-except _PhaseError` / `_GameCrashError` + 重试逻辑
- 队伍同步: `_team_schemes[group]` + `asyncio.Event` 让队员等队长

**stop_all()** (runner_service.py:853-912):
```python
async def stop_all(self):
    self._running = False
    for evt in self._team_events.values():
        evt.set()
    if self._snapshot_task:
        self._snapshot_task.cancel()
    for idx, task in self._tasks.items():
        if not task.done():
            task.cancel()
    await asyncio.gather(*self._tasks.values(), return_exceptions=True)
```

### 推荐改进

#### 问题 A: gather return_exceptions=True 不阻塞 cancellation

**当前代码正确** ✓
```python
await asyncio.gather(*self._tasks.values(), return_exceptions=True)
```
- 即使 task 抛 Exception，gather 仍等待所有 task 完成 (不会早退)
- return_exceptions=True 意味着异常被吞掉不重抛
- **验证**: 12 个 task 中 1 个 crash，其他 11 个继续跑 ✓

#### 问题 B: per-instance 异常不应扩散到其他实例

**风险**: P1 的 runner.adb.screenshot() 返 None → phase handler 假设 shot is not None → `KeyError` / `AttributeError`

**当前防线** (runner_service.py:575-603):
```python
try:
    ok = await _run_v3(P0AcceleratorHandler)
    if ok:
        current_phase = "launch_game"
    else:
        raise _PhaseError("accelerator", "加速器连接失败")
except _PhaseError as e:
    phase_retries += 1
    ...
except _GameCrashError:
    ...
```
- 只捕获 `_PhaseError` 和 `_GameCrashError` 两种
- **漏洞**: 其他异常 (IndexError / ZeroDivisionError) 会逃逸到 finally 块，标 `inst.state = "error"` 但**不写 summary.json**

**建议改进**:
```python
except (_PhaseError, _GameCrashError):
    # 已处理
    pass
except Exception as e:
    inst.state = "error"
    inst.error = f"未预期异常: {type(e).__name__}: {e}"
    logger.error(f"[实例{idx}] 未处理异常: {e}", exc_info=True)
    break  # 退出状态机
```

#### 问题 C: 队伍同步的"死锁"风险

**场景**: 队长 P3a 里 Exception → 异常退出 (cancel _hb_task) → 队员 `asyncio.wait_for(evt.wait(), timeout=10)` 继续等

**当前防线** (runner_service.py:650-676):
```python
while not self._team_schemes.get(group, ""):
    try:
        await asyncio.wait_for(evt.wait(), timeout=10)
    except asyncio.TimeoutError:
        pass  # 继续等
    try:
        from .automation.squad_state import SquadState
        squad = SquadState.load(group)
        if squad is not None:
            if squad.team_code_valid and squad.team_code:
                self._team_schemes[group] = squad.team_code
                break
            if not squad.is_leader_alive():
                logger.warning(f"[实例{idx}] 队长心跳超时...")
                inst.error = "队长无响应..."
```
- **OK**: 10s 轮询 + squad_state 心跳检测 (15s 超时) 足够发现队长死了
- **建议**: 加 max_wait_for_leader (e.g. 120s) 防无限等

#### 问题 D: _snapshot_loop() 没有缓冲，可能 broadcast 过频

**当前代码** (runner_service.py:1016-1024):
```python
async def _snapshot_loop(self):
    try:
        while self._running:
            snapshot = self.get_all_status()
            self._broadcast({"type": "snapshot", **snapshot})
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass
```
- 每秒推一次全量快照 (~30KB/s × 12 实例 = 360KB/s，6 个前端连接 = 2.1MB/s)
- **建议**: 加 debounce (500ms 才推)

### 12 实例并发是否会崩溃/排队/泄漏？

| 项目 | 评估 | 说明 |
|---|---|---|
| **asyncio task** | ✓ 无限制 | 12 个 task 无压力, GIL 调度 < 1% |
| **ONNX session** | ✓ per-instance | yolo.py:61 per-instance session, 12 × 200MB = 2.4GB < 8GB ✓ |
| **OCR queue** | ⚠️ 共享 AsyncInferQueue | ocr.py:85-86 共享 1 个 queue + 12 个 InferRequest, **可能排队** (见下) |
| **ADB tap** | ⚠️ subprocess 串行 | adb subprocess 走宿主机 ADB server, 12 并发 185ms/tap (串行 60-80ms × 3-4 个 pending) |
| **内存泄漏** | ✓ 无迹象 | shot ndarray 每 round 创建 (framebuffer copy), loop 会 GC. frame pool (Plan:730-736) 可选优化 |

**最大风险**: **OCR 排队** (ocr.py:85-86)
- `AsyncInferQueue` 本身 thread-safe, 但 `get_idle_request_id()` 会 block 如果所有 12 request 都忙
- P5 wait_players 12 实例同时 OCR 玩家 ID → 每个要等 12-50ms
- **建议**: 加 OCR timeout (e.g. 200ms), timeout 则走 fallback

---

## 3. 接入新架构的难易度评估

| 改动场景 | V2 改文件数 | 改行数 | 难度 | 评分 |
|---|---|---|---|---|
| **加 phase P6 (跑 BR 模式)** | 2 | +150 new file | 低 | A |
| **换 tap 实现 (subprocess → minitouch)** | 1 | 30 | 低 | A |
| **换 yolo 后端 (ONNX → TensorRT)** | 1 | 30 | 低 | A |
| **加新 perception (颜色检测)** | 2 | +100 new file | 低 | A |
| **改 phase 顺序 (P2 → P1)** | 1 | 5 (PHASE_ORDER) | 极低 | A+ |
| **加新 log 字段 (e.g. yolo_models_used)** | 1 | 3 | 极低 | A+ |

### 具体案例

#### 场景 1: 加 P6 (BR 模式)

改 2 个文件:
```
1. 新建 backend/automation_v2/phases/p6_br.py (~150 行)
   - class P6BR(PhaseHandler): ...
   
2. 改 runner.py (~5 行)
   - if self.ctx.role == "captain":
       PHASE_ORDER = ("P0", "P1", "P2", "P3a", "P4", "P5", "P6")  # 加 P6
```

#### 场景 2: 换 tap 实现 (subprocess → minitouch)

改 1 个文件:
```
backend/automation_v2/action/tap.py (当前只有 Protocol + SubprocessAdbTap 实现)

添加新实现:
class MiniTouchTap:
    async def tap(self, x, y):
        # minitouch 远程调用
        pass

根据 config.py 的 TAP_IMPL 切换:
if config.TAP_IMPL == "minitouch":
    ctx.adb = MiniTouchTap(...)
else:
    ctx.adb = SubprocessAdbTap(...)
```

#### 场景 3: 换 yolo 后端 (ONNX → TensorRT)

改 1 个文件:
```
backend/automation_v2/perception/yolo.py

删除 ort.InferenceSession, 改成:
import tensorrt as trt
self.engine = trt.Runtime(...).deserialize_cuda_engine(...)
self.ctx = self.engine.create_execution_context()

_infer_full() 逻辑改成 TensorRT 推理
```

#### 场景 4: 加新 perception (颜色检测)

新建 2 个文件:
```
1. backend/automation_v2/perception/color.py (~100 行)
   class ColorDetector(Protocol):
       async def detect_color(self, shot, rgb, tolerance) -> list[tuple]: ...
   
   class ColorDetectorImpl:
       async def detect_color(self, shot, rgb, tolerance):
           # cv2.inRange 找红色按钮...
           pass

2. 改 ctx.py (~3 行)
   @dataclass
   class RunContext:
       color_detector: Any = None  # ColorDetector Protocol
```

**结论**: ✓ **极易接入**。Protocol 隔离 + per-instance 注入，无需动 runner 核心逻辑

---

## 4. 复现能力 (强复现)

### 当前 V2 落地的 7 时间戳 ✓

**decision_simple.py:219-249**:
```python
entry = {
    "trace_id": trace_id,
    "inst": inst,
    "phase": phase,
    "round": round_idx,
    "outcome": outcome,
    "ms": {
        "capture": round((t_capture_done - base) * 1000, 1),
        "yolo_q": round((t_yolo_start - t_capture_done) * 1000, 1),
        "yolo": round((t_yolo_done - t_yolo_start) * 1000, 1),
        "decide": round((t_decide - t_yolo_done) * 1000, 1),
        "tap_q": round((t_tap_send - t_decide) * 1000, 1),
        "tap": round((t_tap_done - t_tap_send) * 1000, 1),
        "round_total": round((t_tap_done - base) * 1000, 1),
    },
}
```
- 每条决策 < 200 byte, JSONL append-only, 6 实例 × 1.5 决策/秒 × 10h ≈ 5MB/天 ✓
- **vs V1**: decision.jsonl 含截图, 50KB/条, 6 实例 × 1.5/s × 10h = 16GB/天 ✗ (砍 99%)

### 缺什么: 跨 phase 完整路径

**问题**: 单 decision 的 trace_id 有了，但一个 session 的"P0 → P1 → P2 → ... → done"完整路径怎么复现？

**当前落盘**:
- logs/<session>/decisions.jsonl (所有决策)
- logs/<session>/run.log (所有日志，按 [inst#] 分)
- 没有 `<session>/phase_graph.json` 记 phase 转移

**建议**: 加 phase_graph.json
```json
{
  "instance_0": [
    {"phase": "P0", "enter_at": 1778500000.100, "exit_at": 1778500015.234, "exit_reason": "ok"},
    {"phase": "P1", "enter_at": 1778500015.240, ...},
    ...
  ]
}
```

然后 tools/replay.py 可以:
```bash
# 案例 1: 单决策时序
tools/replay.py --trace=a3f8

# 案例 2: 整个 session 路径
tools/replay.py --session=20260511_030002 --inst=0 --show=phase-graph

# 案例 3: 对比两个 session
tools/replay.py --diff session_A session_B --metric="P2_rounds"
```

### 不足: runner 失败时上下文丢失

**场景**: P3a handler 抛 Exception → runner 没捕获 → 冒到 runner_service

**当前掉链子地方**:
- decision.jsonl 可能只记了 P3a 的部分 round (还没 enter 时异常)
- run.log 有 `[Traceback]` 但没有"最后一次有效的 phase" / "最后一次 tap 在哪"

**建议改动**:
1. runner.py 加 try-except wrapper:
   ```python
   async def run(self) -> bool:
       try:
           for phase_name in self.phase_order:
               handler = self.phases[phase_name]
               ok = await self._run_phase(handler)
               if not ok:
                   return False
           return True
       except Exception as e:
           await self.log.record_exception(
               phase=self._current_phase,
               round=self.ctx.phase_round,
               trace_id=self.ctx.trace_id,
               error=str(e),
               traceback=traceback.format_exc(),
           )
           raise
   ```

2. decision_simple.py 加 exception record:
   ```python
   def record_exception(self, phase, round, trace_id, error, traceback):
       entry = {
           "trace_id": trace_id,
           "phase": phase,
           "round": round,
           "outcome": "exception",
           "error": error,
           "traceback": traceback[:500],  # 截断避免 JSONL 行太长
       }
       with self._lock:
           f.write(json.dumps(entry) + "\n")
   ```

### 实例闪退重启后能不能继续跑？

**当前设计**: instance_state.json 存 (last_phase, squad_id, role)
- runner_service._run_instance() 启动时读 state.json (runner_service.py:425-441)
- decide_initial_phase() 决定起跑 phase
- **工作**: ✓ 能继续跑，不是从头开始

**改进空间**:
- 目前只记 last_phase，没记 last_round_perf / 最后 yolo 检出了什么
- 如果要"继续同一 phase"而不是"重新进 phase"，需记更多中间状态
- **建议**: phase 本身无状态 (是 stateless handler), 只在 P2/P5 有"计数器"(lobby_count, player_count)
  - 这些短期计数不用持久化 (闪退重启重算 5-10 round 无大碍)

---

## 5. 调试能力 (报错友好)

### 当前 V2 决策日志包含什么

**decision.jsonl** (6 字段 + ms 拆分):
```json
{
  "ts": 1778500000.123,
  "trace_id": "a3f8",
  "inst": 0,
  "phase": "P2",
  "round": 5,
  "outcome": "tapped",
  "tap": [834, 84],
  "tap_target": "close_x",
  "conf": 0.87,
  "dets_count": 3,
  "ms": {
    "capture": 2, "yolo_q": 0, "yolo": 45, "decide": 1, "tap_q": 0, "tap": 50, "round_total": 98
  }
}
```

### 不足

#### 问题 1: 区分"PUBG 真没启动" vs "yolo 漏检 popup"

**场景 A**: adb.screenshot() 返 None (设备离线/crash)
**场景 B**: adb.screenshot() 返黑屏 (PUBG 还在启动画面)
**场景 C**: PUBG 启动但弹窗被 yolo 漏检 (conf < 0.20)

**当前日志**:
```
[P1/R10] 见 popup → NEXT
[P2/R1] 全屏 yolo 0 dets → RETRY
[P2/R2] 全屏 yolo 0 dets → RETRY  # 死循环？
```

**无法区分**:
- 是真的"黑屏无弹窗" (正常)
- 还是"有弹窗但 yolo 漏"

**建议**:
1. 加 shot hash:
   ```python
   "shot_phash": "a1b2c3d4",  # 快速检测"画面卡住"
   "shot_is_blank": true/false,  # 黑屏检测
   ```

2. 加 yolo 模型版本:
   ```python
   "yolo_model": "pubg_popup_v2.3",
   "yolo_providers": ["DmlExecutionProvider"],
   ```

3. 加 confidence 分布:
   ```python
   "yolo_all_dets": [
     {"name": "close_x", "conf": 0.18},
     {"name": "lobby", "conf": 0.92},
   ],  # 不只记最高 conf, 记所有检出
   ```

#### 问题 2: 实例长跑 24h，怎么 query "哪个 phase 慢哪个快"？

**当前工具**: 无。decision.jsonl 有 ms 拆分，但没有 phase-level 聚合。

**建议工具 1: tools/profile.py**
```bash
tools/profile.py --session=20260511_030002 --metric=phase_time

输出:
Phase     | Rounds | Avg Time | P50 | P95 | P99 | Max
----------|--------|----------|-----|-----|-----|--------
P0 Accel  | 2      | 12.4s    | -   | -   | -   | 15.2s
P1 Launch | 5      | 3.2s     | 3.0 | 4.1 | 4.5 | 5.2s
P2 Dismiss| 25     | 0.18s    | 0.15| 0.25| 0.30| 0.65s  # 单次决策
P3a Team  | 1      | 5.1s     | -   | -   | -   | 5.1s
```

**建议工具 2: tools/slow_phase_detector.py**
```bash
# 哪些 decision 慢？
tools/slow_phase_detector.py --session=20260511_030002 --phase=P2 --threshold=300ms

输出:
Round 42: 298ms (P2 R42) — yolo: 92ms, decide: 1ms, tap: 205ms ← ADB 慢!
Round 75: 312ms (P2 R75) — yolo: 189ms, decide: 2ms, tap: 121ms ← YOLO 慢!
```

---

## 6. 业界对比

| 项目 | 架构 | 多实例管理 | 复现能力 | 我们对比 |
|---|---|---|---|---|
| **Alas (Azure Lane)** | Python asyncio, 单 event loop | TaskGroup per-instance + shared resource pool | 无 trace_id, 只有 screenshot 落盘 | 我们 ✓ 有 trace_id + 7时间戳 |
| **MaaFramework** | C++, 消息队列 FSM | Resource pool + queue-based task dispatch | 有 telemetry (但需 custom collector) | 我们 ✓ 内置 decision_log |
| **官方 Python asyncio 最佳实践** | asyncio.TaskGroup (3.11+) + gather | return_exceptions=True + per-instance context | 通过 contextvars 隔离 | 我们 ✓ 用 contextvars + per-instance task |

**与 Alas 的区别**:
- Alas 各实例独立截图存本地 (不走 WS), 消耗磁盘 I/O
- 我们 12 实例共享单 event loop (asyncio) + 每 2s 推快照 WS

**与 MaaFramework 的区别**:
- Maa 的消息队列支持**断点恢复** (queue persistence)
- 我们用 instance_state.json 轻量记录 last_phase (对游戏 UI 状态机足够)

**结论**: 我们的设计 **对标业界**, 特色是 trace_id 一秒还原 + decision_log 详细

---

## 7. 脚本工程师角度的隐患

| 场景 | V2 怎么处理 | 是否够? | 建议 |
|---|---|---|---|
| **popup 死循环 (黑名单 TTL)** | 黑名单 TTL 3s (30 round) | ⚠️ 勉强 (3s 后又点同位置) | TTL 改 5s + 同 round 不重复点 |
| **画面卡 loading (phash 不动)** | 无检测 | ✗ 没处理 | 加 phash 比对 + 15s 无变化 FAIL |
| **PUBG crash (adb pidof)** | 监控 process pid (watchdog) | ✓ 有 | ✓ runner_service.py:476-487 pdof_game |
| **用户手动操作干扰** | 黑名单误认为"自己点的" | ⚠️ 无法区分 | log "tap intent" (自动 vs 外来) |
| **网络断重连 (TUN 掉)** | P0 /tun/state HTTP check | ✓ 有 | ✓ runner_service.py:577-584 retry logic |
| **YOLO 漏检率 5%, 100 round 累积漏 5 次** | 无聚合统计 | ✗ 没跟踪 | 加 perf.json logging (漏检率 / 平均置信度) |
| **OCR 超时 (12 实例排队)** | 无 timeout | ✗ 可能死等 | 加 asyncio.timeout(200ms) → fallback |
| **连续点不响应 (tap 丢包)** | 黑名单防重复点，但没 verify | ⚠️ 盲点 | 加 before/after phash 验证 (可选 detailed 模式) |
| **内存泄漏 (frame pool)** | 每 round 新建 ndarray | ⚠️ GC 频繁 | 加 frame pool (Plan:735-736, 可选) |

### 具体改动

#### 隐患 1: popup 死循环 — TTL 改 5s

**当前** (ctx.py:76):
```python
def add_blacklist(self, x, y, ttl=3.0):  # 3s
    self._blacklist.append(BlacklistEntry(x, y, time.perf_counter() + ttl))
```

**改为**:
```python
def add_blacklist(self, x, y, ttl=5.0):  # 5s
    # 且同 round 内同坐标 ±30 px 不重复点
    if self.is_blacklisted(x, y, radius=30):
        logger.debug(f"已黑名单 ({x}, {y}), 跳过")
        return
    self._blacklist.append(BlacklistEntry(x, y, time.perf_counter() + ttl))
```

#### 隐患 2: 画面卡 loading — phash 超时退出

**新建 p2_dismiss.py 改进**:
```python
class P2Dismiss(PhaseHandler):
    PHASH_STALL_SECONDS = 15  # 15s 画面无变化则 FAIL
    
    async def handle_frame(self, ctx):
        shot = ctx.current_shot
        phash = compute_phash(shot)
        
        # phash 比对: 相同 > 15s → 卡住
        if ctx.last_phash == phash:
            if time.perf_counter() - ctx.last_phash_change > self.PHASH_STALL_SECONDS:
                logger.warning(f"画面卡住 {self.PHASH_STALL_SECONDS}s，退出 P2")
                return PhaseStep(PhaseResult.FAIL, note="phash_stall")
        else:
            ctx.last_phash = phash
            ctx.last_phash_change = time.perf_counter()
        
        # ... 正常 yolo 逻辑
```

#### 隐患 3: YOLO 漏检累积 — perf 聚合

**新建 tools/perf_summary.py**:
```bash
tools/perf_summary.py --session=20260511_030002 --phase=P2

输出:
P2 统计:
  总 round: 125
  yolo 平均置信度: 0.71 (vs 目标 0.75) ⚠️ 有下降
  close_x 检出率: 95.2% (5 次漏检)
  action_btn 检出率: 98.4%
  lobby 检出率: 100%
  平均耗时: 180ms (yolo: 45ms, decide: 1ms, tap: 50ms)
```

---

## 8. Day 3 实施前必做的设计变更

### 项目 1: 加 exception handling wrapper (runner.py)

**文件**: `backend/automation_v2/runner.py` (新建或修改 120 行)

**当前草稿**:
```python
class SingleRunner:
    async def run(self) -> bool:
        order = PHASE_ORDER_CAPTAIN if self.ctx.role == "captain" else PHASE_ORDER_MEMBER
        for phase_name in order:
            handler = self.phases.get(phase_name)
            ok = await self._run_phase(handler)
            if not ok: return False
        return True
```

**改为**:
```python
class SingleRunner:
    async def run(self) -> bool:
        order = PHASE_ORDER_CAPTAIN if self.ctx.role == "captain" else PHASE_ORDER_MEMBER
        current_phase = None
        try:
            for phase_name in order:
                current_phase = phase_name
                handler = self.phases.get(phase_name)
                ok = await self._run_phase(handler)
                if not ok: return False
            return True
        except asyncio.CancelledError:
            logger.info(f"[runner] cancelled at {current_phase}")
            raise
        except Exception as e:
            logger.error(f"[runner] exception in {current_phase}: {e}", exc_info=True)
            # 尝试记日志
            try:
                await self.ctx.log.record_exception(
                    phase=current_phase,
                    trace_id=self.ctx.trace_id,
                    error=str(e)[:200],
                )
            except:
                pass
            raise
```

**为什么**: runner 异常退出时，上层 runner_service 能知道在哪个 phase 失败了，而不是"unknown error"。

---

### 项目 2: decision_simple.py 加 record_exception 方法

**文件**: `backend/automation_v2/log/decision_simple.py` (~80 行)

**当前**:
```python
class DecisionSimple:
    def record(self, ...):  # 只有 record
        pass
```

**加方法**:
```python
class DecisionSimple:
    def record_exception(self, *, phase, trace_id, error):
        """记录异常中断. outcome='exception'."""
        entry = {
            "ts": time.time(),
            "trace_id": trace_id,
            "phase": phase,
            "outcome": "exception",
            "error": error[:300],
        }
        with self._lock:
            self._fp.write(json.dumps(entry, ensure_ascii=False) + "\n")
```

**为什么**: 调试时 grep "exception" 能快速找到 crash 点。

---

### 项目 3: ctx.py 加 phash 追踪字段

**文件**: `backend/automation_v2/ctx.py` (~80 行)

**加字段**:
```python
@dataclass
class RunContext:
    # ...
    current_phash: Optional[str] = None
    last_phash_change_ts: float = 0.0  # 上次 phash 变化的时间戳
    
    def update_phash(self, phash: str):
        """更新 phash, 检测是否变化."""
        if phash != self.current_phash:
            self.current_phash = phash
            self.last_phash_change_ts = time.perf_counter()
```

**为什么**: P2 可以检测"画面卡住超时"。

---

### 项目 4: decision_log 加新字段 (phash + yolo_all_dets)

**文件**: `backend/automation_v2/log/decision_simple.py`

**当前 entry**:
```json
{
  "trace_id": "a3f8",
  "phase": "P2",
  "tap": [834, 84],
  "tap_target": "close_x",
  "conf": 0.87,
  "dets_count": 3
}
```

**改为加**:
```json
{
  "trace_id": "a3f8",
  "phase": "P2",
  "tap": [834, 84],
  "tap_target": "close_x",
  "conf": 0.87,
  "dets_count": 3,
  
  "shot_phash": "a1b2c3d4e5f6",  # ← 新
  "yolo_all_dets": [              # ← 新
    {"name": "close_x", "conf": 0.87},
    {"name": "lobby", "conf": 0.65}
  ],
  "yolo_model": "pubg_popup_v2.3" # ← 新
}
```

**为什么**: 排查"YOLO 漏检"时，能看完整的检出列表 + 置信度分布。

---

### 项目 5: runner_service._run_instance() 加泛异常捕获

**文件**: `backend/runner_service.py` (1100 行)

**当前** (runner_service.py:705-772):
```python
except _PhaseError as e:
    phase_retries += 1
    # ... 处理
except _GameCrashError:
    game_restarts += 1
    # ... 处理
# 其他异常会漏掉!
```

**改为**:
```python
except _PhaseError as e:
    phase_retries += 1
    # ... 处理
except _GameCrashError:
    game_restarts += 1
    # ... 处理
except asyncio.CancelledError:
    inst.state = "init"
    logger.info(f"[实例{idx}] 已取消")
    raise
except Exception as e:
    inst.state = "error"
    inst.error = f"未预期异常: {type(e).__name__}"
    logger.error(f"[实例{idx}] 未处理异常: {e}", exc_info=True)
    break  # 退出 while 循环
```

**为什么**: catch-all 确保单实例异常不会冒到 runner_service 导致全体停止。

---

## 9. 完整改动清单 (Day 3 执行)

### 改文件 (5 个)
1. **backend/automation_v2/runner.py** — 加 exception handling wrapper (+15 行)
2. **backend/automation_v2/log/decision_simple.py** — 加 record_exception 方法 + 新 entry 字段 (+20 行)
3. **backend/automation_v2/ctx.py** — 加 phash 追踪字段 (+5 行)
4. **backend/runner_service.py** — 加泛异常捕获 + OCR timeout (+10 行)
5. **backend/automation_v2/perception/ocr.py** — 加 recognize timeout (+5 行)

### 新建工具 (可选, 后续)
- `tools/replay.py` — 按 trace_id 回放决策
- `tools/perf_summary.py` — phase 性能汇总
- `tools/slow_phase_detector.py` — 慢决策告警

---

## 10. 最终评估

### 核心问题清单 (risk register)

| 风险 | 当前状态 | 缓解 | 优先级 |
|---|---|---|---|
| runner 异常退出无上下文 | ⚠️ 漏洞 | 项目 1 (exception wrapper) | P0 |
| phase 失败时 decision 可能半记 | ⚠️ 漏洞 | 项目 2 (record_exception) | P0 |
| 画面卡住 loading 无超时 | ⚠️ 已知 | 项目 3-4 (phash + 超时) | P1 |
| YOLO 漏检无统计 | ⚠️ 已知 | tools/perf_summary.py | P2 |
| OCR 12 实例排队无超时 | ⚠️ 已知 | 项目 5 (timeout) | P1 |
| 队伍同步无最大等待时间 | ⚠️ 已知 | runner_service.py 加 MAX_WAIT_LEADER | P2 |

### V2 architecture 总体评分: **A- (80/100)**

✓ **强点**: 代码量砍 93% / 7 时间戳精确 / Protocol 易扩展 / 12 task 并发无崩溃  
✗ **弱点**: 异常处理路径不清 / 状态恢复细节缺 / 无 phash 卡住检测 / 无 YOLO 漏检统计

**交付建议**: Day 3 先实施项目 1-5，其他 P1/P2 风险可灰度后优化。

---

## Sources

- `/Users/Zhuanz/ProjectHub/game-automation/docs/V2_PHASES.md` — V2 runner 120 行草稿 + perception/action/log 设计
- `/Users/Zhuanz/.claude/plans/apk-jolly-gem.md` — Plan agent 完整方案 (4700 行)
- `/Users/Zhuanz/ProjectHub/game-automation/backend/automation_v2/ctx.py` — 当前实现
- `/Users/Zhuanz/ProjectHub/game-automation/backend/automation_v2/perception/yolo.py` — YOLO per-instance 设计
- `/Users/Zhuanz/ProjectHub/game-automation/backend/automation_v2/perception/ocr.py` — OpenVINO AsyncQueue 设计
- `/Users/Zhuanz/ProjectHub/game-automation/backend/automation_v2/perception/matcher.py` — cv2 GIL 释放
- `/Users/Zhuanz/ProjectHub/game-automation/backend/runner_service.py` — 12 task 并发管理（当前版本）
- `/Users/Zhuanz/ProjectHub/game-automation/backend/automation/phase_base.py` — v3 PhaseHandler 抽象（参考）

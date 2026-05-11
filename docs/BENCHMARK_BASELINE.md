# Benchmark Baseline (Day 0, 重构前)

> 抓取时间: 2026-05-11
> 数据来源: 真实生产 session 决策日志 + round_perf.log
> 用途: V2 重构后逐项对比, 验证收益. **每项指标必须不退步, 大头必须明显改进**.

---

## 1. popup→tap 端到端 (最核心指标)

### 数据源: session `test_20260511_030002` (主 runner P0→P1→P2 完整流程, 6 实例)

| 实例 | P1 退出 → P2 第 1 tap | 连续 tap 间隔 (popup→popup) |
|---|---|---|
| inst 0 | 3365 ms | 5156 / 6479 ms |
| inst 1 | 3477 ms | 4544 / 6405 ms |
| inst 2 | 3835 ms | 4593 / 6371 ms |
| inst 3 | 4253 ms | 5758 ms |
| inst 4 | **2371 ms** | 5600 / 4789 / 3954 / 4081 / 3337 / 3642 / **2824 ms** |
| inst 6 | 3711 ms | 4224 / 4173 / 3315 / 3226 ms |

**统计**:
- popup → tap 平均: **~3500 ms**
- 连续 popup 间隔平均: **~4500 ms** (含 popup 关闭动画 + 下一 popup 弹出延迟)
- 最快单 tap (inst 4 第 8 个): 2824 ms
- 用户感知: "10 秒才点击" (累计 2-3 个 round 加 sleep)

**V2 目标**: popup → tap **< 1000 ms** (-71%)

---

## 2. 单 round_perf 拆解

### 数据源: `round_perf.log` 最新生产数据 (P2 phase)

样本 (inst3 R32-R53):
```
R32: handle_frame=809.5ms  action_exec=809.5ms  round_total=1027ms
R34: handle_frame=980.4ms                       round_total=1217ms
R35: handle_frame=1035.0ms                      round_total=1426ms
R36: handle_frame=1381.0ms                      round_total=1611ms
R44: handle_frame=1723.6ms (screenshot=373.3)   round_total=1975ms
R53: handle_frame=3901.3ms (new_decision=2543.6) round_total=4254ms  ← spike
```

**6 段 ms 分布** (各段中位数估算):

| 段 | 中位数 | 备注 |
|---|---|---|
| screenshot (capture) | 0-450 ms | 大多 0ms (用 daemon cache), 偶尔 ldopengl 拿不到 retry 200-400ms |
| phash | 1-3 ms | 算 64-bit dHash, 几乎免费 |
| new_decision (mkdir + set_input) | 2-500 ms | 修了同步后 < 5ms, 但 set_input.imwrite spike 偶尔 500ms+ |
| handle_frame (5 路 perceive + policy) | **1000-1900 ms** | **大头**, 占 round 60-80% |
| action_exec - handle_frame | 0-1500 ms | 有 tap 时 sleep(0.4) + adb tap subprocess; 已修 sleep=0 |
| finalize (decision.json 写盘) | 100-300 ms | 含 imwrite 3-4 张图 |
| **round_total** | **1500-4200 ms** | 极端 spike 4 秒+ |

**V2 目标**:
- screenshot: 0-50ms (daemon 砍后直接 ldopengl)
- phash: 1ms (不变)
- new_decision: 1ms (JSONL append-only)
- handle_frame: **80-150 ms** (1 路 yolo + 黑名单查)
- action_exec: 30-150 ms (subprocess tap, 不 sleep)
- finalize: 1 ms (JSONL append, 没图)
- **round_total: 150-300 ms** (-80%)

---

## 3. perceive 内部 5 路 gather 分解

### 数据源: 代码分析 + 真实推理实测

P2 perceive 一个 round 跑:
- `_run_lobby_tpl`: cv2.matchTemplate × 2 模板 × ROI 624×297, scale=[1.0] → **30-60 ms**
- `_run_login_tpl`: cv2.matchTemplate × 2 模板 × ROI 960×378, scale=[1.0] → **40-80 ms**
- `_run_yolo`:
  - daemon cache hit (90%): 1 ms
  - daemon cache miss (10%): 同步跑 yolo 700 ms
- `_run_phash`: numpy dHash → 5-10 ms
- (memory_l1 异步 fire-and-forget, 不在 gather 里, 但**异步任务占 default executor**)
- `_run_quad`: LobbyQuadDetector.check (count yolo dets) → < 1 ms
- (`close_x_tpl / dismiss_btn_tpl`: 默认关, 0 ms)

**5 路 asyncio.gather 实际等最慢的那个**: **70-200 ms** (lobby_tpl + login_tpl 串行加 yolo cache hit)
**10% cache miss 时**: **700+ ms**

加 quad + memory bookkeeping + decision_log set_input (imwrite spike):
**总 perceive 实测 1000-1900 ms** (跟 round_perf 的 handle_frame 对得上)

**V2 目标**:
- 删 5 路 gather, 直接 1 次 yolo (ROI 优先 + 全屏 fallback)
- yolo cache 概念也砍, 同步调 yolo 实测 50 ms (CPU EP) / 91 ms (DML)
- 单 round perceive: **50-100 ms** (-95%)

---

## 4. ADB tap 延迟 (6 实例并发)

### 数据源: POC 实测 (前面 minitouch_bench.py)

| 方案 | 单实例 p50 | 6 并发 p50 | 备注 |
|---|---|---|---|
| **subprocess.run** | **125 ms** | **185 ms** | 当前生产用, fork × 6 → ADB server 串行队列 |
| MaaTouch 持久 shell | 0.02 ms (write) | < 30 ms (估) | 引入点错位置 bug + crash, **已禁用** |
| 持久 adb shell stdin | 实测反而 4-9x 慢 | - | deadlock, **已撤回** |
| **adb-shell pure-python (POC 待做)** | 50-100 ms | **真并发 < 80 ms** | TCP 持久, 无 fork. V2 完成后单独 POC. |

**V2 目标**: 继续 subprocess (子项目"ADB tap POC"延后到 v2 上线后).

---

## 5. 决策落盘 IO

### 数据源: decision_log.py 实现分析

**当前 (v1)**:
- 每决策一个目录, 内含:
  - `decision.json` (含 5 tier_evidence, 1-3 KB)
  - `input.jpg` (~25 KB)
  - `yolo_annot.jpg` (~30 KB, P2 跑了 yolo 才有)
  - `tap_annot.jpg` (~25 KB, 有 tap 才有)
  - `roi_*.jpg` (各 ROI 切片, 各 ~5 KB)
  - 模板拷贝 `tmpl_*.png` (各 ~2 KB)
- **平均每决策 ~50 KB** 写 5-10 个文件
- 6 实例 × 1.5 决策/秒 × 10h = 32 万决策/天 = **16 GB/天**

**V2 简版**:
- `decisions.jsonl` 1 行/决策, ~250 byte (含完整 7 时间戳)
- 6 实例 × 1.5 决策/秒 × 10h = 32 万 × 250 byte = **~80 MB/天** (-99.5%)

**V2 详版** (env=1 启用):
- 同 v1 但砍 yolo_annot / tap_annot / roi_*.jpg 注解图
- 保留 input.jpg + decision.json (tier_evidence)
- 平均 ~30 KB/决策, **~10 GB/天** (-37%)

---

## 6. WebSocket 流量

### 数据源: backend/runner_service.py + frontend useWebSocket.ts 分析

**当前 (v1)**:
- `_snapshot_loop` 每秒推全量 (6 实例 × ~5KB) = 30 KB/秒
- `GlobalLogHandler` log 推送无频率限制 (P2 高峰 100+ logs/sec, 各 ~200 byte = 20 KB/秒)
- 总 ws 流量约 **50 KB/秒** (running 期间)

**V2 目标**:
- snapshot 200ms debounce 仅在变化推 → 平均 5 KB/秒
- log 限频 10/秒 batch 推 → 2 KB/秒
- 总 **~7 KB/秒** (-86%)

---

## 7. 启动时间 (cold start)

### 数据源: backend 启动日志分析

**当前 (v1)**:
- ONNX runtime + yolo 模型加载: 800 ms
- OpenVINO + OCR 模型加载: 1200 ms
- OCR 首次推理 (无预热): **2700 ms** (warm 后 500 ms)
- vision_daemon 启动 + 6 capture threads: 200 ms
- 总 cold start: **~5 秒** (到能跑业务)

**V2 目标**:
- 砍 vision_daemon (-200 ms)
- 启动时预热 yolo + OCR (各 1 次 dummy 推理) → cold start 后首次业务调用即 warm
- 总 cold start **~3 秒** (-40%)

---

## 8. 12 实例并发风险点 (重构前调查)

实测当前 6 实例并发已经有以下瓶颈, 12 实例会放大:

### A. ADB server :5037 串行
- 6 并发 tap p50 185 ms (vs 单 125 ms)
- 12 并发预估 p50 **300-400 ms** (V2 仍走 subprocess, 风险**中**)
- 缓解: 灰度时观察, 真不行走 POC adb-shell

### B. ONNX 单 session 共享 + GPU lock
- 6 实例共享 1 session 排队, 12 实例排队更严重
- V2 改 **per-instance session** (12 × 200MB = 2.4 GB ≤ 8GB VRAM), 无锁真并发

### C. OpenVINO OCR 单线程锁
- 6 实例共享 1 lock 串行
- V2 用 OpenVINO `NUM_STREAMS=4`, 12 实例 OCR 并发收益明显

### D. ThreadPoolExecutor default 16 workers
- 6 实例 × 8 to_thread = 48 任务 > 16 workers, 排队 5+ 秒
- V2 启动时 set default executor max_workers=128 (按公式 `instance_count × 8 + cpu`)

### E. decision_log 后台 IO 池
- 当前 dlog_pool_workers=8, 6 实例够; 12 实例需 16
- V2 砍 IO 池, JSONL 同步 1 行 < 1ms

---

## 9. V2 验证标准 (Day 4 完成后实测对比)

每项指标 V2 实测必须达到, 否则不上线:

| 指标 | v1 baseline | V2 目标 | V2 实测 (待填) | 通过? |
|---|---|---|---|---|
| popup → tap p50 | 3500 ms | < 1000 ms | TBD | ⬜ |
| popup → tap p99 | 6500 ms | < 1500 ms | TBD | ⬜ |
| P2 round_total p50 | 1500 ms | < 250 ms | TBD | ⬜ |
| P2 round_total p99 | 4000 ms | < 500 ms | TBD | ⬜ |
| perceive (handle_frame) p50 | 1200 ms | < 150 ms | TBD | ⬜ |
| screenshot (1 实例) | 0-450 ms | < 100 ms | TBD | ⬜ |
| decision.jsonl 单条大小 | 50 KB | < 500 byte | TBD | ⬜ |
| WebSocket 流量 (running) | 50 KB/秒 | < 15 KB/秒 | TBD | ⬜ |
| cold start | 5 秒 | < 3.5 秒 | TBD | ⬜ |
| **6 实例 30 分钟稳定性** | 已知偶发 spike | 无 critical bug | TBD | ⬜ |
| **12 实例并发实测** (新) | 未测 | popup→tap < 1500 ms | TBD | ⬜ |

---

## 10. 不可量化但必须保持的功能 (regression test)

V2 上线前必须人工验证:

- [ ] 6 实例 P0 → P1 → P2 → P3a → P4 完整流程跑通
- [ ] 队员实例 P0 → P1 → P2 → P3b 跑通
- [ ] P5 等真人入队仍正常 (legacy 拷过来不动)
- [ ] 闪退恢复 (instance_state) 仍 work
- [ ] vm_watchdog 仍 work (LDPlayer 死了自动重启)
- [ ] WebSocket snapshot 推到前端, 实例卡片实时更新
- [ ] decision 日志能落盘 + 前端 archive 页面能展示 (简版字段)
- [ ] Memory tab 显示占位 "暂未启用" (不报错不白屏)
- [ ] Wizard / Settings / Recognition 页面仍能渲染
- [ ] Dashboard 中控台 Header 按钮状态正确 (running / standby)

---

## 11. 性能监控接入

V2 上线后, 用以下命令实时看真实数据:

```bash
# 1. round_perf.log 实时 tail
tail -f logs/<session>/round_perf.log | grep P2

# 2. JSONL 决策 (trace_id → 完整时序)
grep '"trace_id":"a3f8"' logs/<session>/decisions.jsonl | jq .

# 3. 统计 P2 round_total 中位数
cat logs/<session>/decisions.jsonl | jq '.ms.round_total' | sort -n | awk 'NR==int(NR*0.5){print "p50:", $0}'

# 4. tools/replay.py (V2 新增)
python tools/replay.py --trace=a3f8       # 时序拆解 + 画面回放
python tools/replay.py --session=<id> --metric=popup_to_tap --p50
```

---

## 12. Baseline 抓取方法 (可重现)

```python
# 从历史 session 抓 popup → tap timing
session = "test_20260511_030002"
decisions = fetch(f"/api/decisions?session={session}&limit=3000")

for inst in [0, 1, 2, 3, 4, 6]:
    events = sorted([x for x in decisions if x.instance == inst], key=lambda x: x.created)
    p2_taps = [x for x in events if x.phase == "P2" and x.outcome == "tapped"]
    for i, x in enumerate(p2_taps):
        # 找前一个事件作为 popup 出现起点
        prev = events[events.index(x) - 1]
        delta = (x.created - prev.created) * 1000
        print(f"inst {inst} tap #{i}: {delta:.0f}ms after {prev.outcome}")
```

```bash
# 从 round_perf.log 抓 P2 round_total
grep "ROUND/P2/" logs/<session>/round_perf.log | \
    awk -F'round_total_ms=' '{print $2}' | sort -n | \
    awk '{a[NR]=$1} END {print "p50:", a[int(NR*0.5)], "p99:", a[int(NR*0.99)]}'
```

---

## 总结

| 维度 | v1 现状 | V2 目标 | 收益 |
|---|---|---|---|
| popup → tap | 3.5 秒 | < 1 秒 | **-71%** |
| P2 round 总耗时 | 1.5 秒 | < 0.25 秒 | **-83%** |
| 决策落盘 IO | 16 GB/天 | 80 MB/天 (简版) | **-99.5%** |
| WebSocket 流量 | 50 KB/秒 | 7 KB/秒 | **-86%** |
| 代码量 backend/automation | 16000 行 | 3500 行 | **-78%** |
| **12 实例并发** | 未测 | 重点验证 | **新指标** |

**Day 0 baseline 完成. 等 V2 实施后 Day 4 填实测数据对比.**

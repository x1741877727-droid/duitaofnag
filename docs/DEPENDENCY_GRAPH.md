# Backend Dependency Analysis — 依赖图谱

**生成日期**: 2026-05-11

---

## 1. 核心数据指标

### 文件统计

| 类别 | 文件数 | 总行数 | 平均行数 | 最大文件 | 状态 |
|------|--------|--------|----------|---------|------|
| **automation/*.py** | 51 | ~16,000 | 314 | single_runner (1,735) | 砍/简化 |
| **api*.py** | 12 | ~3,400 | 283 | api.py (976) | 合并/砍 |
| **其他 (config/main/runner_service)** | 5 | ~1,300 | 260 | runner_service (1,145) | 简化 |
| **backend/ 总计** | 68 | 25,688 | 377 | - | - |

### 依赖度量

| 指标 | 数值 | 说明 |
|------|------|------|
| **高入度节点** (入度 ≥ 5) | 2 | runner_service (13), api (6) |
| **纯叶子节点** (入度=0, 出度=0) | 62 | 大多数独立模块, 可放心砍 |
| **低入度混合** (入度 1-4) | 4 | metrics, screenshot_collector, 等 |
| **循环依赖** | 0 | 无 (架构干净) |

---

## 2. 扇入/扇出矩阵

### 高风险节点 (高扇入 = 砍它影响大)

```
runner_service.py (入度 13)
├─ 直接 import: automation.{single_runner, adb_lite, decision_log, watchdogs, ...}
├─ 被 import 者: api.py, main.py, replay.py, debug_server.py, ...
├─ 砍/留: **简化** (1145 → 500 行)
└─ 影响范围: 全业务流 (P0-P5), 不能砍

api.py (入度 6)
├─ 直接 import: automation.{vision_daemon, overlay_installer, vm_watchdog, ...}
├─ 被 import 者: api_*.py 路由, main.py, debug_server.py
├─ 砍/留: **简化** (976 → 250 行)
└─ 影响范围: HTTP/WS 接口, 不能砍
```

### 孤立节点 (叶子 = 最安全砍)

```
待砍的叶子 (无人 import):
├─ api_oracle.py (202 行) — 0 用户
├─ image_preproc.py (59 行) — 叶子, 没人用
├─ wait_helpers.py (154 行) — asyncio.sleep 代替
└─ ... (共 25 个文件, 7,686 行)
```

---

## 3. 业务关键路径图 (P0-P5 调用链)

```
MultiRunnerService._run_phase_loop()
│
├─ P0_Accel (77 行)
│  └─ adb_lite.start_accelerator() ─── 保留
│
├─ P1_Launch (225 → 100 行)
│  ├─ adb_lite.start_app()
│  └─ yolo_dismisser.detect()
│     └─ [砍] motion_gate 80 行
│
├─ P2_Dismiss (35 + 346 + 89 + 166 → 80 行) ←← 核心优化
│  ├─ [砍] p2_perception.perceive() 5 路 gather (346 行)
│  │    ├─ yolo_dismisser
│  │    ├─ [砍] memory_l1.query()
│  │    ├─ screen_matcher
│  │    ├─ phash
│  │    └─ [砍] lobby_check.quad_detect()
│  │
│  ├─ [砍] p2_policy.decide() 3 tier (89 行)
│  │    └─ 改: 简单黑名单
│  │
│  ├─ p2_subfsm.step() → 80 行 (砍 streak 逻辑)
│  │    └─ lobby_count 二元判断
│  │
│  └─ action_executor.apply() → 150 行 (砍 verify 延迟)
│      └─ adb_lite.tap()
│
├─ P3a_TeamCreate / P3b_TeamJoin (80 + 78 行)
│  ├─ screen_classifier.classify()
│  ├─ screen_matcher.find_button()
│  └─ action_executor.apply()
│
├─ P4_MapSetup (76 行)
│  └─ action_executor.apply()
│
└─ P5_WaitPlayers (1,101 行) ← **完全留不动**
   ├─ ocr_dismisser.recognize()
   └─ action_executor.apply()


性能关键路径:
  P2_perception.perceive() 当前 1500ms
    ├─ yolo 50ms (保留)
    ├─ memory_l1.query 100ms (砍)
    ├─ screen_matcher 300ms (保留但简化)
    ├─ phash 150ms (保留)
    ├─ lobby_check.quad 800ms (砍, YOLO class 替代)
    └─ [5 路并发等最慢的] → 改串行 yolo 仅 50ms
  
  预期收益: 1500ms → 50ms (-96%)
```

---

## 4. 模块依赖强度矩阵

### 被 runner_service 直接调用的模块

| 模块 | 调用点 | 必需性 | 砍/留 |
|------|--------|--------|--------|
| single_runner.py | 主 phase 循环 | **必需** | 简化至 500 行 |
| adb_lite.py | tap / start_app | **必需** | 简化至 200 行 |
| decision_log.py | 每 round 记录 | **必需** | 拆分 (log + log_detailed) |
| watchdogs.py | 监视 phase 超时 | 可替代 | **砍** (vm_watchdog 代替) |
| ocr_dismisser.py | P2/P5 调用 | **必需** | 简化至 200 行 |
| screen_matcher.py | P3/P4 调用 | **必需** | 简化至 250 行 |
| instance_state.py | 状态追踪 | **必需** | 简化至 150 行 |
| recovery.py | 闪退恢复 | **必需** | 保留 (172 行) |

### 被 api.py 直接调用的模块

| 模块 | 端点 | 必需性 | 砍/留 |
|------|------|--------|--------|
| vision_daemon.py | /api/vision_daemon/* | 可替代 | **砍** (性能反而慢) |
| overlay_installer.py | /api/overlay/* | 无关 | **砍** (ACE 防护无需) |
| yolo_dismisser.py | /api/yolo/test | **必需** | 简化至 200 行 |
| screen_matcher.py | /api/templates/test | **必需** | 保留 |

---

## 5. 前端依赖的 API 路由图

```
Web Frontend
│
├─ App.tsx (启动检查)
│  └─ GET /api/health ————————→ backend/main.py
│     GET /api/start ————————→ runner_service.py
│     POST /api/stop ————————→ runner_service.py
│     WebSocket /ws ————————→ api.py (ConnectionManager)
│
├─ Dashboard.tsx (实时监控)
│  ├─ GET /api/runner/test_phase —→ api.py
│  ├─ GET /api/screenshot/{idx} —→ runner_service.py
│  └─ GET /api/tun/state ————————→ runner_service.py
│
├─ Settings.tsx
│  ├─ GET /api/settings ————————→ api.py
│  ├─ GET /api/accounts ————————→ api.py
│  ├─ GET /api/emulators ————————→ api.py
│  ├─ [砍] GET /api/perf/status —→ 返 410, 隐藏卡片
│  └─ [砍] GET /api/vision_daemon/stats → 返 410, 占位
│
├─ RecognitionView.tsx (模板/YOLO/OCR 管理)
│  ├─ GET /api/templates/list ———→ api_templates.py
│  ├─ POST /api/templates/upload —→ api_templates.py
│  ├─ POST /api/templates/test ———→ api_templates.py
│  ├─ GET /api/yolo/info ————————→ api_yolo.py
│  ├─ POST /api/yolo/test ————————→ api_yolo.py
│  ├─ GET /api/roi/list —————————→ api_roi.py
│  ├─ POST /api/roi/save —————————→ api_roi.py
│  └─ POST /api/roi/test_ocr ————→ api_roi.py
│
├─ LabelerView.tsx (样本标注)
│  ├─ GET /api/labeler/classes ——→ api_yolo_labeler.py
│  ├─ GET /api/labeler/list ———→ api_yolo_labeler.py
│  └─ POST /api/labeler/capture —→ api_yolo_labeler.py
│
├─ DataView.tsx (历史查询)
│  ├─ GET /api/sessions —————————→ api_decisions.py
│  └─ GET /api/decisions ————————→ api_decisions.py
│
└─ [砍] MemoryView.tsx (记忆库)
   └─ [砍] GET /api/memory/dedup —→ 返 410, 显示占位

关键: 19 个保留 endpoint schema 100% 不变
     4 个砍的 endpoint fetch 失败时前端兜底显示占位或隐藏卡片
```

---

## 6. 12 实例并发调用树

```
MultiRunnerService (并发 12 实例)
│
├─ Instance[0]._run_phase_loop()
│  └─ p2_dismiss.handle_frame()
│     └─ yolo_dismisser.detect(shot)
│        └─ [YOLO session lock] — 竞争点! (6 → 12 实例翻倍)
│
├─ Instance[1]._run_phase_loop()
├─ Instance[2]._run_phase_loop()
│  └─ ocr_dismisser.recognize()
│     └─ [OpenVINO async lock] — 需 async 模式
│
├─ Instance[3-11]._run_phase_loop()
│
└─ [concurrent 数据结构]
   ├─ decision_log.record() → ThreadPoolExecutor (8 → 16 workers)
   ├─ adb_lite.tap() → subprocess 6 → 12 并发 (瓶颈点!)
   └─ screencap_ldopengl.capture() → OpenGL fence 同步 (需验证)

关键风险:
  1. ADB 并发: 当前串行 185ms × 6, 12 实例 ? (POC 验证 adb-shell)
  2. YOLO lock: 单 session 加 lock 安全但慢, 考虑 per-instance session
  3. OCR async: OpenVINO 改 async 避免阻塞
```

---

## 7. 砍/留/迁 文件清单表

### 完全砍 (25 个, 7,686 行)

```python
# Vision/Memory/Perf 层
vision_daemon.py (453)      # daemon 无收益
memory_l1.py (1,326)        # 用户确认砍, < 5% 命中率
api_memory.py (166)         # 跟 memory_l1
watchdogs.py (316)          # 重复 vm_watchdog

# 决策/状态层
state_expectation.py (272)  # phash 足够
squad_state.py (180)        # 状态已在 runner_service
recorder_helpers.py (257)   # 合并到 log.py

# 弹窗处理
popup_dismiss.py (316)      # 已被 p2_subfsm 替代
popup_closer.py (112)       # 重复 yolo_dismisser
popup_specs.py (133)        # 注释式定义

# 性能/配置
perf_optimizer.py (1,009)   # 一次装机改常量
runtime_profile.py (290)    # 写死代码
api_perf_optimize.py (245)  # 跟 perf_optimizer
api_perf.py (248)           # mode 端点砍

# 指标/调试
metrics.py (379)            # 无人消费
debug_logger.py (163)       # logging 足够
image_preproc.py (59)       # 叶子无人用
screenshot_collector.py (151) # 测试工具

# OCR/识别
ocr_pool.py (219)           # OpenVINO 单线程
ocr_cache.py (206)          # cache 收益 < 5%
lobby_check.py (120)        # YOLO class 代替
template_test.py (162)      # 测试工具

# 配置/工具
rules_loader.py (156)       # 写死代码
roi_config.py (124)         # 常量定义
overlay_installer.py (268)  # 无关业务
wait_helpers.py (154)       # asyncio.sleep 代替

# API 层
api_oracle.py (202)         # 0 用户
```

### 简化重写 (15 个, 当前 8,700 → 目标 3,000)

```python
# 核心业务
single_runner.py (1,735 → 500)      # 删 RunContext 肥字段
p2_perception.py (346 → 0)          # 合并入 p2_dismiss
p2_policy.py (89 → 0)               # 合并入 p2_dismiss
p2_subfsm.py (166 → 80)             # 砍 streak 逻辑
p1_launch.py (225 → 100)            # 砍 motion gate

# 执行/识别
action_executor.py (382 → 150)      # 砍 verify 延迟
adb_lite.py (737 → 200)             # 砍 MaaTouch + 持久 shell
ocr_dismisser.py (432 → 200)        # 改同步调用
yolo_dismisser.py (342 → 200)       # 固定 conf
screen_matcher.py (465 → 250)       # 简化 multi_scale
screen_classifier.py (177 → 150)    # 简化

# 状态/日志
instance_state.py (194 → 150)       # 简化恢复
decision_log.py (846 → 280)         # 拆分 log + log_detailed

# API 层
api.py (976 → 250)                  # 删 wizard, 简化 ws
(其他 api_*.py 各自简化或合并)
```

### 保留 (12 个, 3,000+ 行)

```python
# 架构基础
phase_base.py (268)                 # PhaseHandler 抽象
runner_fsm.py (354)                 # v3 状态机
phase_base.py + phases/p*.py        # 基础设施

# 基础设施
screencap_ldopengl.py (335)         # 唯一快截图方案
vm_watchdog.py (150)                # 模拟器监控
_onnxruntime_patch.py (146)         # 性能补丁
recovery.py (172)                   # 闪退恢复
user_paths.py (116)                 # 路径管理
config.py (150)                     # 配置加载

# Phases (不砍)
p0_accelerator.py (77)
p1_launch.py (当前 225, 改 100 但保留框架)
p3a_team_create.py (80)
p3b_team_join.py (78)
p4_map_setup.py (76)
p5_wait_players.py (1,101)          # 用户确认留

# 基础设施文件
main.py (256)                       # 入口
runner_service.py (1,145 → 500)     # 简化但保留
replay.py (241)                     # 调试工具
```

---

## 8. 风险评估与缓解

### 高风险砍除 (需灰度验证)

| 砍这个 | 风险等级 | 谁会断 | 缓解方案 |
|------|---------|--------|---------|
| vision_daemon (453 行) | **中** | P2 perceive yolo cache | 实测对比: daemon miss 后业务 791ms vs 直接 yolo 50ms |
| memory_l1 (1,326 行) | **低** | P2 popup 检测准确率 | 黑名单防循环已验证, 命中率 < 5% 无感 |
| deferred_verify | **中** | action_executor 点击验证 | phash + 3 帧超时机制代替, 灰度测 1 周 |
| P2_perception 5 路 gather | **低** | 理论并发收益 | 实测串行 yolo 50ms 已足, 删 gather 省 1500ms+ 延迟 |

### 12 实例并发风险 (需 POC)

| 风险点 | 当前 (6 实例) | 目标 (12 实例) | 缓解 |
|--------|---------------|----------------|------|
| YOLO session + lock | 50ms | 100ms+ ? | POC 验证 per-instance session |
| ADB tap 并发 | 6 串行 185ms | 12 并发 ? | POC adb-shell library 10min 稳定性测试 |
| OCR async | 500-800ms | 1000ms+ ? | OpenVINO async infer 避免阻塞 |
| 磁盘 IO (decision log) | 5MB/day | 10MB/day | 异步写 + batch 足够 |

---

## 9. 重构后的导入图 (V2 结构)

```
backend/automation_v2/
├── runner.py (新, 核心循环)
├── ctx.py (新, 运行上下文)
├── phases/
│   ├── p0_accel.py (拷贝, 不动)
│   ├── p1_launch.py (新, 100 行)
│   ├── p2_dismiss.py (新, 80 行, 合并 4 文件)
│   ├── p3a/p3b/p4 (新, 简化)
│   └── p5_wait_players.py (拷贝, 不动)
├── perception/
│   ├── yolo.py (新, 优化, 接口稳定)
│   ├── ocr.py (新, 简化)
│   └── matcher.py (保留, 简化)
├── action/
│   ├── tap.py (新, 抽象 AdbTap)
│   └── executor.py (新, 简化)
└── log/
    ├── decision_simple.py (新)
    └── decision_detailed.py (新, env 切换)

backend/automation/  ← 旧版, 完全不动 (生产用)
```

**入口选择**:
```python
# backend/runner_service.py
RUNNER_VER = os.environ.get("GAMEBOT_RUNNER_VERSION", "v1")
if RUNNER_VER == "v2":
    from .automation_v2.runner import SingleRunner
else:
    from .automation.single_runner import SingleRunner  # 旧
```

---

## 结论

- **砍**: 25 个文件 7,686 行 (最安全, 都是叶子/冗余)
- **简**: 15 个文件 8,700 → 3,000 行 (-65%, 删不必要复杂度但保核心)
- **留**: 12 个文件 3,000+ 行 (架构基础, 已设计好)
- **新**: v2/ 结构并行, env 切换灰度, 1 周后砍旧版

**预期性能**:
- P2 round: 2,900ms → < 200ms (-93%)
- popup→tap: 2-10s → < 1s (-90%)
- decision.jsonl: 50KB → 200byte (-99%)


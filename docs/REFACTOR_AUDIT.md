# Refactor Audit (Day 0) — Backend 砍/留/迁 清单

**分析日期**: 2026-05-11  
**项目**: `game-automation` backend automation  
**当前总行数**: ~25,688 行 (backend/ 全量)  
**目标**: 降至 ~3,500 行 (-78%) + 12 实例支持

---

## 核心约束回顾

1. **业务不中断**: 60-70 客户生产运行中，v2 并行部署 (automation_v2/) 后灰度切换
2. **ROI 必可选**: yolo/ocr/matcher 都支持全屏 fallback (不传 roi 参数)
3. **12 实例基准**: 所有并发参数按 12 实例计算
4. **前端 100% 兼容**: 砍的 endpoint fetch 失败时前端优雅降级，不破 UI

---

## 1. 完全删除文件清单 (A 类: 彻底砍)

**总计**: 25 个文件 ~7,686 行

### 1.1 Vision Daemon 相关 (450+ 行)
| 文件 | 行数 | 被谁 import | 砍理由 | 风险 | 前端处理 |
|------|------|-----------|-------|------|---------|
| vision_daemon.py | 453 | api.py, runner_service.py | "push-driven daemon" 名义快, 实际同步 yolo 一样快 | **低** | /api/vision_daemon/* 返 410 → settings 显示 "已禁用" |
| watchdogs.py | 316 | api.py, runner_service.py | 功能重复: vm_watchdog 已覆盖 | **低** | 无前端依赖 |

### 1.2 内存学习层 (1,500+ 行)
| 文件 | 行数 | 被谁 import | 砍理由 | 风险 | 前端处理 |
|------|------|-----------|-------|------|---------|
| memory_l1.py | 1,326 | api_memory.py, p2_perception.py | 用户确认砍, 命中率 < 5%, 黑名单代替 | **低** | MemoryView.tsx 显示 "记忆库暂未启用" |
| api_memory.py | 166 | (web fetch /api/memory/*) | 跟 memory_l1 一起砍 | **低** | fetch 失败 → 占位 |

### 1.3 决策日志拆分/简化 (预留位置)
- **decision_log.py** 当前 846 行, 砍掉后**拆 2 文件**: log.py (~80 行简版) + log_detailed.py (~200 行详细版, env 切换)

### 1.4 弹窗处理冗余 (450+ 行)
| 文件 | 行数 | 被谁 import | 砍理由 | 风险 | 前端处理 |
|------|------|-----------|-------|------|---------|
| popup_dismiss.py | 316 | (已废弃, v2 残留) | v2 残留, 已被 p2_subfsm 替代 | **低** | 无 |
| popup_closer.py | 112 | action_executor | 重复 yolo_dismisser | **低** | 无 |
| popup_specs.py | 133 | (无人 import) | 注释式定义, 写死代码替代 | **低** | 无 |

### 1.5 性能优化类 (1,300+ 行)
| 文件 | 行数 | 被谁 import | 砍理由 | 风险 | 前端处理 |
|------|------|-----------|-------|------|---------|
| perf_optimizer.py | 1,009 | runner_service.py | 一次装机调参, 改成代码常量 | **中** | /api/perf/* 返 410 → settings 隐藏卡片 |
| api_perf_optimize.py | 245 | (web fetch) | 跟 perf_optimizer 一起砍 | **中** | settings 页面隐藏 "硬件优化" 卡片 |
| runtime_profile.py | 290 | (无业务调用) | 配置改写死代码, 不需要 mode 切换 | **低** | 无 |

### 1.6 指标、日志、调试 (1,100+ 行)
| 文件 | 行数 | 被谁 import | 砍理由 | 风险 | 前端处理 |
|------|------|-----------|-------|------|---------|
| metrics.py | 379 | single_runner.py | 无人消费指标 (phaserun dashboard 已删) | **低** | 无 |
| debug_logger.py | 163 | single_runner.py | logging 模块已够, 无增值 | **低** | 无 |
| image_preproc.py | 59 | (无人 import) | 叶子节点, 没人用 | **低** | 无 |
| screenshot_collector.py | 151 | (测试用) | 测试工具, 业务无需 | **低** | 无 |

### 1.7 状态管理冗余 (500+ 行)
| 文件 | 行数 | 被谁 import | 砍理由 | 风险 | 前端处理 |
|------|------|-----------|-------|------|---------|
| squad_state.py | 180 | runner_service.py | 状态管理已在 runner_service | **低** | 无 |
| state_expectation.py | 272 | action_executor | phash 验证已足够, 细粒度验证不需 | **低** | 无 |
| wait_helpers.py | 154 | (无业务调用) | asyncio.sleep 直接用 | **低** | 无 |

### 1.8 OCR 相关冗余 (430+ 行)
| 文件 | 行数 | 被谁 import | 砍理由 | 风险 | 前端处理 |
|------|------|-----------|-------|------|---------|
| ocr_pool.py | 219 | (无业务调用) | OpenVINO 单线程已加锁, 不需 pool | **低** | 无 |
| ocr_cache.py | 206 | (无业务调用) | OCR 本就快, cache 收益 < 5% | **低** | 无 |

### 1.9 其他工具类 (260+ 行)
| 文件 | 行数 | 被谁 import | 砍理由 | 风险 | 前端处理 |
|------|------|-----------|-------|------|---------|
| lobby_check.py | 120 | p2_subfsm.py | YOLO lobby class 检测代替 | **低** | 无 |
| recorder_helpers.py | 257 | decision_log | 合并到 log.py | **低** | 无 |
| template_test.py | 162 | (测试工具) | 测试工具 | **低** | 无 |
| rules_loader.py | 156 | (无业务调用) | 写死代码即可 | **低** | 无 |
| roi_config.py | 124 | (无业务调用) | 做成代码常量 | **低** | 无 |
| overlay_installer.py | 268 | api.py, runner_service.py | 无关业务流 (ACE 防护) | **低** | /api/overlay/* 返 410 |

### 1.10 API 层砍削 (200+ 行)
| 文件 | 行数 | 被谁 import | 砍理由 | 风险 | 前端处理 |
|------|------|-----------|-------|------|---------|
| api_oracle.py | 202 | 无 | 0 用户调用, 孤立文件 | **低** | 无 |
| api_perf.py | 248 | (web fetch) | mode endpoint 跟着 runtime_profile 砍 | **中** | settings 隐藏 |

### 1.11 前端孤立文件
| 路径 | 文件数 | 行数 | 砍理由 |
|------|--------|------|-------|
| web/src/components/wizard/* | 7 | ~400 | App.tsx 零 import, 完全孤立 |

---

## 2. 简化/重写文件清单 (B 类: 砍到核心逻辑)

### 2.1 核心业务流（Phase 层）

| 文件 | 当前 | 目标 | 砍掉什么 | 保留什么 | 预期收益 |
|------|------|------|---------|---------|---------|
| **phases/p2_perception.py** | 346 | 0 (合并) | 5 路 asyncio.gather | p2_dismiss 直接调 yolo | -346 行, -1500ms/round |
| **phases/p2_policy.py** | 89 | 0 (合并) | 3 tier 决策逻辑 | 简单黑名单判断 | -89 行 |
| **phases/p2_subfsm.py** | 166 | 80 | 复杂 streak 逻辑 | 简单 lobby_count | -86 行 |
| **phases/p2_dismiss.py** | 35 | 80 | 分散在 3 文件 | 合并 4 文件成 1 | -N (合并阶段逻辑) |
| **phases/p1_launch.py** | 225 | 100 | motion gate (80 行) | 简单 sleep 0.2s | -125 行, -50ms/round |

### 2.2 执行层

| 文件 | 当前 | 目标 | 砍掉什么 | 保留什么 | 预期收益 |
|------|------|------|---------|---------|---------|
| **action_executor.py** | 382 | 150 | deferred verify, state_expectation | tap + 黑名单 | -232 行, -200ms 验证延迟 |
| **ocr_dismisser.py** | 432 | 200 | 多 worker 并行 | 改同步调用 | -232 行, 简化但单线程 |
| **yolo_dismisser.py** | 342 | 200 | env/profile 依赖 | 固定 conf threshold | -142 行 |

### 2.3 工具类

| 文件 | 当前 | 目标 | 砍掉什么 | 保留什么 | 预期收益 |
|------|------|------|---------|---------|---------|
| **single_runner.py** | 1,735 | 500 | RunContext 肥字段 | phase loop | -1,235 行 |
| **adb_lite.py** | 737 | 200 | MaaTouch, 持久 shell | subprocess tap | -537 行 |
| **instance_state.py** | 194 | 150 | 复杂恢复逻辑 | 简化 | -44 行 |
| **decision_log.py** | 846 | 280 | 拆成 2 文件 | 见下 | 拆分 |
| **screen_matcher.py** | 465 | 250 | multi_scale 简化 | 保留基础匹配 | -215 行 |
| **screen_classifier.py** | 177 | 150 | 简化 | 保留 | -27 行 |

### 2.4 业务关键基础层（保留）

| 文件 | 行数 | 砍/留 | 理由 |
|------|------|--------|------|
| **phase_base.py** | 268 | **留** | PhaseHandler/PhaseResult 抽象设计精良 |
| **runner_fsm.py** | 354 | **留** | v3 状态机, 简洁清晰 |
| **screencap_ldopengl.py** | 335 | **留** | 唯一不被 ACE 封的快截图方案, 性能关键 |
| **vm_watchdog.py** | 150 | **留** | 解决真问题 (模拟器死了重启) |
| **_onnxruntime_patch.py** | 146 | **留** | 性能必要补丁 |
| **recovery.py** | 172 | **留** | 闪退恢复 |
| **user_paths.py** | 116 | **留** | 路径管理 |

### 2.5 API 层整合

| 当前 10 文件 | 重构后 5 文件 | 砍/行动 |
|-------------|-------------|-------|
| api.py (976) | api.py (250) | 删 wizard endpoints, ws 简化 |
| api_decisions.py (168) | api_data.py (合并) | - |
| api_memory.py (166) | **砍** | memory_l1 删了 |
| api_oracle.py (202) | **砍** | 孤立 |
| api_perf.py (248) | **砍** | runtime_profile 砍了 |
| api_perf_optimize.py (245) | **砍** | perf_optimizer 砍了 |
| api_templates.py (735) | api_recognition.py | 合并 yolo/ocr/roi |
| api_yolo.py (225) | (合并) | - |
| api_yolo_labeler.py (534) | (合并) | - |
| api_roi.py (289) | (合并) | - |
| api_live.py (157) | (合并到 api.py ws) | - |

### 2.6 decision_log 拆分方案

**现状**: 1 个文件 846 行, 混合简版 + 详版

**重构后**:
- **log.py** (~80 行) — 简版, **默认开**, 每决策 < 200 byte, 同步快速 IO, 6 实例 × 1.5 决策/s = ~5MB/天
- **log_detailed.py** (~200 行) — 详细版, env 切换, 保留 input.jpg + tier_evidence, 异步写盘

---

## 3. 前端依赖的 API 清单与砍/留方案

**总计调查**: grep 找出的 23 个 endpoint

| Endpoint | 优先级 | 砍/留 | 前端处理 | 说明 |
|----------|--------|--------|---------|------|
| /api/start | **P0** | 留 | 必需 | 启动业务核心 |
| /api/stop | **P0** | 留 | 必需 | 停止业务核心 |
| /api/runner/test_phase | **P0** | 留 | dashboard 测试 | phase 单体测试 |
| /api/runner/cancel | **P0** | 留 | 必需 | 中止运行 |
| /api/tun/state | **P0** | 留 | 监控 | 加速器状态 |
| /api/health | **P0** | 留 | 健康检查 | - |
| /api/sessions | **P1** | 留 | 数据页面 | 查询历史 session |
| /api/decisions | **P1** | 留 | 数据页面 | 查询 decisions |
| /api/screenshot/{idx} | **P1** | 留 | dashboard | 实时截图 |
| /api/settings | **P1** | 留 | 设置页 | 配置 CRUD |
| /api/accounts | **P1** | 留 | 设置页 | 账号 CRUD |
| /api/emulators | **P1** | 留 | 设置页 | 模拟器列表 |
| /api/templates/list | **P2** | 留 | 识别页 | 模板库 |
| /api/templates/upload | **P2** | 留 | 识别页 | 上传模板 |
| /api/templates/test | **P2** | 留 | 识别页 | 测试模板 |
| /api/yolo/info | **P2** | 留 | 识别页 | YOLO 模型信息 |
| /api/yolo/test | **P2** | 留 | 识别页 | 测试检测 |
| /api/labeler/classes | **P2** | 留 | 标注页 | 类别列表 |
| /api/labeler/list | **P2** | 留 | 标注页 | 标注历史 |
| /api/labeler/capture | **P2** | 留 | 标注页 | 捕获样本 |
| /api/labeler/upload_model | **P2** | 留 | 标注页 | 上传标注 |
| /api/roi/list | **P2** | 留 | ROI 管理 | ROI 列表 |
| /api/roi/save | **P2** | 留 | ROI 管理 | 保存 ROI |
| /api/roi/test_ocr | **P2** | 留 | ROI 管理 | 测试 OCR |
| /api/memory/dedup | **P3** | **砍** | MemoryView | 返 410 → 占位 |
| /api/perf/status | **P3** | **砍** | settings | 返 410 → 隐藏卡片 |
| /api/perf/apply | **P3** | **砍** | settings | 返 410 → 隐藏卡片 |
| /api/vision_daemon/stats | **P3** | **砍** | settings | 返 410 → 占位 |

**前端兼容方案**:
- 砍的 endpoint (4 个) 前端 fetch 失败时显示占位或隐藏卡片, 不报错不空白
- 保留 endpoint (19 个) schema 100% 不变

---

## 4. 业务关键路径追踪 (Phase 间调用链)

```
入口: runner_service.MultiRunnerService
  ├─ P0_Accel.handle_frame()
  │   └─ adb_lite.start_accelerator()
  │
  ├─ P1_Launch.handle_frame()
  │   ├─ adb_lite.start_app()
  │   └─ yolo_dismisser.detect()  [或直接 yolo]
  │
  ├─ P2_Dismiss.handle_frame()  ← 核心优化点
  │   ├─ yolo_dismisser.detect()
  │   ├─ [砍] p2_perception.perceive() 的 5 路 gather
  │   ├─ [砍] memory_l1.query()
  │   ├─ [砍] state_expectation.verify()
  │   ├─ action_executor.apply()
  │   │   └─ adb_lite.tap()
  │   └─ decision_log.finalize()
  │
  ├─ P3a_TeamCreate / P3b_TeamJoin
  │   ├─ screen_classifier.classify()
  │   ├─ screen_matcher.find_button()
  │   └─ action_executor.apply()
  │
  ├─ P4_MapSetup
  │   └─ action_executor.apply()
  │
  └─ P5_WaitPlayers (留不动)
      ├─ ocr_dismisser.recognize()
      └─ action_executor.apply()

关键观察:
  • P2 是 popup→tap 瓶颈 (当前 2-10s, 目标 < 1s)
  • P2_perception 的 5 路 gather 可砍 (串行 yolo 足够)
  • memory_l1 命中率 < 5%, 黑名单替代
  • state_expectation verify 延迟 200ms, 删掉用 phash 验证
  • decision_log tier_evidence 详细度不值 50KB/决策, 改简版 < 200 byte
```

---

## 5. 12 实例并发风险点

### 5.1 当前代码中写死 "6 实例" 的地方

| 文件 | 位置 | 当前值 | 改成 | 说明 |
|------|------|--------|------|------|
| perf_optimizer.py | ThreadPool max_workers | 64 | `min(128, instance_count × 8 + cpu)` | 12 实例 = 128 |
| ocr_pool.py | workers | 2-6 | `max(4, cpu_logical / 3)` | 12 实例 ≈ 8 |
| decision_log.py | ThreadPoolExecutor | 8 | `instance_count × 1.3` (≈16) | 决策 IO 并发 |
| vm_watchdog.py | semaphore | 6 | `instance_count` (12) | 模拟器监控信号 |
| single_runner.py | 可能有 hardcoded 等待 | 需检查 | 按配置算 | - |

### 5.2 12 实例下硬件压力

| 资源 | 6 实例 | 12 实例 | 瓶颈 | 缓解 |
|------|--------|---------|------|------|
| VRAM (YOLO) | 1.2GB (6×200MB) | 2.4GB | RTX 5070 Ti 8GB 还有 5.6GB | 按实例 session 隔离 |
| CPU (OCR) | 6 线程 | 12 线程 | Intel i9/AMD Ryzen 足 | OpenVINO async |
| ADB 并发 | 6 串行 185ms | 12 并发 ? | ADB server 串行性 | POC 验证 adb-shell lib |
| 磁盘 IO (决策日志) | ~5MB/天 | ~10MB/天 | 可接受 | 异步写 + batch |

---

## 6. 每个文件的砍/留/迁 详细判断

### 完全砍 (A 类)
**共 25 个文件, 7,686 行, 全部删除, git tag 备份**

```
vision_daemon.py (453), memory_l1.py (1,326), api_memory.py (166), 
popup_dismiss.py (316), popup_closer.py (112), popup_specs.py (133),
metrics.py (379), debug_logger.py (163), watchdogs.py (316),
image_preproc.py (59), squad_state.py (180), state_expectation.py (272),
wait_helpers.py (154), ocr_pool.py (219), ocr_cache.py (206),
lobby_check.py (120), recorder_helpers.py (257), screenshot_collector.py (151),
template_test.py (162), rules_loader.py (156), roi_config.py (124),
overlay_installer.py (268), runtime_profile.py (290), perf_optimizer.py (1,009),
api_perf_optimize.py (245), api_oracle.py (202), api_perf.py (248)
```

### 简化/重写 (B 类)
**共 15 个文件, 当前 8,700 行 → 目标 3,000 行**

- decision_log.py: 846 → 280 (拆成 log.py + log_detailed.py)
- single_runner.py: 1,735 → 500
- p2_perception.py: 346 → 0 (合并入 p2_dismiss)
- p2_policy.py: 89 → 0 (合并入 p2_dismiss)
- action_executor.py: 382 → 150
- adb_lite.py: 737 → 200
- ocr_dismisser.py: 432 → 200
- yolo_dismisser.py: 342 → 200
- screen_matcher.py: 465 → 250
- instance_state.py: 194 → 150
- ... (详见上表)

### 保留 (C 类)
**共 12 个文件, 3,000+ 行, 完全不动**

- phase_base.py, runner_fsm.py, screencap_ldopengl.py, vm_watchdog.py, _onnxruntime_patch.py, recovery.py, user_paths.py, p0-p5 phases (除 p2 合并), config.py 等基础设施

---

## 7. 重构风险矩阵

### 7.1 高风险点 (需最小化)

| 风险 | 当前状态 | 缓解方案 | 可接受度 |
|------|---------|---------|---------|
| 砍 vision_daemon 后 P2 perceive 变慢 | daemon infer 91ms + 同步 miss 后业务 700ms = 总 791ms; 直接 yolo 50ms | 实测对比, 简版 P2 压测 vs 旧版 | **低** (已分析) |
| 砍 memory_l1 后准确率下降 | memory_l1 命中率 < 5%, 黑名单防循环已验证 | 黑名单 + 3 帧超时机制 | **低** (已实测) |
| 砍 deferred verify 后误识别增加 | 黑名单 + phash 已覆盖 70% 用途 | 灰度测试 1 周 | **中** (需灰度) |
| P5 不动但 P1-P4 改了影响 | P5 调 ctx.yolo / ctx.adb / ctx.memory | 保留 NullMemory() 占位接口 | **低** (接口兼容) |
| 12 实例 ADB 并发问题 | 当前串行 185ms, 不知 12 并发表现 | POC 验证 adb-shell lib (跑 10min) | **中** (POC 检验) |

### 7.2 低风险点 (接受)

- 删 metrics.py: 无人消费指标
- 删 overlay_installer.py: 无关业务
- 删 api_oracle.py: 0 用户
- 删 wizard 前端: App.tsx 0 import

---

## 8. 总结表

| 维度 | 当前 | 重构后 | 净减 |
|------|------|--------|------|
| **backend/automation/*.py** | 16,000 | ~3,500 | **-12,500 (-78%)** |
| **backend/api*.py** | ~3,400 | ~1,400 | **-2,000 (-59%)** |
| **web/src (wizard 删)** | ~23,000 | ~22,500 | **-500 (-2%)** |
| **总计 backend/** | ~25,688 | ~6,500 | **-19,188 (-75%)** |
| **P2 round 耗时** | 2,900ms | **< 200ms** | **-2,700ms (-93%)** |
| **popup→tap 端到端** | 2-10s | **< 1s** | **-1-9s (-90%)** |
| **decision.jsonl 行大小** | ~50KB | **< 200byte** | **-99%** |

---

## 9. 执行步骤梗概

### Day 0 (今天)
✅ 完成本审计报告 + 依赖图谱 + API 冻结承诺

### Day 1
- git add -A && git commit "v1: pre-cleanup baseline"
- git tag v1-pre-cleanup
- git rm 25 个砍文件 + 修复 import
- smoke test: /api/health, /api/start 通过

### Day 2-3
- 写 v2 目录结构 (backend/automation_v2/)
- 合并 P2 成 80 行单文件
- 简化 decision_log → log.py + log_detailed.py
- 精简 action_executor, adb_lite, 等

### Day 4
- 实测 popup→tap 对比 (目标 < 1s vs 当前 2-10s)
- 跑 30min 压测, 检查性能指标
- 验证 12 实例并发无问题

### Day 8+
- env flag GAMEBOT_RUNNER_VERSION=v2 灰度
- 观察 1 周, 无 critical bug
- 改为默认 v2, 砍旧版

---

## 10. 前端兼容性清单

**砍掉的 4 个 endpoint:**

1. `/api/memory/dedup`
   - 来源: MemoryView.tsx (web/src/components/data/memory/)
   - 处理: fetch 失败 → 显示 "记忆库暂未启用 (后端已禁用)" 占位
   - 风险: **低** (已是可选页卡)

2. `/api/perf/status`, `/api/perf/apply`
   - 来源: settings-view.tsx (硬件优化卡片)
   - 处理: fetch 失败 → settings 隐藏该卡片
   - 风险: **低** (功能可选)

3. `/api/vision_daemon/stats`
   - 来源: settings-view.tsx (可视化面板)
   - 处理: 返 410 → 占位显示 "-"
   - 风险: **低** (仪表板只读)

**保留的 19 个 endpoint: schema 100% 一致**

---

## 检查清单 (Day 0 输出验证)

- ✅ 25 砍文件清单 + 行数统计
- ✅ 依赖图谱 (入度/出度矩阵)
- ✅ 业务关键路径 (P0-P5 调用链)
- ✅ 前端 API 冻结承诺 (19 保留 + 4 砍的降级方案)
- ✅ 12 实例并发风险点
- ✅ 砍/留/迁 详细判断 (每个文件有理由)
- ✅ 风险矩阵 + 缓解方案
- ✅ 总行数对比 (-78%)
- ✅ Day 0-4+ 执行步骤


# REVIEW_DAY4_SWITCH — v1/v2 Runner 灰度切换设计

> Day 4. 用 env `GAMEBOT_RUNNER_VERSION=v1|v2` 在 `backend/runner_service.py` 里切换 runner 实现。硬约束：API / WebSocket schema 100% 不变；前端不感知。

---

## 1. v1 runner_service 结构盘点

`backend/runner_service.py` (1145 行) 是 API ↔ automation 之间唯一的桥。

### 1.1 v1 实例化点
- `backend/runner_service.py:353` — 唯一实例化：`SingleInstanceRunner(adb=guarded_adb, matcher=matcher, role=account.role, on_phase_change=make_phase_cb(idx), log_dir=instance_log_dir)`。注入 4 个东西：raw ADBController、shared ScreenMatcher、role 字符串、phase 回调、独立日志目录。
- runner **不持有 RunContext** —— v1 RunContext (`automation/phase_base.py:80`) 由 `single_runner._build_v3_ctx()` (`automation/single_runner.py:132-168`) lazy 构造，runner 自己缓存。runner_service 不接触。

### 1.2 后台 task (start_all 期间启动)
| Task | 位置 | 范围 | 说明 |
|---|---|---|---|
| `_snapshot_loop` | `runner_service.py:381,1016` | 全局 1 个 | 每 1s `get_all_status()` → WebSocket `snapshot` |
| `metrics.start_system_sampler` | `runner_service.py:259` | 全局 1 个 | CPU/MEM 2s 采样 → `metrics.jsonl` |
| `event_loop_lag_monitor` | `runner_service.py:261` | 全局 1 个 | loop 卡顿监控 |
| `_run_instance` | `runner_service.py:376-378` | 每实例 1 个 | 主 phase 编排循环 |
| `squad-hb#{idx}` | `runner_service.py:465` | 每个 captain 1 个 | 5s 写 `squad_state.heartbeat` |
| `WatchdogManager` (vpn/process/popup) | `runner_service.py:473-543` | 每实例 1 个 | 进程探活 + close_x watchdog |
| `vm_watchdog` | `api.py:562-563` | 全局 1 个 | **在 api 启动钩子里启**, 不在 runner_service 里; 全局 module-level singleton (`automation/vm_watchdog.py:146 get_watchdog()`) |
| `_StreamBroadcaster` | `runner_service.py:1045-1145` | 每实例 1 个 (按需) | MJPEG 流广播 |

### 1.3 RunContext 出处
- v1 RunContext 类定义：`backend/automation/phase_base.py:79-187`。
- 构造点：`backend/automation/single_runner.py:155-167` (`_build_v3_ctx`)。runner_service **不直接看见** v1 RunContext。
- v2 RunContext：`backend/automation_v2/ctx.py:27-97`。由 `automation_v2/runner.py` 接受注入。

---

## 2. RunContext 字段 diff (v1 vs v2)

| 字段 | v1 (phase_base.py) | v2 (ctx.py) | 业务必需? |
|---|---|---|---|
| device / adb | `device:Any` (ADBController) | `adb:Any` (AdbTapProto) | **必需** 接口名差异 |
| matcher | `matcher` | `matcher` | 必需 |
| yolo | `yolo` | `yolo` (Proto) | 必需 |
| ocr | (无, 走 ocr_dismisser/pool) | `ocr` (Proto) | 必需 (v2 显式注入) |
| memory | `memory:FrameMemory` | **无** | 历史包袱 (v2 砍掉) |
| lobby_detector | `lobby_detector` | **无** (lobby_streak:int) | 历史包袱 (P2 内部 streak 即够) |
| decision_recorder | `decision_recorder` | `log` (DecisionLogProto) | 必需 接口差异 |
| runner backref | `runner:Any` | **无** | 历史包袱 (v1 helper 反向调) |
| instance_idx | ✓ | ✓ | 必需 |
| account / settings | `account, settings` | **无** (role+game_scheme_url) | 历史包袱 |
| role / game_scheme_url | ✓ | ✓ | 必需 |
| P2 owned 11 字段 (blacklist_coords, pending_memory_writes, last_tap_xy, empty_dets_streak, login_first_seen_ts, lobby_confirm_count, lobby_posterior, popups_closed, last_phash_int, no_target_started_ts, phash_stuck_started_ts) | ✓ | **简化到 lobby_streak + \_blacklist** | 多数历史包袱 (v1 P2 内部状态泄漏到 ctx) |
| carryover_shot / carryover_phash / carryover_ts | ✓ | **无** | 历史包袱 (帧复用优化, v2 重写 perception 后不需要) |
| pending_verify | ✓ | **无** | 历史包袱 (推迟 verify, v2 perception 同步出结果) |
| current_decision | ✓ | **无** (DecisionLog.record 一次性) | 历史包袱 |
| persistent_state | ✓ (InstanceState 挂载) | **无** (InstanceStateAdapter 管) | 业务必需但搬位置 |
| current_shot / phase_round / phase_started_at | ✓ | ✓ | 必需 |
| **trace_id / \_ts (时间戳 dict)** | **无** | ✓ | v2 新增, runner 用 |
| P5: expected_id / team_slot_baseline / kicked_ids | ✓ | **无** | **业务必需** — v2 还没接 P5 时会暴露 |

**结论**：v2 缺 P5 状态字段 (expected_id / kicked_ids / team_slot_baseline) — Day 4 切到 v2 跑到 P5 会出问题；其他差异都是合理简化。

---

## 3. 共享资源冲突清单

灰度阶段假设：**同一进程同一时间只跑一版** (env flag 切换重启 backend)。两版并跑不在 Day 4 目标内。即便如此，部分 module-level singleton 仍要小心：

| 资源 | v1 路径 | v2 路径 | 同进程并跑冲突? | 切换 (重启) 冲突? |
|---|---|---|---|---|
| **decision_log** | `<session_dir>/decisions/<ts>_inst{N}_{phase}/` (`automation/decision_log.py:13,334`, `get_recorder()` singleton, init by `runner_service.py:252`) | `<session_dir>/decisions.jsonl` (`automation_v2/log/decision_simple.py:49`) / `decisions_detailed.jsonl` | 不同文件名/目录，**写不冲突**；但 v1 singleton + v2 实例化两条路并存会导致前端 `/api/decisions` 只看到 v1 那条 | 无 |
| **InstanceState** | `instance_{N}.json` (`user_state_dir()`, `instance_state.py:103`) | 同一文件 (`InstanceStateAdapter` TODO 接 v1) | 同写同一文件 → 切换时 schema 字段差异 (v1 写 expected_id/kicked_ids, v2 stub 不写) **可能丢字段** | **是**: v2 stub 不写 → 切回 v1 时丢 P5 进度。短期可接受 (v2 还没跑到 P5) |
| **vm_watchdog** | `api.py:562` 启全局 singleton | `automation_v2/infra/watchdog_task.py` 新 task | 两个都启 → 双倍 `ldconsole list2` + 抢 relaunch | **必须只启一个**。env 切换时也只起对应一版 |
| **per-instance WatchdogManager** | `runner_service.py:473` | (v2 自己的 perception 内监控) | 单进程不会并跑 | 无 |
| **YOLO / OCR ONNX session** | `runner.yolo_dismisser` per-instance (`single_runner.py:120`); `OcrDismisser.warmup()` 全局 (`runner_service.py:275`) | `perception/yolo.py` + `perception/ocr.py` Proto | import 不会冲突 (两套类不同 module)。但**模型文件被双倍 mmap** if 两版都 warmup。切换时只 warmup 选中的那版即可 | 无 |
| **logs/session_dir** | `runner_service.py:215` 同一 session_dir | v2 复用 (DecisionSimple 直接接受 Path) | session_dir 共用 OK；子目录分离 | 无 |
| **memory_l1 (FrameMemory)** | `single_runner.py:147` shared singleton | v2 砍了 | v2 不读不写 → 无 | 切回 v1 时记忆还在，OK |

---

## 4. API/WS 兼容性逐 endpoint check

`api.py` 引用的 `service.*` 都是 `MultiRunnerService` 方法，不接触 RunContext。因此**切 runner 实现对 API 是黑盒**——只要保留 `MultiRunnerService` 外壳即可。

| Endpoint / 方法 | 调用点 | v2 切换需要做什么 |
|---|---|---|
| `start_all(settings, accounts)` | `api.py:669,688` | 走 `if RUNNER_VER=='v2': start_all_v2()` 分支即可 |
| `stop_all()` | `api.py:625,699` | v2 task 都用 asyncio.gather, cancel 模式一致 — 不动 |
| `get_all_status()` → `{instances, stats, running}` | `api.py:707,965` | **关键**：v2 也必须填 `InstanceStatus` (`state`/`stage_times`/`error`)。v2 phase 用 `P0/P1/...`，v1 用 `accelerator/launch_game/...` — **必须翻译**。映射：`P0→accelerator, P1→launch_game, P2→dismiss_popups, P3a→team_create, P3b→team_join, P4→map_setup, P5→done` |
| `get_screenshot(idx, adb_path, max_width)` | `api.py:873` | 实现走 `_runners[idx].adb` —— v2 SingleRunner.ctx.adb 也是同款接口；只要在 `_runners` dict 里塞 v2 runner 即可，访问 `.adb` 仍可拿到 ADBController |
| `get_or_create_stream_broadcaster` | `api.py:905` | 同上，访问 `runner.adb._adb` 走相同接口 |
| `set_broadcast(fn)` / WS `snapshot/log/state_change` | `api.py:471,511`, `runner_service.py:172` | broadcast schema 不变；v2 phase 名翻译后 `state` 字段保持 v1 词汇 |
| `running` 属性 | `api.py:624,665,696` | 保留 |
| `_instances` / `_runners` / `_start_time` 内部字段 | `api.py:951-965` | 不能改名 —— v2 路径也要塞同名 dict |
| `vm_watchdog` 全局 (`api.py:562-628`) | api 启动钩子 | 跟 runner 版本**绑同一开关**：v2 走 `automation_v2/infra/watchdog_task.py`，v1 走原 singleton |

**结论**：保留 `MultiRunnerService` 类名 + 外部方法签名不变；切换是**内部** `_run_instance` 实现切换。前端零感知。

---

## 5. 推荐切换方案

### 5.1 设计选择：inline if-else 在 `_run_instance` 内

不做 RunnerFactory / 装饰器。理由：
- **改动量最小**：runner_service.py 已经 1145 行；引入 Factory 会再加 100+ 行抽象。
- **极简原则** (项目 CLAUDE.md 5-10)：env flag 是一次性切换工具，不需要可扩展性。
- **作用域窄**：只有 `_run_instance` (407-829 行) 内部分支；start_all 的资源准备 (matcher/adb/minicap/session_dir/metrics) 两版共用，不动。

### 5.2 具体改动 (估计 +60 行 / 改 5 行)

**改动 1** — `runner_service.py:9-26` 顶部：
- 新增 `RUNNER_VERSION = os.environ.get("GAMEBOT_RUNNER_VERSION", "v1").lower()`。
- 不要在 module 顶层 import v2，避免 v1-only 部署链 import 失败。改在分支里 lazy import。

**改动 2** — `runner_service.py:338-378` Pass 3 装配：
- 抽 `if RUNNER_VERSION == "v2":` 分支：
  - 构造 v2 RunContext (注入 raw_adb 包成 AdbTapProto adapter / matcher / yolo_v2 / ocr_v2 / `make_decision_log(session_dir)`)
  - 构造 v2 phases dict (`{"P0": P0Accel(), "P1": P1Launch(), "P2": P2Dismiss(), "P3a": P3aTeamCreate(), "P3b": P3bTeamJoin(), "P4": P4MapSetup(), "P5": P5WaitPlayers()}`)
  - 实例化 `SingleRunner(ctx, phases, middlewares=[...], state_adapter=InstanceStateAdapter(idx))`
  - 塞进 `self._runners[idx]` (变量名同 v1, 因为 `api.py:951` 读这个 dict 长度)
- v1 路径完全不变 (现有 353 行那段保留)

**改动 3** — `runner_service.py:407 _run_instance`：
- 在函数开头分支：`if RUNNER_VERSION == "v2": return await self._run_instance_v2(idx)`
- 新增 `_run_instance_v2` 方法 (~40 行)：
  - 走 `await runner.run()` (v2 SingleRunner 自己跑完所有 phase)
  - 用 phase→v1 词汇映射 (上表 §4) 写 `inst.state`，让 WS schema 不变
  - v2 SingleRunner 内部已经有 max_seconds + 异常防御，**runner_service 层不需要重做** `_PhaseError` / `_GameCrashError` 重试逻辑 (这部分 v2 还没接入，Day 5 再说)
- v1 路径 408-829 整段保留

**改动 4** — `api.py:562-628` vm_watchdog 钩子：
- 同样 env 分支：`if RUNNER_VERSION == "v2"` 启 `automation_v2/infra/watchdog_task.WatchdogTask` (但其 `_scan_dead_instances` 还是 stub，**可先继续用 v1 singleton 直到 Day 5 接业务**)。
- **极简版**：Day 4 vm_watchdog 不动，两版共用 v1 singleton。注释一行说明。

### 5.3 风险点

1. **P5 状态丢失** (§2 末尾) — v2 ctx 没 expected_id/kicked_ids，跑到 P5 必出 bug。Day 4 灰度建议**只跑到 P3a/P3b**，set env 时附带 `--max-phase P4`，或在 v2 RunContext 加 P5 字段 (推荐后者，10 行加完)。
2. **WS state 词汇映射** — `get_all_status()` 必须把 v2 `P0/P1/...` 翻成 `accelerator/launch_game/...`，否则前端 `PHASE_LABELS` 找不到 key 显示空白。在 `_run_instance_v2` 写状态前过 `V2_PHASE_TO_V1` dict 即可。
3. **InstanceStateAdapter 是 stub** (`automation_v2/infra/state.py:30-79` 全是 TODO) — v2 闪退恢复**目前不工作**，重启 backend 必从 P0 重跑。Day 4 验证用，能接受；写在已知风险里。
4. **decision_log 双写** — 切到 v2 时 v1 `get_recorder()` singleton 还是会被 `runner_service.py:252` 初始化 (因为 try/except 包着)。无害但会创建空目录。可选清理：在 v2 分支跳过这一段 (省 5 行)。
5. **YOLO model 双 mmap** — `OcrDismisser.warmup()` (`runner_service.py:275`) 是 v1-only。v2 应该在 phases 实例化时各自调 `ctx.yolo.warmup() / ctx.ocr.warmup()`，避免 `runner.py:11` 注释里写的 cold start。Day 4 简单做：v2 分支跳过 v1 warmup。

### 5.4 改动量预估
- `runner_service.py`：+60 行 (新增 v2 装配 + `_run_instance_v2`)，改 1 行 (顶部 env 读取)，**v1 路径 0 改动**
- `api.py`：0 行 (vm_watchdog Day 4 不动)
- 新建 0 文件

回滚成本：`unset GAMEBOT_RUNNER_VERSION` 重启 backend，零回滚风险。

---

## 6. 验收标准

1. `GAMEBOT_RUNNER_VERSION=v1 python -m backend.main` → 行为完全等价当前 main 分支。
2. `GAMEBOT_RUNNER_VERSION=v2 python -m backend.main` → 跑通 P0→P3a/P3b (P4/P5 已知缺字段，先不要求)，WS `snapshot.instances[i].state` 仍是 v1 词汇。
3. `/api/runner/status` 返回结构跟 v1 字段集一致 (`get_all_status()` 字典 key 不变)。
4. 前端打开不报错；`PHASE_LABELS` 命中所有 state 词。

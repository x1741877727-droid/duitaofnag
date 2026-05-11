# Day 4 风险评估 — V2 灰度接入

**日期**: 2026-05-11 · **分支**: feat/merchant-ui-refresh · **回退 tag**: v1-pre-cleanup / day3-runner
**前置事实**:
- v2 phases 已存在 (p0/p1/p2 有业务, p3a/p3b/p4/p5 是 skeleton 调 `flows/*` 但 flows 目录里**只有 `__init__.py`** → 走 ImportError 分支直接 NEXT/DONE)
- middleware 3 个都是 stub (`_detect_*` 全 return None, `_dismiss_*` 全 return False)
- `GAMEBOT_RUNNER_VERSION` 只在 `automation_v2/__init__.py` 注释里出现, **runner_service.py 未读这个 env**, 灰度入口暂未连接
- v1 状态: P0/P1/P2 已是 v3 PhaseHandler 直跑 (`_run_v3`), P3a/P3b/P4/P5 仍走 `single_runner.phase_team_create/join/map_setup` (legacy 实例)
- 决策落盘: v2 写 `<session>/decisions.jsonl`, v1 detailed 模式写 `decisions_detailed.jsonl` + `img/` —— **同 session_dir 不冲突**

---

## 1. 第一次跑可能挂的 5 类失败模式

### 1.1 [CRITICAL] Skeleton 空跑导致下游业务断流
P3a 走 ImportError → `step_next(outcome_hint="skeleton")`, **不写** `ctx.game_scheme_url`.
- 队长 v2 路径: P3a 立即 NEXT → P4 NEXT → P5 NEXT → run() 返 True, 然而 `runner_service._team_schemes[group]` **从未被赋值** (那是 runner_service 拿 `runner.phase_team_create()` 返回值写的, v2 SingleRunner 完全不走那条路).
- 队员 v2 路径: P3b 看 `ctx.game_scheme_url is None` → `step_fail("scheme 为空")` → session FAIL.
- 连锁: 整个 6/12 实例组**全部队员秒挂**, 队长以为 "session 成功", 实例 state 标 done 但游戏内根本没建队/选模式. 用户看到的是 LDPlayer 卡在大厅, automation 显示 idle.
- 同样问题: P4 skeleton 直接 DONE, 队员 P5 skeleton DONE → 真人玩家从来等不到队伍, P5 1101 行的 wait_players 心跳逻辑全断.

### 1.2 [HIGH] decision.jsonl 与 decisions_detailed/ 同目录混写
v1 detailed 模式按 `DECISION_LOG_DETAILED=1` 开启, 写 `decisions_detailed.jsonl` + `img/`. v2 默认 simple 写 `decisions.jsonl`. **文件名不同, 不会互覆盖**. 但风险:
- 同一灰度跑里, v1 task (e.g. P3a/P4/P5 走 legacy) 与 v2 task (P0/P1/P2 走新 runner) 都在写同 session_dir, 日志合并工具 (replay.py / profile.py) 读 `decisions.jsonl` 时会**漏掉** legacy 决策, 给"P2 快了 5x"的假象 (实际是 P3-P5 被静默跳过).
- 影响诊断准确性, 不影响业务. 不致命但**误判风险大**.

### 1.3 [HIGH] YOLO/OCR 调用激增, 12 实例并发显存峰值
v1 P2 5 路 gather (lobby_tpl/login_tpl/yolo/memory/phash) 但 yolo 每 round 只 1 次. v2 P2 设计是 **ROI 优先 + 全屏 fallback**, 漏检时一 round 跑 2 次 yolo 推理 (一次 ROI ~30ms, 一次全屏 ~50ms).
- 单实例: P2 round_interval 0.2s, 漏检率假设 30% → 平均 1.3 次 yolo/round = 6.5 yolo/s, vs v1 ~1.5/s. **+4x 调用频率**.
- 12 实例 × per-instance ONNX session × 200MB = 2.4GB. DML 后端单 GPU 并发推理**没有显式锁**但 GPU 队列序列化, 实测 v1 12 实例 yolo p95 已 90-180ms, v2 在 P2 阶段同时跑 12 个 = 12×6.5 = 78 yolo/s 排队, p95 可能冲到 250-400ms → round_total 破 800ms, 防不住快速弹窗.
- 风险: 单实例 30 分钟可能看不出 (只有 1 个), **6 实例 / 12 实例**对照才会暴露.

### 1.4 [CRITICAL] middleware 全 no-op, 真邀请来了卡死
`invite_dismiss._detect_invite_dialog` 永远 None. 业务跑久了, QQ 好友/公会邀请弹窗叠在 PUBG 上 → P2 yolo 看到 close_x 但**位置不在它的黑名单白名单里**, 可能误关掉游戏内的"确定"按钮 (action_btn fallback) 或反复 tap 同位置 (黑名单 3s TTL 过期后再点). 
- 撑多久: 邀请频率经验值 ~每实例 30 分钟 1 次, 6 实例 1 小时 = 12 次邀请, **必撞**. 单实例 30 分钟可能撑过.
- crash_check `_check_pubg_alive` 永远 True → PUBG 真挂了 v2 不会触发 GAME_RESTART, 实例僵尸到 max_seconds (60-600s) 才 FAIL.

### 1.5 [MEDIUM] state_adapter 半接 + recovery 走错
`runner.py` 接受 `state_adapter` 参数但 runner_service 装配时**没传** (要查 `_run_instance` 哪里 new SingleRunner). 即使传了, v2 `infra/state.py` 注释里写 "如果是 P3a 且没 game_scheme_url, 退到 P0" 表明 recovery 逻辑还未实现. 闪退场景:
- v2 跑到 P2 闪退 → 重启 → `state.get_recovery_phase()` 返 None → 从 P0 重跑 (loss ~3-5 min 进度), v1 是从 decide_initial_phase() 的 LOBBY 续跑.
- 影响: 不是数据损坏, 是**额外耗时**. 12 实例 8h 跑里, 平均闪退 1-2 次/实例, 多 5-10 min/实例 = 整体 10-20% 时间损耗.

---

## 2. 检测信号 (用户怎么发现 v2 有 bug)

| 失败模式 | 一线信号 | 命令 |
|---|---|---|
| 1.1 skeleton 空跑 | 队员 instance.state="error" 且 error="game_scheme_url 为空" | `grep '"outcome":"team_join_no_scheme"' <session>/decisions.jsonl` |
| 1.1 队长假成功 | session 30s 内 done, 但 LDPlayer 截图仍在大厅 | `grep '"outcome":"skeleton"' decisions.jsonl \| wc -l` (>0 即灰度未完成) |
| 1.2 日志混写 | tools/profile.py 输出 P3a/P4/P5 round=0 | `python -m backend.automation_v2.tools.profile <session>` |
| 1.3 yolo 排队 | `ms.yolo_q` p95 > 100ms 或 `ms.yolo` p95 > 200ms | `jq '.ms.yolo' decisions.jsonl \| sort -n \| tail -100` |
| 1.4 邀请卡死 | P2 phase_round > 100 (60s × 5/s = 300 round 是上限) | `grep '"phase":"P2".*"round":[1-9][0-9][0-9]' decisions.jsonl` |
| 1.4 crash 没触发 | adb shell pidof 返空但 inst.state 仍 "playing" | 后端 log `grep -E '\[实例\d+\].*pubg' run.log` |
| 1.5 recovery 错 | 重启后 instance_state.json 的 phase=P0 但 squad_state.team_code 有值 | `cat .gocache/instance_state_*.json` |

---

## 3. 回退硬指标 (任一触发立刻 `export GAMEBOT_RUNNER_VERSION=v1`)

1. **outcome=exception 占比 > 5%** (单实例 5 分钟窗口, decisions.jsonl 里 outcome=exception 行数 / 总行数). v1 baseline < 1%.
2. **P2 round_total p95 > 1500ms** (BENCHMARK_BASELINE.md 标 v1 ~1100ms, v2 设计目标 < 500ms). >1500ms 说明 GPU 排队/线程争用.
3. **team_create 成功率 < 70%** (6/12 实例对照里, captain 实例 P3a → P4 转移成功的比例). v1 baseline ~95%.
4. **P5 wait_players timeout 率 > 30%** (灰度后队员的 P5 outcome=players_timeout 占比). v1 baseline < 10%.
5. **后端进程 RSS 在 1 小时内增长 > 1GB** 或 GPU 显存 > 6GB (12 实例预算 4.8GB + 系统). 内存泄漏信号.

附加软指标 (告警但不立刻回退): decision.jsonl 增速 > 50KB/s/instance (异常 round 暴增) · run.log ERROR 行 > 5/min.

---

## 4. 灰度 Phase Gate

### Gate A · 单实例 30 分钟 (Day 4 上午)
**条件**: 1 个 LDPlayer 实例, 灰度 captain role, 跑完整 P0-P5.
**通过判据 (全部满足)**:
- session 至少 1 轮完整跑完 (P5 outcome=players_ready 或 explicit timeout)
- decisions.jsonl 不存在 outcome=exception
- P2 round_total p95 < 600ms
- 进程 RSS 末值 - 初值 < 200MB
- 后端 ERROR 日志 = 0

**任一失败 → 修复后重测, 不放过.**

### Gate B · 6 实例 1 小时 (Day 4 下午)
**条件**: 6 实例 (1 captain + 5 member, 或 2 组 3 人), GAMEBOT_RUNNER_VERSION=v2.
**通过判据**:
- 至少完成 2 个完整对局周期 (P0→P5→done→重置→P0)
- team_create 成功率 ≥ 90% (5/6 队伍创建成功)
- 整体 outcome=exception 占比 < 2%
- yolo p95 < 200ms, OCR p95 < 300ms
- 无 1.4 类邀请卡死 (P2 phase_round 最大值 < 50)
- 12 实例预热: 后台同时 6 实例 (剩 6 实例跑 v1) 时 v1 实例 P2 p95 不退化

### Gate C · 12 实例 4 小时 (Day 5)
**条件**: 12 实例全部 v2 (production 替换), 4 小时连续跑.
**通过判据**:
- 完成 ≥ 6 完整周期 (40min/周期 × 6)
- 全实例 session 成功率 ≥ 85% (vs v1 baseline ~88%)
- exception < 1%, GPU 显存峰值 < 6GB
- 无单实例 > 30 分钟无 decision 写入 (僵尸检测)
- 4h 末尾对比 4h 起始: round_total p95 漂移 < 20% (无累积劣化)

---

## 5. Day 4 work order (序号 + 验收)

> **总原则**: 一步一验, 失败回退到 day3-runner tag. 不批量跨步.

### Step 1 · flows/ 业务接入 (~ 2h)
1.1 `flows/team_create.py` — 把 v1 `single_runner.phase_team_create` 7 步 OCR 复制 import, 包装 `async def run_team_create(ctx) -> str | None`. **验收**: 单测 mock OCR → 返一个伪 scheme URL.
1.2 `flows/team_join.py` — 包 v1 `phase_team_join`. **验收**: 单测 `scheme="pubgmhd://xxx"` → True.
1.3 `flows/map_setup.py` — 包 v1 `phase_map_setup`. **验收**: 单测 return True path 通.
1.4 `flows/wait_players.py` — 直接 `from backend.automation.phases.p5_wait_players import P5WaitPlayers` + 1 行 await 包装. **不动 1101 行业务**. **验收**: import 成功, 1 次空跑不抛.

### Step 2 · middleware 业务接入 (~ 1h)
2.1 `invite_dismiss._detect_invite_dialog` — 接 OCR 关键词 "邀请你/加入队伍/加入战队", roi 中央. **验收**: 喂截图 fixture, 邀请图→hit, 大厅图→miss.
2.2 `crash_check._check_pubg_alive` — `ctx.adb.shell("pidof com.tencent.tmgp.pubgmhd")`. **验收**: 真 LDPlayer 杀 PUBG 进程 → 10s 内 log 出 pubg_crashed.
2.3 `network_error` — 暂留 stub, 标 TODO. 不阻塞 Day 4 (出现频率低).

### Step 3 · runner_service env flag 切换 (~ 1h)
3.1 在 `_run_instance()` 入口读 `os.getenv("GAMEBOT_RUNNER_VERSION", "v1")`, ="v2" 时构造 `SingleRunner(ctx, phases, middlewares, state_adapter)` 跑, 否则走当前 `_run_v3` + legacy path.
3.2 把 v2 SingleRunner 跑完后**回填** `self._team_schemes[group] = ctx.game_scheme_url`, 让队员/同组实例能同步 (这是 1.1 失败模式的根因修复). **验收**: 单实例 v2 跑完, `self._team_schemes` 里有值; v1 path 不受影响 (env unset).

### Step 4 · Gate A 单实例 30min (~ 0.5h 跑 + 0.5h 看日志)
按 §4 Gate A 判据. 任一失败 → 回 Step 1-3 修. 通过 → tag `day4-step4-pass`.

### Step 5 · Gate B 6 实例 1h
按 §4 Gate B. 通过 → tag `day4-step5-pass`, 进 Day 5.

---

## 6. 未决问题 (我不确定的)

1. **v1 P5 wait_players 1101 行能不能 import 进 v2 不破?** —— P5 依赖 `runner.ocr`, `runner.action_executor`, `runner.adb_lite`, 这些是 v1 single_runner 注入的, v2 RunContext 接口名不同 (`ctx.ocr` / `ctx.adb`). 直接 import 可能 AttributeError. **建议**: Step 1.4 先在 LDPlayer 跑 5 分钟看异常.
2. **vm_watchdog 跨版本会不会重复启动?** —— v1 在 `runner_service._run_instance` 启 watchdog (line 540), v2 `automation_v2/infra/watchdog_task.py` 注释说"全局 1 个跟 12 runner 平级", 但**注释里的 TODO 没接 v1 vm_watchdog.scan**. 灰度时两份 watchdog 都跑或都没跑都有风险. **建议**: Day 4 v2 完全 reuse v1 watchdog (runner_service 那段不动), `WatchdogTask` Day 5 再启用.
3. **decisions.jsonl 同目录写, replay.py 能不能区分 v1 vs v2 行?** —— 当前 entry schema 没带 runner_version 字段. 同时跑 6 v1 + 6 v2 时, 聚合分析无法按版本 split. **建议**: Step 3.1 起在 ctx 初始化时塞 `ctx._runner_version="v2"`, 落到 decision entry 一个字段.
4. **P0 加速器 v2 走的是 `phases/p0_accel.py` 还是 v1 `P0AcceleratorHandler`?** —— 当前 `runner_service.py:578` 强写 v1 P0AcceleratorHandler, v2 phases/p0_accel.py 几乎没人用. 灰度切换时要确认 v2 是否真用 v2 的 P0, 还是仍走 v1 的 v3 handler (REVIEW_DAY3_ARCH.md 评 A-, 但实际 wiring 可能错).
5. **squad_state 跨版本兼容?** —— v1 写 `squad_state.team_code`, v2 写 `ctx.game_scheme_url` + `self._team_schemes[group]`. 闪退后 v2 重启从 squad_state 读得到吗? state_adapter 未实现这条路.
6. **per-instance ONNX session 12 个的真实显存** —— V2_PHASES.md 估 2.4GB, 但是 DML provider 在 Windows 上常驻 fence/heap overhead 我没测过. Gate C 前必须用 GPU-Z 实测一次.

---

**结论**: Day 4 不要一次性全切. 严格按 Step 1-5 顺序, 单实例不过不进 6 实例. 最大风险是 **1.1 skeleton 空跑**, Step 1 + Step 3.2 把它消掉是 Day 4 全部价值所在. 1.4 邀请卡死要 Step 2.1 真接入, 不能跟到 Gate B.

文件: `/Users/Zhuanz/ProjectHub/game-automation/docs/REVIEW_DAY4_RISKS.md`

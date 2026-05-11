# Gamebot — 内容规格 (Content Spec)

> 这份文档**只列每个页面/组件包含什么内容、字段、状态、交互、数据来源**。
> **不规定**视觉、配色、字体、排版、网格/抽屉/dock 等任何呈现方式。
> 设计师拿到这份后, 排版/呈现完全自由发挥, 但下面所有字段、状态、交互、数据流不能删减。
> 字段名 (英文) 来自 `web/src/lib/store.ts` 和 backend FastAPI, 不能改 (跟代码锁死)。
> 中文 label 可以改 (例 "运行" 改 "工作台" 都行), 但功能必须保留。

---

## 0. 项目定位

桌面 SaaS 工具。同时跑 6-18 个安卓模拟器 (LDPlayer) 自动玩"和平精英"。
两套外壳 (运行时 by build flag):
- **客户版 (prod)**: 日常运营用, 只露操作 + 复盘 + 设置
- **dev 版 (dev)**: 加上训练/性能/识别调试

---

## 1. 全局结构 (任何页面都有的 chrome)

### 1.1 标题栏 (Header)

| 字段 | 来源 | 说明 |
|---|---|---|
| App 名 | 字面 "Gamebot" | 静态 |
| 当前会话状态文案 | 派生自 `instances` + `isRunning` | "运行中 · 11/12 在跑" / "12 台待命" / "等待配置" |
| 异常实例数 | `instances` 中 `state === 'error'` 的计数 | 仅 > 0 时显示 |
| 在线实例数 / 总数 | `instances` 中非 init/done/error/ready/in_game 的计数 | 整数 / 整数 |
| 已运行时长 | `runningDuration` (秒) | 仅 `isRunning=true` 时显示, 格式 HH:MM:SS |
| 主操作按钮 | 派生 | 见 §1.1.1 |
| 日志抽屉切换 | `showLogPanel` | toggle |

#### 1.1.1 主操作按钮状态

| isRunning | phaseTester.busy | 显示 | 点击行为 |
|---|---|---|---|
| false | false | "开始今天的工作 N 台" / "启动" | POST `/api/start` |
| true | false | "全部停止" | POST `/api/stop` |
| any | true | "停测试 · {progress}" | POST `/api/runner/cancel` |

旁边附"应急 ▾"下拉, 含: 全部重启 / 强制停止 (后端可暂未实现, 但 UI 必须有入口)。

### 1.2 侧栏 (Nav)

#### 1.2.1 客户版 (prod)

| nav key | 中文 label | 跳到 |
|---|---|---|
| `dashboard` | 运行 | §2 |
| `data` | 决策档案 | §3 (2 子页) |
| `settings` | 设置 | §5 |

#### 1.2.2 dev 版加这些

| nav key | 中文 label | 跳到 |
|---|---|---|
| `perf` | 性能 | §4 |
| `recognition` | 识别 | §6 (4 子页) |

#### 1.2.3 用户信息区

| 字段 | 来源 | 说明 |
|---|---|---|
| 用户名 | static | "阿郑" |
| 角色文案 | build flag | "客户 · v3.x" / "开发者 · dev v3.x" |

### 1.3 日志抽屉 (LogDrawer)

#### 1.3.1 头部

| 字段 | 来源 | 说明 |
|---|---|---|
| 标题 | 字面 "实时日志" | |
| 总条数 | `logs.length` | |
| 实时心跳指示 | `liveConnected` | 在线/离线 |
| 清空按钮 | | 调 `clearLogs()` |
| 关闭按钮 | | toggle `showLogPanel` |

#### 1.3.2 过滤 tab

`logFilter`: number(实例 idx) / 'all' / 'SYS' / 'GUARD'

实例 tab 数动态: 跟 `instances` 当前数对齐。超过 8 个折叠 "更多 ▾"。

#### 1.3.3 列表

每条 LogEntry 字段 (来自 store.ts):

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string | 唯一 ID |
| `timestamp` | number (epoch ms) | |
| `instance` | number / 'SYS' / 'GUARD' | 实例编号或系统级 |
| `level` | 'info' / 'warn' / 'error' | 严重度 |
| `message` | string | 正文 |
| `state` | InstanceState (可选) | 该日志关联的 phase |

行为:
- 倒序 (最新在上)
- 点 `instance` 标识 → 设 `logFilter` 为该 instance
- 点消息正文 → 复制全文到剪贴板
- 列表上限 `MAX_LOGS = 500` 条

#### 1.3.4 空状态

文案: "暂无日志"

### 1.4 Toast / 通知

3 秒自动消失。触发场景:

| 触发 | 文案 |
|---|---|
| 启动成功 | "{N} 台已开始跑" |
| 启动失败 | "启动失败: {reason}" |
| 配置已保存 | "已保存" |
| 实例从 error 恢复 | "{N} 号已重连" |
| 阶段测试完成 | "测试完成: {N} 通过 / {M} 失败" |

### 1.5 全局键盘快捷键

| 键 | 行为 |
|---|---|
| `Cmd/Ctrl + 1-7` | 按顺序切换 nav 项 |
| `Cmd/Ctrl + R` | 刷新当前页数据 |
| `Cmd/Ctrl + L` | toggle 日志抽屉 |
| `Esc` | 关闭模态 / 取消选择 |
| `Space` | (在运行页) 选中/取消选中当前 hover 的实例 |

---

## 2. 运行 (Dashboard) — **合并了原中控台**

主页面。原 "运行" + "中控台" 合并后唯一对外业务页。

### 2.1 状态机

| 状态 key | 触发 | 显示哪一类内容 |
|---|---|---|
| `empty` | `accounts.length === 0` | §2.2 |
| `idle` | `accounts.length > 0 && !isRunning` | §2.3 |
| `running` | `isRunning === true` | §2.4 |
| `running + anomaly` | `running` + 任一 instance `state === 'error'` | §2.4 + §2.5 |

### 2.2 状态: 零配置 (`empty`)

| 字段 | 来源 | 说明 |
|---|---|---|
| 时间问候 | `Date.now()` | 早上好/下午好/晚上好/夜深了 |
| 大引导文案 | static | "先去配账号" |
| 3 步流程 | static | 1. 加账号 (QQ/昵称/队/角色) 2. 启 LDPlayer 3. 回这里启动 |
| 主 CTA | static | "去设置 添加账号" → nav `settings` |
| 次 CTA | static | "看演示" → 帮助文档 (外链 OR `docs/QUICK_START.md`) |
| 队伍规则提示 | static | 一组 = 1 队长 + 2 队员; 大组 1=A+B / 2=C+D / 3=E+F |

### 2.3 状态: 待启动 (`idle`)

#### 2.3.1 总览信息

| 字段 | 来源 | 说明 |
|---|---|---|
| 时间问候 | `Date.now()` | 同 §2.2 |
| 准备状态文案 | static | "一切准备就绪" / "等待配置" |
| readiness.accounts | `accounts.length` + 队伍数 | "{total} 个号已分 {teamCount} 个队" |
| readiness.emulators | `emulators.filter(e => e.running).length` | "{ready}/{total} 台 LDPlayer 在跑" |
| 主 CTA | static | "开始今天的工作 N 台" |
| 大组切换 | `activeSquad` (1/2/3) | 见 §2.6 |

#### 2.3.2 实例预览 (每个 instance 一项)

每项字段:

| 字段 | 来源 | 说明 |
|---|---|---|
| `index` | account.index | 0-based |
| 别名 | account.nickname | |
| LDPlayer 名 | emulator[index].name | 例 "雷电模拟器-1" |
| 队伍 | account.group | A-F |
| 是否队长 | `account.role === 'captain'` | bool |
| 状态 pill | static | "待启动" |
| readiness.account | `account != null` | ✓/✗ |
| readiness.emulator | `emulators[index].running` | ✓/✗ |

不显示画面缩略图。

### 2.4 状态: 运行中 (`running`)

#### 2.4.1 总览 KPI

| 字段 | 来源 | 说明 |
|---|---|---|
| 已运行时长 | `runningDuration` | HH:MM:SS |
| 在线模拟器数 / 总数 | `instances` 派生 | 整数 / 整数 |
| 完成局数 | `sessions` 派生, 本会话 | 整数 |
| 异常数 | `state === 'error'` 计数 | 整数, > 0 时强调 |

#### 2.4.2 视图筛选 (chip 单选 + 异常 toggle)

| chip key | 显示哪些 instance |
|---|---|
| `all` (默认) | 全部 |
| `squad-1` | group ∈ A,B |
| `squad-2` | group ∈ C,D |
| `squad-3` | group ∈ E,F |
| `error-only` (toggle, 可叠加) | `state === 'error'` |

`activeSquad` (1/2/3) 跟 squad-N chip 双向绑定。

#### 2.4.3 实例总览 (每个 instance 一项)

每项字段 (来自 `Instance` interface):

| 字段 | 类型 | 说明 |
|---|---|---|
| `index` | number | 0-based |
| `nickname` | string | 来自 instance |
| LDPlayer 名 | string | join `emulators[index].name` |
| `group` | TeamGroup (A-F) | |
| `role` | TeamRole (captain/member) | |
| `state` | InstanceState | 见 §7.1 |
| state 中文 label | string | `STATE_LABEL[state]` |
| state tone | 'live'/'warn'/'error'/'idle' | 用于状态色 |
| `stateDuration` | number (秒) | 当前 state 已停留 |
| 上次决策时间 | 派生 from `liveDecisions[index][0].ts` | 相对时间 "8s 前" |
| `error` | string | 仅 state=error 时有意义 |
| 实时画面 | JPG, ~2 fps | `GET /api/screenshot/{index}` |
| `adbSerial` | string | 例 "emulator-5556" |

#### 2.4.4 选中行为

| 字段 | 来源 |
|---|---|
| `selectedInstances` | number[] |
| `focusedInstance` | number / null |

行为:
- 单击实例 → toggle 该 idx 进 `selectedInstances`, 同时设 `focusedInstance`
- 选中 0 个: 不显示详情区
- 选中 1 个: 详情区显示该实例完整详情 (§2.4.5)
- 选中 2-4 个: 详情区分屏显示每个的简版详情 (snapshot + phase + state)
- 选中 > 4 个: 提示 "最多看 4 台同时", 仅显前 4 个详情
- `Esc` → 调 `clearInstanceSelection()`
- `Space` 在 hover 实例时 → 调 `toggleInstanceSelection(idx)`

#### 2.4.5 实例详情区 (选中 1 个时, 完整版)

##### 头部

| 字段 | 来源 |
|---|---|
| `index` | number |
| 别名 | `instance.nickname` |
| LDPlayer 名 | `emulators[index].name` |
| 队伍 + 队长标识 | `instance.group`, `instance.role` |
| 关闭操作 | 调 `clearInstanceSelection()` |

##### 实时画面

| 字段 | 来源 |
|---|---|
| 大画面 (5 fps) | `GET /api/screenshot/{index}` |
| 当前 phase | `instance.state` + label |
| `stateDuration` | number 秒 |

##### 5 层识别证据 (Tier 1-5)

每行字段:

| 字段 | 内容 |
|---|---|
| Tier 编号 | 'T1' - 'T5' |
| Tier 名 | 模版 / OCR / YOLO / Memory / 兜底 |
| 命中状态 | 'ok' / 'fail' / 'skip' |
| 命中详情 | 模版 ID / OCR 文本 / YOLO 类别+数量 / 历史 tap 坐标 / 启用规则名 |
| 置信度 | 0-1 浮点, skip 时显 "—" |

数据来源: `liveDecisions[index][0]` 衍生 (现有 schema 含 `tier_count`, 可能需要 backend 扩 `tier_evidence` 字段)。

##### 决策日志

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string | 决策 ID |
| `ts` | number (epoch ms) | 时间戳, 显示 HH:MM:SS |
| `phase` | string | 该决策时所处 phase |
| `round` | number | 局数 |
| `outcome` | string | 决策结果 |
| `tap_method` | string | 例 'template' / 'yolo' / 'ocr' |
| `tap_target` | string | 例 'lobby_btn' |
| `tap_xy` | [number, number] / null | 坐标 |
| `verify_success` | boolean / null | tap 后是否验证成功 |
| 耗时 | number (ms) | 派生 |

来源: `liveDecisions[index]` (最近 50 条, store cap), 倒序展示。
Filter 选项: 全部 / 成功 (`verify_success === true`) / 失败 (`verify_success === false`)

##### Phase 时间线

数据来源: `phaseHistory[index]: { from: string; to: string; ts: number }[]`

每段:

| 字段 | 内容 |
|---|---|
| phase 名 | string |
| 持续时间 | number (秒) = `next.ts - this.ts` |
| 占比 | 该段 / 本局总时长 |

底部:
- 本局总时长 (秒)

##### Stage 累计耗时 (来自 `instance.stageTimes`)

`Record<string, number>` (例 `{"accelerator": 12.3, "launch_game": 8.1}`)
按 phase 列出, 显示总累计 (跨多局合计)。

#### 2.4.6 实例详情区 (选中 2-4 个时, 简版分屏)

每屏:
- `index` 标签
- 实时画面 (~5 fps)
- 当前 phase + state label
- `stateDuration`

不展开 5 层证据 / 决策日志 / Phase 时间线。

### 2.5 异常态特殊内容 (`running + anomaly`)

#### 2.5.1 异常 banner

仅当任一 instance `state === 'error'` 时显示:

| 字段 | 内容 |
|---|---|
| 主标 | "{N} 号已掉线 · {team} 队缺 1 人" (动态文案, 多个错误时聚合最严重的一个) |
| 副标 | "{nickname} · {N 分钟前断开} · 当前局结束后停止接新局" |
| 操作 1 | "查看" → 设 `selectedInstances = [errorIdx]`, `focusedInstance = errorIdx` |
| 操作 2 | "尝试重启" → POST `/api/start/{errorIdx}` (需 backend 提供) |

#### 2.5.2 异常实例视觉提示

state=error 的实例需要在总览里有醒目提示 (具体怎么醒目设计师定)。

### 2.6 大组切换 (`activeSquad`)

| `activeSquad` | 包含 teams |
|---|---|
| 1 | A + B |
| 2 | C + D |
| 3 | E + F |

来自 store: `activeSquad: number; setActiveSquad(n)`

行为: 跟 §2.4.2 chip filter 双向绑定。

### 2.7 阶段测试 (PhaseTester) — dev only

dev 模式下用来单独跑某个 phase 验证逻辑, 不进游戏整局。原中控台底部折叠面板, 合并后保留在运行页里。
关键: **不同 phase 需要不同输入**, 表单要按选中的 phase **动态显示/隐藏**输入项。

#### 2.7.1 phase 列表 (来源 `GET /api/runner/phases`)

每个 phase 字段:

| 字段 | 类型 | 说明 |
|---|---|---|
| `key` | string | 'P0' / 'P1' / 'P2' / 'P3a' / 'P3b' / 'P4' / 'P5' (跟 backend 锁死) |
| `name` | string | 中文名 (例 "加速器校验" / "启动游戏" / "等待真人入队") |
| `handler` | string | 后端 handler 类名 |
| `description` | string | 详细描述 |
| `flow_steps` | string[] | 该 phase 内部的子步骤说明 |
| `max_rounds` | number | 最大跑轮数 (0 = 单次) |
| `round_interval_s` | number | 轮间隔秒数 |

phase 详情 (点单个 phase 看): `GET /api/runner/phase_doc/{phase}` 返回上面所有字段 + `source_file` (源代码路径)。

#### 2.7.2 选阶段 (`selKeys`)

- 多选 (string[]), **顺序敏感** (按勾选顺序串行跑)
- 至少选 1 个, 否则跑测试按钮 disabled
- 选中 N 个 → 按选中顺序串行执行

#### 2.7.3 选实例

| 字段 | 类型 | 说明 |
|---|---|---|
| `selInst` | number / null | 单实例模式备用 |
| 来自 store | `selectedInstances` | 多实例时复用 §2.4.4 的多选 (上面卡片选中谁就跑谁) |

实际目标 instance 列表派生:
- 如果 `selectedInstances.length > 0`: 用这个数组
- 否则: 用 `[selInst]` (单实例)
- 都没: 跑测试按钮 disabled

#### 2.7.4 角色选择 (`selRole`) — **仅当选中的 phase 含 ROLE_PHASES 时显示**

`ROLE_PHASES = ['P3a', 'P3b', 'P4']`

判定: `selKeys.some(k => ROLE_PHASES.has(k))` → 显示角色选择。

| 字段 | 类型 | 取值 |
|---|---|---|
| `selRole` | 'captain' / 'member' | 二选一 |

业务含义:
- P3a (创建队伍) 只在 captain 跑
- P3b (加入队伍) 只在 member 跑
- P4 (准备就绪) 队长 / 队员都行, 但行为不同

P0 / P1 / P2 选中时这个输入**不显示** (它们对 role 不敏感)。

#### 2.7.5 P5 玩家 ID (`expectedId`) — **仅当选中 P5 时显示**

判定: `selKeys.includes('P5')` → 显示 ID 输入。

| 字段 | 类型 | 校验 |
|---|---|---|
| `expectedId` | string | 必填 / 必须是 10 位数字 (`/^\d{10}$/`) |

业务含义:
- P5 = "等待真人入队" phase
- 输入的 10 位 ID = 你想等的真人玩家在游戏里的 ID
- 240 秒超时未入队 → P5 fail

输入时自动:
- 移除非数字字符 (`replace(/\D/g, '')`)
- 截断到 10 位 (`.slice(0, 10)`)
- 校验失败时按钮 disabled, 鼠标 hover 提示 "P5 需要 10 位玩家 ID"

#### 2.7.6 其他 phase 不需要 ID

P0-P4 选中时这个输入**不显示**。

#### 2.7.7 keepGoing toggle

| 字段 | 类型 | 说明 |
|---|---|---|
| `keepGoing` | bool | 单步失败是否继续往下跑 |

默认 false (失败即停)。开启后某 phase 失败仍继续跑后面的 phase。

#### 2.7.8 跑 / 取消 / 状态

| 字段 | 类型 | 说明 |
|---|---|---|
| `busy` | bool | 测试是否在跑 |
| `progress` | string | 当前进度文案 (例 "P3a 跑中... #1") |

操作:
- 跑: `POST /api/runner/test_phase` body `{ instance, phase, role, expected_id?, keep_going? }`
  - 返回 `{ ok, phase_name, duration_ms, error? }`
  - 多实例 / 多 phase 时前端循环串行调
- 取消: `POST /api/runner/cancel`
- 拿单任务进度: `GET /api/runner/test_phase/{task_id}`
- 新会话: `POST /api/runner/test_new_session` (清掉 runner state 重新开)

#### 2.7.9 业务约束 (前端必须实现)

| 约束 | 行为 |
|---|---|
| P5 只在队长跑 | 多实例 + 选 P5 → 自动 skip 非队长实例, 只在 `targets[0]` 跑 |
| ROLE_PHASES 多实例 → 自动切组 | 选了 P3a/P3b/P4 + 选多于 1 个实例 → 按 squad 编排 (P3a 拿 scheme 同步给 P3b, P4 队长收尾) |
| 主 runner 在跑时禁测试 | `isRunning=true` 时跑测试按钮 disabled, 提示 "停掉主跑才能测" |
| 大组联动模式 | 大组之间 Promise.allSettled 并发, 组内 P0-P2 并发, P3a → P3b → P4 → P5 串行 |

#### 2.7.10 结果展示 (`results`)

每条结果字段:

| 字段 | 类型 | 说明 |
|---|---|---|
| `phase` | string | phase key (P0..P5) |
| `phase_name` | string | 中文名 (例 "#1 加速器校验") |
| `ok` | bool | 是否通过 |
| `duration_ms` | number (可选) | 耗时 |
| `error` | string (可选) | 失败时填错误描述 |

列表追加显示, 倒序或顺序由设计师定。提供"清空" / "导出 JSON" 操作。

### 2.8 数据来源汇总

| 用途 | endpoint | method |
|---|---|---|
| 启动主跑 | `/api/start` | POST |
| 启动单实例 | `/api/start/{instance_index}` | POST |
| 停止全部 | `/api/stop` | POST |
| 全局状态 | `/api/status` | GET |
| 账号列表 | `/api/accounts` | GET |
| 模拟器列表 | `/api/emulators` | GET |
| 实例画面 | `/api/screenshot/{instance_index}` | GET (image/jpeg) |
| 实时事件流 | `/ws/live` | WebSocket |
| 阶段测试 | 见 §2.7.2 | |

---

## 3. 数据 (Data) — 含 2 子页

`dataSubView`: 'archive' / 'memory'

子页之间互斥单选, 内部 sub-tab 切换。

### 3.1 子页 `archive` — 决策档案

#### 3.1.1 三层导航数据流

```
Session 列表  →  该 session 的决策列表  →  单条决策详情
```

#### 3.1.2 Session 列表项

| 字段 | 来源 (`GET /api/sessions`) | 说明 |
|---|---|---|
| `session_id` | string | UUID |
| `started_at` | epoch ms | |
| `ended_at` | epoch ms / null | |
| `instance_count` | number | 该会话用了几台 |
| `decisions_count` | number | 决策总数 |
| `error_count` | number | 错误决策数 |
| `rounds_completed` | number | 完成局数 |

#### 3.1.3 决策列表

数据来源: `GET /api/decisions?session_id=xxx&instance=xxx&result=xxx`

每条:

| 字段 | 类型 |
|---|---|
| `decision_id` | string |
| `ts` | epoch ms |
| `instance_index` | number |
| `phase` | string |
| `tap_method` | string ('template' / 'yolo' / 'ocr' / 'memory' / 'fallback') |
| `tap_target` | string |
| `tap_xy` | [number, number] / null |
| `outcome` | string |
| `verify_success` | boolean / null |
| `duration_ms` | number |
| `note` | string (开发者备注) |

Filter:
- 按 instance (multi-select)
- 按 result (全部 / verify_success=true / verify_success=false)
- 按 tap_method
- 按时间范围

#### 3.1.4 决策详情

数据来源: `GET /api/decision/{decision_id}/data`

##### 顶部摘要
- decision_id, ts, instance_index, phase, tap_method, tap_target, outcome, verify_success, duration_ms, note

##### 3 张图轮播
- shot_in.jpg (决策前) — `GET /api/decision/{decision_id}/image/shot_in.jpg`
- shot_mid.jpg (处理中) — 同上 mid
- shot_out.jpg (决策后) — 同上 out

##### 5 层 Tier debug 面板

每层一个块, 字段:

| Tier | 字段 |
|---|---|
| T1 模版 | template_id, confidence, candidate_boxes (在原图叠加 bbox), threshold |
| T2 OCR | roi (xyxy), text_recognized, confidence, ocr_engine |
| T3 YOLO | detection_classes, bboxes, confidences, model_version |
| T4 Memory | memory_hit (bool), historical_tap_xy, memory_hash |
| T5 兜底 | rules_enabled (string[]), fallback_reason |

### 3.2 子页 `memory` — 记忆库

数据来源:
- `GET /api/memory/stats` — 统计概览
- `GET /api/memory/list` — 列表
- `GET /api/memory/{rid}` — 单条
- `GET /api/memory/{rid}/snapshot` — 截图 (image)
- `DELETE /api/memory/{rid}` — 删
- `POST /api/memory/{rid}/mark_fail` — 标记失败
- `GET /api/memory/pending/list` — pending 列表
- `GET /api/memory/pending/{key}/sample/{idx}` — pending 样本 (image)
- `POST /api/memory/pending/{key}/discard` — 丢弃 pending
- `POST /api/memory/dedup` — 去重

#### 3.2.1 统计概览

| 字段 | 内容 |
|---|---|
| total_records | 总记忆数 |
| pending_count | 待审核数 |
| by_phase | Record<phase, count> |
| by_outcome | Record<outcome, count> |
| disk_size_mb | 磁盘占用 MB |

#### 3.2.2 记忆列表项

| 字段 | 类型 |
|---|---|
| `rid` | string (record id) |
| `phase` | string |
| `tap_target` | string |
| `tap_xy` | [number, number] |
| `outcome` | string |
| `created_at` | epoch ms |
| `hit_count` | number (被命中次数) |
| `marked_fail` | bool |

操作: 删除 / 标记失败 / 查截图

#### 3.2.3 Pending 列表项

| 字段 | 类型 |
|---|---|
| `key` | string (pending key) |
| `samples_count` | number |
| `phase` | string |
| `tap_target` | string |
| `last_seen` | epoch ms |

操作: 看样本图 / 丢弃

---

## 4. 性能 (Perf) — dev only

### 4.1 数据来源

- `GET /api/perf/snapshot` — 当前快照
- `GET /api/perf/series?metric=xxx&window=24h` — 24h 时序

### 4.2 全局指标卡

每卡字段:

| 字段 | 单位 | 来源 metric |
|---|---|---|
| CPU usage | % | `cpu_percent` |
| 内存占用 | MB | `mem_mb` |
| 截图 fps | fps | `screenshot_fps` |
| OCR 平均耗时 | ms | `ocr_avg_ms` |
| YOLO 平均耗时 | ms | `yolo_avg_ms` |
| 后端响应 | ms | `api_avg_ms` |

每卡附 24h 趋势 (mini chart, 数据点数任设计师定密度)。

### 4.3 Per-instance 性能表

每实例一行:

| 字段 | 来源 |
|---|---|
| `instance_index` | number |
| 截图 fps | per_instance.screenshot_fps |
| OCR 调用次数 | per_instance.ocr_count |
| OCR 平均耗时 | per_instance.ocr_avg_ms |
| YOLO 调用次数 | per_instance.yolo_count |
| YOLO 平均耗时 | per_instance.yolo_avg_ms |
| 网络出站 | per_instance.net_kbps (KB/s) |
| 错误数 | per_instance.error_count |

### 4.4 瓶颈列表

最慢的 5-10 个 phase / 决策类型。每行:

| 字段 | 内容 |
|---|---|
| `phase` | string |
| `tap_method` | string |
| `avg_ms` | number |
| `p95_ms` | number |
| `count` | number |

按 `avg_ms` 降序。

---

## 5. 设置 (Settings)

### 5.1 子模块: 环境配置

字段 (来自 `Settings` interface + `GET /api/settings`):

| 字段 | 类型 | 说明 |
|---|---|---|
| `ldPlayerPath` | string | LDPlayer 安装路径, 带 [浏览] 选 |
| `adbPath` | string | ADB 路径, 带 [浏览] 选 |
| `gamePackage` | string | 默认 `com.tencent.tmgp.pubgmhd` |
| `targetMap` | string | 主战场地图名 |
| `pipelineStages` | PipelineStage[] | 流水线阶段配置 (见下) |

`PipelineStage`:
```ts
{
  key: string         // 例 'accelerator'
  label: string       // 例 '代理就绪'
  states: string[]    // 例 ['accelerator']
}
```

操作: PUT `/api/settings` 保存。

### 5.2 子模块: 模拟器扫描

数据: `GET /api/emulators`

返回每个 `Emulator`:

| 字段 | 类型 |
|---|---|
| `index` | number |
| `name` | string (例 "雷电模拟器-1") |
| `running` | bool |
| `adbSerial` | string (例 "emulator-5556") |

操作:
- [扫描] 按钮 → 重新调 `/api/emulators`
- 启动 / 停止 / 重启 / 删除单实例 (后端可能未全部实现, UI 必须有入口)

### 5.3 子模块: 队伍编排 (SquadBuilder)

数据来源: `GET /api/accounts` / `PUT /api/accounts`

每条 `AccountAssignment`:

| 字段 | 类型 | 说明 |
|---|---|---|
| `index` | number | 0-based, 跟模拟器编号对齐 |
| `nickname` | string | 显示名 |
| `gameId` | string | 游戏 ID (10 位) |
| `qq` | string | QQ 号 (来自 backend, 不一定在 store) |
| `group` | TeamGroup (A-F) | |
| `role` | TeamRole | captain/member |
| `accel_mode` | 'apk' / 'tun' / null | per-instance override (可选) |

操作:
- 添加账号 (新建一行)
- 删除账号
- 改任意字段 (nickname / gameId / qq / group / role)
- 拖拽 / 选择改 group / role
- 一键导出 / 导入 (JSON)
- 一键保存 PUT `/api/accounts`

#### 5.3.1 队伍配置规则 (校验)

- 每队最多 3 人: 1 captain + 2 members
- captain 不能空; member 可以空
- 同一 `account.index` 不能在多个队
- 大组 1 = A+B / 大组 2 = C+D / 大组 3 = E+F
- 总实例上限 18

校验失败时阻止保存并提示具体哪条规则破。

---

## 6. 识别 (Recognition) — dev only

`recognitionSubView`: 'templates' / 'template-tuner' / 'yolo' / 'ocr'

预处理算子枚举 (跨 4 个子页共享): `Preprocessing = 'grayscale' | 'clahe' | 'binarize' | 'sharpen' | 'invert' | 'edge'` (固定 6 个, 用 `ALL_PREPROC` 数组导出, 顺序敏感, 子页都按数组形式表示)。

### 6.1 子页 `templates` — 模版库

#### 6.1.1 列表 schema (`GET /api/templates/list`)

`TemplateListResp`:

| 字段 | 类型 | 说明 |
|---|---|---|
| `count` | number | 总数 |
| `items` | TemplateMeta[] | 见 6.1.2 |
| `categories` | { name: string; count: number }[] | 分类 + 每类数量, 用来生成 filter |
| `template_dir` | string | 模版目录路径 (绝对路径) |

#### 6.1.2 单模版 schema (TemplateMeta)

| 字段 | 类型 | 说明 |
|---|---|---|
| `name` | string | 模版 ID |
| `category` | string | 分类 (跟 phase 对应, 用来 filter) |
| `path` | string | 文件路径 |
| `size_bytes` | number | 文件大小 |
| `mtime` | number (epoch ms) | 修改时间 |
| `width` | number | 模版宽度 (px) |
| `height` | number | 模版高度 (px) |
| `phash` | string | perceptual hash, 用于查重 |
| `preprocessing` | Preprocessing[] | 持久化的预处理算子顺序 |
| `threshold` | number | 匹配阈值 (0 = 用默认 0.80) |
| `source_w` | number | 原始截图宽 |
| `source_h` | number | 原始截图高 |

#### 6.1.3 详情 schema (`GET /api/templates/detail/{name}`)

`TemplateDetail`:

| 字段 | 类型 | 说明 |
|---|---|---|
| `name` | string | |
| `category` | string | |
| `width` / `height` | number | |
| `phash` | string | |
| `has_original` | bool | 是否保留了原始大图 |
| `original_url` | string | 原图 URL (派生 `/api/templates/original/{name}`) |
| `crop_bbox` | [x1,y1,x2,y2] / null | 在原图坐标系的裁剪框 |
| `source` | string | 截图源 (decision_id / instance / upload 等) |
| `saved_at` | number / null | 保存时间 |

#### 6.1.4 命中统计 schema (`GET /api/templates/stats/{name}`)

`TemplateStats`:

| 字段 | 类型 | 说明 |
|---|---|---|
| `name` | string | |
| `sessions_scanned` | number | 扫描了多少 session |
| `match_count` | number | 匹配次数 |
| `hit_count` | number | 命中次数 (高于 threshold) |
| `hit_rate` | number | 命中率 0-1 |
| `avg_score` | number | 平均得分 |

#### 6.1.5 测试匹配 schema (`POST /api/templates/test`)

请求 (`TestArgs`):

| 字段 | 类型 | 说明 |
|---|---|---|
| `name` | string | 模版名 |
| `instance` | number / undefined | 取该 instance 当前画面作为测试源 |
| `decision_id` | string / undefined | 取该决策的截图作为测试源 |
| `session` | string / undefined | session 配合 decision_id |
| `image_b64` | string / undefined | 上传 base64 图作为测试源 |
| `threshold` | number / undefined | 临时覆盖默认 |
| `use_edge` | bool / undefined | 是否启用 edge 算子 |
| `preprocessing` | Preprocessing[] / undefined | 临时覆盖 yaml 持久值 |

注: instance / decision_id / image_b64 三选一作为测试源。

返回 (`TemplateTestResult`):

| 字段 | 类型 | 说明 |
|---|---|---|
| `template` | string | |
| `threshold` | number | |
| `hit` | bool | 是否命中 |
| `score` | number | 匹配得分 0-1 |
| `cx` / `cy` | number | 命中中心点 (像素) |
| `w` / `h` | number | 命中框宽高 |
| `bbox` | number[] / null | [x1,y1,x2,y2] |
| `duration_ms` | number | 耗时 |
| `annotated_b64` | string | 已标注 bbox 的图 base64 (前端直接显示) |
| `note` | string | 备注 |
| `source` | string / undefined | 测试源描述 |
| `source_image_size` | [w, h] / undefined | |

#### 6.1.6 改元数据 schema (`POST /api/templates/save_meta`)

`SaveMetaArgs`:

| 字段 | 行为 |
|---|---|
| `name` | 必填 |
| `preprocessing` | 数组 = 覆盖, 空数组 [] = 清空, undefined = 不动 |
| `threshold` | 数字 = 覆盖, 0 = 不动 (向后兼容) |

#### 6.1.7 上传 schema (`POST /api/templates/upload`)

multipart/form-data:

| 字段 | 类型 | 说明 |
|---|---|---|
| `file` | Blob/File | PNG/JPG |
| `name` | string | 模版 ID |
| `overwrite` | 'true' / 'false' | 是否覆盖同名 |
| `crop_x` / `crop_y` / `crop_w` / `crop_h` | number (可选) | 在原图上的裁剪框 |

返回 `{ ok, name, path, width, height, phash, similar: [{ name, phash_dist }] }` — `similar` 列出 phash 距离近的模版供查重。

#### 6.1.8 列表行为

- 顶部搜索: `name` 模糊匹配
- 分类 filter: 用 `categories` 列表生成 chip ('全部' + 每个 category)
- 列表项点击 → 进详情
- 删除: `DELETE /api/templates/{name}`
- 添加来源: 上传文件 / 上传后裁切 / 从近期决策的截图 (拉 `GET /api/decisions?limit=30`) 选一张进 cropper

#### 6.1.9 cropper 行为 (上传时)

- 显示原图 + 拖拽框选裁剪区
- 输入模版 name + 是否 overwrite
- 提交后调 6.1.7 上传

### 6.2 子页 `template-tuner` — 模版调试

调阈值 / 调预处理算子组合。

#### 6.2.1 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `selectedName` | string | 选中的模版 (从 list 选) |
| `filter` | string | 列表搜索关键字 |
| `editPreproc` | Preprocessing[] | 当前编辑的预处理顺序 |
| `editThreshold` | number | 当前编辑的阈值 (0 = 用默认 0.80) |
| `src` | { kind: 'instance', idx: number } / null | 测试帧来源 |
| `previewOrig` | string (img URL) | 原图 |
| `previewProc` | string (img URL) | 应用 preprocessing 后的图 |
| `matchResult` | TemplateTestResult / null | 匹配结果 (复用 6.1.5 schema) |
| `busy` | bool | |
| `hint` | string | 提示文案 |

#### 6.2.2 操作

| 操作 | API |
|---|---|
| 拉模版列表 | `GET /api/templates/list` |
| 拉 preprocessing 预览 (实时, 改 editPreproc 触发) | `GET /api/templates/preview/{name}?preprocessing=g,c,s` 返回 `{ original_b64, processed_b64, size }` |
| 跑测试匹配 | `POST /api/templates/test` 带临时 preprocessing/threshold |
| 保存当前调试到模版 (持久化) | `POST /api/templates/save_meta` |
| dirty 检测 | 当前 `editPreproc` / `editThreshold` 跟模版的 meta 不一致时, 显示"未保存" |

#### 6.2.3 可选预处理算子 (按这个顺序勾选, 顺序影响结果)

固定 6 个 (`ALL_PREPROC`):

| key | 中文 | 说明 |
|---|---|---|
| `grayscale` | 灰度 | |
| `clahe` | 自适应直方均衡 | |
| `binarize` | 二值化 | |
| `sharpen` | 锐化 | |
| `invert` | 反色 | |
| `edge` | 边缘 | |

### 6.3 子页 `yolo` — 5 子子页

#### 6.3.1 采集 (Capture)

实时拉某 instance 当前帧加进 dataset。

`POST /api/labeler/capture` 请求:

| 字段 | 类型 | 说明 |
|---|---|---|
| `instance` | number | 实例索引 |
| `tag` | string | 标签 (默认 'manual'), 文件名前缀用 |

返回 `{ ok, name, size, width, height }`

操作:
- 选 instance → 一键截当前帧
- 自定义 tag (默认 'manual')
- 截后自动加进 dataset (未标注状态)

#### 6.3.2 数据集 (Dataset)

`GET /api/labeler/list` 返回 `DatasetList`:

| 字段 | 类型 | 说明 |
|---|---|---|
| `total` | number | 总图数 |
| `labeled` | number | 已标注 |
| `skipped` | number | 已跳过 |
| `remaining` | number | 待标注 |
| `classes` | string[] | 全局类别名列表 (按 id 顺序) |
| `per_class` | PerClassStat[] | 每类统计 (见下) |
| `items` | DatasetItem[] | 每张图 (见下) |

`PerClassStat`:

| 字段 | 类型 |
|---|---|
| `id` | number |
| `name` | string |
| `instances` | number (该类总 bbox 数) |
| `images` | number (该类出现的图数) |

`DatasetItem`:

| 字段 | 类型 | 说明 |
|---|---|---|
| `name` | string | 文件名 |
| `size` | number | 文件大小 |
| `mtime` | number (epoch ms) | |
| `labeled` | bool | 是否已标注 |
| `skipped` | bool | 是否标记为跳过 |
| `class_ids` | number[] | 该图含哪些 class id |

操作:
- 浏览 / 按 `class_ids` filter / 按 labeled 状态 filter
- 单删: `DELETE /api/labeler/image/{filename}`
- 批量删
- 看图: `GET /api/labeler/image/{filename}` (image)
- 导出 ZIP: `GET /api/labeler/export.zip` (含 images/ + labels/ + classes.txt)

#### 6.3.3 标注 (Labeler)

`GET /api/labeler/labels/{filename}` 返回 `{ boxes: LabelBox[], exists: bool }`

`LabelBox` (YOLO normalized format):

| 字段 | 类型 |
|---|---|
| `class_id` | number |
| `cx` | number 0-1 (中心 x) |
| `cy` | number 0-1 (中心 y) |
| `w` | number 0-1 (宽度) |
| `h` | number 0-1 (高度) |

`POST /api/labeler/labels/{filename}` body `{ boxes: LabelBox[] }` 保存。

类别管理:
- `GET /api/labeler/classes` 返回 `{ classes: string[], legacy_cids: number[] }`
- `POST /api/labeler/classes` body `{ name }` 加新类别, 返回 `{ classes, new_id }`

预标注:
- `POST /api/labeler/preannotate/{filename}` 用当前模型自动跑, 返回 `PreannotateResp`:
  - `boxes: PreannotateBox[]` (= LabelBox + `class_name` + `score`)
  - `image_size`, `duration_ms`, `count`

操作:
- Canvas 上画 bbox 选 class
- 上一张 / 下一张 / 跳过 / 删除 / 保存 (键盘快捷键)
- 预标注按钮 → 用模型预填 bbox, 用户校正

#### 6.3.4 测试 (Tester) — 单图推理

`GET /api/yolo/info` 拿模型信息 (见 6.3.5)。

`POST /api/yolo/test` 请求 (`YoloTestArgs`):

| 字段 | 类型 | 说明 |
|---|---|---|
| `instance` | number / undefined | 取该 instance 当前画面 |
| `decision_id` | string / undefined | 取该决策截图 |
| `session` | string / undefined | session 配 decision_id |
| `image_b64` | string / undefined | 上传 base64 图 |
| `conf_thr` | number / undefined | 置信度阈值 |
| `classes` | string[] / undefined | 仅检测这些类 |

注: 4 种 source 选一种, 跟 templates test 一样。

返回 `YoloTestResult`:

| 字段 | 类型 |
|---|---|
| `ok` | bool |
| `source` | string |
| `source_image_size` | [w, h] |
| `conf_thr` | number |
| `duration_ms` | number |
| `detections` | YoloDet[] |
| `annotated_b64` | string (含 bbox 标注的图) |
| `error` | string / undefined |

`YoloDet`:

| 字段 | 类型 |
|---|---|
| `name` | string (类别名) |
| `class_id` | number |
| `score` | number 0-1 |
| `x1`, `y1`, `x2`, `y2` | number (像素坐标) |
| `cx`, `cy` | number (中心点) |

(注: 不是跑完整 test_set 出 mAP — 这个 endpoint 是单图推理。)

#### 6.3.5 模型 (Model)

`GET /api/yolo/info` 返回 `YoloInfo`:

| 字段 | 类型 | 说明 |
|---|---|---|
| `available` | bool | 是否加载成功 |
| `model_path` | string | 当前模型路径 |
| `classes` | string[] | 类别列表 |
| `input_size` | number | 输入分辨率 (例 640) |
| `error` | string / undefined | 加载失败时 |

`POST /api/labeler/upload_model` 上传模型:
- multipart/form-data, field `file`
- 接受 .onnx 文件
- 返回 `{ ok, saved, size, latest }`

操作: 上传新模型 / 切换模型 / 看当前模型类别列表

### 6.4 子页 `ocr` — OCR / ROI 调试

#### 6.4.1 ROI 列表 schema (`GET /api/roi/list`)

`RoiListResp`:

| 字段 | 类型 |
|---|---|
| `items` | RoiItem[] |
| `count` | number |
| `yaml_path` | string (持久化 yaml 路径) |

`RoiItem`:

| 字段 | 类型 | 说明 |
|---|---|---|
| `name` | string | ROI 名 (key) |
| `rect` | [x1,y1,x2,y2] (0-1 normalized) | 归一化坐标, 跟分辨率无关 |
| `scale` | number | 截取放大倍数 (例 2 = 2x scale-up 给 OCR 用) |
| `desc` | string | 描述 |
| `used_in` | string | 哪些 phase / handler 用了它 |
| `preprocessing` | string[] | 持久化的预处理列表 |

#### 6.4.2 保存 ROI (`POST /api/roi/save`)

`RoiSaveReq`:

| 字段 | 类型 | 行为 |
|---|---|---|
| `name` | string | 必填 |
| `rect` | [x1,y1,x2,y2] (0-1) | 必填 |
| `scale` | number | 必填 |
| `desc` | string / undefined | |
| `used_in` | string / undefined | |
| `preprocessing` | string[] / undefined | 空数组 = 清空, undefined = 不动 |

返回 `{ ok, name, backup }` (backup 是上次的备份路径)。

#### 6.4.3 实测 OCR (`POST /api/roi/test_ocr`)

请求 (`RoiTestOcrReq`):

| 字段 | 类型 | 说明 |
|---|---|---|
| `instance` | number / undefined | 取该 instance 当前画面 |
| `decision_id` | string / undefined | 取该决策截图 |
| `session` | string / undefined | |
| `rect` | [x1,y1,x2,y2] (0-1) | 必填 |
| `scale` | number | 必填 |
| `preprocessing` | Preprocessing[] / undefined | 注: ocr 子页支持 `'grayscale' \| 'clahe' \| 'binarize' \| 'sharpen' \| 'invert'` (5 个, 没有 edge) |

返回 (`RoiTestOcrResp`):

| 字段 | 类型 | 说明 |
|---|---|---|
| `ok` | bool | |
| `full_image_b64` | string | 整图 base64 (data:image/jpeg;base64,...) |
| `cropped_image_b64` | string | 裁切后送 OCR 的图 |
| `source_size` | [w, h] | 原图尺寸 |
| `rect_pixels` | [x1,y1,x2,y2] | 像素坐标 (从 0-1 norm 算来的) |
| `scale` | number | |
| `ocr_results` | OcrHit[] | OCR 命中 |
| `n_texts` | number | 文本数量 |

`OcrHit`:

| 字段 | 类型 | 说明 |
|---|---|---|
| `text` | string | 识别文本 |
| `conf` | number 0-1 | 置信度 |
| `box` | [[x,y],[x,y],[x,y],[x,y]] | 4 角点, 在原图坐标系 |
| `cx`, `cy` | number | 中心点 |

#### 6.4.4 操作

- 选实例 / 选决策截图 / 上传图 (作 ROI 测试源)
- Canvas 上画 ROI 框 (拖拽), 实时显示 0-1 归一化坐标
- 选 preprocessing 算子 (5 个)
- 调 scale (放大倍数)
- 一键测试 → 显示原图 + 裁切图 + OCR 命中 (text + conf + 4 角点)
- 保存为新 ROI / 覆盖现有 ROI

---

## 7. 数据字典 (跨页面共享, 锁死)

### 7.1 InstanceState 枚举

(来自 `web/src/lib/store.ts:21-24`, 跟 backend 锁死)

| state 值 | 中文 label | tone | 阶段分类 |
|---|---|---|---|
| `init` | 等待启动 | idle | 启动前 |
| `accelerator` | 启动代理 | warn | 启动中 |
| `launch_game` | 启动游戏 | warn | 启动中 |
| `wait_login` | 等待登录 | idle | 启动中 |
| `dismiss_popups` | 清理弹窗 | warn | 启动中 |
| `lobby` | 大厅就绪 | live | 准备中 |
| `map_setup` | 设置地图 | warn | 配队中 |
| `team_create` | 创建队伍 | warn | 配队中 |
| `team_join` | 加入队伍 | warn | 配队中 |
| `ready` | 准备就绪 | live | 准备中 |
| `in_game` | 游戏中 | live | 主玩 |
| `done` | 完成 | live | 收局 |
| `error` | 出错 | error | 异常 |

### 7.2 TeamGroup / TeamRole / Squad

- `TeamGroup`: 'A' | 'B' | 'C' | 'D' | 'E' | 'F'
- `TeamRole`: 'captain' | 'member'
- Squad → Teams 映射:
  - 大组 1 = A + B
  - 大组 2 = C + D
  - 大组 3 = E + F
- 每队最多 3 slot (1 captain + 2 members)
- 总实例上限 18

### 7.3 PipelineStage 默认配置

(来自 `defaultPipelineStages`)

```
accelerator    → 代理就绪     → ['accelerator']
launch_game    → 启动游戏     → ['launch_game']
dismiss_popups → 清理弹窗     → ['wait_login', 'dismiss_popups']
lobby          → 进入大厅     → ['lobby']
team           → 组队         → ['team_create', 'team_join']
map_setup      → 设置地图     → ['map_setup']
ready          → 准备就绪     → ['ready']
in_game        → 游戏中       → ['in_game']
```

### 7.4 字段类型对照 (后端 → 前端)

| backend 字段 | TS 字段 | 类型 |
|---|---|---|
| `instance_index` | `index` | number |
| `nickname` | `nickname` | string |
| `qq` | `qq` | string |
| `game_id` | `gameId` | string |
| `group` | `group` | TeamGroup |
| `role` | `role` | TeamRole |
| `state` | `state` | InstanceState |
| `error` | `error` | string |
| `state_duration` | `stateDuration` | number (秒) |
| `adb_serial` | `adbSerial` | string |
| `stage_times` | `stageTimes` | Record<string, number> |
| `running` | `running` | bool |

---

## 8. WebSocket 实时事件 (`/ws/live`)

### 8.1 连接

- URL: `ws://localhost:8900/ws/live`
- 心跳: client 30s 一次 ping, server 回 pong
- `liveConnected` (store) bool 表示连接状态

### 8.2 事件类型 (来自 `LiveEvent` union)

#### 8.2.1 `decision`

| 字段 | 类型 |
|---|---|
| `type` | 'decision' |
| `id` | string |
| `ts` | number (epoch ms) |
| `instance` | number |
| `phase` | string |
| `round` | number |
| `outcome` | string |
| `tap_method` | string (可选) |
| `tap_target` | string (可选) |
| `tap_xy` | [number, number] / null (可选) |
| `verify_success` | bool / null (可选) |
| `tier_count` | number (可选) |

行为: 推到 `liveDecisions[instance]` 头部, cap 50 条/实例。

#### 8.2.2 `phase_change`

| 字段 | 类型 |
|---|---|
| `type` | 'phase_change' |
| `instance` | number |
| `from` | string |
| `to` | string |
| `ts` | number (epoch ms) |

行为: 推到 `phaseHistory[instance]`, 同步更新 `instances[instance].state`。

#### 8.2.3 `hello` / `pong`

| 字段 | 类型 |
|---|---|
| `type` | 'hello' / 'pong' |
| `ts` | number |
| `version` | string (可选) |

行为: 设 `liveConnected = true`。

#### 8.2.4 `intervene_ack`

| 字段 | 类型 |
|---|---|
| `type` | 'intervene_ack' |
| `command` | string |
| `instance` | number |
| `token` | string |
| `result` | string |
| `ts` | number |

行为: Toast 反馈干预命令是否被 backend 接受。

#### 8.2.5 `perf`

| 字段 | 类型 |
|---|---|
| `type` | 'perf' |
| `ts` | number |
| `global` | Record<string, number> (可选) |
| `instances` | Record<number, Record<string, unknown>> (可选) |

行为: 性能页 (§4) 实时更新。

---

## 9. 后端 API 完整清单

### 9.1 控制类

| endpoint | method | 用途 |
|---|---|---|
| `/api/start` | POST | 启动主跑 |
| `/api/start/{instance_index}` | POST | 启动单实例 |
| `/api/stop` | POST | 停止全部 |
| `/api/status` | GET | 拿全局状态 |
| `/api/health` | GET | 健康检查 |

### 9.2 配置类

| endpoint | method | 用途 |
|---|---|---|
| `/api/settings` | GET / PUT | 环境配置读写 |
| `/api/accounts` | GET / PUT | 账号列表读写 |
| `/api/emulators` | GET | 模拟器列表 |

### 9.3 实例画面 / 实时

| endpoint | method | 用途 |
|---|---|---|
| `/api/screenshot/{instance_index}` | GET | 实例当前画面 (image/jpeg) |
| `/ws/live` | WS | 实时事件流 |

### 9.4 决策档案

| endpoint | method | 用途 |
|---|---|---|
| `/api/sessions` | GET | session 列表 |
| `/api/decisions` | GET | 决策列表 (filter: session_id / instance / result) |
| `/api/decision/{decision_id}/data` | GET | 单条决策详情 |
| `/api/decision/{decision_id}/image/{filename}` | GET | 决策图 (shot_in/mid/out) |

### 9.5 记忆库

| endpoint | method | 用途 |
|---|---|---|
| `/api/memory/stats` | GET | 统计 |
| `/api/memory/list` | GET | 列表 |
| `/api/memory/{rid}` | GET | 单条 |
| `/api/memory/{rid}/snapshot` | GET | 截图 |
| `/api/memory/{rid}` | DELETE | 删 |
| `/api/memory/{rid}/mark_fail` | POST | 标记失败 |
| `/api/memory/pending/list` | GET | pending 列表 |
| `/api/memory/pending/{key}/sample/{idx}` | GET | pending 样本 |
| `/api/memory/pending/{key}/discard` | POST | 丢 pending |
| `/api/memory/dedup` | POST | 去重 |

### 9.6 性能

| endpoint | method | 用途 |
|---|---|---|
| `/api/perf/snapshot` | GET | 当前性能快照 |
| `/api/perf/series?metric=&window=` | GET | 时序数据 |

### 9.7 模板

| endpoint | method | 用途 |
|---|---|---|
| `/api/templates/list` | GET | 列表 |
| `/api/templates/file/{name}` | GET | 模版图片 |
| `/api/templates/detail/{name}` | GET | 详情 |
| `/api/templates/original/{name}` | GET | 原始大图 |
| `/api/templates/stats/{name}` | GET | 命中统计 |
| `/api/templates/preview/{name}` | GET | preprocessing 预览 |
| `/api/templates/{name}` | DELETE | 删 |
| `/api/templates/upload` | POST | 上传 |
| `/api/templates/test` | POST | 测试匹配 |
| `/api/templates/save_meta` | POST | 改元数据 |

### 9.8 YOLO

| endpoint | method | 用途 |
|---|---|---|
| `/api/yolo/info` | GET | 模型信息 |
| `/api/yolo/test` | POST | 测试 |
| `/api/labeler/list` | GET | dataset 列表 |
| `/api/labeler/image/{filename}` | GET | 帧图片 |
| `/api/labeler/labels/{filename}` | GET / POST | 标签读写 |
| `/api/labeler/image/{filename}` | DELETE | 删帧 |
| `/api/labeler/export.zip` | GET | 导出 |
| `/api/labeler/capture` | POST | 采集 |
| `/api/labeler/upload_model` | POST | 上传模型 |
| `/api/labeler/classes` | GET / POST | 类别读写 |
| `/api/labeler/preannotate/{filename}` | POST | 预标注 |

### 9.9 ROI / OCR

| endpoint | method | 用途 |
|---|---|---|
| `/api/roi/list` | GET | ROI 列表 |
| `/api/roi/save` | POST | 保存 ROI |
| `/api/roi/test_ocr` | POST | OCR 实测 |

### 9.10 Runner 测试 (PhaseTester 用)

| endpoint | method | 用途 |
|---|---|---|
| `/api/runner/phases` | GET | 可选阶段 |
| `/api/runner/phase_doc/{phase}` | GET | 阶段文档 |
| `/api/runner/test_phase` | POST | 跑测试 |
| `/api/runner/test_phase/{task_id}` | GET | 进度 |
| `/api/runner/cancel` | POST | 取消 |
| `/api/runner/test_new_session` | POST | 新会话 |

---

## 10. 4 种页面状态 (设计师必须覆盖)

每个有数据的页面都要画这 4 种状态:

| 状态 | 触发 |
|---|---|
| `empty` | 没数据 (零账号 / 零决策 / 零模版 等) |
| `loading` | 正在拉数据 (HTTP pending / WS connecting) |
| `normal` | 主使用场景 |
| `error` | 网络断 / 配置缺失 / API 5xx / WS disconnected |

特别是 §2 运行页的 4 个 sub-state (`empty` / `idle` / `running` / `running + anomaly`) 都要单独画。

---

## 11. 不能动的硬约束

- §7.1 的 InstanceState 枚举值 (跟 backend 锁死)
- §7.4 的字段命名映射 (跟代码锁死)
- §9 所有 endpoint URL (backend 已实现)
- §8 所有 WebSocket 事件 type 枚举 (跟代码锁死)
- §1.2 nav 项 dev/prod 区分 (build flag 决定)
- §5.3.1 队伍配置规则 (业务约束)

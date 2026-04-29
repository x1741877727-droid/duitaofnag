# Memory 联邦化 + 客户端遥测 — 设计方案

> **状态**: 设计阶段, 仅本地 pending 持久化已实现 (2026-04-30).
> server / 客户端遥测 / 联邦聚合等全部待开发.

## 一、产品定位

GameBot 分两个版本，UI 和能力完全不同：

| 版本 | 受众 | UI 页面 | 能力 |
|---|---|---|---|
| **dev (维护者用)** | 你自己 | Console / Archive / Memory / Templates / Perf / OCR Tuner / Settings + 待加的 **全局蓄水池审核页** | 完整调试 / 编辑 memory / 推 canonical 到 server |
| **prod (普通用户)** | 付费用户 | **只有 [运行] + [设置]** 两页 | 黑盒。后台静默拉 canonical memory + 静默上传 telemetry。**用户不知情、不可控、不能编辑** |

### 为什么只露 [运行] + [设置]

1. **质量保护** — 用户能编辑 memory / templates → 1 万人各改各的 → 数据污染 → 整体不可用
2. **认知负担** — 用户只想点开始按钮，看 5 层识别 + std + 蓄水池只会懵
3. **支持成本** — 暴露功能 = 报错路径 × N → 你 1 个人扛不住
4. **品牌信任** — 专业产品要简洁封装。露调试器看着像半成品
5. **隐私** — Archive 截图含游戏账号 / 私信 / 战绩，用户那看着反感
6. **迭代自由** — dev UI 能随便砸了重做。暴露给用户就要保兼容性
7. **反向工程门槛** — 调试页暴露 ROI / 模板 / phash / YOLO 类名等内部参数

## 二、架构: Tesla telemetry 模型

```
N 个用户 client                        中央服务器                      你 (维护者)
─────────────────                     ────────────                   ──────────────
[运行] + [设置]                        telemetry_events.db            完整 dev UI
                                       canonical_memory.db             + 全局蓄水池页
   ↓                                   aggregated_pending.db           ↑
   静默上传 (后台 worker, 用户不感知)                                    评审 / promote / ban
   ┌─→ 本地 5-confirm 通过的 entry  ─→ 收集 + 跨用户聚合                 ↓
   │   (mode B)                         ↓                              手动决策
   └─→ mark_fail 失败投票              展示给维护者                     ↓
                                                                       commit → canonical
   静默下载 (启动 + 定时 N 分钟)         ↓
   ←── canonical_memory snapshot ←─── canonical 版本号 + delta
```

## 三、关键决策 (用户已拍板)

### 决策 1: 上传模式 = **B (本地 5-confirm 通过后才上传)**

每客户端本地仍跑蓄水池 (5-confirm + std), 通过本地阈值的 entry 才上传。
- ✅ 节省带宽 (大部分噪音在本地就过滤掉)
- ✅ 上传质量高 (已经过本地 std 检查)
- ⚠️ 学习速度比 mode A (全量 raw 事件) 慢
- 决策依据: 隐私 + 带宽优先, 学习速度次之

### 决策 2: **不上传图片**, 但**本地保留 1-3 张**

- 上传 server 的字段: `target / phash / anchor_phash / x / y / size / target_w / target_h / first_ts / contributor_id`
- **不传任何 jpg / png 截图**
- 客户端本地: 每 commit 的 entry 保留 1-3 张样本快照 (用于本地 debug / 未来远程拉取诊断)
  - 当前: commit 时存 1 张 (`snapshots/<target>_<ts>.jpg`)
  - **TODO**: 改成存 1-3 张 (中位数样本 + 最早/最晚, 见实现说明)
- 决策依据: 隐私 (账号/私信泄露) + 带宽 + 服务端存储成本

### 决策 3: 客户端身份 = **延后**

- 当前阶段先不做 auth / 身份
- 未来: 每客户端首次启动生成 UUID + token, 存 user_data_dir
- 用 token 做配额限制 (每用户每小时上传上限) + 拉黑作恶用户

### 决策 4: server 部署位置 = **延后**

- 当前还没有 server, 写在这等以后
- 候选: `171.80.4.221` (跑 gameproxy / beacon 那台 Linux), 加 FastAPI 进程
- 端口候选: 9902 (跟 gameproxy 9900 / beacon 9901 邻近)
- 备选: 新机器, 看预算

## 四、数据模型 (草案)

### 客户端 SQLite (现在 + 持久化 pending 用)

```sql
-- 已入库 (canonical) — 现已存在
CREATE TABLE frame_action (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_name TEXT, phash TEXT, anchor_phash TEXT, dhash TEXT,
    qhash_0..3 TEXT, action_x INTEGER, action_y INTEGER,
    action_w INTEGER, action_h INTEGER,
    hit_count INTEGER, success_count INTEGER, fail_count INTEGER,
    last_seen_ts INTEGER, snapshot_path TEXT, note TEXT,
    history_json TEXT, archived INTEGER
);

-- 蓄水池持久化 (新增, 修当前 backend 重启丢 pending 的 bug)
CREATE TABLE pending_entries (
    pkey TEXT PRIMARY KEY,         -- "<target>__<phash16hex>"
    target_name TEXT NOT NULL,
    phash TEXT NOT NULL,           -- decimal int as string
    samples_json TEXT NOT NULL,    -- JSON: [[x,y,ts,anchor,snap_relpath],...]
    ts_first REAL NOT NULL
);
CREATE INDEX idx_pending_target ON pending_entries(target_name);
```

### server 端 SQLite (未来)

```sql
-- 客户端上传的 entry 候选
CREATE TABLE telemetry_uploads (
    upload_id INTEGER PRIMARY KEY,
    contributor_token TEXT,        -- 客户端身份
    target_name TEXT,
    phash TEXT, anchor_phash TEXT,
    x INTEGER, y INTEGER, w INTEGER, h INTEGER,
    confirm_count INTEGER,         -- 客户端本地累计的 confirm 次数 (mode B)
    std_x REAL, std_y REAL,
    uploaded_ts REAL
);

-- server 跨用户聚合 (维护者审核)
CREATE TABLE aggregated_pending (
    agg_id INTEGER PRIMARY KEY,
    target_name TEXT, phash TEXT,  -- group key
    contributors_json TEXT,        -- 哪些 token 贡献过
    total_confirms INTEGER,
    median_xy_json TEXT,
    first_seen_ts REAL,
    last_updated_ts REAL,
    status TEXT                    -- "pending_review" / "promoted" / "rejected"
);

-- 维护者 promote 后的正式 canonical (供客户端拉)
CREATE TABLE canonical_memory (
    entry_id INTEGER PRIMARY KEY,
    schema_version INTEGER,        -- 兼容性
    snapshot_version INTEGER,      -- 整批快照版本号
    target_name TEXT, phash TEXT, anchor_phash TEXT,
    action_x INTEGER, action_y INTEGER, action_w INTEGER, action_h INTEGER,
    promoted_ts REAL,
    promoted_by TEXT               -- 维护者标识
);
```

## 五、API 草案 (server 端待实现)

```
GET  /memory/snapshot?since=<version>
   返回: { schema_version, snapshot_version, entries: [...], deletions: [...] }
   客户端启动 + 定时拉. since 为 0 时全量, 否则增量.

POST /memory/contribute
   body: [ {target, phash, anchor_phash, x, y, w, h, confirm_count, std_x, std_y}, ... ]
   header: X-Contributor-Token: <uuid>
   ack: { received: N, dedupped: M }
   客户端 mode B 通过本地 5-confirm 后上传.

POST /memory/mark_fail
   body: { entry_id, reason? }
   header: X-Contributor-Token: <uuid>
   客户端用户点 "这条点错了" 触发. server 累计 fail_votes, 失败率高的 entry 自动 archive.

GET  /memory/version
   ack: { schema_version, snapshot_version, last_updated }
   客户端用来比对是否需要 pull.

(维护者后台, 鉴权另算)
GET  /admin/aggregated_pending     — 看待审核
POST /admin/promote/{agg_id}       — 提升为 canonical
POST /admin/reject/{agg_id}        — 否决
POST /admin/ban/{contributor_token} — 封号
```

## 六、客户端 UI 双版本切换

打包时由环境变量 / build flag 控制：

```
GAMEBOT_BUILD_VARIANT=prod  →  只编译 [运行] + [设置] 路由, 其他页路由 404
GAMEBOT_BUILD_VARIANT=dev   →  全 6 页路由, 默认
```

或者前端运行时根据 `/api/build/variant` 端点返回值动态隐藏 nav 项.

调试页代码本身不删, 只是 prod 版 nav 不暴露入口. 这样 dev 仍能 iter.

## 七、实施分阶段

### 阶段 1 — 本地 pending 持久化 ✅ (本次)

**目标**: 修 "backend 重启丢 pending" bug. 让 5-confirm 阈值跨次运行能累计.

**改动**:
- `backend/automation/memory_l1.py`: 加 `pending_entries` 表 + 持久化 _pending dict
- backend 启动时 `_load_pending_from_db()` 恢复内存状态
- `_pending_add` / 各种清理路径都同步落盘

**不依赖任何 server**, 单机用户立刻受益.

### 阶段 2 — 1-3 张快照保留 (待做)

修 commit 路径: 不再只存 1 张 `snapshots/<target>_<ts>.jpg`, 改成把 pending 的 5 个样本里的 1-3 张代表性快照拷贝到 commit 目录.
策略: 取离 median (final_x, final_y) 最近的 3 个样本.

### 阶段 3 — server skeleton + 客户端遥测 worker

待 server 机器到位后开工. 内容:
- server: FastAPI + SQLite + 上面 5 个 endpoint
- 客户端: 后台 asyncio task `telemetry_worker.py` — 监听 commit 事件 → 上传 → ack 后从队列移除 (失败重试 + 离线缓冲)
- 客户端: 启动时 `canonical_pull.py` — 拉最新 snapshot, 应用到本地 `frame_action` 表 (merge)

### 阶段 4 — 维护者审核 UI + 反作弊

- dev UI 加「全局蓄水池」页 - 看 server 的 aggregated_pending
- "promote / reject / ban" 按钮 + 批量操作
- 配额限制中间件 (每 contributor_token 每小时 N 条上限)
- 多源验证 (同 target+phash 必须 ≥ 3 contributor_token 才允许 promote)

### 阶段 5 — 用户版打包

- build 流程加 `GAMEBOT_BUILD_VARIANT=prod`
- 路由白名单 / nav 隐藏
- 移除 dev-only API endpoint

## 八、暂不做 / 永不做

- ❌ 用户能编辑 memory / templates / yolo 类名
- ❌ 客户端用户互相投票 / 评分
- ❌ 上传完整截图到 server
- ❌ 上传游戏账号 / 昵称 / 私信内容
- ❌ 客户端能选择 "我不想上传" — 上传是强制 (telemetry 服务条款覆盖)
- ❌ 客户端能看到自己上传了什么 — 黑盒

## 九、风险登记

| 风险 | 缓解 |
|---|---|
| server 离线 → 客户端 telemetry 卡 | 离线缓冲队列 + 自动重试 (最多 N MB), 不阻塞用户操作 |
| canonical schema 不兼容 | `schema_version` 字段, 客户端启动握手, 不兼容拒绝同步保护 |
| 个别用户瞎上传污染数据 | 阶段 4 配额 + 多源验证 + ban 机制 |
| 客户端被破解后伪造 contributor_token | token 有 expiry + server 端可吊销 (TODO) |
| 法务: 强制上传可能违反 GDPR 等 | 服务条款明确告知 + 提供数据删除请求接口 (`POST /user/forget_me`) |

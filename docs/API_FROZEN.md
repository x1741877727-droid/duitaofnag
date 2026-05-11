# API Frozen Contract (Day 0)

重构期间 (前端**一行不改**), 以下 HTTP/WebSocket 接口及其 schema 为硬约束——后端砍 endpoint 前必须确保前端已有兜底处理.

---

## HTTP Endpoints (必留)

### GET /api/accounts
前端调用: `App.tsx:80` (启动时 1 次)
```typescript
// 请求: -
// 响应:
interface AccountResponse extends Array<{
  qq: string;
  nickname: string;
  game_id: string;
  group: 'A' | 'B';
  role: 'captain' | 'member';
  instance_index: number;
}> {}
```
**调用频率**: 启动时 1 次  
**砍掉处理**: 前端 catch → `setAccounts([])`, UI 显示"账号配置不可用"  
**状态**: ✅ 后端已实现 (api.py:731)

---

### GET /api/emulators
前端调用: `App.tsx:107` (poll 每 5s)  
```typescript
// 响应:
interface EmulatorsResponse {
  instances: Array<{
    index: number;
    name: string;
    running: boolean;
    adb_serial: string;
  }>;
  ldplayer_path: string;
}
```
**调用频率**: 启动后每 5s 轮询  
**砍掉处理**: 前端 catch → setEmulators([]), 中控台显示"模拟器离线"  
**状态**: ✅ 后端已实现 (api.py:648, 5s TTL 缓存)

---

### POST /api/start
前端调用: `App.tsx:140` (用户点启动按钮)  
```typescript
// 请求: -（empty body）
// 响应:
interface StartResponse {
  ok: boolean;
  error?: string;
}
```
**调用频率**: 用户手动  
**砍掉处理**: 前端已有 try/catch (App.tsx:144), fetch fail → UI 显示"启动失败"  
**状态**: ✅ 后端已实现 (api.py:663)

---

### POST /api/stop
前端调用: `App.tsx:136` (用户点停止按钮)  
```typescript
// 请求: -
// 响应: { ok: boolean; error?: string; }
```
**调用频率**: 用户手动  
**砍掉处理**: 前端已有 try/catch, fetch fail → setInstances({})  
**状态**: ✅ 后端已实现 (api.py:694)

---

### GET /api/settings
前端调用: `settings-view.tsx:234` (进设置页 1 次)  
```typescript
// 响应:
interface SettingsResponse {
  ldplayer_path: string;
  adb_path: string;
  game_package: string;
  game_mode: string;
  game_map: string;
}
```
**砍掉处理**: 前端 catch → 显示默认值, 用户无法编辑  
**状态**: ✅ 后端已实现 (api.py:711)

---

### PUT /api/settings
前端调用: `settings-view.tsx:254` (用户修改设置)  
```typescript
// 请求:
interface SettingsUpdate {
  ldplayer_path?: string;
  adb_path?: string;
  game_package?: string;
  game_mode?: string;
  game_map?: string;
}
// 响应: { ok: boolean; }
```
**砍掉处理**: 前端 catch → toast 提示"设置保存失败"  
**状态**: ✅ 后端已实现 (api.py:721)

---

### GET /api/accounts → PUT /api/accounts
前端调用: `squad-builder.tsx:226` (编辑队伍)  
```typescript
// GET: 同上 /api/accounts
// PUT 请求:
interface AccountItem {
  qq: string;
  nickname: string;
  game_id: string;
  group: 'A' | 'B';
  role: 'captain' | 'member';
  instance_index: number;
}
// 响应: { ok: boolean; }
```
**状态**: ✅ 后端已实现 (api.py:745)

---

### GET /api/tun/state
前端调用: `StandbyState.tsx:42` (gameproxy 加速器状态)  
```typescript
// 响应: (反代自 gameproxy :9901)
interface TunStateResponse {
  ok: boolean;
  mode?: 'tun' | 'socks5' | 'offline';
  uptime_seconds?: number;
  counters?: Record<string, number>;
  error?: string;
}
```
**砍掉处理**: 前端 catch → UI 显示"加速器离线"  
**状态**: ✅ 后端已实现 (api.py:757)

---

### POST /api/runner/test_phase
前端调用: `PhaseTester.tsx:191` (阶段测试)  
```typescript
// 请求: FormData (multipart, 含测试参数)
// 响应: { task_id: string; }
```
**砍掉处理**: 前端 catch → 显示"测试不可用"  
**状态**: ✅ 后端已实现 (api_runner_test.py)

---

### POST /api/runner/cancel
前端调用: `PhaseTester.tsx:498`, `Header.tsx:69` (取消测试/运行)  
```typescript
// 请求: -
// 响应: { ok: boolean; }
```
**砍掉处理**: 前端已有 .catch(() => {}), 无提示  
**状态**: ✅ 后端已实现 (api_runner_test.py)

---

## HTTP Endpoints (可砍，但前端已兜底)

### GET /api/memory/list
前端调用: `memoryApi.ts:31` (记忆库页面)  
```typescript
// 响应:
interface MemoryListResp {
  items: MemoryRecord[];
  count: number;
  targets: { name: string; count: number }[];
  available: boolean;
}
```
**前端处理**: `fetchMemoryList()` 有 throw, 上层 component 需 catch  
**砍掉方案 A**: 后端保留但改 return `{items: [], available: false}`  
**砍掉方案 B**: 前端 wrap try/catch → 显示"记忆库已禁用"  
**状态**: ✅ 后端已实现 (api_memory.py:51)

---

### POST /api/memory/dedup
前端调用: `memoryApi.ts:110` (用户手动去重)  
```typescript
// 请求: -
// 响应: { merged: number; }
```
**砍掉处理**: 前端 catch → toast "去重功能不可用"  
**状态**: ✅ 后端已实现 (api_memory.py:154)

---

### GET /api/roi/list, POST /api/roi/save, POST /api/roi/test_ocr
前端调用: `roiApi.ts` (ROI 调试工具)  
**砍掉处理**: 前端 catch → 显示"ROI 工具不可用"  
**状态**: ✅ 后端已实现 (api_roi.py)

---

### GET /api/templates/list, POST /api/templates/test, POST /api/templates/upload, POST /api/templates/save_meta
前端调用: `templatesApi.ts` (模板管理)  
**砍掉处理**: 前端 catch → 显示"模板管理不可用"  
**状态**: ✅ 后端已实现 (api_templates.py)

---

### GET /api/perf/status, POST /api/perf/apply
前端调用: `perfApi.ts` (性能优化)  
**砍掉处理**: 前端 catch → UI 显示"性能优化不可用"  
**状态**: ✅ 后端已实现 (api_perf_optimize.py)

---

### GET /api/yolo/info, POST /api/yolo/test, GET /api/labeler/list, POST /api/labeler/capture, POST /api/labeler/upload_model, POST /api/labeler/classes
前端调用: `yoloApi.ts` (YOLO 标注工具)  
**砍掉处理**: 前端 catch → 显示"标注工具不可用"  
**状态**: ✅ 后端已实现 (api_yolo.py, api_yolo_labeler.py)

---

## WebSocket Endpoints (必留)

### WS /ws (主实时推流)
前端连接: `useWebSocket.ts:12` (App 启动自动连)  

**处理的消息类型**:

#### msg.type === 'snapshot'
**Payload**:
```typescript
interface SnapshotMessage {
  type: 'snapshot';
  running: boolean;
  instances: Record<string, Instance>;
  stats?: {
    running_duration?: number;  // 秒
  };
}

interface Instance {
  index: number;
  group: 'A' | 'B';
  role: 'captain' | 'member';
  state: InstanceState;  // 见下
  nickname: string;
  error: string;
  state_duration: number;  // 秒
  stage_times: Record<string, number>;  // 各阶段耗时 (秒)
}

type InstanceState = 
  | 'init' | 'lobby' | 'team_setup' | 'loading'
  | 'fighting' | 'standby' | 'ended'
  | 'error' | 'lost' | 'unknown';
```
**前端处理**: `useWebSocket.ts:20-44` → 转换并 dispatch `setInstances()`, `setIsRunning()`, `setRunningDuration()`  
**砍掉影响**: ❌ 不能砍 — 中控台核心依赖  

#### msg.type === 'state_change'
**Payload**:
```typescript
interface StateChangeMessage {
  type: 'state_change';
  data: {
    instance: number;     // instance index
    new: InstanceState;   // 新状态
  };
}
```
**前端处理**: `useWebSocket.ts:46-52` → dispatch `updateInstance()`  
**砍掉影响**: ❌ 不能砍 — 状态实时更新的核心

#### msg.type === 'log'
**Payload**:
```typescript
interface LogMessage {
  type: 'log';
  data: {
    timestamp: number;      // unix 秒
    instance: number;       // instance index, -1 表示系统日志
    level: 'info' | 'warn' | 'error';
    message: string;
    state?: InstanceState;  // 可选, 当前实例状态
  };
}
```
**前端处理**: `useWebSocket.ts:54-67` → dispatch `addLog()`, 右侧日志栏展示  
**砍掉影响**: ⚠️ 可砍 — 前端无日志栏时可无视

**后端可不推的 msg.type**: 任何不在上述清单的 type 前端都用 default 忽略 (useWebSocket.ts:69)

**连接管理**:
- 断线 3s 后自动重连 (useWebSocket.ts:76)
- 心跳: 后端接收 "ping" 字符串 (useWebSocket.ts:82), 后端无需返回 pong (心跳仅单向)

---

### WS /ws/live (中控台实时事件推流)
前端连接: `useLiveStream.ts:21` (App 启动自动连)  

**处理的消息类型**:

#### msg.type === 'decision'
**Payload**:
```typescript
interface LiveDecisionEvent {
  type: 'decision';
  ts: number;         // unix 秒, 前端×1000 转为 ms (useLiveStream.ts:100)
  phase?: string;
  decision?: string;
  // ... 其他决策字段 (前端不强制检查)
}
```
**前端处理**: `useLiveStream.ts:97-101` → dispatch `pushLiveDecision()`  
**砍掉影响**: ❌ 不能砍 — 决策日志的核心

#### msg.type === 'phase_change'
**Payload**:
```typescript
interface PhaseChangeEvent {
  type: 'phase_change';
  phase: string;
  // ... 其他字段
}
```
**前端处理**: `useLiveStream.ts:104-105` → dispatch `pushPhaseChange()`  
**砍掉影响**: ❌ 不能砍 — 阶段历史的核心

#### msg.type === 'intervene_ack'
**前端处理**: `useLiveStream.ts:107-109` → Day 3 处理 (目前忽略)  
**砍掉影响**: ⚠️ 可砍 — 前端尚未实现

#### msg.type === 'perf'
**前端处理**: `useLiveStream.ts:110-111` → Day 5 处理 (目前忽略)  
**砍掉影响**: ⚠️ 可砍 — 前端尚未实现

#### msg.type === 'hello' / 'pong'
**前端处理**: `useLiveStream.ts:113-116` → 忽略  
**砍掉影响**: ⚠️ 可砍

**连接管理**:
- 断线 3s 后自动重连 (useLiveStream.ts:58-61)
- 心跳: 前端每 25s 发 `{type: 'ping'}` (useLiveStream.ts:80-86), 后端可忽略或返 pong

---

## 重构期间硬规则

1. **Schema 字段只能加, 不能删/改类型**
   - ✅ `{ok: boolean}` → `{ok: boolean; detail?: string}` (加可选字段)
   - ❌ `{ok: boolean}` → `{status: 'ok'|'error'}` (改类型 = break)

2. **砍 endpoint 的流程**
   - Step 1: 前端先加 try/catch
   - Step 2: 后端改 return `{...response, deprecated: true}` 或 `{..., available: false}`
   - Step 3: 观察 1 周前端是否 fallback 正常
   - Step 4: 后端彻底删除

3. **WebSocket msg.type 砍了**
   - 前端 switch case 已经 default 忽略
   - 后端可直接停推, 0 破坏

4. **新接口可以加**
   - 后端新增 endpoint (如 `/api/vx/*`) 不破坏现有的
   - 前端想用新接口需更新 (这不算 "前端一行不改")

---

## 实例规模检查

前端代码中硬编码 "实例数" 的地方 (重构后改成动态):

| 文件 | 行号 | 内容 | 现状 |
|---|---|---|---|
| api.py | 582 | `[0, 1, 2, 3, 4, 6]` (Vision Daemon fallback) | 6 实例 hardcode |
| api.py | 613 | `for inst in [0, 1, 2, 3, 4, 6]` (test yolo path) | 6 实例 hardcode |
| useWebSocket.ts | 34 | `adbSerial: emulator-${5554 + i*2}` | 动态计算 ✅ |
| App.tsx | 90, 118 | 同上 | 动态 ✅ |

**重构检查清单**:
- [ ] 后端启动时探测真实实例数, 而不是 hardcode 6
- [ ] 前端 WebSocket snapshot msg 包含动态 instances count
- [ ] 测试: 2 实例、6 实例、12 实例场景都能正常初始化

---

## 已知可砍的 endpoints (后端死接口)

| 接口 | 理由 | 前端影响 | 状态 |
|---|---|---|---|
| GET /api/oracle/* | 旧 OracleView 已删, 前端无引用 | 0 | 可砍 |
| GET /api/decisions | 迁到 8901 debug server | 仅 debug 工具 | 可砍 |
| GET /api/sessions | 同上 | 仅 debug 工具 | 可砍 |

---

## 版本记录

- **Day 0** (2026-05-11): 初版冻结合约, 12 个必留接口 + 2 个 WebSocket, 对应前端 App v3.0


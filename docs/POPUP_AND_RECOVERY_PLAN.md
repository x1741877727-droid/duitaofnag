# 弹窗清理 + 闪退恢复 完整实施计划

> 作者: Claude Code (基于用户场景反推 + ALAS 商业框架调研)
> 日期: 2026-05-06
> 状态: 待执行
> 关联文件: `backend/automation/phases/p5_wait_players.py`, `backend/automation/watchdogs.py`, `backend/automation/popup_closer.py`

---

## 0. 目标 (一句话)

让脚本**在任何 phase 任何时刻被弹窗 / 闪退打断, 都能精确定位中断点 → 清弹窗 / 重启游戏 → 从最近可恢复点继续**, 永不"全流程从头再来"。

---

## 1. 现状的 4 个隐患

| 隐患 | 表现 | 后果 |
|---|---|---|
| H1 弹窗遮挡识别 | 弹窗盖住 lobby slot, YOLO 漏数 | 误判退队 / 候选位置错位 |
| H2 弹窗在 tap 瞬间冒出 | 决策时画面 OK, tap 时画面变 | tap 落到错误位置, 流程错乱 |
| H3 闪退后从 P0 重头跑 | 任何 phase 闪退都重新 P0/P1/P2/P3 | 浪费时间 + 跨实例协调失败 |
| H4 闪退后 P5 不知道谁是新人 | 重启后 4 个 slot 都是已知队员 | 无法判断是否还需要等真人 |

---

## 2. 总体架构 — 3 套独立系统

```
┌─────────────────────────────────────────────────────────────┐
│  系统 1: 弹窗清理 (Popup Dismissal)                          │
│   - 2 层漏斗: YOLO close_x 触发 → OCR 关键词识别            │
│   - 文字驱动 (跨电脑稳), 不用模板                            │
│   - 每个 phase 主 loop 调一次, 不开后台 daemon              │
│   - 命中即写决策, 不命中 0 开销                             │
├─────────────────────────────────────────────────────────────┤
│  系统 2: 状态持久化 (State Persistence)                       │
│   - per-instance: instance_state.json (phase / baseline /    │
│     known_slot_ids / kicked_ids / expected_id)              │
│   - per-squad: squad_state.json (leader_alive / team_code / │
│     team_code_valid / heartbeat)                             │
│   - phase enter/exit 写; 长 phase 周期写 (每 N 秒)           │
├─────────────────────────────────────────────────────────────┤
│  系统 3: 闪退恢复 (Crash Recovery)                           │
│   - 检测: process_watchdog 已有 (跑路/卡死)                  │
│   - 重启游戏 (已有 P1)                                       │
│   - 清弹窗到大厅 (已有 PopupCloser)                          │
│   - 读 instance_state.json → 决定从哪个 phase 续              │
│   - 每个 phase 声明"resume 条件" + "resume 入口"             │
│   - 跨实例: 队长闪退 → 队员暂停 (squad_state)               │
└─────────────────────────────────────────────────────────────┘
```

3 套**互相独立**, 可以分开实施 / 调试 / 测试。

---

## 3. 系统 1: 弹窗清理

### 3.1 2 层漏斗设计

```
每次 phase 主 loop 截图 + YOLO 推理后:
    ↓
[第 1 层] YOLO 已检测到 close_x 或 dialog?
    ├─ 没 → 跳过弹窗清理, 走业务逻辑 (0 开销)
    └─ 有 → 进第 2 层
        ↓
[第 2 层] 遍历 KNOWN_POPUPS, 对每个 spec:
    ├─ 在该 spec 的 anchor_roi 局部 OCR (~30ms)
    ├─ 找到 anchor_keyword 文字? + co_occurrence 全部命中?
    ├─ 都命中 → 这个就是 X 弹窗
    │   └─ 执行 dismiss_action (tap "拒绝" / tap close_x / 等)
    │       └─ 写决策 "PopupClose·{name}" → 跳过本轮业务逻辑
    └─ 没命中 → 试下一个 spec; 全没命中 → YOLO 误报, 跳过
```

### 3.2 KNOWN_POPUPS — Stage 1 实施版 (用户已采样 + 训练 YOLO)

**前提**: 用户已补样本 + fine-tune 完, close_x / dialog 类已覆盖以下 3 种弹窗的视觉特征。

```python
# backend/automation/popup_specs.py

@dataclass
class PopupSpec:
    name: str                                # 唯一 ID, 写入决策 outcome
    anchor_keywords: list[str]               # OCR 关键词 (任一命中即可)
    anchor_roi: tuple[float, float, float, float]  # 比例坐标 (x1,y1,x2,y2), 缩小 OCR 范围
    co_occurrence: list[str] = []            # 必须同时存在的其他文字 (防半渲染误判)
    dismiss_kind: Literal["ocr_tap", "fixed_xy"] = "ocr_tap"
    dismiss_value: str | tuple = ""          # OCR 文字 ("拒绝") 或固定 (x,y)
    phases_active: list[str] = ["all"]       # 哪些 phase 启用; "all" 表示全部
    excluded_phases: list[str] = []          # 显式排除 (e.g. P5 verify 中)
    # ── 防连发 / 致命标记 (新增) ─────────────────────────
    min_interval_s: float = 0.8              # 同一弹窗两次 dismiss 最小间隔
    fatal_threshold: int = 0                 # 连续触发 ≥ 此数 = 该 session 已死, 触发恢复; 0 = 不触发
    fatal_window_s: float = 60.0             # fatal_threshold 计数的滑动窗口

KNOWN_POPUPS = [
    # ─── 弹窗 1a: 好友邀请 (QQ 好友, 有 X 按钮) ───
    PopupSpec(
        name="friend_invite_qq",
        anchor_keywords=["来自好友"],
        anchor_roi=(0.55, 0.05, 1.0, 0.55),
        co_occurrence=["申请入队"],
        dismiss_kind="ocr_tap",
        dismiss_value="拒绝",            # 优先 tap 拒绝 (X 也 ok 但拒绝更明确)
        min_interval_s=0.8,
    ),
    # ─── 弹窗 1b: 推荐组邀请 (没 X, 有"不了 2s" 自动倒计时关闭) ───
    PopupSpec(
        name="friend_invite_recommend",
        anchor_keywords=["推荐组", "模拟器在线玩家"],
        anchor_roi=(0.0, 0.10, 1.0, 0.70),
        co_occurrence=["邀请组队"],
        dismiss_kind="ocr_tap",
        dismiss_value="不了",            # tap "不了" 立刻关 (不等 2s 倒计时)
        min_interval_s=0.8,
    ),
    # ─── 弹窗 2: 网络异常 ───
    PopupSpec(
        name="network_error",
        anchor_keywords=["无法连接", "检查你的网络"],
        anchor_roi=(0.10, 0.20, 0.90, 0.90),
        co_occurrence=["提示"],          # 共现"提示"标题, 避免跟其他对话框混
        dismiss_kind="ocr_tap",
        dismiss_value="确定",            # tap "确定" (注意: 跟"取消"区分)
        min_interval_s=2.0,              # 网络弹窗稍长间隔, 避免疯狂连点
        fatal_threshold=5,               # 60s 内触发 5 次 → 网络真崩 → 触发恢复 (退到登录后重启加速器)
        fatal_window_s=60.0,
    ),
    # ─── 弹窗 3: 账号被挤 (致命弹窗, 点确定后退到登录页) ───
    PopupSpec(
        name="account_squeezed",
        anchor_keywords=["账号在别处登录"],
        anchor_roi=(0.10, 0.20, 0.90, 0.90),
        co_occurrence=["提示"],
        dismiss_kind="ocr_tap",
        dismiss_value="确定",            # 必须点, 否则一直卡这屏
        min_interval_s=2.0,
        fatal_threshold=1,               # 触发 1 次就 fatal — 账号被顶, 当前 session 必死
        fatal_window_s=60.0,
    ),
    # ─── 待补 (用户后续采样): 系统公告 / 7 周年 / 收藏等级 / 闪退恢复 / 战令 / 签到 ───
]
```

### 3.2.1 防连发设计 (anti-spam debouncing)

**问题**: 网络弹窗连续出现 — tap 确定 → 1 秒后又冒一个 → tap → 又冒。如果不防, 脚本会:
- 死循环 tap 确定, 不能正常进任何业务
- 决策档案被刷屏, 看不出真问题

**方案**: per-spec 状态 + 滑动窗口计数

```python
# backend/automation/popup_specs.py 配套

class DismissalTracker:
    """跟踪每个 spec 的最近 dismiss 时间 + 60s 窗口计数."""
    def __init__(self):
        self._last_dismissed: dict[str, float] = {}      # spec.name → ts
        self._dismissal_history: dict[str, list[float]] = {}  # spec.name → [ts, ...]

    def can_dismiss(self, spec: PopupSpec) -> bool:
        """是否可以 dismiss: 检查 min_interval_s 冷却."""
        last = self._last_dismissed.get(spec.name, 0)
        return time.time() - last >= spec.min_interval_s

    def record(self, spec: PopupSpec) -> None:
        now = time.time()
        self._last_dismissed[spec.name] = now
        hist = self._dismissal_history.setdefault(spec.name, [])
        hist.append(now)
        # 修剪 fatal_window_s 之外的记录
        cutoff = now - spec.fatal_window_s
        self._dismissal_history[spec.name] = [t for t in hist if t >= cutoff]

    def is_fatal(self, spec: PopupSpec) -> bool:
        """该 spec 在 fatal_window_s 内是否已超过 fatal_threshold 次?"""
        if spec.fatal_threshold <= 0:
            return False
        hist = self._dismissal_history.get(spec.name, [])
        return len(hist) >= spec.fatal_threshold
```

每个 instance 一个 `DismissalTracker`, 挂在 ctx.runner 上 (跨 phase 持续累计)。

### 3.2.2 dismiss_known_popups helper 集成防连发 + 致命

```python
async def dismiss_known_popups(ctx, *, exclude_names=None) -> Optional[str]:
    runner = ctx.runner
    tracker = getattr(runner, "popup_tracker", None) or DismissalTracker()
    runner.popup_tracker = tracker

    # ... [第 1 层: YOLO close_x/dialog 触发] ...
    # ... [第 2 层: 遍历 KNOWN_POPUPS] ...

    for spec in KNOWN_POPUPS:
        if spec.name in (exclude_names or set()):
            continue
        if not _phase_match(spec, ctx):
            continue
        if not tracker.can_dismiss(spec):
            logger.debug(f"[popup] {spec.name} cooldown 中, skip")
            continue
        if not await _check_anchor_and_co(spec, shot, ctx):
            continue

        # 命中! 记录 + dismiss
        tracker.record(spec)

        # 致命 check
        if tracker.is_fatal(spec):
            logger.warning(f"[popup] {spec.name} 已触发 {spec.fatal_threshold} 次 → fatal!")
            # 写决策标记 + 触发恢复 (Stage 3 的 recovery 入口)
            await _record_fatal_popup_decision(ctx, spec)
            raise PopupFatalEscalation(spec.name)  # 上层捕获 → 走闪退恢复路径

        # 正常 dismiss
        await _execute_dismiss(spec, shot, ctx)
        await _record_dismiss_decision(ctx, spec)
        return spec.name

    return None
```

`PopupFatalEscalation` 异常由 phase handler 捕获, 触发跟闪退一样的 recovery 流程。
- network_error 触发 fatal: 60s 内 5 次 → 网络确实崩 → 退游戏 → 重启加速器 → 重 phase
- account_squeezed 触发 fatal (threshold=1): 直接 fatal → 标 squad leader_alive=false → 队员暂停 → 当前 instance 暂时无解 (要人工或 auto-relogin)

### 3.3 helper API (放在 PhaseHandler base 让所有 phase 复用)

```python
# backend/automation/phase_base.py 增加:

async def dismiss_known_popups(
    self,
    ctx: RunContext,
    *,
    yolo_dets: Optional[list] = None,   # 调用方已跑过 YOLO 就传进来, 复用结果省一次推理
    exclude_names: Optional[set] = None,  # 当前 phase 正在交互的 UI 名字, 不清这些
) -> Optional[str]:
    """从 KNOWN_POPUPS 顺序匹配, 命中 dismiss 一个就返回 spec.name. 一帧最多清一个,
    下一轮主 loop 再调一次清下一个 (防一次清太多打乱节奏 + 防误关).

    返回值:
      - None: 没弹窗, 业务流照常
      - str: 弹窗已清掉, 调用方应 continue 跳过本轮业务 (画面已变, 重新观察)
    """
    ...
```

### 3.4 调用范式 (各 phase)

**P5 主 loop 入口**:
```python
while True:
    if cancelled or timeout: ...
    shot = await screenshot()
    yolo_result = await self._yolo_detect_all(ctx, shot)
    
    # ★ 弹窗清理优先 — 命中就 continue, 让画面稳定下一轮再判
    dismissed = await self.dismiss_known_popups(
        ctx, yolo_dets=yolo_result.all_dets if yolo_result else None,
    )
    if dismissed:
        continue
    
    # 业务: baseline 比对...
```

**P5 verify 中 (打开了详情面板, 防误关自己 UI)**:
```python
# 在 _run_verify_step helper 里, tap 前调一次 last-mile
async def _run_verify_step(...):
    pre_shot = await screenshot()
    
    # ★ tap 前 last-mile 弹窗 check (商业 ALAS pattern)
    dismissed = await self.dismiss_known_popups(
        ctx, exclude_names={"player_detail_close"}  # 别误关我们的玩家详情面板
    )
    if dismissed:
        # 弹窗冒出来了, 这次 tap 取消, 让上层重新决策
        record_step("step_cancelled_popup", ...)
        return False
    
    # 写决策 + tap...
```

### 3.5 Phase-specific exclude_names 配置

```python
# 各 phase 主动声明"我现在打开了什么 UI", 防止那个 UI 被误关
P5_VERIFY_OPEN_UI = {"player_detail_close"}  # P5 verify 中
P4_MAP_OPEN_UI = {"map_dialog_close"}         # P4 选地图中
P3A_OPEN_UI = {"team_create_close", "team_qr_dialog_close"}
```

---

## 4. 系统 2: 状态持久化

### 4.1 per-instance state.json

路径: `%APPDATA%/GameBot/state/instance_{N}.json`

```python
# backend/automation/instance_state.py (新文件)

@dataclass
class InstanceState:
    instance_idx: int
    phase: str                              # 当前 phase
    phase_round: int = 0
    phase_started_at: float = 0.0
    last_save_ts: float = 0.0
    
    # 业务字段
    expected_id: Optional[str] = None
    selected_map: Optional[str] = None      # P4
    
    # P5 关键: 已知队员的 slot ↔ id 映射 (闪退后辨别新人)
    known_slot_ids: list[dict] = field(default_factory=list)
    # 每条: {"cx": 256, "cy": 290, "player_id": "1234567890",
    #        "verified_at": 1778080000, "is_baseline": True}
    
    kicked_ids: set = field(default_factory=set)
    
    # 跨阶段共享
    role: str = "unknown"  # captain/member
    squad_id: str = ""     # 关联到 squad_state
    
    def save(self): ...
    @classmethod
    def load(cls, instance_idx: int) -> Optional["InstanceState"]: ...
```

### 4.2 写盘时机

| 时机 | 写什么 | 频率 |
|---|---|---|
| Phase enter | phase=新 phase, phase_started_at=now, phase_round=0 | 每次切 phase |
| Phase exit (success/fail) | 清 phase-specific 字段, 但保留跨 phase (squad_id, role) | 每次出 phase |
| 长 phase 周期写 | 全字段 (尤其 P5 known_slot_ids) | 每 5s |
| 关键事件 | known_slot_ids 更新, kicked_ids 更新 | 立即写 |

### 4.3 per-squad state.json

路径: `%APPDATA%/GameBot/state/squad_{group_id}.json`

```python
@dataclass
class SquadState:
    group_id: str                    # 唯一 ID (e.g. "squad_001")
    leader_instance: int
    member_instances: list[int]
    
    # 队伍状态
    team_code: str = ""              # P3a 生成
    team_code_valid: bool = False    # 队长闪退时设 False
    team_code_generated_at: float = 0.0
    
    # 心跳 (队长每 5s 写一次, 队员定时读)
    leader_alive: bool = True
    leader_last_heartbeat: float = 0.0
    
    # 整队 phase
    squad_phase: str = "P0"          # 整队级 phase, 跟 instance phase 不一定同
    
    def save(self): ...
    @classmethod
    def load(cls, group_id: str) -> Optional["SquadState"]: ...
```

### 4.4 关键: P5 入队时记录 slot ID

P5 启动后 baseline 阶段 + 每次新真人验证后, 都更新 `known_slot_ids`:

```python
# baseline 阶段: 把当前 4 个 lobby cx 当作机器队员, 标 is_baseline=True
# 不主动 OCR ID (因为 baseline 都是机器号, 不需要识别)

# 真人加入后 verify 成功:
known_slot_ids.append({
    "cx": new_lobby_cx, "cy": new_lobby_cy,
    "player_id": got_id, "verified_at": time.time(),
    "is_baseline": False,
})
state.save()
```

闪退后: 重启 → 清弹窗 → 回到 P5 → 当前 4 个 lobby cx 跟 known_slot_ids 比对:
- 全在已知列表里 → 没人新加入, 继续等
- 多出来的 cx → 新真人, 走 verify 流程

---

## 5. 系统 3: 闪退恢复

### 5.1 闪退检测 (已有, 但要扩展)

```python
# backend/automation/watchdogs.py 已有 process_watchdog
# 扩展: 检测到游戏进程消失 → 触发 ON_CRASH 事件
```

### 5.2 恢复入口 (新)

```python
# backend/automation/recovery.py (新文件)

async def recover_from_crash(ctx: RunContext) -> str:
    """闪退后恢复. 返回应跳到的 phase 名."""
    state = InstanceState.load(ctx.instance_idx)
    if state is None:
        return "P0"  # 没历史状态, 从头跑
    
    crashed_phase = state.phase
    
    # 1) 重启游戏 (复用 P1 逻辑)
    await launch_game(ctx)
    
    # 2) 清所有弹窗到大厅 (复用 PopupCloser)
    await wait_for_lobby_clearing_popups(ctx)
    
    # 3) 决定从哪个 phase 续 — 每个 phase 自己声明 resume 规则
    return _resume_phase_for(crashed_phase, state, ctx)


def _resume_phase_for(crashed_phase: str, state: InstanceState, ctx) -> str:
    """每个 phase 都有自己的恢复策略."""
    if crashed_phase == "P0":
        return "P0"  # 启动加速器
    if crashed_phase == "P1":
        return "P1"  # 重启游戏
    if crashed_phase == "P2":
        return "P2"  # 清弹窗 (已经做了一遍, 再做也无害)
    
    # P3a (队长创建)
    if crashed_phase == "P3a":
        # 队长闪退 → 标记 team_code 失效, 队员收到通知会暂停
        squad = SquadState.load(state.squad_id)
        if squad and squad.leader_instance == state.instance_idx:
            squad.team_code_valid = False
            squad.leader_alive = False
            squad.save()
        return "P3a"  # 队长重新创建
    
    # P3b (队员加入)
    if crashed_phase == "P3b":
        # 队员闪退 → 等队长 team_code 仍有效就重新加入
        squad = SquadState.load(state.squad_id)
        if squad and squad.team_code_valid:
            return "P3b"  # 直接重 join
        else:
            return "WAIT_LEADER"  # 队员暂停, 等队长生成新码
    
    # P4 (选地图)
    if crashed_phase == "P4":
        # 队伍是否还在? 检查大厅是否有队员卡片
        if await _is_team_intact(ctx):
            return "P4"  # 队伍还在, 重选图
        else:
            return "P3a" if state.role == "captain" else "P3b"
    
    # P5 (等真人) — 关键 case
    if crashed_phase == "P5":
        # 直接回 P5, 已知 slot_ids 自动从 state.json 恢复
        return "P5"
    
    return "P0"  # 兜底


async def _is_team_intact(ctx) -> bool:
    """检测重启后队伍是否还在 (大厅有队员卡片)."""
    shot = await ctx.runner.adb.screenshot()
    yolo_result = await detect_lobby(shot)
    return len(yolo_result.lobby_dets) >= 2  # 自己 + 至少 1 队员
```

### 5.3 跨实例协调 (队长闪退队员暂停)

```python
# 队长心跳 (在每个 phase 主 loop 里写一次)
async def _leader_heartbeat(ctx):
    if ctx.role != "captain": return
    squad = SquadState.load(ctx.squad_id)
    if squad and squad.leader_instance == ctx.instance_idx:
        squad.leader_last_heartbeat = time.time()
        squad.leader_alive = True
        squad.save()

# 队员监听 (在 P3b 等阶段循环检查)
async def _check_leader_alive(ctx) -> bool:
    if ctx.role != "member": return True
    squad = SquadState.load(ctx.squad_id)
    if squad is None: return True
    if not squad.leader_alive: return False
    if not squad.team_code_valid: return False
    # 心跳超时 (>15s) 也认为队长挂了
    if time.time() - squad.leader_last_heartbeat > 15: return False
    return True

# 在每个队员 phase 入口 (P3b/P5/...) 调用:
if not await _check_leader_alive(ctx):
    return PhaseStep(WAIT, wait_seconds=2.0,
                     note="队长不在, 暂停等队长 squad 重组")
```

### 5.4 各 phase 的 resume 入口

每个 phase enter 时检查 InstanceState, 决定是冷启 or 续:

```python
# 例: P5WaitPlayersHandler.enter
async def enter(self, ctx):
    await super().enter(ctx)
    state = InstanceState.load(ctx.instance_idx)
    if state and state.phase == "P5" and state.known_slot_ids:
        # 续: 用 state.known_slot_ids 当 baseline
        ctx.team_slot_baseline = [(k["cx"], k["cy"]) for k in state.known_slot_ids]
        ctx.p5_known_slot_ids = state.known_slot_ids
        logger.info(f"[P5] resume from state, baseline {len(ctx.team_slot_baseline)}")
    else:
        # 冷启: 走原来 baseline 建立流程
        ctx.p5_known_slot_ids = []
```

---

## 6. 边界 case 清单 — 全部要处理

### 6.1 弹窗类
- [x] 普通好友邀请
- [ ] 网络异常断开 (有"重新连接"按钮)
- [ ] 7 周年庆典 (右上角)
- [ ] 收藏等级提升
- [ ] 战令任务完成
- [ ] 每日登录奖励
- [ ] 服务器维护公告
- [ ] 系统公告 (滚动条)
- [ ] 礼物发放 (生日 / 节日)
- [ ] 闪退恢复 / 重新登录提示
- [ ] 多端登录顶号提示 (有"确定"按钮)

### 6.2 闪退类 (per phase)
- [ ] P0 闪退 → 重 P0
- [ ] P1 闪退 → 重 P1
- [ ] P2 闪退 → 重 P2 (清完不会丢东西)
- [ ] P3a 队长闪退 → 队员暂停, 队长重 P3a, 重发码
- [ ] P3a 队长闪退 + team_code 已生效 → 队员收到 invalid → 暂停 → 等队长重发
- [ ] P3b 队员闪退 + team_code 还有效 → 直接重 P3b
- [ ] P3b 队员闪退 + team_code 失效 → WAIT_LEADER
- [ ] P4 闪退 + 队伍还在 → 重 P4
- [ ] P4 闪退 + 队伍解散了 → 退到 P3a
- [ ] P5 闪退 + 已 verify N 人 → 重 P5, 用 known_slot_ids 续
- [ ] verify 中闪退 (中间状态: 小卡片 / 详细面板 / OCR 完待 close)
- [ ] kick 中闪退 (中间状态: 已 tap 移出但 dialog 还在)
- [ ] 战斗中闪退 (P6+, 暂时不处理)

### 6.3 LDPlayer 类
- [ ] LDPlayer 卡死 (process 在但 UI 冻结) — process_watchdog phash 检测已有
- [ ] LDPlayer 进程消失
- [ ] adb 连接中断
- [ ] 截图返回 None 持续 N 秒

### 6.4 网络类
- [ ] 网络断开瞬间出弹窗 → 弹窗清理处理
- [ ] 长时间网络断开 → process_watchdog 触发 → 跳到 P0 重新加速器
- [ ] 单次 API 调用超时 (Cloudflare 隧道波动) — 业务流自带 retry

### 6.5 跨实例类
- [ ] 队长 + 队员同时闪退 → 都重 P0 (因为没人维护 squad_state)
- [ ] 队长 P3a 闪退 + 队员仍在 P3b waiting → 队员超时退 P3b → 重新整队
- [ ] 队员 1 闪退后 队员 2 已 P3b 完 → 队员 2 等队长 / 队伍 → 见后续
- [ ] 队员 P5 闪退 + 队长 P5 在跑 → 队员重启完队伍可能已开局, 跟队长不同步 → 用 squad_state.squad_phase 协调

---

## 7. 实施时间表 (估时, 按依赖排)

```
Week 1 (~3 天):
  [Day 1] Stage 1: 弹窗清理 + KNOWN_POPUPS 初版 3 条
            ├─ popup_specs.py 实现
            ├─ phase_base 加 dismiss_known_popups helper
            ├─ P5 主 loop + verify steps 接入
            └─ 用户采样 + 补 KNOWN_POPUPS

  [Day 2] Stage 2: 状态持久化
            ├─ instance_state.py + squad_state.py 实现
            ├─ phase_base enter/exit 钩子写状态
            ├─ P5 known_slot_ids 写入逻辑
            └─ 长 phase 周期写

  [Day 3] Stage 3: 单实例闪退恢复
            ├─ recovery.py 实现
            ├─ 各 phase 写 resume 规则
            ├─ P5 resume from known_slot_ids
            └─ 集成到 process_watchdog 触发链

Week 2 (~2 天):
  [Day 4] Stage 4: 跨实例协调
            ├─ squad_state heartbeat 机制
            ├─ 队员 _check_leader_alive
            ├─ 队长闪退 → 队员 WAIT_LEADER
            └─ 队长重生成 team_code 流程

  [Day 5] 全场景测试
            ├─ 用户配合制造每一种闪退场景
            ├─ 用户配合制造每一种弹窗场景
            └─ 修 corner case
```

---

## 8. 测试场景清单 (Day 5 用户协助)

### 8.1 Stage 1 弹窗清理验证 (优先, 现在就要测)

```
[ ] 场景 S1: P5 中好友邀请 (QQ 好友带 X 那种)
    预期: YOLO close_x+dialog 触发 → OCR "来自好友" 命中 → match friend_invite_qq spec
         → tap "拒绝" → 写决策 → 这一轮 continue → 下一轮恢复正常 baseline

[ ] 场景 S2: P5 中推荐组邀请 (没 X, 有"不了 2s")
    预期: YOLO dialog 触发 (没 close_x 也行) → OCR "推荐组" 命中 → tap "不了" → 关闭

[ ] 场景 S3: P5 中网络异常 (单次)
    预期: YOLO dialog 触发 → OCR "无法连接"+"提示" 命中 → tap "确定" → 关闭 → 继续业务

[ ] 场景 S4: P5 中网络异常连续 (2-3 次, 真实网络抖动)
    预期: 第 1 次 dismiss + 记录 → 1.5 秒内不会重复 dismiss (cooldown 2.0s) →
         第 2 次 (3s 后) dismiss → 第 3 次 dismiss → 都正常处理, 不疯狂连点

[ ] 场景 S5: 网络真崩 (60s 内触发 5 次 network_error)
    预期: 第 5 次触发时 tracker.is_fatal=True → 抛 PopupFatalEscalation →
         上层 catch → 写决策"FATAL: network_error 60s 5x" → 标记 instance 进入恢复流程
         (Stage 3: 退游戏 → 重 P0 加速器 → 重启游戏)

[ ] 场景 S6: 账号被挤
    预期: YOLO dialog 触发 → OCR "账号在别处登录" 命中 → tap "确定" →
         tracker.is_fatal=True (threshold=1) → 抛 PopupFatalEscalation →
         标 squad leader_alive=false → 当前 instance 进入"等待人工" 状态
         (Stage 3 之后再加 auto-relogin)

[ ] 场景 S7: P5 verify 详情面板期间冒好友邀请
    预期: dismiss_known_popups(exclude_names={"player_detail_close"}) 不会误关详情面板;
         friend_invite spec 不在 exclude_names → 正常 dismiss → verify 继续

[ ] 场景 S8: 真人加入瞬间冒出好友邀请 (race condition)
    预期: tap 前 last-mile check 拦截 → 先关弹窗 → step 标 "skipped_by_popup" →
         上层主 loop continue → 下一轮重新观察 → 找到候选 → 重新走 verify

[ ] 场景 S9: 弹窗连环 (好友邀请 + 网络异常同时存在)
    预期: 一轮只 dismiss 一个 (按 KNOWN_POPUPS 顺序) → continue → 下一轮 dismiss 第二个
         → 之后才回业务 (避免一次 dismiss 太多打乱节奏)

[ ] 场景 S10: P4 选地图弹窗的 X (防误关 — 自己的 UI)
    预期: 那个 X 不在 KNOWN_POPUPS 任何 spec → OCR 跑遍都没文字命中 → 不 tap → P4 流程不被打断

[ ] 场景 S11: P5 详情面板自身的 X (防误关 — 自己的 UI)
    预期: P5 verify 期间 exclude_names 已设, 即使 KNOWN_POPUPS 里有什么也不动详情面板
```

### 8.2 Stage 2-3-4 闪退恢复验证 (后续, 状态系统做完再测)

```
[ ] 场景 R1: P4 选地图时游戏闪退
    预期: 重启 → 清弹窗到大厅 → 检测队伍仍在 → 跳回 P4 → 重新选图

[ ] 场景 R2: P5 等人时游戏闪退, 已 verify 1 个真人
    预期: 重启 → 清弹窗 → 回 P5 → 加载 known_slot_ids = baseline 3 + 已 verify 1 →
        当前 4 lobby slot 都已知 (没多人) → 继续等下一个

[ ] 场景 R3: P5 等人时游戏闪退, 重启后已经 4 人 (新真人在闪退期间加入)
    预期: 重启 → 清弹窗 → 回 P5 → 加载 known_slot_ids = baseline 3 →
        当前 4 lobby slot, 1 个 cx 不在 known_slot_ids → 走 verify

[ ] 场景 R4: P3a 队长闪退, 此时队员 P3b 已 join 完
    预期: 队长 squad_state.team_code_valid=false → 队员 P5 入口 _check_leader_alive
        → leader_alive=false → 队员 WAIT_LEADER → 队长重 P3a 生成新码 → 队员重 P3b
        → squad 重组完成 → 都进 P5

[ ] 场景 R5: P3a 队长闪退 + 队员在 P3b 等队伍码途中
    预期: 队员检测 team_code_valid=false → 暂停 → 队长重生成 → 队员获取新码 → join
```

---

## 9. 评审 / 决策点 — 用户拍板

实施前要确认:

| # | 决策点 | 默认值 | 备选 |
|---|---|---|---|
| 1 | 状态文件路径 | `%APPDATA%/GameBot/state/` | 项目内 `logs/state/` |
| 2 | 长 phase 周期写盘间隔 | 5s | 3s / 10s |
| 3 | 队长心跳间隔 | 5s | 2s / 10s |
| 4 | 队长心跳超时阈值 | 15s | 10s / 30s |
| 5 | OCR 漏识也踢人? | **否** (保守) | 是 (激进) |
| 6 | KNOWN_POPUPS 命中后 wait 多久再继续? | 0 (立即下一轮) | 500ms |
| 7 | dismiss_known_popups 调用频率 | 每轮 1 次 | 每 N 轮 1 次 (省 OCR) |
| 8 | 闪退后是否立即跳 phase, 还是先 P0/P1 走一遍 | 立即跳 (优化) | 先全过 (保守) |

---

## 10. 不在本计划范围 (后续计划)

- 战斗中闪退恢复 (P6+) — 业务还没设计
- LDPlayer 重启 (process_watchdog 杀进程后再起) — 已有
- 跨设备状态同步 (多台 PC) — 暂不需要

---

## 11. 关键文件 — 哪些会被改

| 文件 | 改动类型 | 说明 |
|---|---|---|
| `backend/automation/popup_specs.py` | 新建 | KNOWN_POPUPS 配置 |
| `backend/automation/instance_state.py` | 新建 | per-instance 持久化 |
| `backend/automation/squad_state.py` | 新建 | per-squad 持久化 |
| `backend/automation/recovery.py` | 新建 | 闪退恢复入口 |
| `backend/automation/phase_base.py` | 改 | 加 dismiss_known_popups + state 钩子 |
| `backend/automation/phases/p5_wait_players.py` | 改 | 接入弹窗清理 + state.known_slot_ids |
| `backend/automation/phases/p3a_team_create.py` | 改 | 接入 squad_state team_code 写 + 心跳 |
| `backend/automation/phases/p3b_team_join.py` | 改 | 接入 _check_leader_alive |
| `backend/automation/phases/p4_map_setup.py` | 改 | resume 时检测队伍 intact |
| `backend/automation/watchdogs.py` | 改 | popup_watchdog 弃用 (或降级为 fallback) + crash 触发 recovery |
| `backend/runner_service.py` | 改 | 启动时跑 recover_from_crash |

---

## 12. 风险 + 缓解

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| KNOWN_POPUPS 漏配某种弹窗 | 高 | 该弹窗不能自动关 | 持续运营 + 用户截图反馈 + 加 spec |
| OCR 偶尔漏识关键词 | 中 | 该次弹窗不能关, 下轮再试 | co_occurrence 校验 + 多关键词组合 |
| state.json 写盘 race | 低 | 状态丢失 1 帧 | flock + atomic write (写 .tmp 再 rename) |
| 跨实例 squad_state race | 中 | 队员误以为队长挂了 | 心跳超时阈值 ≥ 3 倍正常间隔 (15s vs 5s) |
| 闪退恢复跳错 phase | 中 | 进入错误 phase 再 fail 跌回 P0 | 每个 resume 有"resume 条件" 校验 (e.g. P5 要求大厅 + 队伍非空) |
| P5 known_slot_ids 跟实际 slot 错位 | 中 | verify 错过新真人 | NMS 距离阈值 50px 容忍小漂移 + co_occurrence 校验 |

---

## 13. 完成判定

3 套系统都做完后, 验证:
- [ ] 用户故意制造 12 个测试场景, 全部 PASS
- [ ] 跑 24 小时不间断挂机, 无 phase 全流程重启 (除非真闪退恢复不了)
- [ ] 决策档案能完整反映每次弹窗清理 / 闪退恢复
- [ ] 代码新增的 PhaseHandler / Phase 子类无重复 boilerplate (DRY)

---

## End

下一步: 用户拍板第 9 节的决策点 → 开始 Stage 1 (弹窗清理) 实施。

# Day 3 Watchdog / Recovery / 突发情况审查

**审查日期**: 2026-05-11  
**范围**: V2 计划中 watchdog / recovery / 跨 phase 突发情况处理  
**目标受众**: 用户确认 V2 架构, Day 3 实施计划

---

## 1. vm_watchdog 现状 + V2 接入方案

### 1.1 当前 vm_watchdog 怎么工作

**位置**: `/Users/Zhuanz/ProjectHub/game-automation/backend/automation/vm_watchdog.py` (140 行)

**监测原理**:
- 每 30 秒跑 `ldconsole list2` (列出所有模拟器实例)
- 解析输出中 `running=1 但 pid=-1` 的行 (VirtualBox 进程崩溃, 壳还在)
- 发现死亡实例 → 调 `ldconsole launch --index N` 重启
- 防雪崩: 单实例累计重启上限 5 次, 超过则放弃 (配置坏了)

**关键参数**:
```python
interval = 30          # 检测周期
max_relaunch = 5       # 单实例重启上限
relaunch_gap = 15s     # 重启间隔 (避免疯狂 launch)
```

**触发条件** (精确定义):
```
NORMAL:   running=0, pid=-1          (用户主动关, 不重启)
DEAD:     running=1, pid=-1          (vbox 崩, 壳残留 ← 重启这个)
HEALTHY:  running=1, pid=大于0       (正常)
```

**错误处理**:
- ldconsole 路径找不到 → warning, 跳过监控 (不 fail)
- list2 命令失败 → debug log, 下轮重试
- launch 命令失败 → error log, 计数器仍++

**跟 runner_service 集成**:
```python
# backend/runner_service.py 启动时
wd = get_watchdog()
wd.start()    # 后台 task 跟 12 个 runner task 平级, asyncio 管理

# backend 关闭时
wd.stop()     # 取消 task, 清理
```

### 1.2 V2 怎么接 vm_watchdog?

**推荐方案: 全局 1 个 watchdog task, 跟 12 个 runner task 平级**

```
backend 启动:
├─ asyncio.gather(
│   ├─ vm_watchdog._loop()          ← 后台 1 个, 不跟任何 runner 绑定
│   ├─ runner_service.run_instance(0)
│   ├─ runner_service.run_instance(1)
│   ├─ ...
│   └─ runner_service.run_instance(11)
│ )
```

**优点**:
1. 解耦: 单 watchdog 不关心 runner 业务逻辑
2. 公平: 所有实例平等监控, 不受 runner 速度影响
3. 简单: 保留旧代码, 仅作为全局后台任务启动

**不建议的方案**:
- ❌ Per-runner 内置 hook: 每个 runner 启一个独立 watchdog, 重复代码 + 浪费资源
- ❌ Per-runner 检测点: runner 内 catch 抛异常后重启, 太晚了 (game 已经 panic)

### 1.3 V2 接入代码草稿

```python
# backend/main.py (新)
async def main():
    # 启 watchdog
    watchdog = get_watchdog()
    watchdog.start()
    
    # 启 12 个 runner
    runner_tasks = [
        asyncio.create_task(runner_service.run_instance(idx))
        for idx in range(12)
    ]
    
    # 一起 gather, 任何任务失败都能检测到
    try:
        await asyncio.gather(watchdog._task, *runner_tasks)
    finally:
        watchdog.stop()
```

**注意**: V2 ctx 不需要知道 watchdog 存在 (完全背景), 但 instance_state.py 可以记录 "发现 vm 死了并重启" 这个事件 (用于决策日志).

---

## 2. instance_state recovery 现状 + V2 接入方案

### 2.1 当前怎么工作

**位置**: 
- `/Users/Zhuanz/ProjectHub/game-automation/backend/automation/instance_state.py` (195 行)
- `/Users/Zhuanz/ProjectHub/game-automation/backend/automation/recovery.py` (173 行)

**持久化数据结构** (instance_{N}.json):
```python
@dataclass
class InstanceState:
    instance_idx: int
    phase: str = ""                        # P0/P1/P2/P3a/P3b/P4/P5
    phase_started_at: float                # 进 phase 时戳
    phase_round: int                       # phase 内第几轮
    
    expected_id: Optional[str]             # P5 等待的目标玩家 ID (10 位数字)
    role: str                              # captain / member / unknown
    squad_id: str                          # 关联队伍 (Stage 4 用)
    selected_map: Optional[str]            # P4 选的地图
    
    known_slot_ids: list[KnownSlot]        # P5 baseline slots (cx,cy,player_id,verified_at)
    kicked_ids: list[str]                  # P5 踢过的人 ID (防重复)
    
    schema_version: int                    # 版本号
    last_save_ts: float                    # 上次写盘时间
```

**写盘时机** (事件驱动, 不用定时器):
```
1. Phase enter:          加载旧状态 / 不存在则 fresh + save
2. P5 baseline 建立:     save (known_slot_ids 初始化)
3. P5 verify 成功:       save (known_slot_ids append 新真人)
4. P5 kick 成功:         save (kicked_ids 加新 ID)
5. Phase exit:           save (最终状态)
6. [新] 其他 phase 切换: save (状态同步, 见下)
```

**读盘 (恢复流程)**:
```python
# backend/automation/recovery.py: decide_initial_phase()
state = InstanceState.load(idx)  # None 或 InstanceState
if state is None or not state.phase:
    return "accelerator"         # 全新启动
else:
    return _resume_phase_for(state.phase, state.role, state.squad_id)
```

**恢复规则** (v1 保守策略):
```
P0/P1/P2:     resume → "accelerator"  (重头无副作用, 加速器快速过)
P3a/P3b/P4/P5: resume → "launch_game" (跳加速器, 直奔游戏 + 大厅)
```

**问题**: 
- ❌ 每 phase 切换时没写状态 (legacy P3a/P4 不走 PhaseHandler, 状态漂移)
- ❌ P5 resume 逻辑只在 P5.enter 时检查 known_slot_ids, P4 闪退回 launch_game 时不知道是否队伍还存在
- ❌ 队长闪退时 Stage 4 (squad_state) 的队员感知延迟长

### 2.2 V2 应该怎么做

**核心改进: 显式写状态 + 智能跳过已完成 phase**

```python
# 新增: 每个 phase enter/exit 时都显式同步状态
class Phase:
    async def enter(self, ctx):
        # 写状态: 即将进入这个 phase
        state = InstanceState.load(ctx.instance_idx)
        if state is None:
            state = InstanceState.fresh(
                ctx.instance_idx,
                phase=self.name,
                role=ctx.role,
                squad_id=ctx.squad_id,
            )
        else:
            state.phase = self.name
            state.phase_round = 0
            state.phase_started_at = time.time()
        state.save_atomic()
        
        # 业务逻辑
        await self.handle_frame(ctx)
    
    async def exit(self, ctx, result):
        # 写最终状态
        state = InstanceState.load(ctx.instance_idx)
        if state:
            state.phase = (self.name if result == DONE else "")  # DONE 时标记完成
            state.save_atomic()
```

**恢复逻辑改进**:
```python
def decide_initial_phase(state: Optional[InstanceState]) -> str:
    """重启时决定从哪个 phase 起"""
    if state is None or not state.phase:
        return "P0"  # 全新
    
    crashed_phase = state.phase
    
    # 智能决策: 看上次是否完成 + role 变化
    if crashed_phase in ("P0", "P1", "P2"):
        # 这些无 side-effect phase, 重头快速过
        return "P0"
    
    if crashed_phase == "P3a" and state.role == "captain":
        # captain 建队时闪退 → 重新建队 (队伍不存在)
        return "P1"
    
    if crashed_phase == "P3b" and state.role == "member":
        # member 加队时闪退 → 重试加队 (队长可能还在)
        # Stage 4 会检查 squad_state.leader_alive, 决定等还是重来
        return "P3b"
    
    if crashed_phase in ("P4", "P5"):
        # 队伍可能还活着 → 跳过 P1/P2/P3, 直奔 P4/P5
        # Stage 4 会检查 is_team_intact(), 决定用旧队还是新队
        return "P4"
    
    return "P0"  # 兜底
```

**P5 恢复场景**:
```python
class P5WaitPlayers:
    async def enter(self, ctx):
        state = InstanceState.load(ctx.instance_idx)
        
        if state and state.phase == "P5":
            # RESUME 场景: 上次在等真人时闪退
            # known_slot_ids 还在 → 拿来当 baseline (不重新采样)
            ctx.team_slot_baseline = [
                (k.cx, k.cy) for k in state.known_slot_ids
            ]
            logger.info(f"P5 RESUME: 加载 {len(ctx.team_slot_baseline)} 已知 slot")
        else:
            # FRESH 场景: 首次进 P5
            await self._build_baseline(ctx)  # 新采样
```

**每 phase 的写状态检查表**:

| Phase | enter 写 | exit 写 | 恢复时保留字段 |
|-------|---------|--------|------------|
| P0 | phase=P0 | phase="" (DONE 时) | - |
| P1 | phase=P1 | phase="" | - |
| P2 | phase=P2 | phase="" | - |
| P3a | phase=P3a | phase="" | squad_id, role |
| P3b | phase=P3b | phase="" | squad_id, role |
| P4 | phase=P4 | phase="" | squad_id, role, selected_map |
| P5 | phase=P5 | phase="" | expected_id, known_slot_ids, kicked_ids |

---

## 3. P5 邀请关闭功能提取

### 3.1 V1 现状 (p5_wait_players.py)

**在哪几行**: 第 295-320 行 (核心是 `dismiss_known_popups` 调用)

**怎么工作**:
```python
# P5 的轮询 loop 中, 每 round 前
yolo = runner.yolo_dismisser
all_dets = await yolo.detect(shot)  # 一次推理

# 弹窗清理 (跨 phase 通用, 优先级最高)
dismissed = await dismiss_known_popups(
    ctx, yolo_dets=all_dets, pre_shot=shot,
    current_phase="P5",
)
if dismissed:
    # 弹窗处理完了, 这轮跳过 baseline (等下轮画面稳定)
    continue
```

**弹窗清理实现** (`popup_dismiss.py` + `popup_specs.py`):
```python
# popup_specs.py 中列出 KNOWN_POPUPS:
# 1. friend_invite_qq      - "来自好友" + "申请入队" → tap "拒绝"
# 2. friend_invite_recommend - "推荐组" + "邀请组队" → tap "不了"
# 3. network_error         - "无法连接" → tap "确定"
# 4. account_squeezed      - "账号在别处登录" → tap "确定" (fatal)

# popup_dismiss.py 中的流程:
# 1. YOLO 检测 dialog bbox
# 2. 对每个 dialog OCR
# 3. 按 KNOWN_POPUPS 顺序匹配 anchor_keywords + co_occurrence
# 4. 第一个命中 → 在 dismiss_value 位置 tap
# 5. 防连发 tracker (min_interval_s 冷却)
```

**问题**:
- ✓ P5 有邀请清理 (因为 dismiss_known_popups 已经做了)
- ❌ 但其他 phase (P1/P2/P3/P4) **没有自动清邀请**, 用户要手动关闭

### 3.2 V2 提取: 跨 phase 邀请自动关闭

**推荐方案: Pre-perceive hook (业界标准)**

```python
# automation_v2/middleware/invite_dismiss.py (~80 行)

from typing import Protocol

class EmergencyHandler(Protocol):
    """突发情况处理 interface (middleware 模式)"""
    async def before_round(self, ctx) -> bool:
        """round 处理前检查. 返 True = 有突发情况, 本轮跳过"""
        ...

class InviteDismissal(EmergencyHandler):
    """邀请弹窗自动关闭 (所有 phase 通用)"""
    
    def __init__(self, yolo, ocr):
        self.yolo = yolo
        self.ocr = ocr
        self.last_dismiss_ts = {}
    
    async def before_round(self, ctx) -> bool:
        """每轮前检查邀请, 有则关闭并返 True (跳过本轮业务逻辑)"""
        shot = ctx.current_shot
        if shot is None:
            return False
        
        # YOLO 检测所有 dialog
        dets = await self.yolo.detect(shot)
        dialog_dets = [d for d in dets if d.name == 'dialog']
        
        if not dialog_dets:
            return False
        
        # 对每个 dialog OCR + 匹配邀请关键词
        for dialog in dialog_dets:
            roi = (dialog.x1, dialog.y1, dialog.x2, dialog.y2)
            ocr_hits = await self.ocr.recognize(shot, roi=roi)
            text = " ".join(h.text for h in ocr_hits)
            
            # 邀请判定 (3 种)
            if any(kw in text for kw in ["来自好友", "申请入队"]):
                # QQ 好友邀请
                return await self._dismiss_friend_invite(ctx, roi, text)
            elif any(kw in text for kw in ["推荐组", "邀请组队"]):
                # 推荐组邀请
                return await self._dismiss_recommend_invite(ctx, roi, text)
            elif "队伍邀请" in text:
                # 公会/队伍邀请
                return await self._dismiss_team_invite(ctx, roi, text)
        
        return False
    
    async def _dismiss_friend_invite(self, ctx, roi, text):
        """"来自好友" 邀请 → tap "拒绝" 按钮"""
        # OCR 找 "拒绝" 位置
        ocr_hits = await self.ocr.recognize(ctx.current_shot, roi=roi, mode="word")
        reject_btn = next((h for h in ocr_hits if h.text == "拒绝"), None)
        if reject_btn:
            # 黑名单这个 tap, 防连发
            if self._is_cooldown("friend_invite_qq"):
                return False
            await ctx.adb.tap(reject_btn.cx, reject_btn.cy)
            self.last_dismiss_ts["friend_invite_qq"] = time.time()
            logger.info(f"[邀请] inst{ctx.instance_idx} 关闭好友邀请 (P{ctx.phase_round})")
            return True  # 本轮跳过, 等画面稳定
        return False
    
    async def _dismiss_recommend_invite(self, ctx, roi, text):
        """推荐组邀请 → tap "不了" 按钮"""
        # 类似 _dismiss_friend_invite
        ...
    
    async def _dismiss_team_invite(self, ctx, roi, text):
        """队伍邀请 → tap "取消" 或右上 X"""
        ...
    
    def _is_cooldown(self, popup_name: str) -> bool:
        """检查是否在冷却期内 (防同一邀请 0.8s 内重复关闭)"""
        last = self.last_dismiss_ts.get(popup_name, 0)
        return time.time() - last < 0.8
```

**在各 phase 中集成**:

```python
# automation_v2/phases/phase_base.py
class PhaseHandler:
    async def handle_frame(self, ctx):
        # 1. 检查突发情况 (中断优先级高)
        for handler in ctx.emergency_handlers:
            has_emergency = await handler.before_round(ctx)
            if has_emergency:
                return PhaseStep(RETRY)  # 跳过本轮, 下轮重来
        
        # 2. 正常业务逻辑
        result = await self._do_handle_frame(ctx)
        return result

# 各 phase 启用邀请处理
async def main():
    ctx = RunContext(...)
    ctx.emergency_handlers = [
        InviteDismissal(yolo, ocr),
        # 可扩展: 其他突发情况
    ]
```

**V2 提取的文件**:
```
backend/automation_v2/
├── middleware/
│   ├── __init__.py
│   ├── emergency_handler.py   (InviteDismissal 接口定义)
│   └── invite_dismiss.py      (~80 行, 邀请关闭实现)
└── phases/
    └── phase_base.py          (修改: 加 before_round hook)
```

---

## 4. 突发情况完整清单 + 处理方案

### 业界突发情况分类

根据自动化脚本实战经验 (ALAS / magisk-frida 等), 完整清单如下:

| 突发情况 | 检测方法 | 处理动作 | 影响 phase | V1 状态 | V2 推荐 |
|---------|---------|---------|-----------|--------|--------|
| **邀请弹窗** | YOLO dialog + OCR "来自好友" / "推荐组" / "队伍邀请" | tap "拒绝" / "不了" / "取消" | 所有 (P1-P5) | P5 内置 | middleware |
| **网络异常弹窗** | YOLO dialog + OCR "无法连接" / "检查网络" | tap "确定" | 所有 | popup_specs | middleware |
| **账号被挤** | YOLO dialog + OCR "账号在别处登录" | tap "确定" → 退回登录页 (fatal) | 所有 | popup_specs (fatal_escalation) | middleware (fatal) |
| **PUBG 应用 crash** | adb pidof 检查 PUBG 进程 PID | am start GAME_PACKAGE | P1-P5 | _GameCrashError (单runner有) | watchdog task |
| **模拟器 VM 死了** | ldconsole list2 检查 running=1,pid=-1 | ldconsole launch | 所有 | vm_watchdog | watchdog task (全局) |
| **ADB 连接断开** | adb devices 检查设备列表 | 重新 adb connect (POC) | 所有 | adb_lite 重试 | adb_lite (keep) |
| **加速器掉线** | adb shell getprop (需加速器 app check) | 重启加速器 app OR P0 重跑 | P0 后 | (无) | P0 内置 check |
| **服务器繁忙/排队** | YOLO "排队中" / "服务器繁忙" | 等待 (sleep 5-10s) | P1-P2 | (无) | middleware |
| **验证码弹窗** | YOLO "验证码" / "人类验证" | 手动/截图上传给用户 | P0-P1 | (无) | fatal escalation |
| **客户端公告** | YOLO dialog + OCR "公告" / "通知" | tap 关闭按钮 | P1-P2 | popup_specs (可选) | middleware |
| **反作弊检测 (ACE)** | YOLO "反作弊" / "检测" 或进程 log | 等待 (通常 3-5s 自动过) | P2-P3 | (无) | watchdog (观察 log) |
| **用户手动干预** | (无法检测) | 暂停脚本 | 所有 | runner.cancel() | cancel signal |
| **滑屏交互意外触发** | phash diff 检测画面变化 | 等待 (画面稳定检测 phash) | P2-P5 | 手动 sleep | 帧差检测 |
| **长跑后内存满** | adb 定期 dumpmem check | 重启 LDPlayer OR 主动 GC | 所有 (长时间) | (无) | watchdog (周期检查) |
| **网络地址转换 (NAT) 超时** | adb 特定命令超时 | 重新建立 adb 连接 | 所有 | adb_lite timeout | adb_lite (keep) |
| **游戏内弹出式菜单** | YOLO "菜单" / "设置" | tap 游戏区域 / tap back | P2-P4 | (无) | tap 消除 |
| **队伍信息不同步** | 看 squad_state.known_members vs 实际画面差距 | 重建队伍 (P3a) | P3-P5 | (Stage 4) | Stage 4 decide |

### 4.1 详细处理方案

#### A. 邀请弹窗 (优先级: 高)

**检测方法**:
- YOLO class `dialog` (置信度 > 0.5)
- 对 dialog bbox 内 OCR, 匹配 anchor keywords:
  - "来自好友" + "申请入队" → 好友邀请
  - "推荐组" + "邀请组队" → 推荐组邀请
  - "队伍邀请" → 公会/队伍邀请

**处理动作**:
- 好友邀请: OCR 找 "拒绝" 按钮位置 → adb tap
- 推荐组邀请: OCR 找 "不了" 按钮 → adb tap
- 队伍邀请: tap 右上 X 或 "取消" 按钮

**在哪接**: middleware `before_round`, 所有 phase 都启用

**预期耗时**: 邀请弹出 → 消失 < 2s (OCR ~500ms + tap 125ms + 画面稳定 ~1300ms)

#### B. PUBG 应用 crash (优先级: 高)

**检测方法**:
```bash
adb shell pidof com.tencent.ig
# 返回值: PID 或空 (进程死了)

# 更可靠的方法: 定期 dumpsys activity 查 foreground app
adb shell dumpsys activity | grep "mFocusedActivity"
```

**处理动作**:
```bash
# 检测到 crash (pidof 返空) 或 foreground app != PUBG
adb shell am start -n com.tencent.ig/com.tencent.ig.MainActivity
# 等待 5s 后重新截图确认
```

**在哪接**: 
- **watchdog task** (独立线程, 每 5s 检查一次 adb pidof)
- 或者 runner 内 catch phash 检测到"黑屏/错误提示" 时主动检查

**预期**:
- Crash 检测: ~100ms (1 条 adb 命令)
- 重启耗时: ~3-5s (app 启动)

#### C. 加速器掉线 (优先级: 中)

**检测方法** (两种):
```bash
# 方法 1: 检查加速器 app 是否还活着
adb shell pidof com.lbe.security  # LBE VPN (常见加速器)
# 返回值: PID 或空

# 方法 2: 测 adb 某条命令延迟
# 如果 adb shell echo ok 超过 2s 还没返回 → 网络断了
```

**处理动作**:
```python
# 若加速器掉线 (P0 后的任意 phase):
# 选项 1: 重启加速器 app (快, 风险低)
await ctx.adb.shell("am start -n com.lbe.security/.MainActivity")
await asyncio.sleep(5)

# 选项 2: 回退到 P0 重新启加速器 (慢但安全)
return PhaseStep(FAIL, note="加速器掉线, 回 P0 重来")
```

**在哪接**:
- P0 exit 时: 校验加速器是否已启
- 其他 phase: 可选, 定期检查 (500ms 一次 adb shell echo ok)

**预期**:
- 掉线检测: ~200ms (adb 命令)
- 恢复耗时: ~5-10s

#### D. 网络弹窗 / 账号被挤 (优先级: 高)

已经在 `popup_specs.py` 中定义:
```python
PopupSpec(
    name="network_error",
    anchor_keywords=["无法连接", "检查你的网络"],
    dismiss_value="确定",
    min_interval_s=2.0,
    fatal_threshold=5,              # 60s 内 5 次 → fatal
    fatal_window_s=60.0,
)

PopupSpec(
    name="account_squeezed",
    anchor_keywords=["账号在别处登录"],
    dismiss_value="确定",
    min_interval_s=2.0,
    fatal_threshold=1,              # 1 次即 fatal
)
```

**在哪接**: middleware (跟邀请一样的流程)

#### E. 验证码弹窗 (优先级: 低 → 手动升级)

**检测方法**:
- YOLO dialog + OCR "验证码" / "人类验证"

**处理动作**:
- 无法自动过 (需人类交互 or 图像识别 API)
- 策略 1: 截图存盘 + 日志告警, 等人类处理
- 策略 2: 向用户弹 UI 对话框要求输入

**在哪接**: phase 内显式检测, 返 PhaseResult.WAIT_USER

#### F. 长跑后内存满 (优先级: 低)

**检测方法**:
```bash
# 定期检查 LDPlayer 进程内存 (每 15 分钟)
adb shell dumpsys meminfo com.tencent.ig | grep "TOTAL"
# 若超过阈值 (e.g., 2GB), 主动清理或重启
```

**处理动作**:
```bash
# 清理 app cache (温和)
adb shell pm clear com.tencent.ig
# 或直接重启 LDPlayer (暴力)
ldconsole restart --index N
```

**在哪接**: 后台 watchdog task (周期检查)

---

## 5. 推荐架构 (3 选 1 对比)

### 方案 A: Middleware 模式 (推荐!)

```python
# 优点:
# 1. 清晰: 所有突发情况处理集中在 middleware list
# 2. 顺序可配: 用户可调整处理优先级
# 3. 可扩展: 加新突发情况 = 加新 handler
# 4. 可禁用: env flag 关掉某个 handler

# 缺点:
# 1. 每 round 前都要遍历 list (通常 2-4 个 handler, 开销 < 10ms)

# 代码:
class PhaseHandler:
    async def handle_frame(self, ctx):
        # 中断点: before_round (高优先级突发情况)
        for handler in ctx.emergency_handlers:
            if await handler.before_round(ctx):
                return PhaseStep(RETRY)  # 本轮跳过
        
        # 正常业务逻辑
        result = await self._handle_frame(ctx)
        
        # 恢复点: after_round (记录)
        for handler in ctx.emergency_handlers:
            await handler.after_round(ctx, result)
        
        return result

# 使用:
ctx.emergency_handlers = [
    InviteDismissal(yolo, ocr),        # 优先级最高
    NetworkErrorHandler(ocr),           # 网络异常
    CrashDetectionHandler(adb),         # crash 检测
    MemoryCleanupHandler(adb, ldconsole),
]
```

**性能**: 6 实例, 每 instance 每 0.2s 一轮 → 总计 30 轮/s × 4 handler × 10ms = 1.2s 额外延迟 (理论上限, 实际不会这么差)

### 方案 B: 独立 watchdog task (简单突发)

```python
# 优点:
# 1. 不影响 runner 主 loop 速度

# 缺点:
# 1. 跟 runner 抢资源 (adb/yolo/截图)
# 2. 竞态条件: 同时 tap + 主 loop tap
# 3. 难调试: 并发问题

# 代码:
async def emergency_watchdog(ctx, runner_idx):
    """后台监控 crash/加速器/邀请 (不用 yolo, 改用 adb 命令)"""
    while not stop:
        # 检查 PUBG crash
        pid = await ctx.adb.pidof(GAME_PACKAGE)
        if pid is None:
            logger.warning(f"inst{runner_idx} PUBG crash, restarting...")
            await ctx.adb.start_app(GAME_PACKAGE)
            await asyncio.sleep(5)
        
        # 检查加速器活着
        vpn_pid = await ctx.adb.pidof(VPN_PACKAGE)
        if vpn_pid is None:
            logger.warning(f"inst{runner_idx} VPN down, restarting...")
            # ...
        
        await asyncio.sleep(2)

# 启用:
asyncio.gather(
    emergency_watchdog(ctx, 0),
    runner_service.run_instance(0),
)
```

**风险**: 
- watchdog tap 邀请 X 按钮时, runner 同时也在 tap popup close_x → 误 tap 坐标
- 解决: 加 lock (但这就变成了方案 A)

### 方案 C: Pre-perceive hook (业界用)

同 方案 A, 但不用独立 handler list, 直接在 perception 阶段:

```python
async def handle_frame(ctx):
    shot = await ctx.adb.screenshot()
    
    # 0. Pre-perceive: 紧急关闭弹窗 (最高优先级)
    dismissed = await dismiss_urgent_popups(ctx, shot)
    if dismissed:
        return RETRY  # 重来
    
    # 1. 正常 perception + decision
    ...
```

**等同于 A 但代码更简洁**

### 最终推荐

**使用方案 A (Middleware) + 隐含 B (watchdog task 仅限 crash/vm/内存)**:

```python
# runner 主 loop (方案 A)
class PhaseHandler:
    async def handle_frame(ctx):
        for handler in ctx.emergency_handlers:
            if await handler.before_round(ctx):
                return RETRY
        return await self._handle_frame(ctx)

# 后台全局任务 (方案 B, 仅限不抢资源的检查)
async def background_watchdog():
    while True:
        # 检查 vm (ldconsole list2, 不走 adb)
        check_vm_dead()
        
        # 检查内存 (可选, 周期性)
        check_memory()
        
        await asyncio.sleep(30)
```

**理由**:
- 邀请/网络/crash 用 middleware (跟 runner 同步)
- VM 死亡用全局 watchdog (独立后台任务)
- 二者不冲突, 各司其职

---

## 6. Day 3 实施任务 (按优先级)

### P0 必做 (Day 3 当天)

1. **[ ] 完整 watchdog 审查** (15 分钟)
   - 读 vm_watchdog.py 全文
   - 确认 ldconsole 路径在 12 个实例机器上都有
   - 测试 ldconsole list2 + launch 命令是否工作

2. **[ ] instance_state + recovery 显式同步** (1 小时)
   - 修改 phase_base.py: 每个 phase enter/exit 时都写状态
   - 修改 recovery.py: 增加 P3b/P4/P5 的智能恢复规则
   - 测试: 在 P4 闪退, 重启时是否正确恢复到 P4

3. **[ ] 邀请 middleware 骨架** (2 小时)
   - 新建 `automation_v2/middleware/invite_dismiss.py` (~80 行)
   - 实现 InviteDismissal.before_round()
   - 在 phase_base.py 中加入 emergency_handlers 调用

4. **[ ] 突发情况完整清单落盘** (30 分钟)
   - 本文档已列出 15+ 种
   - 每种标上"检测方法" + "处理动作" + "影响 phase"

### P1 可选 (Day 4-5, 如果 P0 提前完成)

5. **[ ] watchdog task 测试** (1.5 小时)
   - 集成到 backend/main.py, 跟 12 个 runner 一起 gather
   - 跑 30 分钟, 模拟实例死亡 (lidconsole kill 或断电), 观察自动重启
   - 检查 decision.json 是否有"vm_restarted"事件记录

6. **[ ] 网络弹窗 / 账号被挤** (2 小时)
   - 已有 popup_specs.py 定义, 改成 middleware handler
   - OCR 模块支持 roi 参数 (crop + 推理)
   - 测试: 模拟网络异常弹窗出现, 自动关闭

7. **[ ] crash 检测 (adb 路线)** (1.5 小时)
   - 方案: 每 phase 的 handle_frame 最开始检查一次 `adb shell pidof`
   - 若 pid 为空 → 重启游戏 + 返 RETRY (让 P1 re-run)
   - 测试: 跑期间 kill 游戏, 观察自动重启

### P2 后期 (Day 6-7, 灰度前)

8. **[ ] 加速器掉线** (1 小时)
   - P0 exit 时校验加速器 pid
   - 若掉线 → 重启或回到 P0 重来

9. **[ ] 内存满清理** (1.5 小时)
   - 后台 watchdog 每 15 分钟检查一次
   - 超过阈值 → 主动 pm clear 或重启 LDPlayer

10. **[ ] 验证码弹窗手动升级** (1 小时)
    - 检测后截图 + alert 用户
    - 用户输入 → 保存 cache (防重复输入)

### 验证清单

每项实施后的验证:

- [ ] 12 实例并发 30 分钟, 无异常
- [ ] decision.jsonl 含完整 trace_id + 时间戳
- [ ] 邀请弹出自动消失 (< 2s)
- [ ] 网络异常弹出自动关闭 (< 1s)
- [ ] crash 自动检测重启 (< 5s)
- [ ] VM 死亡自动重启 (< 20s, 包括 ldconsole launch 等待)
- [ ] 闪退恢复: P4 → 重启 → P4 (不是 P1)
- [ ] P5 resume: 已知 slot 加载, 不重复采样

---

## 7. 架构时间线

```
Day 3 (今):
├─ 审查完整 watchdog/recovery/邀请处理架构
├─ 发现问题点 + 确认方案
└─ P0 任务开始 (watchdog 集成 + state 同步)

Day 4:
├─ 邀请 middleware 实装
├─ 跑完整 P0→P5 流程, 邀请自动消失测试
└─ 完成 P1 任务 (watchdog test + crash 检测)

Day 5:
├─ 网络弹窗/加速器掉线
└─ 集成测试

Day 6-7:
├─ 内存满清理
├─ 验证码弹窗 (可选)
└─ 全量灰度前 smoke test

Day 8 (灰度):
├─ env flag 控制 v1/v2 切换
├─ 1 实例 v2 + 5 实例 v1 (混合)
└─ 观察 12 小时, 无 critical bug → 升全量
```

---

## Sources & References

- `/Users/Zhuanz/ProjectHub/game-automation/backend/automation/vm_watchdog.py` (140 行)
- `/Users/Zhuanz/ProjectHub/game-automation/backend/automation/instance_state.py` (195 行)
- `/Users/Zhuanz/ProjectHub/game-automation/backend/automation/recovery.py` (173 行)
- `/Users/Zhuanz/ProjectHub/game-automation/backend/automation/phases/p5_wait_players.py` (1101 行, 第 295-320 行邀请处理)
- `/Users/Zhuanz/ProjectHub/game-automation/backend/automation/popup_specs.py` (100 行, 弹窗配置)
- `/Users/Zhuanz/.claude/plans/apk-jolly-gem.md` (重构大计划)
- `/Users/Zhuanz/ProjectHub/game-automation/docs/REFACTOR_AUDIT.md` (Day 0 审计)

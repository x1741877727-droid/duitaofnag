# 同步匹配 + 同局检测方案

## 已验证可行

### 1. 同局检测 — 网络层方案（已验证 ✓）

**原理**：匹配成功后游戏会连接战斗服务器。两队连的同一个 IP:Port = 同一局。

**验证数据**：
```
大厅状态: 101.33.48.163 不存在
加载页面: 101.33.48.163:5692 新增 ESTABLISHED
对局中:   101.33.48.163:5692 保持 ESTABLISHED
回到大厅: 几秒后连接消失
```

**检测方法**：
```bash
# 高频轮询（每100ms一次），检测新增连接
cat /proc/<PID>/net/tcp
```

**判断逻辑**：
1. 记录大厅状态的基线连接
2. 匹配开始后持续轮询 `/proc/<PID>/net/tcp`
3. 出现新的 ESTABLISHED 连接 = 进入加载页
4. 两队比对新增 IP:Port
   - 相同 → 同一局 ✓ 继续游戏
   - 不同 → 不是同一局 → `iptables -A OUTPUT -d <IP> -j DROP` 断网 → 退出

**性能**：
- 读 `/proc/<PID>/net/tcp` ≈ 1ms（纯文件读取，不碰游戏内存）
- 断网 iptables ≈ 1ms
- 从加载页出现到断网 < 200ms
- **零封号风险**（不读游戏内存，不修改游戏）

### 2. 同步匹配 — 信号同步

**方法**：两个 runner 都 await 同一个 `asyncio.Event`，同时点匹配按钮。

```python
# runner_service.py
match_event = asyncio.Event()

# A队 runner
await match_event.wait()
await adb.tap(match_button_x, match_button_y)

# B队 runner
await match_event.wait()
await adb.tap(match_button_x, match_button_y)

# 控制器
match_event.set()  # 两队同时触发
```

**精度**：两队点击间隔 < 50ms。

---

## 完整对战流程

```
阶段1: 准备（已实现）
  加速器 → 启动游戏 → 弹窗清理 → 大厅
  → 3个机器人组队 → 地图设置

阶段2: 等待真人玩家
  → 生成QR码 → 发给指定玩家
  → 检测第4人加入（像素检测槽位变化）
  → 身份验证（待定，见下方问题）
  → 5分钟超时

阶段3: 同步匹配
  → A队 + B队同时点匹配
  → 记录各自基线连接

阶段4: 同局检测（毫秒级）
  → 高频轮询 /proc/PID/net/tcp（每100ms）
  → 检测到新增 ESTABLISHED 连接 = 进入加载页
  
  情况A: 两队都进入加载页
    → 比对战斗服务器 IP:Port
    → 相同 = 同一局 ✓
    → 不同 = 断网退出 → 回到阶段3
  
  情况B: A队进了，B队还没进
    → A队等待 1-2 秒
    → B队也进了 → 比对 IP
    → B队超时没进 → A队断网退出 → 回到阶段3
  
  情况C: 两队都没进（匹配失败）
    → 重新匹配

阶段5: 对局中
  → （后续实现）
```

---

## 待解决问题

### 真人玩家身份验证

**需求**：确认加入队伍的第4人是指定玩家（防QR码泄漏）。

**已尝试方案**：
| 方案 | 结果 |
|------|------|
| 内存读取玩家名 | 不稳定，ACE 检测导致崩溃/卡死 |
| OCR 读名字 | 特殊字符不准 |
| UIAutomator | UE4 不暴露 UI 元素 |
| logcat 抓包 | 无业务日志 |
| 聊天验证码 | 玩家可能被禁言 |

**内存读取崩溃分析**：
- 4KB 单页 pread 0.1 秒扫完，数据完整，但几秒后游戏崩溃
- 2MB dd 块也崩
- 不是读取粒度问题，是 ACE 检测到 /proc/pid/mem 被外部进程读取后延迟杀进程
- 外挂工具（GameGuardian）不崩是因为它们有额外的反检测机制（进程隐藏、fd 隐藏等）

**待探索方向**：
1. 隐藏 memreader 进程 — 改进程名、隐藏 /proc/self
2. 通过内核模块读取（绕过 /proc 文件系统）
3. 利用 LDPlayer 的宿主机直接读虚拟机内存（hypervisor 层）
4. 改进 OCR — 用游戏字体渲染已知名字做模板匹配
5. 接受"只验证人数不验证身份"（QR码本身是私发的）

---

## 关键代码位置

| 文件 | 说明 |
|------|------|
| `backend/runner_service.py` | 状态机 + 队伍管理 |
| `backend/automation/single_runner.py` | 各阶段执行 |
| `tools/memreader.c` | 内存读取器（v9，4KB pread） |
| `tools/team_scan.py` | Python dd 扫描方案 |
| `backend/automation/guarded_adb.py` | 弹窗守卫 |

# 和平精英防封规则 — 花瓶 1.fp 移植版

## 战果记录

| 指标 | 数据 |
|---|---|
| 部署时间 | 2026-04-20 17:40 |
| 首次命中 | 16:41:33（进大厅 ~1min 后） |
| 持续时长 | **24+ 分钟不封**（用户主动结束监控） |
| 命中频次 | ~15 次/分钟 稳定 |
| 前基线 | pos21 patch = 15 分钟封号 |

**结论**：源自花瓶/ccproxy 的 1.fp 两条 Advanced 规则，把 pos21 的"破坏派"方案彻底换成"伪装派"，封号时长从 15 分钟顶到 24+ 分钟且无封号迹象。

---

## 两条规则

规则来源：`~/Downloads/1.fp`（花瓶作者实测声明不封号），在 [relay.go:16](relay.go#L16) `patchWPEAdvanced` 实现。

### Rule 1：设备指纹上报子包破坏

```
find:   01 0A 00 23   （任意位置）
modify: +3 → 0x37
        +11 → 0x00
```

**WPE 原始 XML**（Mode=Advanced，StartFrom=Position）：
```xml
<Mode>Advanced</Mode>
<Action>Replace</Action>
<StartFrom>Position</StartFrom>
<Search>0|01,1|0A,2|00,3|23</Search>
<Modify>3|37,11|00</Modify>
```

**含义**：
- `01 0A 00 23` 是 ACE 包内**嵌套子包头**（"设备指纹上报"），紧跟其后是 32 字符的 UUID hex 字符串
- pcap 验证命中位置 = **pos 61**（ACE data1 通道 size=218 包里）
- `+3` (= 子包 type 字段) 0x23→0x37 让服务端按另一种 type 解析 → 包被解析但被识别为"无效/忽略"
- `+11` (= flag 字节) 0x01→0x00 把"有违规/触发检测"flag 清零 → 服务端看到的是"无违规样本"

**命中端口**：C→S 443/9900/10012

---

### Rule 2：17500 下发 marker 破坏

```
find:   0A 92   （任意位置）
modify: +1 → 0x11
```

**WPE 原始 XML**：
```xml
<Mode>Advanced</Mode>
<Action>Replace</Action>
<StartFrom>Position</StartFrom>
<Search>0|0A,1|92</Search>
<Modify>1|11</Modify>
```

**含义**：
- `0A 92` 是 17500 端口服务端下发数据里的**加密/压缩 marker**（类 protobuf/zlib 子帧 header）
- 命中位置分散在 1460/2920 字节大包的深处（TLS record / 压缩 blob 内部）
- 改 +1 字节后 → 客户端反序列化失败 → **ACE 客户端收不到新的检测规则下发**
- 等于让 ACE 引擎"冻结"在出厂版本，不再更新违规识别模型

**命中端口**：主要 S→C 17500 + 少量 S→C 443

---

## 协同机制（为什么不封）

```
Rule 1 (C→S)     ──❌──→  服务端设备指纹上报被污染
                            服务端模型缺失"违规"输入

Rule 2 (S→C)     ──❌──→  客户端检测规则更新被污染
                            ACE 引擎检测能力冻结

等效于"装死客户端"：
  - 服务端看你 = 一切正常（没违规数据进模型）
  - 客户端看自己 = 一切正常（检测规则是旧的，没发现问题）
```

### 对比之前的破坏派

| 方案 | 机制 | 服务端视角 | 结果 |
|---|---|---|---|
| pos21 `0x69→0x4D` | 破坏 ACE 外层 tamper 字节 | "这个包解析失败 = 异常样本" | 15 min 慢封 |
| 1.fp Rule 1+2 | 改子包 type/flag 为合法值 | "这个包是合法的，指纹正常" | 24+ min 未封 |

**核心差异**：破坏派让服务端"看不见"（累积为异常），伪装派让服务端"看到清白的"（喂为白样本）。

---

## 游戏版本更新后的修复流程

### 第一步：判断是不是规则失效

**症状**：重新测试，封号时间突然退回到 5-10 分钟。

**验证**：在 `/opt/gameproxy/proxy.log` 搜 `[WPE-ADV C→S]` 命中率：
- 还能看到命中 → pattern 没变，可能是其他防护变了
- 命中数骤降到接近 0 → pattern 改了，需要重新 mine

### 第二步：找新的 pattern（按重要性排序）

#### 方法 A. 等花瓶作者发新的 1.fp
- gitee 仓库（之前是 ez9673/udp） 通常会更新
- 百度网盘"225 个游戏封包合集"这类 rar 每月会有新版
- **最省事，优先用这个**

#### 方法 B. 从现有 pcap 对比推新 pattern

运行 `/tmp/find_systematic_diff.py`（本次会话写的）：
```bash
python3 /tmp/find_systematic_diff.py
```

它做的事：
1. 加载"我们的"pcap + 六花/花瓶的 pcap
2. 按 size + 方向分桶
3. 找两者字节分布≥90% 稳定但不一致的位置
4. 排除 UUID/时间戳/session nonce 等自然差异

**注意**：pcap 必须是同一层级（都在 payload 层或都在 TCP 段层），否则分布不可比。我们的 proxy 抓的是 payload 层；tcpdump 抓的是 TCP 段层。

#### 方法 C. 逆向 libtersafe.so 找新的 sub-packet 格式

用 Ghidra 分析 `libtersafe.so`（Memory: `libtersafe_reverse_breakthrough`）：
- `tss_recv_sec_signature` 是上报入口
- `FUN_005c04f8` 主循环每秒一轮
- data1-4 4 条通道各自独立
- 找"嵌套子包 header 生成"代码（对应 Rule 1 的 `01 0A 00 23`）

### 第三步：验证新 pattern 在流量里

用 `/tmp/verify_new_rules.py` 模板（本次会话写的）：
```python
TARGET = bytes([0x01, 0x0a, 0x00, 0x23])  # 换成新 pattern
scan('/tmp/ace_cap_local/latest.pcap', '我们')
# 要求：命中数 > 30/session（低了说明 pattern 不常见，改它无效）
```

### 第四步：把新规则写入 relay.go

模板（参考 `patchWPEAdvanced`）：
```go
// 新 Rule N：[说明]
i := 0
for i <= len(data)-PATTERN_LEN {
    j := bytes.Index(data[i:], []byte{0xXX, 0xYY, ...})
    if j < 0 { break }
    pos := i + j
    if out == nil {
        out = make([]byte, len(data))
        copy(out, data)
    }
    // modify 对应字节
    out[pos+OFFSET] = NEW_VALUE
    changed = true
    i = pos + PATTERN_LEN
}
```

同步更新 [wpe_adv_test.go](wpe_adv_test.go) 加单元测试。

### 第五步：编译 + 部署

本地 mac 编译 linux 二进制：
```bash
cd /Users/Zhuanz/ProjectHub/game-automation/gameproxy-go
GOOS=linux GOARCH=amd64 go build -o /tmp/gameproxy_new .
```

上传 + 替换 + 重启：
```bash
sshpass -p '0qkp5IteqnhQugoe' scp /tmp/gameproxy_new root@171.80.4.221:/opt/gameproxy/gameproxy.new
sshpass -p '0qkp5IteqnhQugoe' ssh root@171.80.4.221 "
  systemctl stop gameproxy &&
  cp /opt/gameproxy/gameproxy.new /opt/gameproxy/gameproxy &&
  systemctl start gameproxy &&
  systemctl is-active gameproxy
"
```

### 第六步：实测验证

启动游戏进大厅 + 进局，监控 `[WPE-ADV]` 命中：
```bash
sshpass -p '0qkp5IteqnhQugoe' ssh root@171.80.4.221 "
  tail -F /opt/gameproxy/proxy.log | gawk '
    /WPE-ADV/ { c++; if (c % 30 == 1) print strftime(\"%H:%M:%S\"), \"x\" c }
    /ALERT|服务停止|panic/ { print \"!!! \", \$0 }
  '
"
```

目标：持续 ≥20 分钟无封号 = 规则有效。

---

## 端口 & 方向覆盖清单

这是本次会话发现的关键盲区。任何新规则**必须全覆盖**：

| 方向 | 应用端口 | 在代码哪里 |
|---|---|---|
| C→S | 443, 9900, 10012, 17500 | [relay.go:86-95](relay.go#L86-L95) |
| S→C | 443, 17500 | [relay.go:117-126](relay.go#L117-L126) |

**不要遗漏 S→C 方向**——Rule 2 的 97% 命中在 S→C 17500，漏了就等于只做一半。

---

## 相关文件 & 数据位置

| 资源 | 路径 |
|---|---|
| 核心实现 | [relay.go](relay.go) `patchWPEAdvanced` |
| 单测 | [wpe_adv_test.go](wpe_adv_test.go) |
| 花瓶原始规则 | `~/Downloads/1.fp` |
| WPE fp 参考库 | `~/Downloads/225个游戏封包-和平王者三角洲暗区游戏.rar` |
| gitee 路由规则 | `https://gitee.com/ez9673/udp/raw/master/zaix` |
| 我们的 pcap | `/tmp/ace_cap_local/*.pcap` |
| 六花/花瓶 pcap | `/tmp/liuhua_pcap/*.pcap` |
| 分析脚本 | `/tmp/verify_new_rules.py`, `/tmp/find_systematic_diff.py`, `/tmp/deep_analyze_2rules.py` |
| 服务器部署 | `root@171.80.4.221:/opt/gameproxy/` SSH pass: `0qkp5IteqnhQugoe` |

---

## 开场活动弹窗精准拦截（2026-04-22 新增）

### 背景

Rule 2 能**概率性**破坏活动数据（只在 0A 92 pattern 命中时），效果是"有时弹有时不弹"。为了**稳定拦截**开场自动弹窗，做了两次会话的对照抓包分析。

### 分析方法

**同一用户连续做两次对照实验**：

| 实验 | 配置 | 目的 |
|---|---|---|
| 实验 1 | Rule 2 打开 | 观察正常防封下活动被概率破坏的情况（conn_000011） |
| 实验 2 | `-rule2-disabled=true`（临时关闭 Rule 2） | 让活动数据"完整无损"通过，捕获原始帧（conn_000001） |

**关键流程**：
- 用户在大厅里手动点：个人主页 / 好友 / 活动页 / 商城，每个窗口记时间戳
- Python 解析 capture bin 文件按时间窗分段
- 对每个 type=0x1340 帧提取 `(size, byte[25])` 作为指纹
- 找**只出现在活动窗口**的指纹

### 核心发现

#### 1. 会话内有重复帧（确认 ECB 加密）

同一会话内多次出现完全相同的 `(size, byte[25])` 组合，例如 `(10377, 0x70)` 出现 2 次（T+1.34s 和 T+7.15s）。**相同明文 → 相同密文** = 这类信号在单会话内是稳定指纹。

#### 2. 跨会话 byte[25] 不稳定，但 **size 稳定**

| 字段 | 跨会话稳定性 |
|---|---|
| `byte[25]`（加密 payload 首字节） | ❌ 每次会话密钥不同，值都变 |
| `size`（declared_payload + 25） | ✅ 由明文结构决定，跨会话几乎不变 |

例：
- conn_000011 活动 banner 帧 size=15609
- conn_000001 活动 banner 帧 size=15689
- 差 80 字节（活动内容小变动），但范围稳定

#### 3. 活动 banner 帧的 size 范围独有

所有分析过的登录/大厅大帧 size：`10377 / 4057 / 3977 / 4441 / 7033 / 20681 / 4729 / 4665 / 4937 / 4473 / 10089 / 31833 / 49401 / 54217 / 31529 / 54009` 等。

**`[15000, 16500]` 范围内没有任何登录帧**——这是活动 banner 独占的 size 区间。

### 最终规则

```
DROP 条件 (S→C 17500):
  type == 0x1340
  AND 15000 <= declared_payload + 25 <= 16500
```

代码：[relay.go:158 S→C 循环里的 ACT-DROP-BANNER](relay.go#L158)

### 副作用评估

| 项 | 影响 |
|---|---|
| 登录大帧（全部 < 15000 或 > 20000） | ✅ 完全保留 |
| 好友/主页/商城通用 UI 帧（11049, 54009） | ✅ 完全保留 |
| Rule 2 对其他帧的概率破坏 | ✅ 继续生效（防封不变） |
| 所有心跳、小帧 | ✅ 完全保留 |

### 为什么 Rule 2 顺带会让商城/广告不显示（机制补充）

Rule 2 改字节的本质：**概率性破坏 + 客户端重试策略的不对等**。

| 数据类型 | 客户端重试策略 | Rule 2 命中后果 |
|---|---|---|
| 登录 / 匹配响应 | 解析失败 → **重试**（新加密包随机盐不同，大概率不含 `0A 92`） | ✅ 最终成功 |
| 心跳 | 小包，几乎不命中 | ✅ 无影响 |
| 活动 banner / 广告 | **可选内容**，解析失败 → **不重试** | ❌ 永久失败显示 |
| 商城 | **可选内容** → 不重试 | ❌ 永久失败显示 |
| 好友列表 | 取决于客户端代码（通常重试 1-2 次） | 🟡 不稳定 |

**核心**：ECB/CBC 块加密的"1 字节错 → 整个 16 字节块乱码"放大效应，让解析必定失败。客户端是否能"救回"取决于是否重试。

### 复现步骤（未来游戏更新后失效时）

如果某天 size 范围 [15000, 16500] 不再是活动 banner：

1. **对照实验**：在 proxy 上临时加 `-rule2-disabled=true`，让活动数据干净通过
2. **时间窗抓包**：用户在大厅连续点：主页/好友/活动页/商城，每步记时间戳
3. **拉下 17500 连接的 capture bin**
4. **用脚本解析**（参考 [/tmp/act_clean/analyze.py](/tmp/act_clean/analyze.py)）：
   - 按时间窗分帧
   - 提取每帧 `(size, byte[25])`
   - 找只在"活动"窗口出现的 size
5. **更新 relay.go 的 actMinSize/actMaxSize 常量**
6. **恢复 Rule 2** 编译部署

**重要**：对照实验**必须短（< 5 分钟）**，Rule 2 关闭时完全无防封。

---

## 一句话总结

**改 ACE 包不要走"破坏派"，走"伪装派"**：找嵌套子包头里的 type + flag 字段，改成服务端会认可的"清白值"；双向都要做（C→S 污染上报 + S→C 冻结下发），不要只看外层 pos。

**拦活动弹窗靠 size 指纹**：跨会话帧 size 是稳定的明文长度特征，byte[25] 加密密钥不同不稳定；活动 banner 帧独占 size 范围 [15000, 16500]，精准 drop 即可不影响任何其他功能。

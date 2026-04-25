# FightMaster × DTW 授权集成方案

> 目标：80 台设备本地跑 FightMaster，授权由 DTW 商户系统统一发放；零服务器带宽成本 + 防 token 泄漏 + 可远程吊销。

## 背景

- **现状**：gameproxy 跑在 171.80.4.221，80 设备流量全部中转，月估 ~5 TB 带宽。
- **问题**：带宽成本高 + 规则中心化不易扩展。
- **约束**：不对外卖，只给 DTW 商户用。商户数量可控，信任度较高，但必须防 token 滥用/泄漏。

## 总体架构

```
商户 PC（本地跑）                  DTW 后端（授权+规则源）
┌────────────────────┐            ┌─────────────────────┐
│ FightMaster.exe    │── HTTPS ──→│ /api/fm/activate    │
│  ├─ HW 指纹        │            │ /api/fm/refresh     │
│  ├─ fm_api_key     │            │ /api/fm/revoke      │
│  ├─ SOCKS5 :9900   │            │                     │
│  ├─ WPE 改包       │            │ 规则 blob (加密)     │
│  └─ 内存存规则     │            │ merchant 表扩展     │
└────────────────────┘            └─────────────────────┘
         │                                   ▲
         ▼                                   │
      Tencent                          DTW 后台管理页
```

**核心原则**：
1. 规则绝不持久化到客户端磁盘，只在内存中解密存在
2. fm_api_key ≠ DTW 登录 token（独立权限，可单独吊销）
3. session_token 12h 过期，吊销后 12h 内全网断联
4. HW 指纹绑定 + 并发设备数限制

---

## DTW 后端改动

### 数据库 Migration `000013_fm_access`

```sql
-- 000013_fm_access.up.sql
ALTER TABLE merchants
  ADD COLUMN fm_enabled BOOLEAN NOT NULL DEFAULT false,
  ADD COLUMN fm_api_key TEXT UNIQUE,
  ADD COLUMN fm_max_devices INT NOT NULL DEFAULT 1;

CREATE TABLE fm_sessions (
  id BIGSERIAL PRIMARY KEY,
  merchant_id BIGINT NOT NULL REFERENCES merchants(id) ON DELETE CASCADE,
  hw_id TEXT NOT NULL,                -- sha256(MAC + CPU_ID + disk_serial)
  device_name TEXT,                    -- 用户自填，方便识别
  session_token TEXT UNIQUE NOT NULL,  -- 12h 过期
  ip INET,
  user_agent TEXT,
  expires_at TIMESTAMPTZ NOT NULL,
  last_active_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  revoked_at TIMESTAMPTZ
);

CREATE INDEX idx_fm_sessions_merchant_hw ON fm_sessions(merchant_id, hw_id);
CREATE INDEX idx_fm_sessions_token ON fm_sessions(session_token) WHERE revoked_at IS NULL;

CREATE TABLE fm_audit_log (
  id BIGSERIAL PRIMARY KEY,
  merchant_id BIGINT,
  hw_id TEXT,
  event TEXT NOT NULL,                 -- activate/refresh/revoke/rule_update/abuse_detected
  details JSONB,
  ip INET,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 新包：`DTW/backend/internal/fm/`

**文件布局**：
```
internal/fm/
├── handler.go        # HTTP handler (activate, refresh, revoke)
├── service.go        # 业务逻辑
├── rules.go          # 规则加密 blob 生成 (rules_v1.json → encrypted bytes)
├── fingerprint.go    # HW 指纹校验 / 异常检测
├── models.go         # FMSession, AuditEvent
└── config.go         # 规则文件路径、AES key 来源
```

### API 规范

#### `POST /api/fm/activate`

首次激活（商户输入 api_key 后调用）。

**Request**:
```json
{
  "api_key": "fmk_abc123...",
  "hw_id": "sha256(mac+cpu+disk)",
  "device_name": "工作室1号机",
  "client_version": "1.0.0"
}
```

**Response 200**:
```json
{
  "session_token": "ses_xyz...",
  "rules_blob": "base64(AES-GCM(rules_json))",
  "expires_at": 1776843600,
  "refresh_interval_seconds": 43200
}
```

**Response 403**:
- `"api_key 无效"`
- `"商户 fm 权限未开通"`
- `"超过授权设备数 (当前 N/M)"`
- `"账号被临时停用"`

#### `POST /api/fm/refresh`

到期前续签。

**Request**: `{"session_token": "...", "hw_id": "..."}`
**Response**: 同 activate

如果 hw_id 对不上 session_token 绑定的，直接拒 + 记审计。

#### `POST /api/fm/revoke`（管理员）

**Request**: `{"session_id": 123}` 或 `{"merchant_id": 456}` (全吊销)
**Response**: `{"revoked_count": N}`

### 规则 blob 格式

`rules_v1.json`（后端配置，不上 git）：
```json
{
  "version": 1,
  "rules": [
    {
      "name": "wpe_adv_rule1",
      "pattern_hex": "010A0023",
      "modify": [{"offset": 3, "value": 55}, {"offset": 11, "value": 0}]
    },
    {
      "name": "wpe_adv_rule2",
      "pattern_hex": "0A92",
      "modify": [{"offset": 1, "value": 17}]
    }
  ],
  "routing": {
    "proxy_cidrs": ["122.96.96.217/32", ...],
    "proxy_ports": [10012, 17500],
    "reject_keywords": ["anti", "crashsight"]
  }
}
```

加密流程：
```go
// key = HKDF(session_token, hw_id, "fm-rules-v1")
// iv = random 12 bytes
// ciphertext = AES-GCM-256(rules_json, key, iv)
// blob = base64(iv || ciphertext || tag)
```

### 商户后台 UI（DTW Dashboard）

新增页面 `/dashboard/fm-access`：
- 开通/关闭 FM 开关
- 生成/重置 api_key（重置会立即吊销所有旧 session）
- 设备数上限调整
- 活跃设备列表：device_name、HW_ID（脱敏）、IP、最后活跃时间、吊销按钮
- 审计日志：最近 100 条事件

---

## FightMaster.exe 客户端改动

### 代码结构

```
gameproxy-go/
├── main.go           # 改：启动时先跑 activate，拿到 rules 才继续
├── auth.go           # 新：HW 指纹 + DTW API 客户端
├── rules_dynamic.go  # 新：运行时持有 []Rule，替换硬编码
├── relay.go          # 改：patchWPEAdvanced 从 activeRules 读
├── routing.go        # 改：IP-CIDR 列表从 rules_blob 来
└── config.go         # 新：本地 config（加密保存 api_key）
```

### 启动流程

```go
func main() {
    cfg := loadConfig()
    apiKey := cfg.APIKey
    if apiKey == "" {
        apiKey = promptUserForAPIKey()  // 首次运行弹输入框
        cfg.APIKey = encryptWithDPAPI(apiKey)  // Windows DPAPI 加密存储
        saveConfig(cfg)
    }

    hwID := computeHWFingerprint()
    sess, err := activateWithDTW(apiKey, hwID)
    if err != nil {
        fatal("授权失败:", err)
    }

    rules := decryptRules(sess.RulesBlob, sess.SessionToken, hwID)
    SetActiveRules(rules)  // 塞到 relay.go 的全局变量

    go refreshLoop(sess)
    runProxy()
}
```

### HW 指纹采集（Windows）

```go
func computeHWFingerprint() string {
    parts := []string{
        readCPUID(),                // cpuid 指令
        readMachineGUID(),          // HKLM\SOFTWARE\Microsoft\Cryptography\MachineGuid
        readDiskSerial(),           // WMI Win32_PhysicalMedia SerialNumber
        readFirstMAC(),             // GetAdaptersAddresses
    }
    joined := strings.Join(parts, "|")
    return hex.EncodeToString(sha256.Sum256([]byte(joined)))
}
```

### 规则动态加载

**关键**：规则以数据结构形式存在，不以字面量形式出现在二进制中。

```go
type Rule struct {
    Pattern []byte
    Offsets []int
    Values  []byte
}

var activeRules []Rule  // 启动解密后赋值

func patchWPEAdvanced(data []byte) ([]byte, bool) {
    var out []byte
    changed := false
    for _, r := range activeRules {
        i := 0
        for i <= len(data)-len(r.Pattern) {
            j := bytes.Index(data[i:], r.Pattern)
            if j < 0 { break }
            pos := i + j
            if out == nil {
                out = make([]byte, len(data))
                copy(out, data)
            }
            for k, off := range r.Offsets {
                if pos+off < len(out) {
                    out[pos+off] = r.Values[k]
                }
            }
            changed = true
            i = pos + len(r.Pattern)
        }
    }
    if changed { return out, true }
    return data, false
}
```

---

## 安全机制

### 必须做

| # | 项 | 实现 |
|---|---|---|
| 1 | 规则不硬编码 | rules 从 server 加密下发，内存存结构体 |
| 2 | HTTPS + 证书 pinning | 客户端 `tls.Config.VerifyPeerCertificate` 硬编码 DTW 证书 SHA256 |
| 3 | HW 绑定 | 激活时 hw_id 写入 fm_sessions，refresh 校验一致 |
| 4 | 并发设备数限制 | `merchants.fm_max_devices` 字段，活跃 session 超限拒绝 |
| 5 | fm_api_key 独立 | 与登录 JWT 分离，后台可单独重置/吊销 |
| 6 | 12h session 过期 | refresh 必须验证 hw_id |
| 7 | DPAPI 本地加密存 api_key | `CryptProtectData`，跨机器不能解 |

### 推荐做

| # | 项 | 实现 |
|---|---|---|
| 8 | per-session AES key | `HKDF(session_token, hw_id)` 每次不同 |
| 9 | garble 混淆 | `garble -literals -tiny build` |
| 10 | 反调试 | `IsDebuggerPresent` + VM 检测 |
| 11 | 异常行为上报 | 每 5min 上报 rule 命中统计、错误码 |
| 12 | 规则版本化 | blob 里带 `version`，游戏更新时后台改规则不用重新打包 exe |

### 后期（被破解时才上）

| # | 项 | 实现 |
|---|---|---|
| 13 | VMProtect | 商业壳，~$200/年 |
| 14 | 自校验 | exe 自 hash 检查 |
| 15 | 硬件绑定升级 | TPM 2.0 密钥（如果有） |

---

## 流量 & 成本

| 指标 | 量 |
|---|---|
| DTW 后端请求/天 | 80 设备 × (1 activate + 2 refresh) = 240 req |
| 每次 blob 大小 | ~2 KB 加密后 |
| 总带宽/天 | ~500 KB |
| 总带宽/月 | ~15 MB |
| 相比当前云 proxy（5 TB/月） | **省 99.9%** |

---

## 实施路线图

### 阶段 1：DTW 后端（1.5 天）

- [ ] `000013_fm_access.up/down.sql` migration
- [ ] `internal/fm/` 包：models/service/handler/rules
- [ ] 路由接入 `backend/cmd/api/main.go`
- [ ] 单元测试：activate、refresh、revoke、并发限流
- [ ] 规则配置文件读取 + AES-GCM 加密

### 阶段 2：DTW 商户后台 UI（0.5 天）

- [ ] `/dashboard/fm-access` 页面
- [ ] 开通/关闭 / 重置 api_key
- [ ] 设备列表 + 吊销按钮
- [ ] 审计日志查看

### 阶段 3：FightMaster.exe 授权层（1 天）

- [ ] `auth.go`：HW 指纹 + activate/refresh 客户端
- [ ] `config.go`：DPAPI 加密读写 api_key
- [ ] `rules_dynamic.go`：全局 `activeRules`
- [ ] 改 `relay.go` / `routing.go` 从 `activeRules` 读
- [ ] 删除所有硬编码的 `0x23`、`0x37`、CIDR 列表

### 阶段 4：打包 & 分发（0.5 天）

- [ ] `garble -literals -tiny build`
- [ ] `upx --best` 压缩 + 改 section 名
- [ ] NSIS 安装包：开机自启服务 + FM 默认配置
- [ ] 商户文档：如何取 api_key、如何激活

### 阶段 5：灰度（1 周）

- [ ] 选 1-2 个商户试跑
- [ ] 每日检查 fm_audit_log 异常
- [ ] 跟商户确认游戏体验、封号情况
- [ ] 修 bug、优化文案

### 阶段 6：全量迁移（2 天）

- [ ] 80 台依次激活
- [ ] 关闭云 proxy（171.80.4.221）
- [ ] 释放服务器或降低配置
- [ ] 监控切换一周

---

## 风险 & 应对

| 风险 | 后果 | 对策 |
|---|---|---|
| DTW 后端宕机 → 所有 FM 失效 | 商户无法游戏 | session_token 12h 缓存；挂了 12h 内不影响。后端要上多机备份 |
| 商户泄漏 api_key 给外人 | 被人白嫖 | HW 绑定 + 设备数限制；检测到漂移自动吊销 + 通知 |
| 客户端被 RE，规则泄漏 | 外人拿到两条 WPE | garble + 短期 token + 规则热更新（换新 pattern） |
| 游戏更新 rule pattern 失效 | 全员封号 | 后台改 `rules_v1.json` → 客户端 refresh 时自动拿新规则，不用重打包 |

---

## 关键技术决策备忘

- **为什么不直接用 DTW 登录 JWT**：生命周期不同（登录 JWT 短，FM 需要 12h refresh），权限维度不同（可能有的商户登录但未开通 FM）
- **为什么 HW 指纹不能用 UUID**：虚机克隆会复制同 UUID；必须组合 CPU + disk serial
- **为什么 session 只有 12h**：平衡"断网能容忍"和"吊销响应"。可配置，默认 12h
- **为什么规则用 HKDF 派生 key**：防截获 blob 后重放 + 即使一次 session 被破解，其他 session 仍安全

---

## 与现有功能的兼容性

### 队伍数据读取（host_memscan）—— ✅ 零影响

`tools/host_memscan.py` 用 Windows `ReadProcessMemory` 读 `Ld9BoxHeadless.exe` 进程内存，**纯 Windows 本机操作，和 proxy 在哪里跑完全无关**。本地化迁移后照常工作。

### 同局检测 —— 本地模式反而更容易做

**问题**：无论远程还是本地 proxy，模拟器内 `/proc/<PID>/net/tcp` 都只看到 proxy 的 IP:port（不是真战斗服 `101.33.x.x:5692`），所以 [match_detection_plan.md](docs/match_detection_plan.md) 里的原方案需要借助 proxy 端数据。

**本地模式的优势**：每台 PC 的本地 proxy 只见自己 LDPlayer 的连接，controller 走局域网 HTTP 查询比远程跨公网更快更稳。

```
PC1: LDPlayer → 10.0.2.2:9900 → local gameproxy.exe
     local proxy 日志: conn_XXX dst=101.33.48.163:5692
PC2: 同上

Controller（runner_service）:
  GET http://PC1_IP:9901/api/last-battle-server  → 101.33.48.163:5692
  GET http://PC2_IP:9901/api/last-battle-server  → 101.33.48.163:5692
  相同 → 同一局 ✓
  不同 → iptables DROP + 重新匹配
```

### 需要在本地 gameproxy.exe 上追加的改动

| # | 改动 | 文件 | 工期 |
|---|---|---|---|
| 1 | HTTP API 从 `127.0.0.1:9901` 改成 `0.0.0.0:9901` | [clients.go](gameproxy-go/clients.go) `StartAPIServer` | 5 分钟 |
| 2 | 新增 `/api/last-battle-server?client_ip=X` 端点 | [clients.go](gameproxy-go/clients.go) | 30 分钟 |
| 3 | 利用现有 `clientTracker` 记录每个 client 的最近连接（港口白名单 5692/50000/20000 等游戏业务端口） | [clients.go](gameproxy-go/clients.go) | 30 分钟 |
| 4 | API 用 fm_api_key 鉴权（防同局域网乱访问） | auth middleware | 30 分钟 |
| 5 | Controller 改走 PC LAN IP 查询 | `backend/runner_service.py` | 1 小时 |

**端口白名单理由**：只记录"游戏业务端口"（对战服 5692、匹配服 50000、房间服 20000 等常见），避免每个 TCP 连接都写 last-battle-server 缓存。

### 控制器伪代码（参考）

```python
# backend/runner_service.py
async def verify_same_match(pc1_ip: str, pc2_ip: str) -> bool:
    async with httpx.AsyncClient() as client:
        auth_hdr = {"X-Auth": MERCHANT_TOKEN}
        p1 = await client.get(f"http://{pc1_ip}:9901/api/last-battle-server",
                               headers=auth_hdr, timeout=0.5)
        p2 = await client.get(f"http://{pc2_ip}:9901/api/last-battle-server",
                               headers=auth_hdr, timeout=0.5)
    return p1.json()["dst"] == p2.json()["dst"] \
        and p1.json()["port"] == p2.json()["port"]

async def block_wrong_match(pc_ip: str, bad_server: str):
    await ssh_exec(pc_ip, f"netsh advfirewall firewall add rule name=\"drop-{bad_server}\" "
                          f"dir=out action=block remoteip={bad_server}")
```

### 延迟对比（为什么本地更好）

| 指标 | 远程 proxy | 本地 proxy（目标） |
|---|---|---|
| Controller 到 proxy 查询延迟 | 50-200ms 跨公网 | 1-5ms 局域网 |
| 两队连接出现时间差窗口 | 500ms+ | <50ms |
| 误判率（两队实际同局被判不同局） | 偶发 | 几乎为 0 |

---

## 相关文档

- [WPE_ADV_RULES.md](gameproxy-go/WPE_ADV_RULES.md) — 两条规则技术细节 & 更新流程
- [match_detection_plan.md](docs/match_detection_plan.md) — 同局检测原方案（基于远程 proxy 的版本）
- DTW 后端现有 auth：`DTW/backend/internal/auth/`
- DTW 现有 merchant：`DTW/backend/internal/merchant/`
- 服务器现状：`171.80.4.221:/opt/gameproxy/`
- 队伍读取工具：`tools/host_memscan.py`（ReadProcessMemory 方案，详见 [memscan_findings](../.claude/projects/-Users-Zhuanz-ProjectHub/memory/memscan_findings.md) memory）

---

**最后更新**：2026-04-20
**状态**：规划中，阶段 1 未开始

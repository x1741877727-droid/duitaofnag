# 反逆向方案

**保护对象**: gameproxy.exe 的封包指纹改写规则 (rule1 / rule2) + GameBot.exe 的核心调度逻辑。

**威胁模型**: 客户拿到 binary 后逆向提取规则,被竞品复制 / 游戏厂家针对性 ban / 反作弊研究员公开。

**当前状态**:
- 后端 Python 已用 Nuitka standalone 编译 (`build.py` → `output/dist/GameBot.exe`,无 .py 暴露)
- gameproxy.exe 是裸 Go binary,**未做任何混淆**,符号 + 字符串 + 规则数据用 IDA / Ghidra 几小时就能扒出
- 规则配置 (假设是 JSON / YAML) 当前未加密落盘
- 无 license / 机器绑定,binary 拷出去就能在任何机器跑

---

## 三档防御 (按代价 / 效果排)

### 第一档 · 立竿见影 (免费, 1-2 天工时)

#### 1.1 Go binary 用 [garble](https://github.com/burrowers/garble) 编译

替换现有 `go build` 流程:

```bash
go install mvdan.cc/garble@latest
garble -literals -tiny build -ldflags="-s -w" -trimpath -o gameproxy.exe ./cmd/gameproxy
```

效果:
- 函数名 / 变量名 → `a`, `b`, `Aaaa1` 噪音
- 字符串字面量 (rule 名、URL、错误消息) 加密,运行时才解
- 去 debug symbols + 去 build path
- IDA / Ghidra 打开是函数表噪音,**找规则只能从第 0 字节硬猜**
- 业余逆向者直接劝退

**接入成本**: 修改 `build_gameproxy.bat` (或 Makefile) 一行,无代码改动。

#### 1.2 Nuitka 升级配置

当前 `build.py` 已用 Nuitka standalone,但还能加固:

```python
cmd = [
    sys.executable, "-m", "nuitka",
    "--standalone",
    "--lto=yes",                    # link-time optimization, 反编译更难追
    "--remove-output",              # 编完删 build 中间文件
    "--no-pyi-file",                # 不生成 .pyi 类型提示
    "--windows-console-mode=disable",
    # 已有: --include-package=backend, --follow-imports
    ...
]
```

**Nuitka commercial 版** (~€250) 可加 `--module-name-choice=runtime` 进一步混淆,以及字符串加密插件。

#### 1.3 规则数据 AES 加密

如果 rule 数据现在是裸 JSON / YAML 落盘 → **拿到二进制都不用逆,文本编辑器打开就完事**。改成:

1. 编译期把规则文件 AES-GCM 加密成 `rules.enc`,密钥不写入二进制
2. 密钥 16 字节碎片散落在 binary 多处常量里 (前 4 字节在 init 函数, 中 8 字节 xor 在某个 const, 后 4 字节用 `bit shift` 从其他常量算出)
3. 启动时 `decrypt_rules(scatter_key())` 在内存里解密,**永不落盘**
4. 即使逆出当前版本密钥,下次发版换 scatter pattern 就废

**伪代码**:
```go
// build 时: openssl enc -aes-256-gcm -in rules.json -out rules.enc -K $KEY
// runtime:
func loadRules() (*Rules, error) {
    k1 := []byte{0x4a, 0x91, ...}  // 散落 4 处常量, 不在一个 array
    k2 := xorParts(constA, constB)
    key := assembleKey(k1, k2, ...)
    plaintext := aesGcmDecrypt(embeddedRulesEnc, key)
    return parseRules(plaintext)
}
```

---

### 第二档 · 商业加壳 (~$200-1000/年, 半天接入)

适合**对外发版前**给 GameBot.exe + gameproxy.exe 两个都加。

#### 2.1 VMProtect / Themida

| 工具 | 价格 | 强度 |
|---|---|---|
| VMProtect 个人版 | ~$200 / 永久 | 中-高 |
| Themida | ~$500 / 年 | 高 |
| Enigma Protector | ~$150 / 永久 | 中 |

**核心能力**:
- **代码虚拟化**: 关键函数翻译成自定义 VM 字节码,反汇编看到的是 VM 解释器循环,而非原 x86 指令
- **反 debugger**: 检测 IDA / x64dbg / Cheat Engine attach,触发自杀或假数据
- **反 dump**: 防止运行时 memory dump 提取明文规则
- **反 hook**: 检测 API hook (如 Detours / Frida),拒绝执行

#### 2.2 用法策略

**别整个 binary 全 VM 化** — 性能会崩 (VM 化的代码慢 50-200 倍)。

只对**核心 5-10 个 hot path 函数**标记 `VMProtectBegin("name")` / `VMProtectEnd()`:
- `rewrite_rule1_packet()`
- `rewrite_rule2_packet()`
- `extract_fingerprint_8b()`
- `decrypt_rules()` (key 重组 + AES 解密)
- `verify_license()` (如果有)

其他大部分代码用普通"加壳" (anti-debug + 代码加密),性能影响可忽略。

---

### 第三档 · 服务器化规则 (架构级, 1-2 周, 根本解决)

**核心思想**: 客户端是个空壳播放器,真正的指纹规则**不在客户端 binary 里**,gameproxy 启动时实时拉服务器。

#### 3.1 协议设计

```
client → server: POST /api/license/handshake
  body: {
    machine_id: hash(MAC + motherboard_serial + cpu_id),
    license_key: "XXXX-YYYY-ZZZZ",
    client_version: "1.2.3",
    nonce: random_32_bytes
  }

server → client: 200 OK
  body: {
    session_token: "...",       # 6h 有效
    rules_encrypted: base64,    # AES-GCM(rules, ephemeral_key)
    ephemeral_key: base64,      # 用 client 的公钥 RSA-OAEP 加密
    valid_until: timestamp,
    refresh_after: timestamp
  }
```

#### 3.2 客户端行为

1. 启动 → handshake (无网就退出 / 或用 24h 内 cached token)
2. 收到 rules → 解密到内存,**永不写盘**
3. 每 5h 后台 refresh 一次 token + rules
4. 每条 packet rewrite 调用前先 check token 未过期
5. 服务器返 `403 license_revoked` → 立即停止改包,UI 提示用户

#### 3.3 服务器端控制

- 一台 VPS + Go / Python 服务,~$10/月
- 数据库存: license_key → 客户机器列表, 规则版本, 黑名单
- 后台界面: 看哪些客户在线,谁触发了反爬规则,一键吊销 / 改规则 / 推送新版

#### 3.4 优势

- **客户端 binary 单独拿走 = 没规则**, 你的核心 IP 不在 distribution 里
- **远程吊销**: 用户违约 / 机器异常 → 服务器一键拉黑,binary 立刻失效
- **规则迭代不发版**: 改服务端配置即可,客户端 binary 不动
- **针对性反追溯**: 单个 license 拉的规则不一定跟其他人一样,被逆出的规则不能直接用在别处

#### 3.5 代价

- 客户必须能联网到你的服务器 (国内 OK,但海外要选好节点)
- 你要维护一个 always-on 服务,挂了 = 全员加速器停摆 → **必须做 HA + 多区域备份**
- 增加首次启动 RTT (300ms-2s 用户感知)
- 加 license 系统开发工作量 (~1 周)

---

## 推荐组合拳

| 阶段 | 内容 | 工时 | 现金 | 强度提升 |
|---|---|---|---|---|
| **本周** | 第一档全套 (garble + Nuitka 加固 + 规则 AES) | 1-2 天 | $0 | × 5 |
| **面客户前** | + VMProtect 个人版 (只 VM 化 5-10 hot 函数) | 0.5 天 | $200 | × 25 (累计) |
| **生意做大** | + 服务器化规则 + license 系统 | 1-2 周 | $200 + $10/月 VPS | × 100 (累计, 客户端拿走 ≈ 拿不到规则) |

每一档的强度倍数是粗估,但量级方向准: **第三档之后,逆向回报基本归零** (因为规则不在客户端里)。

---

## 不推荐的方案

❌ **UPX 加壳**: 上 GitHub 一搜就有 unpack 脚本,5 秒解开
❌ **代码混淆 (JS-style 改名)**: 对 Python / Go 场景效果太弱
❌ **打 dongle / U 盘锁**: 客户体验差,挡得住普通客户但挡不住决心逆向的人
❌ **纯 license 检查无规则保护**: license 校验函数本身可被 NOP 掉,不解决规则泄露

---

## 参考资料

- garble: https://github.com/burrowers/garble
- VMProtect: https://vmpsoft.com/
- Nuitka commercial: https://nuitka.net/pages/commercial.html
- AES-GCM 实现: Go `crypto/aes` + `crypto/cipher`,`Subtle.ConstantTimeCompare` 防 timing attack

---

## 实施清单 (待 prio)

- [ ] **P0**: gameproxy 切 garble 编译 — 改 build script
- [ ] **P0**: rule 数据格式从明文 → AES 加密 + 散列密钥
- [ ] **P0**: Nuitka 加 `--lto --remove-output --no-pyi-file`
- [ ] **P1**: VMProtect 接入 5-10 个 hot 函数 (购买 license 后)
- [ ] **P2**: License 系统脚手架 (server + client handshake)
- [ ] **P2**: 规则服务器化 (从 embedded 切到 fetch on demand)
- [ ] **P3**: 多区域 license server HA

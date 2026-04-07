# 远程调试指南

让 macOS（或任何远程客户端，包括 Claude）能实时查看和操作 Windows 上运行的后端。

## 工作原理

```
Windows 机器                              macOS / Claude
┌─────────────────┐                       ┌─────────────────┐
│  LDPlayer × 6   │                       │                 │
│       ↑         │                       │  curl 命令      │
│  Python 后端    │ ←─── HTTPS ───┐       │  Read 截图      │
│  :8900 (本地)   │              │       │  分析诊断       │
│       ↑         │              │       │                 │
│  Cloudflared    │  ←─公网URL─→ │← ─ ── │                 │
│  Tunnel         │              │       │                 │
└─────────────────┘              │       └─────────────────┘
                          https://xxx.trycloudflare.com
```

Cloudflared 是 Cloudflare 提供的免费隧道工具，**无需注册账号、零配置**，
启动后会给你一个临时的公网 HTTPS URL，可以从任何地方访问你的本地服务。

---

## Windows 端配置（一次性）

### 1. 下载 cloudflared

访问 https://github.com/cloudflare/cloudflared/releases/latest

下载 `cloudflared-windows-amd64.exe`，重命名为 `cloudflared.exe`，
放到项目根目录（`game-automation/`）或加入 PATH。

### 2. 安装 Python 依赖

```bash
cd game-automation
pip install -r backend/requirements.txt
```

### 3. 构建前端（首次）

```bash
cd web
npm install
npm run build
cd ..
```

---

## 使用流程

### 启动远程调试

双击 `start-with-tunnel.bat` 或执行：

```bash
start-with-tunnel.bat
```

会看到：

```
[1/2] 启动后端 (端口 8900)...
[2/2] 启动 Cloudflared 隧道...

INF +--------------------------------------------------------------+
INF |  Your quick Tunnel has been created! Visit it at (it may    |
INF |  take some time to be reachable):                            |
INF |  https://abc-def-ghi-jkl.trycloudflare.com                   |
INF +--------------------------------------------------------------+
```

复制这个 URL 发给我（Claude），或在 macOS 上直接用浏览器打开。

### 我能做的事

把 URL 发给我之后，我可以执行：

```bash
# 1. 健康检查
curl https://xxx.trycloudflare.com/api/diagnostic/health

# 2. 一键诊断快照（最重要）
# 返回: 所有实例状态 + 6 个截图(base64) + 最近日志 + 错误
curl https://xxx.trycloudflare.com/api/diagnostic/snapshot > snapshot.json

# 3. 看最近的错误
curl https://xxx.trycloudflare.com/api/diagnostic/errors

# 4. 看某个实例的日志
curl "https://xxx.trycloudflare.com/api/diagnostic/logs?instance_index=0&limit=50"

# 5. 立即归档所有实例截图
curl -X POST https://xxx.trycloudflare.com/api/diagnostic/archive-now?label=before_match

# 6. 列出归档的截图
curl https://xxx.trycloudflare.com/api/diagnostic/screenshots

# 7. 下载某个截图
curl https://xxx.trycloudflare.com/api/diagnostic/screenshot/20260407-153022_0_manual.jpg -o sc.jpg

# 8. 测试 LLM API（不需要启动协调器）
curl -X POST "https://xxx.trycloudflare.com/api/debug/test-llm?prompt_key=detect_popup"

# 9. 完整管道延迟测试
curl -X POST "https://xxx.trycloudflare.com/api/debug/test-pipeline?instance_index=0"

# 10. 单步控制
curl -X POST "https://xxx.trycloudflare.com/api/debug/step?instance_index=0&trigger=enter_lobby"

# 11. 启停控制
curl -X POST https://xxx.trycloudflare.com/api/start
curl -X POST https://xxx.trycloudflare.com/api/stop
curl -X POST https://xxx.trycloudflare.com/api/pause
curl -X POST https://xxx.trycloudflare.com/api/resume
```

---

## 推荐协作流程

### 场景：联调时遇到问题

1. **你**：在 Windows 上启动后端 + 隧道
2. **你**：把 URL 发给我
3. **我**：先 `curl /api/diagnostic/snapshot` 看全局
4. **我**：根据状态判断问题在哪
5. **我**：用 `archive-now` 让你保存当前截图
6. **我**：下载截图分析画面
7. **我**：用单步 API 一步步推进，找到卡点
8. **我**：建议修改方案，你修改后再次测试

### 日常调试

每次启动都会生成一个新的日志文件：
- `logs/run-YYYYMMDD-HHMMSS.jsonl` — 结构化日志
- `screenshots/` — 归档截图（自动保留最新 200 个）

你可以提交这些到 git，我可以离线分析历史数据。

---

## 安全说明

- Cloudflared quick tunnel 是临时 URL，每次启动 URL 不同
- URL 是公开的，但很难被猜到
- 如果担心安全，可以加 HTTP Basic Auth 或 IP 白名单
- 调试完成后关闭 cloudflared (Ctrl+C) 即可断开公网访问

---

## 替代方案

如果不能用 cloudflared，备选：

| 方案 | 优点 | 缺点 |
|------|------|------|
| **ngrok** | 类似，更知名 | 免费版有连接限制 |
| **frp 自建** | 自主可控 | 需要 VPS |
| **Tailscale** | 加密 P2P | 需要双方都装客户端 |
| **局域网直连** | 最快 | 需要在同一网络 |

如果 macOS 和 Windows 在同一局域网（家里），可以直接：
```bash
python backend/main.py --dev --host 0.0.0.0 --port 8900
# macOS 浏览器: http://Windows的IP:8900
```

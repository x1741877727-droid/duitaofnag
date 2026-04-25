# 远程调试指南

这份文档只描述 `game-automation` **当前真实可用** 的远程调试方式。

如果这里和旧文档、旧 prompt、旧批处理脚本里的说明冲突，优先核对：

- `agents/remote_agent.py`
- `backend/api.py`
- `backend/main.py`

## 先记住一句话

现在这个项目的远程调试，已经不是“只靠 `8900` 后端 + 旧 diagnostic 接口”了。

当前正确理解是：

- `8900`：主后端 / 控制台 / 产品 API
- `9100`：Remote Agent / 远程机器入口 / AI 调试入口

## 两条远程面

### 1. 后端服务 `8900`

用途：

- 浏览器控制台
- 查看实例状态
- 查看截图
- 启动/停止自动化
- 读取和修改设置、账号配置

启动方式：

```bash
python backend/main.py --dev --port 8900
```

当前真实公开接口来自 `backend/api.py`：

- `GET /api/emulators`
- `POST /api/start`
- `POST /api/start/{instance_index}`
- `POST /api/stop`
- `GET /api/status`
- `GET /api/settings`
- `PUT /api/settings`
- `GET /api/accounts`
- `PUT /api/accounts`
- `GET /api/screenshot/{instance_index}`
- `GET /api/health`
- `WS /ws`

### 2. Remote Agent `9100`

用途：

- 远程跑命令
- 读文件
- 下载文件
- 打开浏览器终端
- 远程看机器状态
- 让 AI 像在本机上一样排查问题

启动方式：

```bash
python agents/remote_agent.py
```

Windows 常用启动方式：

```bat
deploy\windows\start-remote-agent.bat
```

当前真实公开接口来自 `agents/remote_agent.py`：

- `GET /`
- `GET /health`
- `GET /ui`
- `POST /exec`
- `GET /history`
- `GET /read`
- `GET /download`
- `WS /ws/exec`

## 到底该用哪个

如果你要看“应用”，用 `8900`。

如果你要看“机器”，用 `9100`。

具体一点：

- 想看实例状态、截图、控制自动化，走 `8900`
- 想跑命令、读日志、翻文件、开 shell，走 `9100`

对于 AI 协作，`9100` 通常是第一入口，因为它能先确认机器到底发生了什么。

## 现在和旧调试方式的差异

旧调试思路通常是：

- 启动 `8900`
- 用 cloudflared 暴露 `8900`
- 再打这些旧接口：
  - `/api/diagnostic/*`
  - `/api/debug/*`
  - `/api/pause`
  - `/api/resume`

这套思路现在已经过时。

我已经核过当前代码，事实是：

- Remote Agent `9100` 才是主要远程运维入口
- `backend/diagnostic.py` 仍然存在，但它的历史 HTTP 路由没有挂到当前 `backend/api.py`
- 一些批处理脚本和旧文档还会打印这些旧接口示例，但不能再把它们当成当前事实

## Remote Agent 细节

### 鉴权

Remote Agent 使用持久 token，文件位置：

```text
agents/.remote_agent_token.txt
```

鉴权方式：

- HTTP 请求：`X-Auth` header
- Web UI / WebSocket：`?token=<TOKEN>`

这个 token 重启后默认不变，除非手动删掉 token 文件。

### 日志

Remote Agent 日志位置：

```text
agents/remote_agent.log
```

### cloudflared

Remote Agent 启动时会尝试自动执行：

```bash
cloudflared tunnel --url http://localhost:9100
```

然后它会：

- 从输出里解析 `trycloudflare.com` 公网地址
- 在 `GET /` 响应里返回 `cf_url`
- 打印带 token 的 Web UI 地址
- 把地址信息上报到 beacon 服务

如果没装 `cloudflared`，它依然可以在局域网里正常使用。

### Web 终端

Remote Agent 自带 Web 终端页面：

```text
http://<host>:9100/ui?token=<TOKEN>
```

如果走 cloudflare 临时隧道，则类似：

```text
https://<trycloudflare>.trycloudflare.com/ui?token=<TOKEN>
```

## 后端服务细节

### 普通开发模式

```bash
python backend/main.py --dev --port 8900
```

### mock 开发模式

```bash
python backend/main.py --dev --mock --port 8900
```

### 局域网模式

```bash
python backend/main.py --dev --host 0.0.0.0 --port 8900
```

### 前端联调模式

后端：

```bash
python backend/main.py --dev --mock --port 8900
```

前端：

```bash
cd web
npm run dev
```

当前 `web/vite.config.ts` 默认代理到 `http://127.0.0.1:8900`

## 典型调试流程

### 方案 A：先看机器

适合：

- Windows 上到底跑了什么不确定
- 需要读日志
- 需要看目录
- 需要执行命令
- 需要远程让 AI 直接排查

步骤：

1. 在 Windows 上启动 Remote Agent
2. 记录 token
3. 先测试健康检查：

```bash
curl -s http://WINDOWS_HOST:9100/health
```

4. 执行命令：

```bash
curl -s -X POST http://WINDOWS_HOST:9100/exec \
  -H "Content-Type: application/json" \
  -H "X-Auth: TOKEN" \
  -d '{"cmd":"dir"}'
```

5. 读取日志：

```bash
curl -sG http://WINDOWS_HOST:9100/read \
  -H "X-Auth: TOKEN" \
  --data-urlencode "path=agents/remote_agent.log"
```

6. 打开 Web 终端：

```text
http://WINDOWS_HOST:9100/ui?token=TOKEN
```

### 方案 B：直接看应用

适合：

- 想看实例状态
- 想看截图
- 想看模拟器检测结果
- 想测试启动/停止逻辑

步骤：

1. 启动后端：

```bash
python backend/main.py --dev --port 8900
```

2. 检查健康状态：

```bash
curl http://WINDOWS_HOST:8900/api/health
```

3. 查看模拟器：

```bash
curl http://WINDOWS_HOST:8900/api/emulators
```

4. 查看运行状态：

```bash
curl http://WINDOWS_HOST:8900/api/status
```

5. 拉一张截图：

```bash
curl "http://WINDOWS_HOST:8900/api/screenshot/0?w=320" -o sc.jpg
```

## Windows 侧脚本说明

### `deploy/windows/start-remote-agent.bat`

作用：

- 回到项目根目录
- 检查并安装 Remote Agent 依赖
- 启动 `agents/remote_agent.py`

### `deploy/windows/agent-start.vbs`

作用：

- 最小化启动 Remote Agent
- 启动前检查 `9100` 端口是否已经在监听

### `deploy/windows/agent-stop.vbs`

作用：

- 找出监听本地 `9100` 的进程
- 直接停止这个进程

### `deploy/windows/start-with-tunnel.bat`

作用：

- 启动 `8900` 后端
- 给 `8900` 打 cloudflared 隧道

但要特别注意：

- 这个脚本现在仍会打印旧的 `/api/diagnostic/*` 示例
- 这些示例不是当前接口事实
- 真正的当前接口还是以 `backend/api.py` 和本文件为准

## 安全提醒

Remote Agent 属于高权限运维入口。

拿到 token 的人可以：

- 执行任意命令
- 读取文件
- 下载文件
- 开远程终端

所以不要把它当成“一个普通 debug API”，而应该当成“远程管理员入口”。

## 当前最常见的误判

### 1. 以为旧 diagnostic 路由还在

下面这些路径现在不要默认存在：

- `/api/diagnostic/health`
- `/api/diagnostic/snapshot`
- `/api/diagnostic/errors`
- `/api/debug/test-llm`
- `/api/debug/test-pipeline`
- `/api/debug/step`
- `/api/pause`
- `/api/resume`

当前 `backend/api.py` 没有公开它们。

### 2. 看到 `backend/diagnostic.py` 就以为它已经对外开放

不是。这个模块现在更像“代码里还保留的诊断能力”，不是“当前公开 API 套件”。

### 3. 以为一定要 cloudflared

不是。

- `8900` 可以局域网直连
- `9100` 也可以局域网直连
- `cloudflared` 只是附加远程暴露方式，不是必需条件

## 快速命令

### 启动后端

```bash
python backend/main.py --dev --port 8900
```

### 启动 Remote Agent

```bash
python agents/remote_agent.py
```

### 看后端健康状态

```bash
curl http://127.0.0.1:8900/api/health
```

### 看 Remote Agent 健康状态

```bash
curl http://127.0.0.1:9100/health
```

### 看 Remote Agent 基本信息

```bash
curl http://127.0.0.1:9100/
```

### 通过 Remote Agent 执行命令

```bash
curl -s -X POST http://127.0.0.1:9100/exec \
  -H "Content-Type: application/json" \
  -H "X-Auth: TOKEN" \
  -d '{"cmd":"python --version"}'
```

## 给下一个 AI 的接手提醒

以后如果新的 AI 来调这个项目：

1. 先判断问题是“应用问题”还是“机器问题”
2. 应用问题先看 `8900`
3. 机器问题先看 `9100`
4. 旧 markdown、旧脚本、旧会话记忆如果和代码冲突，优先信代码

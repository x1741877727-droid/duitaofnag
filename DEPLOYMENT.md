# 部署与代码同步指南

如何把项目从 macOS 传到 Windows，以及如何在两边同步代码。

## 总览

```
macOS (开发)                  GitHub                    Windows (运行)
┌────────────────┐    push    ┌──────────┐    pull    ┌────────────────┐
│  Claude 改代码  │ ─────────→ │  代码仓   │ ────────→ │ update.bat     │
│       ↑        │            └──────────┘            │  自动重启服务  │
│  我的指挥      │                                    │       ↓        │
└────────────────┘            ←─── HTTPS ───          │ 后端 + LDPlayer│
                       cloudflared tunnel              └────────────────┘
```

---

## 一、首次部署

### 步骤 1：在 macOS 上把代码 push 到 GitHub

```bash
cd /Users/Zhuanz/Vexa/game-automation

# 初始化 git
git init
git add .
git commit -m "initial: 游戏自动化框架"

# 在 GitHub 创建一个新仓库（私有），假设叫 game-automation
git remote add origin https://github.com/你的用户名/game-automation.git
git branch -M main
git push -u origin main
```

### 步骤 2：在 Windows 上克隆

打开 PowerShell 或 CMD：

```bash
# 进入你想放项目的目录，比如 D:\
cd D:\

# 克隆
git clone https://github.com/你的用户名/game-automation.git
cd game-automation
```

### 步骤 3：Windows 一键安装依赖

```bash
# 双击运行
first-setup.bat
```

这个脚本会自动：
- 检查 Python 和 Node.js
- 安装所有 Python 依赖（fastapi, opencv, paddleocr 等）
- 安装前端依赖并构建 dist

如果 pip 慢，用国内镜像：
```bash
pip install -r backend\requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 步骤 4：配置文件

编辑 `settings.json`：
```json
{
  "ldplayer_path": "C:\\leidian\\LDPlayer9",   ← 改成你的雷电路径
  "llm_api_url": "http://你的Gemini逆向API地址",
  "llm_api_key": "你的key (如果有)",
  "game_package": "私服游戏包名",
  "dev_mock": false
}
```

编辑 `accounts.json` 配置 6 个账号（QQ 号、游戏 ID、分组、角色）。

### 步骤 5：下载 cloudflared（远程调试用）

1. 访问 https://github.com/cloudflare/cloudflared/releases/latest
2. 下载 `cloudflared-windows-amd64.exe`
3. 重命名为 `cloudflared.exe`
4. 放到 `game-automation/` 目录

### 步骤 6：启动

```bash
# 远程调试模式（让我能看到）
start-with-tunnel.bat

# 或本地模式（自己用）
python backend\main.py --dev --port 8900
```

---

## 二、日常更新流程

### macOS 端（我或你）

```bash
cd /Users/Zhuanz/Vexa/game-automation

# 改代码后
git add .
git commit -m "fix: xxx"
git push
```

### Windows 端（你）

```bash
# 双击运行
update.bat
```

`update.bat` 会自动：
1. 停止正在运行的后端和 cloudflared
2. `git pull` 拉取最新代码
3. 如果 `requirements.txt` 有变化，自动 `pip install`
4. 如果 `package.json` 有变化，自动 `npm install`
5. 重新构建前端
6. 询问启动模式（远程调试/本地/不启动）

整个过程通常 10-30 秒。

---

## 三、需要传输的文件清单

`.gitignore` 已经排除了不该传的，**必须提交的核心文件**：

```
game-automation/
├── backend/                    ★ 所有 Python 代码
│   ├── *.py
│   ├── adb/, recognition/, handlers/, tools/
│   └── requirements.txt
├── web/                        ★ 前端源码
│   ├── src/
│   ├── package.json
│   ├── tsconfig.json
│   ├── vite.config.ts
│   └── index.html
├── settings.json               ★ 配置模板
├── accounts.json               ★ 账号模板
├── README.md
├── REMOTE_DEBUG.md
├── DEPLOYMENT.md
├── .gitignore
├── first-setup.bat             ★ Windows 首次部署
├── update.bat                  ★ Windows 一键更新
├── start-with-tunnel.bat       ★ Windows 启动 + 隧道
├── build.py                    ★ Nuitka 打包脚本
└── test_*.py                   ★ 测试脚本
```

**不需要传输的**（已在 .gitignore 中排除）：
- `__pycache__/` Python 缓存
- `web/node_modules/` npm 依赖（Windows 上 `npm install` 会重新生成）
- `web/dist/` 前端构建产物（Windows 上 `npm run build` 会重新生成）
- `logs/` 运行时日志
- `screenshots/` 归档截图
- `cloudflared.exe` Windows 单独下载

---

## 四、常见问题

### Q: 如果 settings.json / accounts.json 里有敏感信息怎么办？

两种方式：

**方式 A：从 git 中排除**

```bash
# 在 .gitignore 中取消注释:
settings.json
accounts.json
```

提交模板版本：
```bash
cp settings.json settings.example.json
git add settings.example.json
```

Windows 上首次启动时复制一份：
```bash
copy settings.example.json settings.json
```

**方式 B：私有仓库**

GitHub 私有仓库直接提交，只要你不分享给别人就行。

### Q: Git push 之前 macOS 上要不要构建前端？

**不需要**。前端构建产物 `web/dist/` 已被 .gitignore 排除，
Windows 上 `update.bat` / `first-setup.bat` 会自动运行 `npm run build`。

### Q: 我想直接用 SCP/SFTP 不用 git 行不行？

可以但不推荐。SCP 适合一次性传输，但每次改代码都要重传整个目录，
而且没有版本回滚能力。Git 增量同步只传变化的文件，秒级完成。

如果你坚持，macOS 命令：
```bash
# 传整个项目（排除生成文件）
rsync -av --exclude='node_modules' --exclude='__pycache__' \
      --exclude='web/dist' --exclude='logs' --exclude='screenshots' \
      ./game-automation/ Windows用户名@WindowsIP:/d/game-automation/
```

### Q: Windows 上能不能不用 Git？

也行，但很麻烦。最简单方案：
1. 在 macOS 上 `git push`
2. 浏览器打开 GitHub 仓库 → Code → Download ZIP
3. 解压覆盖到 Windows 目录

每次更新都要重做一遍，不如装个 git。

### Q: PaddleOCR 在 Windows 上装失败？

PaddleOCR 比较挑环境，如果失败：

```bash
# 用 CPU 版本
pip install paddlepaddle==2.5.2 -i https://pypi.tuna.tsinghua.edu.cn/simple
pip install paddleocr==2.7.0
```

或先跳过 OCR（mock 模式可用）：
```bash
# 编辑 backend/requirements.txt 注释掉:
# paddleocr>=2.7.0
# paddlepaddle>=2.5.0
```

### Q: 我能不能用 Tailscale / Frp 替代 cloudflared？

完全可以。任何能把 Windows 的 8900 端口暴露到公网的工具都行。
cloudflared 的优势是**无需注册、零配置**，启动就能用。

---

## 五、典型工作日

```
早上:
  Windows: 双击 update.bat → 选 [1] 远程调试模式
  Windows: 复制 cloudflared 输出的 URL，发给 Claude
  → 现在我可以远程操作你的环境了

调试中:
  我: curl /api/diagnostic/snapshot → 看现场
  我: 发现 popup_handler 有问题 → 改代码 → push
  你: 双击 update.bat → 选 [1]
  → 30 秒后新代码已运行

晚上:
  Windows: 关闭 cloudflared 窗口 (Ctrl+C)
  Windows: 关闭后端窗口
```

---

## 六、安全建议

如果你的 GitHub 仓库是公开的：
- **不要**把 `settings.json` 和 `accounts.json` 提交（在 .gitignore 中加上）
- **不要**把 LLM API key 写在代码里
- 用环境变量或单独的 `.env` 文件管理密钥

如果是私有仓库且只有你自己用，怎么方便怎么来。

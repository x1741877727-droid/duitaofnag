# 游戏自动化控制台

LDPlayer 多开自动化 + LLM 视觉识别 + 状态机驱动

## 快速开始

### 开发模式 (macOS/Windows)

```bash
# 1. 安装 Python 依赖
cd backend
pip install -r requirements.txt

# 2. 构建前端
cd ../web
npm install && npm run build

# 3. 启动 (mock 模式, macOS 开发用)
cd ..
python backend/main.py --dev --mock --port 8900

# 浏览器打开 http://127.0.0.1:8900
```

### 前端开发 (热更新)

```bash
# 终端 1: 后端
python backend/main.py --dev --mock --port 8900

# 终端 2: 前端 (自动代理到后端)
cd web && npm run dev
# 浏览器打开 http://127.0.0.1:5174
```

### Windows 桌面模式

```bash
pip install pywebview
python backend/main.py --mock  # pywebview 桌面窗口
```

### 打包为 EXE (Windows)

```bash
# Nuitka (推荐, 代码保护)
pip install nuitka
python build.py

# 或 PyInstaller (更快)
pip install pyinstaller
python build.py --pyinstaller

# 检查依赖
python build.py --check
```

## 项目结构

```
game-automation/
├── backend/
│   ├── main.py              # 程序入口
│   ├── api.py               # FastAPI REST + WebSocket
│   ├── coordinator.py       # 协调器 (两组同步)
│   ├── instance_agent.py    # 实例 Agent (状态机驱动)
│   ├── state_machine.py     # 18 种状态 FSM
│   ├── models.py            # 数据模型
│   ├── config.py            # 配置管理
│   ├── adb/                 # ADB 控制 + LDPlayer 管理
│   ├── recognition/         # OpenCV + OCR + LLM 三级识别
│   ├── handlers/            # 各状态处理器
│   └── tools/               # 模板采集工具
├── web/                     # React 前端
├── settings.json            # 全局设置
├── accounts.json            # 账号配置
└── build.py                 # EXE 打包脚本
```

## 配置

编辑 `settings.json`:
- `ldplayer_path`: 雷电模拟器安装路径
- `llm_api_url`: LLM API 地址
- `game_package`: 游戏包名
- `game_mode` / `game_map`: 预设模式和地图
- `dev_mock`: true 开启 mock 模式 (macOS 开发)

编辑 `accounts.json`:
- 6 个账号, 分 A/B 两组, 每组 1 captain + 2 member

@echo off
chcp 65001 >nul
set "ROOT=%~dp0..\.."
cd /d "%ROOT%"
REM ================================================================
REM 首次部署脚本 (Windows)
REM 一次性安装所有依赖并构建前端
REM ================================================================

echo.
echo ============================================================
echo  游戏自动化控制台 - 首次部署
echo ============================================================
echo.

REM === 1. 检查 Python ===
echo [1/5] 检查 Python...
python --version >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 Python
    echo 请先安装 Python 3.10+ : https://www.python.org/downloads/
    echo 安装时勾选 "Add Python to PATH"
    pause
    exit /b 1
)
python --version

REM === 2. 检查 Node.js ===
echo.
echo [2/5] 检查 Node.js...
node --version >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 Node.js
    echo 请先安装 Node.js 18+ : https://nodejs.org/
    pause
    exit /b 1
)
node --version

REM === 3. 安装 Python 依赖 ===
echo.
echo [3/5] 安装 Python 依赖 (可能需要几分钟)...
pip install --upgrade pip
pip install -r backend\requirements.txt
if errorlevel 1 (
    echo [错误] Python 依赖安装失败
    echo 尝试用国内镜像:
    echo   pip install -r backend\requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    pause
    exit /b 1
)

REM === 4. 安装前端依赖 ===
echo.
echo [4/5] 安装前端依赖...
cd web
call npm install
if errorlevel 1 (
    echo [错误] npm install 失败
    cd ..
    pause
    exit /b 1
)

REM === 5. 构建前端 ===
echo.
echo [5/5] 构建前端...
call npm run build
if errorlevel 1 (
    echo [错误] 前端构建失败
    cd ..
    pause
    exit /b 1
)
cd ..

echo.
echo ============================================================
echo  首次部署完成!
echo ============================================================
echo.
echo 下一步:
echo   1. 编辑 config\settings.json 配置雷电模拟器路径和 LLM API
echo   2. 编辑 config\accounts.json 配置 6 个账号信息
echo   3. 下载 cloudflared.exe 放到项目根目录 (远程调试用)
echo      https://github.com/cloudflare/cloudflared/releases/latest
echo   4. 运行 deploy\windows\start-with-tunnel.bat 启动
echo.
echo 或本地启动 (无远程调试):
echo   python backend\main.py --dev --port 8900
echo   浏览器打开 http://127.0.0.1:8900
echo.
pause

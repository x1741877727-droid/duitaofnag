@echo off
chcp 65001 >nul
set "ROOT=%~dp0..\.."
cd /d "%ROOT%"
set "PATH=%ROOT%;%PATH%"
REM ================================================================
REM 一键启动: 后端 + Cloudflared 隧道
REM 让 macOS / Claude 能远程访问 Windows 上的后端
REM ================================================================

echo.
echo ============================================================
echo  游戏自动化控制台 - 远程调试模式
echo ============================================================
echo.

REM 检查 cloudflared
where cloudflared >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 cloudflared.exe
    echo.
    echo 请先下载 cloudflared:
    echo   https://github.com/cloudflare/cloudflared/releases/latest
    echo.
    echo 下载 cloudflared-windows-amd64.exe，重命名为 cloudflared.exe
    echo 放到项目根目录或加入 PATH 环境变量
    echo.
    pause
    exit /b 1
)

REM 检查 Python 后端依赖
python -c "import fastapi, uvicorn, cv2" 2>nul
if errorlevel 1 (
    echo [错误] Python 依赖未安装
    echo 请运行: pip install -r backend\requirements.txt
    echo.
    pause
    exit /b 1
)

REM 1. 后台启动后端 (绑定 0.0.0.0 但 cloudflared 走 localhost)
echo [1/2] 启动后端 (端口 8900)...
start "GameAutomation Backend" /MIN cmd /c "python backend\main.py --dev --host 127.0.0.1 --port 8900"

REM 等待后端就绪
timeout /t 4 /nobreak >nul

REM 2. 启动 cloudflared 隧道
echo [2/2] 启动 Cloudflared 隧道...
echo.
echo ============================================================
echo  隧道启动后会显示一个 https://xxx.trycloudflare.com URL
echo  把这个 URL 发给 Claude/远程调试者即可
echo ============================================================
echo.
echo 测试命令 (复制 URL 后):
echo   curl https://xxx.trycloudflare.com/api/diagnostic/health
echo   curl https://xxx.trycloudflare.com/api/diagnostic/snapshot
echo.
echo 按 Ctrl+C 停止隧道 (后端继续运行)
echo.

cloudflared tunnel --url http://localhost:8900

echo.
echo 隧道已关闭。后端仍在运行，可在任务管理器结束 python 进程
pause

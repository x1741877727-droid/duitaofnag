@echo off
chcp 65001 >nul
set "ROOT=%~dp0..\.."
cd /d "%ROOT%"
REM ================================================================
REM 一键更新脚本 (Windows)
REM 拉取最新代码 → 更新依赖 → 重建前端 → 重启服务
REM ================================================================

echo.
echo ============================================================
echo  游戏自动化控制台 - 更新代码
echo ============================================================
echo.

REM === 1. 停止正在运行的后端 ===
echo [1/5] 停止正在运行的服务...
taskkill /F /FI "WINDOWTITLE eq GameAutomation Backend*" 2>nul
taskkill /F /FI "IMAGENAME eq cloudflared.exe" 2>nul
timeout /t 2 /nobreak >nul

REM === 2. Git pull ===
echo.
echo [2/5] 拉取最新代码...
git pull
if errorlevel 1 (
    echo [警告] git pull 失败，可能有本地修改冲突
    echo 请手动处理后重试
    pause
    exit /b 1
)

REM === 3. 检查 Python 依赖是否需要更新 ===
echo.
echo [3/5] 检查 Python 依赖...
git diff HEAD@{1} HEAD --name-only 2>nul | findstr "requirements.txt" >nul
if not errorlevel 1 (
    echo   requirements.txt 有变化，重新安装依赖...
    pip install -r backend\requirements.txt
) else (
    echo   Python 依赖无变化，跳过
)

REM === 4. 检查前端依赖和重建 ===
echo.
echo [4/5] 重建前端...
git diff HEAD@{1} HEAD --name-only 2>nul | findstr "package.json" >nul
if not errorlevel 1 (
    echo   package.json 有变化，重新安装 npm 依赖...
    cd web
    call npm install
    cd ..
)

cd web
call npm run build
if errorlevel 1 (
    echo [错误] 前端构建失败
    cd ..
    pause
    exit /b 1
)
cd ..

REM === 5. 重启服务 ===
echo.
echo [5/5] 启动服务...
echo.
echo ============================================================
echo  更新完成! 选择启动模式:
echo ============================================================
echo.
echo   [1] 远程调试模式 (启动 cloudflared 隧道)
echo   [2] 本地模式 (浏览器访问)
echo   [3] 不启动，手动运行
echo.
set /p choice="请选择 (1/2/3): "

if "%choice%"=="1" (
    call deploy\windows\start-with-tunnel.bat
) else if "%choice%"=="2" (
    start "GameAutomation Backend" cmd /c "python backend\main.py --dev --port 8900 & pause"
    timeout /t 3 /nobreak >nul
    start http://127.0.0.1:8900
) else (
    echo.
    echo 手动启动命令:
    echo   远程调试: deploy\windows\start-with-tunnel.bat
    echo   本地模式: python backend\main.py --dev --port 8900
    echo.
    pause
)

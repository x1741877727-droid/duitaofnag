@echo off
chcp 65001 >nul
title Remote Agent
echo.
echo ============================================================
echo  Remote Agent 启动器
echo ============================================================
echo.

REM 1. 检查 fastapi 是否已安装
python -c "import fastapi, uvicorn" 2>nul
if errorlevel 1 (
    echo [1/2] Installing FastAPI and uvicorn (one-time, 1-2 min)...
    pip install fastapi uvicorn
    if errorlevel 1 (
        echo.
        echo [ERROR] FastAPI install failed
        pause
        exit /b 1
    )
) else (
    echo [1/2] FastAPI already installed
)

REM 2. 启动 remote_agent.py
echo.
echo [2/2] 启动 Remote Agent...
echo.
python remote_agent.py
pause

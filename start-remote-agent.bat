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
    echo [1/2] 安装 FastAPI 和 uvicorn (一次性, 1-2 分钟)...
    pip install fastapi uvicorn -i https://pypi.tuna.tsinghua.edu.cn/simple
    if errorlevel 1 (
        echo.
        echo [错误] FastAPI 安装失败
        echo 尝试默认源:
        pip install fastapi uvicorn
        if errorlevel 1 (
            pause
            exit /b 1
        )
    )
) else (
    echo [1/2] FastAPI 已安装
)

REM 2. 启动 remote_agent.py
echo.
echo [2/2] 启动 Remote Agent...
echo.
python remote_agent.py
pause

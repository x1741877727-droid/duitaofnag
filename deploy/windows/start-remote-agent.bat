@echo off
chcp 65001 >nul
title Remote Agent v2
set "ROOT=%~dp0..\.."
cd /d "%ROOT%"
set "PATH=%ROOT%;%PATH%"
echo.
echo ============================================================
echo  Remote Agent v2
echo ============================================================
echo.

REM 检查并安装依赖
python -c "import fastapi, uvicorn, websockets" 2>nul
if errorlevel 1 (
    echo [1/2] 安装依赖 (首次运行，约 1-2 分钟)...
    pip install fastapi "uvicorn[standard]"
    if errorlevel 1 (
        echo.
        echo [ERROR] 安装失败，请手动运行: pip install fastapi "uvicorn[standard]"
        pause
        exit /b 1
    )
) else (
    echo [1/2] 依赖已安装
)

REM 启动
echo.
echo [2/2] 启动 Remote Agent v2...
echo.
pip install fastapi "uvicorn[standard]" -q
cmd /k python agents\remote_agent.py

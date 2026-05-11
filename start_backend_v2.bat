@echo off
REM v2 灰度 backend 启动脚本. unset GAMEBOT_RUNNER_VERSION 即回退 v1.
cd /d %~dp0
set GAMEBOT_RUNNER_VERSION=v2
set GAMEBOT_VISION_DAEMON=0
REM 关 OcrPool (ProcessPoolExecutor): 防 worker crash 时 Python 3.12 Windows
REM shutdown(wait=False) 实际阻塞 22s 拖死整个 asyncio event loop.
REM OCR 走主进程 asyncio.to_thread + sync RapidOCR, 慢 ~3x 但绝不卡死.
set GAMEBOT_OCR_POOL_DISABLE=1
echo [v2] starting backend (RUNNER_VERSION=v2, VISION_DAEMON=0, OCR_POOL=0)
echo cwd: %CD%
python -u backend\main.py --dev --host 0.0.0.0 --port 8900

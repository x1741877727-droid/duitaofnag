@echo off
REM v2 灰度 backend 启动脚本. unset GAMEBOT_RUNNER_VERSION 即回退 v1.
cd /d %~dp0
set GAMEBOT_RUNNER_VERSION=v2
set GAMEBOT_VISION_DAEMON=0
echo [v2] starting backend (RUNNER_VERSION=v2, VISION_DAEMON=0, OcrPool=on-with-async-recover)
echo cwd: %CD%
python -u backend\main.py --dev --host 0.0.0.0 --port 8900

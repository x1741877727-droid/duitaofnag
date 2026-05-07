@echo off
chcp 65001 >nul

REM ========================================================================
REM  Step 2 one-time setup. Double-click to run.
REM  Self-elevate + delegate everything to install_autostart.ps1.
REM ========================================================================

REM === Self-elevate ===
net session >nul 2>&1
if %errorLevel% NEQ 0 (
    echo Not admin. Re-launching elevated via UAC...
    powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

set "PS1=%~dp0install_autostart.ps1"
echo Running: %PS1%
powershell -ExecutionPolicy Bypass -NoProfile -File "%PS1%"
echo.
pause

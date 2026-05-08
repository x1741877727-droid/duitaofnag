@echo off
REM ============================================================
REM  build_apk.bat — 一键 build verify-overlay.apk
REM  前置: 已装 Android SDK (cmdline-tools / platform-tools / build-tools-34)
REM        ANDROID_HOME 环境变量指向 SDK 根
REM        JAVA_HOME 指向 JDK 17+
REM ============================================================

setlocal
cd /d "%~dp0"

if not defined ANDROID_HOME (
    echo [ERROR] ANDROID_HOME 没设. 装好 Android SDK 后:
    echo         setx ANDROID_HOME "C:\Users\%USERNAME%\AppData\Local\Android\Sdk"
    exit /b 1
)

REM 没 wrapper 就先生成
if not exist "gradlew.bat" (
    echo [build] 生成 gradle wrapper ...
    gradle wrapper --gradle-version 8.4 --distribution-type bin
    if errorlevel 1 (
        echo [ERROR] gradle wrapper 失败 — 装个 gradle ^>=8.4: https://gradle.org/install/
        exit /b 1
    )
)

echo [build] assembleRelease ...
call gradlew.bat :app:assembleRelease
if errorlevel 1 (
    echo [ERROR] build 失败.
    exit /b 1
)

set "APK_SRC=app\build\outputs\apk\release\app-release.apk"
set "APK_DST=..\..\fixtures\verify-overlay.apk"

if not exist "%APK_SRC%" (
    echo [ERROR] 找不到 %APK_SRC%
    exit /b 1
)

if not exist "..\..\fixtures" mkdir "..\..\fixtures"

copy /Y "%APK_SRC%" "%APK_DST%" >nul
if errorlevel 1 (
    echo [ERROR] 拷贝 apk 失败.
    exit /b 1
)

for %%I in ("%APK_DST%") do set "SIZE=%%~zI"
echo.
echo [DONE] APK -^> %APK_DST%  (%SIZE% bytes)
echo.
echo 安装到当前 LDPlayer:
echo     adb install -r "%APK_DST%"
echo.

endlocal

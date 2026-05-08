"""
浮窗 APK 自动部署器.

加速器 (gameproxy / TUN) 启动成功后, 把 verify-overlay.apk 推到所有在线 LDPlayer 实例 +
appops 授权 + am start-foreground-service, 让屏幕底部出现"小猫趴着 + fightmaster 已启动"浮窗.

单次同步流程, 失败 silent (不阻断 gameproxy 主流程). 每台模拟器:
  1. pm list packages | grep com.gamebot.overlay  -> 已装跳 install
  2. install -r <apk>
  3. appops set com.gamebot.overlay SYSTEM_ALERT_WINDOW allow   (LDPlayer root 直通授权)
  4. pm grant POST_NOTIFICATIONS                                  (Android 13+)
  5. am start-foreground-service -n com.gamebot.overlay/.OverlayService
"""

from __future__ import annotations

import logging
import os
import platform
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

PKG = "com.gamebot.overlay"
SERVICE = f"{PKG}/.OverlayService"

_SF = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0


# =====================
# 路径解析
# =====================

def find_apk_path(config=None) -> Optional[str]:
    """按优先级找 verify-overlay.apk."""
    candidates: list[str] = []

    # 1. settings.overlay_apk_path (未来字段, 现在没就跳过)
    if config is not None:
        try:
            p = getattr(config.settings, "overlay_apk_path", "").strip()
            if p:
                candidates.append(p)
        except Exception:
            pass

    # 2. 打包后 exe 同级 fixtures/
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).parent
        candidates.append(str(exe_dir / "fixtures" / "verify-overlay.apk"))
        candidates.append(str(exe_dir / "verify-overlay.apk"))

    # 3. 开发树 (backend/automation/.. -> repo root/fixtures)
    repo_root = Path(__file__).resolve().parent.parent.parent
    candidates.append(str(repo_root / "fixtures" / "verify-overlay.apk"))
    candidates.append(str(repo_root / "android" / "verify-overlay" / "app" / "build" /
                          "outputs" / "apk" / "release" / "app-release.apk"))

    # 4. Windows 部署常见路径兜底
    candidates += [
        r"D:\game-automation\fixtures\verify-overlay.apk",
        r"D:\game-automation\duitaofnag\fixtures\verify-overlay.apk",
    ]

    for c in candidates:
        if c and Path(c).is_file():
            return c
    return None


def find_adb_path(config=None) -> Optional[str]:
    """settings.adb_path -> ldplayer 自带 -> 系统 PATH 'adb'."""
    if config is not None:
        try:
            p = (getattr(config.settings, "adb_path", "") or "").strip()
            if p and Path(p).is_file():
                return p
        except Exception:
            pass
        try:
            ld = (getattr(config.settings, "ldplayer_path", "") or "").strip()
            if ld:
                cand = Path(ld) / "adb.exe"
                if cand.is_file():
                    return str(cand)
        except Exception:
            pass
    # 兜底: 系统 PATH
    return "adb"


# =====================
# adb 调用底层
# =====================

def _run(adb: str, *args: str, timeout: float = 10.0) -> tuple[int, str, str]:
    """跑 adb 命令, 返回 (rc, stdout, stderr). 异常 -> (-1, '', str(e))."""
    try:
        cp = subprocess.run(
            [adb, *args],
            capture_output=True,
            timeout=timeout,
            creationflags=_SF,
        )
        out = cp.stdout.decode("utf-8", errors="replace") if cp.stdout else ""
        err = cp.stderr.decode("utf-8", errors="replace") if cp.stderr else ""
        return cp.returncode, out, err
    except subprocess.TimeoutExpired:
        return -1, "", f"adb timeout {timeout}s"
    except FileNotFoundError:
        return -1, "", f"adb not found: {adb}"
    except Exception as e:
        return -1, "", f"adb exec error: {e}"


def list_online_serials(adb: str) -> list[str]:
    """adb devices -> [emulator-XXXX, ...] (只取 device 状态的)."""
    rc, out, err = _run(adb, "devices", timeout=5)
    if rc != 0:
        logger.warning(f"[overlay] adb devices 失败 rc={rc}: {err.strip()}")
        return []
    serials: list[str] = []
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith("List of"):
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            serials.append(parts[0])
    return serials


def is_installed(adb: str, serial: str) -> bool:
    rc, out, _ = _run(adb, "-s", serial, "shell", "pm", "list", "packages", PKG, timeout=8)
    return rc == 0 and PKG in out


def install_apk(adb: str, serial: str, apk: str) -> tuple[bool, str]:
    rc, out, err = _run(adb, "-s", serial, "install", "-r", "-g", apk, timeout=60)
    if rc == 0 and "Success" in out:
        return True, "install ok"
    # -g (auto-grant runtime permissions) 在老 Android 没有, 退回不带 -g
    if "-g" in (err + out) and "unknown" in (err + out).lower():
        rc2, out2, err2 = _run(adb, "-s", serial, "install", "-r", apk, timeout=60)
        if rc2 == 0 and "Success" in out2:
            return True, "install ok (no -g)"
        return False, f"install failed: {err2.strip() or out2.strip()}"
    return False, f"install failed: {err.strip() or out.strip()}"


def grant_overlay_permission(adb: str, serial: str) -> tuple[bool, str]:
    """LDPlayer root: 直接 appops set 跳过用户权限页."""
    rc, _, err = _run(adb, "-s", serial, "shell",
                       "appops", "set", PKG, "SYSTEM_ALERT_WINDOW", "allow",
                       timeout=8)
    if rc != 0:
        return False, f"appops set failed: {err.strip()}"
    return True, "ok"


def grant_post_notifications(adb: str, serial: str) -> None:
    """Android 13+ 需要; 老版本会 silent 失败 (rc != 0), 不影响主流程."""
    _run(adb, "-s", serial, "shell",
         "pm", "grant", PKG, "android.permission.POST_NOTIFICATIONS",
         timeout=8)


def start_overlay_service(adb: str, serial: str) -> tuple[bool, str]:
    """先 force-stop 再 start-foreground-service, 保证 service 是新进程."""
    _run(adb, "-s", serial, "shell", "am", "force-stop", PKG, timeout=8)
    # Android 8+ 用 start-foreground-service; 老版用 startservice
    rc, out, err = _run(adb, "-s", serial, "shell",
                         "am", "start-foreground-service", "-n", SERVICE,
                         timeout=8)
    if rc != 0 or ("Error" in out or "Error" in err):
        rc2, out2, err2 = _run(adb, "-s", serial, "shell",
                                 "am", "startservice", "-n", SERVICE,
                                 timeout=8)
        if rc2 != 0 or ("Error" in out2 or "Error" in err2):
            return False, f"am start failed: {(err2 or out2 or err or out).strip()}"
    return True, "service started"


# =====================
# 单台 + 全量部署
# =====================

def deploy_to(adb: str, serial: str, apk: str) -> tuple[bool, str]:
    """一台模拟器走完整套流程. 返回 (ok, summary)."""
    try:
        if not is_installed(adb, serial):
            ok, msg = install_apk(adb, serial, apk)
            if not ok:
                return False, f"[{serial}] {msg}"
            installed_now = True
        else:
            installed_now = False

        ok, msg = grant_overlay_permission(adb, serial)
        if not ok:
            return False, f"[{serial}] {msg}"

        grant_post_notifications(adb, serial)

        ok, msg = start_overlay_service(adb, serial)
        if not ok:
            return False, f"[{serial}] {msg}"

        tag = "新装" if installed_now else "已装"
        return True, f"[{serial}] OK ({tag} + 浮窗已启)"
    except Exception as e:
        return False, f"[{serial}] 异常: {e}"


def deploy_all(config=None) -> dict:
    """部署到所有在线 LDPlayer. 返回 {ok, total, success, failed, results: [str], reason}."""
    apk = find_apk_path(config)
    if not apk:
        return {"ok": False, "reason": "verify-overlay.apk 未找到 (放到 fixtures/ 或设 overlay_apk_path)",
                "total": 0, "success": 0, "failed": 0, "results": []}

    adb = find_adb_path(config)
    serials = list_online_serials(adb)
    if not serials:
        return {"ok": True, "reason": "无在线模拟器, 跳过", "total": 0,
                "success": 0, "failed": 0, "results": []}

    logger.info(f"[overlay] 部署到 {len(serials)} 台: {serials}")

    results: list[str] = []
    success = 0
    with ThreadPoolExecutor(max_workers=min(8, len(serials))) as ex:
        futs = {ex.submit(deploy_to, adb, s, apk): s for s in serials}
        for fut in as_completed(futs):
            ok, msg = fut.result()
            results.append(msg)
            if ok:
                success += 1
                logger.info(f"[overlay] {msg}")
            else:
                logger.warning(f"[overlay] {msg}")

    return {
        "ok": success > 0,
        "total": len(serials),
        "success": success,
        "failed": len(serials) - success,
        "results": results,
        "apk_path": apk,
    }


def stop_all(config=None) -> dict:
    """停所有模拟器的浮窗 service (gameproxy 关时调)."""
    adb = find_adb_path(config)
    serials = list_online_serials(adb)
    stopped = 0
    for s in serials:
        rc, _, _ = _run(adb, "-s", s, "shell", "am", "force-stop", PKG, timeout=5)
        if rc == 0:
            stopped += 1
    logger.info(f"[overlay] 关停浮窗: {stopped}/{len(serials)}")
    return {"ok": True, "total": len(serials), "stopped": stopped}

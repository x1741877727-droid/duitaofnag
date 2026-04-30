"""dxhook 自动预热 watchdog.

为什么需要这个:
  test_phase 第一次给某实例调 setup_minicap 时, 注 inject.exe + 验 SHM 要 4-7s.
  6 实例并发首测时, 这步互相争抢 (Windows 子进程创建 + Ld9BoxHeadless 注入路径
  有隐式排队), 实测一个实例 333ms 完成时, 另一个还要 5955ms — 18× 不一致.

  老想法 "backend 启动时一次预热" 行不通 — backend 起来时模拟器可能还没启动.

新方案 (这文件):
  后台跑 watchdog, 每 5s 扫一次 adb devices, 看到新 online 设备立即异步
  setup_minicap. 用户什么时候点测试, 实例都是热的.
  - 模拟器还没起? watchdog 等着, 0 浪费.
  - 模拟器中途重启? 设备消失再上来, watchdog 自动重新预热.
  - 模拟器一直在跑? 第一次扫到就预热, 之后不重复.

集成: backend/api.py startup hook 启动时 spawn 这个 task.
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from typing import Optional, Set

logger = logging.getLogger(__name__)

# 已预热的 serial 集合 — 跨 watchdog 周期记忆, 不重复预热
_WARM_SET: Set[str] = set()
# 已知设备的最近 snapshot, 检测掉线
_LAST_DEVICES: Set[str] = set()
# 任务句柄, backend shutdown 时 cancel
_WATCHDOG_TASK: Optional[asyncio.Task] = None


def _list_adb_devices(adb_path: str) -> list[str]:
    """同步跑 adb devices, 拿在线 (不算 offline / unauthorized) emulator-XXXX."""
    try:
        result = subprocess.run(
            [adb_path, "devices"],
            capture_output=True, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        out = result.stdout.decode("utf-8", errors="ignore")
    except Exception as e:
        logger.debug(f"[dxhook_warmup] adb devices 失败: {e}")
        return []
    devices = []
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith("List of"):
            continue
        parts = line.split("\t")
        if len(parts) >= 2 and parts[1].strip() == "device" and parts[0].startswith("emulator-"):
            devices.append(parts[0])
    return devices


async def _warm_one(adb_path: str, serial: str) -> bool:
    """异步给单个 serial 预热 dxhook. 失败 silent (下次 watchdog 重试)."""
    try:
        from .adb_lite import ADBController
        adb = ADBController(serial, adb_path)
        ok = await asyncio.to_thread(adb.setup_minicap)
        if ok:
            # 注: ADBController 实例不持久化到全局 — 真正业务用时
            # _build_test_runner 会再创一次 ADBController, setup_minicap 内部
            # 检 SHM 已建好, 直接复用 (dxhook DLL 在 emulator 进程里, 跨 ADBController 共享)
            logger.info(f"[dxhook_warmup] {serial} 预热完成 ✓")
            # 必要时 stop 释放本地 stream (dxhook DLL 留在 emulator 那边).
            # 这里其实不 stop, 让 stream 留着, 下次业务用 ADBController 直接 SHM 读.
            return True
        logger.debug(f"[dxhook_warmup] {serial} setup_minicap 返 False, 下轮重试")
        return False
    except Exception as e:
        logger.debug(f"[dxhook_warmup] {serial} 异常 (下轮重试): {e}")
        return False


async def _watchdog_loop(adb_path: str, scan_interval_s: float = 5.0):
    """后台主循环. 不断扫 adb devices, 看到新 online 实例立即异步预热."""
    global _LAST_DEVICES, _WARM_SET
    logger.info(f"[dxhook_warmup] watchdog 启动, 每 {scan_interval_s}s 扫一次 adb")
    while True:
        try:
            devices = await asyncio.to_thread(_list_adb_devices, adb_path)
            cur_set = set(devices)

            # 掉线的从 _WARM_SET 移除 (重新上来时再预热)
            offline = _LAST_DEVICES - cur_set
            for s in offline:
                if s in _WARM_SET:
                    _WARM_SET.discard(s)
                    logger.info(f"[dxhook_warmup] {s} 掉线, 标记待重新预热")

            # 新上来的并发预热
            new_devices = cur_set - _WARM_SET
            if new_devices:
                logger.info(f"[dxhook_warmup] 新发现 {len(new_devices)} 个实例 → 并发预热")
                tasks = [asyncio.create_task(_warm_one(adb_path, s)) for s in new_devices]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for s, r in zip(new_devices, results):
                    if r is True:
                        _WARM_SET.add(s)

            _LAST_DEVICES = cur_set
        except asyncio.CancelledError:
            logger.info("[dxhook_warmup] watchdog 退出")
            return
        except Exception as e:
            logger.warning(f"[dxhook_warmup] loop err: {e}")

        try:
            await asyncio.sleep(scan_interval_s)
        except asyncio.CancelledError:
            return


def start_watchdog(adb_path: str) -> None:
    """启动 watchdog (idempotent). 在 backend startup hook 里调."""
    global _WATCHDOG_TASK
    if _WATCHDOG_TASK is not None and not _WATCHDOG_TASK.done():
        return
    if not adb_path or not os.path.isfile(adb_path):
        logger.warning(f"[dxhook_warmup] adb 路径无效, 跳过预热: {adb_path}")
        return
    interval = float(os.environ.get("GAMEBOT_DXHOOK_WARMUP_INTERVAL_S", "5"))
    _WATCHDOG_TASK = asyncio.create_task(_watchdog_loop(adb_path, interval))


def stop_watchdog() -> None:
    """backend shutdown 时调."""
    global _WATCHDOG_TASK
    if _WATCHDOG_TASK is not None:
        _WATCHDOG_TASK.cancel()
        _WATCHDOG_TASK = None


def get_warm_status() -> dict:
    """状态查询 (debug 用)."""
    return {
        "warm_count": len(_WARM_SET),
        "warm_devices": sorted(_WARM_SET),
        "watchdog_running": _WATCHDOG_TASK is not None and not _WATCHDOG_TASK.done(),
    }

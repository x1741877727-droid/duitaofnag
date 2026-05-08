"""
FastAPI 路由 — V1
REST API: 启停控制、模拟器检测、配置 CRUD、截图、状态查询
WebSocket: 实时推送实例状态、日志
"""

import asyncio
import json
import logging
import os
import platform
import subprocess
import sys
import time

# Windows 下隐藏 cmd 窗口
_SF = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel

from .config import ConfigManager, AccountConfig
from .runner_service import MultiRunnerService

logger = logging.getLogger(__name__)

# 模块级 service / config 引用 (api_templates 等 router 用)
_active_service = None
_active_config = None

# /api/emulators 缓存：ldconsole list2 + adb devices 在 Windows 慢机最坏 15s，
# 旧实现直接同步 subprocess.run 阻塞 asyncio 主循环，是"全页面慢"根因之一。
# 5s TTL 缓存 + to_thread，前端多次轮询期间只刷一次盘。
_emulators_cache: "tuple[float, list, str] | None" = None  # (monotonic_ts, instances, ldplayer_path)
_EMULATORS_CACHE_TTL = 5.0


# =====================
# WebSocket 连接管理
# =====================

class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []
        self._queue: asyncio.Queue = asyncio.Queue()
        self._drain_task: Optional[asyncio.Task] = None

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, message: dict):
        dead = []
        for ws in self.active:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    def broadcast_sync(self, message: dict):
        self._queue.put_nowait(message)

    async def _drain_loop(self):
        while True:
            try:
                msg = await self._queue.get()
                await self.broadcast(msg)
            except asyncio.CancelledError:
                break
            except Exception:
                pass

    def start_drain(self):
        if self._drain_task is None:
            self._drain_task = asyncio.create_task(self._drain_loop())

    def stop_drain(self):
        if self._drain_task:
            self._drain_task.cancel()
            self._drain_task = None


ws_manager = ConnectionManager()


# =====================
# 雷电模拟器检测
# =====================

def detect_ldplayer_instances(ldplayer_path: str) -> list[dict]:
    """调用 ldconsole list2 检测所有模拟器实例"""
    ldconsole = os.path.join(ldplayer_path, "ldconsole.exe")
    if not os.path.exists(ldconsole):
        return []

    try:
        result = subprocess.run(
            [ldconsole, "list2"],
            capture_output=True, timeout=10,
            creationflags=_SF,
        )
        # ldconsole 在中文 Windows 上输出 GBK 编码
        for enc in ("utf-8", "gbk"):
            try:
                output = result.stdout.decode(enc).strip()
                break
            except UnicodeDecodeError:
                continue
        else:
            output = result.stdout.decode("utf-8", errors="replace").strip()
        if not output:
            return []

        # 获取 ADB 实际在线设备列表
        adb_path = os.path.join(ldplayer_path, "adb.exe")
        adb_online = set()
        try:
            adb_result = subprocess.run(
                [adb_path, "devices"], capture_output=True, timeout=5,
                creationflags=_SF,
            )
            for line in adb_result.stdout.decode("utf-8", errors="replace").splitlines():
                line = line.strip()
                if line.startswith("emulator-") and "device" in line:
                    serial = line.split()[0]
                    adb_online.add(serial)
        except Exception:
            pass

        instances = []
        for line in output.splitlines():
            parts = line.split(",")
            if len(parts) < 5:
                continue
            # list2 格式 (10字段): index,name,top_hwnd,bind_hwnd,running,pid,vbox_pid,width,height,dpi
            try:
                idx = int(parts[0])
            except ValueError:
                continue
            name = parts[1]
            is_running = parts[4] == "1"
            pid = int(parts[5]) if len(parts) > 5 else -1

            # 从 ADB 在线列表匹配真实端口（LDPlayer 重启后端口可能变化）
            adb_port = 5554 + idx * 2
            serial = f"emulator-{adb_port}"
            # 如果默认端口不在线，扫描 ADB 在线设备匹配
            if serial not in adb_online and is_running:
                for online_serial in adb_online:
                    port = int(online_serial.split("-")[1])
                    # 匹配运行中但未被其他实例占用的端口
                    if online_serial not in [i.get("adb_serial") for i in instances]:
                        serial = online_serial
                        adb_port = port
                        break

            instances.append({
                "index": idx,
                "name": name,
                "running": is_running,
                "pid": pid,
                "adb_serial": serial,
                "adb_port": adb_port,
            })
        return instances
    except Exception as e:
        logger.error(f"检测模拟器失败: {e}")
        return []


# =====================
# 请求/响应模型
# =====================

class AccountItem(BaseModel):
    qq: str = ""
    nickname: str = ""
    game_id: str = ""
    group: str = "A"
    role: str = "member"
    instance_index: int = 0
    emulator_name: str = ""  # 模拟器名称（只读展示用）
    accel_mode: Optional[str] = None  # Step 2: "apk" | "tun" | None(用全局 default)

class SettingsUpdate(BaseModel):
    ldplayer_path: Optional[str] = None
    adb_path: Optional[str] = None
    game_package: Optional[str] = None
    game_mode: Optional[str] = None
    game_map: Optional[str] = None
    # Step 2 加速器双形态切换
    accelerator_default_mode: Optional[str] = None        # "apk" | "tun"
    accelerator_master_disable_tun: Optional[bool] = None  # True=紧急强制 apk

class AccelModeUpdate(BaseModel):
    mode: Optional[str] = None  # "apk" | "tun" | null(清除 override 用全局 default)

class MasterDisableTunUpdate(BaseModel):
    disable: bool


# =====================
# 加速器 auto-start helper
# =====================

def _find_gameproxy_exe(config: "ConfigManager | None" = None) -> "str | None":
    """按优先级找 gameproxy.exe 路径."""
    from pathlib import Path
    candidates: list[str] = []
    if config is not None:
        try:
            p = getattr(config.settings, "gameproxy_path", "").strip()
            if p:
                candidates.append(p)
        except Exception:
            pass
    # 跟 build.py 输出 dist 平级 (Nuitka standalone 部署常用) — exe 旁边
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        candidates.append(os.path.join(exe_dir, "gameproxy.exe"))
        candidates.append(os.path.join(exe_dir, "gameproxy-go", "dist", "gameproxy.exe"))
    # 开发树 + 绝对路径兜底
    candidates += [
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "gameproxy-go", "dist", "gameproxy.exe"),
        r"D:\game-automation\duitaofnag\gameproxy-go\dist\gameproxy.exe",
        r"C:\Users\Administrator\gameproxy.exe",
        r"C:\Users\Administrator\Desktop\game-automation\gameproxy.exe",
    ]
    for c in candidates:
        if c and Path(c).is_file():
            return c
    return None


def _gameproxy_running() -> bool:
    """通过 GET 127.0.0.1:9901/api/tun/state 判断 gameproxy 是否已在跑."""
    import urllib.request, json as _json
    try:
        with urllib.request.urlopen("http://127.0.0.1:9901/api/tun/state", timeout=1) as resp:
            cur = _json.loads(resp.read().decode("utf-8"))
            return bool(cur.get("ok") and (cur.get("uptime_seconds", 0) > 0 or cur.get("mode") == "tun"))
    except Exception:
        return False


def _ensure_gameproxy_running(config: "ConfigManager | None" = None) -> "tuple[bool, str]":
    """同步函数 (调用方走 asyncio.to_thread). 已在跑就直接返 True; 否则 Popen 启动 + 轮询 6s.

    返回 (ok, message). 失败 message 含原因.
    """
    import subprocess, time
    from pathlib import Path

    if _gameproxy_running():
        return True, "已在运行"

    exe = _find_gameproxy_exe(config)
    if not exe:
        return False, "gameproxy.exe 未找到 (设置 gameproxy_path 或放到 exe 同目录)"

    try:
        log_dir = Path(exe).parent
        log_path = log_dir / "gameproxy.log"
        creationflags = 0
        if os.name == "nt":
            creationflags = 0x00000200 | 0x00000008  # CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS
        subprocess.Popen(
            [exe],
            cwd=str(log_dir),
            stdout=open(log_path, "ab"),
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
        )
    except Exception as e:
        return False, f"Popen 失败: {e}"

    # 等 :9901 起来, 最多 6s
    for _ in range(12):
        time.sleep(0.5)
        if _gameproxy_running():
            return True, f"已启动 (exe: {exe})"
    return False, f"已 Popen 但 6s 内未就绪 (exe: {exe})"


async def _redeploy_overlay(config: "ConfigManager | None", aio_mod) -> None:
    """gameproxy 起来后异步重推浮窗 — 不阻断 caller, 失败 silent."""
    try:
        await aio_mod.sleep(2)  # 等 ldconsole / adb 缓存稳一下
        from .automation.overlay_installer import deploy_all as _d
        res = await aio_mod.to_thread(_d, config)
        logger.info(f"[api] overlay 重推: {res.get('success', 0)}/{res.get('total', 0)}"
                    + (f" — {res.get('reason')}" if res.get('reason') else ""))
    except Exception as e:
        logger.warning(f"[api] overlay 重推异常: {e}")


# =====================
# 应用工厂
# =====================

def create_app(config: ConfigManager) -> FastAPI:
    app = FastAPI(title="FightMaster", version="1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    service = MultiRunnerService()
    # 暴露给其他 router (api_templates 抓实例帧用)
    global _active_service, _active_config
    _active_service = service
    _active_config = config

    # 调试 web UI（独立 0.0.0.0:8901，Mac 浏览器可访问，不影响桌面 webview）
    try:
        from .debug_server import start_in_thread as _start_debug, set_service as _set_debug_service
        _set_debug_service(service)
        _start_debug(host="0.0.0.0", port=8901)
    except Exception as _e:
        import logging as _logging
        _logging.getLogger(__name__).warning(f"debug server 启动失败（不影响主程序）: {_e}")

    # 中控台 v3 路由 — WebSocket 实时推流 (decision/phase_change/intervene_ack/perf)
    try:
        from .api_live import router as _live_router, install_listeners as _install_live
        app.include_router(_live_router)
        _install_live()
        logger.info("[api] /ws/live 中控台实时推流已挂载")
    except Exception as _e:
        logger.warning(f"[api] api_live 挂载失败: {_e}")

    # 决策档案 / 历史会话 (从 8901 迁到主 8900)
    try:
        from .api_decisions import router as _decisions_router
        app.include_router(_decisions_router)
        logger.info("[api] /api/decisions /api/sessions 已挂载")
    except Exception as _e:
        logger.warning(f"[api] api_decisions 挂载失败: {_e}")

    # 模版库 + 模版测试
    try:
        from .api_templates import router as _templates_router
        app.include_router(_templates_router)
        logger.info("[api] /api/templates/* 已挂载")
    except Exception as _e:
        logger.warning(f"[api] api_templates 挂载失败: {_e}")

    # YOLO 测试
    try:
        from .api_yolo import router as _yolo_router
        app.include_router(_yolo_router)
        logger.info("[api] /api/yolo/* 已挂载")
    except Exception as _e:
        logger.warning(f"[api] api_yolo 挂载失败: {_e}")

    # YOLO 标注 / 数据集 / 采集 / 模型上传
    try:
        from .api_yolo_labeler import router as _yolo_lab_router
        app.include_router(_yolo_lab_router)
        logger.info("[api] /api/labeler/* 已挂载")
    except Exception as _e:
        logger.warning(f"[api] api_yolo_labeler 挂载失败: {_e}")

    # 单阶段 dryrun
    try:
        from .api_runner_test import router as _rt_router
        app.include_router(_rt_router)
        logger.info("[api] /api/runner/test_phase 已挂载")
    except Exception as _e:
        logger.warning(f"[api] api_runner_test 挂载失败: {_e}")

    # 性能监控
    try:
        from .api_perf import router as _perf_router
        app.include_router(_perf_router)
        logger.info("[api] /api/perf/* 已挂载")
    except Exception as _e:
        logger.warning(f"[api] api_perf 挂载失败: {_e}")

    # 记忆库
    try:
        from .api_memory import router as _mem_router
        app.include_router(_mem_router)
        logger.info("[api] /api/memory/* 已挂载")
    except Exception as _e:
        logger.warning(f"[api] api_memory 挂载失败: {_e}")

    # 决策回放 + Oracle 标注 (新)
    try:
        from .api_oracle import router as _oracle_router
        app.include_router(_oracle_router)
        logger.info("[api] /api/oracle/* 已挂载")
    except Exception as _e:
        logger.warning(f"[api] api_oracle 挂载失败: {_e}")

    # ROI 调试 / 校准
    try:
        from .api_roi import router as _roi_router
        app.include_router(_roi_router)
        logger.info("[api] /api/roi/* 已挂载")
    except Exception as _e:
        logger.warning(f"[api] api_roi 挂载失败: {_e}")

    @app.on_event("startup")
    async def startup():
        ws_manager.start_drain()
        service.set_broadcast(ws_manager.broadcast_sync)
        # 全局日志拦截器 — 让 backend 启动钩子 / 阶段测试 / overlay 部署 等
        # 没具体 instance contextvar 的日志, 也能进前端右侧日志栏 (标 SYS).
        try:
            from .runner_service import GlobalLogHandler
            import logging as _logging
            _global_handler = GlobalLogHandler(ws_manager.broadcast_sync)
            _global_handler.setLevel(_logging.INFO)
            _logging.getLogger("backend").addHandler(_global_handler)
            logger.info("[api] 全局日志拦截器已挂 (backend.* → 前端 SYS)")
        except Exception as _e:
            logger.warning(f"[api] 全局日志拦截器挂载失败: {_e}")
        # OCR pre-warm: 启动时跑一次 dummy 图, 让 RapidOCR 模型权重 + ONNX runtime
        # 加载到内存. 避免第一次真实 OCR 调用时 cold start 慢 4-6 倍 (实测 2.7s vs 0.5s).
        # 在后台线程跑, 不阻塞 startup.
        import asyncio as _asyncio
        async def _ocr_prewarm():
            try:
                import numpy as _np
                from .automation.ocr_dismisser import OcrDismisser
                ocr = OcrDismisser()
                dummy = _np.zeros((100, 200, 3), dtype=_np.uint8)
                # _ocr_all 是同步, 走 to_thread 不卡 loop
                await _asyncio.to_thread(ocr._ocr_all, dummy)
                logger.info("[api] OCR pre-warm 完成")
            except Exception as e:
                logger.warning(f"[api] OCR pre-warm 失败 (不影响功能): {e}")
        _asyncio.create_task(_ocr_prewarm())

        # gameproxy auto-start: backend 启动 = 加速器自动跟着起.
        # 客户分发场景下双击 GameBot.exe 即一切就绪, 无需手动点 UI 按钮.
        # 加速器起来后再推浮窗 APK 到所有在线模拟器 (心理 + 物理双保险).
        async def _gameproxy_autostart():
            try:
                ok, msg = await _asyncio.to_thread(_ensure_gameproxy_running, config)
                logger.info(f"[api] gameproxy auto-start: {msg}")
                if not ok:
                    logger.warning(f"[api] gameproxy 自启失败 (不影响 backend): {msg}")
                    return
                # 等 ldconsole / adb 服务稳一会儿再扫
                await _asyncio.sleep(2)
                try:
                    from .automation.overlay_installer import deploy_all as _deploy_overlay
                    res = await _asyncio.to_thread(_deploy_overlay, config)
                    logger.info(f"[api] overlay 部署: {res.get('success', 0)}/{res.get('total', 0)} 成功"
                                + (f" — {res.get('reason')}" if res.get("reason") else ""))
                except Exception as e:
                    logger.warning(f"[api] overlay 部署异常 (不影响 backend): {e}")
            except Exception as e:
                logger.warning(f"[api] gameproxy auto-start 异常: {e}")
        _asyncio.create_task(_gameproxy_autostart())

    @app.on_event("shutdown")
    async def shutdown():
        ws_manager.stop_drain()
        if service.running:
            await service.stop_all()
        # test_phase 走旁路构造的 controller 也要清理截图流
        try:
            from .api_runner_test import stop_test_controllers
            n = stop_test_controllers(service)
            if n > 0:
                logger.info(f"[shutdown] 关 {n} 条 test 截图流")
        except Exception as e:
            logger.debug(f"[shutdown] stop test controllers 异常: {e}")

    # ── 模拟器检测 ──

    @app.get("/api/emulators")
    async def get_emulators():
        """检测本机所有雷电模拟器实例"""
        global _emulators_cache
        ldp = config.settings.ldplayer_path
        now = time.monotonic()
        cached = _emulators_cache
        if cached and now - cached[0] < _EMULATORS_CACHE_TTL and cached[2] == ldp:
            return {"instances": cached[1], "ldplayer_path": ldp}
        instances = await asyncio.to_thread(detect_ldplayer_instances, ldp)
        _emulators_cache = (now, instances, ldp)
        return {"instances": instances, "ldplayer_path": ldp}

    # ── 控制 ──

    @app.post("/api/start")
    async def start():
        if service.running or service._starting:
            return {"ok": False, "error": "已在运行中"}
        config.load()
        try:
            await service.start_all(config.settings, config.accounts)
            return {"ok": True}
        except Exception as e:
            logger.error(f"启动失败: {e}", exc_info=True)
            return {"ok": False, "error": str(e)}

    @app.post("/api/start/{instance_index}")
    async def start_one(instance_index: int):
        """启动单个实例"""
        if service.running:
            return {"ok": False, "error": "请先停止当前运行"}
        config.load()
        accounts = [a for a in config.accounts if a.instance_index == instance_index]
        if not accounts:
            accounts = [AccountConfig(
                qq="", nickname=f"实例{instance_index}", game_id="",
                group="A", role="captain", instance_index=instance_index,
            )]
        try:
            await service.start_all(config.settings, accounts)
            return {"ok": True}
        except Exception as e:
            logger.error(f"启动实例{instance_index}失败: {e}", exc_info=True)
            return {"ok": False, "error": str(e)}

    @app.post("/api/stop")
    async def stop():
        if not service.running:
            return {"ok": False, "error": "未在运行"}
        try:
            await service.stop_all()
            return {"ok": True}
        except Exception as e:
            logger.error(f"停止失败: {e}", exc_info=True)
            return {"ok": False, "error": str(e)}

    @app.get("/api/status")
    async def status():
        return service.get_all_status()

    # ── 设置 ──

    @app.get("/api/settings")
    async def get_settings():
        return {
            "ldplayer_path": config.settings.ldplayer_path,
            "adb_path": config.settings.adb_path,
            "game_package": config.settings.game_package,
            "game_mode": config.settings.game_mode,
            "game_map": config.settings.game_map,
        }

    @app.put("/api/settings")
    async def put_settings(data: SettingsUpdate):
        for key, value in data.dict(exclude_none=True).items():
            if hasattr(config.settings, key):
                setattr(config.settings, key, value)
        config.save_settings()
        return {"ok": True}

    # ── 账号 ──

    @app.get("/api/accounts")
    async def get_accounts():
        return [
            {
                "qq": a.qq,
                "nickname": a.nickname,
                "game_id": a.game_id,
                "group": a.group,
                "role": a.role,
                "instance_index": a.instance_index,
                "accel_mode": a.accel_mode,
            }
            for a in config.accounts
        ]

    @app.put("/api/accounts")
    async def put_accounts(items: list[AccountItem]):
        config.accounts = [
            AccountConfig(
                qq=it.qq, nickname=it.nickname, game_id=it.game_id,
                group=it.group, role=it.role, instance_index=it.instance_index,
                accel_mode=it.accel_mode,
            )
            for it in items
        ]
        config.save_accounts()
        return {"ok": True}

    # ── Step 2 加速器双形态切换 ──

    @app.get("/api/accelerator/mode")
    async def get_accel_mode_state():
        """返回当前加速器全局状态 + 每 instance 的 effective mode.

        effective mode 决议规则 (跟 SingleInstanceRunner._resolve_accel_mode 一致):
          1. master_disable_tun=True → 强制 "apk"
          2. account.accel_mode (per-instance override)
          3. settings.accelerator_default_mode (全局 default)
        """
        master_disable = bool(getattr(config.settings, "accelerator_master_disable_tun", False))
        default_mode = getattr(config.settings, "accelerator_default_mode", "apk")

        instances = []
        for a in config.accounts:
            if master_disable:
                effective = "apk"
            elif a.accel_mode:
                effective = a.accel_mode
            else:
                effective = default_mode
            instances.append({
                "instance_index": a.instance_index,
                "qq": a.qq,
                "nickname": a.nickname,
                "accel_mode_override": a.accel_mode,  # None 或 "apk"/"tun"
                "effective_mode": effective,
            })
        return {
            "master_disable_tun": master_disable,
            "default_mode": default_mode,
            "instances": instances,
        }

    @app.post("/api/accelerator/mode/{instance_index}")
    async def set_accel_mode(instance_index: int, payload: AccelModeUpdate):
        """设某 instance 的 accel_mode override. mode=null/缺省 = 清 override (用全局 default)."""
        target = next((a for a in config.accounts if a.instance_index == instance_index), None)
        if target is None:
            return {"ok": False, "error": f"instance {instance_index} 未找到"}
        new_mode = payload.mode
        if new_mode is not None and new_mode not in ("apk", "tun"):
            return {"ok": False, "error": f"mode 必须是 apk/tun/null, 收到 {new_mode!r}"}
        target.accel_mode = new_mode
        config.save_accounts()
        return {"ok": True, "instance_index": instance_index, "accel_mode": new_mode}

    @app.post("/api/accelerator/master_disable_tun")
    async def set_master_disable_tun(payload: MasterDisableTunUpdate):
        """紧急 kill switch: True=强制全部走 apk, False=正常."""
        config.settings.accelerator_master_disable_tun = payload.disable
        config.save_settings()
        return {"ok": True, "master_disable_tun": payload.disable}

    @app.get("/api/tun/state")
    async def tun_state():
        """反代 gameproxy :9901/api/tun/state — 加速器页用 (当前模式 + 实时计数).

        gameproxy 不可达时返回 mode=offline, 让前端区分"服务挂了" vs "服务在跑没改包".
        """
        import urllib.request, json as _json
        try:
            with urllib.request.urlopen("http://127.0.0.1:9901/api/tun/state", timeout=2) as resp:
                return _json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            return {"ok": False, "mode": "offline", "error": str(e), "counters": {}}

    @app.post("/api/tun/start")
    async def tun_start():
        """启动 gameproxy.exe (本地 TUN 加速器) — UI 手动启停按钮调.

        正常流程是 backend 启动时已自启 (startup hook), 此端点用于 gameproxy 崩了
        手动重启的兜底.
        """
        import asyncio as _aio
        # 已在跑就不重复启 — 但还是顺势重推一次浮窗 (用户可能新加了模拟器)
        if _gameproxy_running():
            _aio.create_task(_redeploy_overlay(config, _aio))
            try:
                import urllib.request, json as _json
                with urllib.request.urlopen("http://127.0.0.1:9901/api/tun/state", timeout=1) as resp:
                    cur = _json.loads(resp.read().decode("utf-8"))
                return {"ok": True, "already_running": True, "state": cur}
            except Exception:
                return {"ok": True, "already_running": True}
        ok, msg = await _aio.to_thread(_ensure_gameproxy_running, config)
        if ok:
            _aio.create_task(_redeploy_overlay(config, _aio))
        return {"ok": ok, "message": msg}

    @app.post("/api/tun/stop")
    async def tun_stop():
        """杀 gameproxy.exe 进程 (本地 TUN 加速器停) + 同步关所有模拟器浮窗."""
        import os, subprocess
        import asyncio as _aio
        # 先关浮窗 (服务都在跑就让它跟着 gameproxy 一起走)
        try:
            from .automation.overlay_installer import stop_all as _stop_overlay
            _aio.create_task(_aio.to_thread(_stop_overlay, config))
        except Exception:
            pass
        try:
            if os.name == "nt":
                # taskkill /F /IM gameproxy.exe → 杀全部同名实例
                r = subprocess.run(
                    ["taskkill", "/F", "/IM", "gameproxy.exe"],
                    capture_output=True, text=True, timeout=5,
                )
                ok = r.returncode == 0
                return {"ok": ok, "stdout": r.stdout, "stderr": r.stderr}
            else:
                r = subprocess.run(["pkill", "-f", "gameproxy"], capture_output=True, text=True, timeout=5)
                return {"ok": r.returncode in (0, 1), "stdout": r.stdout, "stderr": r.stderr}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @app.post("/api/overlay/deploy")
    async def overlay_deploy():
        """手动重推浮窗 APK 到所有在线模拟器.

        正常流程是 gameproxy 自启时一并推送; 这个 endpoint 用于手动重推
        (新连模拟器 / APK 升级 / 浮窗被用户误关).
        """
        import asyncio as _aio
        try:
            from .automation.overlay_installer import deploy_all as _deploy
            res = await _aio.to_thread(_deploy, config)
            return res
        except Exception as e:
            return {"ok": False, "reason": f"deploy 异常: {e}"}

    @app.post("/api/overlay/stop")
    async def overlay_stop():
        """关停所有模拟器的浮窗 service (gameproxy 关时同步关浮窗用)."""
        import asyncio as _aio
        try:
            from .automation.overlay_installer import stop_all as _stop
            res = await _aio.to_thread(_stop, config)
            return res
        except Exception as e:
            return {"ok": False, "reason": f"stop 异常: {e}"}

    @app.get("/api/proxy_verify")
    async def proxy_verify():
        """反代 gameproxy :9901/verify HTML, 让 cloudflare 公网 UI 也能 access.

        APK 时代 verify 走 vpn-app 拦虚拟域名 gameproxy-verify; TUN 时代没 vpn-app
        拦截了, 这个 endpoint 让 GameBot UI 上点验证按钮也能看到 verify 页 (含
        gameproxy uptime/active_connections/total_connections, 区分本地 vs 远端).
        """
        import urllib.request
        try:
            with urllib.request.urlopen("http://127.0.0.1:9901/verify", timeout=3) as resp:
                html = resp.read().decode("utf-8")
            return Response(content=html, media_type="text/html; charset=utf-8")
        except Exception as e:
            err_html = f"""<html><body style="font-family:sans-serif;padding:40px;background:#0A0E1A;color:#e0e0e0">
<h2 style="color:#ff5252">gameproxy :9901/verify 不可达</h2>
<p>错误: <code>{e}</code></p>
<p>检查 gameproxy.exe 是否在跑 (ps / netstat) 以及 :9901 是否监听.</p>
</body></html>"""
            return Response(content=err_html, media_type="text/html; charset=utf-8", status_code=502)

    # ── 截图 ──

    @app.get("/api/screenshot/{instance_index}")
    async def screenshot(instance_index: int, w: int = 0):
        """截图，可选 ?w=320 缩小返回（缩略图用）"""
        adb_path = config.settings.adb_path or os.path.join(
            config.settings.ldplayer_path, "adb.exe")
        jpg = await service.get_screenshot(instance_index, adb_path=adb_path, max_width=w)
        if jpg is None:
            return Response(content=b"", status_code=204)
        return Response(content=jpg, media_type="image/jpeg")

    @app.get("/api/stream/{instance_index}")
    async def stream(instance_index: int, fps: int = 5, w: int = 0):
        """MJPEG 流式截图 (multipart/x-mixed-replace).

        多客户端订阅同一 instance 时共享一个 producer (避免重复 screencap).
        ?fps=N (1-15) 控制帧率, ?w=N 缩放宽度.
        """
        from fastapi.responses import StreamingResponse
        fps = max(1, min(15, int(fps)))
        adb_path = config.settings.adb_path or os.path.join(
            config.settings.ldplayer_path, "adb.exe")
        broadcaster = service.get_or_create_stream_broadcaster(
            instance_index, fps=fps, max_width=int(w), adb_path=adb_path,
        )
        boundary = b"--gpframe"

        async def gen():
            queue = await broadcaster.subscribe()
            try:
                while True:
                    jpg = await queue.get()
                    if jpg is None:  # broadcaster 关闭信号
                        break
                    yield (
                        b"--gpframe\r\n"
                        b"Content-Type: image/jpeg\r\n"
                        b"Content-Length: " + str(len(jpg)).encode() + b"\r\n\r\n"
                        + jpg + b"\r\n"
                    )
            finally:
                broadcaster.unsubscribe(queue)

        headers = {
            "Cache-Control": "no-cache, no-store, must-revalidate, private",
            "Pragma": "no-cache",
            "Connection": "close",
            "X-Accel-Buffering": "no",
        }
        return StreamingResponse(
            gen(),
            media_type=f"multipart/x-mixed-replace; boundary={boundary.decode().lstrip('-')}",
            headers=headers,
        )

    # ── 健康 ──

    @app.get("/api/health")
    async def health(window: int = 300):
        """健康度仪表盘。
        ?window=N  指标聚合窗口（秒），默认最近 5 分钟；window=0 用全部 in-memory ring（最多 10000 条）。
        包含：截图/OCR/template_match/tap/phase 各动作 P50/P95/P99 + phase 按名分组 + sys 快照。
        """
        from .automation import metrics
        win: Optional[float] = None if window == 0 else float(window)
        return {
            "ok": True,
            "running": service.running,
            "instances": len(service._instances),
            "uptime": round(time.time() - service._start_time, 1) if service.running else 0,
            "metrics": metrics.summary(window_seconds=win),
        }

    # ── WebSocket ──

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket):
        await ws_manager.connect(websocket)
        try:
            if service.running:
                snapshot = service.get_all_status()
                await websocket.send_json({"type": "snapshot", **snapshot})
            while True:
                data = await websocket.receive_text()
                if data == "ping":
                    await websocket.send_text("pong")
        except WebSocketDisconnect:
            ws_manager.disconnect(websocket)
        except Exception:
            ws_manager.disconnect(websocket)

    return app

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

class SettingsUpdate(BaseModel):
    ldplayer_path: Optional[str] = None
    adb_path: Optional[str] = None
    game_package: Optional[str] = None
    game_mode: Optional[str] = None
    game_map: Optional[str] = None


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

    # 调试 web UI（独立 0.0.0.0:8901，Mac 浏览器可访问，不影响桌面 webview）
    try:
        from .debug_server import start_in_thread as _start_debug, set_service as _set_debug_service
        _set_debug_service(service)
        _start_debug(host="0.0.0.0", port=8901)
    except Exception as _e:
        import logging as _logging
        _logging.getLogger(__name__).warning(f"debug server 启动失败（不影响主程序）: {_e}")

    @app.on_event("startup")
    async def startup():
        ws_manager.start_drain()
        service.set_broadcast(ws_manager.broadcast_sync)

    @app.on_event("shutdown")
    async def shutdown():
        ws_manager.stop_drain()
        if service.running:
            await service.stop_all()

    # ── 模拟器检测 ──

    @app.get("/api/emulators")
    async def get_emulators():
        """检测本机所有雷电模拟器实例"""
        if config.settings.dev_mock:
            # Mock 模式：返回假数据
            return {"instances": [
                {"index": 0, "name": "雷电模拟器", "running": True, "pid": 1234, "adb_serial": "emulator-5554", "adb_port": 5554},
                {"index": 1, "name": "雷电模拟器-1", "running": True, "pid": 1235, "adb_serial": "emulator-5556", "adb_port": 5556},
                {"index": 2, "name": "雷电模拟器-2", "running": True, "pid": 1236, "adb_serial": "emulator-5558", "adb_port": 5558},
                {"index": 3, "name": "雷电模拟器-3", "running": True, "pid": 1237, "adb_serial": "emulator-5560", "adb_port": 5560},
                {"index": 4, "name": "雷电模拟器-4", "running": False, "pid": -1, "adb_serial": "emulator-5562", "adb_port": 5562},
                {"index": 5, "name": "雷电模拟器-5", "running": False, "pid": -1, "adb_serial": "emulator-5564", "adb_port": 5564},
            ], "ldplayer_path": config.settings.ldplayer_path}
        instances = detect_ldplayer_instances(config.settings.ldplayer_path)
        return {"instances": instances, "ldplayer_path": config.settings.ldplayer_path}

    # ── 控制 ──

    @app.post("/api/start")
    async def start():
        if service.running or service._starting:
            return {"ok": False, "error": "已在运行中"}
        config.load()
        try:
            if config.settings.dev_mock:
                await service.start_mock(config.accounts)
            else:
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
            if config.settings.dev_mock:
                await service.start_mock(accounts)
            else:
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
            }
            for a in config.accounts
        ]

    @app.put("/api/accounts")
    async def put_accounts(items: list[AccountItem]):
        config.accounts = [
            AccountConfig(
                qq=it.qq, nickname=it.nickname, game_id=it.game_id,
                group=it.group, role=it.role, instance_index=it.instance_index,
            )
            for it in items
        ]
        config.save_accounts()
        return {"ok": True}

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

"""
FastAPI 路由 — V1
REST API: 启停控制、模拟器检测、配置 CRUD、截图、状态查询
WebSocket: 实时推送实例状态、日志
"""

import asyncio
import json
import logging
import os
import subprocess
import time
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
            adb_port = 5554 + idx * 2
            instances.append({
                "index": idx,
                "name": name,
                "running": is_running,
                "pid": pid,
                "adb_serial": f"emulator-{adb_port}",
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
    app = FastAPI(title="游戏自动化控制台", version="1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    service = MultiRunnerService()

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
        instances = detect_ldplayer_instances(config.settings.ldplayer_path)
        return {"instances": instances, "ldplayer_path": config.settings.ldplayer_path}

    # ── 控制 ──

    @app.post("/api/start")
    async def start():
        if service.running:
            return {"ok": False, "error": "已在运行中"}
        config.load()
        await service.start_all(config.settings, config.accounts)
        return {"ok": True}

    @app.post("/api/start/{instance_index}")
    async def start_one(instance_index: int):
        """启动单个实例（测试用，不需要预先配置账号）"""
        if service.running:
            return {"ok": False, "error": "请先停止当前运行"}
        config.load()
        # 先找已有配置
        accounts = [a for a in config.accounts if a.instance_index == instance_index]
        if not accounts:
            # 没有配置就自动创建一个临时的
            accounts = [AccountConfig(
                qq="", nickname=f"实例{instance_index}", game_id="",
                group="A", role="captain", instance_index=instance_index,
            )]
        await service.start_all(config.settings, accounts)
        return {"ok": True}

    @app.post("/api/stop")
    async def stop():
        if not service.running:
            return {"ok": False, "error": "未在运行"}
        await service.stop_all()
        return {"ok": True}

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
    async def screenshot(instance_index: int):
        jpg = await service.get_screenshot(instance_index)
        if jpg is None:
            return Response(content=b"", status_code=204)
        return Response(content=jpg, media_type="image/jpeg")

    # ── 健康 ──

    @app.get("/api/health")
    async def health():
        return {
            "ok": True,
            "running": service.running,
            "instances": len(service._instances),
            "uptime": round(time.time() - service._start_time, 1) if service.running else 0,
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

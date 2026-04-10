"""
FastAPI 路由 — V1 简化版
REST API: 启停控制、配置 CRUD、截图、状态查询
WebSocket: 实时推送实例状态、日志
"""

import asyncio
import json
import logging
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
    """管理所有 WebSocket 连接，广播消息"""

    def __init__(self):
        self.active: list[WebSocket] = []
        self._queue: asyncio.Queue = asyncio.Queue()
        self._drain_task: Optional[asyncio.Task] = None

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)
        logger.info(f"WebSocket 连接: {len(self.active)} 个客户端")

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, message: dict):
        """异步广播给所有客户端"""
        dead = []
        for ws in self.active:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    def broadcast_sync(self, message: dict):
        """从同步代码中调度广播（用于日志回调）"""
        self._queue.put_nowait(message)

    async def _drain_loop(self):
        """持续从队列中取消息并广播"""
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
# 请求/响应模型
# =====================

class AccountItem(BaseModel):
    qq: str
    nickname: str
    game_id: str
    group: str
    role: str
    instance_index: int


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
        # 注入广播函数：runner_service 的回调通过 broadcast_sync 调度
        service.set_broadcast(ws_manager.broadcast_sync)

    @app.on_event("shutdown")
    async def shutdown():
        ws_manager.stop_drain()
        if service.running:
            await service.stop_all()

    # ── 控制 ──

    @app.post("/api/start")
    async def start():
        if service.running:
            return {"ok": False, "error": "已在运行中"}
        config.load()  # 重新加载最新配置
        await service.start_all(config.settings, config.accounts)
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
                qq=it.qq,
                nickname=it.nickname,
                game_id=it.game_id,
                group=it.group,
                role=it.role,
                instance_index=it.instance_index,
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

    # ── 健康检查 ──

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
            # 立即发送当前状态快照
            if service.running:
                snapshot = service.get_all_status()
                await websocket.send_json({"type": "snapshot", **snapshot})

            # 保持连接，处理心跳
            while True:
                data = await websocket.receive_text()
                if data == "ping":
                    await websocket.send_text("pong")
        except WebSocketDisconnect:
            ws_manager.disconnect(websocket)
        except Exception:
            ws_manager.disconnect(websocket)

    return app

"""
LiveBroadcaster + /ws/live — 实时把决策事件推给中控台前端.

数据源:
  - decision_log finalize → register_live_listener(_on_decision)
  - phase_change (RunnerFSM._on_phase_change → broadcast_phase_change)
  - intervene_ack (api_intervene.py → broadcast_intervene_ack)
  - perf snapshot (perf_collector → broadcast_perf, 1Hz)

WebSocket 协议见 plan / vivid-giggling-summit.md.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

router = APIRouter()


class LiveBroadcaster:
    """全局单例. 多 client 广播 JSON 事件."""

    def __init__(self):
        self._clients: set[WebSocket] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock = asyncio.Lock()

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """从同步钩子 (decision_log listener) schedule 到主 loop 用."""
        self._loop = loop

    async def add(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.add(ws)

    async def remove(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)

    async def broadcast(self, event: dict) -> None:
        """向所有 client 推一条事件. 失败 client 自动剔除."""
        if not self._clients:
            return
        try:
            payload = json.dumps(event, ensure_ascii=False, default=_json_default)
        except Exception as e:
            logger.debug(f"[live] json err: {e}")
            return
        async with self._lock:
            dead = []
            for ws in list(self._clients):
                try:
                    await ws.send_text(payload)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self._clients.discard(ws)

    def schedule_broadcast(self, event: dict) -> None:
        """从同步上下文 (decision_log listener) 安全 schedule 一次推送."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            asyncio.run_coroutine_threadsafe(self.broadcast(event), loop)
        except Exception as e:
            logger.debug(f"[live] schedule_broadcast err: {e}")


def _json_default(o: Any):
    if isinstance(o, (set, frozenset)):
        return list(o)
    if hasattr(o, "isoformat"):
        return o.isoformat()
    return str(o)


# 全局单例
_broadcaster = LiveBroadcaster()


def get_broadcaster() -> LiveBroadcaster:
    return _broadcaster


# ─── decision_log listener: finalize → schedule broadcast ───


def _on_decision_finalized(summary: dict) -> None:
    """从 decision_log finalize 回调 (同步上下文). schedule 异步推送."""
    event = {
        "type": "decision",
        "ts": time.time(),
        **summary,
    }
    _broadcaster.schedule_broadcast(event)


def install_listeners() -> None:
    """启动时调一次, 把 broadcaster 接到 decision_log."""
    try:
        from .automation.decision_log import get_recorder
        get_recorder().register_live_listener(_on_decision_finalized)
        logger.info("[live] decision_log listener installed")
    except Exception as e:
        logger.warning(f"[live] install_listeners err: {e}")


def broadcast_perf(snapshot: dict) -> None:
    _broadcaster.schedule_broadcast({
        "type": "perf",
        "ts": time.time(),
        **snapshot,
    })


# ─── WebSocket 路由 ───


@router.websocket("/ws/live")
async def ws_live(ws: WebSocket):
    await ws.accept()
    # 把 loop 记下来 (从 decision_log 同步钩子用)
    _broadcaster.attach_loop(asyncio.get_running_loop())
    await _broadcaster.add(ws)
    # 进门发个 hello (告诉前端连上了)
    try:
        await ws.send_text(json.dumps({
            "type": "hello",
            "ts": time.time(),
            "version": "v3",
        }))
    except Exception:
        pass
    try:
        while True:
            # 读 client 消息 (心跳 / 订阅过滤)
            try:
                msg = await ws.receive_text()
            except WebSocketDisconnect:
                break
            # 简单 ping/pong (前端可发 {"type":"ping"})
            try:
                data = json.loads(msg)
                if data.get("type") == "ping":
                    await ws.send_text(json.dumps({"type": "pong", "ts": time.time()}))
            except Exception:
                pass
    finally:
        await _broadcaster.remove(ws)

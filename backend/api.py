"""
FastAPI 路由
REST API：启停控制、配置 CRUD、状态查询
WebSocket：实时推送实例状态、日志、统计
"""

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from .config import ConfigManager
from .coordinator import Coordinator
from .models import LogEntry

logger = logging.getLogger(__name__)


# =====================
# WebSocket 连接管理
# =====================

class ConnectionManager:
    """管理所有 WebSocket 连接，广播消息"""

    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)
        logger.info(f"WebSocket 连接: {len(self.active)} 个客户端")

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)
        logger.info(f"WebSocket 断开: {len(self.active)} 个客户端")

    async def broadcast(self, message: dict):
        dead = []
        for ws in self.active:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


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
    llm_api_url: Optional[str] = None
    llm_api_key: Optional[str] = None
    game_package: Optional[str] = None
    game_mode: Optional[str] = None
    game_map: Optional[str] = None
    match_timeout: Optional[int] = None
    state_timeout: Optional[int] = None
    screenshot_interval: Optional[float] = None
    dev_mock: Optional[bool] = None


class TemplateCapture(BaseModel):
    instance_index: int
    region_x: int
    region_y: int
    region_w: int
    region_h: int
    name: str
    category: str
    threshold: float = 0.85


# =====================
# 全局状态
# =====================

_config: Optional[ConfigManager] = None
_coordinator: Optional[Coordinator] = None
_coordinator_task: Optional[asyncio.Task] = None


def create_app(config: ConfigManager) -> FastAPI:
    """创建 FastAPI 应用"""
    global _config
    _config = config

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("FastAPI 启动")
        yield
        # 关闭时停止协调器
        if _coordinator_task and not _coordinator_task.done():
            await _stop_coordinator()
        logger.info("FastAPI 关闭")

    app = FastAPI(title="游戏自动化控制台", lifespan=lifespan)

    # CORS（pywebview 内嵌时可能从 file:// 或不同端口访问）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    _register_routes(app)
    return app


def _register_routes(app: FastAPI):
    """注册所有路由"""

    # =====================
    # 控制接口
    # =====================

    @app.post("/api/start")
    async def start_automation():
        """启动自动化流程"""
        global _coordinator, _coordinator_task

        if _coordinator_task and not _coordinator_task.done():
            return {"ok": False, "error": "已在运行中"}

        _coordinator = Coordinator(
            config=_config,
            on_log=_on_log,
            on_state_change=_on_state_change,
            on_stats_update=_on_stats_update,
        )

        await _coordinator.initialize()
        _coordinator_task = asyncio.create_task(_coordinator.start())

        return {"ok": True, "message": "已启动"}

    @app.post("/api/stop")
    async def stop_automation():
        """停止自动化"""
        await _stop_coordinator()
        return {"ok": True, "message": "已停止"}

    @app.post("/api/pause")
    async def pause_automation():
        """暂停"""
        if _coordinator:
            _coordinator.pause()
            return {"ok": True}
        return {"ok": False, "error": "未运行"}

    @app.post("/api/resume")
    async def resume_automation():
        """恢复"""
        if _coordinator:
            _coordinator.resume()
            return {"ok": True}
        return {"ok": False, "error": "未运行"}

    # =====================
    # 状态查询
    # =====================

    @app.get("/api/status")
    async def get_status():
        """获取所有实例状态"""
        if not _coordinator:
            return {
                "running": False,
                "instances": {},
                "stats": {},
            }
        return {
            "running": True,
            "paused": _coordinator._paused,
            "instances": _coordinator.get_all_states(),
            "stats": _coordinator._get_stats_dict(),
        }

    # =====================
    # 配置管���
    # =====================

    @app.get("/api/settings")
    async def get_settings():
        """获取全局设置"""
        s = _config.settings
        return {
            "ldplayer_path": s.ldplayer_path,
            "llm_api_url": s.llm_api_url,
            "llm_api_key": "***" if s.llm_api_key else "",
            "game_package": s.game_package,
            "game_mode": s.game_mode,
            "game_map": s.game_map,
            "match_timeout": s.match_timeout,
            "state_timeout": s.state_timeout,
            "screenshot_interval": s.screenshot_interval,
            "dev_mock": s.dev_mock,
        }

    @app.put("/api/settings")
    async def update_settings(update: SettingsUpdate):
        """更新设置"""
        for key, val in update.model_dump(exclude_none=True).items():
            if hasattr(_config.settings, key):
                setattr(_config.settings, key, val)
        _config.save_settings()
        return {"ok": True}

    @app.get("/api/accounts")
    async def get_accounts():
        """获取账号列表"""
        return [
            {
                "qq": a.qq, "nickname": a.nickname, "game_id": a.game_id,
                "group": a.group, "role": a.role, "instance_index": a.instance_index,
            }
            for a in _config.accounts
        ]

    @app.put("/api/accounts")
    async def update_accounts(accounts: list[AccountItem]):
        """更新账号列表"""
        from .config import AccountConfig
        _config.accounts = [AccountConfig(**a.model_dump()) for a in accounts]
        _config.save_accounts()
        return {"ok": True}

    # =====================
    # 模板工具
    # =====================

    @app.get("/api/templates")
    async def list_templates(category: Optional[str] = None):
        """列出模板"""
        if not _coordinator:
            return []
        tool = _get_template_tool()
        if tool:
            return tool.list_templates(category)
        return []

    @app.post("/api/templates/capture")
    async def capture_template(req: TemplateCapture):
        """采集模板"""
        if not _coordinator:
            return {"ok": False, "error": "未运行"}

        ctrl = _coordinator.controllers.get(req.instance_index)
        if not ctrl:
            return {"ok": False, "error": f"实例 {req.instance_index} 不存在"}

        img = await ctrl.screenshot()
        if img is None:
            return {"ok": False, "error": "截图失败"}

        tool = _get_template_tool()
        if not tool:
            return {"ok": False, "error": "模板工具不可用"}

        result = tool.capture_template(
            screenshot=img,
            region=(req.region_x, req.region_y, req.region_w, req.region_h),
            name=req.name,
            category=req.category,
            threshold=req.threshold,
        )
        return {"ok": result.success, "template": result.template_name, "error": result.error}

    @app.get("/api/templates/verify")
    async def verify_templates(instance_index: int = 0, category: Optional[str] = None):
        """批量验证模板"""
        if not _coordinator:
            return {"ok": False, "error": "未运行"}

        ctrl = _coordinator.controllers.get(instance_index)
        if not ctrl:
            return {"ok": False, "error": "实例不存在"}

        img = await ctrl.screenshot()
        if img is None:
            return {"ok": False, "error": "截图失败"}

        tool = _get_template_tool()
        if not tool:
            return {"ok": False, "error": "模板工具不可用"}

        return tool.verify_all(img, category)

    @app.delete("/api/templates/{template_key:path}")
    async def delete_template(template_key: str):
        """删除模板"""
        tool = _get_template_tool()
        if tool and tool.delete_template(template_key):
            return {"ok": True}
        return {"ok": False, "error": "模板不存在"}

    @app.get("/api/screenshot/{instance_index}")
    async def get_screenshot(instance_index: int):
        """获取实例截图（JPEG）"""
        if not _coordinator:
            return Response(status_code=404)

        ctrl = _coordinator.controllers.get(instance_index)
        if not ctrl:
            return Response(status_code=404)

        img = await ctrl.screenshot()
        if img is None:
            return Response(status_code=500)

        import cv2
        _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return Response(content=buf.tobytes(), media_type="image/jpeg")

    # =====================
    # 调试工具
    # =====================

    @app.post("/api/debug/test-llm")
    async def test_llm(instance_index: int = 0, prompt_key: str = "detect_popup"):
        """
        测试 LLM API：截图 → 发给 LLM → 返回结果 + 耗时
        用于验证 API 配置是否正确、延迟是否可接受
        """
        import time as _time

        if not _coordinator:
            # 未启动时直接测试 LLM（不需要模拟器）
            from .recognition.llm_vision import LLMVision
            llm = LLMVision(
                api_url=_config.settings.llm_api_url,
                api_key=_config.settings.llm_api_key,
                mock=_config.settings.dev_mock,
            )
            import numpy as np
            img = np.zeros((720, 1280, 3), dtype=np.uint8)
            result = await llm.analyze(img, prompt_key)
            return {
                "ok": result.success,
                "latency_ms": round(result.latency_ms, 1),
                "response": result.parsed or result.raw_response[:500],
                "error": result.error,
                "note": "使用空白图片测试（未启动模拟器）",
            }

        ctrl = _coordinator.controllers.get(instance_index)
        if not ctrl:
            return {"ok": False, "error": f"实例 {instance_index} 不存在"}

        # 截图
        t0 = _time.time()
        img = await ctrl.screenshot()
        screenshot_ms = (_time.time() - t0) * 1000

        if img is None:
            return {"ok": False, "error": "截图失败"}

        # LLM 分析
        agent = next(iter(_coordinator.agents.values()))
        result = await agent.pipeline.llm.analyze(img, prompt_key)

        return {
            "ok": result.success,
            "screenshot_ms": round(screenshot_ms, 1),
            "llm_ms": round(result.latency_ms, 1),
            "total_ms": round(screenshot_ms + result.latency_ms, 1),
            "response": result.parsed or result.raw_response[:500],
            "error": result.error,
        }

    @app.post("/api/debug/test-ocr")
    async def test_ocr(instance_index: int = 0):
        """测试 OCR：截图 → PaddleOCR → 返回识别文字 + 耗时"""
        import time as _time

        if not _coordinator:
            return {"ok": False, "error": "请先启动"}

        ctrl = _coordinator.controllers.get(instance_index)
        if not ctrl:
            return {"ok": False, "error": "实例不存在"}

        t0 = _time.time()
        img = await ctrl.screenshot()
        screenshot_ms = (_time.time() - t0) * 1000

        if img is None:
            return {"ok": False, "error": "截图失败"}

        agent = next(iter(_coordinator.agents.values()))
        t1 = _time.time()
        ocr_resp = agent.pipeline.ocr.recognize(img)
        ocr_ms = (_time.time() - t1) * 1000

        return {
            "ok": True,
            "screenshot_ms": round(screenshot_ms, 1),
            "ocr_ms": round(ocr_ms, 1),
            "total_ms": round(screenshot_ms + ocr_ms, 1),
            "text_count": len(ocr_resp.results),
            "full_text": ocr_resp.full_text[:500],
            "results": [
                {"text": r.text, "confidence": round(r.confidence, 3),
                 "center": [r.center_x, r.center_y]}
                for r in ocr_resp.results[:20]
            ],
        }

    @app.post("/api/debug/test-template")
    async def test_template_match(instance_index: int = 0, category: Optional[str] = None):
        """测试模板匹配：截图 → 匹配所有模板 → 返回结果 + 耗时"""
        import time as _time

        if not _coordinator:
            return {"ok": False, "error": "请先启动"}

        ctrl = _coordinator.controllers.get(instance_index)
        if not ctrl:
            return {"ok": False, "error": "实例不存在"}

        t0 = _time.time()
        img = await ctrl.screenshot()
        screenshot_ms = (_time.time() - t0) * 1000

        if img is None:
            return {"ok": False, "error": "截图失败"}

        agent = next(iter(_coordinator.agents.values()))
        t1 = _time.time()
        matches = agent.pipeline.template.match_all(img, category)
        match_ms = (_time.time() - t1) * 1000

        return {
            "ok": True,
            "screenshot_ms": round(screenshot_ms, 1),
            "match_ms": round(match_ms, 1),
            "matched_count": len(matches),
            "total_templates": len(agent.pipeline.template.templates),
            "results": [
                {"name": m.template_name, "confidence": round(m.confidence, 3),
                 "x": m.x, "y": m.y, "scale": m.scale}
                for m in matches
            ],
        }

    @app.post("/api/debug/test-pipeline")
    async def test_full_pipeline(instance_index: int = 0):
        """
        完整管道测试：截图 → 模板+OCR+LLM → 综合报告
        测所有识别层的延迟和结果
        """
        import time as _time

        if not _coordinator:
            return {"ok": False, "error": "请先启动"}

        ctrl = _coordinator.controllers.get(instance_index)
        if not ctrl:
            return {"ok": False, "error": "实例不存在"}

        # 截图
        t0 = _time.time()
        img = await ctrl.screenshot()
        screenshot_ms = (_time.time() - t0) * 1000

        if img is None:
            return {"ok": False, "error": "截图失败"}

        agent = next(iter(_coordinator.agents.values()))

        # 模板匹配
        t1 = _time.time()
        matches = agent.pipeline.template.match_all(img)
        template_ms = (_time.time() - t1) * 1000

        # OCR
        t2 = _time.time()
        ocr_resp = agent.pipeline.ocr.recognize(img)
        ocr_ms = (_time.time() - t2) * 1000

        # LLM 状态分析
        t3 = _time.time()
        llm_state = await agent.pipeline.llm.analyze_state(img)
        llm_ms = (_time.time() - t3) * 1000

        # LLM 弹窗检测
        t4 = _time.time()
        llm_popup = await agent.pipeline.llm.detect_popup(img)
        llm_popup_ms = (_time.time() - t4) * 1000

        return {
            "ok": True,
            "latency": {
                "screenshot_ms": round(screenshot_ms, 1),
                "template_ms": round(template_ms, 1),
                "ocr_ms": round(ocr_ms, 1),
                "llm_state_ms": round(llm_ms, 1),
                "llm_popup_ms": round(llm_popup_ms, 1),
                "total_ms": round(screenshot_ms + template_ms + ocr_ms + llm_ms + llm_popup_ms, 1),
            },
            "template": {
                "matched": len(matches),
                "total": len(agent.pipeline.template.templates),
            },
            "ocr": {
                "text_count": len(ocr_resp.results),
                "full_text": ocr_resp.full_text[:200],
            },
            "llm_state": llm_state,
            "llm_popup": llm_popup,
        }

    @app.post("/api/debug/step")
    async def step_instance(instance_index: int, trigger: str):
        """
        单步调试：手动触发状态机转换
        例如 trigger="login_ok" 手动推进到下一步
        """
        if not _coordinator:
            return {"ok": False, "error": "请先启动"}

        agent = _coordinator.agents.get(instance_index)
        if not agent:
            return {"ok": False, "error": "实例不存在"}

        current = agent.state.value
        available = agent.fsm.get_available_triggers()

        if trigger not in available:
            return {
                "ok": False,
                "error": f"当前状态 {current} 不支持 {trigger}",
                "available_triggers": available,
            }

        trigger_fn = getattr(agent.fsm, trigger)
        trigger_fn()

        return {
            "ok": True,
            "old_state": current,
            "new_state": agent.state.value,
            "available_triggers": agent.fsm.get_available_triggers(),
        }

    @app.get("/api/debug/state")
    async def debug_state():
        """获取所有实例的详细调试信息"""
        if not _coordinator:
            return {"running": False}

        instances = {}
        for idx, agent in _coordinator.agents.items():
            instances[idx] = {
                "index": idx,
                "group": agent.info.group.value,
                "role": agent.info.role.value,
                "state": agent.state.value,
                "state_duration": round(agent.fsm.state_duration, 1),
                "available_triggers": agent.fsm.get_available_triggers(),
                "is_error": agent.fsm.is_error_state(),
                "is_terminal": agent.fsm.is_terminal_state(),
                "error_msg": agent.info.error_msg,
                "nickname": agent.info.nickname,
                "game_id": agent.info.game_id,
            }

        return {
            "running": True,
            "paused": _coordinator._paused,
            "instances": instances,
            "teams": {
                g: {
                    "captain": t.captain_index,
                    "members": t.member_indices,
                    "qr_url": t.qr_code_url[:50] if t.qr_code_url else "",
                }
                for g, t in _coordinator.teams.items()
            },
            "stats": _coordinator._get_stats_dict(),
            "cache_stats": {},
        }

    # =====================
    # 远程诊断接口（Claude/curl 友好）
    # =====================

    @app.get("/api/diagnostic/snapshot")
    async def diagnostic_snapshot(include_screenshots: bool = True):
        """
        一键诊断快照：状态 + 日志 + 所有实例截图(base64) + 错误
        让远程客户端 (curl) 一个请求看清当前所有情况
        """
        from .diagnostic import get_diagnostic
        diag = get_diagnostic()
        snap = await diag.take_snapshot(_coordinator)
        if not include_screenshots:
            snap.pop("screenshots_b64", None)
        return snap

    @app.get("/api/diagnostic/logs")
    async def diagnostic_logs(
        limit: int = 200,
        instance_index: Optional[int] = None,
        level: Optional[str] = None,
    ):
        """获取最近日志（可过滤实例和级别）"""
        from .diagnostic import get_diagnostic
        diag = get_diagnostic()
        return {
            "session_id": diag.session_id,
            "log_file": diag.log_file,
            "logs": diag.get_recent_logs(limit, instance_index, level),
        }

    @app.get("/api/diagnostic/errors")
    async def diagnostic_errors(limit: int = 50):
        """获取最近的错误和警告"""
        from .diagnostic import get_diagnostic
        diag = get_diagnostic()
        return {"errors": diag.get_recent_errors(limit)}

    @app.get("/api/diagnostic/screenshots")
    async def list_archived_screenshots(
        limit: int = 50,
        instance_index: Optional[int] = None,
    ):
        """列出归档的截图文件"""
        from .diagnostic import get_diagnostic
        diag = get_diagnostic()
        return {"screenshots": diag.list_screenshots(limit, instance_index)}

    @app.get("/api/diagnostic/screenshot/{filename}")
    async def get_archived_screenshot(filename: str):
        """下载归档的截图"""
        from .diagnostic import get_diagnostic
        diag = get_diagnostic()
        path = diag.get_screenshot_path(filename)
        if not path:
            return Response(status_code=404)
        return FileResponse(path, media_type="image/jpeg")

    @app.post("/api/diagnostic/archive-now")
    async def archive_screenshots_now(label: str = "manual"):
        """立即归档所有实例的当前截图"""
        from .diagnostic import get_diagnostic
        diag = get_diagnostic()

        if not _coordinator:
            return {"ok": False, "error": "未运行"}

        archived = []
        for idx, ctrl in _coordinator.controllers.items():
            img = await ctrl.screenshot()
            if img is not None:
                fname = diag.archive_screenshot(img, idx, label)
                if fname:
                    archived.append(fname)

        return {"ok": True, "count": len(archived), "files": archived}

    @app.get("/api/diagnostic/health")
    async def health_check():
        """简单健康检查（让远程探活用）"""
        from .diagnostic import get_diagnostic
        diag = get_diagnostic()
        return {
            "ok": True,
            "session_id": diag.session_id,
            "uptime": round(time.time() - diag.session_start, 1),
            "running": _coordinator is not None,
            "paused": _coordinator._paused if _coordinator else None,
            "instance_count": len(_coordinator.agents) if _coordinator else 0,
        }

    # =====================
    # WebSocket
    # =====================

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        await ws_manager.connect(ws)
        try:
            # 发送当前状态快照
            if _coordinator:
                await ws.send_json({
                    "type": "snapshot",
                    "instances": _coordinator.get_all_states(),
                    "stats": _coordinator._get_stats_dict(),
                })

            # 保持连接，接收客户端心跳
            while True:
                data = await ws.receive_text()
                if data == "ping":
                    await ws.send_text("pong")
        except WebSocketDisconnect:
            ws_manager.disconnect(ws)


# =====================
# 回调 → WebSocket 广播
# =====================

def _on_log(entry: LogEntry):
    """日志回调 → 广播 + 持久化到 JSONL"""
    from .diagnostic import get_diagnostic
    log_dict = entry.to_dict()

    # 持久化
    get_diagnostic().write_log(log_dict)

    # WebSocket 广播
    asyncio.ensure_future(ws_manager.broadcast({
        "type": "log",
        "data": log_dict,
    }))


def _on_state_change(instance_index: int, old: str, new: str):
    """状态变化回调 → 广播 + 写日志"""
    from .diagnostic import get_diagnostic
    state_log = {
        "timestamp": time.time(),
        "instance": instance_index,
        "level": "info",
        "message": f"状态变化: {old} → {new}",
        "state": new,
        "type": "state_change",
    }
    get_diagnostic().write_log(state_log)

    msg = {
        "type": "state_change",
        "data": {"instance": instance_index, "old": old, "new": new},
    }
    asyncio.ensure_future(ws_manager.broadcast(msg))

    # 同时广播完整状态快照
    if _coordinator:
        asyncio.ensure_future(ws_manager.broadcast({
            "type": "snapshot",
            "instances": _coordinator.get_all_states(),
            "stats": _coordinator._get_stats_dict(),
        }))


def _on_stats_update(stats: dict):
    """统计更新回调 → 广播"""
    asyncio.ensure_future(ws_manager.broadcast({
        "type": "stats",
        "data": stats,
    }))


# =====================
# 辅助函数
# =====================

async def _stop_coordinator():
    global _coordinator, _coordinator_task
    if _coordinator:
        await _coordinator.stop()
    if _coordinator_task and not _coordinator_task.done():
        _coordinator_task.cancel()
        try:
            await _coordinator_task
        except asyncio.CancelledError:
            pass
    _coordinator = None
    _coordinator_task = None


def _get_template_tool():
    """获取模板工具实例"""
    if not _coordinator or not _coordinator.agents:
        return None
    # 取第一个 agent 的 pipeline 中的 matcher
    first_agent = next(iter(_coordinator.agents.values()))
    matcher = first_agent.pipeline.template
    from .tools.template_tool import TemplateTool
    return TemplateTool(matcher)

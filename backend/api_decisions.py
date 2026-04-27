"""
/api/decisions/* + /api/sessions/* — 决策档案 / 历史会话查询.

从 backend/debug_server.py 迁出, 挂到主 8900 上, 让中控台前端走单端口.
旧 8901 路由保留过渡期 1 周, 与此并存.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter()


def _safe_id(s: str) -> str:
    """防路径穿越."""
    return re.sub(r"[^A-Za-z0-9_\-./]", "_", str(s))[:200]


def _safe_name(s: str) -> bool:
    """文件名校验, 不允许 / \\ .."""
    return not ("/" in s or "\\" in s or ".." in s or s == "")


def _resolve_decision_dir(decision_id: str, session: str = "") -> Optional[Path]:
    """决策目录定位. session 空则用当前 session."""
    from .automation.decision_log import get_recorder
    rec = get_recorder()
    if session:
        root = rec._logs_root()
        if root is None:
            return None
        return root / session / "decisions" / decision_id
    root = rec.root()
    if root is None:
        return None
    return root / decision_id


# ─── /api/sessions ───


@router.get("/api/sessions")
async def api_sessions():
    """列出所有有决策记录的 session (含历史)."""
    try:
        from .automation.decision_log import get_recorder
        rec = get_recorder()
        sessions = rec.list_sessions()
        cur = ""
        try:
            r = rec.root()
            if r is not None:
                cur = r.parent.name
        except Exception:
            cur = ""
        return {"sessions": sessions, "current_session": cur}
    except Exception as e:
        logger.warning(f"api_sessions err: {e}")
        return {"sessions": [], "current_session": "", "error": str(e)}


# ─── /api/decisions ───


@router.get("/api/decisions")
async def api_decisions(limit: int = 200, instance: int = -1, session: str = ""):
    """
    决策列表.
      session 空 → 当前 session 内存索引 (最新最快)
      session 有值 → 扫磁盘 logs/{session}/decisions/
    """
    try:
        from .automation.decision_log import get_recorder
        rec = get_recorder()
        inst_filter = instance if instance >= 0 else None
        if session:
            items = rec.list_session_decisions(session, limit=limit, instance=inst_filter)
            return {
                "count": len(items),
                "items": items,
                "session": session,
                "enabled": rec.is_enabled(),
            }
        items = rec.list_recent(limit=limit, instance=inst_filter)
        cur_name = ""
        try:
            r = rec.root()
            if r is not None:
                cur_name = r.parent.name
        except Exception:
            pass
        return {
            "count": len(items),
            "items": items,
            "session": cur_name,
            "enabled": rec.is_enabled(),
        }
    except Exception as e:
        logger.warning(f"api_decisions err: {e}")
        return {"count": 0, "items": [], "session": "", "enabled": False, "error": str(e)}


# ─── /api/decision/{id}/data ───


@router.get("/api/decision/{decision_id}/data")
async def api_decision_data(decision_id: str, session: str = ""):
    """单条决策的完整 JSON."""
    p = _resolve_decision_dir(_safe_id(decision_id), session)
    if p is None:
        raise HTTPException(404, "no active session")
    json_p = p / "decision.json"
    if not json_p.is_file():
        # 兜底: 尝试历史 session
        if not session:
            try:
                from .automation.decision_log import get_recorder
                rec = get_recorder()
                sessions = rec.list_sessions()
                for s in sessions:
                    p2 = _resolve_decision_dir(_safe_id(decision_id), s["session"])
                    if p2 and (p2 / "decision.json").is_file():
                        return JSONResponse(
                            json.loads((p2 / "decision.json").read_text(encoding="utf-8"))
                        )
            except Exception:
                pass
        raise HTTPException(404, "decision not found")
    try:
        return JSONResponse(json.loads(json_p.read_text(encoding="utf-8")))
    except Exception as e:
        raise HTTPException(500, str(e))


# ─── /api/decision/{id}/image/{file} ───


@router.get("/api/decision/{decision_id}/image/{filename}")
async def api_decision_image(decision_id: str, filename: str, session: str = ""):
    """决策目录下任意图片 (input.jpg / yolo_annot.jpg / tmpl_*.png ...)."""
    if not _safe_name(filename):
        raise HTTPException(400, "invalid filename")
    d_dir = _resolve_decision_dir(_safe_id(decision_id), session)
    if d_dir is None:
        raise HTTPException(404)
    p = d_dir / filename
    if not p.is_file():
        raise HTTPException(404, f"image not found: {filename}")
    media_type = "image/png" if p.suffix == ".png" else "image/jpeg"
    return FileResponse(p, media_type=media_type)

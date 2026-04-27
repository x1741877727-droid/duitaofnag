"""
/api/memory/* — 记忆库 (FrameMemory L1) 浏览 / 操作.

记录: phash + action_xy + 命中/失败计数 + 当时的截图 (含红圈点击点).
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

logger = logging.getLogger(__name__)

router = APIRouter()


_memory_inst = None


def _get_memory():
    """单例: 跟主 runner 共享同一 db (user_data_dir/memory/dismiss_popups.db)."""
    global _memory_inst
    if _memory_inst is not None:
        return _memory_inst
    try:
        from .automation.memory_l1 import FrameMemory
        from .automation.user_paths import user_data_dir
        db_path = user_data_dir() / "memory" / "dismiss_popups.db"
        _memory_inst = FrameMemory(db_path)
    except Exception as e:
        logger.warning(f"[api_memory] init err: {e}")
        _memory_inst = None
    return _memory_inst


@router.get("/api/memory/stats")
async def memory_stats():
    mem = _get_memory()
    if mem is None:
        return {"available": False, "error": "memory db 不可用"}
    return {"available": True, **mem.stats()}


@router.get("/api/memory/list")
async def memory_list(target: str = "", limit: int = 500):
    """列出记忆条目 (默认按 last_seen 倒序)."""
    mem = _get_memory()
    if mem is None:
        return {"items": [], "available": False}
    items = mem.list_all(target=target, limit=limit)
    # target 频次统计
    targets: dict[str, int] = {}
    for it in items:
        targets[it["target_name"]] = targets.get(it["target_name"], 0) + 1
    return {
        "items": items,
        "count": len(items),
        "targets": [{"name": k, "count": v} for k, v in sorted(targets.items())],
        "available": True,
    }


@router.get("/api/memory/{rid}")
async def memory_detail(rid: int):
    mem = _get_memory()
    if mem is None:
        raise HTTPException(503, "memory db 不可用")
    rec = mem.get_by_id(int(rid))
    if rec is None:
        raise HTTPException(404, "记录不存在")
    rec["similar"] = mem.find_similar(int(rid), max_dist=5)
    return rec


@router.get("/api/memory/{rid}/snapshot")
async def memory_snapshot(rid: int):
    """记录时的实际帧 (已画红圈点击位置)."""
    mem = _get_memory()
    if mem is None:
        raise HTTPException(503, "memory db 不可用")
    p = mem.snapshot_path(int(rid))
    if p is None:
        raise HTTPException(404, "无快照 (旧记录未存; 新写入的会有)")
    return FileResponse(p, media_type="image/jpeg")


@router.delete("/api/memory/{rid}")
async def memory_delete(rid: int):
    mem = _get_memory()
    if mem is None:
        raise HTTPException(503)
    ok = mem.delete_by_id(int(rid))
    if not ok:
        raise HTTPException(404, "记录不存在")
    return {"ok": True, "deleted": rid}


@router.post("/api/memory/{rid}/mark_fail")
async def memory_mark_fail(rid: int):
    """前端 '这条点错了' 按钮: 失败计数 +1.
    达到 fail >= 5 且 fail > succ 时, FrameMemory.query 会自动忽略这条 (置信度账本)."""
    mem = _get_memory()
    if mem is None:
        raise HTTPException(503)
    rec = mem.mark_fail(int(rid))
    if rec is None:
        raise HTTPException(404)
    return {"ok": True, "record": rec}

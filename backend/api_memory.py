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
    """单例: 跟主 runner 共享同一 FrameMemory (memory_l1.get_shared_memory).
    必须用 shared 而不是自己 new, 否则蓄水池 / LRU / BKTree 各自独立, 学习记不住."""
    global _memory_inst
    if _memory_inst is not None:
        return _memory_inst
    try:
        from .automation.memory_l1 import get_shared_memory
        from .automation.user_paths import user_data_dir
        db_path = user_data_dir() / "memory" / "dismiss_popups.db"
        _memory_inst = get_shared_memory(db_path)
    except Exception as e:
        logger.warning(f"[api_memory] init err: {e}")
        _memory_inst = None
    return _memory_inst


@router.get("/api/memory/stats")
async def memory_stats():
    mem = _get_memory()
    if mem is None:
        return {"available": False, "error": "memory db 不可用"}
    s = mem.stats()
    s["pending"] = mem.pending_detail()
    return {"available": True, **s}


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


# ──────── 蓄水池 (pending) 浏览 ────────

@router.get("/api/memory/pending/list")
async def memory_pending_list(target: str = ""):
    """蓄水池里"待 commit"的全部条目, 含每 sample 详情 (坐标 / has_snapshot)."""
    mem = _get_memory()
    if mem is None:
        return {"items": [], "available": False}
    items = mem.pending_detail(target=target)
    return {"items": items, "count": len(items), "available": True}


@router.get("/api/memory/pending/{key}/sample/{idx}")
async def memory_pending_sample(key: str, idx: int):
    """单张 pending 样本快照 (带红圈点击点)."""
    mem = _get_memory()
    if mem is None:
        raise HTTPException(503)
    p = mem.pending_snapshot_path(key, int(idx))
    if p is None:
        raise HTTPException(404, "样本不存在或快照已清")
    return FileResponse(p, media_type="image/jpeg")


@router.post("/api/memory/pending/{key}/discard")
async def memory_pending_discard(key: str):
    """手动丢弃一条 pending (清快照 + 移出蓄水池)."""
    mem = _get_memory()
    if mem is None:
        raise HTTPException(503)
    ok = mem.discard_pending(key)
    if not ok:
        raise HTTPException(404, "key 不存在")
    return {"ok": True, "key": key}


@router.post("/api/memory/dedup")
async def memory_dedup():
    """一键合并已入库的重复条目.
    判据: 同 target + 坐标差 <30px + (phash≤12 或 anchor 距≤6).
    场景: remember() 老逻辑 phash<3 太严格, 同位置同弹窗只要稍变就被当新条目入库,
    导致同 (target, xy) 出现多条记录 (例: dismiss_popups (899,53) 出现 2 条).
    本接口扫一遍现有 frame_action, 把同条按新判据合并 hit/success/fail 计数."""
    import asyncio as _aio
    mem = _get_memory()
    if mem is None:
        raise HTTPException(503)
    n = await _aio.to_thread(mem.dedup)
    return {"ok": True, "merged": int(n)}

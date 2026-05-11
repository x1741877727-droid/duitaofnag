"""
/api/perf/* — 硬件性能优化向导 backend.

3 个 endpoint:
  GET  /api/perf/status                    → {optimized, signature_match, last_applied_at}
  GET  /api/perf/detect                    → 硬件 + plan (不真改, 给 modal 显示)
  POST /api/perf/apply                     → 启 task, 返 task_id
  GET  /api/perf/apply/{task_id}           → poll 进度 + 结果

设计文档: docs/PERF_TUNING.md
实现: backend/automation/perf_optimizer.py
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import time
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .automation import perf_optimizer as po

logger = logging.getLogger(__name__)
router = APIRouter()

# ─────────────── 后台 task 表 ───────────────

# task_id → {status, progress[], result, started_at, finished_at}
_TASKS: dict[str, dict] = {}
_TASKS_TTL_S = 3600
_TASKS_MAX = 50


def _gc_old_tasks():
    if len(_TASKS) <= _TASKS_MAX:
        # 还按 ttl 删
        now = time.time()
        for tid in list(_TASKS):
            t = _TASKS[tid]
            if t.get("status") == "done" and (now - t.get("created_at", now)) > _TASKS_TTL_S:
                _TASKS.pop(tid, None)
        return
    # 超量删最早
    sorted_tids = sorted(_TASKS, key=lambda x: _TASKS[x].get("created_at", 0))
    for tid in sorted_tids[: len(sorted_tids) - _TASKS_MAX]:
        _TASKS.pop(tid, None)


# ─────────────── Models ───────────────


class ApplyReq(BaseModel):
    target_count: int = po.DEFAULT_TARGET_COUNT


# ─────────────── Endpoints ───────────────


@router.get("/api/perf/status")
async def status():
    """前端首次进中控台调一下, 决定是否浮 modal."""
    hw = await asyncio.to_thread(po.detect_hardware)
    optimized, sig_state = po.is_optimized(hw)
    state = po.load_state()
    return {
        "optimized": optimized,
        "signature_state": sig_state,                # None / "matched" / "changed"
        "last_applied_at": (state or {}).get("applied_at") if state else None,
        "current_signature": po._hw_signature(hw),
    }


@router.get("/api/perf/detect")
async def detect(target_count: int = po.DEFAULT_TARGET_COUNT):
    """探测硬件 + 给计划. 不真改."""
    hw = await asyncio.to_thread(po.detect_hardware)
    plan = po.compute_plan(hw, target_count=target_count)
    return {
        "hardware": _asdict_safe(hw),
        "plan": _asdict_safe(plan),
    }


@router.post("/api/perf/apply")
async def apply(req: ApplyReq):
    """开始优化. 返 task_id, 前端轮询进度."""
    _gc_old_tasks()
    task_id = secrets.token_urlsafe(8)
    _TASKS[task_id] = {
        "status": "running",
        "created_at": time.time(),
        "finished_at": None,
        "progress": [],          # [{step_id, message, ts}]
        "result": None,
        "target_count": req.target_count,
    }
    asyncio.create_task(_run_apply(task_id, req.target_count))
    return {"ok": True, "task_id": task_id, "status": "running"}


@router.get("/api/perf/apply/{task_id}")
async def apply_status(task_id: str):
    t = _TASKS.get(task_id)
    if not t:
        raise HTTPException(404, f"task_id 不存在或已过期: {task_id}")
    return {
        "task_id": task_id,
        "status": t["status"],
        "progress": t["progress"][-50:],
        "result": t["result"],
        "elapsed_s": round(time.time() - t["created_at"], 1),
        "estimate": t.get("estimate"),  # {total_steps, est_seconds, will_run, will_skip}
    }


@router.get("/api/perf/estimate")
async def estimate(target_count: int = po.DEFAULT_TARGET_COUNT):
    """估算 apply 总步数 + 耗时, 给前端 step 3 显示 'apply 会做什么'."""
    hw = await asyncio.to_thread(po.detect_hardware)
    plan = po.compute_plan(hw, target_count=target_count)
    audit = await asyncio.to_thread(po.audit_optimization_state, hw, target_count, plan)
    return po.estimate_apply_steps(plan, audit)


# ─────────────── 内部 ───────────────


async def _run_apply(task_id: str, target_count: int):
    t = _TASKS[task_id]
    try:
        hw = await asyncio.to_thread(po.detect_hardware)
        plan = po.compute_plan(hw, target_count=target_count)
        # 增量模式: 先 audit 拿当前状态, apply 跳过 applied 的步骤
        audit = await asyncio.to_thread(po.audit_optimization_state, hw, target_count, plan)
        # 估算总步数, 让前端进度条好看
        est = po.estimate_apply_steps(plan, audit)
        t["estimate"] = est

        async def on_step(sid: str, msg: str):
            t["progress"].append({
                "step_id": sid, "message": msg, "ts": time.time(),
            })

        result = await po.apply_plan(hw, plan, on_step=on_step, audit=audit)
        if result.success:
            await asyncio.to_thread(po.save_state, hw, plan, result)
        t["result"] = {
            "success": result.success,
            "error": result.error,
            "duration_s": result.duration_s,
            "changes": [_asdict_safe(c) for c in result.changes],
            "skipped": result.skipped,
            "hardware": _asdict_safe(hw),
            "plan": _asdict_safe(plan),
        }
    except Exception as e:
        logger.error(f"[perf apply] task {task_id} 异常: {e}", exc_info=True)
        t["result"] = {"success": False, "error": str(e)}
    finally:
        t["status"] = "done"
        t["finished_at"] = time.time()


def _asdict_safe(obj):
    """dataclass → dict, 兼容 list/dict 嵌套."""
    from dataclasses import asdict, is_dataclass
    if is_dataclass(obj):
        return asdict(obj)
    return obj


# ─────────────── Audit (增量补全) ───────────────


@router.get("/api/perf/audit")
async def audit(target_count: int = po.DEFAULT_TARGET_COUNT):
    """检查 LDPlayer 当前实际配置 vs 推荐 plan.

    返回:
      - applied: 已设置过的 (绿勾, 不动)
      - missing: 缺的 (红色, 一键补)
      - drift: 偏离 (黄色, 询问修复)
      - below_recommended: 已设但低于推荐 (灰色, 硬件限制)
      - needs_action: 是否要弹补全 modal
    """
    hw = await asyncio.to_thread(po.detect_hardware)
    plan = po.compute_plan(hw, target_count=target_count)
    report = await asyncio.to_thread(
        po.audit_optimization_state, hw, target_count, plan,
    )
    return {
        "hardware": _asdict_safe(hw),
        "expected_plan": _asdict_safe(plan),
        "report": {
            "applied": [_asdict_safe(x) for x in report.applied],
            "missing": [_asdict_safe(x) for x in report.missing],
            "drift": [_asdict_safe(x) for x in report.drift],
            "below_recommended": [_asdict_safe(x) for x in report.below_recommended],
            "instance_indexes_audited": report.instance_indexes_audited,
            "notes": report.notes,
            "needs_action": report.needs_action,
            "total_items": report.total_items,
        },
    }


# ─────────────── Runtime Profile (mode 切换) ───────────────


class ModeReq(BaseModel):
    mode: str           # "stable" / "balanced" / "speed"


@router.get("/api/runtime/profile")
async def get_runtime_profile():
    """当前运行时 profile (mode + 所有性能参数)."""
    from .automation import runtime_profile as rp
    p = rp.get_profile()
    return {
        "mode": p.mode,
        "valid_modes": list(rp.VALID_MODES),
        "profile": p.as_dict(),
    }


@router.post("/api/runtime/profile")
async def set_runtime_profile(req: ModeReq):
    """切 mode (持久化 + 通知 decision_log/vision_daemon reload)."""
    from .automation import runtime_profile as rp
    if req.mode not in rp.VALID_MODES:
        raise HTTPException(400, f"未知 mode: {req.mode}, 支持 {rp.VALID_MODES}")
    p = rp.set_mode(req.mode)
    return {"ok": True, "mode": p.mode, "profile": p.as_dict()}


@router.get("/api/runtime/preset/{mode}")
async def preview_preset(mode: str):
    """预览某个 mode 的参数预设, 不切换. wizard 给用户选 mode 时显示用."""
    from .automation import runtime_profile as rp
    if mode not in rp.VALID_MODES:
        raise HTTPException(400, f"未知 mode: {mode}")
    return {"mode": mode, "profile": rp.preset(mode).as_dict()}

"""
/api/runner/test_phase — 单实例 + 单阶段 dryrun.

不进入完整 P0→P1→P2→P3→P4 流程, 只跑一个 phase.
用于「我想测一下队长创建队伍这步是否正常」的场景, 无需启动整套.

要求: 主 runner 没在跑 (避免 ADB 抢占 / 状态污染).
"""
from __future__ import annotations

import asyncio
import logging
import os
import secrets
import time
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()

# 全局取消令牌 (前端按"停止测试" → POST /cancel → set v=True)
# single_runner._run_v3_phase 每轮检查这个 flag.
CANCEL_FLAG: dict = {"v": False}

# 后台任务表: task_id → {status, result, started_at, ...}
# 长任务 (P1/P2/P3a/P4 可能 30-90s) 走异步, POST 立即返 task_id, 前端轮询 GET status.
# 解决: trycloudflare 隧道单请求 ~30s 超时, 长任务被它 502 误报.
_TASKS: dict[str, dict] = {}
_TASKS_TTL_S = 3600                  # 任务结果保留 1h, 之后自动清
_TASKS_MAX = 500                     # 表内最多 500 条, 超了删最早的


# phase 名 → handler class
_HANDLER_MAP = {
    "P0": ("P0AcceleratorHandler", "加速器校验"),
    "P1": ("P1LaunchHandler", "启动游戏"),
    "P2": ("P2DismissHandler", "清弹窗"),
    "P3a": ("P3aTeamCreateHandler", "队长创建"),
    "P3b": ("P3bTeamJoinHandler", "队员加入"),
    "P4": ("P4MapSetupHandler", "选地图开打"),
}


class TestPhaseReq(BaseModel):
    instance: int
    phase: str               # P0 / P1 / P2 / P3a / P3b / P4
    role: str = "captain"    # captain / member (P3a 用 captain, P3b 用 member)
    scheme: Optional[str] = None  # P3b 用: 队长 P3a 跑完吐出来的 scheme URL, 队员加入用


@router.get("/api/runner/phases")
async def list_phases():
    """列出可测试的 phase + 中文名 + description + flow_steps (从代码读, 同步).

    返回示例:
      [{key: "P0", name: "加速器校验",
        description: "...", flow_steps: [...], max_rounds: 1}, ...]
    """
    out = []
    for key, (cls_name, fallback_name) in _HANDLER_MAP.items():
        try:
            from .automation import phases as _p
            cls = getattr(_p, cls_name)
            inst = cls()  # 拿类属性 (有些用 self 才能取, 但 description/flow_steps 都是类属性)
            out.append({
                "key": key,
                "name": getattr(inst, "name_cn", "") or fallback_name,
                "handler": cls_name,
                "description": getattr(inst, "description", "") or "",
                "flow_steps": list(getattr(inst, "flow_steps", []) or []),
                "max_rounds": int(getattr(inst, "max_rounds", 0) or 0),
                "round_interval_s": float(getattr(inst, "round_interval_s", 0) or 0),
            })
        except Exception as e:
            out.append({
                "key": key, "name": fallback_name, "handler": cls_name,
                "description": "", "flow_steps": [], "error": str(e),
            })
    return {"phases": out}


@router.get("/api/runner/phase_doc/{phase}")
async def phase_doc(phase: str):
    """单 phase 的完整文档 (description + flow_steps + 类源代码引用)."""
    if phase not in _HANDLER_MAP:
        raise HTTPException(404, f"未知 phase: {phase}")
    cls_name, fallback_name = _HANDLER_MAP[phase]
    from .automation import phases as _p
    cls = getattr(_p, cls_name)
    inst = cls()
    # 拿源文件路径 (用户想看代码可以追到这里)
    import inspect
    src_file = ""
    try:
        src_file = inspect.getsourcefile(cls) or ""
    except Exception:
        pass
    return {
        "key": phase,
        "name": getattr(inst, "name_cn", "") or fallback_name,
        "handler_class": cls_name,
        "description": getattr(inst, "description", ""),
        "flow_steps": list(getattr(inst, "flow_steps", []) or []),
        "max_rounds": int(getattr(inst, "max_rounds", 0) or 0),
        "round_interval_s": float(getattr(inst, "round_interval_s", 0) or 0),
        "source_file": src_file.replace("\\", "/"),
    }


async def _execute_test_phase(req: TestPhaseReq) -> dict:
    """实际跑 phase 的核心逻辑. 返回完整结果 dict.
    异常自己捕获, 写到 result.error 不抛."""
    phase = req.phase.strip()
    if phase not in _HANDLER_MAP:
        return {"ok": False, "error": f"未知 phase: {phase}", "phase": phase}

    from . import api as _api_mod
    svc = getattr(_api_mod, "_active_service", None)
    cfg = getattr(_api_mod, "_active_config", None)
    if svc is None or cfg is None:
        return {"ok": False, "error": "主 service 不可用", "phase": phase}

    if svc.running:
        return {
            "ok": False, "phase": phase,
            "error": "主 runner 在跑, 阶段测试要求主 runner 停 (避免 ADB 抢帧 / 状态打架)",
        }

    runner = svc._runners.get(int(req.instance))
    if runner is None:
        try:
            runner = await _build_test_runner(svc, cfg, int(req.instance), req.role)
        except Exception as e:
            logger.warning(f"[test_phase] build_runner err: {e}", exc_info=True)
            return {"ok": False, "phase": phase, "error": f"构造测试 runner 失败: {e}"}

    cls_name, _ = _HANDLER_MAP[phase]
    try:
        from .automation import phases as _p
        handler_cls = getattr(_p, cls_name)
    except Exception as e:
        return {"ok": False, "phase": phase, "error": f"加载 handler 失败: {e}"}

    handler = handler_cls()
    CANCEL_FLAG["v"] = False

    if req.scheme:
        try:
            ctx = runner._build_v3_ctx()
            ctx.game_scheme_url = req.scheme
        except Exception as e:
            logger.debug(f"[test_phase] 注入 scheme 失败: {e}")

    t0 = time.perf_counter()
    error: Optional[str] = None
    ok = False
    try:
        ok = await runner._run_v3_phase(handler, instance_idx=int(req.instance))
    except Exception as e:
        error = str(e)
        logger.warning(f"[test_phase] _run_v3_phase err: {e}", exc_info=True)

    dur_ms = round((time.perf_counter() - t0) * 1000, 2)

    scheme_out = ""
    try:
        ctx = runner._v3_ctx
        if ctx is not None:
            scheme_out = ctx.game_scheme_url or ""
    except Exception:
        pass

    try:
        from .automation.decision_log import get_recorder
        rec = get_recorder()
        sess_dir = rec.root()
        sess_name = sess_dir.parent.name if sess_dir is not None else ""
    except Exception:
        sess_name = ""

    return {
        "ok": bool(ok),
        "phase": phase,
        "phase_name": _HANDLER_MAP[phase][1],
        "instance": int(req.instance),
        "role": req.role,
        "duration_ms": dur_ms,
        "decision_session": sess_name,
        "fresh_runner": runner is not None and svc._runners.get(int(req.instance)) is None,
        "error": error,
        "game_scheme_url": scheme_out,
    }


def _gc_old_tasks() -> None:
    """清过期任务 (TTL + 表上限)."""
    now = time.time()
    expired = [tid for tid, t in _TASKS.items()
               if now - t.get("created_at", now) > _TASKS_TTL_S]
    for tid in expired:
        _TASKS.pop(tid, None)
    # 表上限: 删最早的
    if len(_TASKS) > _TASKS_MAX:
        sorted_ids = sorted(_TASKS.keys(), key=lambda t: _TASKS[t].get("created_at", 0))
        for tid in sorted_ids[: len(_TASKS) - _TASKS_MAX]:
            _TASKS.pop(tid, None)


async def _run_test_phase_bg(task_id: str, req: TestPhaseReq) -> None:
    """后台 task: 跑 phase, 把结果写回 _TASKS[task_id]. 异常不抛."""
    try:
        result = await _execute_test_phase(req)
    except Exception as e:
        result = {"ok": False, "error": f"后台异常: {e}", "phase": req.phase}
        logger.warning(f"[test_phase bg {task_id}] 异常: {e}", exc_info=True)
    t = _TASKS.get(task_id)
    if t is not None:
        t["status"] = "done"
        t["finished_at"] = time.time()
        t["result"] = result


@router.post("/api/runner/test_phase")
async def test_phase(req: TestPhaseReq):
    """单阶段 dryrun. **后台任务模型** — 立即返 task_id, 前端轮询 status.

    解决: trycloudflare 隧道单请求 ~30s 超时, 长 phase (P1/P2/P3a/P4 可能 30-90s)
    被中间层 502 截断. 改成后台 task 后单 HTTP 永远 < 100ms, cloudflared 满意.

    Response: { ok: True, task_id, status: "running" }
    前端: GET /api/runner/test_phase/{task_id} 轮询直到 status=="done"
    """
    _gc_old_tasks()
    task_id = secrets.token_urlsafe(8)
    _TASKS[task_id] = {
        "status": "running",
        "created_at": time.time(),
        "phase": req.phase,
        "instance": req.instance,
        "role": req.role,
    }
    asyncio.create_task(_run_test_phase_bg(task_id, req))
    return {"ok": True, "task_id": task_id, "status": "running"}


@router.get("/api/runner/test_phase/{task_id}")
async def test_phase_status(task_id: str):
    """查后台任务状态 / 结果. 单次 HTTP < 5ms (内存查表)."""
    t = _TASKS.get(task_id)
    if t is None:
        raise HTTPException(404, f"task_id 不存在或已过期: {task_id}")
    if t["status"] == "running":
        elapsed = (time.time() - t["created_at"]) * 1000
        return {
            "task_id": task_id,
            "status": "running",
            "elapsed_ms": round(elapsed, 1),
            "phase": t.get("phase", ""),
            "instance": t.get("instance", -1),
        }
    # done
    return {
        "task_id": task_id,
        "status": "done",
        "elapsed_ms": round((t.get("finished_at", time.time()) - t["created_at"]) * 1000, 1),
        **t.get("result", {}),
    }


@router.post("/api/runner/cancel")
async def cancel_test():
    """打断当前阶段测试 (set CANCEL_FLAG, _run_v3_phase 每轮检查)."""
    CANCEL_FLAG["v"] = True
    return {"ok": True, "msg": "已请求中止, 当前帧跑完即停"}


def stop_test_controllers(svc) -> int:
    """关闭所有 test_phase 注册的截图流 (dxhook / wgc / screenrecord).
    在 backend 退出 (shutdown / SIGINT) 时调, 避免 hook DLL / SHM handle 残留.
    返回关掉的实例数."""
    n = 0
    ctrls = getattr(svc, "_test_controllers", None) or {}
    for idx, adb in list(ctrls.items()):
        stream = getattr(adb, "_stream", None)
        if stream:
            try:
                stream.stop()
                n += 1
                logger.info(f"[test_phase] 已 stop 截图流 inst{idx}")
            except Exception as e:
                logger.debug(f"[test_phase] stop inst{idx} 异常: {e}")
        adb._stream = None
    if hasattr(svc, "_test_controllers"):
        svc._test_controllers.clear()
    # 同时清 _test_runners 缓存 (避免下次启动复用悬挂的 runner)
    if hasattr(svc, "_test_runners"):
        svc._test_runners.clear()
    return n


@router.post("/api/runner/test_new_session")
async def test_new_session():
    """强制开新 test session — 前端每次"开始一轮测试"前调一次.
    不调的话, 多次测试都会塞在同一 session (recorder 第一次 init 后 root 就有了,
    后续 _build_test_runner 看 root 不空就不重建). 用户痛点: 跑两次测试合在一起,
    决策档案分不开, 总耗时看起来比实际长."""
    import sys as _sys
    from datetime import datetime
    from pathlib import Path
    from .automation.decision_log import get_recorder

    if getattr(_sys, "frozen", False):
        proj_root = os.path.dirname(_sys.executable)
    else:
        proj_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    session_dir = Path(proj_root) / "logs" / f"test_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    session_dir.mkdir(parents=True, exist_ok=True)
    get_recorder().init(session_dir)   # init 会清 _index 内存索引
    logger.info(f"[test_phase] 新 session: {session_dir}")
    return {"ok": True, "session": session_dir.name}


async def _build_test_runner(svc, cfg, instance_idx: int, role: str):
    """临时构造 SingleInstanceRunner. 跟主 runner 隔离, 不进 svc._runners.

    缓存策略 (避免每次 test_phase 都重建):
      svc._test_runners[instance_idx] = (runner, role)
      role 一致 + adb._stream 还在 → 直接返
      否则 → 重建 (旧 stream 显式 stop)

    setup_minicap 是 sync subprocess (4-7s), **必须 to_thread** 防阻塞 asyncio loop.
    """
    import asyncio as _aio
    from .automation.adb_lite import ADBController
    from .automation.screen_matcher import ScreenMatcher
    from .automation.single_runner import SingleInstanceRunner

    # 缓存命中? — role 一致 + adb stream 还在 → 直接返
    cache = getattr(svc, "_test_runners", None)
    if cache is None:
        svc._test_runners = {}
        cache = svc._test_runners
    cached = cache.get(instance_idx)
    if cached is not None:
        cached_runner, cached_role = cached
        if cached_role == role and cached_runner.adb._stream is not None:
            logger.debug(f"[test_phase] inst{instance_idx} 复用 cached runner")
            return cached_runner

    adb_path = cfg.settings.adb_path or os.path.join(
        cfg.settings.ldplayer_path, "adb.exe")
    serial = f"emulator-{5554 + instance_idx * 2}"
    adb = ADBController(serial, adb_path)

    # 启 dxhook / wgc 截图流 — 包 to_thread 防 inject.exe subprocess wait 阻塞 asyncio
    try:
        ok = await _aio.to_thread(adb.setup_minicap)
        if ok:
            logger.info(f"[test_phase] {serial} 启 capture stream OK")
    except Exception as e:
        logger.warning(f"[test_phase] {serial} setup_minicap 异常: {e}")

    # 注册到 svc._test_controllers 用于 SIGINT / shutdown 时统一 stop()
    try:
        if not hasattr(svc, "_test_controllers"):
            svc._test_controllers = {}
        prev = svc._test_controllers.get(instance_idx)
        if prev is not None and prev is not adb and prev._stream is not None:
            try: prev._stream.stop()
            except Exception: pass
        svc._test_controllers[instance_idx] = adb
    except Exception:
        pass

    # ScreenMatcher 项目根 fixtures/templates
    import sys
    if getattr(sys, "frozen", False):
        proj_root = os.path.dirname(sys.executable)
    else:
        proj_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tmpl_dir = os.path.join(proj_root, "fixtures", "templates")
    if not os.path.isdir(tmpl_dir):
        tmpl_dir = os.path.join(proj_root, "_internal", "fixtures", "templates")
    matcher = ScreenMatcher(tmpl_dir)
    # load_all 是 sync I/O (10 个 PNG cv2.imread), 包 to_thread 不阻塞 loop
    await _aio.to_thread(matcher.load_all)

    # decision_log session
    try:
        from .automation.decision_log import get_recorder
        rec = get_recorder()
        if rec.root() is None:
            from datetime import datetime
            from pathlib import Path
            session_dir = Path(proj_root) / "logs" / f"test_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            session_dir.mkdir(parents=True, exist_ok=True)
            rec.init(session_dir)
            logger.info(f"[test_phase] 临时 session: {session_dir}")
    except Exception as e:
        logger.debug(f"[test_phase] init recorder err: {e}")

    runner = SingleInstanceRunner(
        adb=adb,
        matcher=matcher,
        role=role,
    )
    # 写入缓存
    svc._test_runners[instance_idx] = (runner, role)
    return runner

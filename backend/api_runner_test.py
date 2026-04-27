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
import time
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()


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


@router.post("/api/runner/test_phase")
async def test_phase(req: TestPhaseReq):
    """单阶段 dryrun.

    1. 主 runner 未跑 → 临时构造 SingleInstanceRunner + 跑该 phase
    2. 主 runner 在跑 → 拒绝 (避免 ADB 抢占)

    返回: {ok, phase, instance, decision_session, error?}
    """
    phase = req.phase.strip()
    if phase not in _HANDLER_MAP:
        raise HTTPException(400, f"未知 phase: {phase} (有效: {list(_HANDLER_MAP.keys())})")

    from . import api as _api_mod
    svc = getattr(_api_mod, "_active_service", None)
    cfg = getattr(_api_mod, "_active_config", None)
    if svc is None or cfg is None:
        raise HTTPException(503, "主 service 不可用")

    if svc.running:
        raise HTTPException(
            409,
            "主 runner 在跑, 阶段测试要求主 runner 停 (避免 ADB 抢帧 / 状态打架)",
        )

    # 拿/造 SingleInstanceRunner
    runner = svc._runners.get(int(req.instance))
    fresh = runner is None
    if fresh:
        try:
            runner = await _build_test_runner(svc, cfg, int(req.instance), req.role)
        except Exception as e:
            logger.warning(f"[test_phase] build_runner err: {e}", exc_info=True)
            raise HTTPException(500, f"构造测试 runner 失败: {e}")

    # 拿 handler
    cls_name, _ = _HANDLER_MAP[phase]
    try:
        from .automation import phases as _p
        handler_cls = getattr(_p, cls_name)
    except Exception as e:
        raise HTTPException(500, f"加载 handler 失败: {e}")

    handler = handler_cls()

    # 跑
    t0 = time.perf_counter()
    error: Optional[str] = None
    ok = False
    try:
        ok = await runner._run_v3_phase(handler)
    except Exception as e:
        error = str(e)
        logger.warning(f"[test_phase] _run_v3_phase err: {e}", exc_info=True)

    dur_ms = round((time.perf_counter() - t0) * 1000, 2)

    # 决策记录所在 session
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
        "fresh_runner": fresh,
        "error": error,
    }


async def _build_test_runner(svc, cfg, instance_idx: int, role: str):
    """临时构造 SingleInstanceRunner. 跟主 runner 隔离, 不进 svc._runners."""
    from .automation.adb_lite import ADBController
    from .automation.screen_matcher import ScreenMatcher
    from .automation.single_runner import SingleInstanceRunner

    adb_path = cfg.settings.adb_path or os.path.join(
        cfg.settings.ldplayer_path, "adb.exe")
    serial = f"emulator-{5554 + instance_idx * 2}"
    adb = ADBController(serial, adb_path)

    # ScreenMatcher 用项目根的 fixtures/templates
    import sys
    if getattr(sys, "frozen", False):
        proj_root = os.path.dirname(sys.executable)
    else:
        proj_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tmpl_dir = os.path.join(proj_root, "fixtures", "templates")
    if not os.path.isdir(tmpl_dir):
        tmpl_dir = os.path.join(proj_root, "_internal", "fixtures", "templates")
    matcher = ScreenMatcher(tmpl_dir)

    # decision_log session 还没初始化 → 用跟 runner_service 同算法的 logs/ 路径
    # (开发: <项目根>/logs/test_TS, exe: <exe目录>/logs/test_TS)
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

    return SingleInstanceRunner(
        adb=adb,
        matcher=matcher,
        role=role,
    )

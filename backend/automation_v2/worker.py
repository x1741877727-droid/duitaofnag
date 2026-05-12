"""Worker subprocess entrypoint — 1 个实例独立进程跑完整 v2 phase loop.

被 backend.runner_service 通过 `python -m backend.automation_v2.worker --idx 0 ...` spawn.
主进程跟 worker 通过 stdin/stdout JSON-line 通信:

Worker → Master (stdout):
  {"type": "state", "phase": "P2", "round": 5}
  {"type": "decision", "trace_id": "...", ...}    # 转发 decision_log
  {"type": "scheme_ready", "scheme": "pubgmhd://..."}    # captain P3a 完成
  {"type": "error", "msg": "..."}
  {"type": "done", "ok": true}

Master → Worker (stdin):
  {"type": "scheme", "scheme": "pubgmhd://..."}   # 主进程 broadcast 给 member
  {"type": "stop"}                                 # 停止

L2 子进程架构 (2026-05-12 day4-fix):
  替代旧的"主进程 asyncio 内跑 12 实例" 模型. 每实例独立进程,
  worker crash 不影响其他实例, 跨进程不抢 D3D12 锁 (DML deadlock 消失).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Optional


# 死锁诊断: 每步打 file trace, 跟 py-spy stack 对比
_TRACE_PATH = os.environ.get("GAMEBOT_WORKER_TRACE", "")
def _trace(msg: str) -> None:
    if not _TRACE_PATH:
        return
    try:
        with open(_TRACE_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{time.time():.3f}] pid={os.getpid()} {msg}\n")
    except Exception:
        pass

_trace("worker.py top imports done")


def _emit(msg: dict) -> None:
    """推一行 JSON 到 stdout (master 读)."""
    try:
        sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    except Exception:
        pass


def _emit_log(level: str, msg: str) -> None:
    _emit({"type": "log", "level": level, "msg": msg[:400]})


# 把 backend logger 也转发到 stdout (master 收集合并 log)
class _StdoutLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            _emit_log(record.levelname.lower(), msg)
        except Exception:
            pass


def _setup_logging(instance_idx: int) -> None:
    handler = _StdoutLogHandler()
    handler.setFormatter(logging.Formatter("[w%(name)s] %(message)s"))
    handler.setLevel(logging.INFO)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def _stdin_blocking_reader(state: dict) -> None:
    """threading 同步读 stdin (Windows asyncio.connect_read_pipe 不支持 stdin).

    在独立 thread 跑 sys.stdin.readline() 阻塞读, 解析 JSON 后写 state dict.
    主 coroutine 通过 state dict poll (轮询 state["game_scheme_url"]).
    """
    import threading
    while True:
        try:
            line = sys.stdin.readline()
            if not line:    # stdin closed
                state["stop_requested"] = True
                break
            line = line.strip()
            if not line:
                continue
            msg = json.loads(line)
        except Exception:
            continue
        mtype = msg.get("type", "")
        if mtype == "scheme":
            state["game_scheme_url"] = msg.get("scheme", "")
            _emit_log("info", "stdin: scheme 收到")
        elif mtype == "stop":
            state["stop_requested"] = True
            _emit_log("info", "stdin: stop 收到")
            break


def _start_stdin_thread(state: dict):
    """thread daemon=True 跑 _stdin_blocking_reader, 不阻塞主进程退出."""
    import threading
    t = threading.Thread(target=_stdin_blocking_reader, args=(state,),
                         daemon=True, name="stdin-reader")
    t.start()
    return t


def _build_components(instance_idx: int, role: str):
    """worker 内独立 init: adb / matcher / yolo / ocr.
    复用 v1 类 (代码不动), 但在这个 subprocess 里独立的 session."""
    _trace("_build_components: begin")
    from backend.config import config
    _trace("_build_components: config imported")
    config.load()
    _trace("_build_components: config.load() done")
    settings = config.settings

    # resolve adb path (跟 runner_service.py:274 同款)
    adb_path = settings.adb_path or os.path.join(settings.ldplayer_path, "adb.exe")

    # detect serial (instance_idx → emulator-555X)
    serial = f"emulator-{5554 + instance_idx * 2}"

    # ADBController
    _trace("_build_components: importing adb_lite (cv2/numpy)")
    from backend.automation.adb_lite import ADBController
    _trace("_build_components: adb_lite imported")
    raw_adb = ADBController(serial, adb_path)
    _trace("_build_components: ADBController constructed")

    # ScreenMatcher (shared template dir)
    from backend.automation.screen_matcher import ScreenMatcher
    template_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                                "fixtures", "templates")
    matcher = ScreenMatcher(template_dir)
    matcher.load_all()

    # v1 SingleInstanceRunner — 给 v2 bridge 用. worker 自己持有.
    from backend.automation.single_runner import SingleInstanceRunner
    v1_runner = SingleInstanceRunner(
        adb=raw_adb,
        matcher=matcher,
        role=role,
        on_phase_change=lambda phase: _emit({
            "type": "v1_phase_change",
            "phase": getattr(phase, "value", str(phase)),
        }),
        log_dir=str(Path(os.environ.get("GAMEBOT_SESSION_DIR", ".gocache")) / f"instance_{instance_idx}"),
    )
    return v1_runner, raw_adb


async def _run_worker(args) -> int:
    """Worker 主流程. 返 exit code (0=success)."""
    _trace("_run_worker: enter")
    state = {"stop_requested": False, "game_scheme_url": args.game_scheme or ""}

    # stdin reader (Windows: 必须 threading 同步读, asyncio.connect_read_pipe 不支持 stdin)
    _start_stdin_thread(state)
    _trace("_run_worker: stdin thread started")
    stdin_task = None    # 不再用 asyncio task

    # 加载组件
    try:
        _trace("_run_worker: calling _build_components")
        v1_runner, raw_adb = _build_components(args.idx, args.role)
        _trace("_run_worker: _build_components returned")
    except Exception as e:
        _emit({"type": "error", "msg": f"init err: {e}\n{traceback.format_exc()[:1000]}"})
        return 1

    # build v2 ctx + phases
    try:
        from backend.automation_v2.bridge import build_v2_ctx, V2_PHASE_TO_V1
        from backend.automation_v2.runner import SingleRunner as V2Runner
        from backend.automation_v2.phases import (
            P0Accel, P1Launch, P2Dismiss,
            P3aTeamCreate, P3bTeamJoin, P4MapSetup,
        )
        from backend.automation_v2.middleware.invite_dismiss import InviteDismissMiddleware
        from backend.automation_v2.middleware.crash_check import CrashCheckMiddleware
    except Exception as e:
        _emit({"type": "error", "msg": f"v2 import err: {e}"})
        return 1

    session_dir = Path(args.session_dir) if args.session_dir else Path(".gocache/worker_default")
    ctx, decision_log = build_v2_ctx(
        instance_idx=args.idx,
        role=args.role,
        v1_runner=v1_runner,
        session_dir=session_dir,
    )
    if state["game_scheme_url"]:
        ctx.game_scheme_url = state["game_scheme_url"]

    phases = {
        "P0": P0Accel(), "P1": P1Launch(), "P2": P2Dismiss(),
        "P3a": P3aTeamCreate(), "P3b": P3bTeamJoin(), "P4": P4MapSetup(),
    }
    phase_order = (["P0","P1","P2","P3a","P4"] if args.role == "captain"
                   else ["P0","P1","P2","P3b"])
    middlewares = [InviteDismissMiddleware(), CrashCheckMiddleware()]

    v2_runner = V2Runner(ctx, phases, middlewares=middlewares,
                         phase_order=phase_order)

    # on_phase_change → stdout JSON
    # 关键: captain 进 P4 时, P3a 已完成, ctx.game_scheme_url 已写好 → 立刻推 scheme_ready
    # (不等整个 run() 结束才推, 否则 member 在 P3b 等到 timeout)
    def _on_phase(phase_name: str):
        v1_label = V2_PHASE_TO_V1.get(phase_name, phase_name)
        _emit({"type": "state", "phase": phase_name, "v1_label": v1_label,
               "round": ctx.phase_round})
        # captain 跑完 P3a 进 P4 的瞬间 → scheme 已就绪 → 立即广播
        if args.role == "captain" and phase_name == "P4" and ctx.game_scheme_url:
            _emit({"type": "scheme_ready", "scheme": ctx.game_scheme_url})
    v2_runner.on_phase_change = _on_phase

    # member 等 scheme: 后台 watch state["game_scheme_url"], 同步到 ctx
    async def _sync_scheme():
        while not state["stop_requested"]:
            await asyncio.sleep(0.5)
            sch = state["game_scheme_url"]
            if sch and not ctx.game_scheme_url:
                ctx.game_scheme_url = sch
                _emit_log("info", f"scheme 同步到 ctx: {sch[:48]}")
    sync_task = asyncio.create_task(_sync_scheme()) if args.role == "member" else None

    _emit_log("info", f"worker idx={args.idx} role={args.role} 启动")

    # YOLO warmup (跑 1 次 dummy 推理预热 ONNX session)
    # 不 warmup 的话 R1 第一次 yolo 调用要 ~1 秒 cold start, popup→tap 慢.
    # warmup 后 R1 yolo ~30ms, R1 popup→tap 整体 1.5s → 0.6s.
    try:
        yolo = getattr(v1_runner, "yolo_dismisser", None)
        if yolo and hasattr(yolo, "warmup"):
            await asyncio.to_thread(yolo.warmup)
            _emit_log("info", "yolo warmup done")
    except Exception as e:
        _emit_log("warn", f"yolo warmup err: {e}")

    ok = False
    try:
        ok = await v2_runner.run()
    except asyncio.CancelledError:
        _emit_log("warn", "worker cancelled")
    except Exception as e:
        _emit({"type": "error", "msg": f"run() 抛: {e}\n{traceback.format_exc()[:800]}"})
    finally:
        if sync_task is not None and not sync_task.done():
            sync_task.cancel()
        try:
            decision_log.close()
        except Exception:
            pass

    # captain 完成 P3a → scheme_ready
    if ok and args.role == "captain" and ctx.game_scheme_url:
        _emit({"type": "scheme_ready", "scheme": ctx.game_scheme_url})

    _emit({"type": "done", "ok": ok})
    return 0 if ok else 1


def main():
    _trace("main(): start")
    ap = argparse.ArgumentParser(description="v2 worker subprocess")
    ap.add_argument("--idx", type=int, required=True, help="instance index")
    ap.add_argument("--role", required=True, choices=["captain", "member"])
    ap.add_argument("--group", default="A", help="组 (用于 scheme 路由)")
    ap.add_argument("--game-scheme", default="", help="member 用: captain 已建好的 scheme")
    ap.add_argument("--session-dir", default="", help="session log 目录")
    args = ap.parse_args()
    _trace(f"main(): args parsed idx={args.idx} role={args.role}")

    _setup_logging(args.idx)
    _trace("main(): logging setup done")
    try:
        _trace("main(): asyncio.run(_run_worker)")
        rc = asyncio.run(_run_worker(args))
    except KeyboardInterrupt:
        rc = 130
    except Exception as e:
        _emit({"type": "error", "msg": f"worker top err: {e}\n{traceback.format_exc()[:800]}"})
        rc = 1
    sys.exit(rc)


if __name__ == "__main__":
    main()

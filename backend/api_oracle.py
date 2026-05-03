"""
/api/oracle/* — 决策回放 + Oracle 标注 + 回归批跑

工作流:
  1. 用户在 Archive UI 看到错决策 → 点"标记错误", 框选正确位置
  2. POST /api/oracle 创建 oracle entry, 存到 fixtures/oracle/<id>.json
  3. 后续每次代码改动, GET /api/oracle/replay-all 跑全集, 看通过/回归

存储: fixtures/oracle/<oracle_id>.json (1 oracle 1 文件, git 友好)
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .replay import compare_oracle, replay_decision, reset_matcher_cache

logger = logging.getLogger(__name__)

router = APIRouter()


def _oracle_dir() -> Path:
    """fixtures/oracle/ 目录, 不存在则创建."""
    backend_dir = Path(__file__).resolve().parent
    target = backend_dir.parent / "fixtures" / "oracle"
    target.mkdir(parents=True, exist_ok=True)
    return target


# ─── Schemas ───────────────────────────────────────────────────────


class OracleCreate(BaseModel):
    decision_id: str
    session: str = ""
    correct: str = "tap"  # "tap" | "no_action" | "exit_phase"
    click_x: Optional[int] = None
    click_y: Optional[int] = None
    label: str = ""  # e.g. "close_x" / "btn_join"
    note: str = ""


# ─── Endpoints ─────────────────────────────────────────────────────


@router.post("/api/oracle")
async def create_oracle(req: OracleCreate):
    """新建 oracle: 引用一个历史决策 + 标注正确动作."""
    if not req.decision_id:
        raise HTTPException(400, "decision_id required")

    # 拉一次 replay 拿原决策快照 + 验证 decision 存在
    rep = await asyncio.to_thread(replay_decision, req.decision_id, req.session)
    if not rep.get("ok"):
        raise HTTPException(404, rep.get("error", "decision not found"))

    safe_did = req.decision_id.replace("/", "_").replace("\\", "_")[:60]
    oid = f"oracle_{int(time.time())}_{safe_did}"
    obj = {
        "id": oid,
        "created_at": time.time(),
        "source_decision_id": req.decision_id,
        "source_session": rep.get("session", req.session),
        "input_size": rep.get("input_size", {}),
        "annotation": {
            "correct": req.correct,
            "click_x": req.click_x,
            "click_y": req.click_y,
            "label": req.label,
            "note": req.note,
        },
        "original_action": rep.get("original", {}),
    }

    p = _oracle_dir() / f"{oid}.json"
    try:
        p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        raise HTTPException(500, f"写入失败: {e}")
    logger.info(f"[oracle] 新建 {oid} (correct={req.correct})")
    return obj


@router.get("/api/oracle")
async def list_oracles():
    """列所有 oracle, 按创建时间倒序."""
    out: list[dict] = []

    def _scan():
        items = []
        for p in _oracle_dir().glob("*.json"):
            try:
                items.append((p.stat().st_mtime, json.loads(p.read_text(encoding="utf-8"))))
            except Exception:
                pass
        items.sort(key=lambda x: x[0], reverse=True)
        return [it[1] for it in items]

    out = await asyncio.to_thread(_scan)
    return {"oracles": out, "total": len(out)}


@router.get("/api/oracle/{oracle_id}")
async def get_oracle(oracle_id: str):
    p = _oracle_dir() / f"{oracle_id}.json"
    if not p.is_file():
        raise HTTPException(404, "not found")
    return json.loads(p.read_text(encoding="utf-8"))


@router.delete("/api/oracle/{oracle_id}")
async def delete_oracle(oracle_id: str):
    p = _oracle_dir() / f"{oracle_id}.json"
    if not p.is_file():
        raise HTTPException(404, "not found")
    p.unlink()
    return {"ok": True, "deleted": oracle_id}


@router.post("/api/oracle/{oracle_id}/replay")
async def replay_one(oracle_id: str):
    """对单个 oracle 跑 replay + 比对."""
    p = _oracle_dir() / f"{oracle_id}.json"
    if not p.is_file():
        raise HTTPException(404, "not found")
    obj = json.loads(p.read_text(encoding="utf-8"))
    rep = await asyncio.to_thread(
        replay_decision,
        obj["source_decision_id"],
        obj.get("source_session", ""),
    )
    cmp = compare_oracle(obj, rep)
    return {"oracle": obj, "replay": rep, "comparison": cmp}


@router.post("/api/oracle/replay-all")
async def replay_all():
    """跑全部 oracle, 返回汇总. 用于代码改动后回归检查."""
    def _runs() -> list[dict]:
        out = []
        for p in sorted(_oracle_dir().glob("*.json")):
            try:
                obj = json.loads(p.read_text(encoding="utf-8"))
                rep = replay_decision(
                    obj["source_decision_id"],
                    obj.get("source_session", ""),
                )
                cmp = compare_oracle(obj, rep)
                out.append({
                    "id": obj["id"],
                    "decision_id": obj["source_decision_id"],
                    "session": obj.get("source_session", ""),
                    "annotation": obj.get("annotation", {}),
                    "original_action": obj.get("original_action", {}),
                    "match": cmp["match"],
                    "reason": cmp["reason"],
                    "current": cmp.get("current"),
                    "expected": cmp.get("expected"),
                    "distance_px": cmp.get("distance_px"),
                })
            except Exception as e:
                out.append({"id": p.stem, "match": "ERROR", "reason": str(e)})
        return out

    results = await asyncio.to_thread(_runs)
    pass_n = sum(1 for r in results if r.get("match") == "PASS")
    fail_n = len(results) - pass_n
    by_kind: dict[str, int] = {}
    for r in results:
        by_kind[r.get("match", "?")] = by_kind.get(r.get("match", "?"), 0) + 1
    return {
        "total": len(results),
        "passed": pass_n,
        "failed": fail_n,
        "by_kind": by_kind,
        "results": results,
        "ts": time.time(),
    }


@router.get("/api/oracle/replay/{decision_id}")
async def replay_arbitrary(decision_id: str, session: str = ""):
    """对任意 decision (无论是否 oracle) 跑当前代码识别, 看会怎么决策."""
    rep = await asyncio.to_thread(replay_decision, decision_id, session)
    if not rep.get("ok"):
        raise HTTPException(404, rep.get("error", "decision not found"))
    return rep


@router.post("/api/oracle/reload-templates")
async def reload_templates():
    """改了模板后调一次, 让 replay 重新加载 ScreenMatcher 模板缓存."""
    reset_matcher_cache()
    return {"ok": True}

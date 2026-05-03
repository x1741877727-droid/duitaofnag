"""
决策回放引擎 (Decision Replay).

输入: 历史 decision_id (来自 logs/<session>/decisions/<id>/).
做法: 加载该决策的 input.jpg + decision.json, 把图喂给**当前代码**的
       识别管线 (ScreenMatcher 模板匹配 + lobby 检测), 返回当前代码的决策
       和原决策的对比.
用途: 改完代码不重启 backend, 即可验证某条历史 bug 是否还会重现.
MVP: 仅模板匹配层. YOLO/OCR 接入留给后续 (它们成本/启动重, 单独评估).

API:
    replay_decision(decision_id, session_name="") -> dict
    compare_oracle(oracle: dict, replay_result: dict) -> dict
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

import cv2

logger = logging.getLogger(__name__)

# 像素阈值: oracle 期望 (x,y) 与当前代码 (x,y) 距离 ≤ 此值视为同一目标
ORACLE_PIXEL_TOLERANCE = 30


# ─── 全局 matcher 缓存 ──────────────────────────────────────────────
# replay 是只读, 多次调用共享同一个 ScreenMatcher 避免每次重加载模板.
_matcher_cache: Any = None


def _get_matcher():
    global _matcher_cache
    if _matcher_cache is not None:
        return _matcher_cache
    from .automation.screen_matcher import ScreenMatcher
    template_dir = _find_template_dir()
    m = ScreenMatcher(template_dir)
    n = m.load_all()
    logger.info(f"[replay] ScreenMatcher 加载 {n} 个模板 (template_dir={template_dir})")
    _matcher_cache = m
    return m


def reset_matcher_cache() -> None:
    """重新加载模板 (新增/修改模板后调用)."""
    global _matcher_cache
    _matcher_cache = None


def _find_template_dir() -> str:
    """跟 runner_service._resolve_template_dir 同算法找模板目录."""
    here = Path(__file__).resolve().parent  # backend/
    candidates = [
        here.parent / "fixtures" / "templates",
        Path.cwd() / "fixtures" / "templates",
        here / "fixtures" / "templates",
    ]
    for c in candidates:
        if c.is_dir():
            return str(c)
    raise RuntimeError(f"templates dir not found, tried: {candidates}")


def _logs_root() -> Optional[Path]:
    from .automation.decision_log import get_recorder
    rec = get_recorder()
    return rec._logs_root()


def _resolve_decision_dir(decision_id: str, session_name: str = "") -> tuple[Optional[Path], str]:
    root = _logs_root()
    if root is None or not root.is_dir():
        return None, ""
    if session_name:
        d = root / session_name / "decisions" / decision_id
        return (d if d.is_dir() else None), session_name
    # 扫所有 session
    for sess in root.iterdir():
        cand = sess / "decisions" / decision_id
        if cand.is_dir():
            return cand, sess.name
    return None, ""


def _hit_to_dict(hit) -> Optional[dict]:
    if hit is None:
        return None
    return {
        "name": hit.name,
        "conf": round(hit.confidence, 3),
        "cx": hit.cx, "cy": hit.cy,
        "w": hit.w, "h": hit.h,
    }


def replay_decision(decision_id: str, session_name: str = "") -> dict:
    """重放: 加载 input.jpg → 跑当前代码识别 → 返回完整 perceive 输出 + 原决策."""
    d_dir, session_name = _resolve_decision_dir(decision_id, session_name)
    if d_dir is None:
        return {"ok": False, "error": f"decision {decision_id} 找不到 (session={session_name or 'all'})"}

    input_jpg = d_dir / "input.jpg"
    if not input_jpg.is_file():
        return {"ok": False, "error": "input.jpg 缺失"}

    img = cv2.imread(str(input_jpg))
    if img is None:
        return {"ok": False, "error": "input.jpg 无法读取"}

    h, w = img.shape[:2]

    matcher = _get_matcher()

    # ── 主要决策入口 ───────────────────────────
    close_btn = matcher.find_close_button(img)
    action_btn = matcher.find_action_button(img)
    dialog_close = matcher.find_dialog_close(img)
    is_lobby = matcher.is_at_lobby(img)

    # ── 全模板扫描 (低阈值) — 帮助看潜在命中 ────────
    interesting_prefixes = ("close_x_", "btn_", "lobby_", "dialog", "template_dismiss")
    all_hits = []
    for name in matcher.template_names:
        if not any(name.startswith(p) for p in interesting_prefixes):
            continue
        try:
            hit = matcher.match_one(img, name, threshold=0.65)
        except Exception as e:
            logger.debug(f"[replay] match_one {name} 异常: {e}")
            continue
        if hit:
            all_hits.append(_hit_to_dict(hit))

    # ── 当前代码 P2 dismiss 优先级模拟: close_x > action_btn > nothing ──
    chosen = None
    if close_btn is not None:
        chosen = {
            "x": close_btn.cx, "y": close_btn.cy,
            "label": close_btn.name, "tier": "close_button",
            "conf": round(close_btn.confidence, 3),
        }
    elif action_btn is not None:
        chosen = {
            "x": action_btn.cx, "y": action_btn.cy,
            "label": action_btn.name, "tier": "action_button",
            "conf": round(action_btn.confidence, 3),
        }

    # ── 加载原决策 ───────────────────────────
    original: dict = {}
    decision_json = d_dir / "decision.json"
    if decision_json.is_file():
        try:
            d = json.loads(decision_json.read_text(encoding="utf-8"))
            t = d.get("tap") or {}
            original = {
                "x": t.get("x"), "y": t.get("y"),
                "label": (t.get("method") or t.get("target_class") or t.get("target_text") or ""),
                "outcome": d.get("outcome", ""),
                "phase": d.get("phase", ""),
                "round": d.get("round"),
                "instance": d.get("instance"),
                "verify_success": (d.get("verify") or {}).get("success"),
            }
        except Exception as e:
            logger.warning(f"[replay] decision.json 解析失败: {e}")

    return {
        "ok": True,
        "decision_id": decision_id,
        "session": session_name,
        "input_size": {"w": w, "h": h},
        "matchers": {
            "is_at_lobby": is_lobby,
            "find_close_button": _hit_to_dict(close_btn),
            "find_action_button": _hit_to_dict(action_btn),
            "find_dialog_close": _hit_to_dict(dialog_close),
        },
        "all_hits": all_hits,
        "current_chosen": chosen,
        "original": original,
    }


# ─── Oracle 比对 ───────────────────────────────────────────────────


def compare_oracle(oracle: dict, replay_result: dict) -> dict:
    """oracle 期望 vs replay 结果. 返回 {match, reason, current?, expected?}.

    match 取值:
      PASS                — 当前代码决策与 oracle 期望一致
      FAIL_NO_ACTION      — oracle 期望点击, 当前代码没选任何目标
      FAIL_OVER_ACTING    — oracle 期望不动作, 当前代码选了目标
      FAIL_WRONG_TARGET   — 都点击, 但位置 > 容差
      ERROR               — replay 失败
    """
    if not replay_result.get("ok"):
        return {"match": "ERROR", "reason": replay_result.get("error", "replay failed")}

    ann = oracle.get("annotation") or {}
    correct = ann.get("correct", "tap")
    chosen = replay_result.get("current_chosen")

    if correct == "no_action":
        if chosen is None:
            return {"match": "PASS", "reason": "no_action (代码也未选目标)"}
        return {
            "match": "FAIL_OVER_ACTING",
            "reason": f"oracle 期望不动作, 代码却选 {chosen['label']}@({chosen['x']},{chosen['y']})",
            "current": chosen,
        }

    # tap 类
    if chosen is None:
        return {"match": "FAIL_NO_ACTION", "reason": "oracle 期望点击, 代码未选任何目标"}

    expected_x = ann.get("click_x") or 0
    expected_y = ann.get("click_y") or 0
    dx = chosen["x"] - expected_x
    dy = chosen["y"] - expected_y
    dist = (dx * dx + dy * dy) ** 0.5

    if dist <= ORACLE_PIXEL_TOLERANCE:
        return {
            "match": "PASS",
            "reason": f"距期望 {dist:.0f}px (阈 {ORACLE_PIXEL_TOLERANCE})",
            "current": chosen,
            "distance_px": round(dist, 1),
        }
    return {
        "match": "FAIL_WRONG_TARGET",
        "reason": f"距期望 {dist:.0f}px (阈 {ORACLE_PIXEL_TOLERANCE})",
        "current": chosen,
        "expected": {"x": expected_x, "y": expected_y, "label": ann.get("label", "")},
        "distance_px": round(dist, 1),
    }

"""
/api/yolo/* — YOLO 推理 dryrun (中控台模版库 YOLO 测试用).

不影响主 runner 的 YOLO session — 直接调 yolo_detector.detect_buttons (单例).
"""
from __future__ import annotations

import base64
import logging
import os
import time
from typing import Any, Optional

import cv2
import numpy as np
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()


def _encode_jpeg_b64(img: np.ndarray, q: int = 70) -> str:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])
    if not ok:
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode("ascii")


def _decode_b64_image(s: str) -> Optional[np.ndarray]:
    try:
        if s.startswith("data:"):
            s = s.split(",", 1)[1]
        data = base64.b64decode(s)
        arr = np.frombuffer(data, dtype=np.uint8)
        return cv2.imdecode(arr, cv2.IMREAD_COLOR)
    except Exception:
        return None


def _load_screenshot_from_decision(decision_id: str, session: str = "") -> Optional[np.ndarray]:
    try:
        from .automation.decision_log import get_recorder
        rec = get_recorder()
        if session:
            root = rec._logs_root()
            if root is None:
                return None
            p = root / session / "decisions" / decision_id / "input.jpg"
        else:
            r = rec.root()
            if r is None:
                return None
            p = r / decision_id / "input.jpg"
        if not p.is_file():
            return None
        return cv2.imread(str(p))
    except Exception:
        return None


def _draw_dets(img: np.ndarray, dets) -> np.ndarray:
    annot = img.copy()
    palette = [
        (0, 200, 0), (0, 200, 220), (0, 100, 220),
        (220, 100, 0), (220, 0, 200), (100, 100, 220),
    ]
    for i, d in enumerate(dets):
        color = palette[i % len(palette)]
        x1, y1, x2, y2 = int(d.x1), int(d.y1), int(d.x2), int(d.y2)
        cv2.rectangle(annot, (x1, y1), (x2, y2), color, 2)
        label = f"{d.name} {d.score:.2f}"
        cv2.putText(annot, label, (x1, max(20, y1 - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        cv2.circle(annot, (cx, cy), 4, (0, 0, 220), -1)
    return annot


# ─── /api/yolo/info ───


@router.get("/api/yolo/info")
async def yolo_info():
    """报告当前 YOLO 模型加载状态 + 类名列表."""
    try:
        from .automation import yolo_detector as yd
        ok = yd.is_available()
        model_path = getattr(yd, "_DEFAULT_MODEL", "")
        classes = yd._class_names if hasattr(yd, "_class_names") else []
        return {
            "available": bool(ok),
            "model_path": str(model_path).replace("\\", "/"),
            "classes": list(classes),
            "input_size": int(getattr(yd, "_input_size", 640) or 640),
        }
    except Exception as e:
        return {"available": False, "model_path": "", "classes": [],
                "error": str(e)}


# ─── /api/yolo/test ───


class YoloTestReq(BaseModel):
    instance: Optional[int] = None
    decision_id: Optional[str] = None
    session: Optional[str] = None
    image_b64: Optional[str] = None
    conf_thr: float = 0.4
    classes: Optional[list[str]] = None


@router.post("/api/yolo/test")
async def yolo_test(req: YoloTestReq):
    """dryrun: 选源 → 跑 detect_buttons → 返回 dets + 标注图."""
    shot: Optional[np.ndarray] = None
    src_kind = ""
    if req.image_b64:
        shot = _decode_b64_image(req.image_b64)
        src_kind = "uploaded_b64"
    elif req.decision_id:
        shot = _load_screenshot_from_decision(req.decision_id, req.session or "")
        src_kind = f"decision:{req.decision_id}"
    elif req.instance is not None:
        from . import api as _api_mod
        svc = getattr(_api_mod, "_active_service", None)
        cfg = getattr(_api_mod, "_active_config", None)
        if svc is None:
            raise HTTPException(503, "主 service 不可用")
        adb_path = ""
        if cfg is not None:
            try:
                adb_path = cfg.settings.adb_path or os.path.join(
                    cfg.settings.ldplayer_path, "adb.exe")
            except Exception:
                pass
        try:
            jpg = await svc.get_screenshot(int(req.instance), adb_path=adb_path, max_width=0)
        except Exception as e:
            raise HTTPException(500, f"截图失败: {e}")
        if jpg is None:
            raise HTTPException(503, f"实例 #{req.instance} 抓不到画面 (模拟器没开?)")
        arr = np.frombuffer(jpg, dtype=np.uint8)
        shot = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        src_kind = f"instance:{req.instance}"

    if shot is None:
        raise HTTPException(400, "未提供有效的源 (image_b64 / decision_id / instance)")

    try:
        from .automation.yolo_detector import detect_buttons
    except Exception as e:
        raise HTTPException(500, f"YOLO 不可用: {e}")

    t0 = time.perf_counter()
    try:
        dets = detect_buttons(shot, names=req.classes, conf_thr=float(req.conf_thr))
    except Exception as e:
        logger.warning(f"[yolo_test] detect err: {e}")
        return JSONResponse({
            "ok": False, "error": str(e),
            "source": src_kind, "duration_ms": 0,
            "detections": [], "annotated_b64": "",
        })
    dur_ms = round((time.perf_counter() - t0) * 1000, 2)

    annotated = _encode_jpeg_b64(_draw_dets(shot, dets))
    out = []
    for d in dets:
        out.append({
            "name": d.name,
            "class_id": int(d.class_id),
            "score": round(float(d.score), 4),
            "x1": int(d.x1), "y1": int(d.y1),
            "x2": int(d.x2), "y2": int(d.y2),
            "cx": int((d.x1 + d.x2) // 2),
            "cy": int((d.y1 + d.y2) // 2),
        })
    return {
        "ok": True,
        "source": src_kind,
        "source_image_size": [int(shot.shape[1]), int(shot.shape[0])],
        "conf_thr": float(req.conf_thr),
        "duration_ms": dur_ms,
        "detections": out,
        "annotated_b64": annotated,
    }

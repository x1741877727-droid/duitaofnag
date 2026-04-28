"""
/api/roi/* — ROI 调试 + 校准 (前端 OCR 调试页面后端).

3 个端点:
  GET  /api/roi/list       所有 ROI: name/rect/scale/desc/used_in
  POST /api/roi/save       修改某 ROI (写回 yaml + 备份 + reload)
  POST /api/roi/test_ocr   抓帧 + 裁 ROI + OCR + 返回结果

切分辨率后 ROI 用归一化坐标本来不需要改, 但 OCR 在小分辨率下识别率
可能下降, 用户需要工具实测调整 — 这是该模块的核心需求.
"""
from __future__ import annotations

import base64
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()


def _roi_yaml_path() -> Path:
    """ROI yaml 路径. dev = config/roi.yaml; frozen = exe 旁的 config/.
    跟 roi_config.py 同算法."""
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
        for sub in ("config/roi.yaml", "_internal/config/roi.yaml"):
            p = base / sub
            if p.exists():
                return p
    here = Path(__file__).resolve()
    return here.parent.parent / "config" / "roi.yaml"


# ─── /api/roi/list ───

@router.get("/api/roi/list")
async def roi_list():
    """所有 ROI 元数据."""
    try:
        from .automation.roi_config import _load
        cfg = _load()
    except Exception as e:
        raise HTTPException(500, f"加载 roi.yaml 失败: {e}")

    items = []
    for name, item in cfg.items():
        rect = item.get("rect", [0, 0, 0, 0])
        items.append({
            "name": name,
            "rect": [float(x) for x in rect[:4]] if len(rect) >= 4 else [0, 0, 0, 0],
            "scale": int(item.get("scale", 1)),
            "desc": str(item.get("desc", "")),
            "used_in": str(item.get("used_in", "")),
        })
    return {"items": items, "count": len(items), "yaml_path": str(_roi_yaml_path())}


# ─── /api/roi/save ───

class SaveRoiReq(BaseModel):
    name: str
    rect: list[float]              # [x1,y1,x2,y2] in [0,1]
    scale: int = 1
    desc: Optional[str] = None
    used_in: Optional[str] = None


@router.post("/api/roi/save")
async def roi_save(req: SaveRoiReq):
    """更新或新增 ROI. 写 yaml 前自动备份."""
    if len(req.rect) != 4:
        raise HTTPException(400, "rect 必须 4 元素 [x1,y1,x2,y2]")
    for v in req.rect:
        if not 0 <= v <= 1:
            raise HTTPException(400, f"rect 值必须 0-1: {v}")
    if req.rect[0] >= req.rect[2] or req.rect[1] >= req.rect[3]:
        raise HTTPException(400, "rect 必须满足 x1<x2 且 y1<y2")
    if not req.name or not req.name.replace("_", "").isalnum():
        raise HTTPException(400, "name 只允许字母数字下划线")

    yaml_p = _roi_yaml_path()
    if not yaml_p.is_file():
        raise HTTPException(500, f"roi.yaml 不存在: {yaml_p}")

    # 备份 (限 10 个最新, 老的自动清理)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = yaml_p.parent / f"{yaml_p.name}.bak.{ts}"
    try:
        bak.write_bytes(yaml_p.read_bytes())
    except Exception as e:
        logger.warning(f"[roi] 备份失败: {e}")
    try:
        baks = sorted(yaml_p.parent.glob(f"{yaml_p.name}.bak.*"), key=lambda p: p.stat().st_mtime)
        for old in baks[:-10]:
            old.unlink()
    except Exception:
        pass

    try:
        import yaml
    except ImportError:
        raise HTTPException(500, "PyYAML 未装")

    with yaml_p.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    if req.name not in data:
        data[req.name] = {}
    data[req.name]["rect"] = [float(x) for x in req.rect]
    data[req.name]["scale"] = int(req.scale)
    if req.desc is not None:
        data[req.name]["desc"] = req.desc
    if req.used_in is not None:
        data[req.name]["used_in"] = req.used_in

    with yaml_p.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False, default_flow_style=None)

    try:
        from .automation.roi_config import reload as _reload
        _reload()
    except Exception as e:
        logger.warning(f"[roi] reload 失败 (改动写盘了但缓存没刷): {e}")

    logger.info(f"[roi] save {req.name} rect={req.rect} scale={req.scale}, backup={bak.name}")
    return {"ok": True, "name": req.name, "backup": bak.name}


# ─── /api/roi/test_ocr ───

class TestOcrReq(BaseModel):
    instance: Optional[int] = None
    decision_id: Optional[str] = None
    session: Optional[str] = None
    rect: list[float]              # 当前编辑中的 rect (不一定 == 已保存的)
    scale: int = 2


@router.post("/api/roi/test_ocr")
async def roi_test_ocr(req: TestOcrReq):
    """抓帧 → 按 req.rect 裁 → scale 放大 → OCR → 返回结果 + 图.

    返回:
        full_image_b64: 全图 (jpeg base64), 前端用来画 ROI 红框
        cropped_image_b64: ROI 内容放大后图 (jpeg base64)
        source_size: [w, h] 原图尺寸
        rect_pixels: [x1,y1,x2,y2] ROI 在原图的像素坐标
        ocr_results: [{text, conf, box (4点原图坐标), cx, cy}]
    """
    if len(req.rect) != 4:
        raise HTTPException(400, "rect 必须 4 元素")

    shot = await _grab_frame(req.instance, req.decision_id, req.session)
    if shot is None:
        raise HTTPException(503, "抓帧失败 (实例没开 / 决策不存在)")

    h, w = shot.shape[:2]
    x1, y1, x2, y2 = req.rect
    px1 = max(0, min(int(w * x1), w - 1))
    py1 = max(0, min(int(h * y1), h - 1))
    px2 = max(px1 + 1, min(int(w * x2), w))
    py2 = max(py1 + 1, min(int(h * y2), h))
    crop = shot[py1:py2, px1:px2]
    if crop.size == 0:
        raise HTTPException(400, "ROI 裁剪结果为空")

    scale = max(1, int(req.scale))
    if scale > 1:
        crop_big = cv2.resize(crop, (0, 0), fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    else:
        crop_big = crop

    # OCR
    ocr_results = []
    try:
        from .automation.ocr_dismisser import OcrDismisser
        odm = OcrDismisser(max_rounds=1)
        ocr = odm._get_ocr()
        result = ocr(crop_big)
        if result and result.boxes is not None:
            for box, text, conf in zip(result.boxes, result.txts, result.scores):
                # box 是 4 点 (在 crop_big 坐标系) → 映射回原图
                box_orig = []
                for (bx, by) in box:
                    ox = float(bx / scale + px1)
                    oy = float(by / scale + py1)
                    box_orig.append([ox, oy])
                cx = sum(p[0] for p in box_orig) / 4
                cy = sum(p[1] for p in box_orig) / 4
                ocr_results.append({
                    "text": str(text),
                    "conf": float(conf),
                    "box": box_orig,
                    "cx": cx,
                    "cy": cy,
                })
    except Exception as e:
        logger.warning(f"[roi/test_ocr] OCR 失败: {e}", exc_info=True)
        raise HTTPException(500, f"OCR 失败: {e}")

    def _b64(img: np.ndarray) -> str:
        ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        if not ok:
            return ""
        return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()

    return {
        "ok": True,
        "full_image_b64": _b64(shot),
        "cropped_image_b64": _b64(crop_big),
        "source_size": [w, h],
        "rect_pixels": [px1, py1, px2, py2],
        "scale": scale,
        "ocr_results": ocr_results,
        "n_texts": len(ocr_results),
    }


# ─── 抓帧辅助 (复用 api_yolo / api_runner_test 同源) ───

async def _grab_frame(instance: Optional[int], decision_id: Optional[str],
                      session: Optional[str]) -> Optional[np.ndarray]:
    if instance is not None:
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
            jpg = await svc.get_screenshot(int(instance), adb_path=adb_path, max_width=0)
        except Exception as e:
            raise HTTPException(500, f"截图失败: {e}")
        if jpg is None:
            return None
        arr = np.frombuffer(jpg, dtype=np.uint8)
        return cv2.imdecode(arr, cv2.IMREAD_COLOR)

    if decision_id:
        if not session:
            raise HTTPException(400, "decision_id 必须配 session")
        from .automation.decision_log import get_recorder
        rec = get_recorder()
        root = rec._logs_root()
        if root is None:
            raise HTTPException(503, "decision recorder 未初始化")
        dec_dir = root / session / "decisions" / decision_id
        if not dec_dir.is_dir():
            raise HTTPException(404, f"决策不存在: {session}/{decision_id}")
        for fname in ("input.jpg", "input.png"):
            p = dec_dir / fname
            if p.is_file():
                img = cv2.imread(str(p))
                if img is not None:
                    return img
        raise HTTPException(404, f"{decision_id} 目录无 input 图")

    raise HTTPException(400, "需要 instance 或 decision_id")

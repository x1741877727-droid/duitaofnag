"""
/api/templates/* — 模版库 API.

端点:
  GET  /api/templates/list           列出 fixtures/templates/*.png + 元数据
  GET  /api/templates/file/{name}    返回模版 PNG (前端 <img>)
  GET  /api/templates/stats/{name}   命中率 (从 metrics.jsonl 聚合)
  POST /api/templates/upload         上传图片 (整图或裁剪后) 保存为新模版
  POST /api/templates/test           dryrun 匹配 (选模版 + 选源 → 返回命中位置)
  DELETE /api/templates/{name}       删除模版

源数据: backend/automation/template_test.py + ScreenMatcher.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

# python-multipart 是 UploadFile/Form 必备. mac dev 环境若没装,
# 跳过 upload 路由让其他路由仍可用.
try:
    from fastapi import UploadFile, File, Form
    _HAS_MULTIPART = True
except Exception:
    _HAS_MULTIPART = False

logger = logging.getLogger(__name__)

router = APIRouter()


# ─── 路径解析 ───


def _project_root() -> Path:
    """兼容打包 (Nuitka / PyInstaller) 找到项目根."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def _template_dir() -> Path:
    root = _project_root()
    candidates = [
        root / "fixtures" / "templates",
        root / "_internal" / "fixtures" / "templates",
    ]
    for p in candidates:
        if p.is_dir():
            return p
    return candidates[0]


def _meta_dir() -> Path:
    """元数据目录: 存原图 + 裁剪 bbox JSON.
    每模版一组: <name>_orig.png + <name>.json
    """
    d = _template_dir().parent / "templates_meta"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _read_meta(name: str) -> Optional[dict]:
    p = _meta_dir() / f"{name}.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_meta(name: str, *, orig_filename: str = "", crop_bbox: Optional[list] = None,
                source: str = "") -> None:
    p = _meta_dir() / f"{name}.json"
    try:
        data = {
            "name": name,
            "orig_filename": orig_filename,
            "crop_bbox": crop_bbox,
            "source": source,
            "saved_at": time.time(),
        }
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.debug(f"[templates] write_meta err: {e}")


# ─── 工具 ───


_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


def _safe_name(name: str) -> str:
    name = (name or "").strip()
    if not _NAME_RE.match(name):
        raise HTTPException(400, f"模版名不合法 (允许 A-Z a-z 0-9 _ - 共 1-64 字符): {name!r}")
    return name


def _phash_image(img: np.ndarray) -> int:
    """计算图像 perceptual hash (与 adb_lite.phash 同算法)."""
    try:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
        small = cv2.resize(gray, (8, 8), interpolation=cv2.INTER_AREA)
        avg = float(small.mean())
        bits = 0
        for i, v in enumerate(small.flatten()):
            if float(v) > avg:
                bits |= (1 << i)
        return bits
    except Exception:
        return 0


def _hamming(a: int, b: int) -> int:
    return bin(int(a) ^ int(b)).count("1")


def _categorize(name: str) -> str:
    """按文件名前缀归类."""
    if name.startswith("accelerator_"):
        return "加速器"
    if name.startswith("close_x_"):
        return "关闭按钮"
    if name.startswith("lobby_"):
        return "大厅"
    if name.startswith("btn_"):
        return "操作按钮"
    if name.startswith("card_"):
        return "卡片"
    if name.startswith("mode_"):
        return "模式"
    if name.startswith("map_"):
        return "地图"
    if name.startswith("tab_"):
        return "标签页"
    if name.startswith("text_"):
        return "文本"
    return "杂项"


# ─── /api/templates/list ───


@router.get("/api/templates/list")
async def list_templates():
    """列出全部模版 + 文件元数据."""
    d = _template_dir()
    items = []
    if d.is_dir():
        for f in sorted(d.glob("*.png")):
            try:
                st = f.stat()
                # 读图获取尺寸 (轻量, 不读像素)
                img = cv2.imread(str(f))
                if img is None:
                    continue
                h, w = img.shape[:2]
                items.append({
                    "name": f.stem,
                    "category": _categorize(f.stem),
                    "path": str(f.relative_to(_project_root())).replace("\\", "/"),
                    "size_bytes": st.st_size,
                    "mtime": st.st_mtime,
                    "width": int(w),
                    "height": int(h),
                    "phash": f"0x{_phash_image(img):016x}",
                })
            except Exception as e:
                logger.debug(f"[templates] skip {f}: {e}")
    # 按分类聚合
    by_cat: dict[str, int] = {}
    for it in items:
        by_cat[it["category"]] = by_cat.get(it["category"], 0) + 1
    return {
        "count": len(items),
        "items": items,
        "categories": [{"name": k, "count": v} for k, v in sorted(by_cat.items())],
        "template_dir": str(d).replace("\\", "/"),
    }


# ─── /api/templates/file/{name} ───


@router.get("/api/templates/file/{name}")
async def template_file(name: str):
    name = _safe_name(name)
    p = _template_dir() / f"{name}.png"
    if not p.is_file():
        raise HTTPException(404, "模版不存在")
    return FileResponse(p, media_type="image/png")


@router.get("/api/templates/detail/{name}")
async def template_detail(name: str):
    """模版详情: 含元数据 + 原图存在性 + 裁剪 bbox.
    前端用 crop_bbox 在原图上画矩形, 显示"模版来自这里".
    """
    name = _safe_name(name)
    p = _template_dir() / f"{name}.png"
    if not p.is_file():
        raise HTTPException(404, "模版不存在")
    img = cv2.imread(str(p))
    h, w = (int(img.shape[0]), int(img.shape[1])) if img is not None else (0, 0)
    meta = _read_meta(name) or {}
    orig_filename = meta.get("orig_filename") or ""
    has_orig = bool(orig_filename) and (_meta_dir() / orig_filename).is_file()
    return {
        "name": name,
        "category": _categorize(name),
        "width": w,
        "height": h,
        "phash": f"0x{_phash_image(img):016x}" if img is not None else "",
        "has_original": has_orig,
        "original_url": f"/api/templates/original/{name}" if has_orig else "",
        "crop_bbox": meta.get("crop_bbox"),     # [x1,y1,x2,y2] 在原图坐标
        "source": meta.get("source", ""),
        "saved_at": meta.get("saved_at"),
    }


@router.get("/api/templates/original/{name}")
async def template_original(name: str):
    """模版原图 (上传/裁剪源图). 老模版未保留 → 404 让前端显示"原图丢失"."""
    name = _safe_name(name)
    meta = _read_meta(name) or {}
    orig_filename = meta.get("orig_filename") or ""
    if not orig_filename:
        raise HTTPException(404, "原图未保留")
    p = _meta_dir() / orig_filename
    if not p.is_file():
        raise HTTPException(404, "原图文件丢失")
    return FileResponse(p, media_type="image/png")


# ─── /api/templates/stats/{name} ───


@router.get("/api/templates/stats/{name}")
async def template_stats(name: str, recent_sessions: int = Query(3, ge=1, le=20)):
    """从最近 N 个 session 的 metrics.jsonl 聚合该模版的命中率统计."""
    name = _safe_name(name)
    try:
        from .automation.decision_log import get_recorder
        rec = get_recorder()
        logs_root = rec._logs_root()
    except Exception:
        logs_root = None
    if logs_root is None or not logs_root.is_dir():
        return {"name": name, "sessions_scanned": 0, "match_count": 0, "hit_count": 0,
                "hit_rate": 0.0, "avg_score": 0.0}
    # 取最近 N 个 session
    sessions = []
    try:
        sessions = sorted(
            [d for d in logs_root.iterdir() if d.is_dir()],
            key=lambda d: d.stat().st_mtime, reverse=True,
        )[:recent_sessions]
    except Exception:
        pass
    match_count = 0
    hit_count = 0
    score_sum = 0.0
    for sess in sessions:
        mfile = sess / "metrics.jsonl"
        if not mfile.is_file():
            continue
        try:
            with open(mfile, "r", encoding="utf-8") as fh:
                for line in fh:
                    try:
                        rec_d = json.loads(line)
                    except Exception:
                        continue
                    if rec_d.get("action") != "template_match":
                        continue
                    if rec_d.get("tpl") != name:
                        continue
                    match_count += 1
                    score_sum += float(rec_d.get("score", 0.0))
                    if rec_d.get("hit"):
                        hit_count += 1
        except Exception:
            continue
    hit_rate = (hit_count / match_count) if match_count > 0 else 0.0
    avg_score = (score_sum / match_count) if match_count > 0 else 0.0
    return {
        "name": name,
        "sessions_scanned": len(sessions),
        "match_count": match_count,
        "hit_count": hit_count,
        "hit_rate": round(hit_rate, 3),
        "avg_score": round(avg_score, 3),
    }


# ─── DELETE /api/templates/{name} ───


@router.delete("/api/templates/{name}")
async def delete_template(name: str):
    name = _safe_name(name)
    p = _template_dir() / f"{name}.png"
    if not p.is_file():
        raise HTTPException(404, "模版不存在")
    try:
        p.unlink()
    except Exception as e:
        raise HTTPException(500, f"删除失败: {e}")
    return {"ok": True, "deleted": name}


# ─── POST /api/templates/upload ───


def _register_upload():
    if not _HAS_MULTIPART:
        return
    try:
        import multipart  # noqa: F401
    except Exception:
        logger.warning("[templates] python-multipart 未安装, /api/templates/upload 不挂载")
        return

    @router.post("/api/templates/upload")
    async def upload_template(
        file: UploadFile = File(...),
        name: str = Form(...),
        overwrite: bool = Form(False),
        crop_x: Optional[int] = Form(None),
        crop_y: Optional[int] = Form(None),
        crop_w: Optional[int] = Form(None),
        crop_h: Optional[int] = Form(None),
    ):
        """上传图片保存为模版.
        crop_* 可选: 若提供则按 (crop_x, crop_y, crop_w, crop_h) 裁剪; 否则整图.
        """
        sname = _safe_name(name)
        data = await file.read()
        if not data:
            raise HTTPException(400, "文件为空")
        arr = np.frombuffer(data, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            raise HTTPException(400, "无法解码图片")
        h, w = img.shape[:2]
        if crop_x is not None and crop_y is not None and crop_w is not None and crop_h is not None:
            x1 = max(0, min(int(crop_x), w - 1))
            y1 = max(0, min(int(crop_y), h - 1))
            x2 = max(x1 + 1, min(int(crop_x + crop_w), w))
            y2 = max(y1 + 1, min(int(crop_y + crop_h), h))
            img = img[y1:y2, x1:x2]
            if img.size == 0:
                raise HTTPException(400, "裁剪区域空")
        d = _template_dir()
        d.mkdir(parents=True, exist_ok=True)
        dst = d / f"{sname}.png"
        if dst.exists() and not overwrite:
            raise HTTPException(409, f"模版已存在: {sname} (overwrite=true 强制覆盖)")
        # phash 去重提示
        new_phash = _phash_image(img)
        similar = []
        for f in d.glob("*.png"):
            if f.stem == sname:
                continue
            try:
                other = cv2.imread(str(f))
                if other is None:
                    continue
                dist = _hamming(new_phash, _phash_image(other))
                if dist < 5:
                    similar.append({"name": f.stem, "phash_dist": dist})
            except Exception:
                continue
        ok = cv2.imwrite(str(dst), img)
        if not ok:
            raise HTTPException(500, "保存失败")
        # 保留原图 + 裁剪 bbox 元数据 (供详情页"看是裁剪源图哪里"用)
        orig_full = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        crop_bbox: Optional[list] = None
        orig_filename = ""
        if orig_full is not None:
            md = _meta_dir()
            orig_filename = f"{sname}_orig.png"
            try:
                cv2.imwrite(str(md / orig_filename), orig_full,
                            [cv2.IMWRITE_PNG_COMPRESSION, 5])
            except Exception:
                orig_filename = ""
            if crop_x is not None and crop_y is not None and crop_w is not None and crop_h is not None:
                crop_bbox = [int(crop_x), int(crop_y),
                             int(crop_x + crop_w), int(crop_y + crop_h)]
        _write_meta(sname, orig_filename=orig_filename,
                    crop_bbox=crop_bbox, source="upload")
        _reload_test_matcher()
        return {
            "ok": True,
            "name": sname,
            "path": str(dst.relative_to(_project_root())).replace("\\", "/"),
            "width": int(img.shape[1]),
            "height": int(img.shape[0]),
            "phash": f"0x{new_phash:016x}",
            "similar": similar,
            "orig_filename": orig_filename,
            "crop_bbox": crop_bbox,
        }


_register_upload()


# ─── POST /api/templates/test ───


class TemplateTestReq(BaseModel):
    name: str
    instance: Optional[int] = None        # 抓该实例当前帧
    decision_id: Optional[str] = None     # 拉历史决策的 input.jpg
    session: Optional[str] = None         # decision_id 配合用
    image_b64: Optional[str] = None       # 上传 base64 图片直接测
    threshold: Optional[float] = None
    use_edge: bool = False


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


def _decode_b64_image(s: str) -> Optional[np.ndarray]:
    try:
        if s.startswith("data:"):
            s = s.split(",", 1)[1]
        data = base64.b64decode(s)
        arr = np.frombuffer(data, dtype=np.uint8)
        return cv2.imdecode(arr, cv2.IMREAD_COLOR)
    except Exception:
        return None


# 全局 ScreenMatcher 单例 (templates 路由复用 — 跟主 runner 隔离)
_test_matcher = None


def _get_test_matcher():
    global _test_matcher
    if _test_matcher is None:
        try:
            from .automation.screen_matcher import ScreenMatcher
            _test_matcher = ScreenMatcher(str(_template_dir()))
        except Exception as e:
            logger.warning(f"[templates] init ScreenMatcher 失败: {e}")
            _test_matcher = None
    return _test_matcher


def _reload_test_matcher():
    """upload/delete 后调, 让测试用 matcher 看见新模版."""
    global _test_matcher
    _test_matcher = None


@router.post("/api/templates/test")
async def template_test(req: TemplateTestReq):
    """dryrun: 选模版 + 选源 → 跑匹配 → 返回命中位置 + 标注图.
    源优先级: image_b64 > decision_id > instance.
    """
    name = _safe_name(req.name)
    matcher = _get_test_matcher()
    if matcher is None:
        raise HTTPException(500, "ScreenMatcher 未就绪")

    shot: Optional[np.ndarray] = None
    src_kind = ""
    if req.image_b64:
        shot = _decode_b64_image(req.image_b64)
        src_kind = "uploaded_b64"
    elif req.decision_id:
        shot = _load_screenshot_from_decision(req.decision_id, req.session or "")
        src_kind = f"decision:{req.decision_id}"
    elif req.instance is not None:
        # 抓帧: runner 启动了用 minicap 流, 没启动直接 ADB 拉 (传 adb_path 触发 fallback)
        from . import api as _api_mod
        svc = getattr(_api_mod, "_active_service", None)
        cfg = getattr(_api_mod, "_active_config", None)
        if svc is None:
            raise HTTPException(503, "主 service 不可用; 试上传图片或选历史决策")
        adb_path = ""
        if cfg is not None:
            try:
                adb_path = cfg.settings.adb_path or os.path.join(
                    cfg.settings.ldplayer_path, "adb.exe")
            except Exception:
                adb_path = ""
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

    from .automation.template_test import run_test
    r = run_test(
        template_name=name,
        matcher=matcher,
        screenshot=shot,
        threshold=req.threshold,
        use_edge=req.use_edge,
        annotate=True,
    )
    from dataclasses import asdict as _asd
    out = _asd(r)
    out["source"] = src_kind
    out["source_image_size"] = [int(shot.shape[1]), int(shot.shape[0])]
    return JSONResponse(out)

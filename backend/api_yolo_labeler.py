"""
/api/labeler/* — YOLO 数据集 / 标注 / 模型上传 / 截图采集.

把 debug_server.py 里 ~250 行 labeler 路由迁到主 8900.
+ 新增 /api/labeler/capture (从实例抓帧 → 存 raw_screenshots/, 用于训练数据采集)
+ 模型上传 /api/labeler/upload_model (与 /api/yolo/upload_model 同效).

存储结构 (跟旧版一致, 不破坏现有数据):
  %APPDATA%/GameBot/data/yolo/
    raw_screenshots/    .png/.jpg 原图
    labels/             {stem}.txt  YOLO 格式 (cid cx cy w h, normalized)
    classes.txt         类名每行一个
    models/latest.onnx  当前生产模型
    .trash/             删除的图移到这里
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()

# 默认类 (如果 classes.txt 不存在) — 跟 yolo_dismisser.CLASSES 对齐
DEFAULT_CLASSES = ["close_x", "action_btn"]


def _read_classes() -> list[str]:
    """动态读 classes.txt. 不存在 → 写默认 + 返回."""
    from .automation.user_paths import user_yolo_dir
    p = user_yolo_dir() / "classes.txt"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(DEFAULT_CLASSES) + "\n", encoding="utf-8")
        return list(DEFAULT_CLASSES)
    try:
        lines = [x.strip() for x in p.read_text(encoding="utf-8").splitlines()]
        return [x for x in lines if x]
    except Exception:
        return list(DEFAULT_CLASSES)


def _write_classes(names: list[str]) -> None:
    from .automation.user_paths import user_yolo_dir
    p = user_yolo_dir() / "classes.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(names) + "\n", encoding="utf-8")


# 兼容旧引用
def _label_classes() -> list[str]:
    return _read_classes()


# ─── 路径 ───


def _yolo_paths():
    """(raw_dir, labels_dir, classes_path)"""
    from .automation.user_paths import user_yolo_dir
    root = user_yolo_dir()
    raw = root / "raw_screenshots"
    labels = root / "labels"
    classes_p = root / "classes.txt"
    raw.mkdir(parents=True, exist_ok=True)
    labels.mkdir(parents=True, exist_ok=True)
    _read_classes()  # 触发默认 classes.txt 写入
    return raw, labels, classes_p


def _label_path_for(image_filename: str) -> Path:
    _, labels_dir, _ = _yolo_paths()
    stem = os.path.splitext(image_filename)[0]
    return labels_dir / f"{stem}.txt"


_NAME_RE = re.compile(r"^[A-Za-z0-9_\-.]+$")


def _safe_filename(name: str) -> str:
    name = (name or "").strip()
    if not _NAME_RE.match(name) or ".." in name:
        raise HTTPException(400, f"非法文件名: {name!r}")
    return name


# ─── /api/labeler/list ───


@router.get("/api/labeler/list")
async def labeler_list():
    """所有原始截图 + 标注状态 + 每类 bbox 数."""
    raw, labels_dir, _ = _yolo_paths()
    classes = _read_classes()
    items = []
    class_counts: dict[int, int] = {}
    class_imgs: dict[int, set[str]] = {}
    for p in sorted(raw.glob("*.png")) + sorted(raw.glob("*.jpg")):
        label_p = labels_dir / f"{p.stem}.txt"
        labeled = label_p.is_file() and label_p.stat().st_size > 0
        skipped = label_p.is_file() and label_p.stat().st_size == 0
        # 抽这张图含哪些 cid (前端按类筛用)
        img_cids: set[int] = set()
        if labeled:
            try:
                for line in label_p.read_text(encoding="utf-8").splitlines():
                    parts = line.strip().split()
                    if len(parts) != 5:
                        continue
                    try:
                        cid = int(parts[0])
                    except ValueError:
                        continue
                    class_counts[cid] = class_counts.get(cid, 0) + 1
                    class_imgs.setdefault(cid, set()).add(p.name)
                    img_cids.add(cid)
            except Exception:
                pass
        items.append({
            "name": p.name,
            "size": p.stat().st_size,
            "mtime": p.stat().st_mtime,
            "labeled": labeled,
            "skipped": skipped,
            "class_ids": sorted(img_cids),
        })
    items.sort(key=lambda x: x["mtime"])
    n_labeled = sum(1 for i in items if i["labeled"])
    n_skipped = sum(1 for i in items if i["skipped"])

    per_class = []
    legacy_names = {2: "dialog (历史)"}
    all_cids = sorted(set(list(class_counts.keys()) + list(range(len(classes)))))
    for cid in all_cids:
        if cid < len(classes):
            name = classes[cid]
        else:
            name = legacy_names.get(cid, f"class_{cid}")
        per_class.append({
            "id": cid,
            "name": name,
            "instances": class_counts.get(cid, 0),
            "images": len(class_imgs.get(cid, set())),
        })

    return {
        "total": len(items),
        "labeled": n_labeled,
        "skipped": n_skipped,
        "remaining": len(items) - n_labeled - n_skipped,
        "classes": classes,
        "per_class": per_class,
        "items": items,
    }


# ─── /api/labeler/image/{name} ───


@router.get("/api/labeler/image/{filename}")
async def labeler_image(filename: str):
    raw, _, _ = _yolo_paths()
    filename = _safe_filename(filename)
    for ext in (".png", ".jpg"):
        full = raw / (os.path.splitext(filename)[0] + ext)
        if full.is_file():
            return FileResponse(full, media_type=f"image/{ext[1:]}")
    raise HTTPException(404, "图片不存在")


# ─── /api/labeler/labels/{name} GET ───


@router.get("/api/labeler/labels/{filename}")
async def labeler_get_labels(filename: str):
    """YOLO 格式 (cid cx cy w h, normalized 0-1)"""
    filename = _safe_filename(filename)
    label_p = _label_path_for(filename)
    if not label_p.is_file():
        return {"boxes": [], "exists": False}
    boxes = []
    for line in label_p.read_text(encoding="utf-8").splitlines():
        parts = line.strip().split()
        if len(parts) != 5:
            continue
        try:
            boxes.append({
                "class_id": int(parts[0]),
                "cx": float(parts[1]),
                "cy": float(parts[2]),
                "w": float(parts[3]),
                "h": float(parts[4]),
            })
        except ValueError:
            continue
    return {"boxes": boxes, "exists": True}


# ─── /api/labeler/labels/{name} POST ───


class SaveLabelsReq(BaseModel):
    boxes: list


@router.post("/api/labeler/labels/{filename}")
async def labeler_save_labels(filename: str, req: SaveLabelsReq):
    """保存 YOLO .txt (空 boxes → 空文件 = 标记为跳过/背景图)"""
    filename = _safe_filename(filename)
    label_p = _label_path_for(filename)
    label_p.parent.mkdir(parents=True, exist_ok=True)
    classes = _read_classes()
    lines = []
    for b in req.boxes:
        try:
            cid = int(b.get("class_id", -1))
            cx = float(b.get("cx", 0))
            cy = float(b.get("cy", 0))
            w = float(b.get("w", 0))
            h = float(b.get("h", 0))
        except (TypeError, ValueError):
            continue
        if cid < 0 or cid >= len(classes):
            continue
        if w <= 0 or h <= 0:
            continue
        lines.append(f"{cid} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}")
    label_p.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return {"ok": True, "count": len(lines), "path": str(label_p)}


# ─── /api/labeler/image/{name} DELETE ───


@router.delete("/api/labeler/image/{filename}")
async def labeler_delete_image(filename: str):
    """废弃图片 (移到 .trash/)"""
    filename = _safe_filename(filename)
    raw, _, _ = _yolo_paths()
    trash = raw.parent / ".trash"
    trash.mkdir(exist_ok=True)
    moved = []
    for ext in (".png", ".jpg"):
        full = raw / (os.path.splitext(filename)[0] + ext)
        if full.is_file():
            full.rename(trash / full.name)
            moved.append(full.name)
    label_p = _label_path_for(filename)
    if label_p.is_file():
        label_p.unlink()
    return {"ok": True, "moved": moved}


# ─── /api/labeler/export.zip ───


@router.get("/api/labeler/export.zip")
async def labeler_export():
    """打包训练数据为 zip (images/ + labels/ + classes.txt + manifest.json)"""
    import random
    raw, labels_dir, _ = _yolo_paths()
    classes = _read_classes()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("classes.txt", "\n".join(classes) + "\n")
        labeled_imgs = []
        skipped_imgs = []
        for p in sorted(raw.glob("*.png")) + sorted(raw.glob("*.jpg")):
            label_p = labels_dir / f"{p.stem}.txt"
            if not label_p.is_file():
                continue
            if label_p.stat().st_size > 0:
                labeled_imgs.append(p)
            else:
                skipped_imgs.append(p)
        max_bg = max(20, len(labeled_imgs) // 3)
        random.seed(42)
        bg_sample = random.sample(skipped_imgs, min(max_bg, len(skipped_imgs)))
        for p in labeled_imgs + bg_sample:
            label_p = labels_dir / f"{p.stem}.txt"
            zf.write(str(p), arcname=f"images/{p.name}")
            if label_p.is_file() and label_p.stat().st_size > 0:
                kept = []
                for line in label_p.read_text(encoding="utf-8").splitlines():
                    parts = line.strip().split()
                    if len(parts) != 5:
                        continue
                    try:
                        cid = int(parts[0])
                    except ValueError:
                        continue
                    if 0 <= cid < len(classes):
                        kept.append(line.strip())
                zf.writestr(f"labels/{p.stem}.txt",
                            "\n".join(kept) + ("\n" if kept else ""))
            else:
                zf.writestr(f"labels/{p.stem}.txt", "")
        zf.writestr("manifest.json", json.dumps({
            "classes": classes,
            "labeled": len(labeled_imgs),
            "background_sampled": len(bg_sample),
            "background_total": len(skipped_imgs),
        }, ensure_ascii=False, indent=2))
    buf.seek(0)
    fname = f"yolo_dataset_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    return Response(
        content=buf.read(),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )


# ─── /api/labeler/capture ───


class CaptureReq(BaseModel):
    instance: int
    tag: str = "manual"


@router.post("/api/labeler/capture")
async def labeler_capture(req: CaptureReq):
    """从实例抓帧 → 存到 raw_screenshots/{tag}_inst{N}_{ts}.png.

    不依赖 runner 启动 (走 ADB fallback). 文件名兼容 screenshot_collector 风格.
    """
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
    if shot is None:
        raise HTTPException(500, "图片解码失败")
    raw, _, _ = _yolo_paths()
    safe_tag = re.sub(r"[^A-Za-z0-9_\-]", "_", req.tag or "manual")[:40] or "manual"
    fname = f"{safe_tag}_inst{int(req.instance)}_{int(time.time())}.png"
    dst = raw / fname
    if not cv2.imwrite(str(dst), shot):
        raise HTTPException(500, "保存失败")
    return {
        "ok": True,
        "name": fname,
        "size": dst.stat().st_size,
        "width": int(shot.shape[1]),
        "height": int(shot.shape[0]),
    }


# ─── /api/labeler/upload_model ───
# multipart 必须, 缺 python-multipart 时降级


def _register_upload_model():
    try:
        import multipart  # noqa: F401
        from fastapi import UploadFile, File
    except Exception:
        logger.info("[labeler] multipart 未装, /api/labeler/upload_model 不挂")
        return

    @router.post("/api/labeler/upload_model")
    async def upload_model(file: UploadFile = File(...)):
        """上传 ONNX 模型保存到 latest.onnx (覆盖生产)."""
        from .automation.user_paths import user_yolo_dir
        models_dir = user_yolo_dir() / "models"
        models_dir.mkdir(parents=True, exist_ok=True)
        name = (file.filename or "model.onnx").strip()
        name = re.sub(r"[^A-Za-z0-9_\-.]", "_", name)
        if not name.endswith(".onnx"):
            name = name + ".onnx"
        out = models_dir / name
        data = await file.read()
        out.write_bytes(data)
        latest = models_dir / "latest.onnx"
        latest.write_bytes(data)
        return {
            "ok": True,
            "saved": str(out).replace("\\", "/"),
            "size": len(data),
            "latest": str(latest).replace("\\", "/"),
        }


_register_upload_model()


# ─── /api/labeler/classes ───


@router.get("/api/labeler/classes")
async def list_classes():
    """当前 classes.txt + 已废弃 cid (历史标注但不在 classes 中)."""
    classes = _read_classes()
    _, labels_dir, _ = _yolo_paths()
    legacy_cids: set[int] = set()
    for lp in labels_dir.glob("*.txt"):
        if lp.stat().st_size == 0:
            continue
        try:
            for line in lp.read_text(encoding="utf-8").splitlines():
                parts = line.strip().split()
                if not parts:
                    continue
                try:
                    cid = int(parts[0])
                    if cid >= len(classes):
                        legacy_cids.add(cid)
                except ValueError:
                    continue
        except Exception:
            continue
    return {"classes": classes, "legacy_cids": sorted(legacy_cids)}


class AddClassReq(BaseModel):
    name: str


@router.post("/api/labeler/classes")
async def add_class(req: AddClassReq):
    """追加新类到 classes.txt. 旧 cid 不动, 新类 id = len(classes)."""
    name = (req.name or "").strip()
    if not re.match(r"^[A-Za-z0-9_]{1,32}$", name):
        raise HTTPException(400, "类名只允许 A-Z a-z 0-9 _ (1-32 字符)")
    cur = _read_classes()
    if name in cur:
        raise HTTPException(409, f"类名已存在: {name}")
    cur.append(name)
    _write_classes(cur)
    return {"ok": True, "classes": cur, "new_id": len(cur) - 1}


# ─── /api/labeler/preannotate/{name} ───
# 用 latest.onnx 跑 inference, 返回 normalized YOLO 框给前端预填.
# 用户只需修补 (调框/删错/加漏), 比从零画快 5-10x.
# 注意: 当前模型只训了已有类 (close_x/action_btn). 新类 (如 dialog_box)
# 第一批必须手标种子集, baseline 训出来后才能预标该类.

@router.post("/api/labeler/preannotate/{filename}")
async def labeler_preannotate(filename: str):
    """对单张图跑 YOLO 推理, 返回 normalized boxes 给前端预填."""
    filename = _safe_filename(filename)
    raw, _, _ = _yolo_paths()
    img_path = None
    for ext in (".png", ".jpg"):
        full = raw / (os.path.splitext(filename)[0] + ext)
        if full.is_file():
            img_path = full
            break
    if img_path is None:
        raise HTTPException(404, "图片不存在")

    img = cv2.imread(str(img_path))
    if img is None:
        raise HTTPException(500, "图片解码失败")
    h, w = img.shape[:2]

    try:
        from .automation.yolo_dismisser import YoloDismisser
    except Exception as e:
        raise HTTPException(500, f"YOLO 模块导入失败: {e}")

    det = YoloDismisser._shared()
    if not det.is_available():
        raise HTTPException(
            503,
            "YOLO 模型未加载 (latest.onnx 不存在或加载失败). "
            "需要先训一版 baseline 上传, 才能用预标注.",
        )

    t0 = time.perf_counter()
    try:
        detections = det.detect(img)
    except Exception as e:
        logger.warning(f"[preannotate] inference err: {e}", exc_info=True)
        raise HTTPException(500, f"推理失败: {e}")
    dur_ms = round((time.perf_counter() - t0) * 1000, 1)

    classes = _read_classes()
    boxes = []
    for d in detections:
        cx_n = ((d.x1 + d.x2) / 2) / w
        cy_n = ((d.y1 + d.y2) / 2) / h
        w_n = (d.x2 - d.x1) / w
        h_n = (d.y2 - d.y1) / h
        cls_name = classes[d.cls] if 0 <= d.cls < len(classes) else f"cls{d.cls}"
        boxes.append({
            "class_id": int(d.cls),
            "class_name": cls_name,
            "cx": float(cx_n),
            "cy": float(cy_n),
            "w": float(w_n),
            "h": float(h_n),
            "score": float(d.conf),
        })

    return {
        "ok": True,
        "filename": filename,
        "image_size": [w, h],
        "duration_ms": dur_ms,
        "boxes": boxes,
        "count": len(boxes),
    }

"""
独立调试 web 服务器 — 跑在 0.0.0.0:8901，Mac 浏览器可访问。

跟主 dashboard 完全独立：
  - 不影响 GameBot.exe 桌面 webview
  - 提供实时帧 + 记录模态（前后 3 秒帧 + 画布批注 + 共享文本备注）
  - 记录持久化到 session_dir/debug_records/<id>/

记录目录结构：
  debug_records/<id>/
    meta.json            点击瞬间状态 + 每帧时间戳/sys 指标
    note.txt             用户输入的备注
    frames/00.jpg ...    原始帧（前 1.5s + 后 1.5s 共约 4 张）
    annotations/00.png   每帧的透明画布批注（PNG，可叠加在 frame 上）

Mac 浏览器：http://192.168.0.102:8901
Claude 拉记录：curl http://192.168.0.102:8901/api/records
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
import re
import threading
import time
from datetime import datetime
from typing import Optional, List, Dict

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse, Response, FileResponse
from pydantic import BaseModel
import uvicorn

logger = logging.getLogger(__name__)

# 全局服务引用（main.py / api.py 启动时注入）
_service = None
_session_dir: Optional[str] = None


def set_service(service):
    """从 api.py 调用，把 MultiRunnerService 实例注入"""
    global _service
    _service = service


def set_session_dir(path: str):
    """从 runner_service 调用，每次 start_all 时更新当前 session 日志目录"""
    global _session_dir
    _session_dir = path


def _records_root() -> Optional[str]:
    """记录持久化根目录。session 没启动时返回 None"""
    if _session_dir is None:
        return None
    p = os.path.join(_session_dir, "debug_records")
    os.makedirs(p, exist_ok=True)
    return p


def _safe_id(s: str) -> str:
    """文件名净化"""
    return re.sub(r"[^A-Za-z0-9_\-]", "_", s)[:64]


def _sys_snapshot() -> dict:
    """整机 CPU/内存/进程数（不阻塞，psutil 在 Windows 上 ~1ms）"""
    try:
        import psutil
        vm = psutil.virtual_memory()
        return {
            "cpu_percent": psutil.cpu_percent(interval=None),
            "mem_percent": vm.percent,
            "mem_used_mb": round(vm.used / (1024 * 1024)),
            "mem_total_mb": round(vm.total / (1024 * 1024)),
            "process_count": len(psutil.pids()),
        }
    except Exception as e:
        return {"error": str(e)}


def _instance_snapshot(idx: int) -> dict:
    """某实例当前状态快照（点击记录时存到 meta.json）"""
    if _service is None or idx not in _service._runners:
        return {"idx": idx, "available": False}
    runner = _service._runners[idx]
    info = {
        "idx": idx,
        "role": getattr(runner, "role", None),
        "group": getattr(runner, "group", None),
        "phase": getattr(runner.phase, "value", str(runner.phase)) if hasattr(runner, "phase") else None,
        "available": True,
    }
    st = _service._instance_status.get(idx) if hasattr(_service, "_instance_status") else None
    if st:
        info["state"] = getattr(st, "state", None)
        info["error"] = getattr(st, "error", None)
        info["stage_times"] = dict(getattr(st, "stage_times", {}) or {})
        info["serial"] = getattr(st, "serial", None)
    return info


# ─────────── FastAPI app ───────────

app = FastAPI(title="GameBot Debug")


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML_PAGE


@app.get("/api/status")
async def api_status():
    """返回每实例当前状态 + 阶段时长 + 整机指标"""
    if _service is None:
        return {"running": False, "instances": [], "sys": _sys_snapshot()}
    out = []
    for idx in sorted(_service._runners.keys()):
        out.append(_instance_snapshot(idx))
    return {
        "running": getattr(_service, "running", False),
        "session_dir": _session_dir,
        "instances": out,
        "sys": _sys_snapshot(),
        "ts": time.time(),
    }


@app.get("/api/screenshot/{idx}.jpg")
async def api_screenshot(idx: int, q: int = 70):
    """实时截图 JPEG"""
    if _service is None or idx not in _service._runners:
        raise HTTPException(404, "instance not found")
    runner = _service._runners[idx]
    adb = getattr(runner, "adb", None)
    if adb is None:
        raise HTTPException(500, "adb not initialized")
    raw_adb = getattr(adb, "_adb", adb)
    try:
        shot = await raw_adb.screenshot()
    except Exception as e:
        raise HTTPException(500, f"screenshot failed: {e}")
    if shot is None:
        raise HTTPException(503, "screenshot returned None")
    ok, buf = cv2.imencode(".jpg", shot, [cv2.IMWRITE_JPEG_QUALITY, max(10, min(100, q))])
    if not ok:
        raise HTTPException(500, "imencode failed")
    return Response(buf.tobytes(), media_type="image/jpeg")


@app.get("/api/sysinfo")
async def api_sysinfo():
    """整机指标 + 时间戳，记录模态每帧附带"""
    return {**_sys_snapshot(), "ts": time.time()}


@app.get("/api/rules")
async def api_rules():
    """返回当前 popup_rules.json"""
    from .automation.rules_loader import RulesLoader
    return {
        "path": RulesLoader.path(),
        "rules": RulesLoader.get(),
    }


class AddKeywordReq(BaseModel):
    field: str
    text: str


@app.post("/api/add_keyword")
async def api_add_keyword(req: AddKeywordReq):
    """追加关键字到指定 field，写盘 → 下一轮 dismiss_popups 自动 reload"""
    from .automation.rules_loader import RulesLoader, DEFAULTS
    path = RulesLoader.path()
    if path is None:
        raise HTTPException(500, "popup_rules.json 路径解析失败")
    if req.field not in DEFAULTS:
        raise HTTPException(400, f"未知 field {req.field}，可选: {list(DEFAULTS.keys())}")
    text = req.text.strip()
    if not text:
        raise HTTPException(400, "text 不能为空")
    try:
        with open(path, "r", encoding="utf-8") as f:
            rules = json.load(f)
    except FileNotFoundError:
        rules = {}
    if req.field not in rules or not isinstance(rules[req.field], list):
        rules[req.field] = list(DEFAULTS[req.field])
    if text in rules[req.field]:
        return {"ok": True, "field": req.field, "text": text, "already_present": True}
    rules[req.field].append(text)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rules, f, ensure_ascii=False, indent=2)
    except Exception as e:
        raise HTTPException(500, f"写 {path} 失败: {e}")
    logger.info(f"[debug] add keyword: {req.field}.append({text!r}) -> {path}")
    return {"ok": True, "field": req.field, "text": text, "list_len": len(rules[req.field])}


# ─────────── 标注器（YOLO 训练数据） ───────────

# 类别定义（v1）—— 简化为 2 类，dialog 不再要求标（人标不一致，对 bot 操作也没意义）
# 兼容历史：之前标了 dialog 的 .txt 仍能读，训练时由训练脚本决定是否使用第 3 类
LABEL_CLASSES = ["close_x", "action_btn"]


def _yolo_paths():
    """返回 (raw_dir, labels_dir, classes_path)"""
    from .automation.user_paths import user_yolo_dir
    root = user_yolo_dir()
    raw = root / "raw_screenshots"
    labels = root / "labels"
    classes_p = root / "classes.txt"
    if not classes_p.exists():
        classes_p.write_text("\n".join(LABEL_CLASSES) + "\n", encoding="utf-8")
    return raw, labels, classes_p


def _label_path_for(image_filename: str):
    """对应图片的 .txt label 路径"""
    _, labels_dir, _ = _yolo_paths()
    stem = os.path.splitext(image_filename)[0]
    return labels_dir / f"{stem}.txt"


@app.get("/labeler", response_class=HTMLResponse)
async def labeler_page():
    # 禁用浏览器缓存，确保用户始终拿最新版（修 bug 后立刻生效）
    return HTMLResponse(
        content=LABELER_HTML,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.get("/api/labeler/list")
async def api_labeler_list():
    """所有原始截图 + 标注状态 + 每类 bbox 实例数"""
    raw, labels_dir, _ = _yolo_paths()
    items = []
    # 每类实例数 (cid -> count) + 出现在多少张图 (cid -> image_set)
    class_counts: dict[int, int] = {}
    class_imgs: dict[int, set[str]] = {}
    for p in sorted(raw.glob("*.png")) + sorted(raw.glob("*.jpg")):
        label_p = labels_dir / f"{p.stem}.txt"
        labeled = label_p.is_file() and label_p.stat().st_size > 0
        skipped = label_p.is_file() and label_p.stat().st_size == 0
        items.append({
            "name": p.name,
            "size": p.stat().st_size,
            "mtime": p.stat().st_mtime,
            "labeled": labeled,
            "skipped": skipped,
        })
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
            except Exception:
                pass
    items.sort(key=lambda x: x["mtime"])
    n_labeled = sum(1 for i in items if i["labeled"])
    n_skipped = sum(1 for i in items if i["skipped"])

    # 组装 per_class: 当前类（active）+ 历史遗留类（如 dialog idx=2）都返回
    per_class = []
    all_cids = sorted(set(list(class_counts.keys()) + list(range(len(LABEL_CLASSES)))))
    legacy_names = {2: "dialog (历史)"}  # 兼容：之前标过 dialog 的，仍显示数字
    for cid in all_cids:
        if cid < len(LABEL_CLASSES):
            name = LABEL_CLASSES[cid]
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
        "classes": LABEL_CLASSES,
        "per_class": per_class,
        "items": items,
    }


@app.get("/api/labeler/image/{filename}")
async def api_labeler_image(filename: str):
    raw, _, _ = _yolo_paths()
    p = raw / _safe_id(filename.replace(".png", ""))
    # 重新拼回扩展名
    for ext in (".png", ".jpg"):
        full = raw / (os.path.splitext(filename)[0] + ext)
        if full.is_file():
            return FileResponse(full, media_type=f"image/{ext[1:]}")
    raise HTTPException(404)


@app.get("/api/labeler/labels/{filename}")
async def api_labeler_get_labels(filename: str):
    """返回 [{class_id, cx, cy, w, h}, ...]"""
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


class SaveLabelsReq(BaseModel):
    boxes: list


@app.post("/api/labeler/labels/{filename}")
async def api_labeler_save_labels(filename: str, req: SaveLabelsReq):
    """保存 YOLO 格式 .txt（boxes 为空 → 空文件，标记为已跳过）"""
    label_p = _label_path_for(filename)
    label_p.parent.mkdir(parents=True, exist_ok=True)
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
        if cid < 0 or cid >= len(LABEL_CLASSES):
            continue
        if w <= 0 or h <= 0:
            continue
        lines.append(f"{cid} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}")
    label_p.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return {"ok": True, "count": len(lines), "path": str(label_p)}


@app.delete("/api/labeler/image/{filename}")
async def api_labeler_delete_image(filename: str):
    """废弃图片（移到 .trash/ 子目录）"""
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


@app.get("/api/log/tail")
async def api_log_tail(n: int = 100):
    """读当前 session 的 run.log 最后 N 行"""
    if not _session_dir:
        return {"lines": [], "session_dir": None}
    log_path = os.path.join(_session_dir, "run.log")
    if not os.path.isfile(log_path):
        return {"lines": [], "session_dir": _session_dir, "error": "run.log not found"}
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        return {"lines": all_lines[-n:], "session_dir": _session_dir}
    except Exception as e:
        return {"lines": [], "error": str(e)}


# ─────────── 记录持久化 ───────────


def _decode_data_url(s: str) -> Optional[bytes]:
    """data:image/png;base64,xxx -> bytes"""
    m = re.match(r"^data:image/[a-z]+;base64,(.+)$", s, re.I)
    if not m:
        return None
    try:
        return base64.b64decode(m.group(1))
    except Exception:
        return None


@app.post("/api/records")
async def api_create_record(payload: dict):
    """
    新建记录。payload:
      {
        "idx": 0,
        "note": "用户文本备注",
        "frames": [
          {
            "data_url": "data:image/jpeg;base64,...",   原始帧
            "annotation_data_url": "data:image/png;base64,..." | None,  画布批注（透明）
            "ts": 12345.678,                              帧时间戳
            "sys": {...}                                  当时整机指标
          },
          ...
        ]
      }
    返回 {"ok": True, "id": "20260426_162345_001"}
    """
    root = _records_root()
    if root is None:
        raise HTTPException(500, "no active session, start runners first")
    idx = payload.get("idx")
    if idx is None:
        raise HTTPException(400, "idx required")
    note = (payload.get("note") or "").strip()
    frames = payload.get("frames") or []
    if not frames:
        raise HTTPException(400, "frames empty")

    ts_now = datetime.now()
    rec_id = ts_now.strftime("%Y%m%d_%H%M%S_") + _safe_id(f"i{idx}")
    rec_dir = os.path.join(root, rec_id)
    os.makedirs(os.path.join(rec_dir, "frames"), exist_ok=True)
    os.makedirs(os.path.join(rec_dir, "annotations"), exist_ok=True)

    # 写 note.txt
    if note:
        with open(os.path.join(rec_dir, "note.txt"), "w", encoding="utf-8") as f:
            f.write(note)

    frame_meta = []
    for i, fr in enumerate(frames):
        data_url = fr.get("data_url") or ""
        raw = _decode_data_url(data_url)
        if raw is None:
            continue
        with open(os.path.join(rec_dir, "frames", f"{i:02d}.jpg"), "wb") as f:
            f.write(raw)
        ann_url = fr.get("annotation_data_url")
        ann_path = None
        if ann_url:
            ann_raw = _decode_data_url(ann_url)
            if ann_raw:
                ann_path = os.path.join(rec_dir, "annotations", f"{i:02d}.png")
                with open(ann_path, "wb") as f:
                    f.write(ann_raw)
        frame_meta.append({
            "i": i,
            "ts": fr.get("ts"),
            "sys": fr.get("sys") or {},
            "annotated": ann_path is not None,
        })

    # 写 meta.json
    meta = {
        "id": rec_id,
        "idx": idx,
        "ts_clicked": ts_now.timestamp(),
        "ts_clicked_human": ts_now.strftime("%Y-%m-%d %H:%M:%S"),
        "instance": _instance_snapshot(idx),
        "frames": frame_meta,
        "note_len": len(note),
    }
    with open(os.path.join(rec_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    logger.info(f"[debug] new record {rec_id} idx={idx} frames={len(frame_meta)} note_len={len(note)}")
    return {"ok": True, "id": rec_id, "frames": len(frame_meta)}


@app.get("/api/records")
async def api_list_records(limit: int = 50):
    """列出所有记录（按时间倒序，最近在前）"""
    root = _records_root()
    if root is None:
        return {"records": [], "session_dir": None}
    out = []
    try:
        names = sorted(os.listdir(root), reverse=True)
    except Exception:
        names = []
    for name in names[:limit]:
        d = os.path.join(root, name)
        meta_p = os.path.join(d, "meta.json")
        if not os.path.isfile(meta_p):
            continue
        try:
            with open(meta_p, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            continue
        note = ""
        note_p = os.path.join(d, "note.txt")
        if os.path.isfile(note_p):
            try:
                with open(note_p, "r", encoding="utf-8") as f:
                    note = f.read()
            except Exception:
                pass
        out.append({
            "id": meta.get("id", name),
            "idx": meta.get("idx"),
            "ts_clicked": meta.get("ts_clicked"),
            "ts_clicked_human": meta.get("ts_clicked_human"),
            "frame_count": len(meta.get("frames") or []),
            "instance": meta.get("instance", {}),
            "note": note,
        })
    return {"records": out, "session_dir": _session_dir, "count": len(out)}


@app.get("/api/record/{rec_id}")
async def api_get_record(rec_id: str):
    """单条记录详情（meta + note，不含图片二进制）"""
    root = _records_root()
    if root is None:
        raise HTTPException(404, "no active session")
    rec_id = _safe_id(rec_id)
    d = os.path.join(root, rec_id)
    if not os.path.isdir(d):
        raise HTTPException(404, "record not found")
    meta_p = os.path.join(d, "meta.json")
    if not os.path.isfile(meta_p):
        raise HTTPException(404, "meta.json missing")
    with open(meta_p, "r", encoding="utf-8") as f:
        meta = json.load(f)
    note = ""
    note_p = os.path.join(d, "note.txt")
    if os.path.isfile(note_p):
        with open(note_p, "r", encoding="utf-8") as f:
            note = f.read()
    return {**meta, "note": note}


@app.get("/api/record/{rec_id}/frame/{i}.jpg")
async def api_record_frame(rec_id: str, i: int):
    root = _records_root()
    if root is None:
        raise HTTPException(404)
    p = os.path.join(root, _safe_id(rec_id), "frames", f"{i:02d}.jpg")
    if not os.path.isfile(p):
        raise HTTPException(404)
    return FileResponse(p, media_type="image/jpeg")


@app.get("/api/record/{rec_id}/annotation/{i}.png")
async def api_record_annot(rec_id: str, i: int):
    root = _records_root()
    if root is None:
        raise HTTPException(404)
    p = os.path.join(root, _safe_id(rec_id), "annotations", f"{i:02d}.png")
    if not os.path.isfile(p):
        raise HTTPException(404)
    return FileResponse(p, media_type="image/png")


# ─────────── HTML page ───────────

HTML_PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>GameBot Debug</title>
<style>
  * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
  html, body { margin: 0; padding: 0; height: 100%; }
  body {
    font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
    background: #0f1115; color: #e3e6eb;
    font-size: 13px;
  }
  .topbar {
    position: sticky; top: 0; z-index: 10;
    background: #161a21; border-bottom: 1px solid #252a33;
    padding: 10px 16px; display: flex; align-items: center; gap: 16px;
  }
  .topbar h1 { margin: 0; font-size: 16px; font-weight: 600; color: #6fa8ff; }
  .topbar .meta { color: #8b95a5; font-size: 12px; }
  .topbar .right { margin-left: auto; display: flex; gap: 8px; }
  .topbar button {
    background: #2a3140; color: #e3e6eb; border: 1px solid #3a4252;
    padding: 6px 12px; border-radius: 6px; cursor: pointer; font-size: 13px;
  }
  .topbar button:hover { background: #3a4252; }
  .topbar button.primary { background: #3b6fd1; border-color: #4a82e8; }
  .topbar button.primary:hover { background: #4a82e8; }

  .sysbar {
    background: #1a1f28; padding: 8px 16px; border-bottom: 1px solid #252a33;
    display: flex; gap: 24px; font-size: 12px; color: #aab2bf;
  }
  .sysbar .stat { display: flex; gap: 6px; align-items: center; }
  .sysbar .stat-label { color: #6e7685; }
  .sysbar .stat-val { color: #e3e6eb; font-weight: 500; }
  .pulse { width: 8px; height: 8px; border-radius: 50%; background: #4ade80;
           animation: pulse 1.4s infinite; }
  .pulse.off { background: #6e7685; animation: none; }
  @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }

  .grid {
    display: grid; gap: 12px; padding: 12px;
    grid-template-columns: repeat(auto-fit, minmax(380px, 1fr));
  }
  .card {
    background: #1a1f28; border: 1px solid #252a33; border-radius: 8px;
    overflow: hidden; display: flex; flex-direction: column;
  }
  .card-head {
    padding: 10px 12px; display: flex; align-items: center; gap: 10px;
    border-bottom: 1px solid #252a33; background: #1f2530;
  }
  .card-title { font-weight: 600; color: #e3e6eb; font-size: 14px; }
  .card-sub { color: #8b95a5; font-size: 12px; }
  .badge {
    padding: 2px 10px; border-radius: 999px; font-size: 11px; font-weight: 500;
    background: #2a3140; color: #aab2bf;
  }
  .badge.ok { background: #1e4034; color: #4ade80; }
  .badge.warn { background: #3d2f1a; color: #fbbf24; }
  .badge.err { background: #3d1d1d; color: #ef4444; }
  .badge.run { background: #1f3550; color: #6fa8ff; }
  .card-actions { margin-left: auto; display: flex; gap: 6px; }
  .btn-icon {
    background: #2a3140; border: 1px solid #3a4252; color: #e3e6eb;
    padding: 5px 10px; border-radius: 5px; cursor: pointer; font-size: 12px;
  }
  .btn-icon:hover { background: #3a4252; border-color: #4a5262; }
  .btn-icon.record { background: #3b6fd1; border-color: #4a82e8; color: #fff; }
  .btn-icon.record:hover { background: #4a82e8; }

  .card-body { position: relative; background: #000; }
  .card-body img {
    width: 100%; display: block; max-height: 280px; object-fit: contain;
    cursor: zoom-in;
  }
  .card-body .ph {
    position: absolute; top: 8px; left: 8px;
    background: rgba(15,17,21,0.7); padding: 3px 8px;
    border-radius: 4px; font-size: 11px; color: #aab2bf; backdrop-filter: blur(4px);
  }
  .card-foot {
    padding: 8px 12px; font-size: 11px; color: #8b95a5;
    border-top: 1px solid #252a33; min-height: 28px;
  }

  /* keyword drawer */
  .drawer-bg {
    position: fixed; inset: 0; background: rgba(0,0,0,0.6);
    display: none; z-index: 50;
  }
  .drawer-bg.on { display: block; }
  .drawer {
    position: fixed; right: 0; top: 0; bottom: 0; width: 420px;
    background: #161a21; border-left: 1px solid #252a33;
    z-index: 51; transform: translateX(100%); transition: transform 0.2s;
    display: flex; flex-direction: column;
  }
  .drawer.on { transform: translateX(0); }
  .drawer-head {
    padding: 14px 16px; border-bottom: 1px solid #252a33;
    display: flex; align-items: center;
  }
  .drawer-head h2 { margin: 0; font-size: 15px; }
  .drawer-head .close {
    margin-left: auto; background: none; border: none; color: #8b95a5;
    font-size: 20px; cursor: pointer;
  }
  .drawer-body { padding: 14px 16px; overflow-y: auto; flex: 1; }
  .field-tabs { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; }
  .field-tab {
    background: #2a3140; border: 1px solid #3a4252; color: #aab2bf;
    padding: 6px 10px; border-radius: 5px; cursor: pointer; font-size: 12px;
  }
  .field-tab.on { background: #3b6fd1; border-color: #4a82e8; color: #fff; }
  .input-row { display: flex; gap: 8px; margin-bottom: 10px; }
  .input-row input {
    flex: 1; background: #0f1115; border: 1px solid #3a4252; color: #e3e6eb;
    padding: 8px 10px; border-radius: 5px; font-size: 13px;
  }
  .input-row button {
    background: #3b6fd1; color: #fff; border: none;
    padding: 8px 16px; border-radius: 5px; cursor: pointer; font-size: 13px;
  }
  .kw-status { color: #4ade80; font-size: 12px; min-height: 18px; }
  .kw-list { font-size: 12px; color: #aab2bf; max-height: 260px; overflow-y: auto;
             background: #0f1115; padding: 8px; border-radius: 5px; }
  .kw-list .kw { display: inline-block; background: #2a3140; padding: 2px 8px;
                 border-radius: 3px; margin: 2px; }

  /* records drawer */
  .rec-list { display: flex; flex-direction: column; gap: 8px; }
  .rec-item { background: #1a1f28; border: 1px solid #252a33; border-radius: 6px;
              padding: 10px; cursor: pointer; }
  .rec-item:hover { border-color: #4a5262; }
  .rec-item .head { display: flex; justify-content: space-between; align-items: center;
                    margin-bottom: 6px; }
  .rec-item .id { color: #8b95a5; font-size: 11px; font-family: monospace; }
  .rec-item .badge { font-size: 10px; }
  .rec-item .note { font-size: 12px; color: #d3d8df; line-height: 1.5;
                    white-space: pre-wrap; word-break: break-word; }
  .rec-item .empty-note { color: #6e7685; font-style: italic; }

  /* record modal */
  .modal-bg {
    position: fixed; inset: 0; background: rgba(0,0,0,0.85);
    display: none; z-index: 100;
    justify-content: center; align-items: center;
  }
  .modal-bg.on { display: flex; }
  .modal {
    background: #161a21; border: 1px solid #252a33; border-radius: 10px;
    width: 95vw; height: 92vh; display: flex; flex-direction: column;
    overflow: hidden;
  }
  .modal-head {
    padding: 12px 16px; border-bottom: 1px solid #252a33;
    display: flex; align-items: center; gap: 12px; background: #1a1f28;
  }
  .modal-head h2 { margin: 0; font-size: 15px; }
  .modal-head .close {
    margin-left: auto; background: none; border: none; color: #8b95a5;
    font-size: 22px; cursor: pointer; padding: 0 8px;
  }
  .modal-body { flex: 1; display: flex; min-height: 0; }
  .modal-canvas-wrap {
    flex: 1; background: #000; position: relative; overflow: hidden;
    display: flex; align-items: center; justify-content: center;
  }
  .modal-canvas-wrap img, .modal-canvas-wrap canvas {
    max-width: 100%; max-height: 100%; display: block; position: absolute;
  }
  .modal-canvas-wrap img { z-index: 1; }
  .modal-canvas-wrap canvas { z-index: 2; cursor: crosshair; }

  .modal-side {
    width: 320px; background: #1a1f28; border-left: 1px solid #252a33;
    display: flex; flex-direction: column; min-height: 0;
  }
  .modal-side .section { padding: 12px 14px; border-bottom: 1px solid #252a33; }
  .modal-side .section h3 { margin: 0 0 8px; font-size: 12px;
                            color: #8b95a5; font-weight: 600; text-transform: uppercase;
                            letter-spacing: 0.5px; }
  .modal-side .meta-row { display: flex; justify-content: space-between;
                          padding: 3px 0; font-size: 12px; }
  .modal-side .meta-row .k { color: #8b95a5; }
  .modal-side .meta-row .v { color: #e3e6eb; font-family: monospace; }
  .modal-side textarea {
    width: 100%; background: #0f1115; border: 1px solid #3a4252; color: #e3e6eb;
    padding: 10px; border-radius: 5px; font-size: 13px; min-height: 100px;
    resize: vertical; font-family: inherit;
  }
  .modal-side .save-bar { padding: 12px 14px; margin-top: auto; display: flex; gap: 8px; }
  .modal-side .save-bar button {
    flex: 1; background: #3b6fd1; color: #fff; border: none;
    padding: 10px; border-radius: 5px; cursor: pointer; font-size: 13px;
  }
  .modal-side .save-bar button:hover { background: #4a82e8; }
  .modal-side .save-bar button.cancel { background: #2a3140; }
  .modal-side .save-bar button.cancel:hover { background: #3a4252; }

  /* tools bar */
  .tools-bar {
    position: absolute; left: 12px; top: 12px; z-index: 5;
    display: flex; flex-direction: column; gap: 4px;
    background: rgba(22,26,33,0.92); padding: 6px; border-radius: 6px;
    backdrop-filter: blur(8px);
  }
  .tools-bar button {
    background: #2a3140; border: 1px solid #3a4252; color: #e3e6eb;
    padding: 6px 8px; border-radius: 4px; cursor: pointer; font-size: 11px;
    width: 60px; text-align: center;
  }
  .tools-bar button.on { background: #3b6fd1; border-color: #4a82e8; }
  .tools-bar button:hover { background: #3a4252; }
  .color-row { display: flex; gap: 3px; margin: 4px 0; }
  .color-dot {
    width: 16px; height: 16px; border-radius: 50%; cursor: pointer;
    border: 2px solid transparent;
  }
  .color-dot.on { border-color: #fff; }

  .frame-nav {
    position: absolute; bottom: 12px; left: 50%; transform: translateX(-50%);
    background: rgba(22,26,33,0.92); padding: 8px 16px; border-radius: 6px;
    display: flex; gap: 16px; align-items: center; z-index: 5;
    backdrop-filter: blur(8px);
  }
  .frame-nav button {
    background: #2a3140; border: 1px solid #3a4252; color: #e3e6eb;
    padding: 6px 14px; border-radius: 4px; cursor: pointer;
  }
  .frame-nav button:disabled { opacity: 0.3; cursor: not-allowed; }
  .frame-nav .label { color: #aab2bf; font-size: 12px; min-width: 80px; text-align: center; }
  .frame-nav .dots { display: flex; gap: 6px; }
  .frame-nav .dot {
    width: 8px; height: 8px; border-radius: 50%; background: #3a4252; cursor: pointer;
  }
  .frame-nav .dot.on { background: #6fa8ff; }

  .toast {
    position: fixed; bottom: 24px; right: 24px; z-index: 200;
    background: #1a1f28; border: 1px solid #4ade80; color: #4ade80;
    padding: 10px 16px; border-radius: 6px; font-size: 13px;
    animation: slideIn 0.2s;
  }
  @keyframes slideIn { from { transform: translateX(40px); opacity: 0; }
                       to { transform: translateX(0); opacity: 1; } }
</style>
</head>
<body>

<div class="topbar">
  <h1>GameBot Debug</h1>
  <span class="meta" id="topbar-meta">session: -</span>
  <div class="right">
    <button onclick="window.open('/labeler','_blank')">YOLO 标注</button>
    <button onclick="openRecords()">历史记录 <span id="rec-count">(0)</span></button>
    <button onclick="openKeywords()">添加关键字</button>
  </div>
</div>

<div class="sysbar" id="sysbar">
  <div class="stat"><span class="pulse off" id="pulse"></span>
    <span class="stat-label">runner:</span><span class="stat-val" id="run-state">-</span></div>
  <div class="stat"><span class="stat-label">CPU:</span><span class="stat-val" id="sys-cpu">-</span></div>
  <div class="stat"><span class="stat-label">内存:</span><span class="stat-val" id="sys-mem">-</span></div>
  <div class="stat"><span class="stat-label">实例:</span><span class="stat-val" id="sys-inst">-</span></div>
</div>

<div class="grid" id="grid"></div>

<!-- 关键字抽屉 -->
<div class="drawer-bg" id="kw-bg" onclick="closeKeywords()"></div>
<div class="drawer" id="kw-drawer">
  <div class="drawer-head">
    <h2>添加弹窗关键字</h2>
    <button class="close" onclick="closeKeywords()">×</button>
  </div>
  <div class="drawer-body">
    <div style="color:#8b95a5;font-size:12px;margin-bottom:12px;line-height:1.6;">
      添加后写入 popup_rules.json，下一轮 dismiss_popups 自动 reload，无需重启。
    </div>
    <div class="field-tabs" id="kw-tabs"></div>
    <div class="input-row">
      <input id="kw-text" placeholder="输入关键字…" onkeydown="if(event.key==='Enter')addKw()">
      <button onclick="addKw()">添加</button>
    </div>
    <div class="kw-status" id="kw-status"></div>
    <h3 style="margin:14px 0 6px;font-size:12px;color:#8b95a5;">当前列表</h3>
    <div class="kw-list" id="kw-list">loading…</div>
  </div>
</div>

<!-- 历史记录抽屉 -->
<div class="drawer-bg" id="rec-bg" onclick="closeRecords()"></div>
<div class="drawer" id="rec-drawer">
  <div class="drawer-head">
    <h2>历史记录</h2>
    <button class="close" onclick="closeRecords()">×</button>
  </div>
  <div class="drawer-body">
    <div class="rec-list" id="rec-list">loading…</div>
  </div>
</div>

<!-- 记录模态 -->
<div class="modal-bg" id="modal-bg">
  <div class="modal">
    <div class="modal-head">
      <h2 id="modal-title">实例 #-</h2>
      <span class="meta" id="modal-sub" style="color:#8b95a5;font-size:12px;"></span>
      <button class="close" onclick="closeModal()">×</button>
    </div>
    <div class="modal-body">
      <div class="modal-canvas-wrap" id="canvas-wrap">
        <div class="tools-bar" id="tools-bar">
          <button class="tool on" data-tool="pen">笔</button>
          <button class="tool" data-tool="rect">矩形</button>
          <button class="tool" data-tool="circle">圆</button>
          <div class="color-row" id="color-row"></div>
          <button onclick="undoStroke()">撤销</button>
          <button onclick="clearAnnot()">清空</button>
        </div>
        <img id="modal-img" alt="">
        <canvas id="modal-canvas"></canvas>
        <div class="frame-nav">
          <button id="prev-btn" onclick="navFrame(-1)">‹ 上一张</button>
          <div class="label" id="frame-label">- / -</div>
          <div class="dots" id="frame-dots"></div>
          <button id="next-btn" onclick="navFrame(1)">下一张 ›</button>
        </div>
      </div>
      <div class="modal-side">
        <div class="section">
          <h3>当前帧信息</h3>
          <div id="frame-meta"></div>
        </div>
        <div class="section">
          <h3>实例状态</h3>
          <div id="inst-meta"></div>
        </div>
        <div class="section" style="flex:1;display:flex;flex-direction:column;">
          <h3>备注（共享 · 所有帧）</h3>
          <textarea id="note-text" placeholder="描述这次记录的情况、问题、要复现的步骤…"></textarea>
        </div>
        <div class="save-bar">
          <button class="cancel" onclick="closeModal()">取消</button>
          <button onclick="saveRecord()">保存记录</button>
        </div>
      </div>
    </div>
  </div>
</div>

<script>
// ─────────── state ───────────
const FIELDS = [
  {k:'close_text', label:'关闭按钮文字'},
  {k:'confirm_text', label:'确认/同意/跳过'},
  {k:'checkbox_text', label:'今日不再弹出'},
  {k:'lobby_keywords', label:'大厅判定'},
  {k:'loading_keywords', label:'加载中判定'},
  {k:'login_keywords', label:'登录页判定'},
  {k:'left_game_keywords', label:'退游判定'},
];
let kwField = 'close_text';

// 每实例的帧 ring buffer：{idx: [{blob, ts, sys}, ...]} 最近 3 张
const ringBuf = {};
const RING_SIZE = 3;

// 模态状态
let modalIdx = null;
let modalFrames = [];   // [{blobUrl, ts, sys, strokes: []}]
let modalCurFrame = 0;
let modalInst = null;

// 画布工具
let curTool = 'pen';
let curColor = '#ff3b3b';
const COLORS = ['#ff3b3b', '#ffd23f', '#4ade80', '#6fa8ff', '#ffffff'];
let canvasDrawing = false;
let canvasStart = null;

// ─────────── grid 实时刷新（in-place，不重建 DOM） ───────────

async function refreshStatus() {
  let data;
  try {
    const r = await fetch('/api/status');
    data = await r.json();
  } catch(e) {
    document.getElementById('topbar-meta').textContent = 'API 失败: ' + e.message;
    return;
  }
  // sysbar
  document.getElementById('topbar-meta').textContent =
    'session: ' + (data.session_dir ? data.session_dir.split(/[\\\/]/).pop() : '-');
  document.getElementById('run-state').textContent = data.running ? '运行中' : '空闲';
  document.getElementById('pulse').classList.toggle('off', !data.running);
  const sys = data.sys || {};
  document.getElementById('sys-cpu').textContent = (sys.cpu_percent ?? '-') + '%';
  document.getElementById('sys-mem').textContent =
    (sys.mem_used_mb ?? '-') + ' / ' + (sys.mem_total_mb ?? '-') + ' MB ('
    + (sys.mem_percent ?? '-') + '%)';
  document.getElementById('sys-inst').textContent = (data.instances || []).length;

  // grid 就地更新
  const grid = document.getElementById('grid');
  const known = new Set();
  (data.instances || []).forEach(inst => {
    known.add(inst.idx);
    let card = document.getElementById('card-' + inst.idx);
    if (!card) {
      card = makeCard(inst);
      grid.appendChild(card);
    }
    updateCard(card, inst);
  });
  // 移除消失的
  grid.querySelectorAll('.card').forEach(c => {
    const idx = parseInt(c.dataset.idx);
    if (!known.has(idx)) c.remove();
  });
  if (grid.children.length === 0) {
    grid.innerHTML = '<div style="grid-column:1/-1;text-align:center;color:#6e7685;padding:48px;">'
      + '没有运行中的实例。在 GameBot 主界面点开始运行。</div>';
  }
}

function makeCard(inst) {
  const card = document.createElement('div');
  card.className = 'card';
  card.id = 'card-' + inst.idx;
  card.dataset.idx = inst.idx;
  card.innerHTML = `
    <div class="card-head">
      <div>
        <div class="card-title">实例 #${inst.idx}</div>
        <div class="card-sub" data-role>${inst.group||'?'} / ${inst.role||'?'}</div>
      </div>
      <span class="badge" data-state>-</span>
      <div class="card-actions">
        <button class="btn-icon record" onclick="openRecord(${inst.idx})">记录</button>
      </div>
    </div>
    <div class="card-body">
      <div class="ph" data-phase>-</div>
      <img alt="loading" data-img onclick="zoomCard(this)">
    </div>
    <div class="card-foot" data-stages>-</div>
  `;
  return card;
}

function updateCard(card, inst) {
  const role = card.querySelector('[data-role]');
  role.textContent = (inst.group||'?') + ' / ' + (inst.role||'?');
  const badge = card.querySelector('[data-state]');
  const state = inst.state || inst.phase || '-';
  badge.textContent = state;
  badge.className = 'badge ' + classifyState(state, inst.error);
  card.querySelector('[data-phase]').textContent = inst.phase || '-';
  const stages = inst.stage_times || {};
  const stagesStr = Object.entries(stages)
    .map(([k,v]) => k + '=' + v.toFixed(0) + 's').join(' · ') || '-';
  card.querySelector('[data-stages]').textContent = stagesStr;
  // 截图：fetch blob → 推 ring buffer + 替 src
  fetchAndCache(inst.idx, card.querySelector('[data-img]'));
}

function classifyState(state, error) {
  if (error) return 'err';
  if (state === 'done') return 'ok';
  if (state === 'init' || state === 'idle' || state === '-') return '';
  return 'run';
}

async function fetchAndCache(idx, imgEl) {
  try {
    const [shotR, sysR] = await Promise.all([
      fetch('/api/screenshot/' + idx + '.jpg?t=' + Date.now()),
      fetch('/api/sysinfo'),
    ]);
    if (!shotR.ok) {
      imgEl.alt = 'shot ' + shotR.status;
      return;
    }
    const blob = await shotR.blob();
    const sys = sysR.ok ? await sysR.json() : {};
    // 推 ring buffer
    if (!ringBuf[idx]) ringBuf[idx] = [];
    ringBuf[idx].push({blob, ts: Date.now()/1000, sys});
    if (ringBuf[idx].length > RING_SIZE) ringBuf[idx].shift();
    // 替 src（in-place，不重建 img 节点 → 保持滚动/zoom 状态）
    if (imgEl._lastUrl) URL.revokeObjectURL(imgEl._lastUrl);
    imgEl._lastUrl = URL.createObjectURL(blob);
    imgEl.src = imgEl._lastUrl;
  } catch(e) { /* ignore */ }
}

function zoomCard(img) {
  const w = window.open('', '_blank');
  w.document.write('<body style="margin:0;background:#000;display:flex;align-items:center;justify-content:center;">'
    + '<img src="' + img.src + '" style="max-width:100%;max-height:100vh;"></body>');
}

// ─────────── 关键字抽屉 ───────────

function openKeywords() {
  document.getElementById('kw-bg').classList.add('on');
  document.getElementById('kw-drawer').classList.add('on');
  renderFieldTabs();
  loadRules();
}
function closeKeywords() {
  document.getElementById('kw-bg').classList.remove('on');
  document.getElementById('kw-drawer').classList.remove('on');
}
function renderFieldTabs() {
  const c = document.getElementById('kw-tabs');
  c.innerHTML = FIELDS.map(f =>
    `<div class="field-tab ${f.k===kwField?'on':''}" onclick="selectField('${f.k}')">${f.label}</div>`
  ).join('');
}
function selectField(k) {
  kwField = k;
  renderFieldTabs();
  loadRules();
}
async function loadRules() {
  const list = document.getElementById('kw-list');
  try {
    const r = await fetch('/api/rules');
    const j = await r.json();
    const arr = (j.rules || {})[kwField] || [];
    list.innerHTML = arr.length
      ? arr.map(t => '<span class="kw">' + escapeHtml(t) + '</span>').join('')
      : '<span style="color:#6e7685;">（空）</span>';
  } catch(e) { list.textContent = '加载失败: ' + e.message; }
}
async function addKw() {
  const text = document.getElementById('kw-text').value.trim();
  if (!text) return;
  const status = document.getElementById('kw-status');
  status.textContent = '保存中…';
  status.style.color = '#aab2bf';
  try {
    const r = await fetch('/api/add_keyword', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({field: kwField, text}),
    });
    const j = await r.json();
    if (j.ok) {
      status.textContent = j.already_present
        ? '已存在' : '已添加（共 ' + j.list_len + ' 条），下一轮自动生效';
      status.style.color = '#4ade80';
      document.getElementById('kw-text').value = '';
      loadRules();
      setTimeout(() => status.textContent = '', 4000);
    } else {
      status.textContent = '失败: ' + JSON.stringify(j);
      status.style.color = '#ef4444';
    }
  } catch(e) {
    status.textContent = '失败: ' + e.message;
    status.style.color = '#ef4444';
  }
}

// ─────────── 历史记录抽屉 ───────────

async function openRecords() {
  document.getElementById('rec-bg').classList.add('on');
  document.getElementById('rec-drawer').classList.add('on');
  await loadRecords();
}
function closeRecords() {
  document.getElementById('rec-bg').classList.remove('on');
  document.getElementById('rec-drawer').classList.remove('on');
}
async function loadRecords() {
  const c = document.getElementById('rec-list');
  c.innerHTML = 'loading…';
  try {
    const r = await fetch('/api/records?limit=100');
    const j = await r.json();
    document.getElementById('rec-count').textContent = '(' + (j.count||0) + ')';
    if (!j.records || j.records.length === 0) {
      c.innerHTML = '<div style="color:#6e7685;text-align:center;padding:24px;">还没有记录</div>';
      return;
    }
    c.innerHTML = j.records.map(rec => `
      <div class="rec-item">
        <div class="head">
          <span><span class="badge run">实例 #${rec.idx}</span></span>
          <span class="id">${rec.id}</span>
        </div>
        <div style="color:#8b95a5;font-size:11px;margin-bottom:6px;">
          ${rec.ts_clicked_human || ''} · ${rec.frame_count} 帧
          · phase=${(rec.instance||{}).phase||'-'}
        </div>
        <div class="note ${rec.note?'':'empty-note'}">${rec.note ? escapeHtml(rec.note) : '（无备注）'}</div>
      </div>
    `).join('');
  } catch(e) {
    c.innerHTML = '加载失败: ' + e.message;
  }
}

// ─────────── 记录模态 ───────────

async function openRecord(idx) {
  modalIdx = idx;
  modalCurFrame = 0;
  modalFrames = [];
  modalInst = null;

  // 1. 从 ring buffer 拿过去 1.5s 的帧
  const past = (ringBuf[idx] || []).slice();
  for (const e of past) {
    modalFrames.push({
      blob: e.blob,
      blobUrl: URL.createObjectURL(e.blob),
      ts: e.ts, sys: e.sys, strokes: [],
    });
  }
  // 2. 立即取一张（点击瞬间）
  modalFrames.push(await captureNow(idx));
  // 3. 拿当前实例 meta
  try {
    const r = await fetch('/api/status');
    const j = await r.json();
    modalInst = (j.instances || []).find(i => i.idx === idx);
  } catch(e) {}

  // 打开模态
  document.getElementById('modal-bg').classList.add('on');
  document.getElementById('modal-title').textContent = '实例 #' + idx + ' · 记录';
  document.getElementById('note-text').value = '';
  renderColorRow();
  bindToolButtons();
  bindCanvas();
  renderModalFrame();

  // 4. 后台再抓 2 帧（+0.7s 和 +1.4s）
  setTimeout(async () => {
    modalFrames.push(await captureNow(idx));
    renderFrameDots();
  }, 700);
  setTimeout(async () => {
    modalFrames.push(await captureNow(idx));
    renderFrameDots();
  }, 1400);
}

async function captureNow(idx) {
  try {
    const [shotR, sysR] = await Promise.all([
      fetch('/api/screenshot/' + idx + '.jpg?t=' + Date.now()),
      fetch('/api/sysinfo'),
    ]);
    const blob = await shotR.blob();
    const sys = sysR.ok ? await sysR.json() : {};
    return {
      blob,
      blobUrl: URL.createObjectURL(blob),
      ts: Date.now()/1000, sys, strokes: [],
    };
  } catch(e) {
    return {blob: null, blobUrl: '', ts: Date.now()/1000, sys: {}, strokes: []};
  }
}

function closeModal() {
  document.getElementById('modal-bg').classList.remove('on');
  for (const f of modalFrames) if (f.blobUrl) URL.revokeObjectURL(f.blobUrl);
  modalFrames = []; modalIdx = null;
}

function renderModalFrame() {
  const f = modalFrames[modalCurFrame];
  if (!f) return;
  const img = document.getElementById('modal-img');
  const canvas = document.getElementById('modal-canvas');
  img.onload = () => {
    // 同步 canvas 尺寸到 img 的渲染尺寸
    const rect = img.getBoundingClientRect();
    canvas.width = img.naturalWidth;
    canvas.height = img.naturalHeight;
    canvas.style.width = rect.width + 'px';
    canvas.style.height = rect.height + 'px';
    canvas.style.left = (rect.left - canvas.parentElement.getBoundingClientRect().left) + 'px';
    canvas.style.top = (rect.top - canvas.parentElement.getBoundingClientRect().top) + 'px';
    redrawCanvas();
  };
  img.src = f.blobUrl;
  // 帧元信息
  document.getElementById('frame-meta').innerHTML =
    metaRow('时间', new Date(f.ts*1000).toLocaleTimeString())
    + metaRow('CPU', (f.sys.cpu_percent ?? '-') + '%')
    + metaRow('内存', (f.sys.mem_percent ?? '-') + '%')
    + metaRow('批注', f.strokes.length + ' 条');
  if (modalInst) {
    document.getElementById('inst-meta').innerHTML =
      metaRow('phase', modalInst.phase || '-')
      + metaRow('state', modalInst.state || '-')
      + metaRow('role', modalInst.role || '-')
      + metaRow('group', modalInst.group || '-')
      + (modalInst.error ? metaRow('error', modalInst.error) : '');
  }
  renderFrameDots();
  document.getElementById('modal-sub').textContent =
    'phase=' + (modalInst?.phase||'-') + ' · state=' + (modalInst?.state||'-');
}

function metaRow(k, v) {
  return '<div class="meta-row"><span class="k">' + escapeHtml(k) + '</span>'
    + '<span class="v">' + escapeHtml(String(v)) + '</span></div>';
}

function renderFrameDots() {
  const c = document.getElementById('frame-dots');
  c.innerHTML = modalFrames.map((_, i) =>
    '<div class="dot ' + (i===modalCurFrame?'on':'') + '" onclick="jumpFrame(' + i + ')"></div>'
  ).join('');
  document.getElementById('frame-label').textContent =
    (modalCurFrame+1) + ' / ' + modalFrames.length;
  document.getElementById('prev-btn').disabled = modalCurFrame <= 0;
  document.getElementById('next-btn').disabled = modalCurFrame >= modalFrames.length - 1;
}

function navFrame(d) {
  const next = modalCurFrame + d;
  if (next < 0 || next >= modalFrames.length) return;
  modalCurFrame = next;
  renderModalFrame();
}
function jumpFrame(i) {
  if (i < 0 || i >= modalFrames.length) return;
  modalCurFrame = i;
  renderModalFrame();
}

// ─────────── 画布工具 ───────────

function bindToolButtons() {
  document.querySelectorAll('.tool').forEach(b => {
    b.onclick = () => {
      curTool = b.dataset.tool;
      document.querySelectorAll('.tool').forEach(x =>
        x.classList.toggle('on', x === b));
    };
  });
}

function renderColorRow() {
  document.getElementById('color-row').innerHTML = COLORS.map(c =>
    '<div class="color-dot ' + (c===curColor?'on':'') + '" '
    + 'style="background:' + c + '" onclick="setColor(\'' + c + '\')"></div>'
  ).join('');
}
function setColor(c) {
  curColor = c;
  renderColorRow();
}

function bindCanvas() {
  const canvas = document.getElementById('modal-canvas');
  canvas.onmousedown = (e) => {
    canvasDrawing = true;
    const p = canvasPoint(e);
    canvasStart = p;
    const f = modalFrames[modalCurFrame];
    f.strokes.push({tool: curTool, color: curColor, size: 4, pts: [p]});
  };
  canvas.onmousemove = (e) => {
    if (!canvasDrawing) return;
    const p = canvasPoint(e);
    const f = modalFrames[modalCurFrame];
    const s = f.strokes[f.strokes.length - 1];
    if (s.tool === 'pen') {
      s.pts.push(p);
    } else {
      s.end = p;
    }
    redrawCanvas();
  };
  canvas.onmouseup = (e) => {
    if (!canvasDrawing) return;
    canvasDrawing = false;
    const p = canvasPoint(e);
    const f = modalFrames[modalCurFrame];
    const s = f.strokes[f.strokes.length - 1];
    if (s.tool !== 'pen') s.end = p;
    redrawCanvas();
    // 刷一次帧 meta（批注数）
    document.getElementById('frame-meta').innerHTML.includes('批注') && renderModalFrame();
  };
  canvas.onmouseleave = canvas.onmouseup;
}

function canvasPoint(e) {
  const canvas = document.getElementById('modal-canvas');
  const rect = canvas.getBoundingClientRect();
  return {
    x: (e.clientX - rect.left) * (canvas.width / rect.width),
    y: (e.clientY - rect.top) * (canvas.height / rect.height),
  };
}

function redrawCanvas() {
  const canvas = document.getElementById('modal-canvas');
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const f = modalFrames[modalCurFrame];
  if (!f) return;
  for (const s of f.strokes) drawStroke(ctx, s);
}

function drawStroke(ctx, s) {
  ctx.strokeStyle = s.color;
  ctx.lineWidth = Math.max(2, s.size);
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  if (s.tool === 'pen') {
    if (s.pts.length < 1) return;
    ctx.beginPath();
    ctx.moveTo(s.pts[0].x, s.pts[0].y);
    for (let i = 1; i < s.pts.length; i++) ctx.lineTo(s.pts[i].x, s.pts[i].y);
    ctx.stroke();
  } else if (s.tool === 'rect' && s.end) {
    const x = s.pts[0].x, y = s.pts[0].y;
    ctx.strokeRect(x, y, s.end.x - x, s.end.y - y);
  } else if (s.tool === 'circle' && s.end) {
    const cx = (s.pts[0].x + s.end.x) / 2;
    const cy = (s.pts[0].y + s.end.y) / 2;
    const rx = Math.abs(s.end.x - s.pts[0].x) / 2;
    const ry = Math.abs(s.end.y - s.pts[0].y) / 2;
    ctx.beginPath();
    ctx.ellipse(cx, cy, rx, ry, 0, 0, Math.PI*2);
    ctx.stroke();
  }
}

function undoStroke() {
  const f = modalFrames[modalCurFrame];
  if (!f || f.strokes.length === 0) return;
  f.strokes.pop();
  redrawCanvas();
  renderModalFrame();
}
function clearAnnot() {
  const f = modalFrames[modalCurFrame];
  if (!f) return;
  f.strokes = [];
  redrawCanvas();
  renderModalFrame();
}

// ─────────── 保存记录 ───────────

async function saveRecord() {
  if (modalIdx === null || modalFrames.length === 0) return;
  const note = document.getElementById('note-text').value;
  // 把每帧 blob 转 dataURL；批注 canvas 渲染为透明 PNG dataURL
  const framePayload = [];
  for (let i = 0; i < modalFrames.length; i++) {
    const f = modalFrames[i];
    const dataUrl = f.blob ? await blobToDataUrl(f.blob) : null;
    let annUrl = null;
    if (f.strokes && f.strokes.length > 0) {
      // 离屏画布渲染批注为透明 PNG
      const off = document.createElement('canvas');
      // 用 img 的 naturalSize 作为画布尺寸（保证坐标一致）
      const img = new Image();
      await new Promise(r => { img.onload = r; img.src = f.blobUrl; });
      off.width = img.naturalWidth;
      off.height = img.naturalHeight;
      const offCtx = off.getContext('2d');
      for (const s of f.strokes) drawStroke(offCtx, s);
      annUrl = off.toDataURL('image/png');
    }
    framePayload.push({
      data_url: dataUrl,
      annotation_data_url: annUrl,
      ts: f.ts,
      sys: f.sys,
    });
  }
  try {
    const r = await fetch('/api/records', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({idx: modalIdx, note, frames: framePayload}),
    });
    const j = await r.json();
    if (j.ok) {
      toast('已保存 ' + j.id);
      closeModal();
      loadRecords();
    } else {
      alert('保存失败: ' + JSON.stringify(j));
    }
  } catch(e) {
    alert('保存失败: ' + e.message);
  }
}

function blobToDataUrl(blob) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onloadend = () => resolve(r.result);
    r.onerror = reject;
    r.readAsDataURL(blob);
  });
}

function toast(msg) {
  const el = document.createElement('div');
  el.className = 'toast';
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 2400);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  })[c]);
}

// 启动
refreshStatus();
setInterval(refreshStatus, 1500);
</script>
</body>
</html>
"""


# ─────────── 标注器 HTML ───────────

LABELER_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>YOLO 标注</title>
<style>
  * { box-sizing: border-box; -webkit-user-select: none; user-select: none; }
  html, body { margin:0; padding:0; height:100%;
               font-family: -apple-system, "PingFang SC", sans-serif;
               background:#0f1115; color:#e3e6eb; font-size:13px; }
  .topbar {
    background:#161a21; border-bottom:1px solid #252a33;
    padding:10px 16px; display:flex; align-items:center; gap:16px;
  }
  .topbar h1 { margin:0; font-size:15px; color:#6fa8ff; }
  .topbar .progress { color:#8b95a5; }
  .topbar .progress strong { color:#4ade80; }
  .topbar button {
    background:#2a3140; color:#e3e6eb; border:1px solid #3a4252;
    padding:5px 10px; border-radius:5px; cursor:pointer; font-size:12px;
  }
  .topbar button:hover { background:#3a4252; }

  .layout { display:flex; height:calc(100% - 50px); }
  .sidebar {
    width:160px; background:#161a21; border-right:1px solid #252a33;
    overflow-y:auto;
  }
  .sidebar .file {
    padding:6px 10px; cursor:pointer; border-bottom:1px solid #1f2530;
    display:flex; justify-content:space-between; font-size:11px;
  }
  .sidebar .file:hover { background:#1f2530; }
  .sidebar .file.cur { background:#2a3a55; color:#fff; }
  .sidebar .file.labeled { color:#4ade80; }
  .sidebar .file.skipped { color:#888; }
  .sidebar .name { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .sidebar .tag { font-size:10px; opacity:0.6; }

  .canvas-wrap {
    flex:1; background:#000; position:relative; overflow:hidden;
    display:flex; align-items:center; justify-content:center;
  }
  .canvas-wrap img, .canvas-wrap canvas {
    max-width:100%; max-height:100%; position:absolute;
  }
  .canvas-wrap img { z-index:1; }
  .canvas-wrap canvas { z-index:2; cursor:crosshair; }

  .right {
    width:280px; background:#161a21; border-left:1px solid #252a33;
    display:flex; flex-direction:column; min-height:0;
  }
  .right .section { padding:14px; border-bottom:1px solid #252a33; }
  .right .section h3 {
    margin:0 0 10px; font-size:11px; color:#8b95a5; font-weight:600;
    text-transform:uppercase; letter-spacing:0.5px;
  }
  .class-btn {
    display:block; width:100%; text-align:left; margin-bottom:6px;
    padding:9px 12px; border:2px solid; border-radius:6px;
    background:#1a1f28; color:#e3e6eb; cursor:pointer; font-size:13px;
  }
  .class-btn .key { float:right; opacity:0.5; font-size:11px; }
  .box-list { font-size:12px; max-height:140px; overflow-y:auto; }
  .box-list .box-item {
    display:flex; justify-content:space-between; align-items:center;
    padding:5px 8px; background:#1a1f28; border-radius:4px;
    margin-bottom:4px;
  }
  .box-list button {
    background:#3d1d1d; color:#ef4444; border:none;
    padding:2px 8px; border-radius:3px; cursor:pointer; font-size:11px;
  }
  .actions {
    margin-top:auto; padding:14px; display:flex; gap:6px; flex-wrap:wrap;
  }
  .actions button {
    flex:1; min-width:80px; padding:9px; border-radius:5px;
    border:1px solid #3a4252; background:#2a3140; color:#e3e6eb;
    cursor:pointer; font-size:12px;
  }
  .actions button.primary { background:#3b6fd1; border-color:#4a82e8; color:#fff; }
  .actions button.primary:hover { background:#4a82e8; }
  .actions button.danger { background:#3d1d1d; border-color:#9d3b3b; color:#ef4444; }
  .actions button.danger:hover { background:#5a2828; }
  .actions button:hover { background:#3a4252; }

  .help {
    padding:10px 14px; font-size:11px; color:#6e7685; line-height:1.6;
    border-top:1px solid #252a33; background:#1a1f28;
  }
  .help kbd {
    background:#2a3140; padding:1px 5px; border-radius:3px; font-size:10px;
    border:1px solid #3a4252;
  }
</style>
</head>
<body>
<div class="topbar">
  <h1>YOLO 标注</h1>
  <span class="progress">
    已标 <strong id="prog-labeled">0</strong> /
    跳过 <span id="prog-skipped">0</span> /
    总计 <span id="prog-total">0</span>
  </span>
  <span id="class-stats" style="display:flex; gap:6px;"></span>
  <button onclick="reload()">刷新</button>
  <span style="margin-left:auto; color:#8b95a5; font-size:11px;" id="cur-name">-</span>
</div>

<div class="layout">
  <div class="sidebar" id="sidebar">loading…</div>

  <div class="canvas-wrap" id="cw">
    <img id="img" alt="">
    <canvas id="canvas"></canvas>
  </div>

  <div class="right">
    <div class="section">
      <h3>类别（点选 / 数字键）</h3>
      <div id="class-row"></div>
    </div>
    <div class="section">
      <h3>已标 bbox（共 <span id="bbox-count">0</span> 个）</h3>
      <div class="box-list" id="box-list">
        <div style="color:#6e7685;">还没标。鼠标拖框开始。</div>
      </div>
    </div>
    <div class="actions">
      <button class="primary" onclick="saveAndNext()">保存下一张 <kbd style="opacity:.7">S</kbd></button>
      <button onclick="skipImage()">跳过 <kbd style="opacity:.7">K</kbd></button>
      <button class="danger" onclick="deleteImage()">删图 <kbd style="opacity:.7">D</kbd></button>
      <button onclick="navImage(-1)">‹ 上张</button>
      <button onclick="navImage(1)">下张 ›</button>
    </div>
    <div class="help">
      拖鼠标 = 画框；<kbd>1/2/3</kbd> 选类；<kbd>S</kbd> 保存下一张；<kbd>K</kbd> 跳过；
      <kbd>D</kbd> 删图（垃圾帧）；<kbd>←</kbd>/<kbd>→</kbd> 上下张；
      <kbd>U</kbd> 撤销最后一个 box。
    </div>
  </div>
</div>

<script>
const COLORS = ['#ef4444', '#fbbf24', '#6fa8ff'];   // close_x, action_btn, dialog
let CLASSES = [];
let items = [];
let curIdx = -1;
let curBoxes = [];     // {class_id, cx, cy, w, h}  归一化 [0,1]
let curClass = 0;
let drawing = false;
let drawStart = null;
let drawCur = null;
let imgNatW = 0, imgNatH = 0;

async function reload() {
  const r = await fetch('/api/labeler/list');
  const d = await r.json();
  items = d.items;
  CLASSES = d.classes;
  document.getElementById('prog-labeled').textContent = d.labeled;
  document.getElementById('prog-skipped').textContent = d.skipped;
  document.getElementById('prog-total').textContent = d.total;
  // 每类实例数显示在顶栏
  const stats = document.getElementById('class-stats');
  stats.innerHTML = (d.per_class || []).map(p => {
    const isLegacy = p.id >= CLASSES.length;
    const color = isLegacy ? '#6e7685' : (COLORS[p.id] || '#888');
    const opacity = isLegacy ? 0.5 : 1;
    return `<span style="background:${color}25; border:1px solid ${color};
                         color:${color}; padding:3px 8px; border-radius:4px;
                         font-size:11px; opacity:${opacity};"
                  title="${p.name}: ${p.instances} 个 bbox, 在 ${p.images} 张图">
              ${p.name}: <strong>${p.instances}</strong>
            </span>`;
  }).join('');
  renderSidebar();
  renderClassRow();
  if (curIdx < 0 || curIdx >= items.length) {
    // 跳到第一个未标的
    const first = items.findIndex(i => !i.labeled && !i.skipped);
    curIdx = first >= 0 ? first : 0;
  }
  if (items.length > 0) loadImage(curIdx);
}

function renderClassRow() {
  const c = document.getElementById('class-row');
  c.innerHTML = CLASSES.map((name, i) => `
    <div class="class-btn" data-cid="${i}"
         style="border-color:${COLORS[i]||'#888'};
                ${i===curClass ? `background:${COLORS[i]}30;` : ''}"
         onclick="setClass(${i})">
      ${name}
      <span class="key">${i+1}</span>
    </div>
  `).join('');
}

function setClass(i) {
  curClass = i;
  renderClassRow();
}

function renderSidebar() {
  const s = document.getElementById('sidebar');
  s.innerHTML = items.map((it, i) => {
    const cls = it.labeled ? 'labeled' : (it.skipped ? 'skipped' : '');
    const tag = it.labeled ? '✓' : (it.skipped ? '–' : '');
    return `<div class="file ${cls} ${i===curIdx?'cur':''}" onclick="loadImage(${i})"
                 data-idx="${i}">
              <span class="name">${escapeHtml(it.name)}</span>
              <span class="tag">${tag}</span>
            </div>`;
  }).join('');
  // scroll cur into view
  const cur = s.querySelector('.file.cur');
  if (cur) cur.scrollIntoView({block:'nearest'});
}

async function loadImage(i) {
  if (i < 0 || i >= items.length) return;
  curIdx = i;
  const it = items[i];
  document.getElementById('cur-name').textContent = it.name + ' (' + (i+1) + '/' + items.length + ')';

  // 立刻清掉上一张的 box（避免缓存命中时 img.onload 比 fetch 先到，画错框）
  curBoxes = [];
  drawAll();
  renderBoxList();

  const img = document.getElementById('img');
  img.onload = () => {
    imgNatW = img.naturalWidth;
    imgNatH = img.naturalHeight;
    syncCanvas();
    drawAll();   // 用当前 curBoxes 画（fetch 没回来就是空，回来了就有内容）
  };
  // 加 ts 防浏览器缓存上一张图，强制每次都重新拉 + 触发 onload
  img.src = '/api/labeler/image/' + encodeURIComponent(it.name) + '?t=' + Date.now();

  // 加载已存 labels（异步，可能比 img.onload 先回也可能后回）
  let myIdx = curIdx;
  try {
    const r = await fetch('/api/labeler/labels/' + encodeURIComponent(it.name));
    if (curIdx !== myIdx) return;  // 期间用户跳别的图了，丢弃
    const d = await r.json();
    curBoxes = (d.boxes || []).map(b => ({
      class_id: b.class_id,
      cx: b.cx, cy: b.cy, w: b.w, h: b.h
    }));
  } catch(e) { curBoxes = []; }

  // 关键：fetch 完成后再画一次（覆盖 onload 时画的空 / 残影）
  drawAll();
  renderBoxList();
  renderSidebar();
}

function syncCanvas() {
  const canvas = document.getElementById('canvas');
  const img = document.getElementById('img');
  const r = img.getBoundingClientRect();
  canvas.width = imgNatW;
  canvas.height = imgNatH;
  canvas.style.width = r.width + 'px';
  canvas.style.height = r.height + 'px';
  canvas.style.left = (r.left - canvas.parentElement.getBoundingClientRect().left) + 'px';
  canvas.style.top = (r.top - canvas.parentElement.getBoundingClientRect().top) + 'px';
}

window.addEventListener('resize', () => { syncCanvas(); drawAll(); });

function canvasPoint(e) {
  const c = document.getElementById('canvas');
  const r = c.getBoundingClientRect();
  return {
    x: (e.clientX - r.left) * (c.width / r.width),
    y: (e.clientY - r.top) * (c.height / r.height),
  };
}

document.getElementById('canvas').addEventListener('mousedown', (e) => {
  drawing = true;
  drawStart = canvasPoint(e);
  drawCur = drawStart;
});
document.getElementById('canvas').addEventListener('mousemove', (e) => {
  if (!drawing) return;
  drawCur = canvasPoint(e);
  drawAll();
});
const finishDraw = (e) => {
  if (!drawing) return;
  drawing = false;
  if (!drawStart || !drawCur) return;
  const x1 = Math.min(drawStart.x, drawCur.x);
  const y1 = Math.min(drawStart.y, drawCur.y);
  const x2 = Math.max(drawStart.x, drawCur.x);
  const y2 = Math.max(drawStart.y, drawCur.y);
  const w = x2 - x1, h = y2 - y1;
  if (w >= 8 && h >= 8) {
    curBoxes.push({
      class_id: curClass,
      cx: (x1 + w/2) / imgNatW,
      cy: (y1 + h/2) / imgNatH,
      w: w / imgNatW,
      h: h / imgNatH,
    });
    renderBoxList();
  }
  drawStart = null; drawCur = null;
  drawAll();
};
document.getElementById('canvas').addEventListener('mouseup', finishDraw);
document.getElementById('canvas').addEventListener('mouseleave', finishDraw);

function drawAll() {
  const c = document.getElementById('canvas');
  const ctx = c.getContext('2d');
  ctx.clearRect(0, 0, c.width, c.height);
  ctx.lineWidth = 3;
  ctx.font = '16px sans-serif';
  for (const b of curBoxes) {
    const x = (b.cx - b.w/2) * c.width;
    const y = (b.cy - b.h/2) * c.height;
    const w = b.w * c.width;
    const h = b.h * c.height;
    ctx.strokeStyle = COLORS[b.class_id] || '#888';
    ctx.strokeRect(x, y, w, h);
    // 标签
    ctx.fillStyle = COLORS[b.class_id] || '#888';
    const label = CLASSES[b.class_id] || '?';
    const tw = ctx.measureText(label).width + 10;
    ctx.fillRect(x, y - 22, tw, 22);
    ctx.fillStyle = '#000';
    ctx.fillText(label, x + 5, y - 6);
  }
  // 正在拖的预览
  if (drawing && drawStart && drawCur) {
    const x1 = Math.min(drawStart.x, drawCur.x);
    const y1 = Math.min(drawStart.y, drawCur.y);
    const x2 = Math.max(drawStart.x, drawCur.x);
    const y2 = Math.max(drawStart.y, drawCur.y);
    ctx.strokeStyle = COLORS[curClass] || '#fff';
    ctx.setLineDash([6, 4]);
    ctx.strokeRect(x1, y1, x2-x1, y2-y1);
    ctx.setLineDash([]);
  }
}

function renderBoxList() {
  document.getElementById('bbox-count').textContent = curBoxes.length;
  const c = document.getElementById('box-list');
  if (curBoxes.length === 0) {
    c.innerHTML = '<div style="color:#6e7685;">还没标。鼠标拖框开始。</div>';
    return;
  }
  c.innerHTML = curBoxes.map((b, i) => `
    <div class="box-item" style="border-left:3px solid ${COLORS[b.class_id]};">
      <span>${CLASSES[b.class_id]} ${(b.w*100).toFixed(0)}×${(b.h*100).toFixed(0)}</span>
      <button onclick="removeBox(${i})">删</button>
    </div>
  `).join('');
}

function removeBox(i) {
  curBoxes.splice(i, 1);
  renderBoxList();
  drawAll();
}

async function saveAndNext() {
  if (curIdx < 0 || curIdx >= items.length) return;
  const name = items[curIdx].name;
  await fetch('/api/labeler/labels/' + encodeURIComponent(name), {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({boxes: curBoxes}),
  });
  // 刷一下列表（更新 labeled 状态）+ 跳下一个未标的
  await reloadKeepCursor();
  navToNextUnlabeled();
}

async function skipImage() {
  if (curIdx < 0 || curIdx >= items.length) return;
  // 空 boxes 数组 = 跳过（写空文件）
  curBoxes = [];
  await fetch('/api/labeler/labels/' + encodeURIComponent(items[curIdx].name), {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({boxes: []}),
  });
  await reloadKeepCursor();
  navToNextUnlabeled();
}

async function deleteImage() {
  if (curIdx < 0 || curIdx >= items.length) return;
  if (!confirm('删除这张图（移到 .trash/）？')) return;
  await fetch('/api/labeler/image/' + encodeURIComponent(items[curIdx].name), {
    method: 'DELETE',
  });
  await reloadKeepCursor();
  if (curIdx >= items.length) curIdx = items.length - 1;
  if (curIdx >= 0) loadImage(curIdx);
}

async function reloadKeepCursor() {
  const r = await fetch('/api/labeler/list');
  const d = await r.json();
  items = d.items;
  document.getElementById('prog-labeled').textContent = d.labeled;
  document.getElementById('prog-skipped').textContent = d.skipped;
  document.getElementById('prog-total').textContent = d.total;
  // 同步刷新每类计数（保存后能立刻看到 bbox 数变化）
  const stats = document.getElementById('class-stats');
  stats.innerHTML = (d.per_class || []).map(p => {
    const isLegacy = p.id >= CLASSES.length;
    const color = isLegacy ? '#6e7685' : (COLORS[p.id] || '#888');
    const opacity = isLegacy ? 0.5 : 1;
    return `<span style="background:${color}25; border:1px solid ${color};
                         color:${color}; padding:3px 8px; border-radius:4px;
                         font-size:11px; opacity:${opacity};"
                  title="${p.name}: ${p.instances} 个 bbox, 在 ${p.images} 张图">
              ${p.name}: <strong>${p.instances}</strong>
            </span>`;
  }).join('');
  renderSidebar();
}

function navToNextUnlabeled() {
  // 从当前位置往后找第一个未标的
  for (let j = curIdx + 1; j < items.length; j++) {
    if (!items[j].labeled && !items[j].skipped) {
      loadImage(j);
      return;
    }
  }
  // 都标完了 → 留在原位
  alert('已经是最后一张未标的了');
}

function navImage(d) {
  const next = curIdx + d;
  if (next >= 0 && next < items.length) loadImage(next);
}

// 键盘
document.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  switch(e.key.toLowerCase()) {
    case '1': case '2': case '3':
      const i = parseInt(e.key) - 1;
      if (i < CLASSES.length) setClass(i);
      e.preventDefault();
      break;
    case 's': saveAndNext(); e.preventDefault(); break;
    case 'k': skipImage(); e.preventDefault(); break;
    case 'd': deleteImage(); e.preventDefault(); break;
    case 'u':  // 撤销最后一个 box
      if (curBoxes.length > 0) { curBoxes.pop(); renderBoxList(); drawAll(); }
      e.preventDefault(); break;
    case 'arrowleft': navImage(-1); e.preventDefault(); break;
    case 'arrowright': navImage(1); e.preventDefault(); break;
  }
});

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  })[c]);
}

reload();
</script>
</body>
</html>
"""


# ─────────── 启动 ───────────

_server_thread: Optional[threading.Thread] = None


def start_in_thread(host: str = "0.0.0.0", port: int = 8901):
    """在后台线程启动 debug server，不阻塞主进程"""
    global _server_thread
    if _server_thread is not None and _server_thread.is_alive():
        logger.info("[debug] server already running, skip")
        return

    def _run():
        try:
            config = uvicorn.Config(
                app, host=host, port=port,
                log_level="warning", access_log=False,
                loop="asyncio",
            )
            server = uvicorn.Server(config)
            asyncio.run(server.serve())
        except Exception as e:
            logger.error(f"[debug] server crashed: {e}")

    _server_thread = threading.Thread(target=_run, daemon=True, name="debug-server")
    _server_thread.start()
    logger.info(f"[debug] server started http://{host}:{port}/")

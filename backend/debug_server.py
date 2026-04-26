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


@app.get("/api/labeler/export.zip")
async def api_labeler_export():
    """打包导出训练数据。返回 zip:
      images/<png>          仅有非空 .txt 标注的图 + 一定比例的跳过图（背景）
      labels/<txt>          YOLO 格式标注
      classes.txt           类名（每行一个）
    Mac 训练脚本一键 curl 这个。
    """
    import io, zipfile, random
    raw, labels_dir, classes_p = _yolo_paths()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # 写 classes.txt
        zf.writestr("classes.txt", "\n".join(LABEL_CLASSES) + "\n")

        # 收集已标图（.txt 非空）
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

        # 背景图（skipped）采样 ~30% 比例（避免负样本压倒正样本）
        max_bg = max(20, len(labeled_imgs) // 3)
        random.seed(42)  # 固定种子，重训重现
        bg_sample = random.sample(skipped_imgs, min(max_bg, len(skipped_imgs)))

        for p in labeled_imgs + bg_sample:
            label_p = labels_dir / f"{p.stem}.txt"
            zf.write(str(p), arcname=f"images/{p.name}")
            # 过滤 class_id >= 当前类数（剔除历史 dialog cid=2）
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
                    if 0 <= cid < len(LABEL_CLASSES):
                        kept.append(line.strip())
                zf.writestr(f"labels/{p.stem}.txt",
                            "\n".join(kept) + ("\n" if kept else ""))
            else:
                # 背景图：空 .txt（YOLO 视作 no-object）
                zf.writestr(f"labels/{p.stem}.txt", "")

        # 元信息
        zf.writestr("manifest.json", json.dumps({
            "classes": LABEL_CLASSES,
            "labeled": len(labeled_imgs),
            "background_sampled": len(bg_sample),
            "background_total": len(skipped_imgs),
        }, ensure_ascii=False, indent=2))

    buf.seek(0)
    from datetime import datetime as _dt
    fname = f"yolo_dataset_{_dt.now().strftime('%Y%m%d_%H%M%S')}.zip"
    return Response(
        content=buf.read(),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )


@app.post("/api/yolo/upload_model")
async def api_yolo_upload_model(file: UploadFile = File(...)):
    """Mac 训练完，把 ONNX 上传回 Windows 用户目录"""
    from .automation.user_paths import user_yolo_dir
    models_dir = user_yolo_dir() / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    name = _safe_id(file.filename or "model.onnx")
    if not name.endswith(".onnx"):
        name = name + ".onnx"
    out = models_dir / name
    data = await file.read()
    out.write_bytes(data)
    # 同时更新 latest.onnx 软链接（Windows 没软链就直接复制覆盖）
    latest = models_dir / "latest.onnx"
    latest.write_bytes(data)
    return {
        "ok": True,
        "saved": str(out),
        "size": len(data),
        "latest": str(latest),
    }


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


# ─────────── Decision Theater (识别可视化) ───────────


@app.get("/decisions", response_class=HTMLResponse)
async def decisions_page():
    return HTMLResponse(content=DECISIONS_HTML, headers={
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    })


@app.get("/api/sessions")
async def api_sessions():
    """列出所有有决策记录的 session（含历史）"""
    try:
        from .automation.decision_log import get_recorder
        sessions = get_recorder().list_sessions()
        cur = ""
        if _session_dir:
            try:
                cur = Path(_session_dir).name
            except Exception:
                cur = ""
        return {"sessions": sessions, "current_session": cur}
    except Exception as e:
        import traceback
        logger.error(f"api_sessions error: {e}\n{traceback.format_exc()}")
        return {"sessions": [], "current_session": "", "error": str(e)}


@app.get("/api/decisions")
async def api_decisions(limit: int = 200, instance: int = -1, session: str = ""):
    """
    列出决策。
      session 空 → 当前 session（用内存索引最快）
      session 给定 → 扫磁盘历史 session
    """
    try:
        from .automation.decision_log import get_recorder
        rec = get_recorder()
        inst_filter = instance if instance >= 0 else None
        if session:
            items = rec.list_session_decisions(session, limit=limit, instance=inst_filter)
            return {
                "count": len(items),
                "items": items,
                "session": session,
                "enabled": rec.is_enabled(),
            }
        items = rec.list_recent(limit=limit, instance=inst_filter)
        cur_name = ""
        if _session_dir:
            try:
                cur_name = Path(_session_dir).name
            except Exception:
                pass
        return {
            "count": len(items),
            "items": items,
            "session_dir": _session_dir,
            "session": cur_name,
            "enabled": rec.is_enabled(),
        }
    except Exception as e:
        import traceback
        logger.error(f"api_decisions error: {e}\n{traceback.format_exc()}")
        return {"count": 0, "items": [], "session": "", "enabled": False, "error": str(e)}


def _resolve_decision_dir(decision_id: str, session: str = "") -> Optional[Path]:
    """根据 session 找决策所在目录。session 空则用当前。"""
    from .automation.decision_log import get_recorder
    rec = get_recorder()
    if session:
        root = rec._logs_root()
        if root is None:
            return None
        return root / session / "decisions" / decision_id
    # 当前 session
    root = rec.root()
    if root is None:
        return None
    return root / decision_id


@app.get("/api/decision/{decision_id}/data")
async def api_decision_data(decision_id: str, session: str = ""):
    """单条决策的完整 JSON"""
    p = _resolve_decision_dir(_safe_id(decision_id), session)
    if p is None:
        raise HTTPException(404, "no active session")
    json_p = p / "decision.json"
    if not json_p.is_file():
        raise HTTPException(404, "decision not found")
    try:
        return JSONResponse(json.loads(json_p.read_text(encoding="utf-8")))
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/decision/{decision_id}/image/{filename}")
async def api_decision_image(decision_id: str, filename: str, session: str = ""):
    """决策目录下的任意图片（input.jpg / yolo_annot.jpg / tmpl_*.png ...）"""
    d_dir = _resolve_decision_dir(_safe_id(decision_id), session)
    if d_dir is None:
        raise HTTPException(404)
    # 安全检查：filename 不能包含路径
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "invalid filename")
    p = d_dir / filename
    if not p.is_file():
        raise HTTPException(404, f"image not found: {filename}")
    media_type = "image/png" if p.suffix == ".png" else "image/jpeg"
    return FileResponse(p, media_type=media_type)


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
    <a href="/decisions" target="_blank" style="text-decoration:none;display:inline-flex;align-items:center;gap:8px;background:linear-gradient(135deg,#3b6fd1,#5a8aff);color:#fff;border:1px solid #4a82e8;padding:6px 14px;border-radius:6px;font-size:13px;font-weight:500;box-shadow:0 2px 6px rgba(74,130,232,0.35);">
      <span style="font-size:14px;">◧</span>决策剧场
    </a>
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


# ─────────── Decision Theater HTML ───────────

DECISIONS_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>决策剧场 · GameBot</title>
<style>
  *, *::before, *::after { box-sizing: border-box; }
  html, body { margin:0; padding:0; height:100%; }
  body {
    background:#f4f6f9; color:#1a2233;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
                 "Microsoft YaHei", sans-serif; font-size:13px;
    -webkit-font-smoothing: antialiased;
  }

  /* ─── 顶栏 ─── */
  .topbar {
    height: 52px; background:#fff; border-bottom:1px solid #e3e6ed;
    padding: 0 16px; display:flex; align-items:center; gap:14px;
    position: sticky; top: 0; z-index: 30;
  }
  .topbar .back {
    color:#5b6573; text-decoration:none; font-size:13px;
    padding:6px 10px; border-radius:6px;
  }
  .topbar .back:hover { background:#eef0f4; color:#1a2233; }
  .topbar h1 {
    margin:0; font-size:15px; font-weight:600; color:#1a2233;
    border-left:1px solid #e3e6ed; padding-left:14px;
  }
  .topbar .status {
    display:flex; align-items:center; gap:7px; color:#5b6573; font-size:12px;
    background:#f4f6f9; padding:5px 10px; border-radius:999px;
  }
  .pulse { width:8px; height:8px; border-radius:50%; background:#16a34a; flex-shrink:0;
           animation: pulse 1.4s infinite; }
  .pulse.off { background:#94a3b8; animation:none; }
  @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.35; } }
  .topbar .ctrls { margin-left:auto; display:flex; align-items:center; gap:8px; }

  /* ─── 自定义下拉 ─── */
  .session-pill {
    display:inline-flex; align-items:center; gap:8px;
    height:30px; padding: 0 10px 0 12px;
    background:#fff; border:1px solid #d6dbe3; border-radius:8px;
    cursor:pointer; font-size:12px; color:#1a2233;
    transition: all 0.12s;
  }
  .session-pill:hover { border-color:#2563eb; box-shadow:0 0 0 3px #2563eb20; }
  .session-pill.active { border-color:#2563eb; background:#eff5ff; }
  .session-pill .lab { color:#8b95a5; font-size:11px; }
  .session-pill .val { font-weight:500; max-width:200px;
    overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .session-pill .caret { color:#8b95a5; font-size:10px; }

  .session-menu {
    position:fixed; top:0; left:0;
    min-width:340px; max-width:440px; max-height: 64vh;
    background:#fff; border:1px solid #d6dbe3; border-radius:10px;
    box-shadow: 0 12px 32px rgba(15,23,42,0.18);
    overflow-y:auto; z-index: 60;
    display:none;
  }
  .session-menu.on { display:block; }
  .session-menu .head {
    padding: 10px 14px; border-bottom: 1px solid #eef0f4;
    font-size: 11px; color: #8b95a5; font-weight: 600; letter-spacing: 0.5px;
    text-transform: uppercase;
    position: sticky; top: 0; background: #fff;
  }
  .session-item {
    padding: 10px 14px; border-bottom: 1px solid #f4f6f9;
    cursor:pointer; transition: background 0.1s;
    display:flex; flex-direction:column; gap:3px;
  }
  .session-item:last-child { border-bottom: none; }
  .session-item:hover { background:#f4f6f9; }
  .session-item.selected { background:#eff5ff; }
  .session-item .name { font-weight:500; color:#1a2233; font-size:13px;
    font-variant-numeric: tabular-nums; }
  .session-item .meta {
    color:#5b6573; font-size:11px;
    display:flex; align-items:center; gap:8px;
  }
  .session-item .live-dot {
    width:6px; height:6px; border-radius:50%; background:#16a34a;
    display:inline-block; animation: pulse 1.4s infinite;
  }
  .session-item .badge-cnt {
    background:#eef0f4; color:#5b6573; padding: 1px 7px; border-radius: 999px;
    font-size:10px; font-variant-numeric: tabular-nums;
  }

  /* ─── 控件 ─── */
  .ctrl-input {
    height:30px; padding: 0 10px;
    background:#fff; border:1px solid #d6dbe3; border-radius:8px;
    font-size:12px; color:#1a2233; outline: none;
    width: 110px;
  }
  .ctrl-input:focus { border-color:#2563eb; box-shadow:0 0 0 3px #2563eb20; }
  .ctrl-input::placeholder { color:#8b95a5; }
  .auto-toggle {
    height:30px; padding: 0 12px;
    background:#fff; border:1px solid #d6dbe3; border-radius:8px;
    font-size:12px; cursor:pointer; color:#1a2233;
    display:inline-flex; align-items:center; gap:7px;
  }
  .auto-toggle .dot { width:7px; height:7px; border-radius:50%; background:#16a34a; }
  .auto-toggle.off .dot { background:#94a3b8; }
  .auto-toggle:hover { border-color:#2563eb; }

  /* ─── 主布局：左右分栏 ─── */
  .layout {
    display: grid;
    grid-template-columns: 340px 1fr;
    height: calc(100vh - 52px);
    overflow: hidden;
  }

  /* ─── 左侧列表 ─── */
  .sidebar {
    background:#fff; border-right:1px solid #e3e6ed;
    overflow-y: auto; overflow-x: hidden;
  }
  .sidebar-empty {
    padding: 40px 24px; text-align:center; color:#8b95a5; font-size:13px;
    line-height: 1.7;
  }
  .sidebar-empty .hint { font-size:11px; color:#a8b0bc; margin-top: 6px; }

  .inst-group { border-bottom: 1px solid #eef0f4; }
  .inst-head {
    padding: 12px 16px 8px; display:flex; align-items:center; gap:8px;
    background:#fafbfc; position: sticky; top: 0; z-index: 5;
    border-bottom: 1px solid #eef0f4;
  }
  .inst-head .name { font-weight:600; color:#1a2233; font-size:13px; }
  .inst-head .stat { margin-left:auto; color:#8b95a5; font-size:11px;
    font-variant-numeric: tabular-nums; }
  .inst-dot {
    width:7px; height:7px; border-radius:50%;
    background:#2563eb;
  }

  .deci-row {
    padding: 9px 16px; cursor: pointer;
    border-left: 3px solid transparent;
    display:flex; align-items:flex-start; gap:9px;
    transition: background 0.08s;
    border-bottom: 1px solid #f4f6f9;
  }
  .deci-row:hover { background: #f8f9fb; }
  .deci-row.selected {
    background: #eff5ff; border-left-color: #2563eb;
  }
  .deci-row.new-glow { animation: rowGlow 1.4s ease-out; }
  @keyframes rowGlow {
    0% { background:#fef3c7; }
    100% { background: transparent; }
  }
  .deci-row .icon {
    width:18px; height:18px; border-radius:50%; flex-shrink:0;
    display:inline-flex; align-items:center; justify-content:center;
    font-size:11px; font-weight:700;
  }
  .deci-row .icon.ok   { background:#dcfce7; color:#15803d; }
  .deci-row .icon.fail { background:#fee2e2; color:#b91c1c; }
  .deci-row .icon.warn { background:#fef3c7; color:#a16207; }
  .deci-row .icon.idle { background:#eef0f4; color:#5b6573; }
  .deci-row .body { flex:1; min-width:0; }
  .deci-row .top { display:flex; align-items:center; gap:8px; }
  .deci-row .time {
    font-family: ui-monospace, "SF Mono", Menlo, monospace;
    font-size: 11px; color:#5b6573;
  }
  .deci-row .round { font-size:10px; color:#8b95a5; }
  .deci-row .story {
    font-size: 12px; color:#1a2233; line-height: 1.4; margin-top:2px;
    overflow: hidden; text-overflow: ellipsis;
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
  }

  .lobby-divider {
    padding: 6px 16px; font-size:10.5px; color:#15803d;
    background: linear-gradient(90deg, #dcfce7, #fff);
    border-bottom: 1px solid #bbf7d0;
    text-align: center; font-weight:500; letter-spacing: 0.3px;
  }
  .phase-divider {
    padding: 6px 16px; font-size:10.5px; color:#a16207;
    background: linear-gradient(90deg, #fef3c7, #fff);
    border-bottom: 1px solid #fde68a;
    text-align: center; font-weight:500;
  }

  /* ─── 右侧详情 ─── */
  .detail-pane {
    overflow-y: auto; padding: 0;
    background:#f4f6f9;
  }
  .detail-empty {
    height: 100%; display:flex; flex-direction:column;
    align-items:center; justify-content:center; color:#8b95a5;
    font-size:14px; gap:8px;
  }
  .detail-empty .icon { font-size: 48px; opacity: 0.3; }

  .detail-head {
    padding: 18px 24px 14px; background:#fff; border-bottom:1px solid #e3e6ed;
    position: sticky; top: 0; z-index: 4;
  }
  .detail-head .row1 {
    display:flex; align-items:center; gap:12px; margin-bottom:6px;
  }
  .detail-head .title { font-size: 16px; font-weight:600; color:#1a2233; }
  .detail-head .meta { color:#5b6573; font-size:12px; }
  .detail-head .meta b { color:#1a2233; font-weight:500; }
  .detail-head .id-tag {
    margin-left:auto; font-family: ui-monospace, "SF Mono", monospace;
    font-size: 11px; color:#8b95a5;
    background:#f4f6f9; padding: 3px 8px; border-radius: 5px;
  }

  .detail-body { padding: 18px 24px; }
  .panel {
    background:#fff; border:1px solid #e3e6ed; border-radius:10px;
    margin-bottom: 14px; overflow: hidden;
  }
  .panel-head {
    padding: 11px 16px; border-bottom: 1px solid #eef0f4;
    display:flex; align-items:center; gap:10px;
    background:#fafbfc;
  }
  .panel-head .h {
    font-size:12px; font-weight:600; color:#1a2233; letter-spacing: 0.2px;
  }
  .panel-head .duration {
    margin-left:auto; font-size:11px; color:#8b95a5;
    font-variant-numeric: tabular-nums;
  }
  .panel-head .badge-tier {
    background:#e0e7ff; color:#3730a3;
    padding: 2px 8px; border-radius: 999px; font-size: 10.5px; font-weight:500;
  }
  .panel-head .badge-tier.exit { background:#dcfce7; color:#15803d; }
  .panel-body { padding: 14px 16px; }

  .img-wrap {
    background: #0a0c10; border-radius: 8px; overflow: hidden;
    border: 1px solid #e3e6ed; position: relative;
  }
  .img-wrap img {
    width:100%; display:block; max-height: 540px;
    object-fit: contain; cursor: zoom-in;
  }
  .img-wrap .label {
    position:absolute; top:10px; left:10px;
    background: rgba(15,23,42,0.78); color:#fff;
    padding:4px 10px; border-radius:6px; font-size:11px;
    backdrop-filter: blur(4px);
  }

  .story-card {
    background: linear-gradient(135deg, #eff5ff, #f0fdf4);
    border: 1px solid #bfdbfe; border-radius: 10px;
    padding: 14px 18px; margin-bottom: 14px;
  }
  .story-card .h {
    font-size: 11px; color:#1e40af; font-weight:600;
    text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 6px;
  }
  .story-card ol {
    margin:0; padding-left:20px; font-size: 13.5px; color:#1a2233; line-height: 1.7;
  }
  .story-card ol li { margin-bottom: 2px; }
  .story-card b { color: #b91c1c; font-weight:600; }

  .verify-card {
    border-radius: 10px; padding: 12px 16px; margin-bottom: 14px;
    display:flex; align-items:center; gap:14px; font-size: 13px;
  }
  .verify-card.ok   { background:#dcfce7; border:1px solid #86efac; color:#15803d; }
  .verify-card.fail { background:#fee2e2; border:1px solid #fca5a5; color:#b91c1c; }
  .verify-card.unk  { background:#fef3c7; border:1px solid #fde68a; color:#a16207; }
  .verify-card .result { font-weight:600; font-size:14px; }
  .verify-card .meta { margin-left:auto; font-size:11px; opacity:0.8;
    font-variant-numeric: tabular-nums; }

  .kv {
    display:grid; grid-template-columns: 110px 1fr; gap: 6px 14px;
    font-size: 12.5px;
  }
  .kv .k { color:#5b6573; }
  .kv .v {
    color:#1a2233;
    font-family: ui-monospace, "SF Mono", monospace;
    font-size: 12px;
  }

  /* 模板列表 */
  .tmpl-grid {
    display:grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
    gap: 8px;
  }
  .tmpl-item {
    background:#fafbfc; border:1px solid #e3e6ed; border-radius:6px;
    padding: 8px; font-size: 11px;
  }
  .tmpl-item.hit {
    background:#dcfce7; border-color:#86efac;
    box-shadow: 0 0 0 1px #86efac;
  }
  .tmpl-item.miss { opacity:0.6; }
  .tmpl-item img {
    width:100%; height:50px; object-fit:contain;
    background:#0a0c10; border-radius:3px; margin-bottom:5px;
    cursor: zoom-in;
  }
  .tmpl-item .name {
    font-family: ui-monospace, monospace; font-size: 10px;
    color:#5b6573; word-break: break-all;
  }
  .tmpl-item .score {
    margin-top: 3px; display:flex; justify-content: space-between;
    font-variant-numeric: tabular-nums; color:#5b6573; font-size: 10.5px;
  }
  .tmpl-item.hit .score b { color:#15803d; }

  /* YOLO/OCR 命中列表 */
  .det-list { display:flex; flex-direction:column; gap:5px; }
  .det-item {
    background:#fafbfc; border:1px solid #eef0f4; border-radius:6px;
    padding: 7px 11px; font-size:12px;
    display:flex; align-items:center; gap:12px;
  }
  .det-item .cls {
    font-weight:600; min-width: 80px;
    font-family: ui-monospace, monospace; font-size: 11px;
  }
  .det-item .cls.close_x   { color:#b91c1c; }
  .det-item .cls.action_btn { color:#a16207; }
  .det-item .conf {
    color:#5b6573; font-variant-numeric: tabular-nums; font-size: 11px;
  }
  .det-item .bbox {
    margin-left:auto; color:#8b95a5; font-size: 10.5px;
    font-family: ui-monospace, monospace;
  }
  .ocr-text { color:#1a2233; font-size: 12.5px; flex:1; }

  /* 浮层 */
  .modal {
    position:fixed; inset:0; background:rgba(15,23,42,0.92);
    display:none; justify-content:center; align-items:center;
    z-index: 100; cursor: zoom-out;
  }
  .modal.on { display:flex; }
  .modal img { max-width:96vw; max-height:96vh; border-radius: 6px; }

  .toast {
    position: fixed; bottom: 22px; left: 50%; transform: translateX(-50%);
    background:#1a2233; color:#fff; padding: 10px 16px; border-radius: 8px;
    font-size: 13px; z-index: 110;
    box-shadow: 0 8px 24px rgba(15,23,42,0.3);
    opacity: 0; transition: opacity 0.2s;
  }
  .toast.on { opacity: 1; }

  /* 滚动条美化 */
  .sidebar::-webkit-scrollbar, .detail-pane::-webkit-scrollbar,
  .session-menu::-webkit-scrollbar { width: 8px; }
  .sidebar::-webkit-scrollbar-thumb, .detail-pane::-webkit-scrollbar-thumb,
  .session-menu::-webkit-scrollbar-thumb { background: #d6dbe3; border-radius: 999px; }
  .sidebar::-webkit-scrollbar-thumb:hover { background: #b8c0cc; }
</style>
</head>
<body>

<header class="topbar">
  <a href="/" class="back">← Debug</a>
  <h1>决策剧场</h1>
  <span class="status" id="status">
    <span class="pulse off" id="pulse"></span>
    <span id="status-text">加载中…</span>
  </span>
  <div class="ctrls">
    <button class="session-pill" id="session-pill" onclick="toggleSessionMenu(event)">
      <span class="lab">会话</span>
      <span class="val" id="session-label">当前 · 实时</span>
      <span class="caret">▾</span>
    </button>
    <input class="ctrl-input" id="filter-inst" type="number" placeholder="实例 #" min="0" max="9">
    <button class="auto-toggle" id="btn-auto" onclick="toggleAuto()">
      <span class="dot"></span><span id="auto-text">自动刷新</span>
    </button>
  </div>
</header>

<div class="session-menu" id="session-menu" onclick="event.stopPropagation()">
  <div class="head">选择会话</div>
  <div id="session-menu-list"></div>
</div>

<div class="layout">
  <aside class="sidebar" id="sidebar">
    <div class="sidebar-empty">加载中…</div>
  </aside>
  <main class="detail-pane" id="detail">
    <div class="detail-empty">
      <div class="icon">◧</div>
      <div>请从左侧选一条决策查看</div>
    </div>
  </main>
</div>

<div class="modal" id="modal" onclick="this.classList.remove('on')">
  <img id="modal-img">
</div>

<div class="toast" id="toast"></div>

<script>
let allItems = [];
let knownIds = new Set();
let allSessions = [];
let currentSession = "";    // "" = 当前 session
let selectedId = "";
let autoRefresh = true;
let cachedDetails = {};

const PHASE_CN = {
  'dismiss_popups': '弹窗清理',
  'launch_game': '启动游戏',
  'wait_login': '等待登录',
  'team_create': '组队（队长建队）',
  'team_join': '组队（队员加入）',
  'map_setup': '地图设置',
  'accelerator': '加速器连接',
};
const OUTCOME_CN = {
  'lobby_confirmed': '✓ 大厅确认完成',
  'tapped': '已点击目标',
  'no_target': '没找到可点目标',
  'loop_blocked': '同位置连点 3 次无效',
};
function phaseText(p) { return PHASE_CN[p] || p || '-'; }
function outcomeText(o) {
  if (!o) return '未知';
  if (OUTCOME_CN[o]) return OUTCOME_CN[o];
  if (o.startsWith('lobby_pending')) return '大厅初判 ' + o.replace('lobby_pending_', '');
  return o;
}

function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  })[c]);
}

function fmtTime(ts) {
  return new Date(ts * 1000).toLocaleTimeString('zh-CN', { hour12: false });
}
function fmtDate(ts) {
  const d = new Date(ts * 1000);
  return d.toLocaleString('zh-CN', { hour12: false });
}

function showToast(msg, ms = 1800) {
  const t = document.getElementById('toast');
  t.textContent = msg; t.classList.add('on');
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.remove('on'), ms);
}

/* ─── 自定义会话下拉 ─── */
function toggleSessionMenu(ev) {
  ev && ev.stopPropagation();
  const menu = document.getElementById('session-menu');
  const pill = document.getElementById('session-pill');
  if (menu.classList.contains('on')) {
    menu.classList.remove('on'); pill.classList.remove('active'); return;
  }
  // 定位到 pill 下方
  const r = pill.getBoundingClientRect();
  menu.style.left = r.left + 'px';
  menu.style.top = (r.bottom + 6) + 'px';
  pill.classList.add('active');
  menu.classList.add('on');
}
document.addEventListener('click', () => {
  const m = document.getElementById('session-menu');
  if (m && m.classList.contains('on')) {
    m.classList.remove('on');
    document.getElementById('session-pill').classList.remove('active');
  }
});

function renderSessionMenu() {
  const list = document.getElementById('session-menu-list');
  let html = '';
  // 当前 session 永远在第一项
  const isCurSel = currentSession === '';
  html += `<div class="session-item ${isCurSel ? 'selected' : ''}" onclick="pickSession('')">
    <div class="name">当前会话</div>
    <div class="meta">
      <span class="live-dot"></span>
      <span>实时刷新</span>
    </div>
  </div>`;
  for (const s of allSessions) {
    if (s.is_current) continue;
    const sel = s.session === currentSession ? 'selected' : '';
    html += `<div class="session-item ${sel}" onclick="pickSession('${escapeHtml(s.session)}')">
      <div class="name">${escapeHtml(s.session)}</div>
      <div class="meta">
        <span class="badge-cnt">${s.decision_count} 条</span>
        <span>${fmtDate(s.mtime)}</span>
      </div>
    </div>`;
  }
  if (allSessions.filter(x => !x.is_current).length === 0) {
    html += `<div class="session-item" style="cursor:default;color:#8b95a5;">
      <div class="meta">暂无历史会话</div>
    </div>`;
  }
  list.innerHTML = html;
}

function pickSession(name) {
  currentSession = name;
  document.getElementById('session-label').textContent =
    name === '' ? '当前 · 实时' : name;
  document.getElementById('session-menu').classList.remove('on');
  document.getElementById('session-pill').classList.remove('active');
  selectedId = '';
  knownIds.clear();
  cachedDetails = {};
  document.getElementById('detail').innerHTML =
    '<div class="detail-empty"><div class="icon">◧</div><div>请从左侧选一条决策查看</div></div>';
  // 历史 session 暂停自动刷新
  if (name !== '' && autoRefresh) {
    autoRefresh = false;
    document.getElementById('btn-auto').classList.add('off');
    document.getElementById('auto-text').textContent = '自动刷新（历史已停）';
  }
  reload(true);
}

async function reloadSessions() {
  try {
    const r = await fetch('/api/sessions');
    const d = await r.json();
    allSessions = (d.sessions || []).slice().sort((a,b) => (b.mtime||0)-(a.mtime||0));
    renderSessionMenu();
  } catch (e) {
    console.warn('reloadSessions failed', e);
  }
}

/* ─── 拉取决策列表 ─── */
async function reload(forceRedraw = false) {
  let url = '/api/decisions?limit=300';
  const inst = document.getElementById('filter-inst').value;
  if (inst !== '') url += '&instance=' + encodeURIComponent(inst);
  if (currentSession) url += '&session=' + encodeURIComponent(currentSession);
  try {
    const r = await fetch(url);
    const d = await r.json();
    if (d.error) {
      document.getElementById('status-text').textContent = '加载失败: ' + d.error;
      document.getElementById('pulse').classList.add('off');
      return;
    }
    allItems = d.items || [];
    document.getElementById('status-text').textContent =
      `${d.count} 条 · ${currentSession ? '历史: ' + currentSession : '当前会话'}`;
    document.getElementById('pulse').classList.toggle('off', !d.enabled);

    // 新决策：闪一下
    const newOnes = forceRedraw ? [] : allItems.filter(it => !knownIds.has(it.id));
    renderSidebar(newOnes.map(x => x.id));

    if (selectedId && !allItems.some(it => it.id === selectedId)) {
      selectedId = '';
    }
  } catch (e) {
    document.getElementById('status-text').textContent = '加载失败: ' + e.message;
    document.getElementById('pulse').classList.add('off');
  }
}

/* ─── 故事一句话 ─── */
function storySummary(it) {
  const o = it.outcome || '';
  const target = it.tap_target || '';
  const v = it.verify_success;
  if (o === 'lobby_confirmed') return '看到「开始游戏」按钮 → 判定到大厅';
  if (o.startsWith('lobby_pending')) return '看到大厅按钮，再确认一次（防误判）';
  if (o === 'no_target') return '没看到弹窗按钮（画面在加载/动画中）';
  if (o === 'loop_blocked') return '同位置点了 3 次没反应，等等再说';
  if (o === 'tapped') {
    if (target === 'close_x') return '看到 X 关闭按钮 → 点了' + (v === true ? '，✓ 弹窗消失' : v === false ? '，✗ 弹窗没消失' : '');
    if (target === 'action_btn') return '看到操作按钮 → 点了' + (v === true ? '，✓ 画面变了' : v === false ? '，✗ 画面没变' : '');
    return '点了一下' + (v === true ? '，✓ 有效' : '');
  }
  return outcomeText(o);
}

function rowIcon(it) {
  const o = it.outcome || '';
  if (o === 'lobby_confirmed') return { icon: '✓', cls: 'ok' };
  if (o === 'tapped' && it.verify_success === true) return { icon: '✓', cls: 'ok' };
  if (o === 'tapped' && it.verify_success === false) return { icon: '✗', cls: 'fail' };
  if (o === 'loop_blocked') return { icon: '⚠', cls: 'warn' };
  if (o === 'no_target') return { icon: '○', cls: 'idle' };
  if (o.startsWith('lobby_pending')) return { icon: '◔', cls: 'warn' };
  return { icon: '·', cls: 'idle' };
}

/* ─── 渲染左侧列表 ─── */
function renderSidebar(newIds = []) {
  const c = document.getElementById('sidebar');
  knownIds = new Set(allItems.map(x => x.id));

  if (allItems.length === 0) {
    c.innerHTML = `<div class="sidebar-empty">
      暂无决策记录
      <div class="hint">在 GameBot 主面板点开始<br>跑实例触发 dismiss_popups 阶段</div>
    </div>`;
    return;
  }

  // 按实例分组
  const byInst = {};
  for (const it of allItems) {
    const k = it.instance ?? -1;
    if (!byInst[k]) byInst[k] = [];
    byInst[k].push(it);
  }
  const instances = Object.keys(byInst).map(x => parseInt(x))
    .sort((a, b) => a - b);

  let html = '';
  for (const inst of instances) {
    const items = byInst[inst].sort((a, b) => b.created - a.created);
    const lobbyDone = items.filter(x => x.outcome === 'lobby_confirmed').length;
    html += `<div class="inst-group">
      <div class="inst-head">
        <span class="inst-dot"></span>
        <span class="name">实例 #${inst}</span>
        <span class="stat">${items.length} 条 · 到大厅 ${lobbyDone}</span>
      </div>`;

    let prev = null;
    for (const it of items) {
      if (prev) {
        if (prev.outcome === 'lobby_confirmed') {
          html += `<div class="lobby-divider">━━ ${fmtTime(prev.created)} 清弹窗完成 ━━</div>`;
        } else if (prev.phase !== it.phase) {
          html += `<div class="phase-divider">切换到 ${phaseText(it.phase)}</div>`;
        }
      }
      const ic = rowIcon(it);
      const sel = it.id === selectedId ? 'selected' : '';
      const flash = newIds.includes(it.id) ? 'new-glow' : '';
      html += `<div class="deci-row ${sel} ${flash}" data-id="${it.id}" onclick="selectDecision('${it.id}')">
        <span class="icon ${ic.cls}">${ic.icon}</span>
        <div class="body">
          <div class="top">
            <span class="time">${fmtTime(it.created)}</span>
            <span class="round">R${it.round}</span>
          </div>
          <div class="story">${escapeHtml(storySummary(it))}</div>
        </div>
      </div>`;
      prev = it;
    }
    html += `</div>`;
  }
  c.innerHTML = html;

  // 选中态恢复
  if (selectedId) {
    const el = c.querySelector(`[data-id="${selectedId}"]`);
    if (el) el.classList.add('selected');
  }
}

/* ─── 选中决策 → 加载详情 ─── */
async function selectDecision(id) {
  selectedId = id;
  // 高亮
  document.querySelectorAll('.deci-row').forEach(x => x.classList.remove('selected'));
  const el = document.querySelector(`[data-id="${id}"]`);
  if (el) el.classList.add('selected');

  const detail = document.getElementById('detail');
  if (cachedDetails[id]) {
    detail.innerHTML = cachedDetails[id];
    return;
  }
  detail.innerHTML = '<div class="detail-empty">加载中…</div>';
  try {
    let url = '/api/decision/' + encodeURIComponent(id) + '/data';
    if (currentSession) url += '?session=' + encodeURIComponent(currentSession);
    const r = await fetch(url);
    if (!r.ok) {
      detail.innerHTML = `<div class="detail-empty"><div class="icon">⚠</div>加载失败 (HTTP ${r.status})</div>`;
      return;
    }
    const d = await r.json();
    const html = renderDetail(d);
    cachedDetails[id] = html;
    detail.innerHTML = html;
    detail.scrollTop = 0;
  } catch (e) {
    detail.innerHTML = `<div class="detail-empty"><div class="icon">⚠</div>加载失败: ${escapeHtml(e.message)}</div>`;
  }
}

/* ─── 详情面板 ─── */
function buildStoryParts(d) {
  const parts = [];
  let templateHit = null;
  const yoloHits = [];
  let ocrText = '';

  for (const t of (d.tiers || [])) {
    if (t.templates) {
      const hit = t.templates.find(x => x.hit);
      if (hit && !templateHit) templateHit = { name: hit.name, score: hit.score };
    }
    if (t.yolo_detections) {
      for (const det of t.yolo_detections)
        if (det.conf > 0.5) yoloHits.push(det);
    }
    if (t.ocr_hits && t.ocr_hits.length > 0) {
      ocrText = t.ocr_hits.map(x => x.text).join(' ');
    }
  }

  if (templateHit) {
    let label = templateHit.name;
    if (label.includes('lobby_start')) label = '「开始游戏」按钮';
    parts.push(`看到 <b>${escapeHtml(label)}</b>（模板分数 ${templateHit.score}）`);
  }
  if (yoloHits.length > 0) {
    const closeXs = yoloHits.filter(x => x.cls === 'close_x');
    const actions = yoloHits.filter(x => x.cls === 'action_btn');
    const seg = [];
    if (closeXs.length > 0) seg.push(`<b>${closeXs.length} 个 X 关闭按钮</b>（信心 ${closeXs[0].conf}）`);
    if (actions.length > 0) seg.push(`<b>${actions.length} 个文字按钮</b>（信心 ${actions[0].conf}）`);
    if (seg.length > 0) parts.push(`YOLO 模型识别出 ${seg.join(' + ')}`);
  } else if (templateHit && (d.tiers || []).some(t => t.name && t.name.includes('YOLO'))) {
    parts.push(`YOLO 没识别到弹窗按钮（说明画面干净）`);
  }
  if (ocrText) parts.push(`按钮文字识别为「<b>${escapeHtml(ocrText)}</b>」`);

  if (d.tap) {
    let action = '';
    if (d.tap.target_class === 'close_x') action = '点了关闭按钮';
    else if (d.tap.target_class === 'action_btn') action = '点了操作按钮';
    else action = '点了一下';
    parts.push(`<b>决定：${action}</b> @ (${d.tap.x}, ${d.tap.y})`);
  } else if (d.outcome === 'lobby_confirmed') {
    parts.push(`<b>判定：已到大厅，结束清弹窗</b>`);
  } else if (d.outcome && d.outcome.startsWith('lobby_pending')) {
    parts.push(`<b>判定：可能到大厅，再确认一次防误判</b>`);
  } else if (d.outcome === 'no_target') {
    parts.push(`<b>判定：暂时没目标</b>（画面可能在加载）`);
  } else if (d.outcome === 'loop_blocked') {
    parts.push(`<b>判定：同位置点 3 次没反应，等等再说</b>`);
  }
  return parts;
}

function renderDetail(d) {
  const sessParam = currentSession ? '?session=' + encodeURIComponent(currentSession) : '';
  const imgUrl = (name) =>
    '/api/decision/' + encodeURIComponent(d.id) + '/image/' + encodeURIComponent(name) + sessParam;

  // 主图
  let mainImg = '', mainLabel = '';
  if (d.tap && d.tap.annot_image) {
    mainImg = d.tap.annot_image; mainLabel = '红圈 = bot 点击位置';
  } else {
    for (const t of (d.tiers || [])) {
      if (t.yolo_annot_image) {
        mainImg = t.yolo_annot_image;
        mainLabel = (t.name || '').includes('大厅')
          ? '绿框 = lobby_start_btn 命中位置'
          : '红框 = close_x · 黄框 = action_btn';
        break;
      }
    }
  }
  if (!mainImg && d.input_image) {
    mainImg = d.input_image; mainLabel = '机器原始截图';
  }

  let html = `<div class="detail-head">
    <div class="row1">
      <span class="title">${phaseText(d.phase)}</span>
      <span class="meta">实例 <b>#${d.instance}</b> · R<b>${d.round}</b> · ${escapeHtml(fmtTime(d.created))}</span>
      <span class="id-tag">${escapeHtml(d.id)}</span>
    </div>
    <div class="meta">${escapeHtml(outcomeText(d.outcome))}</div>
  </div>
  <div class="detail-body">`;

  if (mainImg) {
    html += `<div class="img-wrap" style="margin-bottom:14px;">
      <span class="label">${mainLabel}</span>
      <img src="${imgUrl(mainImg)}" onclick="zoom(this.src)">
    </div>`;
  }

  // 故事卡
  const parts = buildStoryParts(d);
  if (parts.length > 0) {
    html += `<div class="story-card">
      <div class="h">bot 是这么想的</div>
      <ol>${parts.map(p => '<li>' + p + '</li>').join('')}</ol>
    </div>`;
  }

  // 验证
  if (d.verify) {
    const ok = d.verify.success;
    const cls = ok === true ? 'ok' : (ok === false ? 'fail' : 'unk');
    const txt = ok === true ? '✓ 画面变了 → 大概率点中'
              : ok === false ? '✗ 画面没变 → 点错或目标无效'
              : '? 没做验证';
    html += `<div class="verify-card ${cls}">
      <span class="result">${txt}</span>
      <span class="meta">画面变化度 ${d.verify.distance}</span>
    </div>`;
  }

  // 输入图
  if (d.input_image && d.input_image !== mainImg) {
    html += `<div class="panel">
      <div class="panel-head"><span class="h">原始截图</span>
        <span class="duration">${d.input_w}×${d.input_h} · phash=${d.input_phash || '-'}</span>
      </div>
      <div class="panel-body">
        <div class="img-wrap"><img src="${imgUrl(d.input_image)}" onclick="zoom(this.src)"></div>
      </div>
    </div>`;
  }

  // 点击详情
  if (d.tap) {
    html += `<div class="panel">
      <div class="panel-head"><span class="h">点击详情</span></div>
      <div class="panel-body">
        <div class="kv">
          <span class="k">点击坐标</span><span class="v">(${d.tap.x}, ${d.tap.y})</span>
          <span class="k">来自识别层</span><span class="v">${escapeHtml(d.tap.method || '-')}</span>
          ${d.tap.target_class ? `<span class="k">目标类别</span><span class="v">${escapeHtml(d.tap.target_class)}</span>` : ''}
          ${d.tap.target_text ? `<span class="k">目标文字</span><span class="v">${escapeHtml(d.tap.target_text)}</span>` : ''}
          ${d.tap.target_conf ? `<span class="k">置信度</span><span class="v">${d.tap.target_conf}</span>` : ''}
        </div>
      </div>
    </div>`;
  }

  // 验证细节
  if (d.verify) {
    html += `<div class="panel">
      <div class="panel-head"><span class="h">验证细节（phash 比对）</span></div>
      <div class="panel-body">
        <div class="kv">
          <span class="k">phash 之前</span><span class="v">${escapeHtml(d.verify.phash_before || '-')}</span>
          <span class="k">phash 之后</span><span class="v">${escapeHtml(d.verify.phash_after || '-')}</span>
          <span class="k">距离</span><span class="v">${d.verify.distance}</span>
          <span class="k">结论</span><span class="v">${d.verify.success === true ? '画面变了' : d.verify.success === false ? '画面没变' : '未判'}</span>
        </div>
      </div>
    </div>`;
  }

  // 各 Tier
  (d.tiers || []).forEach((t, idx) => {
    html += `<div class="panel">
      <div class="panel-head">
        <span class="badge-tier ${t.early_exit ? 'exit' : ''}">Tier ${t.tier}</span>
        <span class="h">${escapeHtml(t.name)}</span>
        <span class="duration">${t.duration_ms}ms${t.early_exit ? ' · 命中后退出' : ''}</span>
      </div>
      <div class="panel-body">`;

    if (t.templates && t.templates.length > 0) {
      const hits = t.templates.filter(x => x.hit).length;
      html += `<div style="font-size:11.5px;color:#5b6573;margin-bottom:10px;">
        试了 <b style="color:#1a2233">${t.templates.length}</b> 个模板，命中 <b style="color:#15803d">${hits}</b> 个
      </div>
      <div class="tmpl-grid">`;
      for (const tm of t.templates) {
        const cls = tm.hit ? 'hit' : 'miss';
        const tmplImg = tm.template_image
          ? `<img src="${imgUrl(tm.template_image)}" onclick="event.stopPropagation();zoom(this.src)">`
          : '<div style="height:50px;color:#8b95a5;text-align:center;line-height:50px;font-size:10px;background:#0a0c10;border-radius:3px;">缺图</div>';
        html += `<div class="tmpl-item ${cls}">${tmplImg}
          <div class="name">${escapeHtml(tm.name)}</div>
          <div class="score"><span>≥ ${tm.threshold}</span><b>${tm.score}${tm.hit ? ' ✓' : ''}</b></div>
        </div>`;
      }
      html += `</div>`;
    }

    if (t.yolo_annot_image) {
      const isLobby = (t.name || '').includes('大厅');
      html += `<div class="img-wrap" style="margin-top:12px;">
        <span class="label">${isLobby ? '模板命中位置' : 'YOLO 标注'}</span>
        <img src="${imgUrl(t.yolo_annot_image)}" onclick="zoom(this.src)">
      </div>`;
    }

    if (t.yolo_detections && t.yolo_detections.length > 0) {
      html += `<div class="det-list" style="margin-top:10px;">`;
      for (const det of t.yolo_detections) {
        html += `<div class="det-item">
          <span class="cls ${det.cls}">${escapeHtml(det.cls)}</span>
          <span class="conf">conf <b>${det.conf}</b></span>
          <span class="bbox">[${det.bbox.join(', ')}]</span>
        </div>`;
      }
      html += `</div>`;
    } else if ((t.name || '').includes('YOLO')) {
      html += `<div style="color:#8b95a5;font-size:11.5px;margin-top:10px;">YOLO 未检测到任何目标</div>`;
    }

    if (t.ocr_roi_image) {
      html += `<div class="img-wrap" style="margin-top:12px;">
        <span class="label">OCR 识别区域 · 橙框=ROI · 绿框=识别到的文字</span>
        <img src="${imgUrl(t.ocr_roi_image)}" onclick="zoom(this.src)">
      </div>`;
    }
    if (t.ocr_hits && t.ocr_hits.length > 0) {
      html += `<div class="det-list" style="margin-top:10px;">`;
      for (const h of t.ocr_hits) {
        html += `<div class="det-item">
          <span class="ocr-text">${escapeHtml(h.text)}</span>
          <span class="conf">${h.conf || ''}</span>
        </div>`;
      }
      html += `</div>`;
    } else if ((t.name || '').startsWith('OCR')) {
      html += `<div style="color:#8b95a5;font-size:11.5px;margin-top:10px;">OCR 未识别到任何文字</div>`;
    }

    if (t.memory_phash_query) {
      html += `<div class="kv" style="margin-top:10px;">
        <span class="k">phash 查询</span><span class="v">${escapeHtml(t.memory_phash_query)}</span>
        <span class="k">命中</span><span class="v">${t.memory_hit ? escapeHtml(JSON.stringify(t.memory_hit)) : '无（新画面）'}</span>
      </div>`;
    }

    if (t.note) {
      html += `<div style="margin-top:10px;padding:8px 11px;background:#fef3c7;border-left:3px solid #d97706;border-radius:4px;font-size:12px;color:#78350f;">
        📝 ${escapeHtml(t.note)}
      </div>`;
    }
    html += `</div></div>`;
  });

  if (d.note) {
    html += `<div class="panel">
      <div class="panel-head"><span class="h">决策备注</span></div>
      <div class="panel-body">${escapeHtml(d.note)}</div>
    </div>`;
  }

  html += `</div>`;
  return html;
}

function zoom(src) {
  document.getElementById('modal-img').src = src;
  document.getElementById('modal').classList.add('on');
}

function toggleAuto() {
  autoRefresh = !autoRefresh;
  const btn = document.getElementById('btn-auto');
  btn.classList.toggle('off', !autoRefresh);
  document.getElementById('auto-text').textContent =
    autoRefresh ? '自动刷新' : (currentSession ? '自动刷新（历史已停）' : '已暂停');
  showToast(autoRefresh ? '自动刷新已开启' : '自动刷新已关闭');
}

document.getElementById('filter-inst').addEventListener('input', () => {
  knownIds.clear();
  reload(true);
});

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    document.getElementById('modal').classList.remove('on');
    const m = document.getElementById('session-menu');
    if (m.classList.contains('on')) {
      m.classList.remove('on');
      document.getElementById('session-pill').classList.remove('active');
    }
  }
  // J/K：上下翻
  if ((e.key === 'j' || e.key === 'k') && !e.ctrlKey && !e.metaKey
      && document.activeElement.tagName !== 'INPUT') {
    e.preventDefault();
    if (allItems.length === 0) return;
    const cur = allItems.findIndex(x => x.id === selectedId);
    let next = cur;
    if (e.key === 'j') next = cur < 0 ? 0 : Math.min(cur + 1, allItems.length - 1);
    else next = cur < 0 ? 0 : Math.max(cur - 1, 0);
    if (allItems[next]) {
      selectDecision(allItems[next].id);
      const el = document.querySelector(`[data-id="${allItems[next].id}"]`);
      if (el) el.scrollIntoView({ block:'nearest', behavior:'smooth' });
    }
  }
});

reloadSessions();
reload(true);
setInterval(() => { if (autoRefresh && !currentSession) reload(); }, 1000);
setInterval(reloadSessions, 12000);
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

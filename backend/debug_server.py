"""
独立调试 web 服务器 — 跑在 0.0.0.0:8901，Mac 浏览器可访问。

跟主 dashboard (8900) 完全独立：
  - 不影响 GameBot.exe 桌面 webview
  - 提供实时帧 + ROI overlay + 添加 keyword/template

用法：
  Mac 浏览器打开 http://192.168.0.102:8901
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import threading
import time
from typing import Optional

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response
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


# ─────────── FastAPI app ───────────

app = FastAPI(title="GameBot Debug")


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML_PAGE


@app.get("/api/status")
async def api_status():
    """返回每实例当前状态 + 阶段时长"""
    if _service is None:
        return {"running": False, "instances": []}
    out = []
    for idx in sorted(_service._runners.keys()):
        runner = _service._runners[idx]
        info = {
            "idx": idx,
            "role": getattr(runner, "role", "?"),
            "group": getattr(runner, "group", "?"),
            "phase": getattr(runner.phase, "value", str(runner.phase)) if hasattr(runner, "phase") else "?",
        }
        # 状态信息（如果存在）
        st = _service._instance_status.get(idx) if hasattr(_service, "_instance_status") else None
        if st:
            info["state"] = st.state
            info["error"] = st.error
            info["stage_times"] = dict(st.stage_times) if st.stage_times else {}
        out.append(info)
    return {
        "running": getattr(_service, "running", False),
        "session_dir": _session_dir,
        "instances": out,
    }


@app.get("/api/screenshot/{idx}.jpg")
async def api_screenshot(idx: int, q: int = 75):
    """实时截图 JPEG（默认 quality 75，~50KB）"""
    if _service is None or idx not in _service._runners:
        raise HTTPException(404, "instance not found")
    runner = _service._runners[idx]
    adb = getattr(runner, "adb", None)
    if adb is None:
        raise HTTPException(500, "adb not initialized")
    raw_adb = getattr(adb, "_adb", adb)  # 解包 GuardedAdb → ADBController
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


@app.get("/api/rules")
async def api_rules():
    """返回当前 popup_rules.json"""
    from .automation.rules_loader import RulesLoader
    return {
        "path": RulesLoader.path(),
        "rules": RulesLoader.get(),
    }


class AddKeywordReq(BaseModel):
    field: str          # close_text / confirm_text / checkbox_text / loading_keywords / lobby_keywords / ...
    text: str           # 要加的关键字


@app.post("/api/add_keyword")
async def api_add_keyword(req: AddKeywordReq):
    """把 text 加到 field 列表，写回 popup_rules.json，下一轮 dismiss_popups 自动生效"""
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

    logger.info(f"[debug] 加 keyword: {req.field}.append({text!r}) → {path}")
    return {"ok": True, "field": req.field, "text": text, "list_len": len(rules[req.field])}


@app.get("/api/log/tail")
async def api_log_tail(n: int = 100):
    """读当前 session 的 run.log 最后 N 行"""
    if not _session_dir:
        return {"lines": [], "session_dir": None}
    log_path = os.path.join(_session_dir, "run.log")
    if not os.path.isfile(log_path):
        return {"lines": [], "session_dir": _session_dir, "error": "run.log 不存在"}
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        return {"lines": all_lines[-n:], "session_dir": _session_dir}
    except Exception as e:
        return {"lines": [], "error": str(e)}


# ─────────── HTML page (single file, plain JS) ───────────

HTML_PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>GameBot Debug</title>
<style>
  * { box-sizing: border-box; }
  body { margin:0; padding:12px; font-family: -apple-system, "PingFang SC", sans-serif;
         background:#1a1a1f; color:#e0e0e8; }
  h1 { margin:0 0 12px; font-size:18px; color:#7aa3ff; }
  .grid { display:grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); gap:12px; }
  .card { background:#252530; border:1px solid #3a3a48; border-radius:6px; padding:8px; }
  .card h3 { margin:0 0 6px; font-size:14px; color:#ffc66d; display:flex; justify-content:space-between; }
  .badge { background:#444; padding:2px 8px; border-radius:4px; font-size:12px; color:#ddd; }
  .badge.done { background:#2d6a4f; color:#fff; }
  .badge.error { background:#9d3b3b; color:#fff; }
  .badge.init { background:#5a4a8a; color:#fff; }
  .shot { width:100%; height:auto; max-height:240px; object-fit:contain;
          background:#000; cursor:zoom-in; }
  .meta { font-size:12px; color:#aab; margin-top:4px; }
  .controls { padding:10px; background:#252530; border:1px solid #3a3a48; border-radius:6px; margin-bottom:12px; }
  .controls input, .controls select, .controls button {
    background:#1a1a1f; color:#e0e0e8; border:1px solid #3a3a48; padding:6px 10px;
    border-radius:4px; font-size:13px; }
  .controls button { background:#3a5fbf; cursor:pointer; }
  .controls button:hover { background:#4a6fd0; }
  pre { background:#0e0e14; padding:6px; max-height:160px; overflow:auto; font-size:11px;
        margin:6px 0 0; color:#9aa; border-radius:4px; }
  .modal { position:fixed; top:0; left:0; right:0; bottom:0; background:rgba(0,0,0,0.85);
           display:none; justify-content:center; align-items:center; z-index:99; }
  .modal.show { display:flex; }
  .modal img { max-width:95vw; max-height:95vh; }
  .session { font-size:11px; color:#888; margin-bottom:8px; }
</style>
</head>
<body>
<h1>GameBot Debug · 实时调试</h1>
<div class="session" id="session">session: -</div>

<div class="controls">
  <strong>加 keyword（写入 popup_rules.json，立刻生效）：</strong>&nbsp;
  <select id="kw-field">
    <option value="close_text">close_text (关闭按钮文字)</option>
    <option value="confirm_text">confirm_text (确认/同意/跳过)</option>
    <option value="checkbox_text">checkbox_text (今日不再弹出)</option>
    <option value="lobby_keywords">lobby_keywords (大厅判定)</option>
    <option value="loading_keywords">loading_keywords (加载中判定)</option>
    <option value="login_keywords">login_keywords (登录页判定)</option>
    <option value="left_game_keywords">left_game_keywords (退游判定)</option>
  </select>
  <input id="kw-text" placeholder="新关键字…" style="width:200px">
  <button onclick="addKw()">加</button>
  <span id="kw-status" style="color:#7d7;"></span>
</div>

<div class="grid" id="grid">loading…</div>

<div class="modal" id="modal" onclick="this.classList.remove('show')">
  <img id="modal-img">
</div>

<script>
const grid = document.getElementById('grid');
const sessionEl = document.getElementById('session');
const ts = () => Math.floor(Date.now()/1000);

async function refresh(){
  let data;
  try { data = await (await fetch('/api/status')).json(); }
  catch(e){ grid.innerHTML = '<div style="color:#f55">服务挂了：' + e.message + '</div>'; return; }
  sessionEl.textContent = 'session: ' + (data.session_dir || '-') + ' · running=' + data.running;
  if (!data.instances || data.instances.length === 0){
    grid.innerHTML = '<div>没有运行中的实例。在 GameBot 主界面点开始。</div>';
    return;
  }
  grid.innerHTML = data.instances.map(i => {
    const phase = i.phase || '?';
    const state = i.state || phase;
    const err = i.error || '';
    const stages = i.stage_times || {};
    const stageStr = Object.entries(stages).map(([k,v]) => k+'='+v.toFixed(0)+'s').join(' · ');
    let badgeClass = 'init';
    if (state === 'done') badgeClass = 'done';
    if (err && err !== '') badgeClass = 'error';
    return `
      <div class="card">
        <h3>
          #${i.idx} · ${i.group||'?'}/${i.role||'?'}
          <span class="badge ${badgeClass}">${state}</span>
        </h3>
        <img class="shot" src="/api/screenshot/${i.idx}.jpg?t=${ts()}" alt="loading…"
             onclick="zoom(this.src)" onerror="this.style.background='#3a1818'">
        <div class="meta">phase: ${phase} ${err? ' · err: '+err : ''}</div>
        <div class="meta">${stageStr}</div>
      </div>`;
  }).join('');
}

function zoom(src){
  const img = document.getElementById('modal-img');
  img.src = src;
  document.getElementById('modal').classList.add('show');
}

async function addKw(){
  const field = document.getElementById('kw-field').value;
  const text = document.getElementById('kw-text').value.trim();
  if (!text){ alert('文字不能空'); return; }
  const status = document.getElementById('kw-status');
  status.textContent = '...';
  try {
    const r = await fetch('/api/add_keyword', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({field, text})
    });
    const j = await r.json();
    if (j.ok){
      status.textContent = '✓ 加成功: ' + j.field + ' (共 ' + (j.list_len || '?') + ' 条)' +
                           (j.already_present ? ' (已存在)' : '');
      document.getElementById('kw-text').value = '';
    } else {
      status.textContent = '✗ ' + JSON.stringify(j);
    }
    setTimeout(()=> status.textContent = '', 4000);
  } catch(e){ status.textContent = '✗ ' + e.message; }
}

refresh();
setInterval(refresh, 1500);
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
        logger.info("[debug] server 已在跑，跳过")
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
            logger.error(f"[debug] server 崩溃: {e}")

    _server_thread = threading.Thread(target=_run, daemon=True, name="debug-server")
    _server_thread.start()
    logger.info(f"[debug] server 启动 http://{host}:{port}/")

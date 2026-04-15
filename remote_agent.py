"""
Remote Agent v2 — 远程命令执行代理

改进:
  - 持久 token (存 .remote_agent_token.txt，重启不变，Claude 记一次即可)
  - 自动启动 cloudflared，解析公网 URL
  - WebSocket 流式输出 + 内嵌 Web 终端 UI
  - 无命令白名单，token 即权限

启动:
  python remote_agent.py

Web UI:
  http://localhost:9100/ui?token=<TOKEN>

Claude 获取当前公网 URL:
  GET http://192.168.0.102:9100/  →  cf_url 字段
"""

import asyncio
import json
import logging
import os
import re
import secrets
import subprocess
import sys
import time
import threading
import socket
from datetime import datetime
from pathlib import Path

try:
    from fastapi import FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
    from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, HTMLResponse
    from pydantic import BaseModel
    import uvicorn
except ImportError:
    print("请先安装依赖:")
    print("  pip install fastapi 'uvicorn[standard]'")
    sys.exit(1)

# =====================
# 配置
# =====================

PORT = 9100
WORK_DIR = Path.home()
TOKEN_FILE = Path(__file__).parent / ".remote_agent_token.txt"
COMMAND_TIMEOUT = 600  # 10 分钟

# =====================
# 持久 Token
# =====================

def _load_or_create_token() -> str:
    if TOKEN_FILE.exists():
        t = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if len(t) >= 16:
            return t
    t = secrets.token_urlsafe(16)
    TOKEN_FILE.write_text(t, encoding="utf-8")
    return t

TOKEN = _load_or_create_token()
START_TIME = time.time()
CURRENT_DIR = WORK_DIR
CLOUDFLARE_URL = ""
HOSTNAME = socket.gethostname()
COMMAND_HISTORY: list[dict] = []

# =====================
# 日志
# =====================

LOG_FILE = Path(__file__).parent / "remote_agent.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# =====================
# cloudflared 自动启动 + beacon 上报
# =====================

BEACON_URL = "http://111.170.170.149:9901/api/beacon"

def _post_beacon():
    """把 cloudflared URL 上报到 gameproxy beacon，Claude 查一次就能找到"""
    if not CLOUDFLARE_URL:
        return
    import urllib.request
    data = json.dumps({
        "hostname": HOSTNAME,
        "url": CLOUDFLARE_URL,
        "ui": f"{CLOUDFLARE_URL}/ui?token={TOKEN}",
        "token": TOKEN,
        "platform": sys.platform,
    }).encode()
    req = urllib.request.Request(BEACON_URL, data=data,
                                  headers={"Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=5)
        logger.info(f"beacon 上报成功: {CLOUDFLARE_URL}")
    except Exception as e:
        logger.debug(f"beacon 上报失败(可忽略): {e}")

def _start_cloudflared():
    global CLOUDFLARE_URL
    try:
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        proc = subprocess.Popen(
            ["cloudflared", "tunnel", "--url", f"http://localhost:{PORT}"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=flags,
        )
        logger.info("cloudflared 启动中，等待公网 URL...")
        for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace")
            m = re.search(r'https://[a-z0-9-]+\.trycloudflare\.com', line)
            if m:
                CLOUDFLARE_URL = m.group()
                logger.info(f"Cloudflare URL: {CLOUDFLARE_URL}")
                print(f"\n  公网 URL: {CLOUDFLARE_URL}")
                print(f"  Web UI:  {CLOUDFLARE_URL}/ui?token={TOKEN}\n")
                _post_beacon()
                break
        proc.wait()
    except FileNotFoundError:
        logger.warning("cloudflared 未安装，跳过（仅局域网可用）")
    except Exception as e:
        logger.warning(f"cloudflared 启动失败: {e}")

threading.Thread(target=_start_cloudflared, daemon=True).start()

# =====================
# 命令执行 (无白名单)
# =====================

async def run_command(cmd: str, cwd: str | None = None) -> dict:
    global CURRENT_DIR
    if cwd is None:
        cwd = str(CURRENT_DIR)

    # cd 特殊处理（subprocess 不影响主进程目录）
    s = cmd.strip()
    if s.lower().startswith("cd "):
        target = s[3:].strip().strip('"').strip("'")
        if target.lower().startswith("/d "):
            target = target[3:].strip()
        try:
            new = (Path(cwd) / target).resolve() if not os.path.isabs(target) else Path(target).resolve()
            if not new.exists():
                return {"ok": False, "stdout": "", "stderr": f"目录不存在: {new}",
                        "returncode": 1, "cwd": cwd, "duration_ms": 0}
            CURRENT_DIR = new
            return {"ok": True, "stdout": f"已切换到: {new}", "stderr": "",
                    "returncode": 0, "cwd": str(new), "duration_ms": 0}
        except Exception as e:
            return {"ok": False, "stdout": "", "stderr": str(e),
                    "returncode": 1, "cwd": cwd, "duration_ms": 0}

    start = time.time()
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=COMMAND_TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {"ok": False, "stdout": "", "stderr": f"超时 ({COMMAND_TIMEOUT}s)",
                    "returncode": -1, "cwd": cwd, "duration_ms": int((time.time()-start)*1000)}

        def decode(b: bytes) -> str:
            for enc in ("utf-8", "gbk", "gb2312"):
                try:
                    return b.decode(enc)
                except UnicodeDecodeError:
                    continue
            return b.decode("utf-8", errors="replace")

        return {
            "ok": proc.returncode == 0,
            "stdout": decode(stdout),
            "stderr": decode(stderr),
            "returncode": proc.returncode,
            "cwd": str(CURRENT_DIR),
            "duration_ms": int((time.time()-start)*1000),
        }
    except Exception as e:
        return {"ok": False, "stdout": "", "stderr": f"执行异常: {e}",
                "returncode": -1, "cwd": cwd, "duration_ms": int((time.time()-start)*1000)}

# =====================
# 内嵌 Web 终端 UI
# =====================

_UI_HTML = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Remote Agent</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0d1117;color:#e6edf3;font-family:'Cascadia Code','Consolas',monospace;height:100vh;display:flex;flex-direction:column;overflow:hidden}
#hdr{padding:10px 16px;background:#161b22;border-bottom:1px solid #30363d;display:flex;align-items:center;justify-content:space-between;flex-shrink:0}
.htitle{color:#58a6ff;font-size:14px;font-weight:600}
.hcwd{color:#8b949e;font-size:11px;margin-left:12px}
#cf-url{font-size:11px;color:#3fb950;margin-right:10px}
#ws-st{font-size:11px;color:#8b949e}
#term{flex:1;overflow-y:auto;padding:12px 16px;font-size:13px;line-height:1.6}
.ln{white-space:pre-wrap;word-break:break-all}
.cmd-ln{color:#79c0ff}.cmd-ln::before{content:"❯ ";color:#3fb950}
.out{color:#e6edf3}.err{color:#ff7b72}.sys{color:#a371f7;font-style:italic}
.ok{color:#3fb950}.fail{color:#ff7b72}
#inp{padding:8px 16px;background:#161b22;border-top:1px solid #30363d;display:flex;gap:8px;align-items:center;flex-shrink:0}
#cmd{flex:1;background:transparent;border:none;outline:none;color:#e6edf3;font-family:inherit;font-size:13px}
#run{background:#238636;border:none;color:#fff;padding:5px 14px;border-radius:6px;cursor:pointer;font-size:12px}
::-webkit-scrollbar{width:6px}::-webkit-scrollbar-track{background:#0d1117}::-webkit-scrollbar-thumb{background:#30363d;border-radius:3px}
</style>
</head>
<body>
<div id="hdr">
  <div style="display:flex;align-items:center">
    <span class="htitle">⚡ Remote Agent</span>
    <span class="hcwd" id="cwd-d">~</span>
  </div>
  <div style="display:flex;align-items:center">
    <span id="cf-url"></span>
    <span id="ws-st">connecting...</span>
  </div>
</div>
<div id="term"></div>
<div id="inp">
  <span style="color:#3fb950;font-size:13px;white-space:nowrap">$&nbsp;</span>
  <input id="cmd" type="text" placeholder="输入命令..." autocomplete="off" spellcheck="false">
  <button id="run">运行</button>
</div>
<script>
const TOKEN=new URLSearchParams(location.search).get('token')||'';
const term=document.getElementById('term');
const cmdEl=document.getElementById('cmd');
const cwdEl=document.getElementById('cwd-d');
const wsStEl=document.getElementById('ws-st');
const cfEl=document.getElementById('cf-url');
const hist=[];let hi=-1;

function addLine(text,cls){
  if(!text&&cls!=='ok'&&cls!=='fail')return;
  const d=document.createElement('div');
  d.className='ln '+cls;
  d.textContent=text;
  term.appendChild(d);
  term.scrollTop=term.scrollHeight;
}

function send(cmd){
  if(!cmd.trim())return;
  hist.unshift(cmd);hi=-1;
  addLine(cmd,'cmd-ln');
  if(ws&&ws.readyState===WebSocket.OPEN)ws.send(JSON.stringify({cmd}));
  else addLine('WebSocket 未连接','err');
  cmdEl.value='';
}

document.getElementById('run').onclick=()=>send(cmdEl.value);
cmdEl.addEventListener('keydown',e=>{
  if(e.key==='Enter'){send(cmdEl.value);return;}
  if(e.key==='ArrowUp'){hi=Math.min(hi+1,hist.length-1);cmdEl.value=hist[hi]||'';e.preventDefault();}
  if(e.key==='ArrowDown'){hi=Math.max(hi-1,-1);cmdEl.value=hi<0?'':hist[hi];e.preventDefault();}
});

let ws;
function connect(){
  const proto=location.protocol==='https:'?'wss:':'ws:';
  ws=new WebSocket(proto+'//'+location.host+'/ws/exec?token='+encodeURIComponent(TOKEN));
  ws.onopen=()=>{
    wsStEl.textContent='已连接';wsStEl.style.color='#3fb950';
    addLine('已连接到 Remote Agent','sys');
    ws.send(JSON.stringify({cmd:'__status__'}));
  };
  ws.onmessage=e=>{
    const m=JSON.parse(e.data);
    if(m.type==='out'){const t=m.data.replace(/\r\n|\r/g,'\n');t.split('\n').forEach(l=>addLine(l,'out'));}
    else if(m.type==='err'){const t=m.data.replace(/\r\n|\r/g,'\n');t.split('\n').forEach(l=>addLine(l,'err'));}
    else if(m.type==='done')addLine(m.code===0?'✓ 完成':'✗ 退出码 '+m.code,m.code===0?'ok':'fail');
    else if(m.type==='status'){cwdEl.textContent=m.cwd||'~';if(m.cf_url){cfEl.textContent='🌐 '+m.cf_url+' |';}}
  };
  ws.onerror=()=>{wsStEl.textContent='错误';wsStEl.style.color='#ff7b72';};
  ws.onclose=()=>{wsStEl.textContent='断开，3s后重连';wsStEl.style.color='#f0883e';setTimeout(connect,3000);};
}
connect();
</script>
</body>
</html>"""

# =====================
# FastAPI 应用
# =====================

app = FastAPI(title="Remote Agent v2", docs_url=None, redoc_url=None)


class ExecRequest(BaseModel):
    cmd: str
    cwd: str | None = None
    timeout: int | None = None


def check_auth(x_auth: str | None):
    if not x_auth or x_auth != TOKEN:
        raise HTTPException(status_code=401, detail="Token 错误")


@app.get("/")
async def root():
    return {
        "name": "Remote Agent",
        "version": "2.0",
        "uptime": int(time.time() - START_TIME),
        "hostname": HOSTNAME,
        "platform": sys.platform,
        "python": sys.version,
        "current_dir": str(CURRENT_DIR),
        "cf_url": CLOUDFLARE_URL,
        "ui": f"http://localhost:{PORT}/ui?token={TOKEN}",
        "hint": "需要 X-Auth header 执行命令",
    }


@app.get("/health")
async def health():
    return {"ok": True, "uptime": int(time.time() - START_TIME)}


@app.get("/ui", response_class=HTMLResponse)
async def ui():
    return HTMLResponse(_UI_HTML)


@app.post("/exec")
async def execute(req: ExecRequest, x_auth: str | None = Header(None)):
    check_auth(x_auth)
    logger.info(f"exec: {req.cmd}")
    result = await run_command(req.cmd, req.cwd)
    result["cmd"] = req.cmd
    COMMAND_HISTORY.append({
        "timestamp": time.time(),
        "cmd": req.cmd,
        "ok": result["ok"],
        "duration_ms": result["duration_ms"],
    })
    if len(COMMAND_HISTORY) > 100:
        COMMAND_HISTORY.pop(0)
    return result


@app.get("/history")
async def get_history(x_auth: str | None = Header(None)):
    check_auth(x_auth)
    return {"history": COMMAND_HISTORY[-50:]}


@app.get("/read")
async def read_file(path: str, max_size: int = 1024 * 1024,
                    x_auth: str | None = Header(None)):
    check_auth(x_auth)
    p = Path(path) if os.path.isabs(path) else CURRENT_DIR / path
    if not p.exists():
        raise HTTPException(404, "文件不存在")
    if p.stat().st_size > max_size:
        raise HTTPException(413, f"文件超过 {max_size} bytes")
    for enc in ("utf-8", "gbk", "gb2312"):
        try:
            return PlainTextResponse(p.read_text(encoding=enc))
        except UnicodeDecodeError:
            continue
    return PlainTextResponse(p.read_bytes().decode("utf-8", errors="replace"))


@app.get("/download")
async def download(path: str, x_auth: str | None = Header(None)):
    check_auth(x_auth)
    p = Path(path) if os.path.isabs(path) else CURRENT_DIR / path
    if not p.exists() or not p.is_file():
        raise HTTPException(404, "文件不存在")
    return FileResponse(p)


# =====================
# WebSocket 流式终端
# =====================

@app.websocket("/ws/exec")
async def ws_exec(ws: WebSocket, token: str = ""):
    if token != TOKEN:
        await ws.close(code=4001)
        return
    await ws.accept()

    async def send(msg: dict):
        try:
            await ws.send_json(msg)
        except Exception:
            pass

    try:
        while True:
            data = await ws.receive_json()
            cmd = data.get("cmd", "")

            if cmd == "__status__":
                await send({"type": "status", "cwd": str(CURRENT_DIR), "cf_url": CLOUDFLARE_URL})
                continue

            if not cmd.strip():
                continue

            logger.info(f"[WS] {cmd}")

            # cd 走同步路径
            if cmd.strip().lower().startswith("cd "):
                r = await run_command(cmd)
                await send({"type": "out" if r["ok"] else "err",
                            "data": r["stdout"] or r["stderr"]})
                await send({"type": "done", "code": r["returncode"]})
                await send({"type": "status", "cwd": str(CURRENT_DIR), "cf_url": CLOUDFLARE_URL})
                continue

            # 流式执行
            try:
                proc = await asyncio.create_subprocess_shell(
                    cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(CURRENT_DIR),
                )

                async def drain(pipe, mtype: str):
                    while True:
                        chunk = await pipe.read(4096)
                        if not chunk:
                            break
                        await send({"type": mtype, "data": chunk.decode("utf-8", errors="replace")})

                await asyncio.gather(drain(proc.stdout, "out"), drain(proc.stderr, "err"))
                await proc.wait()
                await send({"type": "done", "code": proc.returncode})
                await send({"type": "status", "cwd": str(CURRENT_DIR), "cf_url": CLOUDFLARE_URL})

            except Exception as e:
                await send({"type": "err", "data": str(e)})
                await send({"type": "done", "code": -1})

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug(f"WS 断开: {e}")


# =====================
# 启动
# =====================

def main():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        local_ip = "127.0.0.1"

    print()
    print("=" * 68)
    print("  Remote Agent v2")
    print("=" * 68)
    print()
    print(f"  主机名:  {HOSTNAME}")
    print(f"  局域网:  http://{local_ip}:{PORT}")
    print(f"  Web UI:  http://{local_ip}:{PORT}/ui?token={TOKEN}")
    print()
    print(f"  ┌──────────────────────────────────────────────────────────┐")
    tok_line = f"  │  Token: {TOKEN}"
    print(tok_line.ljust(64) + "│")
    print(f"  └──────────────────────────────────────────────────────────┘")
    print()
    print("  cloudflared 启动中... (公网 URL 稍后显示)")
    print("  按 Ctrl+C 停止")
    print("=" * 68)
    print()

    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()

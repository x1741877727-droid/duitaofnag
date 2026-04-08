"""
Remote Agent — 远程命令执行代理
让 Claude 通过 HTTP API 远程帮 Windows 用户执行安装/调试命令

启动:
  python remote_agent.py
  (会自动生成密码并打印)

通过 cloudflared 暴露:
  cloudflared tunnel --url http://localhost:9100

安全机制:
  - 必须带密码 (header X-Auth)
  - 命令白名单
  - 危险关键词黑名单
  - 所有命令记日志到 remote_agent.log
"""

import asyncio
import json
import logging
import os
import secrets
import shlex
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    from fastapi import FastAPI, Header, HTTPException, Request
    from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
    from pydantic import BaseModel
    import uvicorn
except ImportError:
    print("请先安装 FastAPI:")
    print("  pip install fastapi uvicorn")
    sys.exit(1)


# =====================
# 配置
# =====================

PORT = 9100
WORK_DIR = Path.home()  # 默认工作目录: 用户主目录

# 命令白名单：只允许这些可执行文件（程序名）
ALLOWED_COMMANDS = {
    "git", "python", "pip", "pip3", "python3",
    "node", "npm", "npx", "yarn",
    "cd", "dir", "ls", "type", "cat", "echo", "where", "which",
    "tasklist", "chcp", "set",
    "cloudflared",
    "powershell",  # 用于一些特殊查询
    "adb",  # Android 调试，用于 logcat / install / shell
    "findstr", "find",  # Windows/通用 文本过滤
    "more",  # 分页查看
}

# 危险关键词（命令中包含就拒绝）
DANGEROUS_PATTERNS = [
    "format ", "del /", "rmdir /s", "rm -rf", "rm -r",
    ":(){", "shutdown", "restart-computer",
    "reg delete", "reg add hklm",
    "diskpart", "fdisk", "mkfs",
    "net user", "net localgroup",
    " | curl ", " | wget ", "curl | sh", "wget | sh",
    "iex(", "iex ", "invoke-expression",
    "&&del", ";del", "&del",
    "/format", "/fs:ntfs",
]

# 命令最大执行时间
COMMAND_TIMEOUT = 600  # 10 分钟（pip install 可能很久）

# =====================
# 状态
# =====================

PASSWORD = secrets.token_urlsafe(16)  # 启动时生成的随机密码

# 把密码写到磁盘，方便远程获取（避免你看图打字）
try:
    _pwd_file = Path(__file__).parent / ".remote_agent_password.txt"
    _pwd_file.write_text(PASSWORD, encoding="utf-8")
except Exception:
    pass
START_TIME = time.time()
COMMAND_HISTORY: list[dict] = []
CURRENT_DIR = WORK_DIR

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
# 安全检查
# =====================

def is_command_allowed(cmd: str) -> tuple[bool, str]:
    """检查命令是否允许执行"""
    cmd_lower = cmd.lower().strip()

    if not cmd_lower:
        return False, "空命令"

    # 检查危险关键词
    for pattern in DANGEROUS_PATTERNS:
        if pattern in cmd_lower:
            return False, f"包含危险关键词: {pattern.strip()}"

    # 提取主程序
    try:
        parts = shlex.split(cmd, posix=False)
    except ValueError:
        parts = cmd.split()

    if not parts:
        return False, "无法解析命令"

    main_cmd = parts[0].lower()
    # 去掉路径前缀和扩展名
    main_cmd = os.path.basename(main_cmd).replace(".exe", "").replace(".bat", "").replace(".cmd", "")

    if main_cmd not in ALLOWED_COMMANDS:
        return False, f"命令 '{main_cmd}' 不在白名单 (允许: {', '.join(sorted(ALLOWED_COMMANDS))})"

    return True, ""


def check_auth(x_auth: str | None):
    """验证密码"""
    if not x_auth or x_auth != PASSWORD:
        raise HTTPException(status_code=401, detail="密码错误")


# =====================
# 命令执行
# =====================

async def run_command(cmd: str, cwd: str | None = None) -> dict:
    """异步执行命令"""
    global CURRENT_DIR

    if cwd is None:
        cwd = str(CURRENT_DIR)

    # 处理 cd 命令（特殊处理，因为 subprocess 不会改主进程目录）
    cmd_stripped = cmd.strip()
    if cmd_stripped.lower().startswith("cd "):
        target = cmd_stripped[3:].strip().strip('"').strip("'")
        # 处理 cd /d X:\xxx
        if target.lower().startswith("/d "):
            target = target[3:].strip()
        try:
            new_path = (Path(cwd) / target).resolve() if not os.path.isabs(target) else Path(target).resolve()
            if not new_path.exists():
                return {
                    "ok": False,
                    "stdout": "",
                    "stderr": f"目录不存在: {new_path}",
                    "returncode": 1,
                    "cwd": cwd,
                    "duration_ms": 0,
                }
            CURRENT_DIR = new_path
            return {
                "ok": True,
                "stdout": f"已切换目录到: {new_path}",
                "stderr": "",
                "returncode": 0,
                "cwd": str(new_path),
                "duration_ms": 0,
            }
        except Exception as e:
            return {
                "ok": False,
                "stdout": "",
                "stderr": str(e),
                "returncode": 1,
                "cwd": cwd,
                "duration_ms": 0,
            }

    start = time.time()
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=COMMAND_TIMEOUT,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {
                "ok": False,
                "stdout": "",
                "stderr": f"命令超时 ({COMMAND_TIMEOUT}s)",
                "returncode": -1,
                "cwd": cwd,
                "duration_ms": int((time.time() - start) * 1000),
            }

        duration_ms = int((time.time() - start) * 1000)

        # 尝试用多种编码解码（Windows 中文环境常见）
        def decode(data: bytes) -> str:
            for enc in ("utf-8", "gbk", "gb2312"):
                try:
                    return data.decode(enc)
                except UnicodeDecodeError:
                    continue
            return data.decode("utf-8", errors="replace")

        return {
            "ok": proc.returncode == 0,
            "stdout": decode(stdout),
            "stderr": decode(stderr),
            "returncode": proc.returncode,
            "cwd": cwd,
            "duration_ms": duration_ms,
        }
    except Exception as e:
        return {
            "ok": False,
            "stdout": "",
            "stderr": f"执行异常: {e}",
            "returncode": -1,
            "cwd": cwd,
            "duration_ms": int((time.time() - start) * 1000),
        }


# =====================
# FastAPI 应用
# =====================

app = FastAPI(title="Remote Agent", docs_url="/docs")


class ExecRequest(BaseModel):
    cmd: str
    cwd: str | None = None


@app.get("/")
async def root():
    return {
        "name": "Remote Agent",
        "version": "1.0",
        "uptime": int(time.time() - START_TIME),
        "current_dir": str(CURRENT_DIR),
        "platform": sys.platform,
        "python": sys.version,
        "hint": "需要 X-Auth header 才能执行命令",
    }


@app.get("/health")
async def health():
    return {"ok": True, "uptime": int(time.time() - START_TIME)}


@app.post("/exec")
async def execute(req: ExecRequest, x_auth: str | None = Header(None)):
    """执行命令"""
    check_auth(x_auth)

    allowed, reason = is_command_allowed(req.cmd)
    if not allowed:
        logger.warning(f"拒绝命令: {req.cmd} ({reason})")
        return JSONResponse(
            status_code=403,
            content={"ok": False, "error": f"命令被拒绝: {reason}", "cmd": req.cmd},
        )

    logger.info(f"执行: {req.cmd} (cwd={req.cwd or CURRENT_DIR})")

    result = await run_command(req.cmd, req.cwd)
    result["cmd"] = req.cmd

    # 记录历史
    COMMAND_HISTORY.append({
        "timestamp": time.time(),
        "cmd": req.cmd,
        "ok": result["ok"],
        "duration_ms": result["duration_ms"],
    })
    if len(COMMAND_HISTORY) > 100:
        COMMAND_HISTORY.pop(0)

    return result


@app.get("/cwd")
async def get_cwd(x_auth: str | None = Header(None)):
    """获取当前工作目录"""
    check_auth(x_auth)
    return {"cwd": str(CURRENT_DIR)}


@app.post("/cwd")
async def set_cwd(path: str, x_auth: str | None = Header(None)):
    """设置工作目录"""
    check_auth(x_auth)
    global CURRENT_DIR
    new_path = Path(path).resolve()
    if not new_path.exists():
        raise HTTPException(status_code=404, detail=f"目录不存在: {new_path}")
    CURRENT_DIR = new_path
    return {"ok": True, "cwd": str(CURRENT_DIR)}


@app.get("/ls")
async def list_dir(path: str | None = None, x_auth: str | None = Header(None)):
    """列出目录内容"""
    check_auth(x_auth)
    target = Path(path) if path else CURRENT_DIR
    if not target.exists():
        raise HTTPException(status_code=404, detail="目录不存在")
    try:
        items = []
        for entry in sorted(target.iterdir()):
            try:
                stat = entry.stat()
                items.append({
                    "name": entry.name,
                    "type": "dir" if entry.is_dir() else "file",
                    "size": stat.st_size,
                    "modified": stat.st_mtime,
                })
            except OSError:
                pass
        return {"path": str(target.resolve()), "items": items}
    except PermissionError:
        raise HTTPException(status_code=403, detail="无权限访问")


@app.get("/read")
async def read_file(path: str, max_size: int = 1024 * 1024,
                    x_auth: str | None = Header(None)):
    """读取小文件内容（最大 1MB）"""
    check_auth(x_auth)
    p = Path(path) if os.path.isabs(path) else CURRENT_DIR / path
    if not p.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    if p.stat().st_size > max_size:
        raise HTTPException(status_code=413, detail=f"文件超过 {max_size} bytes")
    try:
        for enc in ("utf-8", "gbk", "gb2312"):
            try:
                return PlainTextResponse(p.read_text(encoding=enc))
            except UnicodeDecodeError:
                continue
        return PlainTextResponse(p.read_bytes().decode("utf-8", errors="replace"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/download")
async def download_file(path: str, x_auth: str | None = Header(None)):
    """下载二进制文件"""
    check_auth(x_auth)
    p = Path(path) if os.path.isabs(path) else CURRENT_DIR / path
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(p)


@app.get("/history")
async def get_history(x_auth: str | None = Header(None)):
    """查看命令历史"""
    check_auth(x_auth)
    return {"history": COMMAND_HISTORY[-50:]}


@app.get("/info")
async def system_info(x_auth: str | None = Header(None)):
    """系统信息"""
    check_auth(x_auth)
    info = {
        "platform": sys.platform,
        "python": sys.version,
        "python_path": sys.executable,
        "cwd": str(CURRENT_DIR),
        "home": str(Path.home()),
        "uptime": int(time.time() - START_TIME),
    }

    # 检查常用工具是否安装
    tools = {}
    for tool in ["git", "python", "pip", "node", "npm", "cloudflared"]:
        result = await run_command(f"where {tool}" if sys.platform == "win32" else f"which {tool}")
        tools[tool] = result["stdout"].strip().split("\n")[0] if result["ok"] else None
    info["tools"] = tools

    return info


# =====================
# 启动
# =====================

def main():
    import socket

    print()
    print("=" * 70)
    print("  Remote Agent 启动")
    print("=" * 70)
    print()
    print(f"  端口: {PORT}")
    print(f"  当前目录: {CURRENT_DIR}")
    print(f"  日志: {LOG_FILE}")
    print()
    print("  ┌─────────────────────────────────────────────────────────────┐")
    print(f"  │  密码 (发给 Claude): {PASSWORD}".ljust(70) + "│")
    print("  └─────────────────────────────────────────────────────────────┘")
    print()
    print("  本地访问:")
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        print(f"    http://127.0.0.1:{PORT}")
        print(f"    http://{local_ip}:{PORT}  (局域网)")
    except Exception:
        print(f"    http://127.0.0.1:{PORT}")
    print()
    print("  下一步:")
    print("    1. 新开一个 CMD 窗口")
    print("    2. 运行: cloudflared tunnel --url http://localhost:9100")
    print("    3. 把 cloudflared 输出的 https URL + 上面的密码 都发给 Claude")
    print()
    print("  按 Ctrl+C 停止")
    print("=" * 70)
    print()

    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()

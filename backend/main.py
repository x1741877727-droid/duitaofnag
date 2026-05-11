"""
程序入口
- 开发模式: uvicorn 启动 FastAPI，浏览器访问
- 桌面模式: pywebview 窗口内嵌 Web UI
- 命令行参数: --dev 开发模式 / --port 端口
"""

import argparse
import faulthandler
import logging
import os
import socket
import sys
import threading

import uvicorn

# native crash 保护 — faulthandler 写到独立 crash.log (而不是 stderr).
# stderr 在后台启动 (cmd hidden) 时不可见; 写文件就能看到 native 崩溃栈.
# 同时: 每 60s 自动 dump 所有线程栈到 traceback_periodic.log, 即使 backend 卡住没崩
# 也能拿到 "卡住瞬间所有线程在干啥".
try:
    if getattr(sys, 'frozen', False):
        _log_dir = os.path.dirname(sys.executable)
    else:
        _log_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # crash.log: 行缓冲, 立刻 flush; mode='w' 每次启动覆盖
    _crash_fh = open(os.path.join(_log_dir, "crash.log"), "w", buffering=1, encoding="utf-8")
    faulthandler.enable(file=_crash_fh, all_threads=True)
    # 每 60s dump 一次所有线程栈到独立文件 (轮询心跳 + 卡住时能看)
    _trace_fh = open(os.path.join(_log_dir, "traceback_periodic.log"), "w", buffering=1, encoding="utf-8")
    faulthandler.dump_traceback_later(60, repeat=True, file=_trace_fh)
except Exception:
    pass

# 项目根目录（兼容 Nuitka/PyInstaller 打包后）
if getattr(sys, 'frozen', False):
    ROOT_DIR = os.path.dirname(sys.executable)  # exe 所在目录
else:
    ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

from backend.config import config
from backend.api import create_app

logger = logging.getLogger(__name__)


def find_free_port() -> int:
    """找一个空闲端口"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def setup_logging(debug: bool = False):
    level = logging.DEBUG if debug else logging.INFO
    fmt = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
    datefmt = "%H:%M:%S"
    formatter = logging.Formatter(fmt, datefmt=datefmt)

    root = logging.getLogger()
    root.setLevel(level)
    # 避免重复 add (主程序可能被 supervisor 多次 import)
    root.handlers.clear()

    # stdout handler (开发模式 console 看; 后台启动可能 None, 兜底跳过)
    if sys.stdout is not None:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(formatter)
        root.addHandler(sh)

    # 文件 handler — 后台启动 + cmd 重定向 buffer 失灵时的唯一可靠 log 源
    # mode="w": 每次启动覆盖, 避免累积膨胀 (motion gate 每帧 log 涨得快)
    # 历史 log 备份到 backend.log.prev
    log_path = os.path.join(ROOT_DIR, "backend.log")
    try:
        if os.path.isfile(log_path):
            try:
                os.replace(log_path, log_path + ".prev")
            except Exception:
                pass
        fh = logging.FileHandler(log_path, mode="w", encoding="utf-8")
        fh.setFormatter(formatter)
        root.addHandler(fh)
    except Exception as e:
        if sys.stderr is not None:
            print(f"[setup_logging] FileHandler 失败 ({log_path}): {e}", file=sys.stderr)

    # Python 未捕获异常 hook → 也写到 logger (faulthandler 是 native crash, 这是 Python 异常)
    def _except_hook(exc_type, exc_val, exc_tb):
        logging.getLogger("uncaught").critical(
            "Python 未捕获异常", exc_info=(exc_type, exc_val, exc_tb)
        )
    sys.excepthook = _except_hook

    # 降低第三方库日志级别
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def start_server(app, host: str, port: int):
    """在子线程中启动 uvicorn"""
    uvicorn.run(app, host=host, port=port, log_level="warning")


def run_dev_mode(port: int, host: str = ""):
    """开发模式：纯 HTTP 服务，浏览器访问"""
    bind_host = host or "0.0.0.0"

    config.load()
    app = create_app(config)
    _mount_frontend(app)

    logger.info(f"开发模式启动: http://{bind_host}:{port}")
    logger.info(f"API 文档: http://{bind_host}:{port}/docs")
    if bind_host == "0.0.0.0":
        local_ip = _get_local_ip()
        logger.info(f"局域网访问: http://{local_ip}:{port}")

    # log_config=None: 跳过 uvicorn 默认 dictConfig (会调 sys.stdout.isatty()),
    # 后台启动 (sys.stdout=None) 时 isatty() 报 AttributeError 导致整个进程退出.
    # 我们已经在 setup_logging() 里配过 root logger, uvicorn 的 access log 也会走过去.
    uvicorn.run(app, host=bind_host, port=port, log_level="info", log_config=None)


def run_desktop_mode(port: int, host: str = ""):
    """
    桌面模式：pywebview 窗口 + 后台 uvicorn
    """
    bind_host = host or "0.0.0.0"

    try:
        import webview
    except ImportError:
        logger.error("pywebview 未安装，请运行: pip install pywebview")
        logger.info("回退到开发模式")
        run_dev_mode(port, host)
        return

    config.load()
    app = create_app(config)
    _mount_frontend(app)

    url = f"http://127.0.0.1:{port}"
    if bind_host == "0.0.0.0":
        local_ip = _get_local_ip()
        logger.info(f"局域网访问: http://{local_ip}:{port}")

    # 子线程启动 uvicorn
    server_thread = threading.Thread(
        target=start_server,
        args=(app, bind_host, port),
        daemon=True,
    )
    server_thread.start()

    # 等待服务器启动
    import time
    for _ in range(30):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect(("127.0.0.1", port))
            s.close()
            break
        except ConnectionRefusedError:
            time.sleep(0.2)

    logger.info(f"桌面模式启动: {url}")

    # pywebview 窗口
    window = webview.create_window(
        title="FightMaster",
        url=url,
        width=1280,
        height=800,
        resizable=True,
        min_size=(900, 600),
    )
    webview.start()


def _mount_frontend(app):
    """挂载前端静态文件（如果已构建）"""
    # PyInstaller 把数据文件放在 _internal/ 里
    web_dist = os.path.join(ROOT_DIR, "web", "dist")
    if not os.path.isdir(web_dist):
        web_dist = os.path.join(ROOT_DIR, "_internal", "web", "dist")
    if os.path.isdir(web_dist):
        from fastapi.staticfiles import StaticFiles
        from fastapi.responses import FileResponse

        @app.get("/")
        async def serve_index():
            return FileResponse(os.path.join(web_dist, "index.html"))

        app.mount("/", StaticFiles(directory=web_dist), name="static")


def _get_local_ip() -> str:
    """获取本机局域网 IP"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def main():
    parser = argparse.ArgumentParser(description="游戏自动化控制台")
    parser.add_argument("--dev", action="store_true", help="开发模式（纯 HTTP，不启动桌面窗口）")
    parser.add_argument("--port", type=int, default=0, help="HTTP 端口（0=自动分配）")
    parser.add_argument("--host", type=str, default="0.0.0.0",
                        help="绑定地址 (默认 0.0.0.0=局域网可访问; 仅本机用 127.0.0.1)")
    parser.add_argument("--debug", action="store_true", help="调试日志")

    args = parser.parse_args()
    setup_logging(args.debug)

    # OCR pool worker 数自动按 CPU 物理核数算 (env 显式指定优先).
    # GPU OCR 实测 sweet spot:
    #   1-2 worker: 队列堆积
    #   3 worker (笔记本/台式 i5-i7): 队列适度 + GPU 利用充分 (默认)
    #   6 worker (E5 服务器 / Ryzen 多核): 队列消失, GPU 仍能撑
    #   太多 worker 会争同一 GPU, 反而单次慢
    # 公式: clamp(2, 6, physical_cores // 4)
    #   4 核: 2     8 核: 2     12 核: 3     14 核: 3     16 核: 4     28 核: 6
    if not os.environ.get("GAMEBOT_OCR_WORKERS"):
        try:
            import psutil
            phys = psutil.cpu_count(logical=False) or 4
        except Exception:
            phys = os.cpu_count() or 4
            # os.cpu_count() 是逻辑核, hybrid CPU 高估 → 砍半作物理核近似
            phys = max(2, phys // 2)
        workers = max(2, min(6, phys // 4))
        os.environ["GAMEBOT_OCR_WORKERS"] = str(workers)

    port = args.port or find_free_port()
    host = args.host  # 传给 run_dev_mode / run_desktop_mode

    if args.dev:
        run_dev_mode(port, host)
    else:
        run_desktop_mode(port, host)


if __name__ == "__main__":
    # PyInstaller + multiprocessing.spawn 必须：
    # 让 worker 子进程启动时识别自己是 worker 后退出 multiprocessing 路径，
    # 不重跑整个 FastAPI app。否则 OcrPool 子进程会递归启动 → pool 崩溃。
    import multiprocessing
    multiprocessing.freeze_support()
    main()

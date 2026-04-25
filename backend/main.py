"""
程序入口
- 开发模式: uvicorn 启动 FastAPI，浏览器访问
- 桌面模式: pywebview 窗口内嵌 Web UI
- 命令行参数: --dev 开发模式 / --mock mock模式 / --port 端口
"""

import argparse
import logging
import os
import socket
import sys
import threading

import uvicorn

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
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # 降低第三方库日志级别
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def start_server(app, host: str, port: int):
    """在子线程中启动 uvicorn"""
    uvicorn.run(app, host=host, port=port, log_level="warning")


def run_dev_mode(port: int, mock: bool, host: str = ""):
    """开发模式：纯 HTTP 服务，浏览器访问"""
    bind_host = host or "127.0.0.1"

    config.load()
    if mock:
        config.settings.dev_mock = True

    app = create_app(config)
    _mount_frontend(app)

    logger.info(f"开发模式启动: http://{bind_host}:{port}")
    logger.info(f"API 文档: http://{bind_host}:{port}/docs")
    logger.info(f"Mock 模式: {config.settings.dev_mock}")
    if bind_host == "0.0.0.0":
        local_ip = _get_local_ip()
        logger.info(f"局域网访问: http://{local_ip}:{port}")

    uvicorn.run(app, host=bind_host, port=port, log_level="info")


def run_desktop_mode(port: int, mock: bool, host: str = ""):
    """
    桌面模式：pywebview 窗口 + 后台 uvicorn
    """
    bind_host = host or "127.0.0.1"

    try:
        import webview
    except ImportError:
        logger.error("pywebview 未安装，请运行: pip install pywebview")
        logger.info("回退到开发模式")
        run_dev_mode(port, mock, host)
        return

    config.load()
    if mock:
        config.settings.dev_mock = True

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
    webview.start(debug=mock)  # mock 模式开启 devtools


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
    parser.add_argument("--mock", action="store_true", help="Mock 模式（macOS 开发用）")
    parser.add_argument("--port", type=int, default=0, help="HTTP 端口（0=自动分配）")
    parser.add_argument("--host", type=str, default="", help="绑定地址（0.0.0.0=局域网可访问，macOS远程调试用）")
    parser.add_argument("--debug", action="store_true", help="调试日志")

    args = parser.parse_args()
    setup_logging(args.debug)

    port = args.port or find_free_port()
    host = args.host  # 传给 run_dev_mode / run_desktop_mode

    if args.dev:
        run_dev_mode(port, args.mock, host)
    else:
        run_desktop_mode(port, args.mock, host)


if __name__ == "__main__":
    # PyInstaller + multiprocessing.spawn 必须：
    # 让 worker 子进程启动时识别自己是 worker 后退出 multiprocessing 路径，
    # 不重跑整个 FastAPI app。否则 OcrPool 子进程会递归启动 → pool 崩溃。
    import multiprocessing
    multiprocessing.freeze_support()
    main()

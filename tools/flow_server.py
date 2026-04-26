"""
本地小服务器 — 服务架构流程图 + 自动保存批注到磁盘

用法：
    python3 tools/flow_server.py
    浏览器开 http://localhost:8765

批注自动保存到 docs/annotations.json
Claude 直接 cat 读，不用复制粘贴。

依赖: 仅 Python 标准库
"""
import http.server
import json
import socketserver
from pathlib import Path
from urllib.parse import urlparse

PROJECT = Path(__file__).resolve().parent.parent
HTML_PATH = PROJECT / "docs" / "architecture_flow.html"
ANNOT_PATH = PROJECT / "docs" / "annotations.json"

PORT = 8765


class Handler(http.server.SimpleHTTPRequestHandler):
    def _send_json(self, code, body):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        # 主页 = 流程图 HTML
        if u.path in ("/", "/index.html"):
            if not HTML_PATH.is_file():
                self.send_error(404, "HTML missing")
                return
            content = HTML_PATH.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return
        # 读批注 (HTML 页面启动时调用，恢复本地批注)
        if u.path == "/api/annotations":
            if ANNOT_PATH.is_file():
                data = ANNOT_PATH.read_bytes()
            else:
                data = b"{}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_error(404)

    def do_POST(self):
        u = urlparse(self.path)
        if u.path == "/api/save":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                obj = json.loads(body.decode("utf-8"))
            except Exception as e:
                self._send_json(400, {"ok": False, "error": str(e)})
                return
            ANNOT_PATH.parent.mkdir(parents=True, exist_ok=True)
            ANNOT_PATH.write_text(
                json.dumps(obj, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self._send_json(200, {"ok": True, "count": len(obj)})
            return
        self.send_error(404)

    def log_message(self, fmt, *args):
        # 静音访问日志，只打印保存事件
        if "/api/save" in (args[0] if args else ""):
            print(f"[save] {self.address_string()} → {ANNOT_PATH.name}")


class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True   # 立刻重启不用等 TIME_WAIT


def main():
    # 自动找可用端口（默认 8765 占了就 8766, 8767 ...）
    import socket
    port = PORT
    while True:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("127.0.0.1", port))
                break
        except OSError:
            port += 1
            if port > PORT + 10:
                print(f"端口 {PORT}-{port} 全占了, 退出")
                return

    print(f"════════════════════════════════════════")
    print(f"  GameBot 架构流程图本地服务器")
    print(f"════════════════════════════════════════")
    print(f"  地址: http://localhost:{port}" + (
        f"  (端口被占, 自动用 {port})" if port != PORT else ""
    ))
    print(f"  批注: {ANNOT_PATH}")
    print(f"  HTML: {HTML_PATH}")
    print(f"════════════════════════════════════════")
    print(f"  Ctrl+C 退出")
    print()

    with ReusableTCPServer(("127.0.0.1", port), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n停止")


if __name__ == "__main__":
    main()

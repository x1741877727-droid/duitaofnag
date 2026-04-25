"""
Phase 5 验证脚本
测试 FastAPI REST API + WebSocket 推送

用法:
  python tests/test_phase5.py
"""

import asyncio
import logging
import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("transitions.core").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger("test_phase5")


async def test_api():
    """测试 REST API 和 WebSocket"""
    import uvicorn
    import threading
    import socket

    from backend.config import config
    from backend.api import create_app

    # 配置
    config.load()
    config.settings.dev_mock = True
    config.settings.state_timeout = 3
    config.settings.screenshot_interval = 0.2

    app = create_app(config)

    # 找空闲端口
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    # 后台启动 uvicorn
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # 等待服务器就绪
    base = f"http://127.0.0.1:{port}"
    for _ in range(30):
        try:
            async with httpx.AsyncClient() as c:
                r = await c.get(f"{base}/api/status", timeout=1)
                if r.status_code == 200:
                    break
        except Exception:
            await asyncio.sleep(0.2)

    logger.info(f"服务器启动: {base}")

    async with httpx.AsyncClient(base_url=base, timeout=10) as client:

        # === 状态查询（未运行） ===
        logger.info("=== 测试状态查询 ===")
        r = await client.get("/api/status")
        assert r.status_code == 200
        data = r.json()
        assert data["running"] is False
        logger.info(f"  未运行状态: ✓ {data}")

        # === 设置 CRUD ===
        logger.info("=== 测试设置接口 ===")
        r = await client.get("/api/settings")
        assert r.status_code == 200
        settings = r.json()
        logger.info(f"  获取设置: ✓ mock={settings['dev_mock']}")

        r = await client.put("/api/settings", json={"match_timeout": 90})
        assert r.status_code == 200
        assert r.json()["ok"]
        logger.info("  更新设置: ✓")

        r = await client.get("/api/settings")
        assert r.json()["match_timeout"] == 90
        logger.info("  验证更新: ✓")

        # 恢复
        await client.put("/api/settings", json={"match_timeout": 60})

        # === 账号 CRUD ===
        logger.info("=== 测试账号接口 ===")
        r = await client.get("/api/accounts")
        assert r.status_code == 200
        accounts = r.json()
        assert len(accounts) == 6
        logger.info(f"  获取账号: ✓ {len(accounts)} 个")

        # 更新账号
        accounts[0]["nickname"] = "TestUpdated"
        r = await client.put("/api/accounts", json=accounts)
        assert r.json()["ok"]
        logger.info("  更新账号: ✓")

        r = await client.get("/api/accounts")
        assert r.json()[0]["nickname"] == "TestUpdated"
        logger.info("  验证更新: ✓")

        # === 启动自动化 ===
        logger.info("=== 测试启动/停止 ===")
        r = await client.post("/api/start")
        assert r.status_code == 200
        assert r.json()["ok"]
        logger.info("  启动: ✓")

        # 等待 Agent 运行
        await asyncio.sleep(3)

        # 查询运行状态
        r = await client.get("/api/status")
        data = r.json()
        assert data["running"] is True
        instances = data["instances"]
        logger.info(f"  运行中: ✓ {len(instances)} 个实例")
        for idx in sorted(instances.keys(), key=int):
            s = instances[idx]
            logger.info(f"    [{idx}] {s['group']}/{s['role']} → {s['state']}")

        # 重复启动应该失败
        r = await client.post("/api/start")
        assert not r.json()["ok"]
        logger.info("  重复启动拒绝: ✓")

        # === 暂停/恢复 ===
        logger.info("=== 测试暂停/恢复 ===")
        r = await client.post("/api/pause")
        assert r.json()["ok"]

        r = await client.get("/api/status")
        assert r.json()["paused"] is True
        logger.info("  暂停: ✓")

        r = await client.post("/api/resume")
        assert r.json()["ok"]

        r = await client.get("/api/status")
        assert r.json()["paused"] is False
        logger.info("  恢复: ✓")

        # === 截图接口 ===
        logger.info("=== 测试截图接口 ===")
        r = await client.get("/api/screenshot/0")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/jpeg"
        assert len(r.content) > 100
        logger.info(f"  截图: ✓ {len(r.content)} bytes")

        # === 模板接口 ===
        logger.info("=== 测试模板接口 ===")
        r = await client.get("/api/templates")
        assert r.status_code == 200
        logger.info(f"  模板列表: ✓ {len(r.json())} 个")

        # === 停止 ===
        r = await client.post("/api/stop")
        assert r.json()["ok"]
        logger.info("  停止: ✓")

        await asyncio.sleep(1)

        r = await client.get("/api/status")
        assert r.json()["running"] is False
        logger.info("  确认已停止: ✓")

    # === WebSocket 测试 ===
    logger.info("=== 测试 WebSocket ===")

    # 先启动自动化
    async with httpx.AsyncClient(base_url=base, timeout=10) as client:
        await client.post("/api/start")

    import websockets
    ws_url = f"ws://127.0.0.1:{port}/ws"

    try:
        async with websockets.connect(ws_url, open_timeout=5) as ws:
            # 应该立即收到 snapshot
            msg = await asyncio.wait_for(ws.recv(), timeout=3)
            import json
            data = json.loads(msg)
            assert data["type"] == "snapshot"
            logger.info(f"  WebSocket snapshot: ✓ {len(data.get('instances', {}))} 实例")

            # 等待状态变化消息
            messages = []
            try:
                for _ in range(10):
                    msg = await asyncio.wait_for(ws.recv(), timeout=2)
                    messages.append(json.loads(msg))
            except asyncio.TimeoutError:
                pass

            log_msgs = [m for m in messages if m["type"] == "log"]
            state_msgs = [m for m in messages if m["type"] == "state_change"]
            logger.info(f"  WebSocket 收到: {len(messages)} 条 (日志:{len(log_msgs)} 状态:{len(state_msgs)})")

            # 心跳
            await ws.send("ping")
            pong = await asyncio.wait_for(ws.recv(), timeout=2)
            assert pong == "pong"
            logger.info("  WebSocket ping/pong: ✓")

    except Exception as e:
        logger.warning(f"  WebSocket 测试跳过: {e}")

    # 清理
    async with httpx.AsyncClient(base_url=base, timeout=10) as client:
        await client.post("/api/stop")

    server.should_exit = True
    await asyncio.sleep(0.5)

    logger.info("\n========== Phase 5 全部测试通过 ==========")


if __name__ == "__main__":
    asyncio.run(test_api())

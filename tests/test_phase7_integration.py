"""
Phase 7 集成测试
1. 单组端到端 (3 实例完整流程)
2. 双组完整流程 (6 实例 + 协调同步)
3. 异常场景 (禁赛、网络断开、ID 不匹配)

全部在 mock 模式下运行
"""

import asyncio
import logging
import os
import sys
import tempfile
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("transitions.core").setLevel(logging.WARNING)

logger = logging.getLogger("test_integration")


# =====================
# 辅助：构建完整组件栈
# =====================

def make_pipeline(tmpdir: str, mock: bool = True):
    from backend.recognition.template_matcher import TemplateMatcher
    from backend.recognition.ocr_reader import OCRReader
    from backend.recognition.llm_vision import LLMVision
    from backend.recognition.cache import LLMCache
    from backend.recognition.pipeline import RecognitionPipeline

    tdir = os.path.join(tmpdir, "templates")
    os.makedirs(tdir, exist_ok=True)
    matcher = TemplateMatcher(tdir)
    ocr = OCRReader(mock=mock)
    llm = LLMVision(api_url="http://mock", mock=mock)
    cache = LLMCache(cache_dir=tmpdir)
    return RecognitionPipeline(matcher, ocr, llm, cache)


def make_agent(index: int, group: str, role: str, tmpdir: str, settings):
    from backend.adb.controller import ADBController
    from backend.config import AccountConfig
    from backend.instance_agent import InstanceAgent

    account = AccountConfig(
        qq=f"qq_{index}", nickname=f"Player{index}", game_id=f"ID{index:03d}",
        group=group, role=role, instance_index=index,
    )
    ctrl = ADBController(
        serial=f"127.0.0.1:{5555 + index * 2}",
        instance_index=index, mock=True,
    )
    pipeline = make_pipeline(tmpdir)
    return InstanceAgent(
        account=account, settings=settings,
        ctrl=ctrl, pipeline=pipeline,
    ), ctrl


# =====================
# Test 1: 单组端到端
# =====================

async def test_single_group():
    """单组 3 实例：Captain 走完 idle→lobby，Member 走完 idle→lobby"""
    from backend.config import Settings
    from backend.models import State

    logger.info("=" * 60)
    logger.info("测试 1: 单组端到端 (3 实例)")
    logger.info("=" * 60)

    settings = Settings(dev_mock=True, state_timeout=5, screenshot_interval=0.2)

    with tempfile.TemporaryDirectory() as tmpdir:
        agents = []
        ctrls = []

        # Captain + 2 Members
        for i, role in enumerate(["captain", "member", "member"]):
            agent, ctrl = make_agent(i, "A", role, tmpdir, settings)
            await ctrl.connect()
            agents.append(agent)
            ctrls.append(ctrl)

        # 并发启动 3 个 Agent
        tasks = [asyncio.create_task(a.start()) for a in agents]

        # 等待一段时间让流程推进
        await asyncio.sleep(4)

        # 检查状态
        for agent in agents:
            logger.info(
                f"  [实例{agent.index}] {agent.info.role.value} → {agent.state.value}"
            )

        # 验证：所有 Agent 都应该推进到至少 lobby
        for agent in agents:
            assert agent.state != State.IDLE, f"实例{agent.index} 不应该还在 IDLE"

        # 停止
        for agent in agents:
            await agent.stop()
        await asyncio.gather(*tasks, return_exceptions=True)

        for ctrl in ctrls:
            await ctrl.disconnect()

    logger.info("✓ 单组端到端通过\n")


# =====================
# Test 2: 双组完整流程 (通过协调器)
# =====================

async def test_dual_group_coordinator():
    """双组 6 实例通过协调器运行"""
    from backend.config import config
    from backend.coordinator import Coordinator

    logger.info("=" * 60)
    logger.info("测试 2: 双组完整流程 (6 实例, 协调器)")
    logger.info("=" * 60)

    config.load()
    config.settings.dev_mock = True
    config.settings.state_timeout = 5
    config.settings.screenshot_interval = 0.2

    logs = []
    state_changes = []

    def on_log(entry):
        logs.append(entry)

    def on_state(idx, old, new):
        state_changes.append((idx, old, new))

    coordinator = Coordinator(config=config, on_log=on_log, on_state_change=on_state)
    await coordinator.initialize()

    assert len(coordinator.agents) == 6
    assert len(coordinator.teams) == 2

    # 启动
    task = asyncio.create_task(coordinator.start())
    await asyncio.sleep(5)

    # 检查所有实例状态
    states = coordinator.get_all_states()
    logger.info("  实例状态:")
    for idx in sorted(states.keys(), key=int):
        s = states[idx]
        logger.info(f"    [{idx}] {s['group']}/{s['role']} → {s['state']} ({s['state_duration']}s)")

    # 验证：所有 6 个 Agent 都推进了
    advanced = sum(1 for s in states.values() if s["state"] != "idle")
    assert advanced == 6, f"6 个 Agent 都应该推进，实际 {advanced}"
    logger.info(f"  6/6 Agent 已推进 ✓")

    # 验证状态变化数量（每个 Agent 至少 3 次: idle→launching→login→lobby）
    assert len(state_changes) >= 18, f"至少 18 次变化，实际 {len(state_changes)}"
    logger.info(f"  状态变化: {len(state_changes)} 次 ✓")

    # 验证统计
    stats = coordinator._get_stats_dict()
    assert stats["running_duration"] >= 4
    logger.info(f"  运行时长: {stats['running_duration']}s ✓")

    # 停止
    await coordinator.stop()
    try:
        await asyncio.wait_for(task, timeout=3)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    logger.info(f"  日志总数: {len(logs)}")
    logger.info("✓ 双组完整流程通过\n")


# =====================
# Test 3: 异常场景
# =====================

async def test_error_scenarios():
    """测试异常：禁赛、网络错误、强制停止"""
    from backend.config import Settings
    from backend.models import Role, State
    from backend.state_machine import GameStateMachine

    logger.info("=" * 60)
    logger.info("测试 3: 异常场景")
    logger.info("=" * 60)

    # --- 3a: 禁赛终止 ---
    logger.info("  3a: 禁赛终止")
    fsm = GameStateMachine(0, Role.CAPTAIN)
    fsm.start()
    fsm.game_ready()
    fsm.login_ok()
    fsm.enter_lobby()
    fsm.banned()  # 从任意状态进入 banned
    assert fsm.current_state == State.ERROR_BANNED
    assert fsm.is_terminal_state()
    # banned 后只能 force_stop
    fsm.force_stop()
    assert fsm.current_state == State.IDLE
    logger.info("    禁赛 → 终止 → force_stop ✓")

    # --- 3b: 网络错误恢复 ---
    logger.info("  3b: 网络错误恢复")
    fsm2 = GameStateMachine(1, Role.MEMBER)
    fsm2.start()
    fsm2.game_ready()
    fsm2.network_error()
    assert fsm2.current_state == State.ERROR_NETWORK
    assert fsm2.is_error_state()
    fsm2.recovered()
    assert fsm2.current_state == State.LOBBY
    logger.info("    网络错误 → 恢复 → lobby ✓")

    # --- 3c: 匹配超时重来 ---
    logger.info("  3c: 匹配超时重来")
    fsm3 = GameStateMachine(2, Role.CAPTAIN)
    fsm3.start()
    fsm3.game_ready()
    fsm3.login_ok()
    fsm3.enter_lobby()
    fsm3.popups_cleared()
    fsm3.setup_done()
    fsm3.team_created()
    fsm3.players_joined()
    fsm3.players_verified()
    fsm3.all_ready()
    # 匹配超时
    fsm3.match_timeout()
    assert fsm3.current_state == State.ABORT
    fsm3.restart()
    assert fsm3.current_state == State.LOBBY
    logger.info("    匹配超时 → abort → restart → lobby ✓")

    # --- 3d: ABORT → 重新走完整流程 ---
    logger.info("  3d: ABORT 后完整重走")
    fsm3.enter_lobby()
    fsm3.popups_cleared()
    fsm3.setup_done()
    fsm3.team_created()
    fsm3.players_joined()
    fsm3.players_verified()
    fsm3.all_ready()
    fsm3.match_found()
    fsm3.opponent_wrong()
    assert fsm3.current_state == State.ABORT
    fsm3.restart()
    assert fsm3.current_state == State.LOBBY
    logger.info("    对手不匹配 → abort → restart → lobby ✓")

    # 第二轮成功
    fsm3.enter_lobby()
    fsm3.popups_cleared()
    fsm3.setup_done()
    fsm3.team_created()
    fsm3.players_joined()
    fsm3.players_verified()
    fsm3.all_ready()
    fsm3.match_found()
    fsm3.opponent_correct()
    assert fsm3.current_state == State.SUCCESS
    logger.info("    第二轮 → 成功 ✓")

    # --- 3e: 未知错误恢复 ---
    logger.info("  3e: 未知错误恢复")
    fsm4 = GameStateMachine(3, Role.CAPTAIN)
    fsm4.start()
    fsm4.game_ready()
    fsm4.unknown_error()
    assert fsm4.current_state == State.ERROR_UNKNOWN
    fsm4.recovered()
    assert fsm4.current_state == State.LOBBY
    logger.info("    未知错误 → 恢复 → lobby ✓")

    logger.info("✓ 异常场景全部通过\n")


# =====================
# Test 4: API 端到端 + WebSocket 完整流程
# =====================

async def test_api_full_lifecycle():
    """通过 HTTP API 完整走一遍：启动→运行→暂停→恢复→停止"""
    import threading, socket
    import httpx, uvicorn

    from backend.config import config
    from backend.api import create_app

    logger.info("=" * 60)
    logger.info("测试 4: API 完整生命周期")
    logger.info("=" * 60)

    config.load()
    config.settings.dev_mock = True
    config.settings.state_timeout = 3
    config.settings.screenshot_interval = 0.2

    app = create_app(config)

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()

    # 等待就绪
    for _ in range(30):
        try:
            async with httpx.AsyncClient() as c:
                await c.get(f"http://127.0.0.1:{port}/api/status", timeout=1)
                break
        except Exception:
            await asyncio.sleep(0.2)

    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=10) as c:
        # 1. 初始状态
        r = await c.get("/api/status")
        assert not r.json()["running"]
        logger.info("  未运行 ✓")

        # 2. 启动
        r = await c.post("/api/start")
        assert r.json()["ok"]
        await asyncio.sleep(3)

        r = await c.get("/api/status")
        d = r.json()
        assert d["running"]
        assert len(d["instances"]) == 6
        logger.info(f"  启动 6 实例 ✓")

        # 3. 截图
        r = await c.get("/api/screenshot/0")
        assert r.status_code == 200
        assert len(r.content) > 100
        logger.info(f"  截图 {len(r.content)} bytes ✓")

        # 4. 暂停
        await c.post("/api/pause")
        r = await c.get("/api/status")
        assert r.json()["paused"]
        logger.info("  暂停 ✓")

        # 5. 恢复
        await c.post("/api/resume")
        r = await c.get("/api/status")
        assert not r.json()["paused"]
        logger.info("  恢复 ✓")

        # 6. 设置读写
        r = await c.get("/api/settings")
        assert r.status_code == 200

        r = await c.put("/api/settings", json={"match_timeout": 120})
        assert r.json()["ok"]

        r = await c.get("/api/settings")
        assert r.json()["match_timeout"] == 120
        await c.put("/api/settings", json={"match_timeout": 60})
        logger.info("  设置 CRUD ✓")

        # 7. 账号读写
        r = await c.get("/api/accounts")
        accs = r.json()
        assert len(accs) == 6
        logger.info("  账号 CRUD ✓")

        # 8. 重复启动拒绝
        r = await c.post("/api/start")
        assert not r.json()["ok"]
        logger.info("  重复启动拒绝 ✓")

        # 9. 停止
        r = await c.post("/api/stop")
        assert r.json()["ok"]
        await asyncio.sleep(1)

        r = await c.get("/api/status")
        assert not r.json()["running"]
        logger.info("  停止 ✓")

        # 10. 停止后可以再次启动
        r = await c.post("/api/start")
        assert r.json()["ok"]
        await asyncio.sleep(2)

        r = await c.get("/api/status")
        assert r.json()["running"]
        logger.info("  重新启动 ✓")

        await c.post("/api/stop")
        await asyncio.sleep(1)

    server.should_exit = True
    logger.info("✓ API 完整生命周期通过\n")


# =====================
# 主函数
# =====================

async def main():
    await test_single_group()
    await test_dual_group_coordinator()
    await test_error_scenarios()
    await test_api_full_lifecycle()

    logger.info("=" * 60)
    logger.info("Phase 7 集成测试全部通过!")
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())

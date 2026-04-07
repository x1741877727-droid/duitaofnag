"""
Phase 3 验证脚本
测试状态机转换、Handler 调度、Instance Agent 流程

用法:
  python test_phase3.py
"""

import asyncio
import logging
import os
import tempfile

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("test_phase3")


async def test_state_machine():
    """测试状态机转换"""
    from backend.models import Role, State
    from backend.state_machine import GameStateMachine

    logger.info("=== 测试状态机 (Captain) ===")

    state_changes = []

    def on_change(old, new):
        state_changes.append((old, new))

    fsm = GameStateMachine(instance_index=0, role=Role.CAPTAIN, on_state_change=on_change)

    # 初始状态
    assert fsm.current_state == State.IDLE
    logger.info(f"  初始状态: {fsm.current_state.value}")

    # 正常流程
    fsm.start()
    assert fsm.current_state == State.LAUNCHING
    logger.info(f"  start → {fsm.current_state.value}")

    fsm.game_ready()
    assert fsm.current_state == State.LOGIN_CHECK
    logger.info(f"  game_ready → {fsm.current_state.value}")

    fsm.login_ok()
    assert fsm.current_state == State.LOBBY

    fsm.enter_lobby()
    assert fsm.current_state == State.DISMISS_POPUPS

    fsm.popups_cleared()
    assert fsm.current_state == State.SETUP  # Captain 特有
    logger.info(f"  popups_cleared → {fsm.current_state.value} (Captain)")

    fsm.setup_done()
    assert fsm.current_state == State.TEAM_CREATE

    fsm.team_created()
    assert fsm.current_state == State.WAIT_PLAYERS

    fsm.players_joined()
    assert fsm.current_state == State.VERIFY_PLAYERS

    fsm.players_verified()
    assert fsm.current_state == State.READY_CHECK

    fsm.all_ready()
    assert fsm.current_state == State.MATCHING

    fsm.match_found()
    assert fsm.current_state == State.VERIFY_OPPONENT

    # 测试成功路径
    fsm.opponent_correct()
    assert fsm.current_state == State.SUCCESS
    logger.info(f"  opponent_correct → {fsm.current_state.value}")

    logger.info(f"  总共 {len(state_changes)} 次状态变化")
    logger.info("✓ Captain 正常流程全部通过\n")

    # --- Member 流程 ---
    logger.info("=== 测试状态机 (Member) ===")
    fsm_m = GameStateMachine(instance_index=1, role=Role.MEMBER)

    fsm_m.start()
    fsm_m.game_ready()
    fsm_m.login_ok()
    fsm_m.enter_lobby()
    fsm_m.popups_cleared()
    assert fsm_m.current_state == State.TEAM_JOIN  # Member 特有
    logger.info(f"  popups_cleared → {fsm_m.current_state.value} (Member)")

    fsm_m.team_joined()
    assert fsm_m.current_state == State.WAIT_PLAYERS

    logger.info("✓ Member 流程通过\n")

    # --- 错误和恢复 ---
    logger.info("=== 测试错误处理 ===")
    fsm_e = GameStateMachine(instance_index=2, role=Role.CAPTAIN)
    fsm_e.start()
    fsm_e.game_ready()

    # 网络错误
    fsm_e.network_error()
    assert fsm_e.current_state == State.ERROR_NETWORK
    assert fsm_e.is_error_state()
    logger.info(f"  network_error → {fsm_e.current_state.value}")

    # 恢复
    fsm_e.recovered()
    assert fsm_e.current_state == State.LOBBY
    logger.info(f"  recovered → {fsm_e.current_state.value}")

    # 禁赛
    fsm_e.enter_lobby()
    fsm_e.banned()
    assert fsm_e.current_state == State.ERROR_BANNED
    assert fsm_e.is_terminal_state()
    logger.info(f"  banned → {fsm_e.current_state.value} (terminal)")

    # 强制停止
    fsm_e.force_stop()
    assert fsm_e.current_state == State.IDLE
    logger.info(f"  force_stop → {fsm_e.current_state.value}")

    # ABORT → 重来
    fsm_a = GameStateMachine(instance_index=3, role=Role.CAPTAIN)
    fsm_a.start()
    fsm_a.game_ready()
    fsm_a.login_ok()
    fsm_a.enter_lobby()
    fsm_a.popups_cleared()
    fsm_a.setup_done()
    fsm_a.team_created()
    fsm_a.players_joined()
    fsm_a.players_verified()
    fsm_a.all_ready()
    fsm_a.match_found()
    fsm_a.opponent_wrong()
    assert fsm_a.current_state == State.ABORT

    fsm_a.restart()
    assert fsm_a.current_state == State.LOBBY
    logger.info(f"  abort → restart → {fsm_a.current_state.value}")

    logger.info("✓ 错误处理全部通过\n")

    # --- 触发器检查 ---
    logger.info("=== 测试触发器 ===")
    fsm_t = GameStateMachine(instance_index=4, role=Role.CAPTAIN)
    triggers = fsm_t.get_available_triggers()
    logger.info(f"  IDLE 可用触发: {triggers}")
    assert "start" in triggers
    assert fsm_t.can_trigger("start")
    assert not fsm_t.can_trigger("login_ok")  # 不在 LOGIN_CHECK 状态

    logger.info("✓ 触发器检查通过\n")


async def test_instance_agent():
    """测试 Instance Agent（mock 模式，快速走过前几个状态）"""
    from backend.adb.controller import ADBController
    from backend.config import AccountConfig, Settings
    from backend.instance_agent import InstanceAgent
    from backend.models import LogEntry, State
    from backend.recognition.cache import LLMCache
    from backend.recognition.llm_vision import LLMVision
    from backend.recognition.ocr_reader import OCRReader
    from backend.recognition.pipeline import RecognitionPipeline
    from backend.recognition.template_matcher import TemplateMatcher

    logger.info("=== 测试 Instance Agent ===")

    with tempfile.TemporaryDirectory() as tmpdir:
        # 配置
        account = AccountConfig(
            qq="test_qq", nickname="TestPlayer", game_id="ID001",
            group="A", role="captain", instance_index=0,
        )
        settings = Settings(dev_mock=True, state_timeout=5, screenshot_interval=0.2)

        # ADB 控制器（mock）
        ctrl = ADBController(serial="127.0.0.1:5555", instance_index=0, mock=True)
        await ctrl.connect()

        # 识别管道（全 mock）
        matcher = TemplateMatcher(os.path.join(tmpdir, "templates"))
        ocr = OCRReader(mock=True)
        llm = LLMVision(api_url="http://mock", mock=True)
        cache = LLMCache(cache_dir=tmpdir)
        pipeline = RecognitionPipeline(matcher, ocr, llm, cache)

        # 日志收集
        logs: list[LogEntry] = []
        state_changes: list[tuple] = []

        def on_log(entry: LogEntry):
            logs.append(entry)

        def on_state(idx, old, new):
            state_changes.append((idx, old, new))

        # 创建 Agent
        agent = InstanceAgent(
            account=account, settings=settings,
            ctrl=ctrl, pipeline=pipeline,
            on_log=on_log, on_state_change=on_state,
        )

        assert agent.state == State.IDLE
        logger.info(f"  Agent 创建: state={agent.state.value} role={agent.info.role.value}")

        # 运行 Agent（带超时，因为 mock 模式 handler 会快速超时）
        task = asyncio.create_task(agent.start())

        # 给 Agent 一些时间运行
        await asyncio.sleep(3)

        # 停止 Agent
        await agent.stop()

        # 等待 task 完成
        try:
            await asyncio.wait_for(task, timeout=2)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        logger.info(f"  状态变化次数: {len(state_changes)}")
        for idx, old, new in state_changes:
            logger.info(f"    [{idx}] {old} → {new}")

        logger.info(f"  日志条数: {len(logs)}")

        # 验证至少走过了一些状态
        assert len(state_changes) >= 1, "应该至少有 1 次状态变化"
        assert state_changes[0] == (0, "idle", "launching"), "第一次变化应该是 idle → launching"

        logger.info("✓ Instance Agent 测试通过\n")


async def test_models():
    """测试数据模型"""
    from backend.models import (
        CoordinatorCommand, Group, InstanceInfo, LogEntry,
        MatchAttempt, Role, SessionStats, State,
    )

    logger.info("=== 测试数据模型 ===")

    # InstanceInfo
    info = InstanceInfo(index=0, group=Group.A, role=Role.CAPTAIN, state=State.LOBBY)
    assert info.group == Group.A
    assert info.role == Role.CAPTAIN

    # SessionStats
    stats = SessionStats(start_time=1000)
    attempt = MatchAttempt(attempt_number=1, timestamp=1001, opponent_is_target=False, abort_reason="不匹配")
    stats.record_attempt(attempt)
    assert stats.total_attempts == 1
    assert stats.abort_count == 1

    attempt2 = MatchAttempt(attempt_number=2, timestamp=1060, opponent_is_target=True)
    stats.record_attempt(attempt2)
    assert stats.success_count == 1

    # LogEntry
    entry = LogEntry(timestamp=1000, instance_index=0, level="info", message="test", state="lobby")
    d = entry.to_dict()
    assert d["instance"] == 0
    assert d["message"] == "test"

    # CoordinatorCommand
    cmd = CoordinatorCommand(action="match_now", target_indices=[0, 3])
    assert cmd.action == "match_now"

    logger.info("✓ 数据模型测试通过\n")


async def main():
    await test_state_machine()
    await test_models()
    await test_instance_agent()
    logger.info("========== Phase 3 全部测试通过 ==========")


if __name__ == "__main__":
    asyncio.run(main())

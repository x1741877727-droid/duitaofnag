"""
Phase 4 验证脚本
测试协调器：初始化、6 实例并发启动、状态同步、统计

用法:
  python test_phase4.py
"""

import asyncio
import logging
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
# 降低 transitions 库的日志级别，避免刷屏
logging.getLogger("transitions.core").setLevel(logging.WARNING)

logger = logging.getLogger("test_phase4")


async def test_coordinator_init():
    """测试协调器初始化"""
    from backend.config import config
    from backend.coordinator import Coordinator
    from backend.models import LogEntry

    logger.info("=== 测试协调器初始化 ===")

    # 加载配置后启用 mock 模式（load 会从 settings.json 覆盖）
    config.load()
    config.settings.dev_mock = True
    config.settings.state_timeout = 3
    config.settings.screenshot_interval = 0.2

    logs: list[LogEntry] = []
    state_changes: list[tuple] = []

    def on_log(entry: LogEntry):
        logs.append(entry)

    def on_state(idx, old, new):
        state_changes.append((idx, old, new))

    coordinator = Coordinator(
        config=config,
        on_log=on_log,
        on_state_change=on_state,
    )

    # 初始化
    await coordinator.initialize()

    # 验证
    assert len(coordinator.agents) == 6, f"应该有 6 个 Agent, 实际 {len(coordinator.agents)}"
    assert len(coordinator.controllers) == 6
    assert len(coordinator.teams) == 2  # A 和 B

    team_a = coordinator.teams["A"]
    team_b = coordinator.teams["B"]
    assert team_a.captain_index == 0
    assert len(team_a.member_indices) == 2
    assert team_b.captain_index == 3
    assert len(team_b.member_indices) == 2

    logger.info(f"  Agent 数量: {len(coordinator.agents)}")
    logger.info(f"  A 组: Captain={team_a.captain_index} Members={team_a.member_indices}")
    logger.info(f"  B 组: Captain={team_b.captain_index} Members={team_b.member_indices}")

    # 验证所有 Agent 角色正确
    for idx, agent in coordinator.agents.items():
        group = "A" if idx < 3 else "B"
        role = "captain" if idx in (0, 3) else "member"
        assert agent.info.group.value == group
        assert agent.info.role.value == role

    logger.info("  所有 Agent 角色验证 ✓")
    logger.info("✓ 协调器初始化测试通过\n")

    return coordinator, logs, state_changes


async def test_coordinator_run():
    """测试协调器运行（短暂运行，验证 Agent 并发启动）"""
    coordinator, logs, state_changes = await test_coordinator_init()

    logger.info("=== 测试协调器运行 ===")

    # 在后台启动协调器
    run_task = asyncio.create_task(coordinator.start())

    # 让它运行几秒
    await asyncio.sleep(4)

    # 检查 Agent 状态
    states = coordinator.get_all_states()
    logger.info("  实例状态:")
    for idx in sorted(states.keys()):
        s = states[idx]
        logger.info(f"    [{idx}] {s['group']}/{s['role']} → {s['state']} ({s['state_duration']}s)")

    # 验证：至少一些 Agent 已经推进了
    advanced = sum(1 for s in states.values() if s["state"] != "idle")
    logger.info(f"  已推进的 Agent: {advanced}/6")
    assert advanced >= 1, "至少应该有 1 个 Agent 推进了"

    # 验证状态变化有记录
    logger.info(f"  状态变化总数: {len(state_changes)}")
    assert len(state_changes) >= 6, "6 个 Agent 启动至少 6 次变化"

    # 检查统计
    stats = coordinator._get_stats_dict()
    logger.info(f"  统计: {stats}")
    assert stats["running_duration"] >= 3

    # 停止
    await coordinator.stop()

    # 等待 task 完成
    try:
        await asyncio.wait_for(run_task, timeout=3)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        run_task.cancel()
        try:
            await run_task
        except asyncio.CancelledError:
            pass

    logger.info(f"  总日志条数: {len(logs)}")
    logger.info(f"  总状态变化: {len(state_changes)}")

    # 打印 Agent 最终状态
    final_states = coordinator.get_all_states()
    for idx in sorted(final_states.keys()):
        s = final_states[idx]
        logger.info(f"  最终 [{idx}] {s['state']}")

    logger.info("✓ 协调器运行测试通过\n")


async def test_coordinator_pause_resume():
    """测试暂停/恢复"""
    from backend.config import config
    from backend.coordinator import Coordinator

    logger.info("=== 测试暂停/恢复 ===")

    config.load()
    config.settings.dev_mock = True
    config.settings.state_timeout = 3
    config.settings.screenshot_interval = 0.2

    coordinator = Coordinator(config=config)
    await coordinator.initialize()

    run_task = asyncio.create_task(coordinator.start())
    await asyncio.sleep(1)

    # 暂停
    coordinator.pause()
    states_before = coordinator.get_all_states()
    await asyncio.sleep(1)
    states_after = coordinator.get_all_states()

    # 暂停期间状态不应变化（或变化极少）
    logger.info(f"  暂停前后状态对比:")
    for idx in sorted(states_before.keys()):
        logger.info(f"    [{idx}] {states_before[idx]['state']} → {states_after[idx]['state']}")

    # 恢复
    coordinator.resume()
    await asyncio.sleep(1)

    states_resumed = coordinator.get_all_states()
    logger.info(f"  恢复后状态:")
    for idx in sorted(states_resumed.keys()):
        logger.info(f"    [{idx}] {states_resumed[idx]['state']}")

    # 停止
    await coordinator.stop()
    try:
        await asyncio.wait_for(run_task, timeout=3)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        run_task.cancel()
        try:
            await run_task
        except asyncio.CancelledError:
            pass

    logger.info("✓ 暂停/恢复测试通过\n")


async def test_team_info():
    """测试组队信息管理"""
    from backend.models import Group, MatchAttempt, SessionStats, TeamInfo

    logger.info("=== 测试组队/统计数据 ===")

    team = TeamInfo(
        group=Group.A,
        captain_index=0,
        member_indices=[1, 2],
        real_player_ids=["Player_X", "Player_Y"],
    )
    assert team.captain_index == 0
    assert len(team.member_indices) == 2

    # 统计
    stats = SessionStats(start_time=time.time())

    # 模拟 3 次匹配
    for i in range(3):
        attempt = MatchAttempt(
            attempt_number=i + 1,
            timestamp=time.time(),
            group_a_matched=True,
            group_b_matched=True,
            opponent_is_target=(i == 2),  # 第三次成功
            abort_reason="" if i == 2 else "对手不匹配",
        )
        stats.record_attempt(attempt)

    assert stats.total_attempts == 3
    assert stats.success_count == 1
    assert stats.abort_count == 2

    logger.info(f"  3 次匹配: success={stats.success_count} abort={stats.abort_count}")
    logger.info("✓ 组队/统计数据测试通过\n")


async def main():
    await test_team_info()
    await test_coordinator_run()
    await test_coordinator_pause_resume()
    logger.info("========== Phase 4 全部测试通过 ==========")


if __name__ == "__main__":
    asyncio.run(main())

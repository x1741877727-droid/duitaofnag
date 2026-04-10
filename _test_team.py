"""直接测试阶段4: 从大厅开始 → 组队 → 获取口令码"""
import asyncio
import logging

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

from backend.automation.adb_lite import ADBController
from backend.automation.screen_matcher import ScreenMatcher
from backend.automation.single_runner import SingleInstanceRunner


async def main():
    adb = ADBController("emulator-5558", r"D:\leidian\LDPlayer9\adb.exe")
    matcher = ScreenMatcher("fixtures/templates")
    n = matcher.load_all()
    print(f"loaded {n} templates: {matcher.template_names}")

    runner = SingleInstanceRunner(adb=adb, matcher=matcher, role="captain")

    # 先截图确认当前在大厅
    shot = await adb.screenshot()
    if shot is not None:
        print(f"screenshot size: {shot.shape}")
        # 检查是否在大厅
        lobby = matcher.match_one(shot, "lobby_start_btn", threshold=0.6)
        print(f"lobby_start_btn match: {lobby}")
    else:
        print("ERROR: screenshot failed!")
        return

    print("\n=== 开始阶段4: 队长创建队伍 ===")
    code = await runner.phase_team_create()
    print(f"\n=== 结果: {code} ===")


asyncio.run(main())

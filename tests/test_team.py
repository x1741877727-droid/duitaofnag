"""
测试脚本：队长创建队伍(QR码) + 队员直接加入(game scheme)
雷电模拟器-1 (emulator-5556) = 队长
雷电模拟器-2 (emulator-5560) = 队员

用法: python tests/test_team.py --adb D:\\leidian\\LDPlayer9\\adb.exe
"""

import asyncio
import logging
import os
import sys
import argparse

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend.automation.adb_lite import ADBController
from backend.automation.screen_matcher import ScreenMatcher
from backend.automation.single_runner import SingleInstanceRunner
from backend.automation.ocr_dismisser import OcrDismisser

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--adb", default=r"D:\leidian\LDPlayer9\adb.exe")
    parser.add_argument("--captain", default="emulator-5556", help="队长 ADB serial")
    parser.add_argument("--member", default="emulator-5560", help="队员 ADB serial")
    parser.add_argument("--templates", default="", help="模板目录")
    args = parser.parse_args()

    # 初始化 ADB
    captain_adb = ADBController(args.captain, args.adb)
    member_adb = ADBController(args.member, args.adb)

    # 加载模板
    template_dir = args.templates
    if not template_dir:
        root = PROJECT_ROOT
        for d in [
            os.path.join(root, "fixtures", "templates"),
            os.path.join(root, "backend", "recognition", "templates"),
        ]:
            if os.path.isdir(d):
                template_dir = d
                break
    matcher = ScreenMatcher(template_dir)
    n = matcher.load_all()
    logger.info(f"已加载 {n} 个模板")

    # 预热 OCR
    OcrDismisser.warmup()

    # 创建 runner
    captain_runner = SingleInstanceRunner(
        adb=captain_adb, matcher=matcher, role="captain", debug=True
    )
    member_runner = SingleInstanceRunner(
        adb=member_adb, matcher=matcher, role="member", debug=True
    )

    # ── 步骤1: 队长创建队伍（QR码方式） ──
    logger.info("=" * 50)
    logger.info(f"步骤1: 队长创建队伍 ({args.captain})")
    game_scheme = await captain_runner.phase_team_create()
    if not game_scheme:
        logger.error("队长创建队伍失败！")
        return

    logger.info(f"队长创建完成，game scheme: {game_scheme}")

    # ── 步骤2: 队员直接加入（一条ADB命令） ──
    logger.info("=" * 50)
    logger.info(f"步骤2: 队员加入队伍 ({args.member})")
    ok = await member_runner.phase_team_join(game_scheme)
    if ok:
        logger.info("队员加入队伍成功！")
    else:
        logger.error("队员加入队伍失败！")

    logger.info("=" * 50)
    logger.info("测试完成")


if __name__ == "__main__":
    asyncio.run(main())

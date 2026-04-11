"""
测试脚本：队长创建队伍 + 队员加入
雷电模拟器-1 (emulator-5556) = 队长
雷电模拟器-2 (emulator-5558) = 队员

用法: python test_team.py --adb D:\leidian\LDPlayer9\adb.exe
"""

import asyncio
import logging
import os
import sys
import argparse

# 把 backend 加入路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend.automation.adb_lite import ADBController
from backend.automation.screen_matcher import ScreenMatcher
from backend.automation.single_runner import SingleInstanceRunner
from backend.automation.ocr_dismisser import OcrDismisser

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


async def read_clipboard(adb: ADBController) -> str:
    """读取模拟器剪贴板内容"""
    loop = asyncio.get_event_loop()
    # 方式1: clipper app
    output = await loop.run_in_executor(
        None, adb._cmd, "shell", "am broadcast -a clipper.get 2>&1"
    )
    # 解析 clipper 输出：data="xxx"
    if "data=" in output:
        start = output.index("data=") + 6
        end = output.index('"', start)
        return output[start:end]

    # 方式2: service call (Android 通用)
    output = await loop.run_in_executor(
        None, adb._cmd, "shell",
        "service call clipboard 2 i32 1 i32 0 2>&1"
    )
    # 解析 service call 输出
    if "String16" in output:
        start = output.index('"') + 1
        end = output.index('"', start)
        return end > start and output[start:end] or ""

    return ""


async def clear_clipboard(adb: ADBController):
    """清空剪贴板"""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None, adb._cmd, "shell",
        "am broadcast -a clipper.set -e text '' 2>&1"
    )


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--adb", default=r"D:\leidian\LDPlayer9\adb.exe")
    parser.add_argument("--captain", default="emulator-5556", help="队长 ADB serial")
    parser.add_argument("--member", default="emulator-5558", help="队员 ADB serial")
    parser.add_argument("--templates", default="", help="模板目录")
    args = parser.parse_args()

    # 初始化 ADB
    captain_adb = ADBController(args.captain, args.adb)
    member_adb = ADBController(args.member, args.adb)

    # 加载模板
    template_dir = args.templates
    if not template_dir:
        root = os.path.dirname(os.path.abspath(__file__))
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
        adb=captain_adb, matcher=matcher, role="captain"
    )
    member_runner = SingleInstanceRunner(
        adb=member_adb, matcher=matcher, role="member"
    )

    # ── 步骤0: 清除剪贴板 ──
    logger.info("=" * 50)
    logger.info("步骤0: 清除两台模拟器剪贴板")
    await clear_clipboard(captain_adb)
    await clear_clipboard(member_adb)
    logger.info("剪贴板已清除 ✓")

    # ── 步骤1: 队长创建队伍 ──
    logger.info("=" * 50)
    logger.info("步骤1: 队长创建队伍 (emulator-5556)")
    result = await captain_runner.phase_team_create()
    if not result:
        logger.error("❌ 队长创建队伍失败！")
        return

    logger.info("队长创建队伍完成 ✓")

    # ── 步骤2: 读取队长剪贴板获取口令码 ──
    logger.info("=" * 50)
    logger.info("步骤2: 读取队长剪贴板口令码")
    await asyncio.sleep(0.5)
    team_code = await read_clipboard(captain_adb)
    logger.info(f"口令码: '{team_code}'")

    if not team_code:
        logger.error("❌ 未能读取口令码！")
        return

    # ── 步骤3: 写入队员剪贴板 ──
    logger.info("=" * 50)
    logger.info(f"步骤3: 写入队员剪贴板 (口令码: {team_code})")
    await member_adb.set_clipboard(team_code)
    await asyncio.sleep(0.3)

    # 验证写入
    verify = await read_clipboard(member_adb)
    logger.info(f"队员剪贴板验证: '{verify}'")

    # ── 步骤4: 队员加入队伍 ──
    logger.info("=" * 50)
    logger.info("步骤4: 队员加入队伍 (emulator-5558)")
    ok = await member_runner.phase_team_join(team_code)
    if ok:
        logger.info("✅ 队员加入队伍成功！")
    else:
        logger.error("❌ 队员加入队伍失败！")

    logger.info("=" * 50)
    logger.info("测试完成")


if __name__ == "__main__":
    asyncio.run(main())

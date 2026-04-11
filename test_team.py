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


async def read_team_code_from_windows(adb_path: str) -> str:
    """从 Windows 剪贴板读取口令码

    LDPlayer 会把模拟器剪贴板同步到 Windows 剪贴板。
    "分享口令码" 复制的格式：
    【微信和QQ】...（整段复制后...）BU2L4080https://... CA9120 ?7324
    口令码是 https:// 前面的大写字母+数字组合（8位）
    """
    import re
    import subprocess
    loop = asyncio.get_event_loop()
    output = await loop.run_in_executor(
        None, subprocess.check_output,
        ["powershell", "-command", "Get-Clipboard"],
    )
    text = output.decode("utf-8", errors="ignore").strip()
    logger.info(f"Windows 剪贴板原文: {text[:80]}...")

    # 提取口令码：https:// 前面的 8 位大写字母+数字
    match = re.search(r'[A-Z0-9]{6,10}(?=https?://)', text)
    if match:
        return match.group(0)

    # 兜底：找所有大写字母+数字的 8 位组合
    matches = re.findall(r'[A-Z][A-Z0-9]{6,9}', text)
    if matches:
        return matches[0]

    return ""


async def clear_windows_clipboard():
    """清空 Windows 剪贴板（允许失败）"""
    import subprocess
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, subprocess.run,
            ["powershell", "-command", "Set-Clipboard -Value 'CLEAR'"],
        )
    except Exception:
        pass  # LDPlayer 可能锁住剪贴板，忽略


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

    # ── 步骤0: 清除 Windows 剪贴板 ──
    logger.info("=" * 50)
    logger.info("步骤0: 清除 Windows 剪贴板")
    await clear_windows_clipboard()
    logger.info("剪贴板已清除 ✓")

    # ── 步骤1: 队长创建队伍 ──
    logger.info("=" * 50)
    logger.info("步骤1: 队长创建队伍 (emulator-5556)")
    result = await captain_runner.phase_team_create()
    if not result:
        logger.error("❌ 队长创建队伍失败！")
        return

    logger.info("队长创建队伍完成 ✓")

    # ── 步骤2: 从 Windows 剪贴板读取口令码 ──
    logger.info("=" * 50)
    logger.info("步骤2: 从 Windows 剪贴板读取口令码")
    await asyncio.sleep(0.5)
    team_code = await read_team_code_from_windows(args.adb)
    logger.info(f"口令码: '{team_code}'")

    if not team_code:
        logger.error("❌ 未能读取口令码！")
        return

    # ── 步骤3: 通过 ADB 写入队员剪贴板 ──
    logger.info("=" * 50)
    logger.info(f"步骤3: 通过 ADB 写入队员剪贴板 (口令码: {team_code})")
    # 用 input text 模拟输入的方式设置剪贴板（兼容性最好）
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None, member_adb._cmd, "shell",
        f"am broadcast -a clipper.set -e text '{team_code}'"
    )
    await asyncio.sleep(0.3)

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

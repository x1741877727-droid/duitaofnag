"""
Windows 上直接运行的单实例测试脚本
用法: python run_test.py [--instance 1] [--lobby-only] [--phase accelerator|game|popups|team|map]
"""
import asyncio
import logging
import sys
import os

# 确保能导入 backend 模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend.automation.single_runner import SingleInstanceRunner, ADBController, Phase
from backend.automation.screen_matcher import ScreenMatcher

logger = logging.getLogger("run_test")


async def run_single_phase(runner: SingleInstanceRunner, phase: str):
    """运行单个阶段"""
    if phase == "accelerator":
        ok = await runner.phase_accelerator()
        logger.info(f"阶段0 加速器: {'成功' if ok else '失败'}")
        return ok
    elif phase == "game":
        ok = await runner.phase_launch_game()
        logger.info(f"阶段1 启动游戏: {'成功' if ok else '失败'}")
        return ok
    elif phase == "popups":
        ok = await runner.phase_dismiss_popups()
        logger.info(f"阶段3 弹窗清理: {'成功' if ok else '失败'}")
        return ok
    elif phase == "team":
        code = await runner.phase_team_create()
        logger.info(f"阶段4 组队: {'成功 code=' + str(code) if code else '失败'}")
        return code is not None
    elif phase == "map":
        ok = await runner.phase_map_setup()
        logger.info(f"阶段6 地图设置: {'成功' if ok else '失败'}")
        return ok
    else:
        logger.error(f"未知阶段: {phase}")
        return False


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="单实例自动化测试")
    parser.add_argument("--instance", type=int, default=1, help="模拟器实例编号 (0=5554, 1=5556, 2=5558)")
    parser.add_argument("--adb", default=r"D:\leidian\LDPlayer9\adb.exe", help="ADB路径")
    parser.add_argument("--lobby-only", action="store_true", help="只运行到大厅（阶段0-3）")
    parser.add_argument("--phase", default="", help="只运行指定阶段: accelerator|game|popups|team|map")
    parser.add_argument("--role", default="captain", choices=["captain", "member"])
    args = parser.parse_args()

    # 配置日志 — 同时输出到控制台和文件
    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(level=logging.INFO, format=log_format)

    # 文件日志
    fh = logging.FileHandler("run_test.log", encoding="utf-8", mode="w")
    fh.setFormatter(logging.Formatter(log_format))
    logging.getLogger().addHandler(fh)

    serial = f"emulator-{5554 + args.instance * 2}"
    logger.info(f"目标设备: {serial}")
    logger.info(f"ADB路径: {args.adb}")

    # 模板目录
    tmpl_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "templates")
    logger.info(f"模板目录: {tmpl_dir}")

    # 初始化
    adb = ADBController(serial, args.adb)
    matcher = ScreenMatcher(tmpl_dir)
    n = matcher.load_all()
    logger.info(f"已加载 {n} 个模板: {matcher.template_names}")

    if n == 0:
        logger.error("未找到模板文件！")
        sys.exit(1)

    runner = SingleInstanceRunner(
        adb=adb,
        matcher=matcher,
        role=args.role,
    )

    # 运行
    if args.phase:
        # 单阶段模式
        ok = await run_single_phase(runner, args.phase)
        logger.info(f"单阶段 [{args.phase}] 结果: {'成功' if ok else '失败'}")
    elif args.lobby_only:
        ok = await runner.run_to_lobby()
        logger.info(f"到大厅结果: {'成功' if ok else '失败'}, 最终阶段: {runner.phase}")
    else:
        ok = await runner.run_full()
        logger.info(f"完整流程结果: {'成功' if ok else '失败'}, 最终阶段: {runner.phase}")

    # 输出日志文件位置
    log_path = os.path.abspath("run_test.log")
    logger.info(f"日志已保存: {log_path}")


if __name__ == "__main__":
    asyncio.run(main())

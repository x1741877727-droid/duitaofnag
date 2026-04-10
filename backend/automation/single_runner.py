"""
SingleInstanceRunner — 单实例自动化运行器
控制一个模拟器实例完成: 加速器 → 启动游戏 → 弹窗清理 → 大厅确认 → 组队 → 地图设置

可在Windows上直接运行:
  python -m backend.automation.single_runner --instance 0
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import cv2
import numpy as np

from .screen_matcher import MatchHit, ScreenMatcher
from .popup_dismisser import PopupDismisser
from .ocr_dismisser import OcrDismisser

logger = logging.getLogger(__name__)


class Phase(str, Enum):
    """运行阶段"""
    INIT = "init"
    ACCELERATOR = "accelerator"
    LAUNCH_GAME = "launch_game"
    WAIT_LOGIN = "wait_login"
    DISMISS_POPUPS = "dismiss_popups"
    LOBBY = "lobby"
    MAP_SETUP = "map_setup"
    TEAM_CREATE = "team_create"
    TEAM_JOIN = "team_join"
    DONE = "done"
    ERROR = "error"


class ADBController:
    """
    ADB控制器 — 直接调用adb命令
    专门为雷电模拟器优化
    """

    def __init__(self, serial: str, adb_path: str = "adb"):
        self.serial = serial
        self.adb_path = adb_path
        self._proc_timeout = 10

    def _cmd(self, *args) -> str:
        """同步执行adb命令"""
        import subprocess
        cmd = [self.adb_path, "-s", self.serial] + list(args)
        try:
            result = subprocess.run(
                cmd, capture_output=True, timeout=self._proc_timeout
            )
            # 尝试多种编码解码
            for enc in ("utf-8", "gbk"):
                try:
                    return result.stdout.decode(enc)
                except UnicodeDecodeError:
                    continue
            return result.stdout.decode("utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            logger.warning(f"ADB命令超时: {cmd}")
            return ""
        except Exception as e:
            logger.error(f"ADB命令失败: {cmd} -> {e}")
            return ""

    async def screenshot(self) -> Optional[np.ndarray]:
        """截图并返回numpy数组 (BGR)"""
        loop = asyncio.get_event_loop()
        try:
            raw = await loop.run_in_executor(None, self._screenshot_sync)
            return raw
        except Exception as e:
            logger.error(f"截图失败: {e}")
            return None

    def _screenshot_sync(self) -> Optional[np.ndarray]:
        """同步截图"""
        import subprocess
        cmd = [self.adb_path, "-s", self.serial, "exec-out", "screencap", "-p"]
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=10)
            if result.returncode != 0:
                return None
            png_data = result.stdout
            if len(png_data) < 100:
                return None
            arr = np.frombuffer(png_data, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            return img
        except Exception:
            return None

    async def tap(self, x: int, y: int):
        """点击（带随机抖动）"""
        import random
        jx = x + random.randint(-3, 3)
        jy = y + random.randint(-3, 3)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, self._cmd, "shell", f"input tap {jx} {jy}"
        )

    async def key_event(self, key: str):
        """按键事件"""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, self._cmd, "shell", f"input keyevent {key}"
        )

    async def start_app(self, package: str, activity: str = ""):
        """启动应用"""
        if activity:
            component = f"{package}/{activity}"
            await self._async_cmd("shell", f"am start -n {component}")
        else:
            await self._async_cmd("shell", f"monkey -p {package} -c android.intent.category.LAUNCHER 1")

    async def stop_app(self, package: str):
        """强制停止应用"""
        await self._async_cmd("shell", f"am force-stop {package}")

    async def get_clipboard(self) -> str:
        """读取剪贴板"""
        loop = asyncio.get_event_loop()
        output = await loop.run_in_executor(
            None, self._cmd, "shell", "am broadcast -a clipper.get"
        )
        # 解析剪贴板内容（需要剪贴板服务或特殊方法）
        return output.strip()

    async def set_clipboard(self, text: str):
        """写入剪贴板"""
        # 使用input text方法 或 am broadcast
        await self._async_cmd("shell", f"am broadcast -a clipper.set -e text '{text}'")

    async def open_url(self, url: str):
        """通过intent打开URL"""
        await self._async_cmd("shell", f"am start -a android.intent.action.VIEW -d '{url}'")

    async def _async_cmd(self, *args) -> str:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._cmd, *args)


# ====================================================================
# 游戏常量
# ====================================================================

GAME_PACKAGE = "com.tencent.tmgp.pubgmhd"
ACCELERATOR_PACKAGE = "com.tencent.lhjsqxfb"  # 六花加速器 (待确认)


class SingleInstanceRunner:
    """
    单实例自动化运行器

    控制一个模拟器实例从启动加速器到进入大厅的完整流程。
    """

    def __init__(
        self,
        adb: ADBController,
        matcher: ScreenMatcher,
        role: str = "captain",  # "captain" | "member"
        target_mode: str = "团队竞技",
        target_map: str = "狙击团竞",
    ):
        self.adb = adb
        self.matcher = matcher
        self.role = role
        self.target_mode = target_mode
        self.target_map = target_map
        self.phase = Phase.INIT
        self.popup_dismisser = PopupDismisser(matcher)
        self.ocr_dismisser = OcrDismisser(max_rounds=25)
        self._team_code: str = ""  # 队长生成的口令码

    # ================================================================
    # 阶段 0: 加速器
    # ================================================================

    async def phase_accelerator(self) -> bool:
        """启动加速器并确认连接"""
        self.phase = Phase.ACCELERATOR
        logger.info("[阶段0] 启动加速器")

        await self.adb.start_app(ACCELERATOR_PACKAGE)
        await asyncio.sleep(3)

        play_click_count = 0  # 连续点击play的次数

        for attempt in range(15):
            shot = await self.adb.screenshot()
            if shot is None:
                await asyncio.sleep(1)
                continue

            status = self.matcher.is_accelerator_connected(shot)

            if status is True:
                logger.info("[阶段0] 加速器已连接 ✓")
                await self.adb.key_event("KEYCODE_HOME")
                await asyncio.sleep(1)
                return True

            if status is False:
                play_click_count += 1
                # 连续点了3次play还没连上，可能有弹窗挡住了
                if play_click_count >= 3:
                    logger.info("[阶段0] 连续点击无效，按返回键清除可能的弹窗")
                    await self.adb.key_event("KEYCODE_BACK")
                    play_click_count = 0
                    await asyncio.sleep(2)
                    continue

                logger.info("[阶段0] 点击启动按钮")
                play_hit = self.matcher.match_one(shot, "accelerator_play")
                if play_hit:
                    await self.adb.tap(play_hit.cx, play_hit.cy)
                await asyncio.sleep(3)
                continue

            # status is None — 按返回键
            logger.info("[阶段0] 不在加速器主界面，按返回键")
            await self.adb.key_event("KEYCODE_BACK")
            play_click_count = 0
            await asyncio.sleep(2)

        logger.error("[阶段0] 加速器启动超时")
        return False

    # ================================================================
    # 阶段 1: 启动游戏
    # ================================================================

    async def phase_launch_game(self) -> bool:
        """启动游戏并等待到大厅或弹窗阶段"""
        self.phase = Phase.LAUNCH_GAME
        logger.info("[阶段1] 启动游戏")

        await self.adb.start_app(GAME_PACKAGE)
        await asyncio.sleep(8)  # 游戏启动需要时间

        # 等待游戏加载完成，最多90秒
        # 只要检测到"公告"或"开始游戏"或弹窗关键词就说明加载完了
        for attempt in range(45):
            shot = await self.adb.screenshot()
            if shot is None:
                await asyncio.sleep(2)
                continue

            # 用OCR检测当前画面
            hits = self.ocr_dismisser.ocr_screen(shot)
            all_text = " ".join(h.text for h in hits)
            logger.info(f"[阶段1] 加载中R{attempt+1}: OCR={all_text[:80]}")

            # 检测到大厅标志或弹窗标志 → 加载完成，交给阶段3处理
            if any(kw in all_text for kw in ["开始游戏", "公告", "活动", "更新公告", "立即前往"]):
                logger.info("[阶段1] 游戏加载完成，进入弹窗清理阶段")
                return True

            await asyncio.sleep(2)

        logger.warning("[阶段1] 游戏加载超时(90s)")
        return False

    # ================================================================
    # 阶段 2: 登录检测
    # ================================================================

    async def phase_wait_login(self, timeout: int = 20) -> bool:
        """等待自动登录完成"""
        self.phase = Phase.WAIT_LOGIN
        logger.info("[阶段2] 等待自动登录")

        start = time.time()
        while time.time() - start < timeout:
            shot = await self.adb.screenshot()
            if shot is None:
                await asyncio.sleep(1)
                continue

            if self.matcher.is_at_lobby(shot):
                logger.info("[阶段2] 登录成功，已在大厅 ✓")
                return True

            # 检测登录页面停留太久 → 告警
            # TODO: 用模板匹配检测"微信登录"/"QQ登录"按钮

            await asyncio.sleep(1)

        logger.warning("[阶段2] 自动登录超时，可能需要手动登录")
        return False

    # ================================================================
    # 阶段 3: 弹窗清理
    # ================================================================

    async def phase_dismiss_popups(self) -> bool:
        """清理所有弹窗直到大厅（OCR驱动）"""
        self.phase = Phase.DISMISS_POPUPS
        logger.info("[阶段3] 开始弹窗清理 (OCR模式)")

        result = await self.ocr_dismisser.dismiss_all(self.adb, self.matcher)
        logger.info(f"[阶段3] 结果: {result.final_state}, 关闭{result.popups_closed}个弹窗, 共{result.rounds}轮")
        return result.success

    # ================================================================
    # 阶段 6: 地图设置 (队长)
    # ================================================================

    async def phase_map_setup(self) -> bool:
        """队长设置地图和模式"""
        self.phase = Phase.MAP_SETUP
        logger.info(f"[阶段6] 地图设置: {self.target_mode} - {self.target_map}")

        # 确认在大厅
        shot = await self.adb.screenshot()
        if shot is None or not self.matcher.is_at_lobby(shot):
            logger.error("[阶段6] 不在大厅")
            return False

        # 点击地图区域（左上角"开始游戏"下方的模式名）
        await self.adb.tap(100, 105)
        await asyncio.sleep(2)

        # 等待地图选择面板打开
        shot = await self.adb.screenshot()
        if shot is None:
            return False

        # 检测"团队竞技"模式入口 — 用模板匹配
        mode_hit = self.matcher.find_button(shot, "btn_team_battle_entry", threshold=0.65)
        if mode_hit:
            logger.info(f"[阶段6] 点击团队竞技: ({mode_hit.cx},{mode_hit.cy})")
            await self.adb.tap(mode_hit.cx, mode_hit.cy)
            await asyncio.sleep(1)
            shot = await self.adb.screenshot()
            if shot is None:
                return False

        # 检测狙击团竞是否可见 — 用模板匹配
        sniper_hit = self.matcher.find_button(shot, "card_sniper_team", threshold=0.65)
        if sniper_hit:
            logger.info(f"[阶段6] 点击狙击团竞: ({sniper_hit.cx},{sniper_hit.cy})")
            await self.adb.tap(sniper_hit.cx, sniper_hit.cy)
            await asyncio.sleep(1)

        # 点击"确定"按钮 — 用模板匹配定位
        shot = await self.adb.screenshot()
        if shot is not None:
            confirm = self.matcher.find_button(shot, "btn_confirm_map", threshold=0.70)
            if confirm:
                logger.info(f"[阶段6] 点击确定: ({confirm.cx},{confirm.cy})")
                await self.adb.tap(confirm.cx, confirm.cy)
                await asyncio.sleep(1)
                logger.info("[阶段6] 地图设置完成 ✓")
                return True

        # 兜底：按返回键退出
        await self.adb.key_event("KEYCODE_BACK")
        await asyncio.sleep(1)
        return True

    # ================================================================
    # 阶段 4: 组队 — 队长创建
    # ================================================================

    async def phase_team_create(self) -> Optional[str]:
        """队长创建队伍并获取口令码，返回口令码"""
        self.phase = Phase.TEAM_CREATE
        logger.info("[阶段4] 队长创建队伍")

        # 1. 打开好友/组队面板：点击左侧"组队"文字
        await self.adb.tap(35, 385)
        await asyncio.sleep(2)

        # 2. 点击底部"组队码"按钮 — 用模板匹配定位
        shot = await self.adb.screenshot()
        if shot is not None:
            code_tab = self.matcher.find_button(shot, "btn_team_code_tab", threshold=0.70)
            if code_tab:
                await self.adb.tap(code_tab.cx, code_tab.cy)
            else:
                # 兜底：底部组队码按钮大约在此位置
                await self.adb.tap(310, 710)
            await asyncio.sleep(2)

        # 3. 点击"分享组队口令码" — 用模板匹配定位
        shot = await self.adb.screenshot()
        if shot is not None:
            share_btn = self.matcher.find_button(shot, "btn_share_team_code", threshold=0.70)
            if share_btn:
                logger.info(f"[阶段4] 分享按钮: ({share_btn.cx},{share_btn.cy})")
                await self.adb.tap(share_btn.cx, share_btn.cy)
                await asyncio.sleep(1)
                # 口令码已静默复制到剪贴板（通过模拟器共享到Windows剪贴板）
                logger.info("[阶段4] 口令码已复制到剪贴板")
                # 关闭组队码面板
                close = self.matcher.find_dialog_close(shot)
                if close:
                    await self.adb.tap(close.cx, close.cy)
                return "clipboard"  # 实际码从Windows剪贴板读取
            else:
                logger.warning("[阶段4] 未找到'分享组队口令码'按钮")

        return None

    # ================================================================
    # 阶段 5: 组队 — 队员加入
    # ================================================================

    async def phase_team_join(self, team_code: str) -> bool:
        """队员通过口令码加入队伍"""
        self.phase = Phase.TEAM_JOIN
        logger.info("[阶段5] 队员加入队伍")

        # 先把口令码写入剪贴板
        await self.adb.set_clipboard(team_code)
        await asyncio.sleep(1)

        # 检测是否自动弹出"使用组队码加入"提示
        for _ in range(5):
            shot = await self.adb.screenshot()
            if shot is None:
                await asyncio.sleep(1)
                continue

            join_hit = self.matcher.match_one(shot, "btn_join", threshold=0.70)
            if join_hit:
                logger.info("[阶段5] 检测到加入提示，点击加入")
                await self.adb.tap(join_hit.cx, join_hit.cy)
                await asyncio.sleep(2)
                return True
            await asyncio.sleep(1)

        # 如果没自动弹出，手动走组队码路径
        logger.info("[阶段5] 未自动弹出，手动走组队码流程")

        # 1. 打开好友/组队面板
        await self.adb.tap(35, 385)
        await asyncio.sleep(2)

        # 2. 点击底部"组队码" — 用模板匹配
        shot = await self.adb.screenshot()
        if shot is not None:
            code_tab = self.matcher.find_button(shot, "btn_team_code_tab", threshold=0.70)
            if code_tab:
                await self.adb.tap(code_tab.cx, code_tab.cy)
            else:
                await self.adb.tap(310, 710)
            await asyncio.sleep(2)

        # 3. 点击"粘贴口令" — 用模板匹配
        shot = await self.adb.screenshot()
        if shot is not None:
            paste_btn = self.matcher.find_button(shot, "btn_paste_code", threshold=0.70)
            if paste_btn:
                await self.adb.tap(paste_btn.cx, paste_btn.cy)
                await asyncio.sleep(1)

            # 4. 点击"加入队伍" — 用模板匹配
            shot = await self.adb.screenshot()
            if shot is not None:
                join_btn = self.matcher.find_button(shot, "btn_join_team", threshold=0.70)
                if join_btn:
                    await self.adb.tap(join_btn.cx, join_btn.cy)
                    await asyncio.sleep(2)
                    return True

        logger.warning("[阶段5] 加入队伍流程异常")
        return False

    # ================================================================
    # 主运行循环
    # ================================================================

    async def run_to_lobby(self) -> bool:
        """
        执行从启动到大厅的完整流程

        Returns:
            True = 成功到达大厅
        """
        logger.info(f"=== 单实例运行开始 (角色: {self.role}) ===")

        # 阶段0: 加速器
        if not await self.phase_accelerator():
            self.phase = Phase.ERROR
            return False

        # 阶段1: 启动游戏
        if not await self.phase_launch_game():
            self.phase = Phase.ERROR
            return False

        # 阶段2+3: 登录+弹窗（合并处理）
        # 游戏启动后，可能先看到登录页再到大厅，也可能直接到弹窗
        # 用弹窗清理循环统一处理
        if not await self.phase_dismiss_popups():
            # 如果弹窗清理超时，可能是登录失败
            logger.warning("弹窗清理失败，尝试检测登录状态...")
            self.phase = Phase.ERROR
            return False

        logger.info("=== 成功到达大厅 ===")
        self.phase = Phase.LOBBY
        return True

    async def run_full(self, team_code: str = "") -> bool:
        """
        完整流程: 启动到大厅 → 地图设置 → 组队

        Args:
            team_code: 队员需要传入队长的口令码
        """
        # 先到大厅
        if not await self.run_to_lobby():
            return False

        if self.role == "captain":
            # 队长: 地图设置 → 创建队伍
            await self.phase_map_setup()
            code = await self.phase_team_create()
            if code:
                self._team_code = code
            self.phase = Phase.DONE
            return True
        else:
            # 队员: 等队长口令码 → 加入
            if not team_code:
                logger.error("队员需要口令码")
                return False
            result = await self.phase_team_join(team_code)
            self.phase = Phase.DONE
            return result


# ====================================================================
# CLI 入口
# ====================================================================

async def main():
    import argparse
    import sys
    from pathlib import Path

    parser = argparse.ArgumentParser(description="单实例自动化运行")
    parser.add_argument("--instance", type=int, default=0, help="模拟器实例编号 (0-5)")
    parser.add_argument("--adb", default="adb", help="ADB路径")
    parser.add_argument("--role", default="captain", choices=["captain", "member"])
    parser.add_argument("--mode", default="团队竞技", help="目标模式")
    parser.add_argument("--map", default="狙击团竞", help="目标地图")
    parser.add_argument("--templates", default="", help="模板目录")
    parser.add_argument("--lobby-only", action="store_true", help="只运行到大厅")
    args = parser.parse_args()

    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    # ADB serial
    serial = f"emulator-{5554 + args.instance * 2}"
    logger.info(f"目标设备: {serial}")

    # 模板目录
    if args.templates:
        tmpl_dir = args.templates
    else:
        # 自动查找
        project_root = Path(__file__).parent.parent.parent
        tmpl_dir = str(project_root / "fixtures" / "templates")

    # 初始化
    adb = ADBController(serial, args.adb)
    matcher = ScreenMatcher(tmpl_dir)
    n = matcher.load_all()
    if n == 0:
        logger.error(f"未找到模板文件: {tmpl_dir}")
        sys.exit(1)
    logger.info(f"已加载模板: {matcher.template_names}")

    runner = SingleInstanceRunner(
        adb=adb,
        matcher=matcher,
        role=args.role,
        target_mode=args.mode,
        target_map=args.map,
    )

    if args.lobby_only:
        success = await runner.run_to_lobby()
    else:
        success = await runner.run_full()

    logger.info(f"运行结果: {'成功' if success else '失败'}, 最终阶段: {runner.phase}")
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())

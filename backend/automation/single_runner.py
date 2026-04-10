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

from .adb_lite import ADBController
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
        on_phase_change=None,  # 回调: (Phase) -> None
    ):
        self.adb = adb
        self.matcher = matcher
        self.role = role
        self.target_mode = target_mode
        self.target_map = target_map
        self._phase = Phase.INIT
        self._on_phase_change = on_phase_change
        self.popup_dismisser = PopupDismisser(matcher)
        self.ocr_dismisser = OcrDismisser(max_rounds=25)
        self._team_code: str = ""  # 队长生成的口令码

    @property
    def phase(self) -> Phase:
        return self._phase

    @phase.setter
    def phase(self, value: Phase):
        old = self._phase
        self._phase = value
        if self._on_phase_change and old != value:
            self._on_phase_change(value)

    # ================================================================
    # 阶段 0: 加速器
    # ================================================================

    async def phase_accelerator(self) -> bool:
        """启动加速器并确认连接，再用百度验证网络真通"""
        self.phase = Phase.ACCELERATOR

        for retry in range(3):
            if retry > 0:
                logger.info(f"[阶段0] 第{retry+1}次重试加速器")

            if not await self._start_accelerator():
                continue

            # 加速器显示已连接 → 验证网络是否真通
            if await self._verify_network():
                return True

            # 网络不通 → 重启加速器
            logger.warning(f"[阶段0] 网络验证失败，重启加速器 (第{retry+1}次)")
            await self.adb.stop_app(ACCELERATOR_PACKAGE)
            await asyncio.sleep(2)

        logger.error("[阶段0] 加速器3次重试均失败")
        return False

    async def _start_accelerator(self) -> bool:
        """启动加速器并等待连接成功（轮询驱动，不靠固定等待）"""
        logger.info("[阶段0] 启动加速器")
        await self.adb.start_app(ACCELERATOR_PACKAGE)

        play_click_count = 0
        # 轮询：每次截图检查，快电脑秒过，慢电脑也能等到
        for attempt in range(30):  # 最多30次 × ~1.5s ≈ 45秒
            await asyncio.sleep(1.5)
            shot = await self.adb.screenshot()
            if shot is None:
                continue

            status = self.matcher.is_accelerator_connected(shot)

            if status is True:
                logger.info(f"[阶段0] 加速器已连接 ✓ ({attempt+1}轮)")
                await self.adb.key_event("KEYCODE_HOME")
                await asyncio.sleep(0.5)
                return True

            if status is False:
                play_click_count += 1
                if play_click_count >= 3:
                    logger.info("[阶段0] 连续点击无效，按返回键清除可能的弹窗")
                    await self.adb.key_event("KEYCODE_BACK")
                    play_click_count = 0
                    continue

                logger.info("[阶段0] 点击启动按钮")
                play_hit = self.matcher.match_one(shot, "accelerator_play")
                if play_hit:
                    await self.adb.tap(play_hit.cx, play_hit.cy)
                continue

            # status is None — 不在主界面
            logger.info("[阶段0] 不在加速器主界面，按返回键")
            await self.adb.key_event("KEYCODE_BACK")
            play_click_count = 0

        logger.error("[阶段0] 加速器启动超时")
        return False

    async def _verify_network(self) -> bool:
        """打开百度验证网络（轮询驱动，检测到结果立即返回）"""
        logger.info("[阶段0] 网络验证: 打开百度...")
        await self.adb.open_url("https://m.baidu.com/")

        SUCCESS_KEYWORDS = ["验证成功", "端口验证", "六花端口"]
        # 注意："搜索"不能用——浏览器地址栏自带"搜索或输入网址"
        FAIL_KEYWORDS = ["百度一下", "热搜", "百度首页"]

        # 每1.5秒检查一次，前3秒只看不判（等页面加载），最多12秒
        for check in range(8):
            await asyncio.sleep(1.5)
            shot = await self.adb.screenshot()
            if shot is None:
                continue

            hits = self.ocr_dismisser.ocr_screen(shot)
            all_text = " ".join(h.text for h in hits)
            logger.info(f"[阶段0] 网络验证R{check+1}: OCR={all_text[:60]}")

            # 前2轮（前3秒）只观察不判定，等页面内容真正加载
            if check < 2:
                if any(kw in all_text for kw in SUCCESS_KEYWORDS):
                    logger.info("[阶段0] 网络验证通过 ✓ 加速器劫持确认")
                    await self._close_browser()
                    return True
                continue  # 前3秒只判成功，不判失败（页面可能还没加载）

            if any(kw in all_text for kw in SUCCESS_KEYWORDS):
                logger.info("[阶段0] 网络验证通过 ✓ 加速器劫持确认")
                await self._close_browser()
                return True

            if any(kw in all_text for kw in FAIL_KEYWORDS):
                logger.warning("[阶段0] 网络验证失败: 百度真实页面，加速器未工作")
                await self._close_browser()
                return False

        logger.warning("[阶段0] 网络验证失败: 超时")
        await self._close_browser()
        return False

    async def _close_browser(self):
        """关掉浏览器回桌面"""
        await self.adb.stop_app("com.android.browser")
        await asyncio.sleep(0.3)
        await self.adb.key_event("KEYCODE_HOME")
        await asyncio.sleep(0.3)

    # ================================================================
    # 阶段 1: 启动游戏
    # ================================================================

    async def phase_launch_game(self) -> bool:
        """启动游戏并等待到大厅或弹窗阶段"""
        self.phase = Phase.LAUNCH_GAME
        logger.info("[阶段1] 启动游戏")

        await self.adb.start_app(GAME_PACKAGE)

        # 轮询等待加载完成（不固定等待，快电脑秒过，慢电脑也能等到）
        # GuardedADB 会自动处理加载中弹出的系统弹窗（内存提醒等）
        for attempt in range(60):  # 最多60次 × 1.5s = 90秒
            await asyncio.sleep(1.5)
            shot = await self.adb.screenshot()  # 守卫自动清弹窗
            if shot is None:
                continue

            # 前5轮用模板快速检查（OCR 慢，游戏刚启动不需要OCR）
            if attempt < 5 and self.matcher:
                if self.matcher.is_at_lobby(shot):
                    logger.info("[阶段1] 游戏已在大厅（模板检测）")
                    return True
                continue  # 前几秒大概率还在启动画面，跳过 OCR

            # 用OCR检测当前画面
            hits = self.ocr_dismisser.ocr_screen(shot)
            all_text = " ".join(h.text for h in hits)
            logger.info(f"[阶段1] 加载中R{attempt+1}: OCR={all_text[:80]}")

            # 检测到大厅标志或弹窗标志 → 加载完成
            if any(kw in all_text for kw in ["开始游戏", "公告", "活动", "更新公告", "立即前往"]):
                logger.info("[阶段1] 游戏加载完成，进入弹窗清理阶段")
                return True

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

        # 关闭守卫：这个阶段自己完整处理弹窗，避免和守卫冲突
        if hasattr(self.adb, 'guard_enabled'):
            self.adb.guard_enabled = False

        result = await self.ocr_dismisser.dismiss_all(self.adb, self.matcher)

        # 恢复守卫
        if hasattr(self.adb, 'guard_enabled'):
            self.adb.guard_enabled = True

        logger.info(f"[阶段3] 结果: {result.final_state}, 关闭{result.popups_closed}个弹窗, 共{result.rounds}轮")
        return result.success

    # ================================================================
    # 阶段 6: 地图设置 (队长)
    # ================================================================

    async def phase_map_setup(self) -> bool:
        """队长设置地图和模式（OCR驱动，单次扫描提取所有目标）"""
        self.phase = Phase.MAP_SETUP
        logger.info(f"[阶段6] 地图设置: {self.target_mode} - {self.target_map}")

        # 禁用守卫（地图面板的关闭按钮会被守卫误判为弹窗）
        if hasattr(self.adb, 'guard_enabled'):
            self.adb.guard_enabled = False

        ocr = OcrDismisser()

        # 构建地图模糊关键词（OCR 常把"狙击"识别为"姐击"/"阻击"等）
        map_keywords = [self.target_map]
        if "狙击" in self.target_map:
            map_keywords.extend(["击团竞大桥", "击团竞"])
        elif "经典" in self.target_map:
            map_keywords.extend(["经典团竞仓库", "经典团竞"])
        elif "军备" in self.target_map:
            map_keywords.extend(["军备团竞图书", "军备团竞"])

        # ── 步骤1: 打开地图面板（模板优先，不用OCR）──
        shot = await self.adb.screenshot()
        if shot is None:
            return False

        hit = self.matcher.match_one(shot, "lobby_start_btn", threshold=0.7)
        if hit:
            await self.adb.tap(hit.cx, hit.cy + 60)
            logger.info(f"[阶段6] 模板定位，点击模式名 ({hit.cx},{hit.cy + 60})")
        else:
            # OCR 兜底找"开始游戏"
            hits = ocr._ocr_all(shot)
            for h in hits:
                if "开始游戏" in h.text:
                    await self.adb.tap(h.cx, h.cy + 60)
                    logger.info(f"[阶段6] OCR定位，点击模式名 ({h.cx},{h.cy + 60})")
                    break
            else:
                logger.error("[阶段6] 找不到'开始游戏'按钮")
                return False

        # ── 轮询等面板打开（等文字数量稳定）──
        hits = []
        prev_count = 0
        for _ in range(15):
            await asyncio.sleep(0.4)
            shot = await self.adb.screenshot()
            if shot is None:
                continue
            hits = ocr._ocr_all(shot)
            # 文字数量 > 30 且连续两次数量接近 = 面板已完全渲染
            if len(hits) > 30 and abs(len(hits) - prev_count) < 5:
                break
            prev_count = len(hits)

        if len(hits) < 20:
            logger.warning("[阶段6] 地图面板未打开")
            return False

        logger.info(f"[阶段6] 面板已打开 ({len(hits)}个文字)")

        # ── 一次性提取所有目标 ──
        team_battle_hit = None
        map_hit = None
        fill_hit = None
        confirm_hit = None
        is_team_battle = False

        all_text = " ".join(h.text for h in hits)
        for h in hits:
            if "团队竞技" in h.text and h.cx < 200:
                team_battle_hit = h
            if "确定" in h.text and h.cx > 1000:
                confirm_hit = h
            if "补位" in h.text:
                fill_hit = h
            if not map_hit:
                for kw in map_keywords:
                    if kw in h.text:
                        map_hit = h
                        break

        # 判断是否已在团竞模式：多种特征词任一命中
        is_team_battle = (fill_hit is not None or
                          "团竞手册" in all_text or
                          "团竞详情" in all_text or
                          "军备团竞" in all_text or
                          "经典团竞" in all_text or
                          "击团竞" in all_text or
                          "迷你战争" in all_text or
                          "轮换团竞" in all_text or
                          "突变团竞" in all_text or
                          map_hit is not None)  # 目标地图能找到说明已在对应分类

        # ── 判断是否需要切换模式 ──
        if not is_team_battle:
            if team_battle_hit:
                logger.info(f"[阶段6] 切换到团队竞技 ({team_battle_hit.cx},{team_battle_hit.cy})")
                await self.adb.tap(team_battle_hit.cx, team_battle_hit.cy)
                await asyncio.sleep(0.5)
                # 重新 OCR
                shot = await self.adb.screenshot()
                if shot is not None:
                    hits = ocr._ocr_all(shot)
                    map_hit = None
                    fill_hit = None
                    confirm_hit = None
                    for h in hits:
                        if "确定" in h.text and h.cx > 1000:
                            confirm_hit = h
                        if "补位" in h.text:
                            fill_hit = h
                        if not map_hit:
                            for kw in map_keywords:
                                if kw in h.text:
                                    map_hit = h
                                    break
            else:
                # 重试一次 OCR（可能面板还没完全渲染）
                logger.info("[阶段6] 未找到团队竞技，重试OCR...")
                await asyncio.sleep(0.5)
                shot = await self.adb.screenshot()
                if shot is not None:
                    hits = ocr._ocr_all(shot)
                    for h in hits:
                        if "团队竞技" in h.text:
                            logger.info(f"[阶段6] 重试找到团队竞技 ({h.cx},{h.cy})")
                            await self.adb.tap(h.cx, h.cy)
                            await asyncio.sleep(0.5)
                            break
                    else:
                        logger.warning("[阶段6] 未找到团队竞技入口")
                        self._restore_guard()
                        return False
        else:
            logger.info("[阶段6] 已在团队竞技，跳过切换")

        # ── 选择地图 ──
        if map_hit:
            logger.info(f"[阶段6] 选择地图 '{map_hit.text}' ({map_hit.cx},{map_hit.cy})")
            await self.adb.tap(map_hit.cx, map_hit.cy)
            await asyncio.sleep(0.3)
        else:
            logger.warning(f"[阶段6] 未找到目标地图 '{self.target_map}'")

        # ── 检查补位（像素检测勾选状态）──
        if fill_hit:
            shot = await self.adb.screenshot()
            if shot is not None:
                check_x = max(0, fill_hit.cx - 60)
                check_y = fill_hit.cy
                y1 = max(0, check_y - 5)
                y2 = min(shot.shape[0], check_y + 5)
                x1 = max(0, check_x - 5)
                x2 = min(shot.shape[1], check_x + 5)
                region = shot[y1:y2, x1:x2]
                if region.size > 0:
                    r_ch = region[:, :, 2]
                    g_ch = region[:, :, 1]
                    b_ch = region[:, :, 0]
                    orange_count = int(((r_ch > 150) & (g_ch > 80) & (b_ch < 80)).sum())
                    if orange_count > 5:
                        logger.info("[阶段6] 补位已开启 → 点击取消")
                        await self.adb.tap(fill_hit.cx, fill_hit.cy)
                        await asyncio.sleep(0.3)
                    else:
                        logger.info("[阶段6] 补位已关闭 → 跳过")

        # ── 点击确定 ──
        if confirm_hit:
            logger.info(f"[阶段6] 点击确定 ({confirm_hit.cx},{confirm_hit.cy})")
            await self.adb.tap(confirm_hit.cx, confirm_hit.cy)
        else:
            await self._ocr_tap(ocr, ["确定"], step="确定")

        # 恢复守卫
        if hasattr(self.adb, 'guard_enabled'):
            self.adb.guard_enabled = True

        logger.info("[阶段6] 地图设置完成 ✓")
        return True

    # ================================================================
    # 阶段 4: 组队 — 队长创建
    # ================================================================

    async def phase_team_create(self) -> Optional[str]:
        """队长创建队伍并获取口令码（优化版：最少OCR调用）"""
        self.phase = Phase.TEAM_CREATE
        logger.info("[阶段4] 队长创建队伍")

        # 禁用守卫
        if hasattr(self.adb, 'guard_enabled'):
            self.adb.guard_enabled = False

        # 清空剪贴板
        await self.adb.set_clipboard("")

        ocr = OcrDismisser()

        # ── 步骤1: OCR找"组队"并点击 ──
        shot = await self.adb.screenshot()
        if shot is None:
            self._restore_guard()
            return None
        hits = ocr._ocr_all(shot)
        clicked = False
        for h in hits:
            if "组队" in h.text and h.cx < 80:
                logger.info(f"[阶段4] 点击组队 ({h.cx},{h.cy})")
                await self.adb.tap(h.cx, h.cy)
                clicked = True
                break
        if not clicked:
            logger.warning("[阶段4] 未找到组队按钮")
            self._restore_guard()
            return None

        # ── 步骤2: 轮询等面板+找"组队码"一起做 ──
        for _ in range(10):
            await asyncio.sleep(0.3)
            shot = await self.adb.screenshot()
            if shot is None:
                continue
            hits = ocr._ocr_all(shot)
            for h in hits:
                if "组队码" in h.text and h.cy > 600:
                    logger.info(f"[阶段4] 点击组队码tab ({h.cx},{h.cy})")
                    await self.adb.tap(h.cx, h.cy)
                    break
            else:
                continue
            break

        # ── 步骤3: 轮询等组队码面板+找"分享"一起做 ──
        for _ in range(10):
            await asyncio.sleep(0.3)
            shot = await self.adb.screenshot()
            if shot is None:
                continue
            # 模板优先
            tmpl = self.matcher.match_one(shot, "btn_share_team_code", threshold=0.65)
            if tmpl:
                logger.info(f"[阶段4] 模板命中分享按钮 ({tmpl.cx},{tmpl.cy})")
                await self.adb.tap(tmpl.cx, tmpl.cy)
                break
            # OCR 兜底
            hits = ocr._ocr_all(shot)
            for h in hits:
                if "分享" in h.text and "口令" in h.text:
                    logger.info(f"[阶段4] OCR命中 '{h.text}' ({h.cx},{h.cy})")
                    await self.adb.tap(h.cx, h.cy)
                    break
            else:
                continue
            break

        logger.info("[阶段4] 口令码已复制到剪贴板")
        await asyncio.sleep(0.3)

        # ── 关闭面板：模板找X → 空白区域，轮询直到回大厅 ──
        for _ in range(4):
            shot = await self.adb.screenshot()
            if shot is None:
                break
            # 模板检测是否回到大厅
            if self.matcher.match_one(shot, "lobby_start_btn", threshold=0.7):
                break
            # 模板找 X 关闭
            close = self.matcher.find_dialog_close(shot)
            if close:
                logger.info(f"[阶段4] 关闭按钮 ({close.cx},{close.cy})")
                await self.adb.tap(close.cx, close.cy)
                await asyncio.sleep(0.3)
                continue
            # 点空白区域
            h, w = shot.shape[:2]
            await self.adb.tap(w * 3 // 4, h // 2)
            await asyncio.sleep(0.3)

        logger.info("[阶段4] 已关闭组队面板")
        self._restore_guard()
        return "clipboard"

    def _restore_guard(self):
        if hasattr(self.adb, 'guard_enabled'):
            self.adb.guard_enabled = True

    async def _ocr_tap(self, ocr: OcrDismisser, keywords: list[str],
                        template_fallback: str = "", step: str = "",
                        retries: int = 3) -> bool:
        """先模板（快~20ms），再OCR（慢~200ms），找到即点"""
        for attempt in range(retries):
            shot = await self.adb.screenshot()
            if shot is None:
                await asyncio.sleep(0.5)
                continue

            # ── 快速路径: 模板匹配 (~20ms) ──
            if template_fallback:
                tmpl_hit = self.matcher.match_one(shot, template_fallback, threshold=0.65)
                if tmpl_hit:
                    logger.info(f"[{step}] 模板匹配 '{template_fallback}' → ({tmpl_hit.cx},{tmpl_hit.cy})")
                    await self.adb.tap(tmpl_hit.cx, tmpl_hit.cy)
                    return True

            # ── 慢速路径: OCR (~200ms) ──
            hits = ocr._ocr_all(shot)
            for kw in keywords:
                for hit in hits:
                    if kw in hit.text:
                        logger.info(f"[{step}] OCR匹配 '{hit.text}' → ({hit.cx},{hit.cy})")
                        await self.adb.tap(hit.cx, hit.cy)
                        return True

            if attempt < retries - 1:
                logger.debug(f"[{step}] 第{attempt+1}次未找到，重试...")
                await asyncio.sleep(0.8)

        logger.warning(f"[{step}] {retries}次尝试均未找到目标")
        return False

    # ================================================================
    # 阶段 5: 组队 — 队员加入
    # ================================================================

    async def phase_team_join(self, team_code: str) -> bool:
        """队员通过口令码加入队伍"""
        self.phase = Phase.TEAM_JOIN
        logger.info("[阶段5] 队员加入队伍")

        ocr = OcrDismisser()

        # 先把口令码写入剪贴板
        await self.adb.set_clipboard(team_code)
        await asyncio.sleep(1)

        # 检测是否自动弹出"使用组队码加入"提示
        for _ in range(5):
            shot = await self.adb.screenshot()
            if shot is None:
                await asyncio.sleep(1)
                continue

            # OCR 查找"加入"或"使用组队码"
            hits = ocr._ocr_all(shot)
            for hit in hits:
                if "加入" in hit.text and "队" in hit.text:
                    logger.info(f"[阶段5] OCR检测到加入提示: '{hit.text}' → ({hit.cx},{hit.cy})")
                    await self.adb.tap(hit.cx, hit.cy)
                    await asyncio.sleep(2)
                    return True

            # 模板兜底
            join_hit = self.matcher.match_one(shot, "btn_join", threshold=0.65)
            if join_hit:
                logger.info("[阶段5] 模板检测到加入提示")
                await self.adb.tap(join_hit.cx, join_hit.cy)
                await asyncio.sleep(2)
                return True
            await asyncio.sleep(1)

        # 没自动弹出，手动走组队码路径
        logger.info("[阶段5] 未自动弹出，手动走组队码流程")

        # 1. 点击"组队"
        if not await self._ocr_tap(ocr, ["组队"], template_fallback="tab_team", step="打开组队面板"):
            return False
        await asyncio.sleep(2)

        # 2. 点击"组队码" tab
        if not await self._ocr_tap(ocr, ["组队码"], template_fallback="btn_team_code_tab", step="组队码tab"):
            return False
        await asyncio.sleep(2)

        # 3. 点击"粘贴口令"
        if not await self._ocr_tap(ocr, ["粘贴口令", "粘贴"], template_fallback="btn_paste_code", step="粘贴口令"):
            return False
        await asyncio.sleep(1)

        # 4. 点击"加入队伍"
        if not await self._ocr_tap(ocr, ["加入队伍", "加入"], template_fallback="btn_join_team", step="加入队伍"):
            return False
        await asyncio.sleep(2)

        return True

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

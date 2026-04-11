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
        """启动加速器并确认连接

        优化策略：
        1. 先检查 tun0 接口 + wget 连通性（<1秒），已连接直接跳过
        2. 没连接才启动加速器 app + 点击连接
        3. 验证用 tun0 + wget，不打开浏览器
        """
        self.phase = Phase.ACCELERATOR

        # ── 快速检查：VPN 是否已连接 ──
        if await self._check_vpn_connected():
            logger.info("[阶段0] 加速器已连接 ✓ 跳过启动")
            return True

        # ── 需要启动加速器 ──
        for retry in range(3):
            if retry > 0:
                logger.info(f"[阶段0] 第{retry+1}次重试")
                await self.adb.stop_app(ACCELERATOR_PACKAGE)
                await asyncio.sleep(1)

            if not await self._start_accelerator():
                continue

            # 等待 VPN 连接建立
            if await self._wait_vpn_connected(timeout=15):
                logger.info("[阶段0] 加速器连接成功 ✓")
                await self.adb.key_event("KEYCODE_HOME")
                await asyncio.sleep(0.3)
                return True

        logger.error("[阶段0] 加速器3次重试均失败")
        return False

    async def _check_vpn_connected(self) -> bool:
        """检查 VPN 隧道是否真正可用（~50ms）

        检测策略：读取 /proc/net/tcp 检查 VPN APP（六花加速器, UID 10062）
        是否有到后端服务器的 ESTABLISHED 连接。

        - 不需要 root、不需要 OCR、不需要打开浏览器
        - VPN APP 通过 :17500 端口维持隧道连接
        - 有活跃连接 → 隧道正常；无连接 → 隧道断了
        - /proc/net/tcp 世界可读，~50ms
        """
        loop = asyncio.get_event_loop()
        raw_adb = getattr(self.adb, '_adb', self.adb)

        # 1. 快速检查 tun0 接口是否存在
        output = await loop.run_in_executor(
            None, raw_adb._cmd, "shell", "ifconfig tun0 2>/dev/null"
        )
        if "UP" not in output:
            return False

        # 2. 检查 VPN APP 是否有活跃后端连接
        #    /proc/net/tcp 格式: idx local remote state ... uid
        #    state=01 = ESTABLISHED, UID 10062 = 六花加速器
        output = await loop.run_in_executor(
            None, raw_adb._cmd, "shell", "cat /proc/net/tcp"
        )
        established_count = 0
        for line in output.split("\n"):
            parts = line.split()
            if len(parts) >= 8 and parts[3] == "01" and parts[7] == "10062":
                established_count += 1

        if established_count == 0:
            logger.warning("[VPN] VPN APP 无活跃后端连接 → 隧道异常")
            return False

        return True

    async def _wait_vpn_connected(self, timeout: int = 15) -> bool:
        """轮询等待 VPN 连接建立并验证通过"""
        for _ in range(timeout * 2):  # 每 0.5 秒检查一次
            await asyncio.sleep(0.5)
            if await self._check_vpn_connected():
                return True
        return False

    async def _start_accelerator(self) -> bool:
        """启动加速器并等待连接成功"""
        logger.info("[阶段0] 启动加速器")
        await self.adb.start_app(ACCELERATOR_PACKAGE)

        play_click_count = 0
        for attempt in range(20):  # 最多 20 × 1.5s = 30 秒
            await asyncio.sleep(1.5)
            shot = await self.adb.screenshot()
            if shot is None:
                continue

            # 检查是否已连接（可能加速器自动连接了）
            status = self.matcher.is_accelerator_connected(shot)

            if status is True:
                logger.info(f"[阶段0] 加速器界面显示已连接 ({attempt+1}轮)")
                return True

            if status is False:
                play_click_count += 1
                if play_click_count >= 3:
                    await self.adb.key_event("KEYCODE_BACK")
                    play_click_count = 0
                    continue

                logger.info("[阶段0] 点击启动按钮")
                play_hit = self.matcher.match_one(shot, "accelerator_play")
                if play_hit:
                    await self.adb.tap(play_hit.cx, play_hit.cy)
                continue

            # 不在主界面
            await self.adb.key_event("KEYCODE_BACK")
            play_click_count = 0

        logger.error("[阶段0] 加速器启动超时")
        return False

    # ================================================================
    # 阶段 1: 启动游戏
    # ================================================================

    async def phase_launch_game(self) -> bool:
        """启动游戏并等待到大厅或弹窗阶段"""
        self.phase = Phase.LAUNCH_GAME

        # ── 启动前二次校验 VPN ──
        if not await self._check_vpn_connected():
            logger.warning("[阶段1] VPN 连通性校验失败，等待恢复...")
            if not await self._wait_vpn_connected(timeout=10):
                logger.error("[阶段1] VPN 未连接，拒绝启动游戏（防封号）")
                return False
            logger.info("[阶段1] VPN 已恢复 ✓")

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
        """队长创建队伍并获取 game scheme URL（通过二维码）

        流程：点组队 → 组队码tab → 二维码组队 → 截屏解码QR →
              curl获取game scheme → 关闭面板

        Returns:
            game scheme URL (如 "pubgmhd1106467070://?tmid:xxx,rlid:xxx,...")
            队员用 am start -d <url> 一条命令直接加入，不需要任何UI操作
        """
        self.phase = Phase.TEAM_CREATE
        logger.info("[阶段4] 队长创建队伍")

        # 禁用守卫
        if hasattr(self.adb, 'guard_enabled'):
            self.adb.guard_enabled = False

        ocr = OcrDismisser()

        # ── 步骤1: 找"组队"入口并点击 ──
        clicked = False
        for attempt in range(3):
            shot = await self.adb.screenshot()
            if shot is None:
                await asyncio.sleep(0.5)
                continue
            hits = ocr._ocr_all(shot)
            for h in hits:
                if "组队" in h.text and h.cx < 100:
                    logger.info(f"[阶段4] 点击组队 ({h.cx},{h.cy})")
                    await self.adb.tap(h.cx, h.cy)
                    clicked = True
                    break
            if clicked:
                break
            for h in hits:
                if "队友" in h.text and h.cx < 100:
                    tap_y = max(h.cy - 100, 50)
                    logger.info(f"[阶段4] 通过'找队友'定位组队 ({h.cx},{tap_y})")
                    await self.adb.tap(h.cx, tap_y)
                    clicked = True
                    break
            if clicked:
                break
            await asyncio.sleep(0.5)

        if not clicked:
            logger.warning("[阶段4] 未找到组队按钮")
            self._restore_guard()
            return None

        # ── 步骤2: 等面板出现，处理弹窗，找组队码tab ──
        for _ in range(10):
            await asyncio.sleep(0.3)
            shot = await self.adb.screenshot()
            if shot is None:
                continue
            hits = ocr._ocr_all(shot)
            all_text = " ".join(h.text for h in hits)

            if "加入队伍" in all_text and "取消" in all_text:
                for h in hits:
                    if "取消" in h.text and h.cy > 400:
                        logger.info(f"[阶段4] 弹窗出现，点击取消 ({h.cx},{h.cy})")
                        await self.adb.tap(h.cx, h.cy)
                        await asyncio.sleep(0.5)
                        break
                continue

            for h in hits:
                if "组队码" in h.text and h.cy > 600:
                    logger.info(f"[阶段4] 点击组队码tab ({h.cx},{h.cy})")
                    await self.adb.tap(h.cx, h.cy)
                    break
            else:
                continue
            break

        # ── 步骤3: 点击"二维码组队"（左侧栏QR图标） ──
        await asyncio.sleep(0.5)
        for attempt in range(5):
            shot = await self.adb.screenshot()
            if shot is None:
                await asyncio.sleep(0.5)
                continue
            hits = ocr._ocr_all(shot)
            qr_clicked = False
            for h in hits:
                if "二维码" in h.text and h.cx < 300:
                    logger.info(f"[阶段4] 点击二维码组队 ({h.cx},{h.cy})")
                    await self.adb.tap(h.cx, h.cy)
                    qr_clicked = True
                    break
            if not qr_clicked:
                # 模板兜底
                tmpl = self.matcher.match_one(shot, "btn_qr_team", threshold=0.65)
                if tmpl:
                    logger.info(f"[阶段4] 模板命中二维码组队 ({tmpl.cx},{tmpl.cy})")
                    await self.adb.tap(tmpl.cx, tmpl.cy)
                    qr_clicked = True
            if qr_clicked:
                break
            await asyncio.sleep(0.5)

        # ── 步骤4: 截屏解码 QR 码 ──
        await asyncio.sleep(1)
        qr_url = ""
        for attempt in range(5):
            shot = await self.adb.screenshot()
            if shot is None:
                await asyncio.sleep(0.5)
                continue

            # OpenCV QR 解码（放大3倍+二值化提高识别率）
            h, w = shot.shape[:2]
            crop = shot[int(h * 0.15):int(h * 0.85), int(w * 0.15):int(w * 0.8)]
            big = cv2.resize(crop, (0, 0), fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
            gray = cv2.cvtColor(big, cv2.COLOR_BGR2GRAY)
            _, thresh = cv2.threshold(gray, 128, 255, cv2.THRESH_BINARY)

            detector = cv2.QRCodeDetector()
            data, _, _ = detector.detectAndDecode(thresh)
            if data:
                qr_url = data
                logger.info(f"[阶段4] QR码解码成功: {data[:60]}...")
                break
            logger.debug(f"[阶段4] QR码解码失败，重试 {attempt+1}/5")
            await asyncio.sleep(0.5)

        if not qr_url:
            logger.error("[阶段4] 无法解码QR码")
            self._restore_guard()
            return None

        # ── 步骤5: 请求URL获取 game scheme ──
        game_scheme = ""
        try:
            import urllib.request
            loop = asyncio.get_event_loop()

            def _fetch_scheme(url: str) -> str:
                req = urllib.request.Request(url, headers={
                    "User-Agent": "Mozilla/5.0 (Linux; Android 7.1.2)"
                })
                with urllib.request.urlopen(req, timeout=5) as resp:
                    html = resp.read().decode("utf-8", errors="ignore")
                # 提取 game scheme: pubgmhd1106467070://...
                import re
                match = re.search(r'(pubgmhd\d+://[^"\']+)', html)
                return match.group(1) if match else ""

            # QR URL 可能是 http，需要跟随重定向到 https
            game_scheme = await loop.run_in_executor(None, _fetch_scheme, qr_url)
        except Exception as e:
            logger.error(f"[阶段4] 获取 game scheme 失败: {e}")

        if not game_scheme:
            logger.error("[阶段4] 未能提取 game scheme URL")
            self._restore_guard()
            return None

        logger.info(f"[阶段4] game scheme: {game_scheme}")

        # ── 步骤6: 关闭面板 ──
        for _ in range(4):
            shot = await self.adb.screenshot()
            if shot is None:
                break
            if self.matcher.match_one(shot, "lobby_start_btn", threshold=0.7):
                break
            close = self.matcher.find_dialog_close(shot)
            if close:
                logger.info(f"[阶段4] 关闭按钮 ({close.cx},{close.cy})")
                await self.adb.tap(close.cx, close.cy)
                await asyncio.sleep(0.3)
                continue
            h, w = shot.shape[:2]
            await self.adb.tap(w * 3 // 4, h // 2)
            await asyncio.sleep(0.3)

        logger.info("[阶段4] 已关闭组队面板")
        self._restore_guard()
        return game_scheme

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

    async def phase_team_join(self, game_scheme_url: str) -> bool:
        """队员通过 game scheme URL 直接加入队伍

        一条 ADB 命令直接加入，不需要任何 UI 操作。
        多队完全并行，每台模拟器各自收到独立的 ADB 命令，零冲突。

        Args:
            game_scheme_url: 游戏内部 scheme URL
                如 "pubgmhd1106467070://?tmid:xxx,rlid:xxx,t:xxx,p:2"
        """
        self.phase = Phase.TEAM_JOIN
        logger.info(f"[阶段5] 队员加入队伍 (scheme: {game_scheme_url[:50]}...)")

        raw_adb = getattr(self.adb, '_adb', self.adb)
        loop = asyncio.get_event_loop()

        # 一条命令直接加入
        output = await loop.run_in_executor(
            None, raw_adb._cmd, "shell",
            f"am start -a android.intent.action.VIEW -d '{game_scheme_url}'"
        )
        logger.info(f"[阶段5] am start 结果: {output.strip()}")

        # 等待游戏处理加入
        await asyncio.sleep(3)
        logger.info("[阶段5] 队员加入完成 ✓")
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

    async def run_full(self, game_scheme_url: str = "") -> bool:
        """
        完整流程: 启动到大厅 → 组队 → 地图设置

        Args:
            game_scheme_url: 队员需要传入队长的 game scheme URL
        """
        # 先到大厅
        if not await self.run_to_lobby():
            return False

        if self.role == "captain":
            # 队长: 创建队伍(QR码) → 地图设置
            scheme = await self.phase_team_create()
            if scheme:
                self._team_code = scheme
            await self.phase_map_setup()
            self.phase = Phase.DONE
            return True
        else:
            # 队员: game scheme 一条命令直接加入
            if not game_scheme_url:
                logger.error("队员需要 game scheme URL")
                return False
            result = await self.phase_team_join(game_scheme_url)
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

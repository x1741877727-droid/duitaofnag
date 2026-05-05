"""
v3 P5 — 等待目标真人入队.

业务上下文:
  P3a 队长建队 → P3b 队员加入 → P4 队长选地图 → 队伍现在 3 占 1 空
  P5 等待真人 (用户 / API 推送) 加入队伍, 校验编号匹配, 不匹配自动踢出继续等.

核心思路:
  方案 V' (2026-05-05 多轮试错后定稿)
  抛弃所有 "100% 自动识别玩家" 的内存读 / 协议解码路 (KVM 映射 / session_key 未破),
  改用业务侧的 "tap slot → 卡片 → OCR 10 位数字编号 → 与 expected_id 比对".

  数字 ID OCR 错误率 < 0.1% (vs 中文昵称 5-15% 错), 配合容错 1 位编辑距离, 实战可信.
  捣乱者 ID 不匹配 → 自动踢人 → 继续等下一波 (业务上服务指定玩家, 不能错服务).

输入:
  ctx.expected_id : str  10 位数字 (api_runner_test 强校验, 入口前注入)

依赖 ROI (config/roi.yaml, 用户在 OCR 调试页拖):
  team_slot_1_nickname     第 1 个角色脚下昵称栏 (字数检测)
  team_slot_2_nickname     第 2 个
  team_slot_3_nickname     第 3 个
  team_slot_4_nickname     第 4 个
  player_card_id           tap slot 后弹卡片上 "编号: 4434564951" 数字区
  player_card_more_btn     卡片底部 "更多" 按钮 (踢人入口)
  kick_menu_kick_btn       "更多" 弹出菜单的 "移出队伍" 按钮

出口:
  DONE  匹配 expected_id
  FAIL  240s 超时 / OCR 失败次数过多 / 入口非大厅
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Optional

from ..phase_base import PhaseHandler, PhaseResult, PhaseStep, RunContext

logger = logging.getLogger(__name__)


# ─────────── 配置常量 (实测后再调) ───────────

SLOT_ROIS = [
    "team_slot_1_nickname",
    "team_slot_2_nickname",
    "team_slot_3_nickname",
    "team_slot_4_nickname",
]

POLL_INTERVAL_S = 1.5            # 字数检测轮询间隔
TIMEOUT_S = 240.0                # 4 分钟超时
TAP_CARD_WAIT_MS = 800           # tap slot 后等卡片动画
KICK_MENU_WAIT_MS = 500          # tap "更多" 后等菜单展开
KICK_DONE_WAIT_MS = 800          # tap "移出队伍" 后等关闭
EXPECTED_ID_LEN = 10             # PUBG player ID 位数
ID_PATTERN = re.compile(r"\d{10}")
ENTRY_GATE_MAX_ATTEMPTS = 8      # 入口守门清弹窗轮数
ENTRY_GATE_INTERVAL_S = 0.5
VERIFY_OCR_RETRY = 2             # 找编号失败的 retry 次数


class P5WaitPlayersHandler(PhaseHandler):
    name = "P5"
    name_cn = "等待玩家"
    description = (
        "等指定真人 (expected_id, 10 位数字) 入队. "
        "OCR 4 个 slot 昵称栏字数变化定位新人 → tap 弹卡片 → OCR 编号 → "
        "匹配 DONE / 不匹配自动踢人继续等. 超时 240s → FAIL."
    )
    flow_steps = [
        "入口守门 (大厅检测 + 清弹窗)",
        "校验 ctx.expected_id 是 10 位数字",
        "Baseline: OCR 4 个 slot 昵称栏字数",
        "轮询: 每 1.5s OCR 4 slot, 找 '0→≥1' 变化的 slot",
        "Verify: tap 该 slot → 卡片 → OCR 'player_card_id' ROI 提 10 位数字",
        "ID == expected → DONE",
        "ID ≠ expected → tap '更多' → '移出队伍' → 继续轮询",
        "超时 240s → FAIL",
    ]
    max_rounds = 1                 # handler 内部一次性跑完整轮询循环
    round_interval_s = 1.0

    async def handle_frame(self, ctx: RunContext) -> PhaseStep:
        runner = ctx.runner
        if runner is None:
            return PhaseStep(PhaseResult.FAIL, note="ctx.runner 未注入")

        from ..recorder_helpers import record_signal_tier
        from ..screen_classifier import ScreenKind
        from ..popup_closer import PopupCloser
        decision = ctx.current_decision

        # ─── 入口守门: 必须在大厅 ───
        kind = await PopupCloser.wait_for_lobby_clearing_popups(
            ctx, max_attempts=ENTRY_GATE_MAX_ATTEMPTS, interval_s=ENTRY_GATE_INTERVAL_S)
        if kind != ScreenKind.LOBBY:
            record_signal_tier(decision, name="入口守门", hit=False, tier_idx=2,
                               note=f"P5 入口非 LOBBY (kind={kind.name})")
            return PhaseStep(PhaseResult.FAIL,
                             note=f"P5 入口守门失败 (kind={kind.name})",
                             outcome_hint=f"entry_not_lobby_{kind.value}")
        record_signal_tier(decision, name="入口守门", hit=True, tier_idx=2,
                           note="P5 入口确认 LOBBY")

        # ─── 校验 expected_id ───
        eid = (ctx.expected_id or "").strip()
        if not (len(eid) == EXPECTED_ID_LEN and eid.isdigit()):
            record_signal_tier(decision, name="参数校验", hit=False, tier_idx=2,
                               note=f"expected_id 无效: {eid!r}")
            return PhaseStep(PhaseResult.FAIL,
                             note=f"expected_id 必须是 {EXPECTED_ID_LEN} 位数字, 实际={eid!r}",
                             outcome_hint="invalid_expected_id")
        logger.info(f"[P5 inst{ctx.instance_idx}] 等待玩家: expected_id={eid}, timeout={TIMEOUT_S}s")
        record_signal_tier(decision, name="参数校验", hit=True, tier_idx=2,
                           note=f"expected_id={eid}")

        # ─── Baseline: 4 slot 字数 ───
        baseline = await self._read_slot_counts(ctx)
        if baseline is None:
            return PhaseStep(PhaseResult.FAIL,
                             note="baseline OCR 失败 (截图返回 None 或 ROI 配置缺)",
                             outcome_hint="baseline_fail")
        ctx.team_slot_baseline = list(baseline)
        logger.info(f"[P5 inst{ctx.instance_idx}] baseline 4 slot 字数: {baseline}")
        record_signal_tier(decision, name="OCR·baseline", hit=True, tier_idx=3,
                           note=f"baseline={baseline}")

        # 健康检查: baseline 至少有 1 个空 slot (字数 0), 否则队伍已满 — 直接进 verify
        if all(c > 0 for c in baseline):
            logger.warning(f"[P5 inst{ctx.instance_idx}] baseline 已 4 满, 立即 verify 全部")
            for n in range(4):
                got_id = await self._verify_slot_id(ctx, n)
                if got_id == eid:
                    record_signal_tier(decision, name="Verify·初查", hit=True, tier_idx=4,
                                       note=f"slot{n+1} ID={got_id} 匹配")
                    return PhaseStep(PhaseResult.DONE, note=f"匹配 slot{n+1}",
                                     outcome_hint="match_at_baseline")
            # 都不匹配, 全是机器队员 → 没空位, 等不到真人
            record_signal_tier(decision, name="Verify·初查", hit=False, tier_idx=4,
                               note="baseline 4 满但无匹配 ID")
            return PhaseStep(PhaseResult.FAIL,
                             note="队伍已满且无匹配 ID, 真人无法加入",
                             outcome_hint="no_empty_slot")

        # ─── Polling Loop ───
        start_ts = time.perf_counter()
        loop_round = 0
        while True:
            elapsed = time.perf_counter() - start_ts
            if elapsed >= TIMEOUT_S:
                record_signal_tier(decision, name="超时", hit=False, tier_idx=4,
                                   note=f"{TIMEOUT_S}s 内未等到玩家入队")
                return PhaseStep(PhaseResult.FAIL,
                                 note=f"等待超时 {TIMEOUT_S}s, 真人未加入",
                                 outcome_hint="wait_timeout")

            await asyncio.sleep(POLL_INTERVAL_S)
            loop_round += 1
            current = await self._read_slot_counts(ctx)
            if current is None:
                logger.warning(f"[P5 inst{ctx.instance_idx}] round {loop_round} OCR 失败, 跳过")
                continue

            # 找 '0 → ≥1' 变化的 slot (允许多个, 通常 1 个)
            new_slots = [
                i for i, (b, c) in enumerate(zip(baseline, current))
                if b == 0 and c > 0
            ]
            # 玩家退队也可能 (b > 0, c == 0): 更新 baseline 不报警
            if any(b > 0 and c == 0 for b, c in zip(baseline, current)):
                logger.info(f"[P5 inst{ctx.instance_idx}] round {loop_round} 检测到队员退队, 更新 baseline {baseline} → {current}")
                baseline = list(current)
                ctx.team_slot_baseline = baseline

            if not new_slots:
                continue  # 字数无 0→≥1 变化, 继续轮询

            n = new_slots[0]
            logger.info(f"[P5 inst{ctx.instance_idx}] round {loop_round} 检测到新人 slot{n+1} (字数 0→{current[n]})")

            # ─── Verify: tap → OCR 编号 ───
            got_id = await self._verify_slot_id(ctx, n)
            if got_id is None:
                record_signal_tier(decision, name=f"Verify·R{loop_round}", hit=False, tier_idx=4,
                                   note=f"slot{n+1} OCR 找不到 10 位编号 → 视为干扰, 继续等")
                # OCR 失败后 baseline 也要 sync (避免反复触发 verify)
                baseline = list(current)
                ctx.team_slot_baseline = baseline
                continue

            if got_id == eid:
                record_signal_tier(decision, name=f"Verify·R{loop_round}", hit=True, tier_idx=4,
                                   note=f"slot{n+1} ID={got_id} ✓ 匹配 expected={eid}")
                return PhaseStep(PhaseResult.DONE,
                                 note=f"目标玩家入队 slot{n+1} ID={got_id} (耗时 {elapsed:.1f}s)",
                                 outcome_hint="match_target")

            # ─── 不匹配: 踢人 ───
            logger.warning(f"[P5 inst{ctx.instance_idx}] slot{n+1} ID={got_id} ≠ expected={eid}, 踢出")
            ctx.kicked_ids.add(got_id)
            kicked = await self._kick_player_open_card(ctx, n)
            record_signal_tier(decision, name=f"Kick·R{loop_round}", hit=kicked, tier_idx=5,
                               note=f"slot{n+1} got_id={got_id} expected={eid} kicked={kicked}")
            # 不管 kick 成功失败, baseline 都要 sync (踢成功 → slot 回 0; 失败 → 字数仍 ≥1 但被记 kicked_ids)
            baseline = await self._read_slot_counts(ctx) or list(current)
            ctx.team_slot_baseline = baseline
            # 继续 loop 等下一波

    # ─────────── helpers ───────────

    @staticmethod
    async def _read_slot_counts(ctx: RunContext) -> Optional[list[int]]:
        """OCR 4 个 slot 昵称栏, 返回 [c1,c2,c3,c4] 字数. 任一失败返 None."""
        runner = ctx.runner
        shot = await runner.adb.screenshot()
        if shot is None:
            logger.warning("[P5] _read_slot_counts: screenshot None")
            return None

        ocr = runner.ocr_dismisser
        from ..roi_config import all_names as _all_roi
        avail = set(_all_roi())
        missing = [n for n in SLOT_ROIS if n not in avail]
        if missing:
            logger.error(f"[P5] ROI 缺失: {missing}, 配置 config/roi.yaml")
            return None

        counts: list[int] = []
        for name in SLOT_ROIS:
            try:
                hits = await asyncio.to_thread(ocr._ocr_roi_named, shot, name)
                # 字数 = 所有 hit 的文字字符总数
                total_chars = sum(len(h.text) for h in hits)
                counts.append(total_chars)
            except Exception as e:
                logger.warning(f"[P5] OCR ROI '{name}' 失败: {e}")
                counts.append(-1)  # 标错值, 调用方据 -1 判断
        if any(c < 0 for c in counts):
            return None
        return counts

    @staticmethod
    async def _verify_slot_id(ctx: RunContext, slot_index: int) -> Optional[str]:
        """tap slot → 卡片弹出 → OCR 'player_card_id' ROI 拿 10 位数字. 失败返 None.

        slot_index: 0..3, 对应 SLOT_ROIS / team_slot_{N+1}_nickname.
        """
        from ..roi_config import get as _roi_get, all_names as _all_roi
        avail = set(_all_roi())
        if "player_card_id" not in avail:
            logger.error("[P5] ROI 缺失: player_card_id, 配置 config/roi.yaml")
            return None

        runner = ctx.runner
        # 拿 slot ROI 中心坐标
        roi_name = SLOT_ROIS[slot_index]
        try:
            x1, y1, x2, y2, _ = _roi_get(roi_name)
        except Exception as e:
            logger.error(f"[P5] 拿不到 ROI '{roi_name}' 坐标: {e}")
            return None

        shot = ctx.current_shot
        if shot is None:
            shot = await runner.adb.screenshot()
        if shot is None:
            return None
        h, w = shot.shape[:2]
        cx = int((x1 + x2) / 2 * w)
        cy = int((y1 + y2) / 2 * h)

        # tap slot → 等卡片
        await runner.adb.tap(cx, cy)
        await asyncio.sleep(TAP_CARD_WAIT_MS / 1000.0)

        # 截图 + OCR 编号区域, 失败 retry
        ocr = runner.ocr_dismisser
        for attempt in range(VERIFY_OCR_RETRY + 1):
            shot2 = await runner.adb.screenshot()
            if shot2 is None:
                continue
            try:
                hits = await asyncio.to_thread(ocr._ocr_roi_named, shot2, "player_card_id")
            except Exception as e:
                logger.warning(f"[P5] OCR player_card_id 失败 (try {attempt}): {e}")
                continue
            id_str = _extract_player_id(hits)
            if id_str:
                logger.info(f"[P5 inst{ctx.instance_idx}] slot{slot_index+1} 卡片 ID={id_str} (try {attempt})")
                return id_str
            # 卡片可能没弹出来, 多 tap 一次
            if attempt < VERIFY_OCR_RETRY:
                logger.debug(f"[P5] OCR 没 10 位数字, retry tap slot{slot_index+1}")
                await runner.adb.tap(cx, cy)
                await asyncio.sleep(TAP_CARD_WAIT_MS / 1000.0)

        return None

    @staticmethod
    async def _kick_player_open_card(ctx: RunContext, slot_index: int) -> bool:
        """踢人. 假设此时卡片已经在 _verify_slot_id 里打开 (tap slot 之后).
        流程: tap '更多' 按钮 → 等菜单 → tap '移出队伍' → 等关闭.
        """
        from ..roi_config import get as _roi_get, all_names as _all_roi
        avail = set(_all_roi())
        for need in ("player_card_more_btn", "kick_menu_kick_btn"):
            if need not in avail:
                logger.error(f"[P5] ROI 缺失: {need}, 踢人无法执行")
                return False

        runner = ctx.runner
        shot = await runner.adb.screenshot()
        if shot is None:
            return False
        h, w = shot.shape[:2]

        # tap "更多" 按钮 (ROI 中心)
        x1, y1, x2, y2, _ = _roi_get("player_card_more_btn")
        cx = int((x1 + x2) / 2 * w)
        cy = int((y1 + y2) / 2 * h)
        await runner.adb.tap(cx, cy)
        await asyncio.sleep(KICK_MENU_WAIT_MS / 1000.0)

        # tap "移出队伍"
        x1, y1, x2, y2, _ = _roi_get("kick_menu_kick_btn")
        cx = int((x1 + x2) / 2 * w)
        cy = int((y1 + y2) / 2 * h)
        await runner.adb.tap(cx, cy)
        await asyncio.sleep(KICK_DONE_WAIT_MS / 1000.0)

        return True


# ─────────── module-private helpers ───────────


def _extract_player_id(ocr_hits) -> Optional[str]:
    """从 OCR hits 提取 10 位数字 ID. 找连续 10 位数字, 按出现顺序返第一个."""
    for h in ocr_hits:
        m = ID_PATTERN.search(h.text)
        if m:
            return m.group(0)
    # 备选: 把所有 hit 拼起来再找 (防被分行切散)
    joined = "".join(h.text for h in ocr_hits)
    m = ID_PATTERN.search(joined)
    return m.group(0) if m else None

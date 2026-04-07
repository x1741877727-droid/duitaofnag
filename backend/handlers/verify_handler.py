"""
校验处理器
1. 玩家 ID 校验：点开玩家主页 → OCR 读 ID → 比对
2. 对手校验：匹配后识别对手队伍信息 → 判断是否为目标
"""

import asyncio
from typing import Optional

from .base import BaseHandler, HandlerResult


class VerifyPlayersHandler(BaseHandler):
    """
    VERIFY_PLAYERS 状态处理器
    逐个点开队伍中玩家主页，OCR 读取 ID，与预设 ID 列表比对
    """

    def __init__(self, *args, expected_player_ids: Optional[list[str]] = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.expected_ids = expected_player_ids or []

    async def execute(self) -> HandlerResult:
        if not self.expected_ids:
            self.log("无需校验玩家（未配置预设 ID）")
            return HandlerResult(trigger="players_verified")

        self.log(f"开始校验玩家 ID，预期: {self.expected_ids}")

        img = await self.take_screenshot()
        if img is None:
            return HandlerResult(trigger="unknown_error", error="截图失败")

        verified_ids = []
        unmatched_ids = []

        # 逐个校验队伍中的玩家
        # 需要找到队伍中玩家头像/位置，依次点击查看主页
        # 这里用 LLM 辅助找到各玩家的点击位置
        question = (
            "这是游戏组队界面截图。请找出队伍中每个玩家的头像或名字位置。"
            "回复 JSON: {\"players\": [{\"name\": \"名字\", \"x\": x, \"y\": y}, ...]}"
        )
        result = await self.pipeline.llm.analyze(img, "custom", question)

        if not result.success or not result.parsed:
            self.log("LLM 未能识别玩家位置，跳过校验", "warn")
            return HandlerResult(trigger="players_verified")

        players = result.parsed.get("players", [])

        for player in players:
            px, py = player.get("x", 0), player.get("y", 0)
            if not px or not py:
                continue

            # 点击玩家头像打开主页
            await self.tap(px, py)
            await asyncio.sleep(1.0)

            # 截图读取 ID
            profile_img = await self.take_screenshot()
            if profile_img is None:
                continue

            # OCR 读 ID
            verify_result = await self.pipeline.verify_player_id(
                profile_img, ""  # 先读出 ID
            )

            read_id = verify_result.data.get("read_id", "")
            if read_id:
                if any(eid in read_id or read_id in eid for eid in self.expected_ids):
                    verified_ids.append(read_id)
                    self.log(f"玩家 ID 匹配: {read_id}")
                else:
                    unmatched_ids.append(read_id)
                    self.log(f"玩家 ID 不匹配: {read_id}", "warn")

            # 关闭主页（返回）
            await self.ctrl.key_event(4)  # BACK
            await asyncio.sleep(0.5)

        if unmatched_ids:
            self.log(f"存在不匹配的玩家: {unmatched_ids}", "warn")
            return HandlerResult(
                trigger="unknown_error",
                error=f"玩家 ID 不匹配: {unmatched_ids}",
                data={"verified": verified_ids, "unmatched": unmatched_ids},
            )

        self.log(f"所有玩家校验通过: {verified_ids}")
        return HandlerResult(
            trigger="players_verified",
            data={"verified": verified_ids},
        )


class VerifyOpponentHandler(BaseHandler):
    """
    VERIFY_OPPONENT 状态处理器
    匹配成功后，识别对手队伍信息
    判断对手是否为另一组
    """

    def __init__(self, *args, target_opponent_ids: Optional[list[str]] = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.target_ids = target_opponent_ids or []

    async def execute(self) -> HandlerResult:
        self.log("校验对手队伍...")

        img = await self.take_screenshot()
        if img is None:
            return HandlerResult(trigger="unknown_error", error="截图失败")

        # 1. 尝试 OCR 读取对手信息
        ocr_resp = self.pipeline.ocr.recognize(img)
        opponent_names_ocr = []
        if ocr_resp.results:
            for r in ocr_resp.results:
                # 简单过滤：包含在目标 ID 中的文字
                for tid in self.target_ids:
                    if tid in r.text or r.text in tid:
                        opponent_names_ocr.append(r.text)

        if opponent_names_ocr:
            self.log(f"OCR 识别到目标对手: {opponent_names_ocr}")
            return HandlerResult(
                trigger="opponent_correct",
                data={"matched_names": opponent_names_ocr, "method": "ocr"},
            )

        # 2. LLM 读取对手信息
        opponents_info = await self.pipeline.llm.read_opponents(img)
        opponent_names = opponents_info.get("opponents", [])

        if not opponent_names:
            self.log("无法识别对手信息", "warn")
            # 无法判断，保守 abort
            return HandlerResult(
                trigger="opponent_wrong",
                data={"reason": "无法识别对手"},
            )

        # 对比对手名单与目标
        matched = []
        for name in opponent_names:
            for tid in self.target_ids:
                if tid in name or name in tid:
                    matched.append(name)
                    break

        # 判定：至少匹配到一半目标就认为是对的
        threshold = max(1, len(self.target_ids) // 2)
        if len(matched) >= threshold:
            self.log(f"对手匹配成功: {matched}")
            return HandlerResult(
                trigger="opponent_correct",
                data={"matched_names": matched, "all_opponents": opponent_names, "method": "llm"},
            )
        else:
            self.log(f"对手不匹配: found={opponent_names}, matched={matched}")
            return HandlerResult(
                trigger="opponent_wrong",
                data={"all_opponents": opponent_names, "matched": matched, "reason": "对手不一致"},
            )

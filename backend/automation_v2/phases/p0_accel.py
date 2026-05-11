"""P0 — 加速器校验.

业务: 1 个 HTTP GET 检查 gameproxy /api/tun/state, ok → NEXT, fail → FAIL.

骨架版本: HTTP probe 接口留给业务接入 (URL/字段名根据实际配置).
"""
from __future__ import annotations

import asyncio
import json
import logging
import urllib.request
import urllib.error

from ..ctx import RunContext
from ..phase_base import PhaseStep, PhaseResult, step_next, step_fail

logger = logging.getLogger(__name__)

# 默认 gameproxy /api/tun/state (跟 v1 一致); 业务可以改 env 覆盖
DEFAULT_TUN_URL = "http://127.0.0.1:9901/api/tun/state"


class P0Accel:
    name = "P0"
    max_seconds = 5.0
    round_interval_s = 0.5

    def __init__(self, tun_url: str = DEFAULT_TUN_URL):
        self.tun_url = tun_url

    async def enter(self, ctx: RunContext) -> None:
        ctx.reset_phase_state()

    async def handle_frame(self, ctx: RunContext) -> PhaseStep:
        # P0 不依赖 yolo / shot, 时间戳填空段
        ctx.mark("yolo_start")
        ctx.mark("yolo_done")

        cur = await asyncio.to_thread(self._probe)
        ctx.mark("decide")

        if cur is None:
            return step_fail(note=f"probe {self.tun_url} 不可达")

        ok = bool(
            cur.get("ok")
            and (cur.get("uptime_seconds", 0) > 0 or cur.get("mode") == "tun")
        )
        if ok:
            return step_next(note=f"tun ok uptime={cur.get('uptime_seconds', 0)}s")
        return step_fail(note=f"tun not ready: {cur}")

    def _probe(self) -> dict | None:
        try:
            with urllib.request.urlopen(self.tun_url, timeout=2) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as e:
            logger.debug(f"[P0] probe url err: {e}")
            return None
        except Exception as e:
            logger.debug(f"[P0] probe err: {e}")
            return None

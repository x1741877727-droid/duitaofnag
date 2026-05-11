"""
v3 P0 — 加速器校验.

校验宿主机 gameproxy.exe 状态 (127.0.0.1:9901/api/tun/state):
通过 → NEXT, 不通过 → FAIL.

历史: 老 APK 4-信号 + 广播 + UI 兜底流程已退役 (2026-05-09 cleanup),
不再支持模拟器内 VPN; 流量必须走宿主机 wintun TUN 通道.
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
import time
import urllib.request

from ..phase_base import PhaseHandler, PhaseResult, PhaseStep, RunContext
from ..recorder_helpers import record_signal_tier

logger = logging.getLogger(__name__)


class P0AcceleratorHandler(PhaseHandler):
    """加速器校验 — 仅 TUN (宿主机 gameproxy + wintun 路由)."""

    name = "P0"
    name_cn = "加速器校验"
    description = "校验宿主 gameproxy.exe :9901 健康 + mode=tun + uptime>0."
    flow_steps = [
        "GET 127.0.0.1:9901/api/tun/state → ok+uptime>0 → NEXT",
        "未就绪 → FAIL",
    ]
    max_rounds = 1
    round_interval_s = 0.5

    async def handle_frame(self, ctx: RunContext) -> PhaseStep:
        runner = ctx.runner
        if runner is None:
            return PhaseStep(PhaseResult.FAIL, note="ctx.runner 未注入")
        return await self._handle_tun(ctx)

    async def _handle_tun(self, ctx: RunContext) -> PhaseStep:
        decision = ctx.current_decision
        t0 = time.perf_counter()
        loop = asyncio.get_event_loop()

        def _probe():
            try:
                with urllib.request.urlopen(
                    "http://127.0.0.1:9901/api/tun/state", timeout=2
                ) as resp:
                    return _json.loads(resp.read().decode("utf-8"))
            except Exception as e:
                return {"error": str(e)}

        cur = await loop.run_in_executor(None, _probe)
        ok = bool(
            cur.get("ok")
            and (cur.get("uptime_seconds", 0) > 0 or cur.get("mode") == "tun")
        )
        ms = (time.perf_counter() - t0) * 1000

        if ok:
            record_signal_tier(decision, name="TUN校验", hit=True,
                               note=f"宿主 gameproxy uptime={cur.get('uptime_seconds')}s",
                               duration_ms=ms)
            logger.info(f"[P0/tun] 宿主 gameproxy 就绪 ✓ uptime={cur.get('uptime_seconds')}s")
            return PhaseStep(PhaseResult.NEXT, note="tun host ok",
                             outcome_hint="tun_host_ok")

        record_signal_tier(decision, name="TUN校验", hit=False,
                           note=f"宿主 gameproxy 异常: {cur}", duration_ms=ms)
        logger.error(f"[P0/tun] 宿主 gameproxy 未就绪: {cur}")
        return PhaseStep(PhaseResult.FAIL, note="tun host not ready",
                         outcome_hint="tun_host_down")

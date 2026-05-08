"""
v3 P0 — 加速器校验.

mode=tun (默认): 校验宿主机 gameproxy.exe 状态 (127.0.0.1:9901/api/tun/state).
  通过 → NEXT, 不通过 → FAIL. 不再拉 vpn-app UI / 不再走 4 信号模拟器内检测.

mode=apk (legacy): 老的 4 信号 + 广播启动 + UI 兜底流程, 兼容尚未迁 TUN 的 account.
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
import time
import urllib.request

from ..phase_base import PhaseAction, PhaseHandler, PhaseResult, PhaseStep, RunContext
from ..recorder_helpers import record_signal_tier

logger = logging.getLogger(__name__)


class P0AcceleratorHandler(PhaseHandler):
    """加速器校验 — TUN 走宿主机, APK 走模拟器内 VPN."""

    name = "P0"
    name_cn = "加速器校验"
    description = "TUN: 校验宿主机 gameproxy. APK: 4 信号 + 广播 + UI 兜底."
    flow_steps = [
        "TUN: GET 127.0.0.1:9901/api/tun/state → ok+uptime>0 → NEXT",
        "APK 快速路径: 4 信号已通过 → 直接 NEXT",
        "APK 广播启动: am broadcast START → 等 8s 内连上",
        "APK UI 启动: 拉 FightMaster 界面 + 点连接 (3 次重试)",
        "全失败 → FAIL",
    ]
    max_rounds = 1
    round_interval_s = 0.5

    async def handle_frame(self, ctx: RunContext) -> PhaseStep:
        runner = ctx.runner
        if runner is None:
            return PhaseStep(PhaseResult.FAIL, note="ctx.runner 未注入")

        mode = "apk"
        try:
            mode = runner._resolve_accel_mode()
        except Exception as e:
            logger.warning(f"[P0] _resolve_accel_mode 异常, 退回 apk: {e}")

        if mode == "tun":
            return await self._handle_tun(ctx)
        return await self._handle_apk(ctx)

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

    async def _handle_apk(self, ctx: RunContext) -> PhaseStep:
        runner = ctx.runner
        decision = ctx.current_decision

        # ── 快速路径: VPN 已连接 ──
        t0 = time.perf_counter()
        if await runner._check_vpn_connected():
            record_signal_tier(decision, name="VPN校验", hit=True,
                               note="VPN 已连接 (4 信号校验通过, 跳过启动)",
                               duration_ms=(time.perf_counter() - t0) * 1000)
            logger.info("[P0/apk] FightMaster 已连接 ✓ 跳过启动")
            return PhaseStep(PhaseResult.NEXT, note="vpn already connected",
                             outcome_hint="vpn_already_connected")

        # ── 广播启动 (1-8s 快路径) ──
        try:
            await runner._start_vpn()
        except Exception as e:
            logger.warning(f"[P0/apk] _start_vpn 异常: {e}")
        if await runner._wait_vpn_connected(timeout=8):
            record_signal_tier(decision, name="VPN校验", hit=True,
                               note="广播启动 → 8s 内连上",
                               duration_ms=(time.perf_counter() - t0) * 1000)
            logger.info("[P0/apk] FightMaster 广播启动成功 ✓")
            return PhaseStep(PhaseResult.NEXT, note="vpn broadcast ok",
                             outcome_hint="vpn_broadcast_ok")

        # ── UI 启动 (3 次重试) ──
        logger.warning("[P0/apk] 广播启动失败, 切换 UI 模式")
        for retry in range(3):
            if retry > 0:
                logger.info(f"[P0/apk] UI 模式第 {retry + 1} 次重试")
                try:
                    await runner._stop_vpn()
                except Exception:
                    pass
                await asyncio.sleep(2)

            try:
                await runner._start_vpn_via_ui()
            except Exception as e:
                logger.warning(f"[P0/apk] _start_vpn_via_ui 异常: {e}")
                continue

            if await runner._wait_vpn_connected(timeout=10):
                record_signal_tier(decision, name="VPN校验", hit=True,
                                   note=f"UI 启动 → 连接 (retry={retry})",
                                   duration_ms=(time.perf_counter() - t0) * 1000)
                logger.info(f"[P0/apk] FightMaster UI 启动成功 ✓ (retry={retry})")
                return PhaseStep(PhaseResult.NEXT, note=f"vpn ui ok (retry={retry})",
                                 outcome_hint="vpn_ui_ok")

        record_signal_tier(decision, name="VPN校验", hit=False,
                           note="广播 + 3 次 UI 启动全失败",
                           duration_ms=(time.perf_counter() - t0) * 1000)
        logger.error("[P0/apk] 所有方式均失败")
        return PhaseStep(PhaseResult.FAIL, note="vpn 全部失败",
                         outcome_hint="vpn_all_failed")

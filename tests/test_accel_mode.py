"""
验证 Step 2 round 5a: accel_mode 字段 + _start_vpn mode 分支.

跑法:
    python -X utf8 tests/test_accel_mode.py
    pytest tests/test_accel_mode.py -v

oracle:
  - AccountConfig.accel_mode 默认 None (=> 用全局 default)
  - Settings.accelerator_default_mode 默认 "apk" => 向后兼容
  - Settings.accelerator_master_disable_tun 默认 False
  - _start_vpn mode=apk: 跟 Step 1.2 行为完全一致 (调 ADB 广播)
  - _start_vpn mode=tun: 不调 ADB 广播 (跳过 vpn-app 启动)
  - master_disable_tun=True: 强制 apk 行为, 即使 default_mode=tun
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from backend.automation.single_runner import SingleInstanceRunner
from backend.config import AccountConfig, Settings, config


def _make_runner_skipping_init():
    runner = SingleInstanceRunner.__new__(SingleInstanceRunner)
    adb = MagicMock()
    adb._adb = MagicMock()
    adb._adb._cmd = MagicMock(return_value="")
    runner.adb = adb
    runner.account = None
    return runner


def _save_settings():
    return (
        getattr(config.settings, "accelerator_proxy_host", ""),
        getattr(config.settings, "accelerator_default_mode", "apk"),
        getattr(config.settings, "accelerator_master_disable_tun", False),
    )


def _restore_settings(saved):
    config.settings.accelerator_proxy_host = saved[0]
    config.settings.accelerator_default_mode = saved[1]
    config.settings.accelerator_master_disable_tun = saved[2]


# ─────────── schema 字段 ───────────


def test_account_config_default_accel_mode_none():
    a = AccountConfig(qq="1", nickname="x", game_id="100", group="A",
                      role="captain", instance_index=0)
    assert a.accel_mode is None
    print("PASS test_account_config_default_accel_mode_none")


def test_account_config_can_set_accel_mode():
    a = AccountConfig(qq="1", nickname="x", game_id="100", group="A",
                      role="captain", instance_index=0, accel_mode="tun")
    assert a.accel_mode == "tun"
    print("PASS test_account_config_can_set_accel_mode")


def test_settings_default_mode_apk():
    s = Settings()
    assert s.accelerator_default_mode == "apk"
    assert s.accelerator_master_disable_tun is False
    print("PASS test_settings_default_mode_apk")


def test_account_load_backward_compat(tmp_path=None):
    """旧 accounts.json 没 accel_mode 字段, 加载后 None"""
    items = [{"qq": "1", "nickname": "x", "game_id": "100",
              "group": "A", "role": "captain", "instance_index": 0}]
    accounts = [AccountConfig(**item) for item in items]
    assert accounts[0].accel_mode is None
    print("PASS test_account_load_backward_compat")


# ─────────── _start_vpn 行为 ───────────


def test_start_vpn_default_mode_apk_broadcasts():
    """default_mode=apk + 没 account override → 广播 ADB"""
    saved = _save_settings()
    try:
        config.settings.accelerator_default_mode = "apk"
        config.settings.accelerator_master_disable_tun = False
        runner = _make_runner_skipping_init()
        asyncio.run(runner._start_vpn())
        cmd = runner.adb._adb._cmd.call_args.args[1]
        assert "am broadcast -a com.fightmaster.vpn.START" in cmd
        print("PASS test_start_vpn_default_mode_apk_broadcasts")
    finally:
        _restore_settings(saved)


def test_start_vpn_default_mode_tun_skips_broadcast():
    """default_mode=tun → 不广播 ADB (脱 APK)"""
    saved = _save_settings()
    try:
        config.settings.accelerator_default_mode = "tun"
        config.settings.accelerator_master_disable_tun = False
        runner = _make_runner_skipping_init()
        asyncio.run(runner._start_vpn())
        # _cmd 应该完全没被调
        assert runner.adb._adb._cmd.call_count == 0, \
            f"tun 模式不应调 ADB, 但 _cmd 被调了 {runner.adb._adb._cmd.call_count} 次"
        print("PASS test_start_vpn_default_mode_tun_skips_broadcast")
    finally:
        _restore_settings(saved)


def test_start_vpn_master_disable_tun_forces_apk():
    """master_disable_tun=True 紧急回滚: 即使 default_mode=tun 也走 apk"""
    saved = _save_settings()
    try:
        config.settings.accelerator_default_mode = "tun"
        config.settings.accelerator_master_disable_tun = True  # kill switch
        runner = _make_runner_skipping_init()
        asyncio.run(runner._start_vpn())
        cmd = runner.adb._adb._cmd.call_args.args[1]
        assert "am broadcast -a com.fightmaster.vpn.START" in cmd, \
            "master_disable_tun 时必须强制 apk 广播"
        print("PASS test_start_vpn_master_disable_tun_forces_apk")
    finally:
        _restore_settings(saved)


def test_start_vpn_per_account_tun_overrides_global_apk():
    """account.accel_mode=tun 覆盖 default=apk"""
    saved = _save_settings()
    try:
        config.settings.accelerator_default_mode = "apk"
        config.settings.accelerator_master_disable_tun = False
        runner = _make_runner_skipping_init()
        runner.account = AccountConfig(
            qq="1", nickname="x", game_id="100", group="A",
            role="captain", instance_index=0, accel_mode="tun"
        )
        asyncio.run(runner._start_vpn())
        assert runner.adb._adb._cmd.call_count == 0, \
            "per-account tun override 应跳过 ADB 广播"
        print("PASS test_start_vpn_per_account_tun_overrides_global_apk")
    finally:
        _restore_settings(saved)


def test_start_vpn_per_account_apk_overrides_global_tun():
    """account.accel_mode=apk 覆盖 default=tun (灰度回滚单台)"""
    saved = _save_settings()
    try:
        config.settings.accelerator_default_mode = "tun"
        config.settings.accelerator_master_disable_tun = False
        runner = _make_runner_skipping_init()
        runner.account = AccountConfig(
            qq="1", nickname="x", game_id="100", group="A",
            role="captain", instance_index=0, accel_mode="apk"
        )
        asyncio.run(runner._start_vpn())
        cmd = runner.adb._adb._cmd.call_args.args[1]
        assert "am broadcast" in cmd, "per-account apk override 应走广播"
        print("PASS test_start_vpn_per_account_apk_overrides_global_tun")
    finally:
        _restore_settings(saved)


if __name__ == "__main__":
    test_account_config_default_accel_mode_none()
    test_account_config_can_set_accel_mode()
    test_settings_default_mode_apk()
    test_account_load_backward_compat()
    test_start_vpn_default_mode_apk_broadcasts()
    test_start_vpn_default_mode_tun_skips_broadcast()
    test_start_vpn_master_disable_tun_forces_apk()
    test_start_vpn_per_account_tun_overrides_global_apk()
    test_start_vpn_per_account_apk_overrides_global_tun()
    print("\nALL PASS")

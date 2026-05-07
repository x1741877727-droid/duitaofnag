"""
验证 _start_vpn 是否按 settings.accelerator_proxy_host 配置广播 extras.

跑法:
    python -X utf8 tests/test_accelerator_extras.py
    pytest tests/test_accelerator_extras.py -v

Step 1.2 改造的 oracle: 默认空 host -> 跟现行为一致 (无 extras);
配上 host -> ADB 广播带 --es proxy_host '<host>' --ei proxy_port <port>.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from backend.automation.single_runner import SingleInstanceRunner
from backend.config import config


def _make_runner_skipping_init():
    """绕过 __init__ 直接造 instance, _start_vpn 只用到 self.adb."""
    runner = SingleInstanceRunner.__new__(SingleInstanceRunner)
    adb = MagicMock()
    adb._adb = MagicMock()
    adb._adb._cmd = MagicMock(return_value="")
    runner.adb = adb
    return runner


def _last_shell_cmd(runner) -> str:
    """从 mock 拿最后一次 _cmd("shell", <cmd>) 的 <cmd>."""
    call = runner.adb._adb._cmd.call_args
    args = call.args
    assert args[0] == "shell", f"expected first arg 'shell', got {args[0]!r}"
    return args[1]


def _save_proxy_settings():
    return (
        getattr(config.settings, "accelerator_proxy_host", ""),
        getattr(config.settings, "accelerator_proxy_port", 9900),
    )


def _restore_proxy_settings(saved):
    if hasattr(config.settings, "accelerator_proxy_host"):
        config.settings.accelerator_proxy_host = saved[0]
    if hasattr(config.settings, "accelerator_proxy_port"):
        config.settings.accelerator_proxy_port = saved[1]


def test_default_no_extras_keeps_legacy_behavior():
    saved = _save_proxy_settings()
    try:
        if hasattr(config.settings, "accelerator_proxy_host"):
            config.settings.accelerator_proxy_host = ""
        runner = _make_runner_skipping_init()
        asyncio.run(runner._start_vpn())
        cmd = _last_shell_cmd(runner)
        assert "--es proxy_host" not in cmd, (
            f"empty host should NOT add extras (legacy compat); got: {cmd!r}"
        )
        assert "am broadcast -a com.fightmaster.vpn.START" in cmd
        assert "-n com.fightmaster.vpn/.CommandReceiver" in cmd
        print("PASS test_default_no_extras_keeps_legacy_behavior")
    finally:
        _restore_proxy_settings(saved)


def test_with_proxy_host_passes_extras():
    saved = _save_proxy_settings()
    try:
        config.settings.accelerator_proxy_host = "192.168.56.1"
        config.settings.accelerator_proxy_port = 9900
        runner = _make_runner_skipping_init()
        asyncio.run(runner._start_vpn())
        cmd = _last_shell_cmd(runner)
        assert "--es proxy_host" in cmd, f"expected extras in cmd: {cmd!r}"
        assert "192.168.56.1" in cmd
        assert "--ei proxy_port 9900" in cmd
        print("PASS test_with_proxy_host_passes_extras")
    finally:
        _restore_proxy_settings(saved)


def test_shell_injection_safe():
    """恶意 host 必须被 shell-quote 包住, 不能逃出."""
    saved = _save_proxy_settings()
    try:
        config.settings.accelerator_proxy_host = "1.2.3.4; rm -rf /"
        config.settings.accelerator_proxy_port = 9900
        runner = _make_runner_skipping_init()
        asyncio.run(runner._start_vpn())
        cmd = _last_shell_cmd(runner)
        # 字符串可能存在但必须被引号包住, 形成单一 shell 参数
        assert "'1.2.3.4; rm -rf /'" in cmd, (
            f"injection must be quoted as single arg; got: {cmd!r}"
        )
        print("PASS test_shell_injection_safe")
    finally:
        _restore_proxy_settings(saved)


def test_custom_port_propagates():
    saved = _save_proxy_settings()
    try:
        config.settings.accelerator_proxy_host = "10.0.2.2"
        config.settings.accelerator_proxy_port = 19900
        runner = _make_runner_skipping_init()
        asyncio.run(runner._start_vpn())
        cmd = _last_shell_cmd(runner)
        assert "--ei proxy_port 19900" in cmd
        print("PASS test_custom_port_propagates")
    finally:
        _restore_proxy_settings(saved)


if __name__ == "__main__":
    test_default_no_extras_keeps_legacy_behavior()
    test_with_proxy_host_passes_extras()
    test_shell_injection_safe()
    test_custom_port_propagates()
    print("\nALL PASS")

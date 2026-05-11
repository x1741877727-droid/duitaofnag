"""执行层 — ADB tap / start_app / screenshot.

- tap.SubprocessAdbTap: 默认实现, subprocess.run adb shell input tap
- 未来预留: PurePythonAdbTap (adb-shell lib, 跳 fork, Day 7+ 评估) / MaaTouchTap

Protocol 稳定, 换实现不破上层.
"""
from .tap import AdbTapProto, SubprocessAdbTap

__all__ = ["AdbTapProto", "SubprocessAdbTap"]

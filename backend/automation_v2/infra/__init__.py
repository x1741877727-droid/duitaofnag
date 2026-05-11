"""基础设施 — vm_watchdog 接入 / instance_state 恢复.

- watchdog_task.py: asyncio task 包装 v1 vm_watchdog, 全局 1 个跟 12 runner 平级
- state.py: V2 RunContext 跟 v1 instance_state 适配 (每 phase enter/exit save)
"""
from .watchdog_task import WatchdogTask
from .state import InstanceStateAdapter

__all__ = ["WatchdogTask", "InstanceStateAdapter"]

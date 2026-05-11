"""决策日志 — 双版本.

- decision_simple.DecisionSimple: JSONL 简版 (默认, ~250B/决策, 含 7 时间戳)
- decision_detailed.DecisionDetailed: 详细版 (env GAMEBOT_DETAILED_LOG=1, 含 input.jpg + tier_evidence)
"""
from .decision_simple import DecisionSimple

__all__ = ["DecisionSimple"]

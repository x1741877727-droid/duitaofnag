"""决策日志 — 双版本.

- decision_simple.DecisionSimple: JSONL 简版 (默认, ~250B/决策, 7 时间戳)
- decision_detailed.DecisionDetailed: 详细版 (env GAMEBOT_DETAILED_LOG=1, 含 input.jpg + tier_evidence)

工厂函数:
    log = make_decision_log(session_dir)   # 自动按 env 选 simple/detailed
"""
from .decision_simple import DecisionSimple
from .decision_detailed import DecisionDetailed, make_decision_log

__all__ = ["DecisionSimple", "DecisionDetailed", "make_decision_log"]

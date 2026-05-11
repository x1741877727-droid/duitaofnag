"""automation_v2 — 重构版业务编排.

跟 backend/automation/ (v1) 完全并行, 旧版生产业务跑 v1, v2 默认不被加载.
通过 env GAMEBOT_RUNNER_VERSION=v2 启用.

设计原则:
- v2 内部完全自包含, 不 import 旧 automation/ (除 P5 legacy)
- 每个文件 <= 150 行, 用户能 1 分钟读懂
- ROI optional (yolo/ocr/matcher 都支持全屏 fallback)
- 12 实例并发为基线
- 强复现: 每决策落 7 个时间戳 + trace_id

详见 docs/V2_PHASES.md.
"""

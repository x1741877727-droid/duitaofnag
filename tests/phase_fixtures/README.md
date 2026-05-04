# Phase Fixture Tests

轻量 phase-level 单元测试 — 不进 emulator, 不跑 ML, 只验**纯函数 policy 逻辑**.

## 为什么不用现有 oracle?

现有 `fixtures/oracle/` 是 decision_log 重放: 喂真生产 input.jpg + 跑完整 P2 perception + 对比标注.
那是端到端回归测, 重 + 慢, 而且需要 ONNX/cv2/rapidocr 全装.

这里测的是**单步纯逻辑** (decide / classify / verify), 不需要真图, 不需要 ML, 30s 跑完全集.

## 目录约定

```
tests/phase_fixtures/
├── p2_decide/                   # 测 phases.p2_policy.decide()
│   ├── memory_hit.json
│   ├── yolo_close_x.json
│   └── nothing.json
└── <new_phase>_<func>/          # 未来加 phase 时按这个套
```

## Fixture JSON schema

```jsonc
{
  "doc": "中文描述这个 case 测什么",

  "perception": {                // → Perception dataclass 字段
    "memory_hit": {"cx": 480, "cy": 60, "note": "..."},
    "yolo_close_xs": [
      {"cx": 100, "cy": 100, "conf": 0.9, "name": "close_x", "x1": 90, "y1": 90, "x2": 110, "y2": 110}
    ],
    "yolo_action_btns": [],
    "yolo_dets_raw": [],
    "memory_hit": null
    // 其他字段不写就用默认值
  },

  "ctx": {                       // → RunContext 字段 (只填测试用得到的)
    "blacklist_coords": []       // [(x, y), ...] — is_blacklisted 用
  },

  "expected": {                  // 期望 decide 返回
    "kind": "tap",               // "tap" / "wait" / "noop" / null
    "x": 480,
    "y": 60,
    "label": "memory_hit"
    // null = 期望 decide 返 None
  }
}
```

## 跑法

```bash
# Windows (有 numpy/onnx 装好的 Python):
python -m pytest tests/test_phase_fixtures.py -v

# 添新 case: 拷一份现有 .json 改字段, 重跑 pytest
```

## 加新 case 流程

1. 在 `<phase>_<func>/` 下加 `<name>.json`
2. 跑 `pytest`, 看断言失败时实际返回 vs 期望
3. 改代码到 pass

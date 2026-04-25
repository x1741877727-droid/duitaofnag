# 黄金回归测试集（Task 0.2）

把"已知正确"的截图 + 预期 OCR/template 结果存进来，每次改代码后跑一遍验证回归。

## 目录结构

每个测试 case = 一个子目录：

```
fixtures/golden_set/
  README.md                       (本文件)
  lobby_smoke/
    frame.png                     # 截图
    expected.json                 # 预期识别结果
  team_menu_with_btn/
    frame.png
    expected.json
  ...
```

## expected.json 格式

```jsonc
{
  "case_name": "lobby_smoke",
  "description": "大厅截图，左侧栏 ROI 应该能识别到任何文字",
  "frame_path": "frame.png",          // 可省略，默认 frame.png

  "checks": [
    // 类型 1：OCR ROI 烟雾测试 —— 只要识别到任意文字就算过
    {
      "type": "ocr_roi_smoke",
      "roi": "team_btn_left",         // 引用 config/roi.yaml 里定义的命名 ROI
      "min_hits": 1                   // 至少识别到 N 段文字
    },

    // 类型 2：OCR ROI 关键词匹配
    {
      "type": "ocr_roi_match",
      "roi": "team_btn_left",
      "must_contain_any": ["组队", "找队友"],   // 任一命中即可（fuzzy）
      "must_not_contain": ["排位"]               // 不能出现
    },

    // 类型 3：模板匹配（暂未启用，预留）
    {
      "type": "template_match",
      "tpl": "lobby/btn_match",
      "expect_hit": true,
      "min_score": 0.85
    }
  ]
}
```

## 添加新 case

1. 从 `logs/<session>/instance_<idx>/screenshots/` 或自己截图工具里挑代表性帧
2. `mkdir fixtures/golden_set/<case_name>` 然后把图片放进去命名 `frame.png`
3. 写 `expected.json`，先用 `ocr_roi_smoke` 跑一遍看 OCR 实际输出，再加严格的 `must_contain_any`

## 运行

```bash
# 全部
python tools/golden_runner.py

# 单个 case
python tools/golden_runner.py --case lobby_smoke

# 显示每个 OCR ROI 的实际文本（调 expected 用）
python tools/golden_runner.py -v
```

退出码：全部通过 → 0；任何一个失败 → 1。可以接到 CI 或 pre-commit。

## 与 metrics / roi.yaml 的关系

- ROI 定义在 [`config/roi.yaml`](../../config/roi.yaml)，golden 检测引用其中的 name
- 改 ROI 坐标 → 跑 golden_runner → 看哪些 case 还过 → 知道改动影响了什么
- 这就是 MASTER_PLAN 阶段 0 的"基线"：以后任何 OCR / 截图 / 阶段优化，都要 golden 全过才算合格

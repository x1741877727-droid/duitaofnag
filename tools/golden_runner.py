#!/usr/bin/env python3
"""黄金回归测试集 runner — Task 0.2

每次改 OCR / ROI / 模板 / 识别相关代码后跑一遍：
    python tools/golden_runner.py

退出码：全部通过 → 0；任何一个失败 → 1。可挂 CI / pre-commit。

Case 在 fixtures/golden_set/<case_name>/{frame.png,expected.json}。
格式见 fixtures/golden_set/README.md。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

# 让 backend 模块可导
_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)


@dataclass
class CheckResult:
    ok: bool
    msg: str
    detail: Optional[str] = None


def _load_cases(set_dir: str, only: Optional[str] = None) -> List[Dict[str, Any]]:
    cases: List[Dict[str, Any]] = []
    for case_dir in sorted(glob.glob(os.path.join(set_dir, "*/"))):
        exp_path = os.path.join(case_dir, "expected.json")
        if not os.path.exists(exp_path):
            continue
        with open(exp_path, "r", encoding="utf-8") as f:
            try:
                exp = json.load(f)
            except json.JSONDecodeError as e:
                print(f"⚠️  {case_dir}: expected.json 解析失败: {e}", file=sys.stderr)
                continue
        if only and exp.get("case_name") != only:
            continue
        frame_name = exp.get("frame_path", "frame.png")
        frame_path = os.path.join(case_dir, frame_name)
        if not os.path.exists(frame_path):
            print(f"⚠️  {case_dir}: 缺 {frame_name}", file=sys.stderr)
            continue
        exp["_frame_path"] = frame_path
        exp["_case_dir"] = case_dir
        cases.append(exp)
    return cases


def _check_ocr_roi_smoke(check: dict, frame: np.ndarray, ocr_obj) -> CheckResult:
    roi_name = check.get("roi")
    if not roi_name:
        return CheckResult(False, "missing 'roi' field")
    min_hits = int(check.get("min_hits", 1))
    try:
        hits = ocr_obj._ocr_roi_named(frame, roi_name)
    except Exception as e:
        return CheckResult(False, f"OCR error: {e}")
    n = len(hits)
    detail = " | ".join(h.text for h in hits[:8]) if hits else ""
    if n >= min_hits:
        return CheckResult(True, f"{n} hits ≥ {min_hits}", detail)
    return CheckResult(False, f"{n} hits < required {min_hits}", detail)


def _check_ocr_roi_match(check: dict, frame: np.ndarray, ocr_obj) -> CheckResult:
    from backend.automation.ocr_dismisser import OcrDismisser
    roi_name = check.get("roi")
    if not roi_name:
        return CheckResult(False, "missing 'roi' field")
    must_any: List[str] = check.get("must_contain_any", [])
    must_not: List[str] = check.get("must_not_contain", [])
    try:
        hits = ocr_obj._ocr_roi_named(frame, roi_name)
    except Exception as e:
        return CheckResult(False, f"OCR error: {e}")
    full_text = " ".join(h.text for h in hits)
    detail = full_text[:300] if full_text else "(no text)"
    # must_contain_any: 任意一个命中即可（fuzzy）
    if must_any:
        matched = any(
            OcrDismisser.fuzzy_match(h.text, kw) for h in hits for kw in must_any
        )
        if not matched:
            return CheckResult(
                False,
                f"none of {must_any} matched in OCR text",
                detail,
            )
    # must_not_contain: 出现任一则失败
    for kw in must_not:
        if any(OcrDismisser.fuzzy_match(h.text, kw) for h in hits):
            return CheckResult(False, f"unexpected '{kw}' in OCR text", detail)
    return CheckResult(True, "OCR keyword constraints satisfied", detail)


def _check_template_match(check: dict, frame: np.ndarray, matcher) -> CheckResult:
    if matcher is None:
        return CheckResult(False, "TemplateMatcher 未加载（templates_dir 缺失？）")
    tpl = check.get("tpl")
    if not tpl:
        return CheckResult(False, "missing 'tpl' field")
    expect_hit = bool(check.get("expect_hit", True))
    min_score = float(check.get("min_score", 0.85))
    try:
        result = matcher.match_one(frame, tpl)
    except Exception as e:
        return CheckResult(False, f"template match error: {e}")
    is_hit = result.matched and result.confidence >= min_score
    detail = f"score={result.confidence:.3f} matched={result.matched}"
    if is_hit and expect_hit:
        return CheckResult(True, f"hit @ {result.confidence:.3f}", detail)
    if not is_hit and not expect_hit:
        return CheckResult(True, f"correctly no hit", detail)
    if is_hit and not expect_hit:
        return CheckResult(False, f"unexpected hit @ {result.confidence:.3f}", detail)
    return CheckResult(False, f"missing hit (best score {result.confidence:.3f})", detail)


_DISPATCH = {
    "ocr_roi_smoke": _check_ocr_roi_smoke,
    "ocr_roi_match": _check_ocr_roi_match,
    "template_match": _check_template_match,
}


def _make_template_matcher() -> Optional[Any]:
    """尝试加载 TemplateMatcher；失败返回 None（template_match 检查会跳过）。"""
    try:
        from backend.recognition.template_matcher import TemplateMatcher
        tdir = os.path.join(_PROJ_ROOT, "fixtures", "templates")
        if not os.path.isdir(tdir):
            return None
        m = TemplateMatcher(templates_dir=tdir)
        m.load_templates()
        return m
    except Exception as e:
        print(f"⚠️  TemplateMatcher 初始化失败: {e}", file=sys.stderr)
        return None


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="黄金回归测试集")
    ap.add_argument(
        "--set-dir",
        default=os.path.join(_PROJ_ROOT, "fixtures", "golden_set"),
        help="测试集目录（默认 fixtures/golden_set）",
    )
    ap.add_argument("--case", default=None, help="只跑 case_name 等于此值的 case")
    ap.add_argument("-v", "--verbose", action="store_true", help="显示 OCR 实际文本")
    args = ap.parse_args(argv)

    cases = _load_cases(args.set_dir, only=args.case)
    if not cases:
        target = f" 匹配 case_name={args.case}" if args.case else ""
        print(f"❌ {args.set_dir} 下没有有效 case{target}")
        return 1

    # OCR 一次性初始化
    from backend.automation.ocr_dismisser import OcrDismisser
    ocr_obj = OcrDismisser()
    matcher = _make_template_matcher()

    pass_count = 0
    fail_count = 0
    t_start = time.perf_counter()

    for case in cases:
        case_name = case.get("case_name", os.path.basename(case["_case_dir"].rstrip("/")))
        frame = cv2.imread(case["_frame_path"])
        if frame is None:
            print(f"❌ {case_name}: 无法读取 {case['_frame_path']}")
            fail_count += 1
            continue

        case_t0 = time.perf_counter()
        case_ok = True
        check_lines: List[str] = []
        for check in case.get("checks", []):
            ctype = check.get("type", "?")
            handler = _DISPATCH.get(ctype)
            if handler is None:
                check_lines.append(f"  [?] unknown check type: {ctype}")
                case_ok = False
                continue
            if ctype == "template_match":
                res = handler(check, frame, matcher)
            else:
                res = handler(check, frame, ocr_obj)
            sym = "✓" if res.ok else "✗"
            label = check.get("roi") or check.get("tpl") or "?"
            line = f"  [{sym}] {ctype:18} {label:25}  {res.msg}"
            if args.verbose and res.detail:
                line += f"\n      {res.detail[:300]}"
            check_lines.append(line)
            if not res.ok:
                case_ok = False

        case_dur = (time.perf_counter() - case_t0) * 1000
        head = f"✅ {case_name}" if case_ok else f"❌ {case_name}"
        print(f"{head}  ({case_dur:.0f} ms)")
        for ln in check_lines:
            print(ln)
        if case_ok:
            pass_count += 1
        else:
            fail_count += 1

    total_dur = time.perf_counter() - t_start
    print(
        f"\n{pass_count} passed, {fail_count} failed "
        f"({len(cases)} cases, {total_dur:.2f}s)"
    )
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

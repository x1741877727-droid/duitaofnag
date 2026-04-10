"""阶段6 分步测试: 地图设置"""
import asyncio
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

from backend.automation.adb_lite import ADBController
from backend.automation.screen_matcher import ScreenMatcher
from backend.automation.ocr_dismisser import OcrDismisser

adb = ADBController("emulator-5558", r"D:\leidian\LDPlayer9\adb.exe")
ocr = OcrDismisser()
matcher = ScreenMatcher("fixtures/templates")
matcher.load_all()


async def ocr_tap(keywords, step=""):
    """OCR找关键词并点击"""
    for attempt in range(3):
        shot = await adb.screenshot()
        if shot is None:
            await asyncio.sleep(0.5)
            continue
        hits = ocr._ocr_all(shot)
        for kw in keywords:
            for h in hits:
                if kw in h.text:
                    print(f"  [{step}] OCR匹配 '{h.text}' → ({h.cx},{h.cy})")
                    await adb.tap(h.cx, h.cy)
                    return True
        if attempt < 2:
            await asyncio.sleep(0.8)
    print(f"  [{step}] 未找到 {keywords}")
    return False


async def ocr_dump(label=""):
    """截图+OCR全文"""
    import cv2
    shot = await adb.screenshot()
    if shot is None:
        print("截图失败")
        return
    cv2.imwrite("_map_panel.png", shot)
    hits = ocr._ocr_all(shot)
    print(f"\n=== {label} ({len(hits)}个文本) ===")
    for h in hits:
        print(f"  [{h.cx:4d},{h.cy:4d}] '{h.text}'")


async def step1_open_map_panel():
    """步骤1: 从大厅打开地图选择面板"""
    print("\n=== 步骤1: 打开地图选择面板 ===")
    shot = await adb.screenshot()
    if shot is None:
        return False
    hits = ocr._ocr_all(shot)
    for h in hits:
        if "开始游戏" in h.text:
            target_y = h.cy + 60
            print(f"  '开始游戏' @ ({h.cx},{h.cy})，点击模式名 ({h.cx},{target_y})")
            await adb.tap(h.cx, target_y)
            await asyncio.sleep(1.5)
            return True
    print("  未找到'开始游戏'")
    return False


async def step2_select_team_battle():
    """步骤2: 点击左侧'团队竞技'"""
    print("\n=== 步骤2: 选择团队竞技 ===")
    return await ocr_tap(["团队竞技"], step="团队竞技")


async def step3_select_map(target="狙击团竞"):
    """步骤3: 选择目标地图

    OCR 经常把"狙击"识别成"姐击"/"阻击"/"组击"等，
    所以用模糊匹配：只要包含"团竞"且包含"击"或"桥"/"基地"就算命中。
    """
    print(f"\n=== 步骤3: 选择地图 '{target}' ===")
    await asyncio.sleep(1)

    # 构建模糊匹配关键词
    # "狙击团竞" → 可能被识别为 "姐击团竞"/"阻击团竞"/"组击团竞"
    # 关联词：大桥、军事基地
    fuzzy_keywords = [target]  # 先尝试精确
    if "狙击" in target:
        fuzzy_keywords.extend(["击团竞", "大桥", "军事基地"])
    if "经典" in target:
        fuzzy_keywords.extend(["经典团竞", "仓库", "滨海"])
    if "军备" in target:
        fuzzy_keywords.extend(["军备团竞", "图书馆"])
    if "迷你" in target:
        fuzzy_keywords.extend(["迷你战争", "电玩"])

    for attempt in range(3):
        shot = await adb.screenshot()
        if shot is None:
            await asyncio.sleep(0.5)
            continue
        hits = ocr._ocr_all(shot)
        for kw in fuzzy_keywords:
            for h in hits:
                if kw in h.text:
                    print(f"  [选择地图] OCR匹配 '{h.text}' (关键词'{kw}') → ({h.cx},{h.cy})")
                    await adb.tap(h.cx, h.cy)
                    return True
        if attempt < 2:
            await asyncio.sleep(0.8)

    print(f"  [选择地图] 未找到 '{target}' 及其变体")
    return False


async def step4_disable_auto_fill():
    """步骤4: 取消'自动匹配队友'补位"""
    print("\n=== 步骤4: 检查并取消自动补位 ===")
    await asyncio.sleep(0.5)
    shot = await adb.screenshot()
    if shot is None:
        return False
    hits = ocr._ocr_all(shot)
    # 找"自动匹配队友"文字
    for h in hits:
        if "自动匹配" in h.text or "匹配队友" in h.text:
            print(f"  找到补位按钮: '{h.text}' @ ({h.cx},{h.cy})")
            # 需要判断当前是否选中状态——先点击看效果
            await adb.tap(h.cx, h.cy)
            print("  已点击取消补位")
            return True
    print("  未找到'自动匹配队友'按钮")
    return False


async def step5_confirm():
    """步骤5: 点击确定"""
    print("\n=== 步骤5: 点击确定 ===")
    await asyncio.sleep(0.5)
    return await ocr_tap(["确定"], step="确定")


async def main():
    step = sys.argv[1] if len(sys.argv) > 1 else "help"

    if step == "1":
        await step1_open_map_panel()
    elif step == "2":
        await step2_select_team_battle()
        await asyncio.sleep(1)
        await ocr_dump("团队竞技面板")
    elif step == "3":
        target = sys.argv[2] if len(sys.argv) > 2 else "狙击团竞"
        await step3_select_map(target)
    elif step == "4":
        await step4_disable_auto_fill()
    elif step == "5":
        await step5_confirm()
    elif step == "dump":
        await ocr_dump("当前画面")
    elif step == "all":
        if not await step1_open_map_panel():
            return
        if not await step2_select_team_battle():
            return
        await asyncio.sleep(1)
        await ocr_dump("团队竞技面板")
        target = sys.argv[2] if len(sys.argv) > 2 else "狙击团竞"
        if not await step3_select_map(target):
            print("  地图未找到，dump当前面板文字:")
            await ocr_dump("查找地图失败")
            return
        await step4_disable_auto_fill()
        await step5_confirm()
        await asyncio.sleep(1)
        await ocr_dump("完成后")
    else:
        print("Usage: python _test_map.py [1|2|3|4|5|dump|all]")
        print("  1    - 打开地图面板")
        print("  2    - 选择团队竞技")
        print("  3 [地图名] - 选择地图")
        print("  4    - 取消自动补位")
        print("  5    - 点击确定")
        print("  dump - OCR当前画面")
        print("  all [地图名] - 全流程")


asyncio.run(main())

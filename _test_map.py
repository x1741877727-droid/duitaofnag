"""阶段6 分步测试: 地图设置"""
import asyncio
import logging
import sys

logging.basicConfig(
    level=logging.DEBUG,
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


async def step_read_current():
    """步骤0: 读取当前大厅显示的模式和地图"""
    print("\n=== 步骤0: OCR 读取当前模式 ===")
    shot = await adb.screenshot()
    if shot is None:
        print("截图失败")
        return

    hits = ocr._ocr_all(shot)
    print(f"OCR 识别到 {len(hits)} 个文本:")
    for h in hits:
        print(f"  [{h.cx:4d},{h.cy:4d}] '{h.text}'")

    # 重点看左上角区域 (x<300, y<150) 的文字
    print("\n--- 左上角区域 (模式名) ---")
    for h in hits:
        if h.cx < 350 and h.cy < 150:
            print(f"  [{h.cx:4d},{h.cy:4d}] '{h.text}'")


async def step_click_mode_area():
    """步骤1: 点击模式名区域打开地图选择"""
    print("\n=== 步骤1: OCR找到模式名并点击 ===")
    shot = await adb.screenshot()
    if shot is None:
        return

    hits = ocr._ocr_all(shot)
    # 找左上角 "开始游戏" 下方的模式名文字
    for h in hits:
        if h.cy > 50 and h.cy < 120 and h.cx < 350:
            print(f"  找到模式名: '{h.text}' @ ({h.cx},{h.cy})")
            await adb.tap(h.cx, h.cy)
            print("  已点击，等待面板打开...")
            await asyncio.sleep(2)
            return

    print("  未找到模式名，尝试点击'开始游戏'下方")
    # 找"开始游戏"位置，往下偏移
    for h in hits:
        if "开始" in h.text and "游戏" in h.text:
            print(f"  找到'开始游戏' @ ({h.cx},{h.cy})，点击下方")
            await adb.tap(h.cx, h.cy + 40)
            await asyncio.sleep(2)
            return


async def step_screenshot_map_panel():
    """步骤1.5: 截图看地图选择面板"""
    print("\n=== 截图: 地图选择面板 ===")
    shot = await adb.screenshot()
    if shot is None:
        return

    import cv2
    cv2.imwrite("_map_panel.png", shot)
    print("  已保存 _map_panel.png")

    hits = ocr._ocr_all(shot)
    print(f"  OCR 识别到 {len(hits)} 个文本:")
    for h in hits:
        print(f"    [{h.cx:4d},{h.cy:4d}] '{h.text}'")


async def main():
    step = sys.argv[1] if len(sys.argv) > 1 else "0"

    if step == "0":
        await step_read_current()
    elif step == "1":
        await step_click_mode_area()
    elif step == "1.5":
        await step_screenshot_map_panel()
    elif step == "all":
        await step_read_current()
        await step_click_mode_area()
        await step_screenshot_map_panel()
    else:
        print(f"Unknown step: {step}")
        print("Usage: python _test_map.py [0|1|1.5|all]")


asyncio.run(main())

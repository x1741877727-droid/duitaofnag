"""
模板匹配准确率测试
用真实截图验证所有模板是否能正确匹配
"""

import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import cv2
from backend.automation.screen_matcher import ScreenMatcher


def test_all():
    screenshots_dir = project_root / "fixtures" / "screenshots"
    templates_dir = project_root / "fixtures" / "templates"

    matcher = ScreenMatcher(str(templates_dir), default_threshold=0.70)
    n = matcher.load_all()
    print(f"\n已加载 {n} 个模板: {matcher.template_names}\n")
    print("=" * 80)

    # ============================================================
    # 测试用例: (截图文件, 应该匹配的模板, 不应该匹配的模板)
    # ============================================================

    tests = [
        # 大厅
        ("dating-8.png", ["lobby_start_btn", "lobby_start_game"], []),

        # 加速器（两个按钮形状相似，0.96以上阈值才能区分）
        # 直接匹配测试用高阈值
        ("liuhuaguanbi-2.png", ["accelerator_play"], []),
        ("liuhuakaiqi-3.png", ["accelerator_pause"], []),

        # X按钮弹窗
        ("youxihuodong-7.png", ["close_x_gold"], []),
        ("youxihuodong-7.1.1.png", ["close_x_white_big"], []),
        ("youxihuodong-7.1.png", ["close_x_signin"], []),
        ("youxihuodong-7.2.3.png", ["close_x_activity"], []),

        # 确定/同意按钮
        ("neicundi-5.1.png", ["btn_confirm"], []),
        ("yinsi2.png", ["btn_agree"], []),

        # 点击屏幕继续
        ("youxihuodong-7.1.2.png", ["text_click_continue"], []),

        # 组队加入
        ("zuduima-9.1.png", ["btn_join"], []),

        # 找队友弹窗
        ("zhoaduiyou.png", ["btn_no_need"], []),

        # 见面礼
        ("jianmianli.png", ["btn_claim_gift"], []),

        # 大厅不应出现弹窗模板
        ("dating-8.png", [], ["close_x_gold", "close_x_white_big", "btn_confirm"]),

        # 非大厅截图不应匹配大厅
        ("youxihuodong-7.png", [], ["lobby_start_btn", "lobby_start_game"]),
    ]

    passed = 0
    failed = 0

    for screenshot_name, should_match, should_not_match in tests:
        screenshot_path = screenshots_dir / screenshot_name
        if not screenshot_path.exists():
            print(f"  ⚠ 截图不存在: {screenshot_name}")
            continue

        img = cv2.imread(str(screenshot_path))
        if img is None:
            print(f"  ⚠ 无法读取: {screenshot_name}")
            continue

        print(f"\n--- {screenshot_name} ---")

        # 应该匹配的
        for tmpl_name in should_match:
            hit = matcher.match_one(img, tmpl_name)
            if hit:
                print(f"  ✓ {tmpl_name}: 匹配 conf={hit.confidence:.3f} @ ({hit.cx},{hit.cy})")
                passed += 1
            else:
                print(f"  ✗ {tmpl_name}: 未匹配 (应该匹配)")
                failed += 1

        # 不应该匹配的
        for tmpl_name in should_not_match:
            hit = matcher.match_one(img, tmpl_name)
            if hit:
                print(f"  ✗ {tmpl_name}: 误匹配 conf={hit.confidence:.3f} (不应该匹配)")
                failed += 1
            else:
                print(f"  ✓ {tmpl_name}: 正确未匹配")
                passed += 1

    print("\n" + "=" * 80)
    print(f"结果: {passed} 通过, {failed} 失败, 总计 {passed + failed}")
    print(f"准确率: {passed/(passed+failed)*100:.1f}%" if (passed + failed) > 0 else "无测试")

    # 额外: 测试便捷方法
    print("\n=== 便捷方法测试 ===")

    lobby_img = cv2.imread(str(screenshots_dir / "dating-8.png"))
    popup_img = cv2.imread(str(screenshots_dir / "youxihuodong-7.1.1.png"))
    accel_off = cv2.imread(str(screenshots_dir / "liuhuaguanbi-2.png"))
    accel_on = cv2.imread(str(screenshots_dir / "liuhuakaiqi-3.png"))

    print(f"  is_at_lobby(大厅截图) = {matcher.is_at_lobby(lobby_img)}")
    print(f"  is_at_lobby(弹窗截图) = {matcher.is_at_lobby(popup_img)}")
    print(f"  find_close_button(弹窗截图) = {matcher.find_close_button(popup_img)}")
    print(f"  find_close_button(大厅截图) = {matcher.find_close_button(lobby_img)}")
    print(f"  is_accelerator_connected(未连接) = {matcher.is_accelerator_connected(accel_off)}")
    print(f"  is_accelerator_connected(已连接) = {matcher.is_accelerator_connected(accel_on)}")
    print(f"  is_accelerator_connected(大厅截图) = {matcher.is_accelerator_connected(lobby_img)}")

    return failed == 0


if __name__ == "__main__":
    success = test_all()
    sys.exit(0 if success else 1)

"""
Phase 1 验证脚本
在 macOS 上用 mock 模式测试，在 Windows 上连接真实模拟器测试

用法:
  python tests/test_phase1.py          # mock 模式
  python tests/test_phase1.py --real   # 真实模式（需要 Windows + 雷电模拟器）
"""

import asyncio
import logging
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("test_phase1")


async def test_mock():
    """Mock 模式测试：验证代码逻辑无需真实设备"""
    from backend.adb.ldplayer import LDPlayerManager
    from backend.adb.controller import ADBController
    from backend.config import config

    # 加载配置
    config.settings.dev_mock = True
    config.load()
    logger.info(f"加载了 {len(config.accounts)} 个账号配置")

    # 测试 LDPlayer 管理器
    ldm = LDPlayerManager(ldplayer_path=config.settings.ldplayer_path, mock=True)

    # 列出实例
    instances = await ldm.list_instances()
    logger.info(f"发现 {len(instances)} 个模拟器实例:")
    for inst in instances:
        logger.info(f"  [{inst.index}] {inst.name} - 端口:{inst.adb_port} 运行:{inst.running}")

    # 启动实例 0
    await ldm.launch(0)
    assert await ldm.is_running(0), "实例 0 应该在运行"
    logger.info("✓ 实例 0 启动成功")

    # 获取 ADB 地址
    serial = await ldm.get_adb_serial(0)
    logger.info(f"实例 0 ADB 地址: {serial}")

    # 测试 ADB 控制器
    ctrl = ADBController(serial=serial, instance_index=0, mock=True)
    connected = await ctrl.connect()
    assert connected, "ADB 连接应该成功"
    logger.info("✓ ADB 连接成功")

    # 截图
    img = await ctrl.screenshot()
    assert img is not None, "截图不应为 None"
    logger.info(f"✓ 截图成功: shape={img.shape}")

    # 点击
    result = await ctrl.tap(640, 360)
    assert result.success, "点击应该成功"
    logger.info(f"✓ 点击成功: ({result.x}, {result.y})")

    # 滑动
    ok = await ctrl.swipe(100, 500, 600, 500, 300)
    assert ok, "滑动应该成功"
    logger.info("✓ 滑动成功")

    # 按键
    await ctrl.key_event(4)  # BACK
    logger.info("✓ 按键成功")

    # 打开 URL
    await ctrl.open_url("https://example.com/team/join?code=abc123")
    logger.info("✓ 打开 URL 成功")

    # 断网/恢复
    await ctrl.disconnect_network()
    logger.info("✓ 断网成功")
    await ctrl.restore_network()
    logger.info("✓ 恢复网络成功")

    # 关闭实例
    await ldm.quit(0)
    logger.info("✓ 实例 0 关闭成功")

    # 测试多实例并发
    logger.info("\n--- 测试 6 实例并发 ---")
    controllers = []
    for i in range(6):
        await ldm.launch(i)
        s = await ldm.get_adb_serial(i)
        c = ADBController(serial=s, instance_index=i, mock=True)
        await c.connect()
        controllers.append(c)

    # 并发截图
    screenshots = await asyncio.gather(*[c.screenshot() for c in controllers])
    for i, img in enumerate(screenshots):
        assert img is not None
    logger.info(f"✓ {len(screenshots)} 个实例并发截图成功")

    # 并发点击
    taps = await asyncio.gather(*[c.tap(640, 360) for c in controllers])
    for t in taps:
        assert t.success
    logger.info(f"✓ {len(taps)} 个实例并发点击成功")

    # 清理
    await ldm.quit_all()
    for c in controllers:
        await c.disconnect()

    logger.info("\n========== Phase 1 Mock 测试全部通过 ==========")


async def test_real():
    """真实模式测试：需要 Windows + 雷电模拟器"""
    from backend.adb.ldplayer import LDPlayerManager
    from backend.adb.controller import ADBController
    from backend.config import config

    config.load()

    ldm = LDPlayerManager(ldplayer_path=config.settings.ldplayer_path, mock=False)

    # 列出实例
    instances = await ldm.list_instances()
    logger.info(f"发现 {len(instances)} 个模拟器实例")

    if not instances:
        logger.error("没有发现模拟器实例，请先在雷电模拟器中创建实例")
        return

    # 取第一个实例测试
    inst = instances[0]
    logger.info(f"使用实例 [{inst.index}] {inst.name}")

    # 确保实例运行
    await ldm.ensure_running(inst.index)

    # ADB 连接
    serial = await ldm.get_adb_serial(inst.index)
    ctrl = ADBController(serial=serial, instance_index=inst.index)

    if not await ctrl.connect():
        logger.error("ADB 连接失败")
        return

    # 截图
    img = await ctrl.screenshot()
    if img is not None:
        import cv2
        path = "test_screenshot.png"
        cv2.imwrite(path, img)
        logger.info(f"✓ 截图保存到 {path}: shape={img.shape}")
    else:
        logger.error("截图失败")

    # 点击屏幕中央
    result = await ctrl.tap(640, 360)
    logger.info(f"✓ 点击结果: success={result.success}")

    await ctrl.disconnect()
    logger.info("\n========== Phase 1 真实测试完成 ==========")


if __name__ == "__main__":
    if "--real" in sys.argv:
        asyncio.run(test_real())
    else:
        asyncio.run(test_mock())

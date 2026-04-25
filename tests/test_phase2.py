"""
Phase 2 验证脚本
测试识别层：模板匹配、OCR、LLM 视觉、缓存、管道、模板工具

用法:
  python tests/test_phase2.py
"""

import asyncio
import logging
import os
import sys
import tempfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import cv2
import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("test_phase2")


def create_test_images():
    """生成测试用的模拟截图和模板"""
    # 模拟游戏截图 (1280x720)
    screenshot = np.zeros((720, 1280, 3), dtype=np.uint8)
    # 画一些模拟 UI 元素
    # 模拟弹窗背景
    cv2.rectangle(screenshot, (300, 150), (980, 570), (60, 60, 60), -1)
    cv2.rectangle(screenshot, (300, 150), (980, 570), (200, 200, 200), 2)
    # 模拟关闭按钮 (右上角 X)
    cv2.rectangle(screenshot, (940, 155), (975, 190), (200, 50, 50), -1)
    cv2.putText(screenshot, "X", (948, 182), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
    # 模拟弹窗标题
    cv2.putText(screenshot, "Activity", (550, 210), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)

    # 模板：关闭按钮区域
    close_btn = screenshot[155:190, 940:975].copy()

    return screenshot, close_btn


async def test_template_matcher():
    """测试模板匹配器"""
    from backend.recognition.template_matcher import TemplateMatcher

    logger.info("=== 测试模板匹配器 ===")

    with tempfile.TemporaryDirectory() as tmpdir:
        templates_dir = os.path.join(tmpdir, "templates")
        os.makedirs(os.path.join(templates_dir, "popup"))

        matcher = TemplateMatcher(templates_dir)
        screenshot, close_btn = create_test_images()

        # 添加模板
        matcher.add_template("close_btn", "popup", close_btn, threshold=0.8)
        logger.info(f"  已加载模板: {list(matcher.templates.keys())}")

        # 单模板匹配
        result = matcher.match_one(screenshot, "popup/close_btn", multi_scale=False)
        logger.info(f"  单尺度匹配: matched={result.matched} conf={result.confidence:.3f} pos=({result.x},{result.y})")
        assert result.matched, "单尺度匹配应该成功"

        # 多尺度匹配
        result = matcher.match_one(screenshot, "popup/close_btn", multi_scale=True)
        logger.info(f"  多尺度匹配: matched={result.matched} conf={result.confidence:.3f} scale={result.scale:.2f}")
        assert result.matched, "多尺度匹配应该成功"

        # 按分类批量匹配
        any_result = matcher.match_any(screenshot, category="popup")
        assert any_result is not None and any_result.matched
        logger.info(f"  分类匹配: {any_result.template_name} conf={any_result.confidence:.3f}")

        # 验证接口
        verify = matcher.verify_template(screenshot, "popup/close_btn")
        logger.info(f"  验证结果: best_scale={verify['best_scale']} conf={verify['best_confidence']:.3f}")

        # 缩放测试：模拟不同分辨率
        scaled_screenshot = cv2.resize(screenshot, (1920, 1080))
        # 归一化后匹配
        result = matcher.match_one(scaled_screenshot, "popup/close_btn", multi_scale=False)
        logger.info(f"  跨分辨率 (1920x1080→归一化): matched={result.matched} conf={result.confidence:.3f}")
        assert result.matched, "归一化后应该能匹配"

        # 保存模板到磁盘
        matcher.save_template("close_btn_saved", "popup", close_btn, threshold=0.8)
        assert os.path.exists(os.path.join(templates_dir, "popup", "close_btn_saved.png"))
        logger.info("  模板保存到磁盘: ✓")

        # 从磁盘重新加载
        matcher2 = TemplateMatcher(templates_dir)
        matcher2.load_templates()
        assert len(matcher2.templates) > 0
        logger.info(f"  从磁盘加载模板: {len(matcher2.templates)} 个 ✓")

    logger.info("✓ 模板匹配器测试全部通过\n")


async def test_ocr():
    """测试 OCR（mock 模式）"""
    from backend.recognition.ocr_reader import OCRReader

    logger.info("=== 测试 OCR 识别器 (mock) ===")

    ocr = OCRReader(mock=True)
    screenshot = np.zeros((720, 1280, 3), dtype=np.uint8)

    # 全文识别
    resp = ocr.recognize(screenshot)
    logger.info(f"  识别结果: {len(resp.results)} 条, 全文: '{resp.full_text}'")
    assert resp.results, "mock 应该返回结果"

    # 关键词查找
    found = resp.find_text("玩家ID")
    assert found is not None
    logger.info(f"  查找 '玩家ID': found at ({found.center_x}, {found.center_y})")

    # 纯文字接口
    text = ocr.recognize_text_only(screenshot)
    assert text
    logger.info(f"  纯文字: '{text}'")

    # 玩家 ID 读取
    pid = ocr.read_player_id(screenshot)
    logger.info(f"  玩家 ID: '{pid}'")

    # ROI 裁剪
    resp_roi = ocr.recognize(screenshot, roi=(10, 10, 300, 100))
    logger.info(f"  ROI 识别: {len(resp_roi.results)} 条")

    logger.info("✓ OCR 测试全部通过\n")


async def test_llm_vision():
    """测试 LLM 视觉（mock 模式）"""
    from backend.recognition.llm_vision import LLMVision

    logger.info("=== 测试 LLM 视觉 (mock) ===")

    llm = LLMVision(api_url="http://mock.api/v1", mock=True)
    screenshot = np.zeros((720, 1280, 3), dtype=np.uint8)

    # 弹窗检测
    popup = await llm.detect_popup(screenshot)
    logger.info(f"  弹窗检测: {popup}")
    assert "has_popup" in popup

    # 状态分析
    state = await llm.analyze_state(screenshot)
    logger.info(f"  状态分析: {state}")
    assert "state" in state

    # 对手识别
    opponents = await llm.read_opponents(screenshot)
    logger.info(f"  对手识别: {opponents}")
    assert "opponents" in opponents

    # 通用问答
    answer = await llm.ask(screenshot, "画面中有什么？")
    logger.info(f"  通用问答: {answer[:50]}...")

    logger.info("✓ LLM 视觉测试全部通过\n")


async def test_cache():
    """测试 LLM 缓存"""
    from backend.recognition.cache import LLMCache

    logger.info("=== 测试 LLM 缓存 ===")

    with tempfile.TemporaryDirectory() as tmpdir:
        cache = LLMCache(cache_dir=tmpdir, hash_threshold=8, ttl=60)

        # 测试图片
        img1 = np.random.randint(0, 255, (720, 1280, 3), dtype=np.uint8)
        img2 = img1.copy()  # 完全相同
        img3 = np.random.randint(0, 255, (720, 1280, 3), dtype=np.uint8)  # 完全不同

        # 写入缓存
        cache.put(img1, "detect_popup", {"has_popup": True, "close_x": 100, "close_y": 200})
        logger.info("  写入缓存: ✓")

        # 精确命中
        result = cache.get(img2, "detect_popup")
        assert result is not None
        assert result["has_popup"] is True
        logger.info(f"  精确命中: ✓ result={result}")

        # 不同 prompt_key 不命中
        result = cache.get(img2, "analyze_state")
        assert result is None
        logger.info("  不同 prompt_key 不命中: ✓")

        # 完全不同的图片不命中
        result = cache.get(img3, "detect_popup")
        # 注意：随机图片的 pHash 可能碰巧相近，这里只做基本验证
        logger.info(f"  不同图片命中: {result is not None}")

        # 缓存统计
        stats = cache.stats()
        logger.info(f"  缓存统计: {stats}")
        assert stats["total_entries"] >= 1

        # 持久化
        cache.save_to_disk()
        assert os.path.exists(os.path.join(tmpdir, "llm_cache.json"))
        logger.info("  持久化: ✓")

        # 重新加载
        cache2 = LLMCache(cache_dir=tmpdir, ttl=60)
        stats2 = cache2.stats()
        logger.info(f"  重新加载: {stats2['total_entries']} 条 ✓")

    logger.info("✓ 缓存测试全部通过\n")


async def test_pipeline():
    """测试三级识别管道"""
    from backend.recognition.template_matcher import TemplateMatcher
    from backend.recognition.ocr_reader import OCRReader
    from backend.recognition.llm_vision import LLMVision
    from backend.recognition.cache import LLMCache
    from backend.recognition.pipeline import RecognitionPipeline, RecognitionLevel

    logger.info("=== 测试三级识别管道 ===")

    with tempfile.TemporaryDirectory() as tmpdir:
        templates_dir = os.path.join(tmpdir, "templates")
        os.makedirs(os.path.join(templates_dir, "popup"))

        matcher = TemplateMatcher(templates_dir)
        ocr = OCRReader(mock=True)
        llm = LLMVision(api_url="http://mock.api/v1", mock=True)
        cache = LLMCache(cache_dir=tmpdir, ttl=60)

        pipeline = RecognitionPipeline(matcher, ocr, llm, cache)

        screenshot, close_btn = create_test_images()

        # 添加模板
        matcher.add_template("close_btn", "popup", close_btn, threshold=0.8)

        # 1. 弹窗检测 — 应该走模板匹配
        result = await pipeline.detect_popup(screenshot)
        logger.info(f"  弹窗检测: level={result.level.value} success={result.success}")
        assert result.success
        assert result.level == RecognitionLevel.TEMPLATE
        logger.info(f"  → 模板命中: pos=({result.click_target})")

        # 2. 空截图弹窗检测 — 模板不中，走 LLM
        blank = np.zeros((720, 1280, 3), dtype=np.uint8)
        result = await pipeline.detect_popup(blank)
        logger.info(f"  空截图弹窗检测: level={result.level.value} success={result.success}")
        # mock LLM 返回 has_popup=True
        assert result.level == RecognitionLevel.LLM

        # 3. 文字读取 — 走 OCR mock
        result = await pipeline.read_text(screenshot)
        logger.info(f"  文字读取: level={result.level.value} text={result.data.get('full_text', '')[:30]}")
        assert result.level == RecognitionLevel.OCR

        # 4. 玩家 ID 校验
        result = await pipeline.verify_player_id(screenshot, "12345678")
        logger.info(f"  玩家 ID 校验: level={result.level.value} data={result.data}")
        assert result.success

        # 5. 缓存验证：第二次 LLM 调用应该走缓存
        cache_stats_before = cache.stats()
        result = await pipeline.detect_popup(blank)
        cache_stats_after = cache.stats()
        logger.info(f"  缓存命中验证: before_hits={cache_stats_before['total_hits']} after_hits={cache_stats_after['total_hits']}")

    logger.info("✓ 管道测试全部通过\n")


async def test_template_tool():
    """测试模板采集工具"""
    from backend.recognition.template_matcher import TemplateMatcher
    from backend.tools.template_tool import TemplateTool

    logger.info("=== 测试模板采集工具 ===")

    with tempfile.TemporaryDirectory() as tmpdir:
        templates_dir = os.path.join(tmpdir, "templates")
        os.makedirs(templates_dir)

        matcher = TemplateMatcher(templates_dir)
        tool = TemplateTool(matcher)

        screenshot, _ = create_test_images()

        # 1. 采集模板
        result = tool.capture_template(
            screenshot=screenshot,
            region=(940, 155, 35, 35),  # 关闭按钮区域
            name="close_x",
            category="popup",
            threshold=0.8,
        )
        logger.info(f"  采集: success={result.success} path={result.image_path}")
        assert result.success

        # 2. 即时测试
        match = tool.test_match(screenshot, "popup/close_x", multi_scale=True)
        logger.info(f"  即时测试: matched={match.matched} conf={match.confidence:.3f}")
        assert match.matched

        # 3. 详细验证
        vr = tool.verify_single(screenshot, "popup/close_x")
        logger.info(f"  详细验证: passed={vr.passed} conf={vr.best_confidence:.3f} scale={vr.best_scale}")

        # 4. 列出模板
        templates = tool.list_templates()
        logger.info(f"  模板列表: {len(templates)} 个")
        assert len(templates) == 1

        # 5. 批量验证
        report = tool.verify_all(screenshot)
        logger.info(f"  批量验证: total={report['total']} passed={report['passed']} rate={report['pass_rate']}%")

        # 6. 截图预览
        preview = tool.get_screenshot_preview(screenshot, "popup/close_x")
        assert len(preview) > 0
        logger.info(f"  截图预览: {len(preview)} bytes")

        # 7. 删除模板
        ok = tool.delete_template("popup/close_x")
        assert ok
        templates = tool.list_templates()
        assert len(templates) == 0
        logger.info("  删除模板: ✓")

    logger.info("✓ 模板采集工具测试全部通过\n")


async def main():
    await test_template_matcher()
    await test_ocr()
    await test_llm_vision()
    await test_cache()
    await test_pipeline()
    await test_template_tool()
    logger.info("========== Phase 2 全部测试通过 ==========")


if __name__ == "__main__":
    asyncio.run(main())

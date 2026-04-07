"""
诊断/日志归档模块
- JSONL 日志文件输出
- 截图自动归档
- 一键诊断快照
"""

import asyncio
import base64
import json
import logging
import os
import time
from collections import deque
from datetime import datetime
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# 项目根目录
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS_DIR = os.path.join(ROOT, "logs")
SCREENSHOTS_DIR = os.path.join(ROOT, "screenshots")


class DiagnosticManager:
    """
    诊断数据管理器
    - 内存中保留最近 N 条日志
    - 同时写入磁盘 JSONL 文件
    - 截图归档目录管理
    """

    def __init__(self, max_recent_logs: int = 1000):
        self.max_recent = max_recent_logs
        self.recent_logs: deque = deque(maxlen=max_recent_logs)

        # 创建目录
        os.makedirs(LOGS_DIR, exist_ok=True)
        os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

        # 当前日志文件
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.log_file = os.path.join(LOGS_DIR, f"run-{ts}.jsonl")
        self.session_id = ts

        # 启动时间
        self.session_start = time.time()

        logger.info(f"诊断模块初始化: log={self.log_file}")

    def write_log(self, entry_dict: dict):
        """写入一条日志（同时存内存和磁盘）"""
        self.recent_logs.append(entry_dict)

        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry_dict, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"日志写入失败: {e}")

    def get_recent_logs(self, limit: int = 200,
                        instance_index: Optional[int] = None,
                        level: Optional[str] = None) -> list[dict]:
        """获取最近日志（可过滤）"""
        logs = list(self.recent_logs)

        if instance_index is not None:
            logs = [l for l in logs if l.get("instance") == instance_index]
        if level:
            logs = [l for l in logs if l.get("level") == level]

        return logs[-limit:]

    def get_recent_errors(self, limit: int = 50) -> list[dict]:
        """获取最近错误日志"""
        return self.get_recent_logs(limit, level="error") + \
               self.get_recent_logs(limit, level="warn")

    def archive_screenshot(self, image: np.ndarray, instance_index: int,
                           label: str = "") -> str:
        """
        归档截图到磁盘
        命名: {timestamp}_{instance}_{label}.jpg
        Returns:
            相对路径
        """
        try:
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            label = label or "snapshot"
            filename = f"{ts}_{instance_index}_{label}.jpg"
            filepath = os.path.join(SCREENSHOTS_DIR, filename)
            cv2.imwrite(filepath, image, [cv2.IMWRITE_JPEG_QUALITY, 80])

            # 限制目录最大文件数
            self._cleanup_old_screenshots(max_files=200)

            return filename
        except Exception as e:
            logger.error(f"截图归档失败: {e}")
            return ""

    def list_screenshots(self, limit: int = 50,
                         instance_index: Optional[int] = None) -> list[dict]:
        """列出归档的截图"""
        try:
            files = sorted(os.listdir(SCREENSHOTS_DIR), reverse=True)
        except FileNotFoundError:
            return []

        result = []
        for fname in files:
            if not fname.endswith((".jpg", ".png")):
                continue
            if instance_index is not None:
                # 文件名格式 ts_instance_label.jpg
                parts = fname.split("_")
                if len(parts) >= 2:
                    try:
                        if int(parts[1]) != instance_index:
                            continue
                    except ValueError:
                        pass

            filepath = os.path.join(SCREENSHOTS_DIR, fname)
            try:
                stat = os.stat(filepath)
                result.append({
                    "filename": fname,
                    "size": stat.st_size,
                    "modified": stat.st_mtime,
                })
            except FileNotFoundError:
                pass

            if len(result) >= limit:
                break

        return result

    def get_screenshot_path(self, filename: str) -> Optional[str]:
        """获取截图完整路径（带安全检查）"""
        # 防止路径遍历
        if "/" in filename or "\\" in filename or ".." in filename:
            return None
        path = os.path.join(SCREENSHOTS_DIR, filename)
        if os.path.exists(path):
            return path
        return None

    def _cleanup_old_screenshots(self, max_files: int = 200):
        """清理旧截图，只保留最新的 N 个"""
        try:
            files = sorted(os.listdir(SCREENSHOTS_DIR), reverse=True)
            files = [f for f in files if f.endswith((".jpg", ".png"))]
            for old in files[max_files:]:
                try:
                    os.remove(os.path.join(SCREENSHOTS_DIR, old))
                except OSError:
                    pass
        except FileNotFoundError:
            pass

    async def take_snapshot(self, coordinator) -> dict:
        """
        一键诊断快照：状态 + 日志 + 截图（base64） + 系统信息
        让我可以一个请求看清当前所有情况
        """
        snapshot = {
            "session_id": self.session_id,
            "timestamp": time.time(),
            "session_duration": round(time.time() - self.session_start, 1),
            "running": False,
            "instances": {},
            "screenshots_b64": {},
            "recent_logs": [],
            "recent_errors": [],
            "stats": {},
        }

        if coordinator is None:
            snapshot["recent_logs"] = self.get_recent_logs(100)
            snapshot["recent_errors"] = self.get_recent_errors(20)
            return snapshot

        snapshot["running"] = True
        snapshot["paused"] = coordinator._paused
        snapshot["stats"] = coordinator._get_stats_dict()

        # 实例详细状态
        for idx, agent in coordinator.agents.items():
            snapshot["instances"][idx] = {
                "index": idx,
                "group": agent.info.group.value,
                "role": agent.info.role.value,
                "state": agent.state.value,
                "state_duration": round(agent.fsm.state_duration, 1),
                "available_triggers": agent.fsm.get_available_triggers(),
                "is_error": agent.fsm.is_error_state(),
                "is_terminal": agent.fsm.is_terminal_state(),
                "error_msg": agent.info.error_msg,
                "nickname": agent.info.nickname,
                "game_id": agent.info.game_id,
            }

        # 所有实例的截图（base64 内嵌）
        async def take_one(idx, ctrl):
            try:
                img = await ctrl.screenshot()
                if img is None:
                    return idx, None
                # 缩小到 640x360 避免响应过大
                small = cv2.resize(img, (640, 360))
                _, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 60])
                return idx, base64.b64encode(buf).decode("ascii")
            except Exception as e:
                logger.error(f"截图实例 {idx} 失败: {e}")
                return idx, None

        tasks = [take_one(idx, ctrl) for idx, ctrl in coordinator.controllers.items()]
        results = await asyncio.gather(*tasks)
        for idx, b64 in results:
            if b64:
                snapshot["screenshots_b64"][idx] = b64

        # 最近日志和错误
        snapshot["recent_logs"] = self.get_recent_logs(100)
        snapshot["recent_errors"] = self.get_recent_errors(20)

        return snapshot


# 全局单例
_diag: Optional[DiagnosticManager] = None


def get_diagnostic() -> DiagnosticManager:
    global _diag
    if _diag is None:
        _diag = DiagnosticManager()
    return _diag

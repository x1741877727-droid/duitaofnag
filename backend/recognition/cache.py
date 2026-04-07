"""
LLM 结果缓存
相同弹窗只需 LLM 分析一次，后续直接复用
通过截图感知哈希（pHash）做相似度匹配
"""

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    """缓存条目"""
    phash: str                  # 感知哈希
    prompt_key: str             # 使用的 prompt 类型
    result: dict                # LLM 返回的结构化结果
    timestamp: float            # 缓存时间
    hit_count: int = 0          # 命中次数


class LLMCache:
    """
    LLM 视觉结果缓存
    - 使用感知哈希（pHash）比较截图相似度
    - 相似截图直接返回缓存结果，避免重复调用 LLM
    - 支持持久化到磁盘
    """

    def __init__(self, cache_dir: str = "", max_entries: int = 500,
                 hash_threshold: int = 8, ttl: int = 3600):
        """
        Args:
            cache_dir: 缓存持久化目录（空则仅内存缓存）
            max_entries: 最大缓存条目数
            hash_threshold: pHash 汉明距离阈值（<=此值视为相同图片）
            ttl: 缓存有效期（秒）
        """
        self.cache_dir = cache_dir
        self.max_entries = max_entries
        self.hash_threshold = hash_threshold
        self.ttl = ttl
        self._cache: dict[str, CacheEntry] = {}  # key: phash

        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)
            self._load_from_disk()

    def get(self, image: np.ndarray, prompt_key: str) -> Optional[dict]:
        """
        查询缓存
        Returns:
            缓存的 LLM 结果 dict，未命中返回 None
        """
        phash = self._compute_phash(image)
        now = time.time()

        # 精确匹配
        cache_key = f"{phash}:{prompt_key}"
        if cache_key in self._cache:
            entry = self._cache[cache_key]
            if now - entry.timestamp < self.ttl:
                entry.hit_count += 1
                logger.debug(f"缓存命中 (精确): {cache_key} hits={entry.hit_count}")
                return entry.result

        # 相似匹配：遍历同 prompt_key 的缓存找相似 hash
        for key, entry in self._cache.items():
            if not key.endswith(f":{prompt_key}"):
                continue
            if now - entry.timestamp >= self.ttl:
                continue

            distance = self._hamming_distance(phash, entry.phash)
            if distance <= self.hash_threshold:
                entry.hit_count += 1
                logger.debug(f"缓存命中 (相似 d={distance}): {key} hits={entry.hit_count}")
                return entry.result

        return None

    def put(self, image: np.ndarray, prompt_key: str, result: dict):
        """写入缓存"""
        phash = self._compute_phash(image)
        cache_key = f"{phash}:{prompt_key}"

        self._cache[cache_key] = CacheEntry(
            phash=phash,
            prompt_key=prompt_key,
            result=result,
            timestamp=time.time(),
        )

        # 淘汰过期或超出容量的条目
        self._evict()

        logger.debug(f"缓存写入: {cache_key}")

    def clear(self):
        """清空缓存"""
        self._cache.clear()
        logger.info("缓存已清空")

    def stats(self) -> dict:
        """缓存统计"""
        now = time.time()
        active = sum(1 for e in self._cache.values() if now - e.timestamp < self.ttl)
        total_hits = sum(e.hit_count for e in self._cache.values())
        return {
            "total_entries": len(self._cache),
            "active_entries": active,
            "total_hits": total_hits,
        }

    def save_to_disk(self):
        """持久化缓存到磁盘"""
        if not self.cache_dir:
            return

        data = {}
        for key, entry in self._cache.items():
            data[key] = {
                "phash": entry.phash,
                "prompt_key": entry.prompt_key,
                "result": entry.result,
                "timestamp": entry.timestamp,
                "hit_count": entry.hit_count,
            }

        filepath = os.path.join(self.cache_dir, "llm_cache.json")
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        logger.info(f"缓存已保存: {len(data)} 条")

    # --- 内部方法 ---

    def _compute_phash(self, image: np.ndarray, hash_size: int = 16) -> str:
        """
        计算感知哈希（pHash）
        缩小图片 → 灰度 → DCT → 取左上角低频 → 二值化
        """
        # 缩小到 hash_size x hash_size
        if len(image.shape) == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray = image

        resized = cv2.resize(gray, (hash_size, hash_size), interpolation=cv2.INTER_AREA)

        # DCT 变换
        dct = cv2.dct(np.float32(resized))

        # 取左上角 8x8（低频部分）
        low_freq = dct[:8, :8]

        # 中值二值化
        median = np.median(low_freq)
        bits = (low_freq > median).flatten()

        # 转为十六进制字符串
        hash_int = 0
        for bit in bits:
            hash_int = (hash_int << 1) | int(bit)
        return format(hash_int, f"0{8*8//4}x")

    def _hamming_distance(self, hash1: str, hash2: str) -> int:
        """计算两个哈希的汉明距离"""
        if len(hash1) != len(hash2):
            return 64  # 最大距离

        val1 = int(hash1, 16)
        val2 = int(hash2, 16)
        xor = val1 ^ val2
        return bin(xor).count("1")

    def _evict(self):
        """淘汰过期和超量的缓存条目"""
        now = time.time()

        # 删除过期条目
        expired = [k for k, e in self._cache.items() if now - e.timestamp >= self.ttl]
        for k in expired:
            del self._cache[k]

        # 超出容量时按 LRU（timestamp）淘汰
        if len(self._cache) > self.max_entries:
            sorted_keys = sorted(self._cache.keys(), key=lambda k: self._cache[k].timestamp)
            to_remove = len(self._cache) - self.max_entries
            for k in sorted_keys[:to_remove]:
                del self._cache[k]

    def _load_from_disk(self):
        """从磁盘加载缓存"""
        filepath = os.path.join(self.cache_dir, "llm_cache.json")
        if not os.path.exists(filepath):
            return

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)

            now = time.time()
            for key, entry_data in data.items():
                if now - entry_data["timestamp"] >= self.ttl:
                    continue  # 跳过过期条目
                self._cache[key] = CacheEntry(**entry_data)

            logger.info(f"从磁盘加载缓存: {len(self._cache)} 条")
        except Exception as e:
            logger.warning(f"加载缓存失败: {e}")

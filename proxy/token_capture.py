"""
QQ OAuth _Callback JSONP 解析与 token 存储模块。
"""

import re
import json
import threading
import os
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs


# 匹配 _Callback( {...} ) 或 _Callback({...})，允许额外空白
_CALLBACK_RE = re.compile(r'_Callback\s*\(\s*(\{.*?\})\s*\)', re.DOTALL)


def parse_qq_callback(text: str) -> dict | None:
    """
    从 QQ OAuth JSONP 响应文本中提取 token 信息。

    返回包含 openid, access_token, pay_token, pf, pfkey, expires_in 的字典，
    或在格式不符时返回 None。
    """
    if not text:
        return None

    m = _CALLBACK_RE.search(text)
    if not m:
        return None

    try:
        data = json.loads(m.group(1))
    except (json.JSONDecodeError, ValueError):
        return None

    # ret != 0 表示失败
    if data.get("ret") != 0:
        return None

    url = data.get("url", "")
    if not url:
        return None

    # parse_qs 会把值包装成列表，取第一个元素
    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=True)

    def _first(key: str) -> str:
        vals = params.get(key, [])
        return vals[0] if vals else ""

    result = {
        "openid": _first("openid"),
        "access_token": _first("access_token"),
        "pay_token": _first("pay_token"),
        "pf": _first("pf"),
        "pfkey": _first("pfkey"),
        "expires_in": _first("expires_in"),
    }

    # openid 和 access_token 是必须字段
    if not result["openid"] or not result["access_token"]:
        return None

    return result


def extract_callback_from_body(body: str) -> dict | None:
    """
    在 HTTP 响应 body 中搜索 _Callback JSONP，提取 token 信息。
    """
    return parse_qq_callback(body)


class TokenStore:
    """
    基于 JSON 文件的线程安全 token 存储。

    支持按 user_id 存取，自动记录 captured_at 时间戳。
    """

    def __init__(self, path: str):
        self._path = path
        self._lock = threading.Lock()

    def _read(self) -> dict:
        if not os.path.exists(self._path):
            return {}
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

    def _write(self, data: dict) -> None:
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def save(self, user_id: str, tokens: dict) -> None:
        """保存或更新指定用户的 token，自动添加 captured_at 时间戳。"""
        with self._lock:
            data = self._read()
            entry = dict(tokens)
            entry["captured_at"] = datetime.now(timezone.utc).isoformat()
            data[user_id] = entry
            self._write(data)

    def get(self, user_id: str) -> dict | None:
        """获取指定用户的 token，不存在时返回 None。"""
        with self._lock:
            data = self._read()
        return data.get(user_id)

    def list_all(self) -> dict:
        """返回所有用户的 token 字典（{user_id: tokens}）。"""
        with self._lock:
            return self._read()

    def delete(self, user_id: str) -> bool:
        """删除指定用户的 token，成功返回 True，不存在返回 False。"""
        with self._lock:
            data = self._read()
            if user_id not in data:
                return False
            del data[user_id]
            self._write(data)
            return True

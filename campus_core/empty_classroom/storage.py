"""空教室缓存存储模块。

管理 data/shared/empty_classroom_cache/ 下的缓存文件读写。

目录结构：
    schedule_html/<term>/<campus>/<building>.html
    parsed_schedule/<term>/<campus>/<building>.json
    query_cache/<hash>.json
    locks/<term>_<campus>_<building>.lock

安全约束：缓存内容不得包含 Cookie、JSESSIONID、CASTGC、密码等敏感字段。
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..storage_paths import ensure_dir, get_empty_classroom_cache_dir

# 敏感字段黑名单（写入前检查并剔除）
_SENSITIVE_KEYWORDS = {s.upper() for s in {"CASTGC", "JSESSIONID", "cookie", "Cookie", "password", "token", "TGC", "bearer"}}


def _cache_dir() -> Path:
    return ensure_dir(get_empty_classroom_cache_dir())


def _schedule_html_dir() -> Path:
    return ensure_dir(_cache_dir() / "schedule_html")


def _parsed_schedule_dir() -> Path:
    return ensure_dir(_cache_dir() / "parsed_schedule")


def _query_cache_dir() -> Path:
    return ensure_dir(_cache_dir() / "query_cache")


def _locks_dir() -> Path:
    return ensure_dir(_cache_dir() / "locks")


def _sanitize(data: dict[str, Any]) -> dict[str, Any]:
    """剔除敏感字段。递归检查 dict 的 key，剔除含敏感关键词的字段。"""

    def _clean(d: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for k, v in d.items():
            # 检查 key 是否敏感
            key_upper = k.upper()
            if any(s in key_upper for s in _SENSITIVE_KEYWORDS):
                continue
            if isinstance(v, dict):
                result[k] = _clean(v)
            elif isinstance(v, list):
                result[k] = [
                    _clean(item) if isinstance(item, dict) else item for item in v
                ]
            else:
                # 检查字符串值是否像 Cookie
                if isinstance(v, str) and any(
                    s.lower() in v.lower() for s in {"castgc", "jsessionid", "bearer"}
                ):
                    continue
                result[k] = v
        return result

    return _clean(data)


def _term_path_segment(term_code: str) -> str:
    """将学期代码转为安全的路径段。"""
    return term_code.replace("/", "_").replace("\\", "_").replace(",", "_")


def _schedule_html_path(term_code: str, campus_code: str, building_code: str) -> Path:
    return (
        _schedule_html_dir()
        / _term_path_segment(term_code)
        / campus_code
        / f"{building_code}.html"
    )


def _parsed_schedule_path(term_code: str, campus_code: str, building_code: str) -> Path:
    return (
        _parsed_schedule_dir()
        / _term_path_segment(term_code)
        / campus_code
        / f"{building_code}.json"
    )


def _query_cache_path(query_hash: str) -> Path:
    return _query_cache_dir() / f"{query_hash}.json"


def _compute_query_hash(**params: Any) -> str:
    """计算查询参数的哈希值作为缓存键。"""
    raw = json.dumps(params, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ── HTML 缓存 ──────────────────────────────────────────────


def save_schedule_html(
    html_text: str,
    term_code: str,
    campus_code: str,
    building_code: str,
) -> Path:
    """保存原始 HTML 到缓存。"""
    path = _schedule_html_path(term_code, campus_code, building_code)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html_text, encoding="utf-8")
    return path


def load_schedule_html(
    term_code: str,
    campus_code: str,
    building_code: str,
) -> str:
    """读取缓存的原始 HTML。"""
    path = _schedule_html_path(term_code, campus_code, building_code)
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def get_schedule_html_age(
    term_code: str,
    campus_code: str,
    building_code: str,
) -> float:
    """获取 HTML 缓存文件的年龄（秒）。不存在返回无限大。"""
    path = _schedule_html_path(term_code, campus_code, building_code)
    if path.exists():
        return time.time() - path.stat().st_mtime
    return float("inf")


# ── 解析后课表缓存 ─────────────────────────────────────────


def save_parsed_schedule(
    data: dict[str, Any],
    term_code: str,
    campus_code: str,
    building_code: str,
) -> Path:
    """保存解析后的课表 JSON 到缓存（自动脱敏）。"""
    path = _parsed_schedule_path(term_code, campus_code, building_code)
    path.parent.mkdir(parents=True, exist_ok=True)
    clean = _sanitize(data)
    path.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_parsed_schedule(
    term_code: str,
    campus_code: str,
    building_code: str,
) -> dict[str, Any]:
    """读取缓存的解析后课表。"""
    path = _parsed_schedule_path(term_code, campus_code, building_code)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def get_parsed_schedule_age(
    term_code: str,
    campus_code: str,
    building_code: str,
) -> float:
    """获取解析后课表缓存的年龄（秒）。"""
    path = _parsed_schedule_path(term_code, campus_code, building_code)
    if path.exists():
        return time.time() - path.stat().st_mtime
    return float("inf")


# ── 查询结果缓存 ───────────────────────────────────────────


def save_query_cache(query_hash: str, data: dict[str, Any]) -> Path:
    """保存查询结果到缓存。"""
    path = _query_cache_path(query_hash)
    path.parent.mkdir(parents=True, exist_ok=True)
    clean = _sanitize(data)
    clean["_cached_at"] = datetime.now().isoformat()
    path.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_query_cache(query_hash: str, ttl_seconds: int = 300) -> dict[str, Any] | None:
    """读取查询结果缓存。TTL 过期返回 None。"""
    path = _query_cache_path(query_hash)
    if not path.exists():
        return None

    age = time.time() - path.stat().st_mtime
    if age > ttl_seconds:
        return None

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def get_query_cache_age(query_hash: str) -> float:
    """获取查询缓存年龄（秒）。"""
    path = _query_cache_path(query_hash)
    if path.exists():
        return time.time() - path.stat().st_mtime
    return float("inf")


# ── 锁目录 ─────────────────────────────────────────────────


def get_locks_dir() -> Path:
    """获取锁文件目录。"""
    return _locks_dir()


# ── 工具函数 ───────────────────────────────────────────────


def compute_query_hash(
    term_code: str,
    week: int,
    day_of_week: int,
    period: int,
    campus_code: str = "",
    building_code: str = "",
    type_code: str = "",
    min_capacity: int = 0,
    keyword: str = "",
) -> str:
    """计算查询参数的哈希值。"""
    return _compute_query_hash(
        term_code=term_code,
        week=week,
        day_of_week=day_of_week,
        period=period,
        campus_code=campus_code,
        building_code=building_code,
        type_code=type_code,
        min_capacity=min_capacity,
        keyword=keyword,
    )


def format_iso(ts: float | None = None) -> str:
    """格式化为 ISO 时间字符串。"""
    if ts is None:
        ts = time.time()
    return datetime.fromtimestamp(ts).isoformat()

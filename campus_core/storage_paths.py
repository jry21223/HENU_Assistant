"""共享路径管理模块。

提供 data/shared/（公共数据）和 data/users/<id>/（用户隔离）的路径函数。
MCP / Agent Skill 默认用户为 "local"，Langbot 可按 QQ 传入 user_key。

安全约束：共享路径下不存放 Cookie、密码、JSESSIONID、CASTGC、token。
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

# 可由 Langbot 的 _activate_user_storage() 在运行时覆盖
_BASE_DIR: Path | None = None


def _get_base_dir() -> Path:
    """获取项目根目录。

    默认为 campus_core/ 的父目录（即 mcp-server/ 或 agent-skill/ 或 langbot-plugin/）。
    可通过 set_base_dir() 在运行时覆盖。
    """
    if _BASE_DIR is not None:
        return _BASE_DIR
    return Path(__file__).resolve().parent.parent


def set_base_dir(path: Path) -> None:
    """运行时覆盖项目根目录（Langbot 用）。"""
    global _BASE_DIR
    _BASE_DIR = Path(path)


@contextmanager
def activated_base_dir(path: Path) -> Iterator[None]:
    """Temporarily bind shared/user registry paths to one runtime root."""
    global _BASE_DIR
    previous = _BASE_DIR
    _BASE_DIR = Path(path)
    try:
        yield
    finally:
        _BASE_DIR = previous


def get_shared_data_dir() -> Path:
    """公共共享数据目录：data/shared/"""
    return _get_base_dir() / "data" / "shared"


def get_user_data_dir(user_key: str | None = None) -> Path:
    """用户隔离数据目录：data/users/<key>/

    Args:
        user_key: 用户标识。MCP/Agent Skill 默认为 "local"，Langbot 传入 QQ 号。
    """
    key = user_key or "local"
    return _get_base_dir() / "data" / "users" / key


def get_empty_classroom_cache_dir() -> Path:
    """空教室共享缓存目录：data/shared/empty_classroom_cache/"""
    return get_shared_data_dir() / "empty_classroom_cache"


def get_resource_registry_dir() -> Path:
    """全局资源编号映射目录：data/shared/resource_registry/"""
    return get_shared_data_dir() / "resource_registry"


def ensure_dir(path: Path) -> Path:
    """确保目录存在并返回路径。"""
    path.mkdir(parents=True, exist_ok=True)
    return path

"""资源 registry 存储层。

读写 data/shared/resource_registry/ 下的 JSON 文件：
    resources.json      — 全量资源记录
    aliases.json        — 别名→resource_id 反向索引
    source_mappings.json — 上游系统 ID → resource_id 映射
    sync_state.json     — 同步状态记录
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from ..storage_paths import ensure_dir, get_resource_registry_dir

# ── 路径 ──────────────────────────────────────────────────


def _registry_dir() -> Path:
    return ensure_dir(get_resource_registry_dir())


def _resources_path() -> Path:
    return _registry_dir() / "resources.json"


def _aliases_path() -> Path:
    return _registry_dir() / "aliases.json"


def _source_mappings_path() -> Path:
    return _registry_dir() / "source_mappings.json"


def _sync_state_path() -> Path:
    return _registry_dir() / "sync_state.json"


# ── 底层读写 ──────────────────────────────────────────────


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ── Resources ─────────────────────────────────────────────


def load_resources() -> dict[str, dict[str, Any]]:
    """加载全量资源记录。返回 {resource_id: record_dict}。"""
    data = _read_json(_resources_path())
    if isinstance(data, dict):
        return data
    return {}


def save_resources(resources: dict[str, dict[str, Any]]) -> None:
    """保存全量资源记录。"""
    _write_json(_resources_path(), resources)


def upsert_resource_record(record: dict[str, Any]) -> None:
    """增量写入一条资源记录。"""
    resources = load_resources()
    rid = record.get("resourceId", record.get("resource_id", ""))
    if not rid:
        return
    record["updatedAt"] = datetime.now().isoformat()
    resources[rid] = record
    save_resources(resources)


def get_resource_record(resource_id: str) -> dict[str, Any] | None:
    """读取单条资源记录。"""
    resources = load_resources()
    return resources.get(resource_id)


def delete_resource_record(resource_id: str) -> bool:
    """删除单条资源记录。"""
    resources = load_resources()
    if resource_id in resources:
        del resources[resource_id]
        save_resources(resources)
        return True
    return False


# ── Alias Index ───────────────────────────────────────────


def load_alias_index() -> dict[str, list[str]]:
    """加载别名反向索引。返回 {normalized_alias: [resource_id, ...]}。"""
    data = _read_json(_aliases_path())
    if isinstance(data, dict):
        return {k: v for k, v in data.items() if isinstance(v, list)}
    return {}


def save_alias_index(index: dict[str, list[str]]) -> None:
    """保存别名反向索引。"""
    _write_json(_aliases_path(), index)


def add_alias_entry(alias: str, resource_id: str) -> None:
    """添加一条别名映射。"""
    index = load_alias_index()
    key = alias.strip().lower()
    if key not in index:
        index[key] = []
    if resource_id not in index[key]:
        index[key].append(resource_id)
    save_alias_index(index)


def lookup_by_alias(alias: str) -> list[str]:
    """通过别名查找 resource_id 列表。"""
    index = load_alias_index()
    key = alias.strip().lower()
    return index.get(key, [])


# ── Source Mappings ───────────────────────────────────────


def load_source_mappings() -> dict[str, dict[str, str]]:
    """加载上游系统 ID 映射。返回 {system: {source_id: resource_id}}。"""
    data = _read_json(_source_mappings_path())
    if isinstance(data, dict):
        return data
    return {}


def save_source_mappings(mappings: dict[str, dict[str, str]]) -> None:
    """保存上游系统 ID 映射。"""
    _write_json(_source_mappings_path(), mappings)


def add_source_mapping(system: str, source_id: str, resource_id: str) -> None:
    """添加一条上游系统 ID 映射。"""
    mappings = load_source_mappings()
    if system not in mappings:
        mappings[system] = {}
    mappings[system][source_id] = resource_id
    save_source_mappings(mappings)


def resolve_source_id(system: str, source_id: str) -> str:
    """通过上游系统 ID 查找 resource_id。"""
    mappings = load_source_mappings()
    return mappings.get(system, {}).get(source_id, "")


# ── Sync State ────────────────────────────────────────────


def load_sync_state() -> dict[str, Any]:
    """加载同步状态。"""
    data = _read_json(_sync_state_path())
    if isinstance(data, dict):
        return data
    return {}


def save_sync_state(state: dict[str, Any]) -> None:
    """保存同步状态。"""
    _write_json(_sync_state_path(), state)


def update_sync_state(scope: str, status: str, detail: dict[str, Any] | None = None) -> None:
    """更新某个 scope 的同步状态。"""
    state = load_sync_state()
    state[scope] = {
        "status": status,
        "updatedAt": datetime.now().isoformat(),
        "detail": detail or {},
    }
    save_sync_state(state)


# ── 安全 ──────────────────────────────────────────────────

_SENSITIVE_KEYWORDS = {s.upper() for s in {"CASTGC", "JSESSIONID", "cookie", "password", "token", "TGC", "bearer"}}


def _check_no_sensitive(data: dict[str, Any]) -> bool:
    """检查数据中是否包含敏感关键词（递归）。"""
    data_str = json.dumps(data, ensure_ascii=False).upper()
    for kw in _SENSITIVE_KEYWORDS:
        if kw in data_str:
            return False
    return True


def safe_save_resources(resources: dict[str, dict[str, Any]]) -> bool:
    """安全保存资源记录，拒绝含敏感关键词的数据。"""
    if not _check_no_sensitive(resources):
        return False
    save_resources(resources)
    return True

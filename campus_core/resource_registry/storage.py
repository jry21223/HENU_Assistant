"""Crash-consistent resource registry storage.

The authoritative registry is one atomically replaced JSON snapshot containing
resources, aliases, source mappings and sync state. Legacy split JSON files are
read only as a migration source.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterable
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from campus_core import atomic_io
from campus_core.empty_classroom.lock import FileLock

from ..storage_paths import ensure_dir, get_resource_registry_dir


_REGISTRY_PROCESS_LOCK = threading.RLock()
_REGISTRY_TRANSACTION_STATE = threading.local()
_REGISTRY_LOCK_TIMEOUT_SECONDS = 30.0
_STATE_VERSION = 1


class RegistryStateError(RuntimeError):
    """The canonical registry snapshot exists but cannot be trusted."""


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


def _state_path() -> Path:
    # Derive this from the legacy resources path so existing isolated test and
    # deployment overrides keep the entire registry in the same directory.
    return _resources_path().parent / "registry_state.json"


@contextmanager
def registry_transaction() -> Iterator[None]:
    """Serialize every registry read/write across threads and local processes."""
    with _REGISTRY_PROCESS_LOCK:
        depth = int(getattr(_REGISTRY_TRANSACTION_STATE, "depth", 0))
        if depth:
            _REGISTRY_TRANSACTION_STATE.depth = depth + 1
            try:
                yield
            finally:
                _REGISTRY_TRANSACTION_STATE.depth = depth
            return

        lock = FileLock(
            _registry_dir() / ".registry.lock",
            timeout=_REGISTRY_LOCK_TIMEOUT_SECONDS,
        )
        if not lock.acquire():
            raise TimeoutError("resource registry lock acquisition timed out")
        _REGISTRY_TRANSACTION_STATE.depth = 1
        try:
            yield
        finally:
            _REGISTRY_TRANSACTION_STATE.depth = 0
            lock.release()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise RegistryStateError(f"cannot read legacy registry file {path.name}: {exc}") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RegistryStateError(f"cannot parse legacy registry file {path.name}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RegistryStateError(f"legacy registry file {path.name} must be a JSON object")
    return payload


def _write_json(path: Path, data: dict[str, Any]) -> None:
    atomic_io.atomic_write_json(path, data)


def _empty_state() -> dict[str, Any]:
    return {
        "version": _STATE_VERSION,
        "resources": {},
        "aliases": {},
        "source_mappings": {},
        "sync_state": {},
    }


def _normalized_state(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("version") != _STATE_VERSION:
        raise RegistryStateError(
            f"unsupported canonical registry version: {payload.get('version')!r}"
        )
    state = _empty_state()
    for key in ("resources", "aliases", "source_mappings", "sync_state"):
        value = payload.get(key)
        if not isinstance(value, dict):
            raise RegistryStateError(f"canonical registry field {key!r} must be an object")
        state[key] = value
    return state


def _read_canonical_state(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RegistryStateError(f"cannot read canonical registry state: {exc}") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RegistryStateError(f"cannot parse canonical registry state: {exc}") from exc
    if not isinstance(payload, dict):
        raise RegistryStateError("canonical registry state must be a JSON object")
    return _normalized_state(payload)


def _load_state_unlocked() -> dict[str, Any]:
    state_path = _state_path()
    if state_path.exists():
        return _read_canonical_state(state_path)

    # One-way compatibility with installations created before 2.1.0. The
    # first mutation writes all four domains into the canonical snapshot.
    state = _empty_state()
    state["resources"] = _read_json(_resources_path())
    state["aliases"] = _read_json(_aliases_path())
    state["source_mappings"] = _read_json(_source_mappings_path())
    state["sync_state"] = _read_json(_sync_state_path())
    return state


def _remove_resource_indexes(state: dict[str, Any], resource_id: str) -> None:
    for alias, values in list(state["aliases"].items()):
        if not isinstance(values, list):
            continue
        retained = [value for value in values if value != resource_id]
        if retained:
            state["aliases"][alias] = retained
        else:
            del state["aliases"][alias]
    for system, mapping in list(state["source_mappings"].items()):
        if not isinstance(mapping, dict):
            continue
        for source_id, mapped_id in list(mapping.items()):
            if mapped_id == resource_id:
                del mapping[source_id]
        if not mapping:
            del state["source_mappings"][system]


def _save_state_unlocked(state: dict[str, Any]) -> None:
    normalized = _empty_state()
    for key in ("resources", "aliases", "source_mappings", "sync_state"):
        value = state.get(key)
        normalized[key] = value if isinstance(value, dict) else {}
    _write_json(_state_path(), normalized)


def load_resources() -> dict[str, dict[str, Any]]:
    """Load all resource records."""
    with registry_transaction():
        resources = _load_state_unlocked()["resources"]
        return dict(resources)


def save_resources(resources: dict[str, dict[str, Any]]) -> None:
    """Replace all resource records without disturbing the indexes."""
    with registry_transaction():
        state = _load_state_unlocked()
        state["resources"] = dict(resources)
        _save_state_unlocked(state)


def upsert_resource_record(record: dict[str, Any]) -> None:
    """Insert or replace one resource record."""
    with registry_transaction():
        rid = str(record.get("resourceId", record.get("resource_id", "")) or "")
        if not rid:
            return
        state = _load_state_unlocked()
        _remove_resource_indexes(state, rid)
        stored = dict(record)
        stored["updatedAt"] = datetime.now().isoformat()
        state["resources"][rid] = stored
        _save_state_unlocked(state)


def upsert_resource_bundle(
    record: dict[str, Any],
    *,
    aliases: Iterable[str] = (),
    source_system: str = "",
    source_id: str = "",
) -> None:
    """Commit a resource and every derived index in one atomic snapshot."""
    with registry_transaction():
        rid = str(record.get("resourceId", record.get("resource_id", "")) or "")
        if not rid:
            return
        state = _load_state_unlocked()
        _remove_resource_indexes(state, rid)
        stored = dict(record)
        stored["updatedAt"] = stored.get("updatedAt") or datetime.now().isoformat()
        state["resources"][rid] = stored

        alias_index = state["aliases"]
        for alias in aliases:
            key = str(alias or "").strip().lower()
            if not key:
                continue
            values = alias_index.setdefault(key, [])
            if isinstance(values, list) and rid not in values:
                values.append(rid)

        system = str(source_system or "").strip()
        upstream_id = str(source_id or "").strip()
        if system and upstream_id:
            system_mapping = state["source_mappings"].setdefault(system, {})
            if not isinstance(system_mapping, dict):
                system_mapping = {}
                state["source_mappings"][system] = system_mapping
            system_mapping[upstream_id] = rid

        _save_state_unlocked(state)


def get_resource_record(resource_id: str) -> dict[str, Any] | None:
    with registry_transaction():
        record = _load_state_unlocked()["resources"].get(resource_id)
        return dict(record) if isinstance(record, dict) else None


def delete_resource_record(resource_id: str) -> bool:
    with registry_transaction():
        state = _load_state_unlocked()
        if resource_id not in state["resources"]:
            return False
        del state["resources"][resource_id]
        _remove_resource_indexes(state, resource_id)
        _save_state_unlocked(state)
        return True


def load_alias_index() -> dict[str, list[str]]:
    with registry_transaction():
        index = _load_state_unlocked()["aliases"]
        return {
            key: list(values)
            for key, values in index.items()
            if isinstance(values, list)
        }


def save_alias_index(index: dict[str, list[str]]) -> None:
    with registry_transaction():
        state = _load_state_unlocked()
        state["aliases"] = {key: list(values) for key, values in index.items()}
        _save_state_unlocked(state)


def add_alias_entry(alias: str, resource_id: str) -> None:
    with registry_transaction():
        state = _load_state_unlocked()
        key = alias.strip().lower()
        values = state["aliases"].setdefault(key, [])
        if isinstance(values, list) and resource_id not in values:
            values.append(resource_id)
        _save_state_unlocked(state)


def lookup_by_alias(alias: str) -> list[str]:
    with registry_transaction():
        values = _load_state_unlocked()["aliases"].get(alias.strip().lower(), [])
        return list(values) if isinstance(values, list) else []


def load_source_mappings() -> dict[str, dict[str, str]]:
    with registry_transaction():
        mappings = _load_state_unlocked()["source_mappings"]
        return {
            system: dict(values)
            for system, values in mappings.items()
            if isinstance(values, dict)
        }


def save_source_mappings(mappings: dict[str, dict[str, str]]) -> None:
    with registry_transaction():
        state = _load_state_unlocked()
        state["source_mappings"] = {
            system: dict(values) for system, values in mappings.items()
        }
        _save_state_unlocked(state)


def add_source_mapping(system: str, source_id: str, resource_id: str) -> None:
    with registry_transaction():
        state = _load_state_unlocked()
        mapping = state["source_mappings"].setdefault(system, {})
        if not isinstance(mapping, dict):
            mapping = {}
            state["source_mappings"][system] = mapping
        mapping[source_id] = resource_id
        _save_state_unlocked(state)


def resolve_source_id(system: str, source_id: str) -> str:
    with registry_transaction():
        mapping = _load_state_unlocked()["source_mappings"].get(system, {})
        return str(mapping.get(source_id, "")) if isinstance(mapping, dict) else ""


def load_sync_state() -> dict[str, Any]:
    with registry_transaction():
        return dict(_load_state_unlocked()["sync_state"])


def save_sync_state(state_value: dict[str, Any]) -> None:
    with registry_transaction():
        state = _load_state_unlocked()
        state["sync_state"] = dict(state_value)
        _save_state_unlocked(state)


def update_sync_state(
    scope: str,
    status: str,
    detail: dict[str, Any] | None = None,
) -> None:
    with registry_transaction():
        state = _load_state_unlocked()
        state["sync_state"][scope] = {
            "status": status,
            "updatedAt": datetime.now().isoformat(),
            "detail": detail or {},
        }
        _save_state_unlocked(state)


_SENSITIVE_KEYWORDS = {
    value.upper()
    for value in {"CASTGC", "JSESSIONID", "cookie", "password", "token", "TGC", "bearer"}
}


def _check_no_sensitive(data: dict[str, Any]) -> bool:
    data_text = json.dumps(data, ensure_ascii=False).upper()
    return not any(keyword in data_text for keyword in _SENSITIVE_KEYWORDS)


def safe_save_resources(resources: dict[str, dict[str, Any]]) -> bool:
    if not _check_no_sensitive(resources):
        return False
    save_resources(resources)
    return True

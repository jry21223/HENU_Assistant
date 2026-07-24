"""Fail-closed bridge between LangBot Storage and file-based campus modules.

Each adapter owns a request-scoped staging directory. A per-user lock covers the
load/execute/save lifecycle, changed files only are persisted, and optimistic
checks reject stale overwrites. Storage read failures are never converted into
empty JSON because doing so can erase an account on the subsequent save.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from langbot_plugin.api.definition.plugin import BasePlugin

logger = logging.getLogger(__name__)

PREFIX_PROFILE = "user:{}:profile"
PREFIX_XK_COOKIE = "user:{}:xk_cookie"
PREFIX_LIBRARY_COOKIE = "user:{}:library_cookie"
PREFIX_SEMINAR_TASK = "user:{}:seminar_task"
PREFIX_SCHEDULE = "user:{}:schedule"
PREFIX_YUNFZ_TOKEN = "user:{}:yunfz_token"
PREFIX_CAS_COOKIE = "user:{}:cas_cookie"
PREFIX_COURSE_MONITOR_CONFIG = "user:{}:course_monitor_config"
PREFIX_COURSE_MONITOR_STATE = "user:{}:course_monitor_state"
# xiqueer request data contains a Cookie and therefore must be user-private.
PREFIX_USER_XIQUEER = "user:{}:xiqueer"
PREFIX_SHARED_PERIOD_TIME = "shared:period_time"
PREFIX_SHARED_CALIBRATION = "shared:calibration"
# Compatibility alias. New code must not persist credentials under this key.
PREFIX_SHARED_XIQUEER = "shared:xiqueer"

SHARED_PERIOD_TIME_FILE = "period_time_config.json"
SHARED_CALIBRATION_FILE = "period_time_calibration_state.json"
XIQUEER_FILE = "xiqueer_period_time_request.json"
SHARED_XIQUEER_FILE = XIQUEER_FILE


class StorageAdapterError(RuntimeError):
    """Base class for storage materialization errors."""


class StorageLoadError(StorageAdapterError):
    pass


class StorageConflictError(StorageAdapterError):
    pass


@dataclass(frozen=True)
class UserStoragePaths:
    user_root: Path
    profile_file: Path
    xk_cookie_file: Path
    library_cookie_file: Path
    seminar_signin_task_file: Path
    schedule_file: Path
    yunfz_token_file: Path
    cas_cookie_file: Path
    course_monitor_config_file: Path
    course_monitor_state_file: Path
    xiqueer_request_file: Path
    output_dir: Path
    shared_data_dir: Path


@dataclass(frozen=True)
class _StorageFile:
    storage_key: str
    path: Path
    shared: bool = False


class PluginStorageAdapter:
    """Materialize one user's Storage records for a single request."""

    # Tests may set this to force staging under a temporary parent.
    _shared_temp_dir: Path | None = None
    _user_locks: dict[tuple[int, str], asyncio.Lock] = {}
    _shared_locks: dict[int, asyncio.Lock] = {}

    def __init__(self, plugin: BasePlugin, storage_key: str):
        normalized = re.sub(
            r"[^0-9A-Za-z._-]+", "_", str(storage_key or "")
        ).strip("._-")
        if not normalized or normalized == "unknown":
            raise ValueError("有效的用户 storage_key 不能为空")
        self._plugin = plugin
        self._storage_key = normalized
        self._paths: UserStoragePaths | None = None
        self._files: list[_StorageFile] = []
        self._original_bytes: dict[str, bytes] = {}
        self._transaction_lock: asyncio.Lock | None = None
        self._staging_root: Path | None = None

    def _key(self, prefix_template: str) -> str:
        return prefix_template.format(self._storage_key)

    @classmethod
    def _lock_for_user(cls, storage_key: str) -> asyncio.Lock:
        loop_id = id(asyncio.get_running_loop())
        return cls._user_locks.setdefault((loop_id, storage_key), asyncio.Lock())

    @classmethod
    def _shared_lock(cls) -> asyncio.Lock:
        loop_id = id(asyncio.get_running_loop())
        return cls._shared_locks.setdefault(loop_id, asyncio.Lock())

    def _build_paths(self) -> UserStoragePaths:
        parent = self._shared_temp_dir
        if parent is not None:
            parent.mkdir(parents=True, exist_ok=True)
        staging = Path(
            tempfile.mkdtemp(
                prefix=f"henu_plugin_{self._storage_key}_",
                dir=str(parent) if parent is not None else None,
            )
        )
        self._staging_root = staging
        user_root = staging / "user"
        output_dir = user_root / "output"
        shared_data_dir = staging / "shared"
        output_dir.mkdir(parents=True, exist_ok=True)
        shared_data_dir.mkdir(parents=True, exist_ok=True)
        return UserStoragePaths(
            user_root=user_root,
            profile_file=user_root / "profile.json",
            xk_cookie_file=user_root / "xk_cookies.json",
            library_cookie_file=user_root / "library_cookies.json",
            seminar_signin_task_file=user_root / "seminar_signin_tasks.json",
            schedule_file=user_root / "schedule_clean_latest.json",
            yunfz_token_file=user_root / "yunfz_token.json",
            cas_cookie_file=user_root / "cas_cookies.json",
            course_monitor_config_file=output_dir / "course_monitor_config.json",
            course_monitor_state_file=output_dir / "course_monitor_state.json",
            xiqueer_request_file=user_root / XIQUEER_FILE,
            output_dir=output_dir,
            shared_data_dir=shared_data_dir,
        )

    def _file_specs(self, paths: UserStoragePaths) -> list[_StorageFile]:
        return [
            _StorageFile(self._key(PREFIX_PROFILE), paths.profile_file),
            _StorageFile(self._key(PREFIX_XK_COOKIE), paths.xk_cookie_file),
            _StorageFile(
                self._key(PREFIX_LIBRARY_COOKIE), paths.library_cookie_file
            ),
            _StorageFile(
                self._key(PREFIX_SEMINAR_TASK), paths.seminar_signin_task_file
            ),
            _StorageFile(self._key(PREFIX_SCHEDULE), paths.schedule_file),
            _StorageFile(self._key(PREFIX_YUNFZ_TOKEN), paths.yunfz_token_file),
            _StorageFile(self._key(PREFIX_CAS_COOKIE), paths.cas_cookie_file),
            _StorageFile(
                self._key(PREFIX_COURSE_MONITOR_CONFIG),
                paths.course_monitor_config_file,
            ),
            _StorageFile(
                self._key(PREFIX_COURSE_MONITOR_STATE),
                paths.course_monitor_state_file,
            ),
            _StorageFile(self._key(PREFIX_USER_XIQUEER), paths.xiqueer_request_file),
            _StorageFile(
                PREFIX_SHARED_PERIOD_TIME,
                paths.shared_data_dir / SHARED_PERIOD_TIME_FILE,
                shared=True,
            ),
            _StorageFile(
                PREFIX_SHARED_CALIBRATION,
                paths.shared_data_dir / SHARED_CALIBRATION_FILE,
                shared=True,
            ),
        ]

    async def load_all(self) -> UserStoragePaths:
        if self._paths is not None:
            raise RuntimeError("同一个 StorageAdapter 不能重复 load_all")

        lock = self._lock_for_user(self._storage_key)
        await lock.acquire()
        self._transaction_lock = lock
        try:
            self._paths = self._build_paths()
            self._files = self._file_specs(self._paths)
            for spec in self._files:
                if spec.shared:
                    async with self._shared_lock():
                        payload = await self._read_storage(spec.storage_key)
                else:
                    payload = await self._read_storage(spec.storage_key)
                self._validate_json_payload(spec.storage_key, payload)
                spec.path.parent.mkdir(parents=True, exist_ok=True)
                spec.path.write_bytes(payload)
                self._original_bytes[spec.storage_key] = payload
            return self._paths
        except Exception:
            self._release_transaction()
            self._cleanup()
            self._paths = None
            raise

    async def save_all(self) -> None:
        if self._paths is None:
            return
        try:
            changed: list[tuple[_StorageFile, bytes]] = []
            for spec in self._files:
                if not spec.path.exists():
                    continue
                payload = spec.path.read_bytes()
                self._validate_json_payload(spec.storage_key, payload)
                if payload != self._original_bytes.get(spec.storage_key, b"{}"):
                    changed.append((spec, payload))

            # Optimistic conflict detection also protects separate LangBot workers.
            for spec, _ in changed:
                lock = self._shared_lock() if spec.shared else None
                if lock is not None:
                    async with lock:
                        current = await self._read_storage(spec.storage_key)
                else:
                    current = await self._read_storage(spec.storage_key)
                expected = self._original_bytes.get(spec.storage_key, b"{}")
                if current != expected:
                    raise StorageConflictError(
                        f"Storage 已被其他请求修改，拒绝覆盖: {spec.storage_key}"
                    )

            for spec, payload in changed:
                lock = self._shared_lock() if spec.shared else None
                if lock is not None:
                    async with lock:
                        await self._write_storage(spec.storage_key, payload)
                else:
                    await self._write_storage(spec.storage_key, payload)
                self._original_bytes[spec.storage_key] = payload
        finally:
            self._release_transaction()
            self._cleanup()
            self._paths = None

    async def abort(self) -> None:
        self._release_transaction()
        self._cleanup()
        self._paths = None

    async def _read_storage(self, storage_key: str) -> bytes:
        try:
            data = await self._plugin.get_plugin_storage(storage_key)
        except Exception as exc:
            if self._is_not_found(exc):
                return b"{}"
            raise StorageLoadError(
                f"读取 LangBot Storage 失败 ({storage_key}): {exc}"
            ) from exc
        if data in (None, b"", ""):
            return b"{}"
        if isinstance(data, str):
            return data.encode("utf-8")
        if isinstance(data, (bytes, bytearray)):
            return bytes(data)
        raise StorageLoadError(
            f"LangBot Storage 返回了不支持的数据类型 ({storage_key}): "
            f"{type(data).__name__}"
        )

    async def _write_storage(self, storage_key: str, payload: bytes) -> None:
        try:
            await self._plugin.set_plugin_storage(storage_key, payload)
        except Exception as exc:
            raise StorageAdapterError(
                f"写入 LangBot Storage 失败 ({storage_key}): {exc}"
            ) from exc
        logger.debug("Saved %d bytes to Storage: %s", len(payload), storage_key)

    @staticmethod
    def _validate_json_payload(storage_key: str, payload: bytes) -> None:
        try:
            json.loads(payload.decode("utf-8"))
        except Exception as exc:
            raise StorageLoadError(
                f"Storage JSON 无效 ({storage_key}): {exc}"
            ) from exc

    @staticmethod
    def _is_not_found(exc: Exception) -> bool:
        if isinstance(exc, (KeyError, FileNotFoundError)):
            return True
        text = str(exc).lower()
        return any(
            marker in text
            for marker in ("not found", "does not exist", "404", "no such key")
        )

    def _release_transaction(self) -> None:
        lock = self._transaction_lock
        self._transaction_lock = None
        if lock is not None and lock.locked():
            lock.release()

    def _cleanup(self) -> None:
        root = self._staging_root
        self._staging_root = None
        if root is not None:
            shutil.rmtree(root, ignore_errors=True)

    # Compatibility helpers retained for older callers.
    async def load_shared_period_time(self, file_path: Path) -> None:
        file_path.write_bytes(await self._read_storage(PREFIX_SHARED_PERIOD_TIME))

    async def save_shared_period_time(self, file_path: Path) -> None:
        await self._write_storage(PREFIX_SHARED_PERIOD_TIME, file_path.read_bytes())

    async def load_shared_calibration(self, file_path: Path) -> None:
        file_path.write_bytes(await self._read_storage(PREFIX_SHARED_CALIBRATION))

    async def save_shared_calibration(self, file_path: Path) -> None:
        await self._write_storage(PREFIX_SHARED_CALIBRATION, file_path.read_bytes())

    async def load_shared_xiqueer(self, file_path: Path) -> None:
        # Legacy callers are redirected to the current user's private key.
        file_path.write_bytes(
            await self._read_storage(self._key(PREFIX_USER_XIQUEER))
        )

    async def save_shared_xiqueer(self, file_path: Path) -> None:
        await self._write_storage(
            self._key(PREFIX_USER_XIQUEER), file_path.read_bytes()
        )


_user_cache: dict[str, PluginStorageAdapter] = {}


async def get_or_create_user_adapter(
    plugin: BasePlugin, storage_key: str
) -> PluginStorageAdapter:
    adapter = PluginStorageAdapter(plugin, storage_key)
    await adapter.load_all()
    _user_cache[storage_key] = adapter
    return adapter


async def save_user_adapter(storage_key: str) -> None:
    adapter = _user_cache.pop(storage_key, None)
    if adapter is not None:
        await adapter.save_all()

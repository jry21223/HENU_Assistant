"""Storage adapter for LangBot Plugin Storage API.

This module bridges LangBot's official Storage API with file-based operations
used by course_schedule.py and mcp_server.py.

Usage:
    adapter = PluginStorageAdapter(plugin, storage_key)
    paths = await adapter.load_all()  # Load from Storage to temp files
    # ... use paths.profile_file, paths.xk_cookie_file, etc.
    await adapter.save_all()  # Save temp files back to Storage
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from langbot_plugin.api.definition.plugin import BasePlugin

logger = logging.getLogger(__name__)


class StorageLoadError(RuntimeError):
    """Raised when persisted plugin data cannot be materialized safely."""


class StorageSaveError(RuntimeError):
    """Raised when materialized plugin data cannot be persisted safely."""


_STORAGE_TRANSACTION_LOCK_ATTR = "_henu_storage_transaction_lock"


def _get_storage_transaction_lock() -> asyncio.Lock:
    """Return the transaction lock associated with the active LangBot loop."""
    loop = asyncio.get_running_loop()
    lock = getattr(loop, _STORAGE_TRANSACTION_LOCK_ATTR, None)
    if lock is None:
        lock = asyncio.Lock()
        setattr(loop, _STORAGE_TRANSACTION_LOCK_ATTR, lock)
    return lock


@asynccontextmanager
async def storage_transaction() -> AsyncIterator[None]:
    """Serialize materialize -> execute -> persist across shared temp state."""
    async with _get_storage_transaction_lock():
        yield

# Storage key prefixes for different data types
PREFIX_PROFILE = "user:{}:profile"
PREFIX_XK_COOKIE = "user:{}:xk_cookie"
PREFIX_LIBRARY_COOKIE = "user:{}:library_cookie"
PREFIX_SEMINAR_TASK = "user:{}:seminar_task"
PREFIX_SCHEDULE = "user:{}:schedule"
PREFIX_YUNFZ_TOKEN = "user:{}:yunfz_token"
PREFIX_CAS_COOKIE = "user:{}:cas_cookie"
PREFIX_COURSE_MONITOR_CONFIG = "user:{}:course_monitor_config"
PREFIX_COURSE_MONITOR_STATE = "user:{}:course_monitor_state"
PREFIX_SHARED_PERIOD_TIME = "shared:period_time"
PREFIX_SHARED_CALIBRATION = "shared:calibration"
PREFIX_SHARED_XIQUEER = "shared:xiqueer"

SHARED_PERIOD_TIME_FILE = "period_time_config.json"
SHARED_CALIBRATION_FILE = "period_time_calibration_state.json"
SHARED_XIQUEER_FILE = "xiqueer_period_time_request.json"


@dataclass
class UserStoragePaths:
    """Paths for local temp files during operations."""

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
    output_dir: Path
    shared_data_dir: Path  # 公共共享缓存（data/shared/），不含 Cookie/账号


class PluginStorageAdapter:
    """Adapter to bridge LangBot Storage API with file-based operations.

    This class handles:
    1. Loading user data from LangBot Storage to local temp files
    2. Providing paths for file-based operations
    3. Saving modified files back to Storage
    """

    # Shared temp directory (created once, reused for all users)
    _shared_temp_dir: Path | None = None

    def __init__(self, plugin: BasePlugin, storage_key: str):
        """Initialize the adapter.

        Args:
            plugin: The plugin instance with access to Storage API
            storage_key: Unique key for this user (derived from QQ/sender_id)
        """
        self._plugin = plugin
        self._storage_key = storage_key
        self._paths: UserStoragePaths | None = None
        self._loaded_content: dict[str, bytes] = {}

    def _key(self, prefix_template: str) -> str:
        """Create a storage key with user prefix."""
        return prefix_template.format(self._storage_key)

    async def load_all(self) -> UserStoragePaths:
        """Load all user data from Storage to local temp files.

        Concurrent callers must hold ``storage_transaction()`` until the
        matching ``save_all()`` completes because user and shared temp paths
        are reused between operations.

        Returns paths to local temp files for file operations.
        """
        self._paths = None
        self._loaded_content.clear()

        # Ensure shared temp directory exists
        if PluginStorageAdapter._shared_temp_dir is None:
            PluginStorageAdapter._shared_temp_dir = Path(
                tempfile.mkdtemp(prefix="henu_plugin_")
            )

        # Create user-specific directory
        user_root = PluginStorageAdapter._shared_temp_dir / self._storage_key
        user_root.mkdir(parents=True, exist_ok=True)

        output_dir = user_root / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        shared_data_dir = PluginStorageAdapter._shared_temp_dir / "shared"
        shared_data_dir.mkdir(parents=True, exist_ok=True)

        paths = UserStoragePaths(
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
            output_dir=output_dir,
            shared_data_dir=shared_data_dir,
        )

        loaded_content: dict[str, bytes] = {}
        try:
            existing_storage_keys = await self._existing_storage_keys()
            for storage_key, file_path in self._storage_bindings(paths):
                if storage_key in existing_storage_keys:
                    loaded_content[storage_key] = await self._load_json(
                        storage_key,
                        file_path,
                    )
                else:
                    loaded_content[storage_key] = self._materialize_json(
                        storage_key,
                        file_path,
                        b"",
                    )
        except Exception:
            # A partial materialization must never become eligible for save_all().
            self._paths = None
            self._loaded_content.clear()
            raise

        self._paths = paths
        self._loaded_content = loaded_content
        return paths

    async def save_all(self) -> None:
        """Save all local temp files back to Storage."""
        if self._paths is None:
            return

        for storage_key, file_path in self._storage_bindings(self._paths):
            baseline = self._loaded_content.get(storage_key)
            if baseline is None or not file_path.exists():
                continue
            try:
                current = file_path.read_bytes()
            except OSError as exc:
                raise StorageSaveError(f"读取待保存文件失败: {file_path}") from exc
            if current == baseline:
                continue
            await self._save_json(file_path, storage_key, data=current)
            self._loaded_content[storage_key] = current

    def _storage_bindings(self, paths: UserStoragePaths) -> list[tuple[str, Path]]:
        """Map every persisted Storage key to its materialized file."""
        return [
            (self._key(PREFIX_PROFILE), paths.profile_file),
            (self._key(PREFIX_XK_COOKIE), paths.xk_cookie_file),
            (self._key(PREFIX_LIBRARY_COOKIE), paths.library_cookie_file),
            (self._key(PREFIX_SEMINAR_TASK), paths.seminar_signin_task_file),
            (self._key(PREFIX_SCHEDULE), paths.schedule_file),
            (self._key(PREFIX_YUNFZ_TOKEN), paths.yunfz_token_file),
            (self._key(PREFIX_CAS_COOKIE), paths.cas_cookie_file),
            (self._key(PREFIX_COURSE_MONITOR_CONFIG), paths.course_monitor_config_file),
            (self._key(PREFIX_COURSE_MONITOR_STATE), paths.course_monitor_state_file),
            (PREFIX_SHARED_PERIOD_TIME, paths.shared_data_dir / SHARED_PERIOD_TIME_FILE),
            (PREFIX_SHARED_CALIBRATION, paths.shared_data_dir / SHARED_CALIBRATION_FILE),
            (PREFIX_SHARED_XIQUEER, paths.shared_data_dir / SHARED_XIQUEER_FILE),
        ]

    async def _existing_storage_keys(self) -> set[str]:
        """List persisted keys so a missing key is not treated as a read failure."""
        try:
            keys = await self._plugin.get_plugin_storage_keys()
            return {str(key) for key in keys}
        except Exception as exc:
            logger.warning("Failed to list LangBot Storage keys: %s", exc)
            raise StorageLoadError("列出 LangBot Storage 键失败") from exc

    def _materialize_json(
        self,
        storage_key: str,
        file_path: Path,
        data: bytes,
    ) -> bytes:
        """Write persisted bytes, or an empty JSON object for a missing key."""
        materialized = data if data else b"{}"
        try:
            file_path.write_bytes(materialized)
        except OSError as exc:
            raise StorageLoadError(f"写入临时 Storage 文件失败: {storage_key}") from exc
        logger.debug("Loaded %s bytes from Storage: %s", len(data), storage_key)
        return materialized

    async def _load_json(self, storage_key: str, file_path: Path) -> bytes:
        """Load JSON data from Storage and write to local file."""
        try:
            data = await self._plugin.get_plugin_storage(storage_key)
        except Exception as e:
            logger.warning("Failed to load Storage key %s: %s", storage_key, e)
            raise StorageLoadError(f"读取 LangBot Storage 失败: {storage_key}") from e

        return self._materialize_json(storage_key, file_path, data)

    async def _save_json(
        self,
        file_path: Path,
        storage_key: str,
        *,
        data: bytes | None = None,
    ) -> None:
        """Save local file content to Storage."""
        if not file_path.exists():
            return
        try:
            payload = file_path.read_bytes() if data is None else data
            await self._plugin.set_plugin_storage(storage_key, payload)
            logger.debug("Saved %s bytes to Storage: %s", len(payload), storage_key)
        except Exception as e:
            logger.warning("Failed to save to Storage %s: %s", storage_key, e)
            raise StorageSaveError(f"保存 LangBot Storage 失败: {storage_key}") from e

    # Shared data methods (cross-user)
    async def load_shared_period_time(self, file_path: Path) -> None:
        """Load shared period time config to file."""
        await self._load_json(PREFIX_SHARED_PERIOD_TIME, file_path)

    async def save_shared_period_time(self, file_path: Path) -> None:
        """Save shared period time config to Storage."""
        await self._save_json(file_path, PREFIX_SHARED_PERIOD_TIME)

    async def load_shared_calibration(self, file_path: Path) -> None:
        """Load shared calibration state to file."""
        await self._load_json(PREFIX_SHARED_CALIBRATION, file_path)

    async def save_shared_calibration(self, file_path: Path) -> None:
        """Save shared calibration state to Storage."""
        await self._save_json(file_path, PREFIX_SHARED_CALIBRATION)

    async def load_shared_xiqueer(self, file_path: Path) -> None:
        """Load shared xiqueer request to file."""
        await self._load_json(PREFIX_SHARED_XIQUEER, file_path)

    async def save_shared_xiqueer(self, file_path: Path) -> None:
        """Save shared xiqueer request to Storage."""
        await self._save_json(file_path, PREFIX_SHARED_XIQUEER)


# User data cache to avoid repeated loading
_user_cache: dict[str, PluginStorageAdapter] = {}


async def get_or_create_user_adapter(
    plugin: BasePlugin, storage_key: str
) -> PluginStorageAdapter:
    """Get cached adapter or create new one."""
    if storage_key not in _user_cache:
        adapter = PluginStorageAdapter(plugin, storage_key)
        await adapter.load_all()
        _user_cache[storage_key] = adapter
    return _user_cache[storage_key]


async def save_user_adapter(storage_key: str) -> None:
    """Save user data back to Storage."""
    adapter = _user_cache.get(storage_key)
    if adapter:
        await adapter.save_all()

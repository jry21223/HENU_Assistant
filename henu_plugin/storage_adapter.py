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

import json
import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from langbot_plugin.api.definition.plugin import BasePlugin

logger = logging.getLogger(__name__)

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
        self._dirty: set[str] = set()  # Track which files were modified

    def _key(self, prefix_template: str) -> str:
        """Create a storage key with user prefix."""
        return prefix_template.format(self._storage_key)

    async def load_all(self) -> UserStoragePaths:
        """Load all user data from Storage to local temp files.

        Returns paths to local temp files for file operations.
        """
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

        self._paths = UserStoragePaths(
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

        # Load each data type from Storage (async)
        await self._load_json(self._key(PREFIX_PROFILE), self._paths.profile_file)
        await self._load_json(self._key(PREFIX_XK_COOKIE), self._paths.xk_cookie_file)
        await self._load_json(
            self._key(PREFIX_LIBRARY_COOKIE), self._paths.library_cookie_file
        )
        await self._load_json(
            self._key(PREFIX_SEMINAR_TASK), self._paths.seminar_signin_task_file
        )
        await self._load_json(self._key(PREFIX_SCHEDULE), self._paths.schedule_file)
        await self._load_json(self._key(PREFIX_YUNFZ_TOKEN), self._paths.yunfz_token_file)
        await self._load_json(self._key(PREFIX_CAS_COOKIE), self._paths.cas_cookie_file)
        await self._load_json(
            self._key(PREFIX_COURSE_MONITOR_CONFIG), self._paths.course_monitor_config_file
        )
        await self._load_json(
            self._key(PREFIX_COURSE_MONITOR_STATE), self._paths.course_monitor_state_file
        )
        await self._load_json(
            PREFIX_SHARED_PERIOD_TIME,
            self._paths.shared_data_dir / SHARED_PERIOD_TIME_FILE,
        )
        await self._load_json(
            PREFIX_SHARED_CALIBRATION,
            self._paths.shared_data_dir / SHARED_CALIBRATION_FILE,
        )
        await self._load_json(
            PREFIX_SHARED_XIQUEER,
            self._paths.shared_data_dir / SHARED_XIQUEER_FILE,
        )

        self._dirty.clear()
        return self._paths

    async def save_all(self) -> None:
        """Save all local temp files back to Storage."""
        if self._paths is None:
            return

        # Save user data
        await self._save_json(self._paths.profile_file, self._key(PREFIX_PROFILE))
        await self._save_json(self._paths.xk_cookie_file, self._key(PREFIX_XK_COOKIE))
        await self._save_json(
            self._paths.library_cookie_file, self._key(PREFIX_LIBRARY_COOKIE)
        )
        await self._save_json(
            self._paths.seminar_signin_task_file, self._key(PREFIX_SEMINAR_TASK)
        )
        await self._save_json(self._paths.schedule_file, self._key(PREFIX_SCHEDULE))
        await self._save_json(self._paths.yunfz_token_file, self._key(PREFIX_YUNFZ_TOKEN))
        await self._save_json(self._paths.cas_cookie_file, self._key(PREFIX_CAS_COOKIE))
        await self._save_json(
            self._paths.course_monitor_config_file, self._key(PREFIX_COURSE_MONITOR_CONFIG)
        )
        await self._save_json(
            self._paths.course_monitor_state_file, self._key(PREFIX_COURSE_MONITOR_STATE)
        )
        await self._save_json(
            self._paths.shared_data_dir / SHARED_PERIOD_TIME_FILE,
            PREFIX_SHARED_PERIOD_TIME,
        )
        await self._save_json(
            self._paths.shared_data_dir / SHARED_CALIBRATION_FILE,
            PREFIX_SHARED_CALIBRATION,
        )
        await self._save_json(
            self._paths.shared_data_dir / SHARED_XIQUEER_FILE,
            PREFIX_SHARED_XIQUEER,
        )

        self._dirty.clear()

    async def _load_json(self, storage_key: str, file_path: Path) -> None:
        """Load JSON data from Storage and write to local file."""
        try:
            data = await self._plugin.get_plugin_storage(storage_key)
            if data:
                file_path.write_bytes(data)
                logger.debug(f"Loaded {len(data)} bytes from Storage: {storage_key}")
            else:
                # No data in storage, create empty JSON file
                file_path.write_text("{}", encoding="utf-8")
        except Exception as e:
            # Key doesn't exist or other error - create empty file
            logger.debug(f"No data in Storage for {storage_key}: {e}")
            file_path.write_text("{}", encoding="utf-8")

    async def _save_json(self, file_path: Path, storage_key: str) -> None:
        """Save local file content to Storage."""
        if not file_path.exists():
            return
        try:
            data = file_path.read_bytes()
            await self._plugin.set_plugin_storage(storage_key, data)
            logger.debug(f"Saved {len(data)} bytes to Storage: {storage_key}")
        except Exception as e:
            logger.warning(f"Failed to save to Storage {storage_key}: {e}")
            raise

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

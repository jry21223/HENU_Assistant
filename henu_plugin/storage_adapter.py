"""Fail-closed bridge between LangBot Storage and file-based campus modules.

Each adapter owns a request-scoped staging directory. A per-user lock covers the
load/execute/save lifecycle, changed files only are persisted, and optimistic
checks reject stale overwrites. Storage read failures are never converted into
empty JSON because doing so can erase an account on the subsequent save.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from langbot_plugin.entities.io.errors import ActionCallError

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
PREFIX_USER_SNAPSHOT = "user:{}:snapshot_v2"
PREFIX_SHARED_PERIOD_TIME = "shared:period_time"
PREFIX_SHARED_CALIBRATION = "shared:calibration"
PREFIX_SHARED_SNAPSHOT = "shared:snapshot_v2"
# Compatibility alias. New code must not persist credentials under this key.
PREFIX_SHARED_XIQUEER = "shared:xiqueer"

SHARED_PERIOD_TIME_FILE = "period_time_config.json"
SHARED_CALIBRATION_FILE = "period_time_calibration_state.json"
XIQUEER_FILE = "xiqueer_period_time_request.json"
SHARED_XIQUEER_FILE = XIQUEER_FILE
USER_SNAPSHOT_SCHEMA = "henu.langbot-user-storage.v2"
SHARED_SNAPSHOT_SCHEMA = "henu.langbot-shared-storage.v2"
USER_LEGACY_PREFIXES = (
    PREFIX_PROFILE,
    PREFIX_XK_COOKIE,
    PREFIX_LIBRARY_COOKIE,
    PREFIX_SEMINAR_TASK,
    PREFIX_SCHEDULE,
    PREFIX_YUNFZ_TOKEN,
    PREFIX_CAS_COOKIE,
    PREFIX_COURSE_MONITOR_CONFIG,
    PREFIX_COURSE_MONITOR_STATE,
    PREFIX_USER_XIQUEER,
)
SHARED_LEGACY_KEYS = (
    PREFIX_SHARED_PERIOD_TIME,
    PREFIX_SHARED_CALIBRATION,
)


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
    _user_locks: ClassVar[dict[tuple[int, str], asyncio.Lock]] = {}
    _shared_locks: ClassVar[dict[int, asyncio.Lock]] = {}

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
        self._original_snapshots: dict[str, bytes | None] = {}
        self._known_storage_keys: set[str] | None = None
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
            await self._load_known_storage_keys()
            self._paths = self._build_paths()
            self._files = self._file_specs(self._paths)
            await self._load_snapshot_domain(
                self._key(PREFIX_USER_SNAPSHOT),
                USER_SNAPSHOT_SCHEMA,
                [spec for spec in self._files if not spec.shared],
            )
            async with self._shared_lock():
                # Another user can create/update the shared snapshot after this
                # request's initial key listing but before this lock is entered.
                # Refresh inside the shared transaction to avoid stale absence.
                await self._load_known_storage_keys()
                await self._load_snapshot_domain(
                    PREFIX_SHARED_SNAPSHOT,
                    SHARED_SNAPSHOT_SCHEMA,
                    [spec for spec in self._files if spec.shared],
                )
            return self._paths
        except BaseException:
            self._release_transaction()
            self._cleanup()
            self._paths = None
            raise

    async def save_all(self) -> None:
        if self._paths is None:
            return
        try:
            current_payloads: dict[str, bytes] = {}
            for spec in self._files:
                payload = spec.path.read_bytes() if spec.path.exists() else b"{}"
                self._validate_json_payload(spec.storage_key, payload)
                current_payloads[spec.storage_key] = payload

            private_specs = [spec for spec in self._files if not spec.shared]
            shared_specs = [spec for spec in self._files if spec.shared]
            private_changed = self._domain_changed(private_specs, current_payloads)
            shared_changed = self._domain_changed(shared_specs, current_payloads)
            if not private_changed and not shared_changed:
                return

            user_snapshot_key = self._key(PREFIX_USER_SNAPSHOT)
            if private_changed:
                await self._assert_snapshot_domain_unchanged(
                    user_snapshot_key,
                    private_specs,
                )

            if shared_changed:
                async with self._shared_lock():
                    # Refresh again at compare-and-set time. The initial listing
                    # is only a read optimization, never conflict evidence.
                    await self._load_known_storage_keys()
                    await self._assert_snapshot_domain_unchanged(
                        PREFIX_SHARED_SNAPSHOT,
                        shared_specs,
                    )
                    await self._commit_snapshot_domains(
                        private_changed=private_changed,
                        private_specs=private_specs,
                        shared_specs=shared_specs,
                        current_payloads=current_payloads,
                    )
            elif private_changed:
                await self._commit_snapshot_domain(
                    user_snapshot_key,
                    USER_SNAPSHOT_SCHEMA,
                    private_specs,
                    current_payloads,
                )
        finally:
            self._release_transaction()
            self._cleanup()
            self._paths = None

    async def abort(self) -> None:
        self._release_transaction()
        self._cleanup()
        self._paths = None

    async def _load_snapshot_domain(
        self,
        snapshot_key: str,
        schema: str,
        specs: list[_StorageFile],
    ) -> None:
        snapshot = await self._read_snapshot_optional(snapshot_key)
        self._original_snapshots[snapshot_key] = snapshot
        if snapshot is None:
            payloads = {
                spec.storage_key: await self._read_storage(spec.storage_key)
                for spec in specs
            }
        else:
            payloads, legacy_mirror_complete = self._decode_snapshot(
                snapshot_key,
                schema,
                specs,
                snapshot,
            )
            if not legacy_mirror_complete:
                await self._mirror_legacy_domain(specs, payloads)
                snapshot = self._encode_snapshot(
                    schema,
                    specs,
                    payloads,
                    legacy_mirror_complete=True,
                )
                await self._write_storage(snapshot_key, snapshot)
                self._original_snapshots[snapshot_key] = snapshot

        for spec in specs:
            payload = payloads[spec.storage_key]
            self._validate_json_payload(spec.storage_key, payload)
            spec.path.parent.mkdir(parents=True, exist_ok=True)
            spec.path.write_bytes(payload)
            self._original_bytes[spec.storage_key] = payload

    def _domain_changed(
        self,
        specs: list[_StorageFile],
        current_payloads: dict[str, bytes],
    ) -> bool:
        return any(
            current_payloads[spec.storage_key]
            != self._original_bytes.get(spec.storage_key, b"{}")
            for spec in specs
        )

    async def _assert_snapshot_domain_unchanged(
        self,
        snapshot_key: str,
        specs: list[_StorageFile],
    ) -> None:
        expected_snapshot = self._original_snapshots.get(snapshot_key)
        current_snapshot = await self._read_snapshot_optional(snapshot_key)
        if current_snapshot != expected_snapshot:
            raise StorageConflictError(
                f"Storage 快照已被其他请求修改，拒绝覆盖: {snapshot_key}"
            )
        if expected_snapshot is not None:
            return
        for spec in specs:
            current = await self._read_storage(spec.storage_key)
            expected = self._original_bytes.get(spec.storage_key, b"{}")
            if current != expected:
                raise StorageConflictError(
                    f"Storage 已被其他请求修改，拒绝覆盖: {spec.storage_key}"
                )

    async def _commit_snapshot_domains(
        self,
        *,
        private_changed: bool,
        private_specs: list[_StorageFile],
        shared_specs: list[_StorageFile],
        current_payloads: dict[str, bytes],
    ) -> None:
        shared_pending = self._encode_snapshot(
            SHARED_SNAPSHOT_SCHEMA,
            shared_specs,
            current_payloads,
            legacy_mirror_complete=False,
        )
        await self._write_storage(PREFIX_SHARED_SNAPSHOT, shared_pending)
        if not private_changed:
            await self._mirror_legacy_domain(shared_specs, current_payloads)
            shared_snapshot = self._encode_snapshot(
                SHARED_SNAPSHOT_SCHEMA,
                shared_specs,
                current_payloads,
                legacy_mirror_complete=True,
            )
            await self._write_storage(PREFIX_SHARED_SNAPSHOT, shared_snapshot)
            self._record_snapshot_commit(
                PREFIX_SHARED_SNAPSHOT,
                shared_snapshot,
                shared_specs,
                current_payloads,
            )
            return

        user_snapshot_key = self._key(PREFIX_USER_SNAPSHOT)
        private_pending = self._encode_snapshot(
            USER_SNAPSHOT_SCHEMA,
            private_specs,
            current_payloads,
            legacy_mirror_complete=False,
        )
        try:
            await self._write_storage(user_snapshot_key, private_pending)
        except Exception:
            rollback_snapshot = self._original_snapshots.get(
                PREFIX_SHARED_SNAPSHOT
            ) or self._encode_snapshot(
                SHARED_SNAPSHOT_SCHEMA,
                shared_specs,
                self._original_bytes,
            )
            try:
                await self._write_storage(PREFIX_SHARED_SNAPSHOT, rollback_snapshot)
            except Exception as rollback_exc:
                raise StorageAdapterError(
                    "用户快照提交失败，且共享快照回滚失败；Storage 状态需要人工核验"
                ) from rollback_exc
            raise

        try:
            await self._mirror_legacy_domain(shared_specs, current_payloads)
            await self._mirror_legacy_domain(private_specs, current_payloads)
            shared_snapshot = self._encode_snapshot(
                SHARED_SNAPSHOT_SCHEMA,
                shared_specs,
                current_payloads,
                legacy_mirror_complete=True,
            )
            private_snapshot = self._encode_snapshot(
                USER_SNAPSHOT_SCHEMA,
                private_specs,
                current_payloads,
                legacy_mirror_complete=True,
            )
            await self._write_storage(PREFIX_SHARED_SNAPSHOT, shared_snapshot)
            await self._write_storage(user_snapshot_key, private_snapshot)
        except Exception as exc:
            raise StorageAdapterError(
                "Storage 权威快照已提交，但 v2.0.4 降级镜像尚未完成；"
                "保持 2.1.0 运行并重新初始化以自动修复"
            ) from exc

        self._record_snapshot_commit(
            PREFIX_SHARED_SNAPSHOT,
            shared_snapshot,
            shared_specs,
            current_payloads,
        )
        self._record_snapshot_commit(
            user_snapshot_key,
            private_snapshot,
            private_specs,
            current_payloads,
        )

    async def _commit_snapshot_domain(
        self,
        snapshot_key: str,
        schema: str,
        specs: list[_StorageFile],
        payloads: dict[str, bytes],
    ) -> None:
        pending_snapshot = self._encode_snapshot(
            schema,
            specs,
            payloads,
            legacy_mirror_complete=False,
        )
        await self._write_storage(snapshot_key, pending_snapshot)
        try:
            await self._mirror_legacy_domain(specs, payloads)
            completed_snapshot = self._encode_snapshot(
                schema,
                specs,
                payloads,
                legacy_mirror_complete=True,
            )
            await self._write_storage(snapshot_key, completed_snapshot)
        except Exception as exc:
            raise StorageAdapterError(
                "Storage 权威快照已提交，但 v2.0.4 降级镜像尚未完成；"
                "保持 2.1.0 运行并重新初始化以自动修复"
            ) from exc
        self._record_snapshot_commit(
            snapshot_key,
            completed_snapshot,
            specs,
            payloads,
        )

    async def _mirror_legacy_domain(
        self,
        specs: list[_StorageFile],
        payloads: dict[str, bytes],
    ) -> None:
        for spec in specs:
            await self._write_storage(spec.storage_key, payloads[spec.storage_key])

    def _record_snapshot_commit(
        self,
        snapshot_key: str,
        snapshot: bytes,
        specs: list[_StorageFile],
        payloads: dict[str, bytes],
    ) -> None:
        self._original_snapshots[snapshot_key] = snapshot
        for spec in specs:
            self._original_bytes[spec.storage_key] = payloads[spec.storage_key]

    @classmethod
    def _encode_snapshot(
        cls,
        schema: str,
        specs: list[_StorageFile],
        payloads: dict[str, bytes],
        *,
        legacy_mirror_complete: bool = True,
    ) -> bytes:
        return cls._encode_snapshot_payload(
            schema,
            [spec.storage_key for spec in specs],
            payloads,
            legacy_mirror_complete=legacy_mirror_complete,
        )

    @classmethod
    def _encode_snapshot_payload(
        cls,
        schema: str,
        storage_keys: list[str],
        payloads: dict[str, bytes],
        *,
        legacy_mirror_complete: bool,
    ) -> bytes:
        files: dict[str, str] = {}
        for storage_key in storage_keys:
            payload = payloads[storage_key]
            cls._validate_json_payload(storage_key, payload)
            files[storage_key] = base64.b64encode(payload).decode("ascii")
        return json.dumps(
            {
                "schema": schema,
                "legacy_mirror_complete": legacy_mirror_complete,
                "files": files,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    @classmethod
    def _decode_snapshot(
        cls,
        snapshot_key: str,
        schema: str,
        specs: list[_StorageFile],
        payload: bytes,
    ) -> tuple[dict[str, bytes], bool]:
        return cls._decode_snapshot_payload(
            snapshot_key,
            schema,
            [spec.storage_key for spec in specs],
            payload,
        )

    @classmethod
    def _decode_snapshot_payload(
        cls,
        snapshot_key: str,
        schema: str,
        storage_keys: list[str],
        payload: bytes,
    ) -> tuple[dict[str, bytes], bool]:
        try:
            decoded = json.loads(payload.decode("utf-8"))
            if not isinstance(decoded, dict) or decoded.get("schema") != schema:
                raise ValueError("snapshot schema 不匹配")
            legacy_mirror_complete = decoded.get("legacy_mirror_complete")
            if not isinstance(legacy_mirror_complete, bool):
                raise TypeError("snapshot legacy_mirror_complete 必须是布尔值")
            files = decoded.get("files")
            if not isinstance(files, dict):
                raise TypeError("snapshot files 必须是对象")
            if set(files) != set(storage_keys):
                raise TypeError("snapshot files 键集合不匹配")
            result: dict[str, bytes] = {}
            for storage_key in storage_keys:
                encoded = files.get(storage_key)
                if not isinstance(encoded, str):
                    raise TypeError(f"snapshot 缺少 {storage_key}")
                item = base64.b64decode(encoded, validate=True)
                cls._validate_json_payload(storage_key, item)
                result[storage_key] = item
            return result, legacy_mirror_complete
        except StorageLoadError:
            raise
        except Exception as exc:
            raise StorageLoadError(
                f"Storage 快照无效 ({snapshot_key}): {exc}"
            ) from exc

    @classmethod
    async def reconcile_legacy_snapshots(
        cls,
        plugin: BasePlugin,
        *,
        allow_legacy_import: bool = False,
    ) -> int:
        """Repair interrupted v2.0.4 downgrade mirrors before serving requests."""
        try:
            keys = await plugin.get_plugin_storage_keys()
        except Exception as exc:
            raise StorageLoadError(f"读取 LangBot Storage 键列表失败: {exc}") from exc
        if not isinstance(keys, list) or any(not isinstance(key, str) for key in keys):
            raise StorageLoadError("LangBot Storage 键列表必须是字符串列表")
        known_keys = set(keys)

        repaired = 0
        snapshot_keys = sorted(
            key
            for key in keys
            if key == PREFIX_SHARED_SNAPSHOT
            or re.fullmatch(r"user:[0-9A-Za-z._-]+:snapshot_v2", key)
        )
        for snapshot_key in snapshot_keys:
            if snapshot_key == PREFIX_SHARED_SNAPSHOT:
                schema = SHARED_SNAPSHOT_SCHEMA
                legacy_keys = list(SHARED_LEGACY_KEYS)
            else:
                storage_key = snapshot_key.removeprefix("user:").removesuffix(
                    ":snapshot_v2"
                )
                schema = USER_SNAPSHOT_SCHEMA
                legacy_keys = [
                    prefix.format(storage_key) for prefix in USER_LEGACY_PREFIXES
                ]
            try:
                raw = await plugin.get_plugin_storage(snapshot_key)
            except Exception as exc:
                raise StorageLoadError(
                    f"读取 LangBot Storage 失败 ({snapshot_key}): {exc}"
                ) from exc
            if isinstance(raw, str):
                raw = raw.encode("utf-8")
            if not isinstance(raw, (bytes, bytearray)):
                raise StorageLoadError(
                    f"LangBot Storage 返回了不支持的数据类型 ({snapshot_key})"
                )
            payloads, mirror_complete = cls._decode_snapshot_payload(
                snapshot_key,
                schema,
                legacy_keys,
                bytes(raw),
            )
            mirror_matches = True
            legacy_payloads: dict[str, bytes] = {}
            missing_legacy_keys: list[str] = []
            for legacy_key in legacy_keys:
                if legacy_key not in known_keys:
                    mirror_matches = False
                    missing_legacy_keys.append(legacy_key)
                    continue
                try:
                    legacy_payload = await plugin.get_plugin_storage(legacy_key)
                except Exception as exc:
                    raise StorageLoadError(
                        f"读取 LangBot Storage 失败 ({legacy_key}): {exc}"
                    ) from exc
                if isinstance(legacy_payload, str):
                    legacy_payload = legacy_payload.encode("utf-8")
                if not isinstance(legacy_payload, (bytes, bytearray)):
                    raise StorageLoadError(
                        f"LangBot Storage 返回了不支持的数据类型 ({legacy_key})"
                    )
                legacy_bytes = bytes(legacy_payload)
                cls._validate_json_payload(legacy_key, legacy_bytes)
                legacy_payloads[legacy_key] = legacy_bytes
                if legacy_bytes != payloads[legacy_key]:
                    mirror_matches = False
            if mirror_complete and mirror_matches:
                continue
            if mirror_complete:
                if not allow_legacy_import:
                    raise StorageLoadError(
                        "完整 snapshot 与 v2.0.4 legacy 镜像发生漂移；"
                        "默认拒绝猜测数据代次"
                    )
                if missing_legacy_keys:
                    raise StorageLoadError(
                        "完整 v2.0.4 降级镜像缺少键: "
                        + ", ".join(missing_legacy_keys)
                    )
                # Importing a rollback generation is an explicit operator
                # handoff after the v2.0.4 worker has stopped cleanly.  Never
                # infer it from drift alone: old releases publish keys one by
                # one and can leave a mixed generation after interruption.
                imported_snapshot = cls._encode_snapshot_payload(
                    schema,
                    legacy_keys,
                    legacy_payloads,
                    legacy_mirror_complete=True,
                )
                try:
                    await plugin.set_plugin_storage(snapshot_key, imported_snapshot)
                except Exception as exc:
                    raise StorageAdapterError(
                        f"无法导入 v2.0.4 回滚数据 ({snapshot_key}): {exc}"
                    ) from exc
                repaired += 1
                continue
            try:
                for legacy_key in legacy_keys:
                    await plugin.set_plugin_storage(legacy_key, payloads[legacy_key])
                if not mirror_complete:
                    completed = json.loads(bytes(raw).decode("utf-8"))
                    completed["legacy_mirror_complete"] = True
                    completed_payload = json.dumps(
                        completed,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                    await plugin.set_plugin_storage(snapshot_key, completed_payload)
            except Exception as exc:
                raise StorageAdapterError(
                    f"无法修复 v2.0.4 降级镜像 ({snapshot_key}): {exc}"
                ) from exc
            repaired += 1
        return repaired

    async def _read_storage_optional(self, storage_key: str) -> bytes | None:
        if (
            self._known_storage_keys is not None
            and storage_key not in self._known_storage_keys
        ):
            return None
        try:
            data = await self._plugin.get_plugin_storage(storage_key)
        except Exception as exc:
            if self._known_storage_keys is None and self._is_not_found(
                exc,
                storage_key,
            ):
                return None
            raise StorageLoadError(
                f"读取 LangBot Storage 失败 ({storage_key}): {exc}"
            ) from exc
        if data in (None, b"", ""):
            return None
        if isinstance(data, str):
            return data.encode("utf-8")
        if isinstance(data, (bytes, bytearray)):
            return bytes(data)
        raise StorageLoadError(
            f"LangBot Storage 返回了不支持的数据类型 ({storage_key}): "
            f"{type(data).__name__}"
        )

    async def _read_snapshot_optional(self, storage_key: str) -> bytes | None:
        payload = await self._read_storage_optional(storage_key)
        # LangBot Storage implementations commonly use an empty JSON object as
        # their missing-value sentinel. When the SDK key listing says the key
        # exists, however, an empty object is a corrupt snapshot and must fail
        # schema validation rather than revive stale legacy records.
        if payload == b"{}" and self._known_storage_keys is None:
            return None
        return payload

    async def _read_storage(self, storage_key: str) -> bytes:
        return await self._read_storage_optional(storage_key) or b"{}"

    async def _write_storage(self, storage_key: str, payload: bytes) -> None:
        try:
            await self._plugin.set_plugin_storage(storage_key, payload)
        except Exception as exc:
            raise StorageAdapterError(
                f"写入 LangBot Storage 失败 ({storage_key}): {exc}"
            ) from exc
        if self._known_storage_keys is not None:
            self._known_storage_keys.add(storage_key)
        logger.debug("Saved %d bytes to Storage: %s", len(payload), storage_key)

    async def _load_known_storage_keys(self) -> None:
        getter = getattr(self._plugin, "get_plugin_storage_keys", None)
        if not callable(getter):
            self._known_storage_keys = None
            return
        try:
            keys = await getter()
        except Exception as exc:
            raise StorageLoadError(f"读取 LangBot Storage 键列表失败: {exc}") from exc
        if not isinstance(keys, list) or any(not isinstance(key, str) for key in keys):
            raise StorageLoadError("LangBot Storage 键列表必须是字符串列表")
        self._known_storage_keys = set(keys)

    @staticmethod
    def _validate_json_payload(storage_key: str, payload: bytes) -> None:
        try:
            decoded = json.loads(payload.decode("utf-8"))
            if not isinstance(decoded, dict):
                raise TypeError("顶层必须是 JSON object")
        except Exception as exc:
            raise StorageLoadError(
                f"Storage JSON 无效 ({storage_key}): {exc}"
            ) from exc

    @staticmethod
    def _is_not_found(exc: Exception, storage_key: str) -> bool:
        if isinstance(exc, (KeyError, FileNotFoundError)):
            return True
        if not isinstance(exc, ActionCallError):
            return False
        pattern = (
            r"^(?:ActionCallError: )*Storage with key "
            + re.escape(storage_key)
            + r" not found$"
        )
        return re.fullmatch(pattern, str(exc)) is not None

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

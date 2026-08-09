from __future__ import annotations

import asyncio
import base64
import json
import tempfile
import unittest
from pathlib import Path

from langbot_plugin.entities.io.errors import ActionCallError

from henu_plugin.storage_adapter import (
    PluginStorageAdapter,
    StorageAdapterError,
    StorageConflictError,
    StorageLoadError,
)


class _FakePlugin:
    def __init__(self) -> None:
        self.storage: dict[str, bytes] = {}
        self.writes: list[str] = []

    async def get_plugin_storage(self, storage_key: str) -> bytes:
        return self.storage.get(storage_key, b"")

    async def set_plugin_storage(self, storage_key: str, data: bytes) -> None:
        self.writes.append(storage_key)
        self.storage[storage_key] = data


class _FailingReadPlugin(_FakePlugin):
    async def get_plugin_storage(self, storage_key: str) -> bytes:
        raise RuntimeError("storage temporarily unavailable")


class _AmbiguousReadErrorPlugin(_FakePlugin):
    async def get_plugin_storage(self, storage_key: str) -> bytes:
        raise RuntimeError(f"storage action not found after database 404: {storage_key}")


class _FreshRealShapePlugin(_FakePlugin):
    async def get_plugin_storage_keys(self) -> list[str]:
        return []

    async def get_plugin_storage(self, storage_key: str) -> bytes:
        raise AssertionError(f"missing key should not be fetched: {storage_key}")


class _KeyListingPlugin(_FakePlugin):
    async def get_plugin_storage_keys(self) -> list[str]:
        return list(self.storage)


class _ExactMissingActionPlugin(_FakePlugin):
    async def get_plugin_storage(self, storage_key: str) -> bytes:
        raise ActionCallError(
            f"ActionCallError: Storage with key {storage_key} not found"
        )


class _WrongMissingActionPlugin(_FakePlugin):
    async def get_plugin_storage(self, storage_key: str) -> bytes:
        raise ActionCallError("Storage with key user:someone-else:profile not found")


class _PresentInvalidSnapshotPlugin(_FakePlugin):
    def __init__(self) -> None:
        super().__init__()
        self.storage["user:qq_10001:snapshot_v2"] = b"{}"
        self.storage["user:qq_10001:profile"] = b'{"student_id":"stale"}'

    async def get_plugin_storage_keys(self) -> list[str]:
        return list(self.storage)


class _FailingSavePlugin(_FakePlugin):
    async def set_plugin_storage(self, storage_key: str, data: bytes) -> None:
        raise RuntimeError(f"storage unavailable: {storage_key}")


class _CancelledReadPlugin(_FakePlugin):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self._blocked_once = False

    async def get_plugin_storage(self, storage_key: str) -> bytes:
        if not self._blocked_once:
            self._blocked_once = True
            self.started.set()
            await asyncio.Event().wait()
        return await super().get_plugin_storage(storage_key)


class _NthWriteFailurePlugin(_FakePlugin):
    def __init__(self, fail_on: int) -> None:
        super().__init__()
        self.fail_on = fail_on
        self.write_count = 0

    async def set_plugin_storage(self, storage_key: str, data: bytes) -> None:
        self.write_count += 1
        if self.write_count == self.fail_on:
            raise RuntimeError(f"simulated write {self.write_count} failure")
        await super().set_plugin_storage(storage_key, data)

    async def get_plugin_storage_keys(self) -> list[str]:
        return list(self.storage)


def _snapshot_file(storage: dict[str, bytes], snapshot_key: str, file_key: str) -> bytes:
    snapshot = json.loads(storage[snapshot_key].decode("utf-8"))
    return base64.b64decode(snapshot["files"][file_key], validate=True)


class PluginStorageAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="henu_storage_adapter_test_")
        PluginStorageAdapter._shared_temp_dir = Path(self._tmp.name)
        PluginStorageAdapter._user_locks.clear()
        PluginStorageAdapter._shared_locks.clear()

    async def asyncTearDown(self) -> None:
        PluginStorageAdapter._shared_temp_dir = None
        PluginStorageAdapter._user_locks.clear()
        PluginStorageAdapter._shared_locks.clear()
        self._tmp.cleanup()

    async def test_request_uses_unique_staging_and_private_xiqueer_key(self) -> None:
        plugin = _FakePlugin()
        plugin.storage["user:qq_10001:xiqueer"] = (
            b'{"headers":{"Cookie":"private"}}'
        )
        adapter = PluginStorageAdapter(plugin, "qq_10001")
        paths = await adapter.load_all()

        self.assertTrue(paths.shared_data_dir.is_dir())
        self.assertNotEqual(paths.shared_data_dir, Path(self._tmp.name) / "shared")
        self.assertEqual(
            json.loads(paths.xiqueer_request_file.read_text(encoding="utf-8"))[
                "headers"
            ]["Cookie"],
            "private",
        )
        self.assertNotIn("shared:xiqueer", plugin.storage)
        await adapter.save_all()

    async def test_unchanged_files_are_not_written(self) -> None:
        plugin = _FakePlugin()
        plugin.storage["user:qq_10001:profile"] = b'{"student_id":"20230001"}'
        adapter = PluginStorageAdapter(plugin, "qq_10001")
        await adapter.load_all()
        await adapter.save_all()
        self.assertEqual(plugin.writes, [])

    async def test_changed_user_and_shared_files_are_saved(self) -> None:
        plugin = _FakePlugin()
        adapter = PluginStorageAdapter(plugin, "qq_10001")
        paths = await adapter.load_all()
        paths.cas_cookie_file.write_text('{"CASTGC":"user-a"}', encoding="utf-8")
        (paths.shared_data_dir / "period_time_config.json").write_text(
            '{"1":{"start":"08:00","end":"08:45"}}', encoding="utf-8"
        )
        await adapter.save_all()

        self.assertEqual(
            _snapshot_file(
                plugin.storage,
                "user:qq_10001:snapshot_v2",
                "user:qq_10001:cas_cookie",
            ),
            b'{"CASTGC":"user-a"}',
        )
        self.assertEqual(
            _snapshot_file(
                plugin.storage,
                "shared:snapshot_v2",
                "shared:period_time",
            ),
            b'{"1":{"start":"08:00","end":"08:45"}}',
        )
        self.assertNotIn("shared:cas_cookie", plugin.storage)

    async def test_private_snapshot_keeps_a_verified_v2_0_4_downgrade_mirror(
        self,
    ) -> None:
        plugin = _FakePlugin()
        adapter = PluginStorageAdapter(plugin, "qq_10001")
        paths = await adapter.load_all()
        paths.profile_file.write_text('{"student_id":"new"}', encoding="utf-8")
        paths.xk_cookie_file.write_text('{"sid":"new-cookie"}', encoding="utf-8")
        await adapter.save_all()

        self.assertEqual(plugin.writes[0], "user:qq_10001:snapshot_v2")
        self.assertEqual(plugin.writes[-1], "user:qq_10001:snapshot_v2")
        self.assertEqual(
            _snapshot_file(
                plugin.storage,
                "user:qq_10001:snapshot_v2",
                "user:qq_10001:profile",
            ),
            b'{"student_id":"new"}',
        )
        self.assertEqual(
            _snapshot_file(
                plugin.storage,
                "user:qq_10001:snapshot_v2",
                "user:qq_10001:xk_cookie",
            ),
            b'{"sid":"new-cookie"}',
        )
        self.assertEqual(
            plugin.storage["user:qq_10001:profile"],
            b'{"student_id":"new"}',
        )
        self.assertEqual(
            plugin.storage["user:qq_10001:xk_cookie"],
            b'{"sid":"new-cookie"}',
        )
        snapshot = json.loads(
            plugin.storage["user:qq_10001:snapshot_v2"].decode("utf-8")
        )
        self.assertIs(snapshot["legacy_mirror_complete"], True)

    async def test_startup_reconciles_a_snapshot_committed_before_legacy_mirror(
        self,
    ) -> None:
        plugin = _NthWriteFailurePlugin(fail_on=2)
        plugin.storage["user:qq_10001:profile"] = b'{"student_id":"old"}'
        plugin.storage["user:qq_10001:xk_cookie"] = b'{"sid":"old-cookie"}'
        adapter = PluginStorageAdapter(plugin, "qq_10001")
        paths = await adapter.load_all()
        paths.profile_file.write_text('{"student_id":"new"}', encoding="utf-8")
        paths.xk_cookie_file.write_text('{"sid":"new-cookie"}', encoding="utf-8")

        with self.assertRaises(StorageAdapterError):
            await adapter.save_all()

        pending = json.loads(
            plugin.storage["user:qq_10001:snapshot_v2"].decode("utf-8")
        )
        self.assertIs(pending["legacy_mirror_complete"], False)
        plugin.fail_on = -1

        reconciled = await PluginStorageAdapter.reconcile_legacy_snapshots(plugin)

        self.assertEqual(reconciled, 1)
        self.assertEqual(
            plugin.storage["user:qq_10001:profile"],
            b'{"student_id":"new"}',
        )
        self.assertEqual(
            plugin.storage["user:qq_10001:xk_cookie"],
            b'{"sid":"new-cookie"}',
        )
        completed = json.loads(
            plugin.storage["user:qq_10001:snapshot_v2"].decode("utf-8")
        )
        self.assertIs(completed["legacy_mirror_complete"], True)

    async def test_reupgrade_imports_newer_v204_legacy_generation(self) -> None:
        plugin = _KeyListingPlugin()
        adapter = PluginStorageAdapter(plugin, "qq_10001")
        paths = await adapter.load_all()
        paths.profile_file.write_text('{"student_id":"new"}', encoding="utf-8")
        paths.xk_cookie_file.write_text('{"sid":"new-cookie"}', encoding="utf-8")
        await adapter.save_all()
        plugin.storage["user:qq_10001:profile"] = b'{"student_id":"v204"}'
        plugin.storage["user:qq_10001:xk_cookie"] = b'{"sid":"v204-cookie"}'

        reconciled = await PluginStorageAdapter.reconcile_legacy_snapshots(
            plugin,
            allow_legacy_import=True,
        )

        self.assertEqual(reconciled, 1)
        self.assertEqual(
            plugin.storage["user:qq_10001:profile"],
            b'{"student_id":"v204"}',
        )
        self.assertEqual(
            plugin.storage["user:qq_10001:xk_cookie"],
            b'{"sid":"v204-cookie"}',
        )
        reupgraded = PluginStorageAdapter(plugin, "qq_10001")
        paths = await reupgraded.load_all()
        self.assertEqual(paths.profile_file.read_bytes(), b'{"student_id":"v204"}')
        self.assertEqual(paths.xk_cookie_file.read_bytes(), b'{"sid":"v204-cookie"}')
        await reupgraded.abort()

    async def test_complete_snapshot_drift_fails_closed_without_explicit_handoff(
        self,
    ) -> None:
        plugin = _KeyListingPlugin()
        adapter = PluginStorageAdapter(plugin, "qq_10001")
        paths = await adapter.load_all()
        paths.profile_file.write_text('{"student_id":"v21"}', encoding="utf-8")
        paths.xk_cookie_file.write_text('{"sid":"v21-cookie"}', encoding="utf-8")
        await adapter.save_all()
        plugin.storage["user:qq_10001:profile"] = b'{"student_id":"partial-v204"}'

        with self.assertRaises(StorageLoadError):
            await PluginStorageAdapter.reconcile_legacy_snapshots(plugin)

        retry = PluginStorageAdapter(plugin, "qq_10001")
        retry_paths = await retry.load_all()
        self.assertEqual(retry_paths.profile_file.read_bytes(), b'{"student_id":"v21"}')
        self.assertEqual(retry_paths.xk_cookie_file.read_bytes(), b'{"sid":"v21-cookie"}')
        await retry.abort()

    async def test_later_snapshot_failure_restores_the_complete_old_generation(
        self,
    ) -> None:
        plugin = _NthWriteFailurePlugin(fail_on=2)
        plugin.storage["user:qq_10001:profile"] = b'{"student_id":"old"}'
        plugin.storage["user:qq_10001:xk_cookie"] = b'{"sid":"old-cookie"}'
        plugin.storage["shared:period_time"] = b'{"1":{"start":"08:00"}}'
        adapter = PluginStorageAdapter(plugin, "qq_10001")
        paths = await adapter.load_all()
        paths.profile_file.write_text('{"student_id":"new"}', encoding="utf-8")
        paths.xk_cookie_file.write_text('{"sid":"new-cookie"}', encoding="utf-8")
        (paths.shared_data_dir / "period_time_config.json").write_text(
            '{"1":{"start":"09:00"}}',
            encoding="utf-8",
        )

        with self.assertRaises(StorageAdapterError):
            await adapter.save_all()

        plugin.fail_on = -1
        retry = PluginStorageAdapter(plugin, "qq_10001")
        retry_paths = await retry.load_all()
        self.assertEqual(
            retry_paths.profile_file.read_bytes(),
            b'{"student_id":"old"}',
        )
        self.assertEqual(
            retry_paths.xk_cookie_file.read_bytes(),
            b'{"sid":"old-cookie"}',
        )
        self.assertEqual(
            (retry_paths.shared_data_dir / "period_time_config.json").read_bytes(),
            b'{"1":{"start":"08:00"}}',
        )
        await retry.save_all()

    async def test_read_failure_is_fail_closed_and_never_writes_empty_data(self) -> None:
        plugin = _FailingReadPlugin()
        adapter = PluginStorageAdapter(plugin, "qq_10001")
        with self.assertRaises(StorageLoadError):
            await adapter.load_all()
        self.assertEqual(plugin.writes, [])

    async def test_fresh_real_langbot_storage_uses_the_key_listing(self) -> None:
        plugin = _FreshRealShapePlugin()
        adapter = PluginStorageAdapter(plugin, "qq_10001")

        paths = await adapter.load_all()

        self.assertEqual(paths.profile_file.read_bytes(), b"{}")
        await adapter.save_all()

    async def test_exact_real_action_missing_error_is_accepted_as_absent(self) -> None:
        plugin = _ExactMissingActionPlugin()
        adapter = PluginStorageAdapter(plugin, "qq_10001")

        paths = await adapter.load_all()

        self.assertEqual(paths.profile_file.read_bytes(), b"{}")
        await adapter.save_all()

    async def test_wrong_key_action_missing_error_is_fail_closed(self) -> None:
        plugin = _WrongMissingActionPlugin()
        adapter = PluginStorageAdapter(plugin, "qq_10001")

        with self.assertRaises(StorageLoadError):
            await adapter.load_all()
        self.assertEqual(plugin.writes, [])

    async def test_present_empty_object_snapshot_does_not_revive_stale_legacy_data(
        self,
    ) -> None:
        plugin = _PresentInvalidSnapshotPlugin()
        adapter = PluginStorageAdapter(plugin, "qq_10001")

        with self.assertRaises(StorageLoadError):
            await adapter.load_all()
        self.assertEqual(plugin.writes, [])

    async def test_text_only_not_found_error_is_not_treated_as_missing(self) -> None:
        plugin = _AmbiguousReadErrorPlugin()
        adapter = PluginStorageAdapter(plugin, "qq_10001")
        with self.assertRaises(StorageLoadError):
            await adapter.load_all()
        self.assertEqual(plugin.writes, [])

    async def test_existing_scalar_json_is_rejected_without_writes(self) -> None:
        plugin = _FakePlugin()
        plugin.storage["user:qq_10001:profile"] = b"[]"
        adapter = PluginStorageAdapter(plugin, "qq_10001")
        with self.assertRaises(StorageLoadError):
            await adapter.load_all()
        self.assertEqual(plugin.writes, [])

    async def test_cancelled_read_releases_user_lock_and_partial_staging(self) -> None:
        plugin = _CancelledReadPlugin()
        first = PluginStorageAdapter(plugin, "qq_10001")
        first_load = asyncio.create_task(first.load_all())
        await asyncio.wait_for(plugin.started.wait(), timeout=1)

        first_load.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first_load

        self.assertEqual(list(Path(self._tmp.name).iterdir()), [])
        second = PluginStorageAdapter(plugin, "qq_10001")
        paths = await asyncio.wait_for(second.load_all(), timeout=1)
        self.assertTrue(paths.user_root.is_dir())
        await second.save_all()

    async def test_save_failure_is_visible(self) -> None:
        plugin = _FailingSavePlugin()
        adapter = PluginStorageAdapter(plugin, "qq_10001")
        paths = await adapter.load_all()
        paths.profile_file.write_text('{"student_id":"20230001"}', encoding="utf-8")
        with self.assertRaises(RuntimeError):
            await adapter.save_all()

    async def test_optimistic_conflict_rejects_stale_overwrite(self) -> None:
        plugin = _FakePlugin()
        plugin.storage["user:qq_10001:profile"] = b'{"student_id":"old"}'
        adapter = PluginStorageAdapter(plugin, "qq_10001")
        paths = await adapter.load_all()
        paths.profile_file.write_text('{"student_id":"mine"}', encoding="utf-8")
        plugin.storage["user:qq_10001:profile"] = b'{"student_id":"other"}'

        with self.assertRaises(StorageConflictError):
            await adapter.save_all()
        self.assertEqual(
            plugin.storage["user:qq_10001:profile"], b'{"student_id":"other"}'
        )

    async def test_same_user_transactions_are_serialized(self) -> None:
        plugin = _FakePlugin()
        first = PluginStorageAdapter(plugin, "qq_10001")
        second = PluginStorageAdapter(plugin, "qq_10001")
        await first.load_all()

        started = asyncio.Event()
        finished = asyncio.Event()

        async def load_second() -> None:
            started.set()
            await second.load_all()
            finished.set()

        task = asyncio.create_task(load_second())
        await started.wait()
        await asyncio.sleep(0)
        self.assertFalse(finished.is_set())
        await first.save_all()
        await asyncio.wait_for(finished.wait(), timeout=1)
        await second.save_all()
        await task

    async def test_shared_conflict_check_and_write_use_one_lock_scope(self) -> None:
        plugin = _FakePlugin()
        adapter = PluginStorageAdapter(plugin, "qq_10001")
        paths = await adapter.load_all()
        (paths.shared_data_dir / "period_time_config.json").write_text(
            '{"1":{"start":"08:00","end":"08:45"}}',
            encoding="utf-8",
        )

        class CountingLock:
            def __init__(self) -> None:
                self.inner = asyncio.Lock()
                self.entries = 0

            async def __aenter__(self):
                self.entries += 1
                await self.inner.acquire()
                return self

            async def __aexit__(self, *_args):
                self.inner.release()

        lock = CountingLock()
        PluginStorageAdapter._shared_locks[id(asyncio.get_running_loop())] = lock  # type: ignore[assignment]
        await adapter.save_all()

        self.assertEqual(lock.entries, 1)

    async def test_stale_key_listing_cannot_overwrite_another_users_shared_snapshot(
        self,
    ) -> None:
        plugin = _KeyListingPlugin()
        first = PluginStorageAdapter(plugin, "qq_10001")
        first_paths = await first.load_all()

        second = PluginStorageAdapter(plugin, "qq_20002")
        second_paths = await second.load_all()
        (second_paths.shared_data_dir / "period_time_config.json").write_text(
            '{"owner":"second"}',
            encoding="utf-8",
        )
        await second.save_all()
        committed = plugin.storage["shared:snapshot_v2"]

        (first_paths.shared_data_dir / "period_time_config.json").write_text(
            '{"owner":"first"}',
            encoding="utf-8",
        )
        with self.assertRaises(StorageConflictError):
            await first.save_all()
        self.assertEqual(plugin.storage["shared:snapshot_v2"], committed)

    async def test_unknown_storage_key_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            PluginStorageAdapter(_FakePlugin(), "unknown")


if __name__ == "__main__":
    unittest.main()

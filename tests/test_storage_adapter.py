from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from henu_plugin.storage_adapter import (
    PluginStorageAdapter,
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


class _FailingSavePlugin(_FakePlugin):
    async def set_plugin_storage(self, storage_key: str, data: bytes) -> None:
        raise RuntimeError(f"storage unavailable: {storage_key}")


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
            plugin.storage["user:qq_10001:cas_cookie"], b'{"CASTGC":"user-a"}'
        )
        self.assertEqual(
            plugin.storage["shared:period_time"],
            b'{"1":{"start":"08:00","end":"08:45"}}',
        )
        self.assertNotIn("shared:cas_cookie", plugin.storage)

    async def test_read_failure_is_fail_closed_and_never_writes_empty_data(self) -> None:
        plugin = _FailingReadPlugin()
        adapter = PluginStorageAdapter(plugin, "qq_10001")
        with self.assertRaises(StorageLoadError):
            await adapter.load_all()
        self.assertEqual(plugin.writes, [])

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

    async def test_unknown_storage_key_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            PluginStorageAdapter(_FakePlugin(), "unknown")


if __name__ == "__main__":
    unittest.main()

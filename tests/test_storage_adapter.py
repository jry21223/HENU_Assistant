from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from henu_plugin.storage_adapter import (
    PluginStorageAdapter,
    StorageLoadError,
    storage_transaction,
)


class _FakePlugin:
    def __init__(self) -> None:
        self.storage: dict[str, bytes] = {}
        self.get_calls: list[str] = []
        self.set_calls: list[str] = []

    async def get_plugin_storage(self, storage_key: str) -> bytes:
        self.get_calls.append(storage_key)
        return self.storage[storage_key]

    async def get_plugin_storage_keys(self) -> list[str]:
        return list(self.storage)

    async def set_plugin_storage(self, storage_key: str, data: bytes) -> None:
        self.set_calls.append(storage_key)
        self.storage[storage_key] = data


class _FailingSavePlugin(_FakePlugin):
    async def set_plugin_storage(self, storage_key: str, data: bytes) -> None:
        raise RuntimeError(f"storage unavailable: {storage_key}")


class _FailingLoadPlugin(_FakePlugin):
    async def get_plugin_storage(self, storage_key: str) -> bytes:
        raise RuntimeError(f"temporary read failure: {storage_key}")


class _FailingKeysPlugin(_FakePlugin):
    async def get_plugin_storage_keys(self) -> list[str]:
        raise RuntimeError("temporary key listing failure")


class PluginStorageAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="henu_storage_adapter_test_")
        PluginStorageAdapter._shared_temp_dir = Path(self._tmp.name)

    async def asyncTearDown(self) -> None:
        PluginStorageAdapter._shared_temp_dir = None
        self._tmp.cleanup()

    async def test_load_all_populates_shared_data_dir(self) -> None:
        plugin = _FakePlugin()
        paths = await PluginStorageAdapter(plugin, "qq_10001").load_all()

        self.assertEqual(paths.shared_data_dir, Path(self._tmp.name) / "shared")
        self.assertTrue(paths.shared_data_dir.is_dir())
        self.assertTrue(paths.profile_file.exists())
        self.assertEqual(paths.profile_file.read_bytes(), b"{}")
        self.assertEqual(plugin.get_calls, [])

    async def test_load_all_materializes_user_private_cas_cookie_and_shared_configs(self) -> None:
        plugin = _FakePlugin()
        plugin.storage["user:qq_10001:cas_cookie"] = b'{"CASTGC":"user-a"}'
        plugin.storage["shared:period_time"] = b'{"1":{"start":"08:00","end":"08:45"}}'
        plugin.storage["shared:calibration"] = b'{"source":"xiqueer"}'
        plugin.storage["shared:xiqueer"] = b'{"data":"request"}'

        paths = await PluginStorageAdapter(plugin, "qq_10001").load_all()

        self.assertEqual(paths.cas_cookie_file.read_text(encoding="utf-8"), '{"CASTGC":"user-a"}')
        self.assertEqual(
            (paths.shared_data_dir / "period_time_config.json").read_text(encoding="utf-8"),
            '{"1":{"start":"08:00","end":"08:45"}}',
        )
        self.assertEqual(
            (paths.shared_data_dir / "period_time_calibration_state.json").read_text(encoding="utf-8"),
            '{"source":"xiqueer"}',
        )
        self.assertEqual(
            (paths.shared_data_dir / "xiqueer_period_time_request.json").read_text(encoding="utf-8"),
            '{"data":"request"}',
        )

    async def test_save_all_keeps_cas_cookie_user_private_and_shared_configs_global(self) -> None:
        plugin = _FakePlugin()
        adapter = PluginStorageAdapter(plugin, "qq_10001")
        paths = await adapter.load_all()
        paths.cas_cookie_file.write_text('{"CASTGC":"user-a"}', encoding="utf-8")
        (paths.shared_data_dir / "period_time_config.json").write_text(
            '{"1":{"start":"08:00","end":"08:45"}}',
            encoding="utf-8",
        )

        await adapter.save_all()

        self.assertEqual(plugin.storage["user:qq_10001:cas_cookie"], b'{"CASTGC":"user-a"}')
        self.assertEqual(
            plugin.storage["shared:period_time"],
            b'{"1":{"start":"08:00","end":"08:45"}}',
        )
        self.assertNotIn("shared:cas_cookie", plugin.storage)

    async def test_save_all_raises_when_storage_api_save_fails(self) -> None:
        adapter = PluginStorageAdapter(_FailingSavePlugin(), "qq_10001")
        paths = await adapter.load_all()
        paths.profile_file.write_text('{"student_id":"20230001"}', encoding="utf-8")

        with self.assertRaises(RuntimeError):
            await adapter.save_all()

    async def test_load_failure_never_materializes_a_saveable_empty_profile(self) -> None:
        plugin = _FailingLoadPlugin()
        plugin.storage["user:qq_10001:profile"] = b'{"student_id":"20230001"}'
        adapter = PluginStorageAdapter(plugin, "qq_10001")

        with self.assertRaises(StorageLoadError):
            await adapter.load_all()
        await adapter.save_all()

        self.assertEqual(
            plugin.storage["user:qq_10001:profile"],
            b'{"student_id":"20230001"}',
        )
        self.assertEqual(plugin.set_calls, [])

    async def test_key_listing_failure_never_enables_save_all(self) -> None:
        plugin = _FailingKeysPlugin()
        adapter = PluginStorageAdapter(plugin, "qq_10001")

        with self.assertRaises(StorageLoadError):
            await adapter.load_all()
        await adapter.save_all()

        self.assertEqual(plugin.set_calls, [])

    async def test_save_all_only_persists_materialized_files_that_changed(self) -> None:
        plugin = _FakePlugin()
        plugin.storage["user:qq_10001:profile"] = b'{"student_id":"old"}'
        adapter = PluginStorageAdapter(plugin, "qq_10001")
        paths = await adapter.load_all()

        await adapter.save_all()
        self.assertEqual(plugin.set_calls, [])

        paths.profile_file.write_text('{"student_id":"new"}', encoding="utf-8")
        await adapter.save_all()

        self.assertEqual(plugin.set_calls, ["user:qq_10001:profile"])
        self.assertEqual(
            plugin.storage["user:qq_10001:profile"],
            b'{"student_id":"new"}',
        )

    async def test_storage_transaction_serializes_materialize_execute_persist(self) -> None:
        plugin = _FakePlugin()
        plugin.storage["user:qq_10001:profile"] = b'{"student_id":"old"}'
        first_loaded = asyncio.Event()
        release_first = asyncio.Event()
        entered: list[str] = []
        observed_by_second: list[str] = []

        async def first_operation() -> None:
            async with storage_transaction():
                adapter = PluginStorageAdapter(plugin, "qq_10001")
                paths = await adapter.load_all()
                entered.append("first")
                paths.profile_file.write_text('{"student_id":"new"}', encoding="utf-8")
                first_loaded.set()
                await release_first.wait()
                await adapter.save_all()

        async def second_operation() -> None:
            await first_loaded.wait()
            async with storage_transaction():
                adapter = PluginStorageAdapter(plugin, "qq_10001")
                paths = await adapter.load_all()
                entered.append("second")
                observed_by_second.append(paths.profile_file.read_text(encoding="utf-8"))
                await adapter.save_all()

        first_task = asyncio.create_task(first_operation())
        second_task = asyncio.create_task(second_operation())
        await first_loaded.wait()
        await asyncio.sleep(0)
        self.assertEqual(entered, ["first"])

        release_first.set()
        await asyncio.gather(first_task, second_task)

        self.assertEqual(entered, ["first", "second"])
        self.assertEqual(observed_by_second, ['{"student_id":"new"}'])


if __name__ == "__main__":
    unittest.main()

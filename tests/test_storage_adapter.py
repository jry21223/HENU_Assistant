from __future__ import annotations

import asyncio
import threading
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from henu_plugin.storage_adapter import PluginStorageAdapter


class _FakePlugin:
    def __init__(self, storage: dict[str, bytes] | None = None):
        self.storage = dict(storage or {})
        self.saved: dict[str, bytes] = {}

    async def get_plugin_storage(self, storage_key: str) -> bytes:
        return self.storage.get(storage_key, b"")

    async def set_plugin_storage(self, storage_key: str, data: bytes) -> None:
        self.saved[storage_key] = data
        self.storage[storage_key] = data


class _FailingSavePlugin(_FakePlugin):
    def __init__(self, failing_keys: set[str]):
        super().__init__()
        self.failing_keys = failing_keys
        self.attempted_keys: list[str] = []

    async def set_plugin_storage(self, storage_key: str, data: bytes) -> None:
        self.attempted_keys.append(storage_key)
        if storage_key in self.failing_keys:
            raise RuntimeError(f"write failed for {storage_key}")


class PluginStorageAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="henu_storage_adapter_test_")
        PluginStorageAdapter._shared_temp_dir = Path(self._tmp.name)
        PluginStorageAdapter._shared_storage_io_lock = threading.Lock()

    async def asyncTearDown(self) -> None:
        PluginStorageAdapter._shared_temp_dir = None
        PluginStorageAdapter._shared_storage_io_lock = threading.Lock()
        self._tmp.cleanup()

    async def test_load_all_populates_shared_data_dir(self) -> None:
        paths = await PluginStorageAdapter(_FakePlugin(), "qq_10001").load_all()

        self.assertEqual(paths.shared_data_dir.parent, Path(self._tmp.name))
        self.assertTrue(paths.shared_data_dir.is_dir())
        self.assertNotEqual(paths.shared_data_dir, paths.user_root / "shared")
        self.assertTrue(paths.profile_file.exists())

    async def test_load_all_materializes_shared_files_outside_user_root(self) -> None:
        plugin = _FakePlugin(
            {
                "user:qq_10001:profile": b'{"student_id":"10001"}',
                "shared:period_time": b'{"period":true}',
                "shared:calibration": b'{"calibration":true}',
                "shared:xiqueer": b'{"xiqueer":true}',
            }
        )

        paths = await PluginStorageAdapter(plugin, "qq_10001").load_all()

        period_file = paths.shared_data_dir / "period_time_config.json"
        calibration_file = paths.shared_data_dir / "period_time_calibration_state.json"
        xiqueer_file = paths.shared_data_dir / "xiqueer_period_time_request.json"

        self.assertEqual(paths.profile_file.read_text(encoding="utf-8"), '{"student_id":"10001"}')
        self.assertEqual(period_file.read_text(encoding="utf-8"), '{"period":true}')
        self.assertEqual(calibration_file.read_text(encoding="utf-8"), '{"calibration":true}')
        self.assertEqual(xiqueer_file.read_text(encoding="utf-8"), '{"xiqueer":true}')
        self.assertEqual(period_file.parent.parent, Path(self._tmp.name))
        self.assertNotEqual(period_file.parent, paths.user_root / "shared")

    async def test_save_all_persists_shared_files_with_shared_storage_keys(self) -> None:
        plugin = _FakePlugin()
        adapter = PluginStorageAdapter(plugin, "qq_10001")
        paths = await adapter.load_all()

        paths.profile_file.write_text('{"student_id":"10001"}', encoding="utf-8")
        (paths.shared_data_dir / "period_time_config.json").write_text('{"period":true}', encoding="utf-8")
        (paths.shared_data_dir / "period_time_calibration_state.json").write_text(
            '{"calibration":true}',
            encoding="utf-8",
        )
        (paths.shared_data_dir / "xiqueer_period_time_request.json").write_text(
            '{"xiqueer":true}',
            encoding="utf-8",
        )

        await adapter.save_all()

        self.assertEqual(plugin.saved["user:qq_10001:profile"], b'{"student_id":"10001"}')
        self.assertEqual(plugin.saved["shared:period_time"], b'{"period":true}')
        self.assertEqual(plugin.saved["shared:calibration"], b'{"calibration":true}')
        self.assertEqual(plugin.saved["shared:xiqueer"], b'{"xiqueer":true}')
        self.assertNotIn("user:qq_10001:period_time", plugin.saved)

    async def test_shared_load_does_not_block_or_overwrite_request_local_shared_files(self) -> None:
        plugin = _FakePlugin({"shared:period_time": b'{"period":"old"}'})
        first_adapter = PluginStorageAdapter(plugin, "qq_10001")
        first_paths = await first_adapter.load_all()
        (first_paths.shared_data_dir / "period_time_config.json").write_text(
            '{"period":"first"}',
            encoding="utf-8",
        )

        second_adapter = PluginStorageAdapter(plugin, "qq_20002")
        second_load = asyncio.create_task(second_adapter.load_all())
        await asyncio.sleep(0.05)

        second_done_before_first_save = second_load.done()
        await first_adapter.save_all()
        second_paths = await asyncio.wait_for(second_load, timeout=1.0)
        await second_adapter.save_all()

        self.assertEqual(
            (first_paths.shared_data_dir / "period_time_config.json").read_text(encoding="utf-8"),
            '{"period":"first"}',
        )
        self.assertEqual(
            (second_paths.shared_data_dir / "period_time_config.json").read_text(encoding="utf-8"),
            '{"period":"old"}',
        )
        self.assertEqual(plugin.saved["shared:period_time"], b'{"period":"first"}')
        self.assertTrue(second_done_before_first_save)

    async def test_save_all_raises_with_aggregated_failed_storage_keys(self) -> None:
        failing_keys = {
            "user:qq_10001:profile",
            "user:qq_10001:schedule",
        }
        plugin = _FailingSavePlugin(failing_keys)
        adapter = PluginStorageAdapter(plugin, "qq_10001")
        await adapter.load_all()

        with self.assertRaises(Exception) as raised:
            await adapter.save_all()

        self.assertEqual(
            tuple(getattr(raised.exception, "failed_keys", ())),
            ("user:qq_10001:profile", "user:qq_10001:schedule"),
        )
        self.assertIn("user:qq_10001:profile", str(raised.exception))
        self.assertIn("user:qq_10001:schedule", str(raised.exception))
        self.assertIn("user:qq_10001:cas_cookie", plugin.attempted_keys)

        next_adapter = PluginStorageAdapter(_FakePlugin(), "qq_after_failure")
        next_paths = await asyncio.wait_for(next_adapter.load_all(), timeout=1.0)
        self.assertTrue(next_paths.profile_file.exists())
        await next_adapter.save_all()

    async def test_shared_save_raises_with_failed_storage_key(self) -> None:
        plugin = _FailingSavePlugin({"shared:period_time"})
        adapter = PluginStorageAdapter(plugin, "qq_10001")
        shared_file = Path(self._tmp.name) / "period_time_config.json"
        shared_file.write_text('{"ok": true}', encoding="utf-8")

        with self.assertRaises(Exception) as raised:
            await adapter.save_shared_period_time(shared_file)

        self.assertEqual(
            tuple(getattr(raised.exception, "failed_keys", ())),
            ("shared:period_time",),
        )


if __name__ == "__main__":
    unittest.main()

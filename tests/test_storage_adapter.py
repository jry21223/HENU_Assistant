from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from henu_plugin.storage_adapter import PluginStorageAdapter


class _FakePlugin:
    async def get_plugin_storage(self, storage_key: str) -> bytes:
        return b""

    async def set_plugin_storage(self, storage_key: str, data: bytes) -> None:
        return None


class _FailingSavePlugin(_FakePlugin):
    def __init__(self, failing_keys: set[str]):
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

    async def asyncTearDown(self) -> None:
        PluginStorageAdapter._shared_temp_dir = None
        self._tmp.cleanup()

    async def test_load_all_populates_shared_data_dir(self) -> None:
        paths = await PluginStorageAdapter(_FakePlugin(), "qq_10001").load_all()

        self.assertEqual(paths.shared_data_dir, Path(self._tmp.name) / "shared")
        self.assertTrue(paths.shared_data_dir.is_dir())
        self.assertTrue(paths.profile_file.exists())

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

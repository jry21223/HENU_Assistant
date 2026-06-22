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


if __name__ == "__main__":
    unittest.main()

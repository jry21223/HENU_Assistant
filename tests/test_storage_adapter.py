from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from henu_plugin.storage_adapter import PluginStorageAdapter


class _FakePlugin:
    def __init__(self) -> None:
        self.storage: dict[str, bytes] = {}

    async def get_plugin_storage(self, storage_key: str) -> bytes:
        return self.storage.get(storage_key, b"")

    async def set_plugin_storage(self, storage_key: str, data: bytes) -> None:
        self.storage[storage_key] = data


class _FailingSavePlugin(_FakePlugin):
    async def set_plugin_storage(self, storage_key: str, data: bytes) -> None:
        raise RuntimeError(f"storage unavailable: {storage_key}")


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


if __name__ == "__main__":
    unittest.main()

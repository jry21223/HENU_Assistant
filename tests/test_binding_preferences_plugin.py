from __future__ import annotations

import asyncio
import json

from langbot_plugin.api.entities.builtin.provider.session import LauncherTypes, Session

from henu_plugin.cache import LIBRARY_QUERY_CACHE
from henu_plugin.hardened_service import HardenedHenuPluginService
from henu_plugin.service import set_current_user_paths
from henu_plugin.storage_adapter import PluginStorageAdapter


class MemoryPlugin:
    def __init__(self, root):
        self.storage = {}
        self.service = HardenedHenuPluginService(root)

    async def get_plugin_storage_keys(self):
        return list(self.storage)

    async def get_plugin_storage(self, key):
        return self.storage.get(key, b"{}")

    async def set_plugin_storage(self, key, value):
        self.storage[key] = value


def person(user):
    return Session(sender_id=user, launcher_id=user, launcher_type=LauncherTypes.PERSON)


def test_rebinding_persists_empty_old_sessions_schedule_and_invalidates_cache(tmp_path):
    async def run():
        plugin = MemoryPlugin(tmp_path)
        plugin.storage["user:binding-user:profile"] = b'{"student_id":"old-student"}'
        plugin.storage["user:binding-user:schedule"] = b'{"schedule":{"old":"account"}}'
        plugin.storage["user:binding-user:library_cookie"] = b'{"fixture":"old-session"}'
        cache_key = "user:binding-user:library:offline-fixture"
        LIBRARY_QUERY_CACHE.set(cache_key, {"success": True, "seats": ["old-account"]})
        adapter = PluginStorageAdapter(plugin, "binding-user")
        paths = await adapter.load_all()
        set_current_user_paths(paths)
        try:
            result = plugin.service.run_tool("setup_account", {
                "student_id": "new-student", "password": "offline-placeholder",
                "verify_login": False, "calibrate_period_time": False,
            }, person("binding-user"), 1)
            await adapter.save_all()
        finally:
            set_current_user_paths(None)

        assert result["success"] is True
        assert result["verified"] is False
        assert json.loads(plugin.storage["user:binding-user:schedule"]) == {}
        assert json.loads(plugin.storage["user:binding-user:library_cookie"]) == {}
        assert LIBRARY_QUERY_CACHE.get(cache_key) is None
    asyncio.run(run())

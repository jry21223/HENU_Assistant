from __future__ import annotations

import asyncio
import json
import pytest

from langbot_plugin.api.entities.builtin.provider.session import LauncherTypes, Session
from components.cli_tools.henu_cli_safe import HenuCliSafe

from henu_plugin.cache import LIBRARY_QUERY_CACHE
from henu_plugin.hardened_service import HardenedHenuPluginService
from henu_plugin.service import set_current_user_paths
from henu_plugin.storage_adapter import PluginStorageAdapter
from henu_plugin.storage_adapter import StorageAdapterError


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
        plugin.storage["user:binding-user:library_cookie"] = (
            b'{"fixture":"old-session"}'
        )
        for record in ("course_monitor_config", "course_monitor_state"):
            plugin.storage[f"user:binding-user:{record}"] = (
                b'{"targets":["old-account-target"]}'
            )
        cache_key = "user:binding-user:library:offline-fixture"
        LIBRARY_QUERY_CACHE.set(cache_key, {"success": True, "seats": ["old-account"]})
        adapter = PluginStorageAdapter(plugin, "binding-user")
        paths = await adapter.load_all()
        set_current_user_paths(paths)
        try:
            result = plugin.service.run_tool(
                "setup_account",
                {
                    "student_id": "new-student",
                    "password": "offline-placeholder",
                    "verify_login": False,
                    "calibrate_period_time": False,
                },
                person("binding-user"),
                1,
            )
            await adapter.save_all()
        finally:
            set_current_user_paths(None)

        assert result["success"] is True
        assert result["verified"] is False
        assert json.loads(plugin.storage["user:binding-user:schedule"]) == {}
        assert json.loads(plugin.storage["user:binding-user:library_cookie"]) == {}
        assert LIBRARY_QUERY_CACHE.get(cache_key) is None
        next_request = PluginStorageAdapter(plugin, "binding-user")
        next_paths = await next_request.load_all()
        try:
            assert json.loads(next_paths.course_monitor_config_file.read_bytes()) == {}
            assert json.loads(next_paths.course_monitor_state_file.read_bytes()) == {}
        finally:
            await next_request.abort()

    asyncio.run(run())


def test_preference_commands_persist_through_storage_and_isolate_users(tmp_path):
    async def run():
        plugin = MemoryPlugin(tmp_path)
        tool = HenuCliSafe()
        tool.plugin = plugin
        saved = await tool.call(
            {"command": "preferences set --library-location A --library-seat-no 001"},
            person("pref-a"),
            1,
        )
        assert saved["success"] is True
        other = await tool.call({"command": "preferences get"}, person("pref-b"), 2)
        shown = await tool.call({"command": "preferences get"}, person("pref-a"), 3)
        assert other["preferences"] == {"library_location": "", "library_seat_no": ""}
        assert shown["preferences"] == {
            "library_location": "A",
            "library_seat_no": "001",
        }
        assert "默认区域：A" in shown["reply_text"]
        assert "默认座位：001" in shown["reply_text"]
        cleared = await tool.call(
            {"command": "preferences clear --library-seat-no"}, person("pref-a"), 4
        )
        assert cleared["preferences"] == {
            "library_location": "A",
            "library_seat_no": "",
        }
        assert (
            json.loads(plugin.storage["user:pref-a:profile"])["library_location"] == "A"
        )

    asyncio.run(run())


def test_preference_write_invalidates_only_that_users_library_cache(tmp_path):
    async def run():
        plugin = MemoryPlugin(tmp_path)
        tool = HenuCliSafe()
        tool.plugin = plugin
        mine = "user:pref-cache-a:library:fixture"
        other = "user:pref-cache-b:library:fixture"
        LIBRARY_QUERY_CACHE.set(mine, {"success": True, "fixture": "old-library"})
        LIBRARY_QUERY_CACHE.set(other, {"success": True, "fixture": "other-user"})

        result = await tool.call(
            {"command": "preferences set --library-location new-library"},
            person("pref-cache-a"),
            1,
        )

        assert result["success"] is True
        assert LIBRARY_QUERY_CACHE.get(mine) is None
        assert LIBRARY_QUERY_CACHE.get(other)["fixture"] == "other-user"

    asyncio.run(run())


@pytest.mark.parametrize(
    "command",
    [
        "preferences clear",
        "preferences clear --typo",
        "preferences clear --library-location unexpected",
        "preferences set --library-location A --library-location B",
        "preferences set --library-seat-no",
        "preferences get unexpected",
    ],
)
def test_malformed_preferences_do_not_write_storage(tmp_path, command):
    async def run():
        plugin = MemoryPlugin(tmp_path)
        tool = HenuCliSafe()
        tool.plugin = plugin
        before = dict(plugin.storage)
        result = await tool.call({"command": command}, person("pref-invalid"), 1)
        assert result["success"] is False
        assert plugin.storage == before

    asyncio.run(run())


def test_preferences_remain_private_and_never_create_a_booking(tmp_path):
    async def run():
        plugin = MemoryPlugin(tmp_path)
        tool = HenuCliSafe()
        tool.plugin = plugin
        group = Session(
            sender_id="pref-private",
            launcher_id="group",
            launcher_type=LauncherTypes.GROUP,
        )
        denied = await tool.call({"command": "preferences get"}, group, 1)
        assert denied["success"] is False
        assert plugin.storage == {}
        await tool.call(
            {"command": "preferences set --library-location A --library-seat-no 001"},
            person("pref-private"),
            2,
        )
        booking = await tool.call(
            {
                "command": "library reserve --date 2026-11-01 --time 08:00 --end-time 12:00"
            },
            person("pref-private"),
            3,
        )
        assert booking["error_code"] == "confirmation_parameters_incomplete"
        assert not any("pending_operation" in key for key in plugin.storage)

    asyncio.run(run())


@pytest.mark.parametrize("concurrent", [False, True])
def test_partial_storage_failure_does_not_replace_the_old_binding(tmp_path, concurrent):
    class FailingPlugin(MemoryPlugin):
        failed = False

        async def set_plugin_storage(self, key, value):
            await super().set_plugin_storage(key, value)
            if key.endswith(":library_cookie") and not self.failed:
                self.failed = True
                if concurrent:
                    self.storage["user:bind-storage:profile"] = (
                        b'{"student_id":"other-writer"}'
                    )
                raise OSError("offline storage response lost")

    async def run():
        plugin = FailingPlugin(tmp_path)
        plugin.storage.update(
            {
                "user:bind-storage:profile": b'{"student_id":"old-student"}',
                "user:bind-storage:library_cookie": b'{"fixture":"old-session"}',
            }
        )
        before = dict(plugin.storage)
        adapter = PluginStorageAdapter(plugin, "bind-storage")
        paths = await adapter.load_all()
        set_current_user_paths(paths)
        try:
            result = plugin.service.run_tool(
                "setup_account",
                {
                    "student_id": "new-student",
                    "password": "offline-placeholder",
                    "verify_login": False,
                    "calibrate_period_time": False,
                },
                person("bind-storage"),
                1,
            )
            assert result["success"] is True
            with pytest.raises(StorageAdapterError):
                await adapter.save_all()
        finally:
            set_current_user_paths(None)
        for key, content in before.items():
            if concurrent and key.endswith(":profile"):
                assert json.loads(plugin.storage[key])["student_id"] == "other-writer"
            else:
                assert plugin.storage[key] == content

    asyncio.run(run())

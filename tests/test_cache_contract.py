from __future__ import annotations

from pathlib import Path
import sys
import types
from types import SimpleNamespace
from unittest.mock import patch


try:
    import langbot_plugin  # noqa: F401
except ModuleNotFoundError:
    module_names = (
        "langbot_plugin",
        "langbot_plugin.api",
        "langbot_plugin.api.entities",
        "langbot_plugin.api.entities.builtin",
        "langbot_plugin.api.entities.builtin.provider",
        "langbot_plugin.api.entities.builtin.provider.session",
    )
    for module_name in module_names:
        sys.modules[module_name] = types.ModuleType(module_name)
    provider = sys.modules["langbot_plugin.api.entities.builtin.provider"]
    provider.session = sys.modules["langbot_plugin.api.entities.builtin.provider.session"]

from henu_plugin import service as service_module
from henu_plugin.cache import (
    SCHEDULE_CACHE,
    TTLCache,
    clear_all_caches,
)
from henu_plugin.service import HenuPluginService


def test_ttl_cache_copies_values_at_set_and_get_boundaries() -> None:
    cache: TTLCache[dict] = TTLCache()
    source = {"rows": [{"id": 1}, {"id": 2}]}

    cache.set("rows", source)
    source["rows"].pop()
    first_read = cache.get("rows")

    assert first_read == {"rows": [{"id": 1}, {"id": 2}]}
    first_read["rows"].clear()
    assert cache.get("rows") == {"rows": [{"id": 1}, {"id": 2}]}


def test_current_schedule_cache_separates_timezone_and_expires_quickly() -> None:
    clear_all_caches()
    service_module._CURRENT_IDENTITY.value = SimpleNamespace(storage_key="qq_10001")
    service = HenuPluginService(Path("."))

    def schedule_result(**kwargs):
        return {"success": True, "timezone": kwargs["timezone"]}

    try:
        with patch.object(service_module.mcp_server, "schedule_query", side_effect=schedule_result) as query:
            shanghai = service._schedule_query({"view": "current", "timezone": "Asia/Shanghai"})
            utc = service._schedule_query({"view": "current", "timezone": "UTC"})

            assert shanghai["timezone"] == "Asia/Shanghai"
            assert utc["timezone"] == "UTC"
            assert query.call_count == 2

            utc["timezone"] = "mutated by delivery layer"
            assert service._schedule_query({"view": "current", "timezone": "UTC"})["timezone"] == "UTC"
            assert query.call_count == 2

            utc_entry = next(
                entry
                for key, entry in SCHEDULE_CACHE._cache.items()
                if ":schedule:current:UTC:" in key
            )
            assert utc_entry.ttl_seconds == 30.0
            utc_entry.created_at -= 31.0
            service._schedule_query({"view": "current", "timezone": "UTC"})
            assert query.call_count == 3
    finally:
        service_module._CURRENT_IDENTITY.value = None
        clear_all_caches()


def test_seminar_cache_key_includes_effective_filters() -> None:
    clear_all_caches()
    service_module._CURRENT_IDENTITY.value = SimpleNamespace(storage_key="qq_10001")
    service = HenuPluginService(Path("."))

    def seminar_result(**kwargs):
        return {"success": True, "members": kwargs["members"], "room": kwargs["room"]}

    try:
        with patch.object(service_module.mcp_server, "seminar_query", side_effect=seminar_result) as query:
            small = service._seminar_query(
                {"view": "rooms", "target_date": "2026-07-14", "members": 2, "room": "A"}
            )
            large = service._seminar_query(
                {"view": "rooms", "target_date": "2026-07-14", "members": 6, "room": "B"}
            )

            assert small == {"success": True, "members": 2, "room": "A"}
            assert large == {"success": True, "members": 6, "room": "B"}
            assert query.call_count == 2

            assert service._seminar_query(
                {"view": "rooms", "target_date": "2026-07-14", "members": 2, "room": "A"}
            ) == small
            assert query.call_count == 2
    finally:
        service_module._CURRENT_IDENTITY.value = None
        clear_all_caches()

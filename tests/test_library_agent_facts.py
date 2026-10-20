import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from components.cli_tools.henu_cli_safe import HenuCliSafe
from langbot_plugin.api.entities.builtin.provider.session import Session, LauncherTypes


def test_library_tool_returns_verified_campus_totals_and_preserves_unknowns(monkeypatch):
    async def run():
        proxy = SimpleNamespace(get_query_vars=AsyncMock(return_value={}), set_query_var=AsyncMock(), get_bot_uuid=AsyncMock(return_value="bot"))
        monkeypatch.setattr("langbot_plugin.api.proxies.query_based_api.QueryBasedAPIProxy", lambda **kwargs: proxy)
        monkeypatch.setattr("components.cli_tools.base.QueryBasedAPIProxy", lambda **kwargs: proxy)
        rows = [
            {"area_id": "30", "location": "701南", "campus": "金明校区", "available_count": 12},
            {"area_id": "31", "location": "702北", "campus": "金明校区", "available_count": 8},
            {"area_id": "46", "location": "第一自习室", "campus": "明伦校区", "available_count": 5},
            {"area_id": "47", "location": "第二自习室", "campus": "明伦校区", "available_count": None},
        ]
        payload = {"success": True, "source": "live", "locations": rows}
        plugin = SimpleNamespace(
            plugin_runtime_handler=Mock(), get_plugin_storage=AsyncMock(return_value=b"{}"), set_plugin_storage=AsyncMock(),
            service=SimpleNamespace(run_tool=Mock(return_value=payload), run_tool_async=AsyncMock(return_value=payload),
                                    get_runtime_context=lambda *args: {"server_time": {"now_iso": "2026-10-20T18:10:00+08:00"}}),
        )
        tool = HenuCliSafe()
        tool.plugin = plugin
        result = await tool.call({"command": "library locations"}, Session(launcher_type=LauncherTypes.PERSON, launcher_id="alice", sender_id="alice"), 1)
        assert result["result_summary"]["library_campuses"] == {
            "金明校区": {"regions": 2, "available_seats": 20, "unknown_regions": 0},
            "明伦校区": {"regions": 2, "available_seats": None, "unknown_regions": 1},
        }

    asyncio.run(run())

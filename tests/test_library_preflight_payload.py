"""Exercise the real bot tool boundary with synthetic school API replies."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
import copy
import pytest
from langbot_plugin.api.entities.builtin.provider.session import Session, LauncherTypes
from components.cli_tools.henu_cli_safe import HenuCliSafe

COMMAND = "library reserve --location '208 过刊阅览室' --seat-no 068 --date 2026-11-16 --time 15:52 --end-time 22:00"


class Service:
    def __init__(self, free=True):
        self.free = free
        self.calls = []

    def is_pure_request(self, *a):
        return True

    async def run_tool_async(self, tool, params, *a):
        self.calls.append(params["command"])
        return {
            "success": True,
            "msg": "查询成功，108/136 个座位可用",
            "area": {"id": "13", "name": "208 过刊阅览室"},
            "target_date": "2026-11-16",
            "time_window": "15:52-22:00",
            "query_semantics": "reservation_availability",
            "date": "2026-11-16",
            "total_count": 136,
            "available_count": 108,
            "returned_count": int(self.free),
            "truncated": False,
            "status_counts": {"1": 108, "6": 27, "2": 1},
            "cli": {
                "mode": "exec",
                "command": COMMAND.replace("reserve", "plan"),
                "action": "library plan",
                "resolved_tool": "library_query",
            },
            "next_commands": [
                "library reserve --location <区域> --seat-no <座位> --date YYYY-MM-DD",
                "library current",
            ],
            "seats": [
                {
                    "id": "3673",
                    "no": "068",
                    "status": "1",
                    "resource_id": "henu:library:seat:henu_library:13:068",
                }
            ]
            if self.free
            else [],
            "reservation_rules": {
                "fixed_segment": True,
                "segment": "1746790",
                "effective_day": "2026-11-16",
                "effective_start": "15:52",
                "effective_end": "22:00",
                "booking_window": {"start": "06:30", "end": "23:00", "open_now": True},
            },
        }


class Plugin:
    def __init__(self, free=True):
        self.service = Service(free)
        self.storage = {}

    async def get_plugin_storage(self, key):
        return self.storage.get(key, b"{}")

    async def set_plugin_storage(self, key, value):
        self.storage[key] = value


@pytest.mark.parametrize("free", [True, False])
def test_bot_can_preview_a_free_seat_after_large_query(free):
    async def run():
        plugin = Plugin(free)
        tool = HenuCliSafe()
        tool.plugin = plugin
        session = Session(
            sender_id="synthetic",
            launcher_id="synthetic",
            launcher_type=LauncherTypes.PERSON,
        )
        r = await tool.call({"command": COMMAND}, session, 1)
        assert bool(r.get("confirmation_required")) is free, r
        assert all("library plan" in c for c in plugin.service.calls)
        if free:
            assert "068" in r["reply_text"] and "22:00" in r["reply_text"]
        else:
            assert r.get("error_code") == "seat_unavailable"

    asyncio.run(run())


def test_normal_delivery_is_restored_after_cancelled_preflight():
    async def run():
        plugin = Plugin()
        tool = HenuCliSafe()
        tool.plugin = plugin
        session = Session(
            sender_id="synthetic",
            launcher_id="synthetic",
            launcher_type=LauncherTypes.PERSON,
        )
        original = plugin.service.run_tool_async

        async def cancelled(*args):
            raise asyncio.CancelledError()

        plugin.service.run_tool_async = cancelled
        with pytest.raises(asyncio.CancelledError):
            await tool.call({"command": COMMAND}, session, 1)
        plugin.service.run_tool_async = original
        normal = await tool.call(
            {
                "command": COMMAND.replace("reserve", "seats").replace(
                    " --seat-no 068", ""
                )
            },
            session,
            2,
        )
        assert normal.get("llm_hint"), normal

    asyncio.run(run())

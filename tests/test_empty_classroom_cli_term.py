import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from components.cli_tools.henu_cli_safe import HenuCliSafe
from henu_plugin.hardened_service import HardenedHenuPluginService
from langbot_plugin.api.entities.builtin.provider.session import Session, LauncherTypes
from henu_mcp.tools import server_impl
from campus_core.empty_classroom.models import Term
from test_empty_classroom_term import classroom_client
from test_confirmation_shortcut import Plugin


def test_cli_query_without_manual_term_returns_school_term_and_rooms(monkeypatch):
    async def run():
        client, _ = classroom_client(
            {"authenticated": True, "current_xn": "2026", "current_xq": "0"}
        )
        monkeypatch.setattr(
            client,
            "fetch_terms",
            lambda: [Term("2026,0", "2026-2027学年第一学期", "2026", "0")],
        )
        monkeypatch.setattr(server_impl, "_get_empty_classroom_client", lambda: client)
        query = Mock(
            return_value=SimpleNamespace(
                total=1,
                to_dict=lambda: {"rooms": [{"roomId": "A", "roomName": "教室A"}]},
            )
        )
        monkeypatch.setattr(server_impl, "query_free_classrooms", query)
        plugin = Plugin()
        plugin.service = HardenedHenuPluginService(Path(__file__).resolve().parents[1])
        tool = HenuCliSafe()
        tool.plugin = plugin
        session = Session(
            sender_id="user-a",
            launcher_id="user-a",
            launcher_type=LauncherTypes("person"),
        )
        result = await tool.call(
            {"command": "empty_classroom query --campus-code 02 --week 5 --period 3"},
            session,
            1,
        )
        assert result["success"] is True
        assert "2026,0" in result["reply_text"]
        assert "教室A" in result["reply_text"]
        assert query.call_args.kwargs["term_code"] == "2026,0"
        assert query.call_args.kwargs["campus_code"] == "02"

    asyncio.run(run())

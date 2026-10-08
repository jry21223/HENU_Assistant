import copy
import datetime as dt
import pytest

from campus_core.bot import HenuCampusBot


MAP = {
    "type": "1",
    "name": "三楼北附楼走廊",
    "date": {
        "reserveType": "1",
        "list": [
            {
                "day": "2026-10-10",
                "book_start": "06:30",
                "book_end": "23:00",
                "times": [
                    {"id": "1772124", "status": 1, "start": "08:00", "end": "23:00"}
                ],
            }
        ],
    },
}


def school_bot(monkeypatch, space_map=None):
    bot = HenuCampusBot("test-student", "", saved_cookies={"_v4_token": "fixture"})
    calls = []

    def school(path, payload, **kwargs):
        calls.append((path, copy.deepcopy(payload)))
        if path == "/v4/Space/map":
            return {"code": 0, "data": copy.deepcopy(space_map or MAP)}
        if path == "/v4/space/pick":
            return {
                "code": 0,
                "data": {"area": [{"id": "16", "name": "三楼北附楼走廊"}]},
            }
        if path == "/v4/Space/seat":
            return {
                "code": 0,
                "data": {"list": [{"id": "100", "no": "001", "status": 1}]},
            }
        if path == "/v4/member/info":
            return {"code": 0, "data": {}}
        raise AssertionError(f"Unexpected school request: {path}")

    monkeypatch.setattr(bot, "_post_json", school)
    return bot, calls


def test_unopened_date_does_not_query_or_book_a_different_day(monkeypatch):
    bot, calls = school_bot(monkeypatch)
    result = bot.list_available_seats(
        area_id="16", target_date="2026-10-11", preferred_time="09:00"
    )
    assert result["success"] is False
    assert "2026-10-11" in result["msg"]
    assert not any(path in {"/v4/Space/seat", "/v4/space/confirm"} for path, _ in calls)


def test_fixed_segment_query_exposes_actual_times_and_booking_window(monkeypatch):
    from campus_core import seat_reservation

    monkeypatch.setattr(
        seat_reservation, "_now_dt", lambda: dt.datetime(2026, 10, 9, 0, 29)
    )
    bot, calls = school_bot(monkeypatch)
    result = bot.list_available_seats(
        area_id="16", target_date="2026-10-10", preferred_time="09:00"
    )
    assert result["success"] is True
    rules = result["reservation_rules"]
    assert rules["effective_start"] == "08:00"
    assert rules["effective_end"] == "23:00"
    assert rules["fixed_segment"] is True
    assert rules["booking_window"] == {
        "start": "06:30",
        "end": "23:00",
        "open_now": False,
    }
    assert result["date"] == "2026-10-10"
    assert not any(path == "/v4/space/confirm" for path, _ in calls)


def test_reservation_cli_preserves_explicit_choices_and_rejects_unknown_flags():
    from henu_plugin.cli import inspect_cli_command

    spec = inspect_cli_command(
        "library reserve --area-id 16 --seat-no 001 --date 2026-10-10 --time 9点 --end-policy server_full_slot"
    )
    assert spec.params["location"] == "16"
    assert spec.params["preferred_time"] == "09:00"
    assert spec.params["end_policy"] == "server_full_slot"
    missing = inspect_cli_command(
        "library reserve --area-id 16 --seat-no 001 --date 2026-10-10 --end-policy server_full_slot"
    )
    assert missing.params.get("preferred_time", "") == ""
    assert inspect_cli_command("library reserve --banana ignored").error
    assert inspect_cli_command("library reserve --time 25:00").error
    assert inspect_cli_command(
        "library reserve --time 09:00 --end-time 12:00 --end-policy server_full_slot"
    ).error


@pytest.mark.parametrize("value", ["25:00", "09:99", "garbage", "23:00"])
def test_unusable_start_does_not_fall_back_to_the_last_segment(monkeypatch, value):
    bot, calls = school_bot(monkeypatch)
    result = bot.list_available_seats(
        area_id="16", target_date="2026-10-10", preferred_time=value
    )
    assert result["success"] is False
    assert not any(path == "/v4/Space/seat" for path, _ in calls)


def test_closed_window_never_reaches_school_submission(monkeypatch):
    from campus_core import seat_reservation

    monkeypatch.setattr(
        seat_reservation, "_now_dt", lambda: dt.datetime(2026, 10, 9, 0, 29)
    )
    bot, calls = school_bot(monkeypatch)
    result = bot.reserve("16", "001", "2026-10-10", "09:00", max_attempts=3)
    assert result["error_code"] == "booking_window_closed"
    assert result["attempt"] == 1
    assert not any(path == "/v4/space/confirm" for path, _ in calls)


def test_invalid_retry_deadline_is_rejected_before_any_school_request(monkeypatch):
    bot, calls = school_bot(monkeypatch)
    result = bot.reserve("16", "001", "2026-10-10", "09:00", retry_until="invalid")
    assert result["success"] is False
    assert "retry_until" in result["msg"]
    assert calls == []


def test_closed_booking_window_is_explained_before_confirmation(monkeypatch):
    import asyncio
    from pathlib import Path
    from unittest.mock import Mock
    import mcp_server
    from components.cli_tools.henu_cli_safe import HenuCliSafe
    from henu_plugin.hardened_service import HardenedHenuPluginService
    from langbot_plugin.api.entities.builtin.provider.session import (
        Session,
        LauncherTypes,
    )
    from test_confirmation_shortcut import Plugin

    async def run():
        from campus_core import seat_reservation

        monkeypatch.setattr(
            seat_reservation, "_now_dt", lambda: dt.datetime(2026, 10, 9, 0, 29)
        )
        bot, _ = school_bot(monkeypatch)
        monkeypatch.setattr(
            mcp_server,
            "library_query",
            lambda **kw: bot.list_available_seats(
                location_name=kw.get("location", ""),
                target_date=kw.get("target_date", ""),
                preferred_time=kw.get("preferred_time", ""),
            ),
        )
        submit = Mock(return_value={"success": True})
        monkeypatch.setattr(mcp_server, "library_reserve", submit)
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
            {
                "command": "library reserve --area-id 16 --seat-no 001 --date 2026-10-10 --time 09:00 --end-policy server_full_slot"
            },
            session,
            1,
        )
        assert result["error_code"] == "booking_window_closed"
        assert "06:30" in result["reply_text"]
        assert "user:user-a:pending_operation" not in plugin.storage
        submit.assert_not_called()

    asyncio.run(run())


def test_failed_submission_is_consumed_and_reported_without_another_confirmation(
    monkeypatch,
):
    import asyncio
    import json
    from pathlib import Path
    from types import SimpleNamespace
    from unittest.mock import Mock
    import mcp_server
    from components.cli_tools.henu_cli_safe import HenuCliSafe
    from henu_plugin.hardened_service import HardenedHenuPluginService
    from henu_plugin.confirmation_shortcut import ConfirmationShortcut
    from langbot_plugin.api.entities.builtin.provider.session import (
        Session,
        LauncherTypes,
    )
    from test_confirmation_shortcut import Plugin, ctx

    async def run():
        from campus_core import seat_reservation

        monkeypatch.setattr(
            seat_reservation, "_now_dt", lambda: dt.datetime(2026, 10, 9, 9, 0)
        )
        bot, _ = school_bot(monkeypatch)
        monkeypatch.setattr(
            mcp_server,
            "library_query",
            lambda **kw: bot.list_available_seats(
                location_name=kw.get("location", ""),
                target_date=kw.get("target_date", ""),
                preferred_time=kw.get("preferred_time", ""),
            ),
        )
        submit = Mock(
            return_value={
                "success": False,
                "msg": "座位开放预约时间为2026-10-09 06:30:00~23:00:00",
            }
        )
        monkeypatch.setattr(mcp_server, "library_reserve", submit)
        plugin = Plugin()
        plugin.service = HardenedHenuPluginService(Path(__file__).resolve().parents[1])
        tool = HenuCliSafe()
        tool.plugin = plugin
        session = Session(
            sender_id="user-a",
            launcher_id="user-a",
            launcher_type=LauncherTypes("person"),
        )
        command = "library reserve --area-id 16 --seat-no 001 --date 2026-10-10 --time 09:00 --end-policy server_full_slot"
        first = await tool.call({"command": command}, session, 1)
        original = json.loads(plugin.storage["user:user-a:pending_operation"])
        assert "实际 08:00～23:00" in first["reply_text"]
        repeated = await tool.call({"command": command}, session, 2)
        assert (
            json.loads(plugin.storage["user:user-a:pending_operation"])["token"]
            == original["token"]
        )
        assert repeated["confirmation_required"]
        flow = ConfirmationShortcut(SimpleNamespace(plugin=plugin))
        request = ctx(query_id=3)
        await flow.handle(request)
        await flow.tasks["user-a"]
        status = await tool.call({"command": "library operation-status"}, session, 4)
        assert status["operation_state"] == "failed"
        assert "06:30" in status["reply_text"]
        assert "已执行" in status["reply_text"]
        assert "✅" not in status["reply_text"]
        again = ctx(query_id=5)
        await flow.handle(again)
        assert "06:30" in str(again.reply.call_args_list)
        assert submit.call_count == 1
        other = Session(
            sender_id="user-a",
            launcher_id="other",
            launcher_type=LauncherTypes("person"),
        )
        assert (await tool.call({"command": "library operation-status"}, other, 6))[
            "operation_state"
        ] == "none"

    asyncio.run(run())


def test_concurrent_confirmations_submit_at_most_once(monkeypatch):
    import asyncio
    import json
    import time
    from pathlib import Path
    from unittest.mock import Mock
    import mcp_server
    from components.cli_tools.henu_cli_safe import HenuCliSafe
    from henu_plugin.hardened_service import HardenedHenuPluginService
    from langbot_plugin.api.entities.builtin.provider.session import (
        Session,
        LauncherTypes,
    )
    from test_confirmation_shortcut import Plugin

    async def run():
        from campus_core import seat_reservation

        monkeypatch.setattr(
            seat_reservation, "_now_dt", lambda: dt.datetime(2026, 10, 9, 9, 0)
        )
        bot, _ = school_bot(monkeypatch)
        monkeypatch.setattr(
            mcp_server,
            "library_query",
            lambda **kw: bot.list_available_seats(
                location_name=kw.get("location", ""),
                target_date=kw.get("target_date", ""),
                preferred_time=kw.get("preferred_time", ""),
            ),
        )

        def school_submit(**kw):
            time.sleep(0.02)
            return {
                "success": True,
                "msg": "预约成功",
                "verification": {"verified": True},
            }

        submit = Mock(side_effect=school_submit)
        monkeypatch.setattr(mcp_server, "library_reserve", submit)
        plugin = Plugin()
        plugin.service = HardenedHenuPluginService(Path(__file__).resolve().parents[1])
        tool = HenuCliSafe()
        tool.plugin = plugin
        session = Session(
            sender_id="user-a",
            launcher_id="user-a",
            launcher_type=LauncherTypes("person"),
        )
        command = "library reserve --area-id 16 --seat-no 001 --date 2026-10-10 --time 09:00 --end-policy server_full_slot"
        await tool.call({"command": command}, session, 1)
        token = json.loads(plugin.storage["user:user-a:pending_operation"])["token"]
        results = await asyncio.gather(
            *(
                tool.call({"command": "confirm " + token}, session, query)
                for query in (2, 3)
            )
        )
        assert submit.call_count == 1
        assert sum(r.get("success") is True for r in results) == 1
        assert any("✅" in r.get("reply_text", "") for r in results)
        await tool.call({"command": command}, session, 4)
        old = json.loads(plugin.storage["user:user-a:pending_operation"])["token"]
        await tool.call({"command": command.replace("09:00", "14:00")}, session, 5)
        assert not (await tool.call({"command": "confirm " + old}, session, 6))[
            "success"
        ]
        assert submit.call_count == 1

    asyncio.run(run())

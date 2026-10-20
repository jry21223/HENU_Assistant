import asyncio
import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from langbot_plugin.api.entities.builtin.platform.message import MessageChain, Plain


def context(text="", query=1, user="alice"):
    ctx = SimpleNamespace(
        query_id=query,
        event=SimpleNamespace(
            sender_id=user,
            launcher_id=user,
            launcher_type="person",
            message_chain=MessageChain([Plain(text=text)]),
        ),
        get_bot_uuid=AsyncMock(return_value="bot"),
        reply=AsyncMock(),
        set_query_var=AsyncMock(),
        prevent_default=Mock(),
        prevent_postorder=Mock(),
        is_prevent_postorder=False,
    )
    return ctx


def wire(ctx):
    text = ctx.reply.call_args.args[0].root[0].text
    encoded = text.rsplit("\n[HENU_ACTIONS_V1:", 1)[1][:-1]
    return text, json.loads(base64.urlsafe_b64decode(encoded))


def test_seat_map_to_reservation_preview_never_submits(monkeypatch):
    from henu_plugin import library_seat_map as lib, qq_buttons as buttons

    async def run():
        storage = {}

        async def get(key):
            return storage.get(key, b"{}")

        async def set_(key, value):
            storage[key] = value

        plugin = SimpleNamespace(
            get_plugin_storage=get,
            set_plugin_storage=set_,
            service=SimpleNamespace(
                run_tool=Mock(side_effect=AssertionError("campus write forbidden"))
            ),
        )
        gateway = AsyncMock(
            return_value={
                "success": True,
                "map_url": "https://zwyy.henu.edu.cn/home/images/web/area/30/seat-free.jpg",
                "seats": ["001", "003"],
                "total_count": 4,
            }
        )
        monkeypatch.setattr(lib, "query_seats", gateway)
        # The installed engine also performs a read-only library-plan preflight.
        # Exercise that public CLI boundary without ever allowing a campus write.
        from components.cli_tools.base import BaseHenuTool

        async def read_plan(self, params, session, query_id):
            assert params["command"].startswith("library plan ")
            return {
                "success": True,
                "area": {"id": "30", "name": "701 七层南自习室"},
                "seats": [{"id": "seat-001", "no": "001"}],
                "target_date": "2026-11-01",
                "reservation_rules": {
                    "effective_start": "14:00",
                    "effective_end": "16:00",
                    "booking_window": {"open_now": True},
                },
            }

        monkeypatch.setattr(BaseHenuTool, "call", read_plan)
        origin = context()
        choices = await lib.start_selection(
            plugin,
            origin,
            {
                "locations": [
                    {
                        "area_id": "30",
                        "location": "701 七层南自习室",
                        "campus": "金明校区",
                        "available_count": 2,
                    }
                ]
            },
        )
        assert (
            choices is None
        )  # Selection is text/image only; buttons are for confirmation.
        campus = context("金明", query=2)
        assert await lib.handle_library_text(plugin, campus, "金明")
        assert "HENU_ACTIONS" not in str(campus.reply.call_args)
        area = context("1", query=3)
        assert await lib.handle_library_text(plugin, area, "1")
        assert "HENU_ACTIONS" not in str(area.reply.call_args)
        window = context("2026-11-01 14:00-16:00", query=4)
        assert await lib.handle_library_text(plugin, window, "2026-11-01 14:00-16:00")
        text = window.reply.call_args.args[0].root[0].text
        assert "HENU_ACTIONS" not in text
        assert "seat-free.jpg" in text and "底图颜色" in text
        assert "剩余 2 座" in text and "14:00" in text and "16:00" in text
        seat = context("001", query=5)
        assert await lib.handle_library_text(plugin, seat, "001")
        text, confirm = wire(seat)
        assert "001" in text and "金明校区" in text and "尚未提交" in text
        assert [a["label"] for a in confirm["actions"]] == ["确认预约", "取消"]
        pending = json.loads(storage["user:alice:pending_operation"])
        assert "--seat-no 001" in pending["command"]
        assert "--end-time 16:00" in pending["command"]
        plugin.service.run_tool.assert_not_called()
        # Cancelling the real preview is safe; no campus API may run.
        cancel = context("HENU_BUTTON " + confirm["actions"][1]["id"], query=6)
        await buttons.handle(plugin, cancel)
        assert json.loads(storage["user:alice:pending_operation"]) == {}
        plugin.service.run_tool.assert_not_called()

    asyncio.run(run())


def test_school_query_uses_exact_window_keeps_reserved_seats_off_buttons(monkeypatch):
    import requests
    from urllib.parse import urlsplit
    from henu_plugin.library_seat_map import _fetch_seats

    calls = []

    def request(self, method, url, **kwargs):
        path = urlsplit(url).path
        calls.append((path, kwargs.get("json") or kwargs.get("data")))
        if path == "/v4/Space/map":
            data = {
                "type": "1",
                "name": "701 七层南自习室",
                "date": {
                    "reserveType": "1",
                    "list": [
                        {
                            "day": "2026-11-01",
                            "times": [
                                {
                                    "id": "slot1",
                                    "start": "08:00",
                                    "end": "23:00",
                                    "status": "1",
                                }
                            ],
                        }
                    ],
                },
            }
        elif path == "/v4/Space/seat":
            data = {
                "list": [
                    {"no": "001", "status": "2", "status_name": "已预约"},
                    {"no": "002", "status": "1", "status_name": "空闲"},
                    {"no": "003", "status": "1", "status_name": "已预约"},
                ]
            }
        else:
            raise AssertionError("unexpected school write")
        result = requests.Response()
        result.status_code = 200
        result._content = json.dumps({"code": 0, "data": data}).encode()
        return result

    monkeypatch.setattr(requests.Session, "request", request)
    area = {"area_id": "30", "location": "701 七层南自习室", "campus": "金明校区"}
    result = _fetch_seats({"_v4_token": "fake"}, area, "2026-11-01", "08:00", "23:00")
    assert result["seats"] == ["002"]
    assert result["reserved_count"] == 1 and result["unknown_count"] == 1
    assert [p for p, _ in calls] == ["/v4/Space/map", "/v4/Space/seat"]
    calls.clear()
    result = _fetch_seats({"_v4_token": "fake"}, area, "2026-11-01", "09:00", "10:00")
    assert not result["success"] and result["windows"] == [
        ("2026-11-01", "08:00", "23:00")
    ]
    assert [p for p, _ in calls] == ["/v4/Space/map"]


async def select_area(plugin, result):
    from henu_plugin import library_seat_map as lib

    await lib.start_selection(plugin, context(query=1), result)
    campus = context("金明", query=2)
    assert await lib.handle_library_text(plugin, campus, "金明")
    area = context("1", query=3)
    assert await lib.handle_library_text(plugin, area, "1")
    return area


def test_agent_can_continue_after_menu_selection_without_reasking_known_conditions(monkeypatch):
    from henu_plugin import library_seat_map as lib
    from components.event_listener.identity_capture_safe import SafeIdentityCaptureListener
    from langbot_plugin.api.entities import events

    async def run():
        monkeypatch.setattr(lib, "query_seats", AsyncMock(return_value={"success": True, "seats": ["002"], "total_count": 2}))
        plugin = SimpleNamespace(service=SimpleNamespace())
        await select_area(plugin, {"date": "2026-11-01", "start": "14:00", "end": "16:00", "locations": [{"area_id": "30", "location": "701 七层南自习室", "campus": "金明校区"}]})
        listener = SafeIdentityCaptureListener()
        listener.plugin = plugin
        await listener.initialize()
        ctx = context("时间不变，换成明伦第一自习室，先只查。", query=10)
        ctx.event = events.PromptPreProcessing.model_construct(default_prompt=[], prompt=[], session_name="person_alice")
        ctx.get_query_vars = AsyncMock(return_value={
            "user_message_text": "时间不变，换成明伦第一自习室，先只查。",
            "henu_launcher_type": "person", "henu_launcher_id": "alice", "henu_sender_id": "alice",
            "_henu_runtime_context": {"request_query_id": 10, "server_time": {"now_iso": "2026-10-20T17:40:00+08:00"}, "account": {"is_bound": True}},
        })
        await listener.registered_handlers[events.PromptPreProcessing][0](ctx)
        text = "\n".join(str(m.content) for m in ctx.event.default_prompt)
        assert "701 七层南自习室" in text and "2026-11-01" in text
        assert "14:00" in text and "16:00" in text
        assert "alice" not in text and "HENU_ACTIONS" not in text
        # Another sender must not inherit the first sender's menu context.
        other = context("时间不变", query=10, user="bob")
        other.event = events.PromptPreProcessing.model_construct(default_prompt=[], prompt=[], session_name="person_bob")
        other.get_query_vars = AsyncMock(return_value={**ctx.get_query_vars.return_value, "henu_launcher_id": "bob", "henu_sender_id": "bob"})
        await listener.registered_handlers[events.PromptPreProcessing][0](other)
        assert "701 七层南自习室" not in str(other.event.default_prompt)

    asyncio.run(run())


def test_explicit_time_window_survives_campus_and_area_selection(monkeypatch):
    from henu_plugin import library_seat_map as lib

    async def run():
        query = AsyncMock(
            return_value={"success": True, "seats": ["002"], "total_count": 2}
        )
        monkeypatch.setattr(lib, "query_seats", query)
        response = await select_area(
            SimpleNamespace(),
            {
                "date": "2026-11-01",
                "start": "08:00",
                "end": "23:00",
                "locations": [
                    {
                        "area_id": "30",
                        "location": "701 七层南自习室",
                        "campus": "金明校区",
                    }
                ],
            },
        )
        assert query.call_args.args[3:] == ("2026-11-01", "08:00", "23:00")
        assert "选择座位" in str(response.reply.call_args)

    asyncio.run(run())


def test_old_async_map_response_is_discarded_after_new_library_selection(monkeypatch):
    from henu_plugin import library_seat_map as lib

    async def run():
        started, release = asyncio.Event(), asyncio.Event()

        async def query(plugin, sender, area, target="", start="", end=""):
            if not target:
                return {"success": True, "windows": []}
            started.set()
            await release.wait()
            return {"success": True, "seats": ["002"], "total_count": 2}

        monkeypatch.setattr(lib, "query_seats", query)
        plugin = SimpleNamespace()
        rows = {
            "locations": [
                {"area_id": "30", "location": "701 七层南自习室", "campus": "金明校区"}
            ]
        }
        await select_area(plugin, rows)
        previous = context("2026-11-01 08:00-23:00", query=4)
        work = asyncio.create_task(
            lib.handle_library_text(plugin, previous, "2026-11-01 08:00-23:00")
        )
        await asyncio.wait_for(started.wait(), 1)
        await lib.start_selection(
            plugin,
            context(query=5),
            {
                "locations": [
                    {
                        "area_id": "43",
                        "location": "401 第一自习室",
                        "campus": "明伦校区",
                    }
                ]
            },
        )
        release.set()
        await work
        assert previous.reply.await_count == 1  # Only its already-sent loading note.

    asyncio.run(run())


def test_large_campus_uses_floor_names_without_selection_buttons():
    from pathlib import Path
    from henu_plugin import library_seat_map as lib
    from henu_plugin.library_availability import region_counts

    async def run():
        raw = json.loads(
            (
                Path(__file__).parents[1]
                / "docs/evidence/library-regions-20261020.json"
            ).read_text()
        )
        plugin = SimpleNamespace()
        await lib.start_selection(
            plugin, context(), {"locations": region_counts(raw["areas"])}
        )
        campus = context("金明", query=2)
        assert await lib.handle_library_text(plugin, campus, "金明")
        text = campus.reply.call_args.args[0].root[0].text
        assert campus.reply.await_count == 1 and "第 1/" not in text
        assert "金明七楼" in text and "HENU_ACTIONS" not in text
        floor = context("金明七楼", query=3)
        assert await lib.handle_library_text(plugin, floor, "金明七楼")
        text = floor.reply.call_args.args[0].root[0].text
        assert floor.reply.await_count == 1
        assert "701 七层南自习室" in text and "702 七层北自习室" in text
        assert "HENU_ACTIONS" not in text
        other = context("1", query=4, user="bob")
        assert not await lib.handle_library_text(plugin, other, "1")
        assert not await lib.handle_library_text(
            plugin, context("今天课表"), "今天课表"
        )

    asyncio.run(run())

"""QQ controls and the real saved-session HTTP -> notification boundary."""

import asyncio
import json
import re
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import requests
import pytest
from langbot_plugin.api.entities.builtin.platform.message import (
    MessageChain,
    Plain,
    Source,
)

NOW = datetime(2026, 10, 30, 8, tzinfo=ZoneInfo("Asia/Shanghai"))
COMMAND = '设置空位提醒 今天 "701 七层南自习室" 14:00 16:00'


@pytest.fixture(autouse=True)
def stable_elapsed_clock(monkeypatch):
    from henu_plugin import library_seat_alerts as lib

    # Explicit tick times drive ordinary scenarios. Slow-storage cases replace
    # this clock themselves, without changing asyncio's real monotonic clock.
    monkeypatch.setattr(lib, "time", SimpleNamespace(monotonic=lambda: 0))


class Plugin:
    def __init__(self):
        self.storage = {}
        self.messages = []
        self.fail_send = False
        for sender in ("alice", "bob"):
            self.storage[f"user:{sender}:profile"] = json.dumps(
                {"student_id": sender}
            ).encode()
            self.storage[f"user:{sender}:library_cookie"] = json.dumps(
                {"_v4_token": "token-" + sender}
            ).encode()

    async def get_plugin_storage(self, key):
        return self.storage.get(key)

    async def set_plugin_storage(self, key, value):
        self.storage[key] = value

    async def send_message(self, *args):
        self.messages.append(args)
        if self.fail_send:
            raise TimeoutError("synthetic lost receipt")


def context(
    text=COMMAND, *, sender="alice", bot="bot", source="message-1", kind="person"
):
    return SimpleNamespace(
        event=SimpleNamespace(
            sender_id=sender,
            launcher_id=sender,
            launcher_type=kind,
            message_chain=MessageChain([Source(id=source, time=NOW), Plain(text=text)]),
        ),
        get_bot_uuid=AsyncMock(return_value=bot),
        reply=AsyncMock(),
        prevent_default=Mock(),
        prevent_postorder=Mock(),
        is_prevent_postorder=False,
    )


def test_concurrent_users_keep_their_own_saved_sessions_and_receivers(monkeypatch):
    from henu_plugin.library_seat_alerts import LibrarySeatAlerts

    school = School()
    school.install(monkeypatch)

    async def run():
        plugin = Plugin()
        alerts = LibrarySeatAlerts(plugin)
        await asyncio.gather(
            *[
                alerts.handle(context(sender=sender), COMMAND, now=NOW)
                for sender in ("alice", "bob")
            ]
        )
        school.calls.clear()
        school.rows = [{"no": "001", "status": "1", "status_name": "空闲"}]
        await asyncio.gather(
            alerts.tick(NOW + timedelta(minutes=10, seconds=1)),
            LibrarySeatAlerts(plugin).tick(NOW + timedelta(minutes=10, seconds=1)),
        )
        assert sorted(message[2] for message in plugin.messages) == ["alice", "bob"]
        assert len(school.calls) == 4
        headers = [
            str(header) for path, _, header in school.calls if path == "/v4/Space/seat"
        ]
        assert sum("token-alice" in header for header in headers) == 1
        assert sum("token-bob" in header for header in headers) == 1
        registry = json.dumps(await alerts.read())
        assert "token-alice" not in registry and "token-bob" not in registry

    asyncio.run(run())


class School:
    def __init__(self):
        self.rows = [{"no": "001", "status": "2", "status_name": "已预约"}]
        self.calls = []
        self.error = None
        self.slot_type = "3"
        self.code = 0
        self.target_date = "2026-10-30"
        self.start, self.end = "14:00", "16:00"
        self.fixed_slot = False

    def request(self, session, method, url, **kwargs):
        path = urlsplit(url).path
        self.calls.append((path, kwargs.get("json"), dict(session.headers)))
        if self.error == "network":
            raise requests.Timeout("synthetic school outage")
        assert kwargs["allow_redirects"] is False
        if path == "/v4/space/pick":
            data = {
                "area": [
                    {"id": "30", "name": "701 七层南自习室", "premisesName": "金明校区"}
                ]
            }
        elif path == "/v4/Space/map":
            data = {
                "type": "1",
                "date": {
                    "reserveType": self.slot_type,
                    "list": [
                        {
                            "day": self.target_date,
                            "def_start_time": "08:00",
                            "def_end_time": "23:00",
                        }
                    ],
                },
            }
            if self.fixed_slot:
                data["date"]["reserveType"] = "1"
                data["date"]["list"][0]["times"] = [
                    {"id": "all-day", "start": "08:00", "end": "23:00", "status": "1"}
                ]
        elif path == "/v4/Space/seat":
            assert kwargs["json"]["day"] == self.target_date
            assert kwargs["json"]["start_time"] == self.start
            assert kwargs["json"]["end_time"] == self.end
            data = {"list": self.rows}
        else:
            raise AssertionError("Login or school write forbidden: " + path)
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps(
            {"code": 10001 if self.error == "auth" else self.code, "data": data}
        ).encode()
        return response

    def install(self, monkeypatch):
        def request(session, method, url, **kwargs):
            return self.request(session, method, url, **kwargs)

        monkeypatch.setattr(requests.Session, "request", request)


def test_help_examples_can_be_copied_to_set_both_scopes_for_a_school_full_slot(
    monkeypatch,
):
    from henu_plugin.library_seat_alerts import LibrarySeatAlerts

    school = School()
    school.target_date = "2026-10-31"
    school.start, school.end, school.fixed_slot = "08:00", "23:00", True
    school.install(monkeypatch)

    async def run():
        alerts = LibrarySeatAlerts(Plugin())
        help_ctx = context("空位提醒帮助")
        await alerts.handle(help_ctx, "空位提醒帮助", now=NOW)
        page = help_ctx.reply.call_args.args[0].root[0].text
        commands = re.findall(
            r'(?m)^(?:例如：)?(设置空位提醒 (?:今天|明天)[^\n]*(?:\n"[^"\n]+"[^\n]*)?)$',
            page,
        )
        assert commands, "Help must provide a copyable setting, not just grammar"
        for index, command in enumerate(commands):
            await alerts.handle(
                context(command, source=f"copied-{index}"), command, now=NOW
            )
            plan = await alerts.status("bot", "alice")
            assert plan["state"] == "active"
            assert (plan["start"], plan["end"]) == ("08:00", "23:00")
            assert bool(plan["seat_no"]) == bool(index)
            await alerts.handle(
                context("取消空位提醒", source=f"cancel-copy-{index}"),
                "取消空位提醒",
                now=NOW,
            )
        assert len(commands) == 2, (
            "Region and single-seat settings need separate examples"
        )

    asyncio.run(run())


def test_expiry_while_waiting_for_query_capacity_does_not_contact_school(monkeypatch):
    from henu_plugin import library_seat_alerts as lib

    elapsed = [0]
    monkeypatch.setattr(lib, "time", SimpleNamespace(monotonic=lambda: elapsed[0]))
    school = School()
    school.install(monkeypatch)

    async def run():
        plugin = Plugin()
        alerts = lib.LibrarySeatAlerts(plugin)
        await alerts.handle(context(), COMMAND, now=NOW)
        school.calls.clear()
        for _ in range(4):
            await alerts.query_slots.acquire()
        scan = asyncio.create_task(alerts.tick(NOW.replace(hour=13, minute=59)))
        while not alerts.workers:
            await asyncio.sleep(0)
        await asyncio.sleep(0)
        elapsed[0] = 120
        for _ in range(4):
            alerts.query_slots.release()
        await scan
        assert not school.calls and not plugin.messages
        assert (await alerts.status("bot", "alice"))["state"] == "expired"

    asyncio.run(run())


@pytest.mark.parametrize("delay_at", ["bot_route", "preparing"])
def test_creation_accounts_for_time_spent_before_school_queries(monkeypatch, delay_at):
    from henu_plugin import library_seat_alerts as lib

    elapsed = [0]
    monkeypatch.setattr(lib, "time", SimpleNamespace(monotonic=lambda: elapsed[0]))
    school = School()
    school.install(monkeypatch)

    class SlowPreparing(Plugin):
        async def set_plugin_storage(self, key, value):
            if (
                delay_at == "preparing"
                and key == lib.KEY
                and any(
                    row.get("state") == "preparing"
                    for row in json.loads(value).values()
                )
            ):
                elapsed[0] = 120
            await super().set_plugin_storage(key, value)

    async def run():
        plugin = SlowPreparing()
        alerts = lib.LibrarySeatAlerts(plugin)
        now = NOW.replace(hour=13, minute=59)
        ctx = context()
        ctx.event.message_chain = MessageChain(
            [Source(id="late-setting", time=now), Plain(text=COMMAND)]
        )

        async def route():
            if delay_at == "bot_route":
                elapsed[0] = 120
            return "bot"

        ctx.get_bot_uuid = route
        await alerts.handle(ctx, COMMAND, now=now)
        assert not school.calls
        assert (await alerts.status("bot", "alice"))["state"] != "active"

    asyncio.run(run())


@pytest.mark.parametrize("delay_at", ["claim", "registry", "session"])
def test_slow_storage_cannot_query_or_notify_after_expiry(monkeypatch, delay_at):
    from henu_plugin import library_seat_alerts as lib

    elapsed = [0]
    monkeypatch.setattr(lib, "time", SimpleNamespace(monotonic=lambda: elapsed[0]))
    school = School()
    school.install(monkeypatch)

    class SlowStorage(Plugin):
        slow = False

        async def get_plugin_storage(self, key):
            if self.slow and (
                (delay_at == "registry" and key == lib.KEY)
                or (delay_at == "session" and key == "user:alice:profile")
            ):
                elapsed[0] = 120
            return await super().get_plugin_storage(key)

        async def set_plugin_storage(self, key, value):
            if (
                self.slow
                and delay_at == "claim"
                and key == lib.KEY
                and any(
                    row.get("last_delivery") == "unknown"
                    for row in json.loads(value).values()
                )
            ):
                elapsed[0] = 120
            await super().set_plugin_storage(key, value)

    async def run():
        plugin = SlowStorage()
        alerts = lib.LibrarySeatAlerts(plugin)
        await alerts.handle(context(), COMMAND, now=NOW)
        plugin.slow = True
        school.calls.clear()
        school.rows = [{"no": "001", "status": "1", "status_name": "空闲"}]
        await alerts.tick(NOW.replace(hour=13, minute=59))
        assert not plugin.messages
        assert (await alerts.status("bot", "alice"))["state"] == "expired"
        if delay_at != "claim":
            assert not school.calls

    asyncio.run(run())


def test_serialized_sdk_source_can_set_and_cancel_alerts(monkeypatch):
    from henu_plugin.library_seat_alerts import LibrarySeatAlerts

    school = School()
    school.install(monkeypatch)

    async def run():
        plugin = Plugin()
        alerts = LibrarySeatAlerts(plugin)
        for text, source, expected in [
            (COMMAND, "set-serialized", "active"),
            ("取消空位提醒", "cancel-serialized", "cancelled"),
        ]:
            ctx = context(text, source=source)
            ctx.event.message_chain = MessageChain.model_validate(
                [item.model_dump(mode="json") for item in ctx.event.message_chain.root]
            )
            assert ctx.event.message_chain.root[0].time.tzinfo is None
            assert await alerts.handle(ctx, text, now=NOW)
            assert (await alerts.status("bot", "alice"))["state"] == expected

    asyncio.run(run())


def test_region_alert_continues_without_duplicate_checks_or_notifications(monkeypatch):
    from henu_plugin.library_seat_alerts import LibrarySeatAlerts

    school = School()
    school.install(monkeypatch)

    async def run():
        plugin = Plugin()
        alerts = LibrarySeatAlerts(plugin)
        ctx = context()
        assert await alerts.handle(ctx, COMMAND, now=NOW)
        assert "已开启" in str(ctx.reply.call_args)
        assert (await alerts.status("bot", "alice"))["interval_minutes"] == 10
        school.rows = [{"no": "001", "status": "1", "status_name": "空闲"}]
        count = len(school.calls)
        await alerts.tick(NOW + timedelta(minutes=9, seconds=1))
        assert len(school.calls) == count
        await alerts.tick(NOW + timedelta(minutes=10, seconds=1))
        assert len(plugin.messages) == 1
        assert plugin.messages[0][:3] == ("bot", "person", "alice")
        assert "001" in str(plugin.messages[0]) and "14:00" in str(plugin.messages[0])
        await LibrarySeatAlerts(plugin).tick(NOW + timedelta(minutes=20, seconds=1))
        assert len(plugin.messages) == 1
        assert (await alerts.status("bot", "alice"))["state"] == "active"

    asyncio.run(run())


@pytest.mark.parametrize(
    "unknown", ["false_code", "duplicate", "missing", "boolean_status"]
)
def test_malformed_or_missing_observations_never_rearm_an_already_free_seat(
    monkeypatch, unknown
):
    from henu_plugin.library_seat_alerts import LibrarySeatAlerts

    school = School()
    school.rows = [{"no": "001", "status": "1", "status_name": "空闲"}]
    school.install(monkeypatch)

    async def run():
        plugin = Plugin()
        alerts = LibrarySeatAlerts(plugin)
        command = COMMAND + " 001"
        await alerts.handle(context(command), command, now=NOW)
        if unknown == "false_code":
            school.code = False
        elif unknown == "duplicate":
            school.rows = [
                {"no": "001", "status": "2", "status_name": "已预约"},
                {"no": "1", "status": "2", "status_name": "已预约"},
            ]
        elif unknown == "missing":
            school.rows = [{"no": "002", "status": "2", "status_name": "已预约"}]
        else:
            school.rows = [{"no": "001", "status": False, "status_name": "已占用"}]
        await alerts.tick(NOW + timedelta(minutes=10, seconds=1))
        school.code = 0
        school.rows = [{"no": "001", "status": "1", "status_name": "空闲"}]
        await alerts.tick(NOW + timedelta(minutes=20, seconds=1))
        assert not plugin.messages
        assert (await alerts.status("bot", "alice"))["state"] == "active"

    asyncio.run(run())


@pytest.mark.parametrize(
    "case",
    [
        "missing_id",
        "missing_seat",
        "fixed_window",
        "bad_interval",
        "past_time",
        "bad_date",
    ],
)
def test_invalid_scope_or_window_never_enables_an_alert(monkeypatch, case):
    from henu_plugin.library_seat_alerts import LibrarySeatAlerts

    school = School()
    school.install(monkeypatch)
    command = COMMAND
    if case == "missing_id":
        command = COMMAND.replace("701 七层南自习室", "3")
    elif case == "missing_seat":
        command += " 999"
    elif case == "fixed_window":
        school.slot_type = "1"
    elif case == "bad_interval":
        command += " 全部 1"
    elif case == "past_time":
        command = COMMAND.replace("14:00", "07:00")
    else:
        command = COMMAND.replace("今天", "2026-02-31")

    async def run():
        plugin = Plugin()
        alerts = LibrarySeatAlerts(plugin)
        await alerts.handle(context(command), command, now=NOW)
        assert (await alerts.status("bot", "alice"))["state"] == "none"
        assert not plugin.messages

    asyncio.run(run())


def test_ambiguous_send_is_not_replayed_but_later_new_seats_can_notify(monkeypatch):
    from henu_plugin.library_seat_alerts import LibrarySeatAlerts

    school = School()
    school.install(monkeypatch)

    async def run():
        plugin = Plugin()
        alerts = LibrarySeatAlerts(plugin)
        await alerts.handle(context(), COMMAND, now=NOW)
        plugin.fail_send = True
        school.rows = [{"no": "001", "status": "1", "status_name": "空闲"}]
        await alerts.tick(NOW + timedelta(minutes=10, seconds=1))
        assert (await alerts.status("bot", "alice"))["last_delivery"] == "unknown"
        plugin.fail_send = False
        await LibrarySeatAlerts(plugin).tick(NOW + timedelta(minutes=20, seconds=1))
        assert len(plugin.messages) == 1
        school.rows.append({"no": "002", "status": "1", "status_name": "空闲"})
        await alerts.tick(NOW + timedelta(minutes=30, seconds=1))
        assert len(plugin.messages) == 2
        assert (await alerts.status("bot", "alice"))["last_delivery"] == "sent"

    asyncio.run(run())


@pytest.mark.parametrize("during_creation", [False, True])
def test_cancellation_during_slow_school_query_wins(monkeypatch, during_creation):
    import threading
    from henu_plugin.library_seat_alerts import LibrarySeatAlerts

    school = School()
    school.install(monkeypatch)
    entered, release = threading.Event(), threading.Event()
    original = school.request

    def slow(session, method, url, **kwargs):
        if urlsplit(url).path == "/v4/Space/seat":
            entered.set()
            assert release.wait(3), "Cancellation blocked behind a global registry lock"
        return original(session, method, url, **kwargs)

    async def run():
        plugin = Plugin()
        alerts = LibrarySeatAlerts(plugin)
        if not during_creation:
            await alerts.handle(context(), COMMAND, now=NOW)
        school.request = slow
        school.rows = [{"no": "001", "status": "1", "status_name": "空闲"}]
        job = asyncio.create_task(
            alerts.handle(context(), COMMAND, now=NOW)
            if during_creation
            else alerts.tick(NOW + timedelta(minutes=10, seconds=1))
        )
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            await asyncio.wait_for(
                alerts.handle(
                    context("取消空位提醒", source="cancel-pending"),
                    "取消空位提醒",
                    now=NOW,
                ),
                1,
            )
        finally:
            release.set()
        await job
        assert (await alerts.status("bot", "alice"))["state"] == "cancelled"
        assert not plugin.messages
        await alerts.handle(context(), COMMAND, now=NOW)
        assert (await alerts.status("bot", "alice"))["state"] == "cancelled"

    asyncio.run(run())


def test_initial_free_seats_are_shown_once_and_new_seats_still_alert(monkeypatch):
    from henu_plugin.library_seat_alerts import LibrarySeatAlerts

    school = School()
    school.rows = [{"no": "001", "status": "1", "status_name": "空闲"}]
    school.install(monkeypatch)

    async def run():
        plugin = Plugin()
        alerts = LibrarySeatAlerts(plugin)
        ctx = context()
        await alerts.handle(ctx, COMMAND, now=NOW)
        assert "001" in str(ctx.reply.call_args)
        await alerts.tick(NOW + timedelta(minutes=10, seconds=1))
        assert not plugin.messages
        school.rows.append({"no": "002", "status": "1", "status_name": "空闲"})
        await alerts.tick(NOW + timedelta(minutes=20, seconds=1))
        assert len(plugin.messages) == 1
        assert "002" in str(plugin.messages[0]) and "001" not in str(plugin.messages[0])

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["auth", "session", "account", "guard", "network"])
def test_login_pauses_and_transient_errors_preserve_free_seat_history(
    monkeypatch, failure
):
    from henu_plugin.library_seat_alerts import LibrarySeatAlerts

    school = School()
    school.install(monkeypatch)

    async def run():
        plugin = Plugin()
        alerts = LibrarySeatAlerts(plugin)
        await alerts.handle(context(), COMMAND, now=NOW)
        school.rows = [{"no": "001", "status": "1", "status_name": "空闲"}]
        await alerts.tick(NOW + timedelta(minutes=10, seconds=1))
        assert len(plugin.messages) == 1
        if failure in {"auth", "network"}:
            school.error = failure
        elif failure == "session":
            plugin.storage["user:alice:library_cookie"] = b'{"_v4_token":"new-token"}'
        elif failure == "account":
            plugin.storage["user:alice:profile"] = b'{"student_id":"different-student"}'
        else:
            plugin.storage["user:alice:login_attempt"] = b'{"status":"failed"}'
        await alerts.tick(NOW + timedelta(minutes=20, seconds=1))
        if failure == "network":
            assert len(plugin.messages) == 1
            assert "未知" in (await alerts.status("bot", "alice"))["last_result"]
            school.error = None
            await alerts.tick(NOW + timedelta(minutes=30, seconds=1))
            assert len(plugin.messages) == 1
            assert (await alerts.status("bot", "alice"))["state"] == "active"
        else:
            assert (await alerts.status("bot", "alice"))["state"] == "paused"
            assert len(plugin.messages) == 2 and "已暂停" in str(plugin.messages[-1])
            count = len(school.calls)
            await LibrarySeatAlerts(plugin).tick(NOW + timedelta(minutes=30, seconds=1))
            assert len(school.calls) == count and len(plugin.messages) == 2

    asyncio.run(run())


def test_status_cancel_and_expiry_are_scoped_and_stop_school_queries(monkeypatch):
    from henu_plugin.library_seat_alerts import LibrarySeatAlerts

    school = School()
    school.install(monkeypatch)

    async def run():
        plugin = Plugin()
        alerts = LibrarySeatAlerts(plugin)
        await alerts.handle(context(), COMMAND, now=NOW)
        for sender, bot in [("bob", "bot"), ("alice", "other-bot")]:
            cancel = context(
                "取消空位提醒", sender=sender, bot=bot, source="cancel-other"
            )
            assert await alerts.handle(cancel, "取消空位提醒", now=NOW)
        assert (await alerts.status("bot", "alice"))["state"] == "active"
        status = context("空位提醒状态")
        assert await alerts.handle(status, "空位提醒状态", now=NOW)
        assert "701 七层南自习室" in str(status.reply.call_args)
        assert "10分钟" in str(status.reply.call_args)
        count = len(school.calls)
        cancel = context("取消空位提醒", source="cancel-own")
        assert await alerts.handle(cancel, "取消空位提醒", now=NOW)
        await alerts.tick(NOW + timedelta(minutes=10, seconds=1))
        assert len(school.calls) == count
        assert (await alerts.status("bot", "alice"))["state"] == "cancelled"
        # A delayed replay of the original setting cannot re-enable a cancellation.
        await alerts.handle(context(), COMMAND, now=NOW)
        assert (await alerts.status("bot", "alice"))["state"] == "cancelled"
        await alerts.handle(context(source="new-setting"), COMMAND, now=NOW)
        count = len(school.calls)
        await alerts.tick(NOW.replace(hour=14))
        assert len(school.calls) == count and not plugin.messages
        assert (await alerts.status("bot", "alice"))["state"] == "expired"

    asyncio.run(run())


@pytest.mark.parametrize(
    "invalid", ["group", "bot", "sender", "launcher", "stale", "handled"]
)
def test_alert_settings_require_current_private_identity(monkeypatch, invalid):
    from henu_plugin.library_seat_alerts import LibrarySeatAlerts

    school = School()
    school.install(monkeypatch)

    async def run():
        plugin = Plugin()
        alerts = LibrarySeatAlerts(plugin)
        ctx = context()
        if invalid == "group":
            ctx.event.launcher_type = "group"
        elif invalid == "bot":
            ctx.get_bot_uuid.return_value = ""
        elif invalid == "sender":
            ctx.event.sender_id = "unknown"
        elif invalid == "launcher":
            ctx.event.launcher_id = "other"
        elif invalid == "stale":
            ctx.event.message_chain = MessageChain(
                [Source(id="old", time=NOW - timedelta(minutes=3)), Plain(text=COMMAND)]
            )
        else:
            ctx.is_prevent_postorder = True
        assert await alerts.handle(ctx, COMMAND, now=NOW)
        assert not school.calls and not plugin.messages
        assert (await alerts.status("bot", "alice"))["state"] == "none"

    asyncio.run(run())


def test_single_seat_realerts_only_after_confirmed_occupation_at_five_minute_intervals(
    monkeypatch,
):
    from henu_plugin.library_seat_alerts import LibrarySeatAlerts

    school = School()
    school.install(monkeypatch)

    async def run():
        plugin = Plugin()
        alerts = LibrarySeatAlerts(plugin)
        command = COMMAND + " 1 5"
        ctx = context(command)
        await alerts.handle(ctx, command, now=NOW)
        assert "每5分钟" in str(ctx.reply.call_args)
        for minutes, state, name in [
            (5, "1", "空闲"),
            (10, "1", "空闲"),
            (15, "1", "已预约"),
            (20, "1", "空闲"),
            (25, "2", "已预约"),
            (30, "1", "空闲"),
        ]:
            school.rows = [
                {"no": "001", "status": state, "status_name": name},
                {"no": "002", "status": "1", "status_name": "空闲"},
            ]
            await alerts.tick(NOW + timedelta(minutes=minutes, seconds=1))
        assert len(plugin.messages) == 2
        assert all(
            "001" in str(message) and "002" not in str(message)
            for message in plugin.messages
        )
        assert (await alerts.status("bot", "alice"))["seat_no"] == "001"

    asyncio.run(run())

import asyncio
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from henu_plugin.library_automation import LibraryAutomation

ZONE = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 10, 27, 6, 59, tzinfo=ZONE)


async def campus_subject(sender):
    return "campus-student"


ARGS = dict(
    run_at="2026-10-27T07:00",
    target_date="2026-10-28",
    location="明伦三楼",
    seat_no="001",
    start="08:00",
    end="10:00",
)


class Plugin:
    def __init__(self):
        self.storage = {}

    async def get_plugin_storage(self, key):
        return self.storage.get(key)

    async def set_plugin_storage(self, key, value):
        self.storage[key] = value


def test_confirmed_plan_executes_once_after_restart():
    async def run():
        plugin = Plugin()
        calls = []

        async def authorize(bot, sender):
            return "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"

        async def reserve(sender, plan):
            calls.append((sender, plan))
            return {"success": True, "msg": "预约成功"}

        jobs = LibraryAutomation(
            plugin, campus_subject=campus_subject, authorize=authorize, reserve=reserve
        )
        preview = await jobs.prepare("bot", "alice", ARGS, "source1", NOW)
        assert preview["state"] == "pending"
        await jobs.tick(NOW.replace(hour=7, minute=0))
        assert not calls
        confirmed = await jobs.confirm("bot", "alice", preview["token"], "source2", NOW)
        assert confirmed["state"] == "scheduled"
        await jobs.tick(NOW.replace(hour=7, minute=0))
        await LibraryAutomation(
            plugin, campus_subject=campus_subject, authorize=authorize, reserve=reserve
        ).tick(NOW.replace(hour=7, minute=0))
        assert len(calls) == 1
        assert (await jobs.status("bot", "alice"))["state"] == "success"

    asyncio.run(run())


def test_revoked_or_rebound_account_never_executes():
    async def run():
        for replacement in ("denied", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"):
            plugin = Plugin()
            current = ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"]
            calls = []

            async def authorize(bot, sender):
                if current[0] == "denied":
                    raise TimeoutError()
                return current[0]

            async def reserve(sender, plan):
                calls.append(plan)

            jobs = LibraryAutomation(
                plugin,
                campus_subject=campus_subject,
                authorize=authorize,
                reserve=reserve,
            )
            preview = await jobs.prepare("bot", "alice", ARGS, "s1", NOW)
            await jobs.confirm("bot", "alice", preview["token"], "s2", NOW)
            current[0] = replacement
            await jobs.tick(NOW.replace(hour=7, minute=0))
            assert not calls
            assert (await jobs.status("bot", "alice"))[
                "state"
            ] == "authorization_failed"

    asyncio.run(run())


def test_timeout_never_retries_and_cancel_is_scoped():
    async def run():
        plugin = Plugin()
        calls = []

        async def authorize(bot, sender):
            return "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"

        async def reserve(sender, plan):
            calls.append(sender)
            raise TimeoutError()

        jobs = LibraryAutomation(
            plugin, campus_subject=campus_subject, authorize=authorize, reserve=reserve
        )
        preview = await jobs.prepare("bot", "alice", ARGS, "s1", NOW)
        await jobs.confirm("bot", "alice", preview["token"], "s2", NOW)
        await jobs.cancel("otherbot", "alice")
        await jobs.cancel("bot", "bob")
        await jobs.tick(NOW.replace(hour=7, minute=0))
        await LibraryAutomation(
            plugin, campus_subject=campus_subject, authorize=authorize, reserve=reserve
        ).tick(NOW.replace(hour=7, minute=1))
        assert calls == ["alice"]
        assert (await jobs.status("bot", "alice"))["state"] == "failed_or_unknown"

    asyncio.run(run())


def test_different_message_and_owner_required_for_confirmation():
    import pytest

    async def run():
        async def authorize(bot, sender):
            return "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"

        jobs = LibraryAutomation(
            Plugin(), campus_subject=campus_subject, authorize=authorize
        )
        preview = await jobs.prepare("bot", "alice", ARGS, "s1", NOW)
        for bot, sender, source in [
            ("bot", "alice", "s1"),
            ("bot", "bob", "s2"),
            ("otherbot", "alice", "s2"),
        ]:
            with pytest.raises(ValueError):
                await jobs.confirm(bot, sender, preview["token"], source, NOW)
        await jobs.cancel("bot", "alice")
        with pytest.raises(ValueError):
            await jobs.confirm("bot", "alice", preview["token"], "s2", NOW)

    asyncio.run(run())


def test_private_message_flow_sets_and_confirms_a_real_sdk_source():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from langbot_plugin.api.entities.builtin.platform.message import (
        MessageChain,
        Plain,
        Source,
    )

    async def run():
        plugin = Plugin()

        async def authorize(bot, sender):
            return "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"

        jobs = LibraryAutomation(
            plugin, campus_subject=campus_subject, authorize=authorize
        )
        now = datetime.now(ZONE)
        args = dict(
            ARGS,
            run_at=(
                now.replace(second=0, microsecond=0) + timedelta(minutes=5)
            ).strftime("%Y-%m-%dT%H:%M"),
            target_date=(now + timedelta(days=1)).strftime("%Y-%m-%d"),
        )
        command = "设置自动预约 " + " ".join(
            args[key]
            for key in ("run_at", "target_date", "location", "seat_no", "start", "end")
        )

        def ctx(text, ident):
            return SimpleNamespace(
                event=SimpleNamespace(
                    sender_id="alice",
                    launcher_id="alice",
                    launcher_type="person",
                    message_chain=MessageChain(
                        root=[Source(id=ident, time=now), Plain(text=text)]
                    ),
                ),
                get_bot_uuid=AsyncMock(return_value="bot"),
                reply=AsyncMock(),
                prevent_default=lambda: None,
                prevent_postorder=lambda: None,
            )

        first = ctx(command, "message1")
        assert await jobs.handle(first)
        plan = await jobs.status("bot", "alice")
        assert plan["state"] == "pending"
        assert args["location"] in str(first.reply.call_args)
        second = ctx("确认自动预约 " + plan["token"], "message2")
        assert await jobs.handle(second)
        assert (await jobs.status("bot", "alice"))["state"] == "scheduled"
        group = ctx("自动预约状态", "message3")
        assert await jobs.handle(group, is_group=True)
        assert args["location"] not in str(group.reply.call_args)

    asyncio.run(run())


def test_two_coordinators_share_claim_lock():
    async def run():
        plugin = Plugin()
        calls = []

        async def authorize(bot, sender):
            await asyncio.sleep(0)
            return "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"

        async def reserve(sender, plan):
            calls.append(sender)
            await asyncio.sleep(0)
            return {"success": True}

        first = LibraryAutomation(
            plugin, campus_subject=campus_subject, authorize=authorize, reserve=reserve
        )
        second = LibraryAutomation(
            plugin, campus_subject=campus_subject, authorize=authorize, reserve=reserve
        )
        preview = await first.prepare("bot", "alice", ARGS, "s1", NOW)
        await first.confirm("bot", "alice", preview["token"], "s2", NOW)
        await asyncio.gather(
            first.tick(NOW.replace(hour=7, minute=0)),
            second.tick(NOW.replace(hour=7, minute=0)),
        )
        assert calls == ["alice"]

    asyncio.run(run())


def test_slow_authorization_misses_window(monkeypatch):
    async def run():
        elapsed = [100.0]
        monkeypatch.setattr("time.monotonic", lambda: elapsed[0])

        async def authorize(bot, sender):
            return "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"

        calls = []

        async def reserve(sender, plan):
            calls.append(sender)

        jobs = LibraryAutomation(
            Plugin(),
            campus_subject=campus_subject,
            authorize=authorize,
            reserve=reserve,
        )
        preview = await jobs.prepare("bot", "alice", ARGS, "s1", NOW)
        await jobs.confirm("bot", "alice", preview["token"], "s2", NOW)

        async def slow_authorize(bot, sender):
            elapsed[0] += 95
            return await authorize(bot, sender)

        jobs.authorize = slow_authorize
        await jobs.tick(NOW.replace(hour=7, minute=0))
        assert not calls
        assert (await jobs.status("bot", "alice"))["state"] == "missed"

    asyncio.run(run())


def test_strict_reservation_rejects_school_time_changes_before_submit(monkeypatch):
    import requests
    from urllib.parse import urlsplit
    from campus_core.bot import HenuCampusBot

    for opening, requested, deadline in [
        ("09:00", "08:00", "2099-01-01T00:00:00+08:00"),
        ("08:00", "08:00", "2020-01-01T00:00:00+08:00"),
        ("08:00", "08:00", "2099-01-01T00:00:00+08:00"),
    ]:
        writes = []

        def request(self, method, url, **kwargs):
            path = urlsplit(url).path
            if path == "/v4/space/pick":
                data = {"area": []}
            elif path == "/v4/Space/map":
                data = {
                    "type": "1",
                    "date": {
                        "reserveType": "3",
                        "list": [
                            {
                                "day": "2026-11-01",
                                "def_start_time": opening,
                                "def_end_time": "11:00",
                            }
                        ],
                    },
                }
            elif path == "/v4/Space/seat":
                data = {"list": [{"id": "seat1", "no": "001", "status": "1"}]}
            elif path == "/v4/space/confirm":
                writes.append(kwargs["json"])
                response = requests.Response()
                response.status_code = 200
                response._content = b'{"code":1,"message":"declined"}'
                return response
            else:
                raise AssertionError("unexpected school operation " + path)
            response = requests.Response()
            response.status_code = 200
            response._content = json.dumps({"code": 0, "data": data}).encode()
            return response

        monkeypatch.setattr(requests.Session, "request", request)
        bot = HenuCampusBot("student", "", {"_v4_token": "fake"})
        result = bot.reserve(
            "30",
            "001",
            "2026-11-01",
            preferred_time=requested,
            preferred_end_time="10:00",
            strict_window=True,
            submit_before=deadline,
            max_attempts=1,
        )
        assert result["success"] is False
        if opening == "09:00" or deadline.startswith("2020"):
            assert not writes
        else:
            assert len(writes) == 1
            from Crypto.Cipher import AES
            from Crypto.Util.Padding import unpad
            import base64

            day = datetime.now(ZONE).strftime("%Y%m%d")
            payload = json.loads(
                unpad(
                    AES.new(
                        (day + day[::-1]).encode(), AES.MODE_CBC, b"ZZWBKJ_ZHIHUAWEI"
                    ).decrypt(base64.b64decode(writes[0]["aesjson"])),
                    16,
                )
            )
            assert payload["start_time"] == "08:00" and payload["end_time"] == "10:00"

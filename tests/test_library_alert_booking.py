"""Pre-authorized alerts at the QQ and real school/Kit HTTP boundaries."""

import asyncio
import base64
import json
import re
from datetime import timedelta
from urllib.parse import urlsplit

import pytest
import requests

from tests.test_library_seat_alerts import COMMAND, NOW, Plugin, School, context

UID = "00000000-0000-4000-8000-000000000001"


class BookingSchool(School):
    def __init__(self):
        super().__init__()
        self.lifetime = True
        self.bound = True
        self.uid = UID
        self.writes = []
        self.lose_receipt = False
        self.confirm_code = 0
        self.confirm_status = 200
        self.on_entitlement = lambda: None
        self.on_submit = lambda: None
        self.on_preflight = lambda: None
        self.rows = [
            {"id": "seat1", "no": "001", "status": "2", "status_name": "已预约"}
        ]

    @staticmethod
    def response(data):
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps(data).encode()
        response._content_consumed = True
        return response

    def request(self, session, method, url, **kwargs):
        parsed = urlsplit(url)
        if parsed.netloc == "kit.test":
            if parsed.path.endswith("/resolve"):
                return self.response(
                    {"data": {"bound": self.bound, "user_id": self.uid}}
                )
            assert parsed.path.endswith("/membership-entitlement")
            self.on_entitlement()
            return self.response(
                {"data": {"user_id": self.uid, "lifetime": self.lifetime}}
            )
        assert parsed.netloc == "zwyy.henu.edu.cn", "No login allowed"
        if parsed.path == "/v4/space/pick":
            self.on_preflight()
        if parsed.path == "/v4/space/confirm":
            from Crypto.Cipher import AES
            from Crypto.Util.Padding import unpad

            assert kwargs["allow_redirects"] is False
            payload = kwargs["json"]
            if "aesjson" in payload:
                payload = json.loads(
                    unpad(
                        AES.new(
                            b"2026103003016202", AES.MODE_CBC, b"ZZWBKJ_ZHIHUAWEI"
                        ).decrypt(base64.b64decode(payload["aesjson"])),
                        16,
                    )
                )
            self.writes.append(payload)
            self.on_submit()
            if self.lose_receipt:
                raise requests.Timeout("synthetic lost school receipt")
            response = self.response({"code": self.confirm_code, "data": {}})
            response.status_code = self.confirm_status
            return response
        if parsed.path == "/v4/index/subscribe":
            return self.response(
                {
                    "code": 0,
                    "data": [
                        {
                            "no": "001",
                            "areaName": "701 七层南自习室",
                            "day": self.target_date,
                        }
                    ],
                }
            )
        return super().request(session, method, url, **kwargs)


@pytest.fixture
def booking_school(monkeypatch):
    from campus_core import seat_reservation, auth
    from henu_plugin import library_seat_alerts
    from types import SimpleNamespace

    monkeypatch.setattr(
        library_seat_alerts, "time", SimpleNamespace(monotonic=lambda: 0)
    )
    monkeypatch.setattr(
        seat_reservation, "_now_dt", lambda: NOW + timedelta(minutes=10)
    )
    monkeypatch.setattr(auth, "_now_dt", lambda: NOW + timedelta(minutes=10))
    for prefix, fields in {
        "HENU_KIT_": {
            "CORE_URL": "https://kit.test",
            "PORTAL_URL": "https://kit.test",
            "CLIENT_ID": "fixture",
            "KEY_ID": "fixture",
            "SECRET": "x" * 32,
            "BOT_UUID": "bot",
        },
        "HENU_KIT_ENTITLEMENT_": {
            "URL": "https://kit.test",
            "CLIENT_ID": "fixture",
            "KEY_ID": "fixture",
            "SECRET": "x" * 32,
        },
    }.items():
        for key, value in fields.items():
            monkeypatch.setenv(prefix + key, value)
    school = BookingSchool()
    school.install(monkeypatch)
    return school


def owned_plugin():
    plugin = Plugin()
    for sender in ("alice", "bob"):
        key = f"user:{sender}:library_cookie"
        cookies = json.loads(plugin.storage[key])
        cookies["_subject_student_id"] = sender
        plugin.storage[key] = json.dumps(cookies).encode()
    return plugin


async def arm(alerts, *, seat="", source="set", confirm=True):
    command = COMMAND + (" " + seat if seat else "")
    request = context(command, source=source)
    await alerts.handle(request, command, now=NOW)
    page = str(request.reply.call_args)
    assert "自动预约" in page and "仅提醒" in page
    request = context("有位自动预约", source=source + "-choose")
    await alerts.handle(request, "有位自动预约", now=NOW)
    page = str(request.reply.call_args)
    token = re.search(r"确认有位自动预约 ([a-f0-9]{16})", page)
    assert token, page
    if confirm:
        command = "确认有位自动预约 " + token[1]
        confirmed = context(command, source=source + "-confirm")
        await alerts.handle(confirmed, command, now=NOW)
        assert "模式：有位自动预约" in str(confirmed.reply.call_args), (
            confirmed.reply.call_args
        )
    return token[1]


@pytest.mark.parametrize("seat", ["", "001"])
@pytest.mark.parametrize("initially_free", [False, True])
def test_confirm_once_then_available_seat_is_reserved_once_even_after_restart(
    booking_school, seat, initially_free
):
    from henu_plugin.library_seat_alerts import LibrarySeatAlerts

    async def run():
        plugin = owned_plugin()
        alerts = LibrarySeatAlerts(plugin)
        if initially_free:
            booking_school.rows[0].update(status="1", status_name="空闲")
        await arm(alerts, seat=seat)
        assert not booking_school.writes
        booking_school.rows[0].update(status="1", status_name="空闲")
        await asyncio.gather(
            alerts.tick(NOW + timedelta(minutes=10)),
            LibrarySeatAlerts(plugin).tick(NOW + timedelta(minutes=10)),
        )
        status = await alerts.status("bot", "alice")
        assert len(booking_school.writes) == 1, (
            status["state"],
            status.get("last_result"),
            [x[0] for x in booking_school.calls],
        )
        assert booking_school.writes[0]["seat_id"] == "seat1"
        assert (
            booking_school.writes[0]["day"],
            booking_school.writes[0]["start_time"],
            booking_school.writes[0]["end_time"],
        ) == ("2026-10-30", "14:00", "16:00")
        assert (await alerts.status("bot", "alice"))["state"] == "reserved"
        await LibrarySeatAlerts(plugin).tick(NOW + timedelta(minutes=20))
        assert len(booking_school.writes) == 1
        assert "预约成功" in str(plugin.messages)

    asyncio.run(run())


@pytest.mark.parametrize("applied", [False, True])
def test_unconfirmed_durable_claim_never_contacts_school(booking_school, applied):
    from henu_plugin.library_seat_alerts import LibrarySeatAlerts, KEY

    class UncertainStorage(Plugin):
        async def set_plugin_storage(self, key, value):
            if key == KEY and any(
                p.get("state") == "booking" for p in json.loads(value).values()
            ):
                if applied:
                    await super().set_plugin_storage(key, value)
                raise TimeoutError("synthetic ambiguous storage receipt")
            await super().set_plugin_storage(key, value)

    async def run():
        plugin = UncertainStorage()
        plugin.storage.update(owned_plugin().storage)
        alerts = LibrarySeatAlerts(plugin)
        await arm(alerts)
        booking_school.rows[0].update(status="1", status_name="空闲")
        await alerts.tick(NOW + timedelta(minutes=10))
        await LibrarySeatAlerts(plugin).tick(NOW + timedelta(minutes=20))
        assert not booking_school.writes

    asyncio.run(run())


@pytest.mark.parametrize(
    "stage", ["member_check", "school_preflight", "submit_member", "school_submit"]
)
def test_cancellation_wins_before_submission_and_does_not_claim_to_revoke_a_started_request(
    booking_school, stage
):
    import threading
    from henu_plugin.library_seat_alerts import LibrarySeatAlerts

    entered, release = threading.Event(), threading.Event()

    def wait():
        entered.set()
        assert release.wait(3)

    async def run():
        plugin = owned_plugin()
        alerts = LibrarySeatAlerts(plugin)
        await arm(alerts)
        if stage == "member_check":
            booking_school.on_entitlement = wait
        elif stage == "school_preflight":
            booking_school.on_preflight = wait
        elif stage == "submit_member":
            calls = [0]

            def final_member():
                calls[0] += 1
                if calls[0] == 2:
                    wait()

            booking_school.on_entitlement = final_member
        else:
            booking_school.on_submit = wait
        booking_school.rows[0].update(status="1", status_name="空闲")
        scan = asyncio.create_task(alerts.tick(NOW + timedelta(minutes=10)))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            request = context("取消空位提醒", source="cancel-race")
            await alerts.handle(request, "取消空位提醒", now=NOW)
            if stage == "school_submit":
                assert "不会撤回" in str(request.reply.call_args)
        finally:
            release.set()
        await scan
        assert len(booking_school.writes) == int(stage == "school_submit")
        status = await alerts.status("bot", "alice")
        assert status["state"] == (
            "reserved" if stage == "school_submit" else "cancelled"
        )
        await LibrarySeatAlerts(plugin).tick(NOW + timedelta(minutes=20))
        assert len(booking_school.writes) == int(stage == "school_submit")

    asyncio.run(run())


@pytest.mark.parametrize("applied", [False, True])
def test_lost_submit_permission_receipt_never_starts_the_school_write(
    booking_school, applied
):
    from henu_plugin.library_seat_alerts import LibrarySeatAlerts, KEY

    class UncertainPermission(Plugin):
        async def set_plugin_storage(self, key, value):
            if key == KEY and any(
                p.get("state") == "booking" and p.get("booking_submitting")
                for p in json.loads(value).values()
            ):
                if applied:
                    await super().set_plugin_storage(key, value)
                raise TimeoutError("synthetic lost permission receipt")
            await super().set_plugin_storage(key, value)

    async def run():
        plugin = UncertainPermission()
        plugin.storage.update(owned_plugin().storage)
        alerts = LibrarySeatAlerts(plugin)
        await arm(alerts)
        booking_school.rows[0].update(status="1", status_name="空闲")
        await alerts.tick(NOW + timedelta(minutes=10))
        await LibrarySeatAlerts(plugin).tick(NOW + timedelta(minutes=20))
        assert not booking_school.writes

    asyncio.run(run())


@pytest.mark.parametrize(
    "change",
    ["campus_session", "kit_binding", "confirmation_source", "confirmation_expired"],
)
def test_confirmation_cannot_authorize_another_account_or_replayed_message(
    booking_school, change
):
    from henu_plugin.library_seat_alerts import LibrarySeatAlerts

    async def run():
        plugin = owned_plugin()
        alerts = LibrarySeatAlerts(plugin)
        token = await arm(alerts, confirm=False)
        if change == "campus_session":
            plugin.storage["user:alice:library_cookie"] = json.dumps(
                {"_v4_token": "different", "_subject_student_id": "alice"}
            ).encode()
        if change == "kit_binding":
            booking_school.uid = "00000000-0000-4000-8000-000000000002"
        command = "确认有位自动预约 " + token
        stamp = NOW + timedelta(minutes=6) if change == "confirmation_expired" else NOW
        request = context(
            command,
            source="set-choose" if change == "confirmation_source" else "new-confirm",
        )
        request.event.message_chain.root[0].time = stamp
        await alerts.handle(request, command, now=stamp)
        assert not (await alerts.status("bot", "alice")).get("auto_reserve")
        booking_school.rows[0].update(status="1", status_name="空闲")
        await alerts.tick(NOW + timedelta(minutes=10))
        assert not booking_school.writes

    asyncio.run(run())


@pytest.mark.parametrize(
    "changed", ["contradictory", "missing_id", "duplicate_no", "bad_code"]
)
def test_final_school_recheck_must_still_be_a_single_reliable_free_seat(
    booking_school, changed
):
    from henu_plugin.library_seat_alerts import LibrarySeatAlerts

    async def run():
        alerts = LibrarySeatAlerts(owned_plugin())
        await arm(alerts)
        booking_school.rows[0].update(status="1", status_name="空闲")

        def alter_response():
            if changed == "contradictory":
                booking_school.rows[0]["status_name"] = "已预约"
            elif changed == "missing_id":
                booking_school.rows[0].pop("id", None)
            elif changed == "duplicate_no":
                booking_school.rows = [
                    booking_school.rows[0],
                    dict(booking_school.rows[0]),
                ]
            else:
                booking_school.code = False

        booking_school.on_entitlement = alter_response
        await alerts.tick(NOW + timedelta(minutes=10))
        assert not booking_school.writes

    asyncio.run(run())


def test_single_seat_submission_uses_canonical_number_even_when_another_seat_has_its_name_alias(
    booking_school,
):
    from henu_plugin.library_seat_alerts import LibrarySeatAlerts

    async def run():
        alerts = LibrarySeatAlerts(owned_plugin())
        await arm(alerts, seat="001")
        booking_school.rows[0].update(status="1", status_name="空闲")

        def aliases():
            booking_school.rows = [
                {
                    "id": "wrong-seat",
                    "no": "002",
                    "name": "001",
                    "status": "1",
                    "status_name": "空闲",
                },
                {"id": "right-seat", "no": "001", "status": "1", "status_name": "空闲"},
            ]

        booking_school.on_entitlement = aliases
        await alerts.tick(NOW + timedelta(minutes=10))
        assert len(booking_school.writes) == 1
        assert booking_school.writes[0]["seat_id"] == "right-seat"

    asyncio.run(run())


@pytest.mark.parametrize("code,http", [(False, 200), (None, 200), ("0", 200), (0, 500)])
def test_malformed_school_submission_result_stays_unknown_without_replay(
    booking_school, code, http
):
    from henu_plugin.library_seat_alerts import LibrarySeatAlerts

    async def run():
        plugin = owned_plugin()
        alerts = LibrarySeatAlerts(plugin)
        await arm(alerts)
        booking_school.confirm_code, booking_school.confirm_status = code, http
        booking_school.rows[0].update(status="1", status_name="空闲")
        await alerts.tick(NOW + timedelta(minutes=10))
        assert (await alerts.status("bot", "alice"))["state"] == "reservation_unknown"
        await LibrarySeatAlerts(plugin).tick(NOW + timedelta(minutes=20))
        assert len(booking_school.writes) == 1

    asyncio.run(run())


@pytest.mark.parametrize(
    "scenario", ["nonmember", "unconfirmed", "revoked", "cancelled", "lost_receipt"]
)
def test_automatic_booking_requires_current_membership_and_independent_authorization(
    booking_school, scenario
):
    from henu_plugin.library_seat_alerts import LibrarySeatAlerts

    async def run():
        plugin = owned_plugin()
        alerts = LibrarySeatAlerts(plugin)
        if scenario == "nonmember":
            booking_school.lifetime = False
            await alerts.handle(context(), COMMAND, now=NOW)
            request = context("有位自动预约", source="choose")
            await alerts.handle(request, "有位自动预约", now=NOW)
            assert "终身会员" in str(request.reply.call_args)
        else:
            await arm(alerts, confirm=scenario != "unconfirmed")
        if scenario == "revoked":
            booking_school.lifetime = False
        if scenario == "cancelled":
            await alerts.handle(
                context("取消空位提醒", source="stop"), "取消空位提醒", now=NOW
            )
        booking_school.lose_receipt = scenario == "lost_receipt"
        booking_school.rows[0].update(status="1", status_name="空闲")
        await alerts.tick(NOW + timedelta(minutes=10))
        await LibrarySeatAlerts(plugin).tick(NOW + timedelta(minutes=20))
        assert len(booking_school.writes) == int(scenario == "lost_receipt")
        if scenario == "lost_receipt":
            assert (await alerts.status("bot", "alice"))[
                "state"
            ] == "reservation_unknown"

    asyncio.run(run())


def test_sdk_buttons_authorize_at_setup_and_replays_cannot_reauthorize(
    tmp_path, monkeypatch, booking_school
):
    from datetime import datetime
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from henu_plugin import library_seat_alerts as lib
    from tests.test_qq_cards_dispatch import MemoryRPC
    from components.event_listener.food_capture import FoodCaptureListener
    from components.cli_tools.henu_food import HenuFood
    from langbot_plugin.api.entities import events
    from langbot_plugin.api.entities.builtin.platform import events as pe, entities
    from langbot_plugin.entities.io.actions.enums import RuntimeToPluginAction

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr(lib, "datetime", Clock)
    monkeypatch.setenv("HENU_KIT_BOT_UUID", "synthetic-bot")
    monkeypatch.chdir(tmp_path)
    receive = AsyncMock()
    monkeypatch.setattr(HenuFood, "receive_message", receive)

    def first_button(sent):
        page = str(sent[0]["text"])
        marker = "\n[HENU_ACTIONS_V1:"
        assert marker in page, page
        wire = json.loads(base64.urlsafe_b64decode(page.split(marker)[1][:-1]))
        return "HENU_BUTTON " + wire["actions"][0]["id"]

    async def run():
        async def initialize_plugin(_):
            pass

        rpc = MemoryRPC(None, initialize_plugin)
        rpc.variables, rpc.sent = {}, []
        plugin = owned_plugin()
        plugin.plugin_runtime_handler = rpc
        plugin.library_seat_alerts = lib.LibrarySeatAlerts(plugin)
        listener = FoodCaptureListener()
        listener.plugin = plugin
        await listener.initialize()
        rpc.plugin_container = SimpleNamespace(
            components=[
                SimpleNamespace(
                    manifest=SimpleNamespace(kind="EventListener"),
                    component_instance=listener,
                )
            ]
        )

        async def emit(text, index):
            chain = context(text, source=f"booking-sdk-{index}").event.message_chain
            incoming = pe.FriendMessage(
                sender=entities.Friend(id="alice", nickname="合成", remark=""),
                message_chain=chain,
            )
            event = events.PersonMessageReceived(
                launcher_type="person",
                launcher_id="alice",
                sender_id="alice",
                message_event=incoming,
                message_chain=chain,
            )
            result = await rpc.actions[RuntimeToPluginAction.EMIT_EVENT.value](
                {
                    "event_context": {
                        "query_id": index,
                        "eid": index + 30,
                        "event_name": "PersonMessageReceived",
                        "event": event.model_dump(mode="json"),
                    }
                }
            )
            assert result.data["event_context"]["is_prevent_default"]

        await emit(COMMAND, 0)
        choose = first_button(rpc.sent[-1])
        await emit(choose, 1)
        confirm = first_button(rpc.sent[-1])
        assert not (
            await plugin.library_seat_alerts.status("synthetic-bot", "alice")
        ).get("auto_reserve")
        await emit(confirm, 2)
        assert (await plugin.library_seat_alerts.status("synthetic-bot", "alice"))[
            "auto_reserve"
        ]["user_id"] == UID
        await emit("取消空位提醒", 3)
        await emit(confirm, 4)
        assert (await plugin.library_seat_alerts.status("synthetic-bot", "alice"))[
            "state"
        ] == "cancelled"
        assert not booking_school.writes
        receive.assert_not_awaited()

    asyncio.run(run())

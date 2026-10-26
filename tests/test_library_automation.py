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


def test_paused_campus_state_blocks_prepare_confirm_and_due_execution():
    async def run():
        blocked = False
        calls = []
        async def subject(sender):
            if blocked:
                raise ValueError('校园登录已暂停')
            return 'campus-student'
        async def authorize(bot, sender):
            return 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
        async def reserve(sender, plan):
            calls.append(plan)
            return {'success': True}
        jobs = LibraryAutomation(Plugin(), campus_subject=subject, authorize=authorize, reserve=reserve)
        blocked = True
        import pytest
        with pytest.raises(ValueError):
            await jobs.prepare('bot', 'alice', ARGS, 'source1', NOW)
        blocked = False
        plan = await jobs.prepare('bot', 'alice', ARGS, 'source1', NOW)
        blocked = True
        with pytest.raises(ValueError):
            await jobs.confirm('bot', 'alice', plan['token'], 'source2', NOW)
        blocked = False
        await jobs.confirm('bot', 'alice', plan['token'], 'source2', NOW)
        blocked = True
        await jobs.tick(NOW + timedelta(minutes=1))
        assert not calls
        assert (await jobs.status('bot', 'alice'))['state'] == 'authorization_failed'
    asyncio.run(run())


def test_campus_login_guards_reject_failed_unknown_and_paused_states():
    import pytest
    from henu_plugin.library_automation import validate_campus_state
    for field in ('last_login_attempt', 'login_verification', 'saved_login_guard'):
        for status in ('failed', 'unknown', 'pending', 'frozen', 'unverified'):
            with pytest.raises(ValueError):
                validate_campus_state({field: {'status': status}}, {}, {})
    with pytest.raises(ValueError):
        validate_campus_state({}, {'status': 'failed'}, {})
    with pytest.raises(ValueError):
        validate_campus_state({}, {}, {'paused': True})
    with pytest.raises(ValueError):
        validate_campus_state({}, {}, {'paused': 'unknown'})
    validate_campus_state({'login_verification': {'status': 'verified', 'verified': True}}, {}, {'paused': False})


def test_saved_session_builder_cannot_attempt_campus_login(monkeypatch):
    from henu_mcp.tools import server_impl as api
    from types import SimpleNamespace
    created = []
    class Bot:
        def __init__(self, sid, password, *args):
            assert password == ''
            created.append(self)
        def _is_token_valid(self):
            return valid
        def login(self):
            raise AssertionError('Password/CAS login must never be attempted')
    monkeypatch.setattr(api, 'HenuCampusBot', Bot)
    monkeypatch.setattr(api, '_load_library_cookies', lambda: {'_v4_token': 'cached'})
    monkeypatch.setattr(api, '_load_cas_cookies', lambda: {'CASTGC': 'cached-tgt'})
    for valid in (True, False):
        result = api._build_library_bot('student', 'never-transmit-password', saved_session_only=True)
        assert bool(result) is valid
        assert created[-1]._saved_session_only is True
    from campus_core.auth import AuthMixin
    assert AuthMixin.login(SimpleNamespace(_saved_session_only=True, _set_last_error=lambda message: None)) is False


def test_saved_session_probe_rejects_unknown_authentication_responses():
    from campus_core.auth import AuthMixin
    from types import SimpleNamespace
    for code in (None, False, 500, 10001, '0', 0):
        bot = SimpleNamespace(token='cached', _saved_session_only=True,
                              _post_json=lambda *args, **kwargs: {'code': code})
        assert AuthMixin._is_token_valid(bot) is (type(code) is int and code == 0)


async def configured(plugin=None, reserve=None):
    async def authorize(bot, sender):
        return 'member-' + sender
    return LibraryAutomation(plugin or Plugin(), authorize=authorize,
                             campus_subject=campus_subject, reserve=reserve)


async def schedule(jobs, sender='alice', source='s1'):
    preview = await jobs.prepare('bot', sender, ARGS, source, NOW)
    await jobs.confirm('bot', sender, preview['token'], source + '-confirm', NOW)


def test_slow_request_does_not_block_other_users_status_or_next_scan():
    async def run():
        entered, finish = asyncio.Event(), asyncio.Event()
        calls = []
        async def reserve(sender, plan):
            calls.append(sender)
            if sender == 'alice':
                entered.set()
                await finish.wait()
            return {'success': True}
        jobs = await configured(reserve=reserve)
        await schedule(jobs)
        await jobs.tick(NOW + timedelta(minutes=1), wait=False)
        await entered.wait()
        # A later scan must run even while an earlier school request is pending.
        await schedule(jobs, 'bob')
        await asyncio.wait_for(jobs.tick(NOW + timedelta(minutes=1)), .5)
        assert (await jobs.status('bot', 'bob'))['state'] == 'success'
        assert (await jobs.status('bot', 'alice'))['state'] == 'attempting'
        finish.set()
        await asyncio.gather(*list(jobs.workers.values()))
        assert calls == ['alice', 'bob']
        assert (await jobs.status('bot', 'bob'))['state'] == 'success'
    asyncio.run(run())


def test_manual_release_after_restart_never_retries_old_plan():
    import pytest
    async def run():
        calls = []
        async def reserve(sender, plan):
            calls.append(sender)
            return {'success': True}
        jobs = await configured(reserve=reserve)
        await schedule(jobs)
        data = await jobs.read()
        data[jobs.scope('bot', 'alice')]['state'] = 'attempting'  # legacy crash
        await jobs.write(data)
        restarted = await configured(jobs.plugin, reserve)
        await restarted.tick(NOW + timedelta(minutes=1))
        assert calls == []
        plan = await restarted.status('bot', 'alice')
        token = plan['recovery_token']
        for bot, sender, value in [('other', 'alice', token), ('bot', 'bob', token),
                                   ('bot', 'alice', 'wrong')]:
            with pytest.raises(ValueError):
                await restarted.release(bot, sender, value)
        with pytest.raises(ValueError):
            await restarted.prepare('bot', 'alice', ARGS, 'new', NOW)
        await restarted.release('bot', 'alice', token)
        await restarted.tick(NOW + timedelta(minutes=1))
        assert not calls
        await schedule(restarted, source='new')
        await restarted.tick(NOW + timedelta(minutes=1))
        assert calls == ['alice']
        with pytest.raises(ValueError):
            await restarted.release('bot', 'alice', token)
    asyncio.run(run())


def test_active_request_cannot_be_released_and_cancelled_scan_does_not_cancel_write():
    import pytest
    async def run():
        entered, finish = asyncio.Event(), asyncio.Event()
        async def reserve(sender, plan):
            entered.set()
            await finish.wait()
            return {'success': True}
        jobs = await configured(reserve=reserve)
        await schedule(jobs)
        scan = asyncio.create_task(jobs.tick(NOW + timedelta(minutes=1)))
        await entered.wait()
        scan.cancel()
        with pytest.raises(asyncio.CancelledError):
            await scan
        plan = await jobs.status('bot', 'alice')
        with pytest.raises(ValueError):
            await jobs.release('bot', 'alice', plan['recovery_token'])
        await jobs.cancel('bot', 'alice')
        assert (await jobs.status('bot', 'alice'))['state'] == 'attempting'
        finish.set()
        await asyncio.gather(*list(jobs.workers.values()))
        assert (await jobs.status('bot', 'alice'))['state'] == 'success'
    asyncio.run(run())


def test_storage_failures_before_claim_after_claim_and_after_submit():
    async def run():
        for failing_state, applied in [('attempting', False), ('attempting', True),
                                       ('success', False), ('success', True)]:
            class FailingPlugin(Plugin):
                fail = False
                async def set_plugin_storage(self, key, value):
                    match = self.fail and any(p['state'] == failing_state for p in json.loads(value).values())
                    if not match or applied:
                        await super().set_plugin_storage(key, value)
                    if match:
                        raise OSError('lost storage response')
            calls = []
            async def reserve(sender, plan):
                calls.append(sender)
                return {'success': True}
            plugin = FailingPlugin()
            jobs = await configured(plugin, reserve)
            await schedule(jobs)
            plugin.fail = True
            import pytest
            with pytest.raises(OSError):
                await jobs.tick(NOW + timedelta(minutes=1))
            assert len(calls) == (1 if failing_state == 'success' else 0)
            # An unapplied claim can safely be scanned again: no request occurred.
            plugin.fail = False
            if failing_state == 'attempting' and not applied:
                await jobs.cancel('bot', 'alice')
            restarted = await configured(plugin, reserve)
            await restarted.tick(NOW + timedelta(minutes=1))
            assert len(calls) == (1 if failing_state == 'success' else 0)
            if (await restarted.status('bot', 'alice'))['state'] == 'attempting':
                token = (await restarted.status('bot', 'alice'))['recovery_token']
                await restarted.release('bot', 'alice', token)
                assert (await restarted.status('bot', 'alice'))['state'] == 'released_unknown'
    asyncio.run(run())


def test_cancel_during_slow_authorization_prevents_submit():
    async def run():
        entered, finish = asyncio.Event(), asyncio.Event()
        jobs = await configured()
        await schedule(jobs)
        original = jobs.authorize
        async def authorize(bot, sender):
            entered.set()
            await finish.wait()
            return await original(bot, sender)
        jobs.authorize = authorize
        calls = []
        async def reserve(sender, plan):
            calls.append(sender)
        jobs.reserve = reserve
        scan = asyncio.create_task(jobs.tick(NOW + timedelta(minutes=1)))
        await entered.wait()
        await jobs.cancel('bot', 'alice')
        finish.set()
        await scan
        assert not calls
        assert (await jobs.status('bot', 'alice'))['state'] == 'cancelled'
    asyncio.run(run())


def test_real_saved_plan_adapter_runs_concurrently_without_global_user_paths(tmp_path, monkeypatch):
    import threading
    from types import SimpleNamespace
    from henu_plugin.library_automation import _reserve_saved_plan
    from campus_core.bot import HenuCampusBot
    barrier = threading.Barrier(2, timeout=2)
    calls = []
    def valid(self):
        assert self.password == '' and self._saved_session_only is True
        assert self.token == 'token-' + self.username
        return True
    def reserve(self, location, seat, date, **kwargs):
        barrier.wait()  # Serial execution or a global runtime lock would fail.
        calls.append(self.username)
        assert kwargs['strict_window'] is True
        assert kwargs['max_attempts'] == 1 and kwargs['retry_until'] == ''
        return {'success': True}
    monkeypatch.setattr(HenuCampusBot, '_is_token_valid', valid)
    monkeypatch.setattr(HenuCampusBot, 'reserve', reserve)
    async def run():
        tasks = []
        for sender in ('alice', 'bob'):
            path = tmp_path / (sender + '.json')
            path.write_text(json.dumps({'_v4_token': 'token-' + sender}))
            plan = dict(ARGS, run_at='2026-10-27T07:00:00+08:00', campus_subject=sender)
            tasks.append(asyncio.to_thread(_reserve_saved_plan,
                         SimpleNamespace(library_cookie_file=path), plan))
        assert all(r['success'] for r in await asyncio.gather(*tasks))
        assert sorted(calls) == ['alice', 'bob']
        for sender in calls:
            assert json.loads((tmp_path / (sender + '.json')).read_text())['_v4_token'] == 'token-' + sender
    asyncio.run(run())


def test_private_recovery_requires_fresh_source_and_explicit_acknowledgement(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from langbot_plugin.api.entities.builtin.platform.message import MessageChain, Plain, Source
    async def run():
        jobs = await configured()
        await schedule(jobs)
        data = await jobs.read()
        data[jobs.scope('bot', 'alice')]['state'] = 'attempting'
        await jobs.write(data)
        plan = await jobs.status('bot', 'alice')
        token = plan['recovery_token']
        now = datetime.now(ZONE)
        def ctx(text, *, stamp=now):
            return SimpleNamespace(event=SimpleNamespace(sender_id='alice', launcher_id='alice', launcher_type='person',
                message_chain=MessageChain(root=[Source(id='new-message', time=stamp), Plain(text=text)])),
                get_bot_uuid=AsyncMock(return_value='bot'), reply=AsyncMock(),
                prevent_default=lambda: None, prevent_postorder=lambda: None)
        command = '解除自动预约 ' + token + ' 已核对'
        for request, group in [(ctx(command), True), (ctx(command, stamp=now-timedelta(minutes=3)), False),
                               (ctx('解除自动预约 ' + token), False)]:
            assert await jobs.handle(request, is_group=group)
            assert (await jobs.status('bot', 'alice'))['state'] == 'attempting'
        request = ctx(command)
        assert await jobs.handle(request)
        assert (await jobs.status('bot', 'alice'))['state'] == 'released_unknown'
        assert '未重试' in str(request.reply.call_args)
    asyncio.run(run())


def test_saved_client_school_timeout_is_never_replayed(tmp_path, monkeypatch):
    import requests
    from types import SimpleNamespace
    from urllib.parse import urlsplit
    from henu_plugin.library_automation import _reserve_saved_plan
    writes = []
    def request(self, method, url, **kwargs):
        path = urlsplit(url).path
        assert kwargs['timeout'] == 25
        if path == '/v4/space/confirm':
            writes.append(path)
            raise requests.Timeout('response lost after school submission')
        if path == '/v4/space/pick':
            data = {'area': []}
        elif path == '/v4/Space/map':
            data = {'type': '1', 'date': {'reserveType': '3', 'list': [
                {'day': '2099-01-02', 'def_start_time': '08:00', 'def_end_time': '11:00'}]}}
        elif path == '/v4/Space/seat':
            data = {'list': [{'id': 'seat1', 'no': '001', 'status': '1'}]}
        else:
            raise AssertionError('unexpected school operation ' + path)
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps({'code': 0, 'data': data}).encode()
        return response
    monkeypatch.setattr(requests.Session, 'request', request)
    path = tmp_path / 'cookies.json'
    path.write_text(json.dumps({'_v4_token': 'fake'}))
    plan = dict(ARGS, location='30', campus_subject='student',
                run_at='2099-01-01T07:00:00+08:00', target_date='2099-01-02')
    result = _reserve_saved_plan(SimpleNamespace(library_cookie_file=path), plan)
    assert result.get('success') is not True
    assert len(writes) == 1

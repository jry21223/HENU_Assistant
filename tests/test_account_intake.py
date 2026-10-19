from __future__ import annotations

import asyncio
from types import SimpleNamespace

from langbot_plugin.api.entities import context, events
from langbot_plugin.api.entities.builtin.platform.message import MessageChain, Plain
from test_binding_preferences_plugin import MemoryPlugin

from components.event_listener.identity_capture_safe import SafeIdentityCaptureListener


def request(query_id, command, *, user='intake-user', group=False):
    event_type = events.GroupMessageReceived if group else events.PersonMessageReceived
    event = event_type.model_construct(
        launcher_type='group' if group else 'person', launcher_id='group' if group else user,
        sender_id=user, message_chain=MessageChain([Plain(text=command)]), message_event=SimpleNamespace(),
    )
    return context.EventContext(query_id=query_id, event_name=event_type.__name__, event=event)


async def dispatch(listener, ctx):
    for handler in listener.registered_handlers[type(ctx.event)]:
        await handler(ctx)


def test_early_group_credential_intake_prevents_model_and_school(tmp_path, monkeypatch):
    replies = []

    async def reply(_ctx, chain, **kwargs):
        replies.append(str(chain))

    monkeypatch.setattr(context.EventContext, 'reply', reply)

    async def run():
        listener = SafeIdentityCaptureListener()
        plugin = MemoryPlugin(tmp_path)
        listener.plugin = plugin
        await listener.initialize()
        ctx = request(1, 'account set --student-id new-student --password synthetic-secret', group=True)
        await dispatch(listener, ctx)
        assert ctx.is_prevented_default()
        assert ctx.is_prevented_postorder()
        assert not plugin.storage
        assert len(replies) == 1
        assert 'synthetic-secret' not in replies[0]

    asyncio.run(run())


def prepare_delivery(plugin, monkeypatch):
    replies, sent = [], []

    async def reply(_ctx, chain, **kwargs):
        replies.append(str(chain))

    async def bot(_ctx):
        return 'offline-bot'

    async def send(bot_id, kind, target, chain):
        sent.append((bot_id, kind, target, str(chain)))

    plugin.send_message = send
    monkeypatch.setattr(context.EventContext, 'reply', reply)
    monkeypatch.setattr(context.EventContext, 'get_bot_uuid', bot)
    return replies, sent


def test_private_intake_is_deduplicated_and_replies_after_query_closes(tmp_path, monkeypatch):
    from test_account_binding import school_http
    from test_binding_preferences_plugin import person

    school_http(monkeypatch)
    monkeypatch.setenv('HENU_MASTER_KEY', 'synthetic-intake-master-key')

    async def run():
        plugin = MemoryPlugin(tmp_path)
        replies, sent = prepare_delivery(plugin, monkeypatch)
        listener = SafeIdentityCaptureListener()
        listener.plugin = plugin
        await listener.initialize()
        ctx = request(10, "account set --student-id new-student --password 'synthetic-secret' --no-calibrate-period-time")
        await dispatch(listener, ctx)
        await dispatch(listener, ctx)
        assert len(replies) == 1
        assert ctx.is_prevented_default() and ctx.is_prevented_postorder()

        async def closed_query(*args, **kwargs):
            raise AssertionError('original query ended')

        monkeypatch.setattr(context.EventContext, 'reply', closed_query)
        await asyncio.gather(*listener._account_login.tasks.values())
        assert len(sent) == 1
        assert sent[0][:3] == ('offline-bot', 'person', 'intake-user')
        assert '验证成功' in sent[0][3]
        assert 'synthetic-secret' not in str(replies + sent)
        from henu_plugin.service import set_current_user_paths
        from henu_plugin.storage_adapter import PluginStorageAdapter

        adapter = PluginStorageAdapter(plugin, 'intake-user')
        paths = await adapter.load_all()
        set_current_user_paths(paths)
        try:
            status = plugin.service.run_tool('system_status', {}, person('intake-user'), 11)
            assert status['account']['account']['student_id'] == 'new-student'
            assert status['account']['account']['last_login_attempt']['verified'] is True
        finally:
            set_current_user_paths(None)
            await adapter.abort()

    asyncio.run(run())


def test_rejected_attempt_budget_survives_new_listener_and_storage_request(tmp_path, monkeypatch):
    from test_account_binding import school_http

    calls = school_http(monkeypatch, reject=True)
    monkeypatch.setenv('HENU_MASTER_KEY', 'synthetic-intake-master-key')

    async def run():
        plugin = MemoryPlugin(tmp_path)
        _, sent = prepare_delivery(plugin, monkeypatch)
        for query_id in range(4):
            listener = SafeIdentityCaptureListener()
            listener.plugin = plugin
            await listener.initialize()
            ctx = request(query_id + 20, 'account set --student-id new-student --password synthetic-wrong --no-calibrate-period-time')
            await dispatch(listener, ctx)
            await asyncio.gather(*listener._account_login.tasks.values())
        assert len([call for call in calls if call[0] == 'POST']) == 3
        assert '限流' in sent[-1][3]
        assert b'new-student' not in plugin.storage['shared:account_login_guard']
        assert b'synthetic-wrong' not in plugin.storage['shared:account_login_guard']

    asyncio.run(run())


def test_student_budget_cannot_be_reset_by_switching_qq_user(tmp_path, monkeypatch):
    from test_account_binding import school_http

    calls = school_http(monkeypatch, reject=True)
    monkeypatch.setenv('HENU_MASTER_KEY', 'synthetic-intake-master-key')

    async def run():
        plugin = MemoryPlugin(tmp_path)
        _, sent = prepare_delivery(plugin, monkeypatch)
        for index in range(4):
            listener = SafeIdentityCaptureListener()
            listener.plugin = plugin
            await listener.initialize()
            await dispatch(listener, request(100 + index,
                'account set --student-id new-student --password synthetic-wrong --no-calibrate-period-time',
                user=f'guard-{index}'))
            await asyncio.gather(*listener._account_login.tasks.values())
        assert len([call for call in calls if call[0] == 'POST']) == 3
        assert '限流' in sent[-1][3]

    asyncio.run(run())


async def deliver_account(plugin, query_id, command, user='intake-user'):
    listener = SafeIdentityCaptureListener()
    listener.plugin = plugin
    await listener.initialize()
    await dispatch(listener, request(query_id, command, user=user))
    await asyncio.gather(*listener._account_login.tasks.values())


def test_pre_auth_network_failures_do_not_exhaust_budget(tmp_path, monkeypatch):
    import requests

    requests_seen = []

    def unavailable(session, method, url, **kwargs):
        requests_seen.append(method.upper())
        raise requests.ConnectionError('synthetic-network-secret')

    monkeypatch.setattr(requests.Session, 'request', unavailable)
    monkeypatch.setenv('HENU_MASTER_KEY', 'synthetic-intake-master-key')

    async def run():
        plugin = MemoryPlugin(tmp_path)
        _, sent = prepare_delivery(plugin, monkeypatch)
        for index in range(5):
            await deliver_account(plugin, index + 200, 'account set --student-id new-student --password synthetic-wrong --no-calibrate-period-time')
        assert '限流' not in sent[-1][3]
        assert 'synthetic-network-secret' not in str(sent)
        assert 'POST' not in requests_seen
        assert plugin.storage['shared:account_login_guard'] == b'{}'

    asyncio.run(run())


def test_guard_write_failure_stops_before_school(tmp_path, monkeypatch):
    from test_account_binding import school_http

    calls = school_http(monkeypatch)
    monkeypatch.setenv('HENU_MASTER_KEY', 'synthetic-intake-master-key')

    async def run():
        plugin = MemoryPlugin(tmp_path)
        _, sent = prepare_delivery(plugin, monkeypatch)
        original = plugin.set_plugin_storage

        async def fail_guard(key, value):
            if key == 'shared:account_login_guard':
                raise OSError('synthetic-storage-secret')
            await original(key, value)

        plugin.set_plugin_storage = fail_guard
        await deliver_account(plugin, 300, 'account set --student-id new-student --password synthetic-secret')
        assert not calls
        assert '保护' in sent[-1][3]
        assert 'synthetic-storage-secret' not in str(sent)

    asyncio.run(run())


def test_no_verify_does_not_clear_existing_budget(tmp_path, monkeypatch):
    from test_account_binding import school_http

    calls = school_http(monkeypatch, reject=True)
    monkeypatch.setenv('HENU_MASTER_KEY', 'synthetic-intake-master-key')

    async def run():
        plugin = MemoryPlugin(tmp_path)
        _, sent = prepare_delivery(plugin, monkeypatch)
        verified = 'account set --student-id new-student --password synthetic-wrong --no-calibrate-period-time'
        for index in range(3):
            await deliver_account(plugin, index + 400, verified)
        before = plugin.storage['shared:account_login_guard']
        await deliver_account(plugin, 403, verified + ' --no-verify-login')
        assert '尚未验证' in sent[-1][3]
        assert plugin.storage['shared:account_login_guard'] == before
        await deliver_account(plugin, 404, verified)
        assert '限流' in sent[-1][3]
        assert len([call for call in calls if call[0] == 'POST']) == 3

    asyncio.run(run())


def test_verified_success_resets_the_matching_failure_budget(tmp_path, monkeypatch):
    from test_account_binding import school_http

    school_http(monkeypatch, reject=True)
    monkeypatch.setenv('HENU_MASTER_KEY', 'synthetic-intake-master-key')

    async def run():
        plugin = MemoryPlugin(tmp_path)
        _, sent = prepare_delivery(plugin, monkeypatch)
        command = 'account set --student-id new-student --password synthetic-password --no-calibrate-period-time'
        await deliver_account(plugin, 500, command)
        await deliver_account(plugin, 501, command)
        school_http(monkeypatch)
        await deliver_account(plugin, 502, command)
        assert '验证成功' in sent[-1][3]
        assert plugin.storage['shared:account_login_guard'] == b'{}'

    asyncio.run(run())


def test_corrupt_guard_stops_before_school(tmp_path, monkeypatch):
    from test_account_binding import school_http

    calls = school_http(monkeypatch)
    monkeypatch.setenv('HENU_MASTER_KEY', 'synthetic-intake-master-key')

    async def run():
        plugin = MemoryPlugin(tmp_path)
        plugin.storage['shared:account_login_guard'] = b'not-json'
        _, sent = prepare_delivery(plugin, monkeypatch)
        await deliver_account(plugin, 600, 'account set --student-id new-student --password synthetic-password')
        assert not calls
        assert '保护' in sent[-1][3]
        assert plugin.storage['shared:account_login_guard'] == b'not-json'

    asyncio.run(run())


def test_missing_sender_never_uses_private_launcher_as_identity(tmp_path, monkeypatch):
    from test_account_binding import school_http

    calls = school_http(monkeypatch)

    async def run():
        plugin = MemoryPlugin(tmp_path)
        replies, sent = prepare_delivery(plugin, monkeypatch)
        await deliver_account(plugin, 700, 'account set --student-id new-student --password synthetic-password', user='')
        assert not calls and not plugin.storage and not sent
        assert '身份' in replies[-1]

    asyncio.run(run())


def test_uncertain_final_delivery_is_not_automatically_sent_twice(tmp_path, monkeypatch):
    from test_account_binding import school_http

    school_http(monkeypatch)
    monkeypatch.setenv('HENU_MASTER_KEY', 'synthetic-intake-master-key')

    async def run():
        plugin = MemoryPlugin(tmp_path)
        prepare_delivery(plugin, monkeypatch)
        deliveries = []

        async def lost_receipt(bot, kind, target, chain):
            deliveries.append(str(chain))
            raise OSError('send response lost')

        plugin.send_message = lost_receipt
        await deliver_account(plugin, 800, 'account set --student-id new-student --password synthetic-password --no-calibrate-period-time')
        assert len(deliveries) == 1

    asyncio.run(run())


def test_extra_credential_flags_do_not_silently_reach_school(tmp_path, monkeypatch):
    from test_account_binding import school_http

    calls = school_http(monkeypatch)
    monkeypatch.setenv('HENU_MASTER_KEY', 'synthetic-intake-master-key')

    async def run():
        plugin = MemoryPlugin(tmp_path)
        replies, sent = prepare_delivery(plugin, monkeypatch)
        await deliver_account(plugin, 900,
            'account set --student-id new-student --password synthetic-password --no-calibrate-period-time --typo accidental-extra-secret')
        assert not calls and not plugin.storage and not sent
        assert 'accidental-extra-secret' not in str(replies)

    asyncio.run(run())


def test_concurrent_route_lookup_admits_only_one_account_job_per_user(tmp_path, monkeypatch):
    from test_account_binding import school_http

    school_http(monkeypatch)
    monkeypatch.setenv('HENU_MASTER_KEY', 'synthetic-intake-master-key')

    async def run():
        plugin = MemoryPlugin(tmp_path)
        replies, sent = prepare_delivery(plugin, monkeypatch)
        listener = SafeIdentityCaptureListener()
        listener.plugin = plugin
        await listener.initialize()
        gate, both_entered = asyncio.Event(), asyncio.Event()
        entered = 0

        async def bot(_ctx):
            nonlocal entered
            entered += 1
            if entered == 2:
                both_entered.set()
            await gate.wait()
            return 'offline-bot'

        monkeypatch.setattr(context.EventContext, 'get_bot_uuid', bot)
        command = 'account set --student-id new-student --password synthetic-password --no-calibrate-period-time'
        first = asyncio.create_task(dispatch(listener, request(1000, command)))
        second = asyncio.create_task(dispatch(listener, request(1001, command)))
        await asyncio.wait_for(both_entered.wait(), 1)
        gate.set()
        await asyncio.gather(first, second)
        await asyncio.gather(*listener._account_login.tasks.values())
        assert sum('正在验证' in reply for reply in replies) == 1
        assert len(sent) == 1

    asyncio.run(run())


def test_cancelled_ack_does_not_strand_the_next_account_request(tmp_path, monkeypatch):
    from test_account_binding import school_http

    from henu_plugin import account_login

    school_http(monkeypatch)
    monkeypatch.setenv('HENU_MASTER_KEY', 'synthetic-intake-master-key')
    clock = [1000.0]
    monkeypatch.setattr(account_login, 'time', SimpleNamespace(monotonic=lambda: clock[0]))

    async def run():
        plugin = MemoryPlugin(tmp_path)
        _, sent = prepare_delivery(plugin, monkeypatch)
        listener = SafeIdentityCaptureListener()
        listener.plugin = plugin
        await listener.initialize()
        started = asyncio.Event()

        async def pending_ack(*args, **kwargs):
            started.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(context.EventContext, 'reply', pending_ack)
        command = 'account set --student-id new-student --password synthetic-password --no-calibrate-period-time'
        handler = asyncio.create_task(dispatch(listener, request(1100, command)))
        await started.wait()
        handler.cancel()
        try:
            await handler
        except asyncio.CancelledError:
            pass
        clock[0] += 31

        async def reply(*args, **kwargs):
            pass

        monkeypatch.setattr(context.EventContext, 'reply', reply)
        await dispatch(listener, request(1101, command))
        await asyncio.wait_for(asyncio.gather(*listener._account_login.tasks.values(), return_exceptions=True), 0.5)
        assert len(sent) == 1
        assert '验证成功' in sent[-1][3]

    asyncio.run(run())


def test_unknown_credential_submission_never_invites_a_retry(tmp_path, monkeypatch):
    import requests
    from test_auth_fail_closed import install_school_http

    install_school_http(monkeypatch, page='form', post_result=requests.ConnectionError('response lost'))
    monkeypatch.setenv('HENU_MASTER_KEY', 'synthetic-intake-master-key')

    async def run():
        plugin = MemoryPlugin(tmp_path)
        _, sent = prepare_delivery(plugin, monkeypatch)
        await deliver_account(plugin, 1200, 'account set --student-id student --password synthetic-password --no-calibrate-period-time')
        assert '结果未知' in sent[-1][3]
        assert '勿重复' in sent[-1][3]
        assert '重试' not in sent[-1][3]

    asyncio.run(run())

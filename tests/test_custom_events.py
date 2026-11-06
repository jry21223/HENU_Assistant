import asyncio
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from henu_plugin.custom_events import CustomEvents, KEY, ZONE, parse

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=ZONE)
ADD = 'event add --title 班会 --start "2026-11-06 19:00" --end "2026-11-06 20:00" --location 综合楼2305 --remind-minutes 30'


class Plugin:
    def __init__(self):
        self.data = {}
        self.sent = []
        self.fail_read = self.fail_write = self.fail_send = False

    async def get_plugin_storage(self, key):
        if self.fail_read:
            raise OSError('offline')
        if key not in self.data:
            raise KeyError(key)
        return self.data[key]

    async def set_plugin_storage(self, key, value):
        if self.fail_write:
            raise OSError('offline')
        self.data[key] = value

    async def send_message(self, *args):
        self.sent.append(args)
        if self.fail_send:
            raise TimeoutError()


async def command(manager, text=ADD, sender='alice', bot='bot', **kwargs):
    return await manager.execute(text, sender=sender, bot=bot, now=NOW, **kwargs)


def test_crud_persistence_owner_and_bot_isolation_and_no_school_account():
    async def run():
        p = Plugin()
        m = CustomEvents(p)
        assert (await command(m))['success']
        rows = (await command(CustomEvents(p), 'event list'))['events']
        assert len(rows) == 1 and rows[0]['location'] == '综合楼2305'
        event_id = rows[0]['id']
        assert not (await command(m, 'event list', sender='bob'))['events']
        assert not (await command(m, 'event list', bot='other'))['events']
        assert not (await command(m, f'event cancel --id {event_id}', sender='bob'))['success']
        result = await command(m, f'event update --id {event_id} --title 年级大会 --start "2026-11-06 19:30" --remind-minutes off')
        assert result['success']
        assert (await command(m, 'event list'))['events'][0]['title'] == '年级大会'
        assert (await command(m, f'event cancel --id {event_id}'))['success']
        assert not (await command(m, 'event list'))['events']
        await m.tick(datetime(2026, 11, 6, 19, tzinfo=ZONE))
        assert not p.sent
    asyncio.run(run())


@pytest.mark.parametrize('suffix', [
    '--start "2026-11-04 19:00"', '--start 明天晚上', '--start "2026-11-06 25:00"',
    '--start "2026-11-06 19:00" --end "2026-11-06 18:00"',
    '--start "2026-11-06 19:00" --remind-minutes -1',
    '--start "2026-11-06 19:00" --remind-minutes 1.5',
    '--start "2026-11-06 19:00" --unknown yes',
    '--start "2026-11-06 19:00" --title 重复',
    '--start "2026-11-06 19:00" --location',
    '--start "2026-11-06 19:00" --remind-minutes 10081',
])
def test_invalid_request_does_not_write(suffix):
    async def run():
        p = Plugin()
        assert not (await command(CustomEvents(p), 'event add --title 班会 ' + suffix))['success']
        assert not p.data
    asyncio.run(run())


def test_private_only_deduplication_and_concurrent_adds():
    async def run():
        p = Plugin(); m = CustomEvents(p)
        assert not (await command(m, kind='group'))['success']
        assert not p.data
        await asyncio.gather(*(command(m, request_id='retry-1') for _ in range(4)))
        assert len((await command(m, 'event list'))['events']) == 1
        await asyncio.gather(*(command(m, request_id=f'new-{i}') for i in range(3)))
        assert len((await command(m, 'event list'))['events']) == 4
    asyncio.run(run())


@pytest.mark.parametrize('failure', ['fail_read', 'fail_write', 'corrupt'])
def test_storage_failure_never_reports_saved_or_overwrites(failure):
    async def run():
        p = Plugin(); m = CustomEvents(p)
        await command(m)
        if failure == 'corrupt':
            p.data[KEY] = b'{"broken": 123}'
        else:
            setattr(p, failure, True)
        before = dict(p.data)
        result = await command(m)
        assert not result['success'] and p.data == before
    asyncio.run(run())


@pytest.mark.parametrize('send_fails', [False, True])
def test_durable_reminder_no_duplicate_after_restart_or_timeout(send_fails):
    async def run():
        p = Plugin(); m = CustomEvents(p)
        await command(m)
        p.fail_send = send_fails
        await m.tick(datetime(2026, 11, 6, 18, 29, tzinfo=ZONE))
        assert not p.sent
        await m.tick(datetime(2026, 11, 6, 18, 30, tzinfo=ZONE))
        assert len(p.sent) == 1 and p.sent[0][:3] == ('bot', 'person', 'alice')
        await CustomEvents(p).tick(datetime(2026, 11, 6, 18, 31, tzinfo=ZONE))
        assert len(p.sent) == 1
        row = (await command(m, 'event list'))['events'][0]
        assert row['delivery'] == ('unknown' if send_fails else 'sent')
    asyncio.run(run())


def test_claim_failure_prevents_send_and_late_restart_does_not_send_stale():
    async def run():
        p = Plugin(); m = CustomEvents(p)
        await command(m)
        p.fail_write = True
        with pytest.raises(OSError):
            await m.tick(datetime(2026, 11, 6, 18, 30, tzinfo=ZONE))
        assert not p.sent
        p.fail_write = False
        await CustomEvents(p).tick(datetime(2026, 11, 7, 12, tzinfo=ZONE))
        assert not p.sent
        row = (await command(m, 'event list --date 2026-11-06'))['events'][0]
        assert row['delivery'] == 'missed'
    asyncio.run(run())


def test_reschedule_rearms_but_edit_title_does_not_duplicate_reminder():
    async def run():
        p = Plugin(); m = CustomEvents(p)
        await command(m)
        await m.tick(datetime(2026, 11, 6, 18, 30, tzinfo=ZONE))
        event_id = (await command(m, 'event list'))['events'][0]['id']
        await command(m, f'event update --id {event_id} --title 改名')
        await m.tick(datetime(2026, 11, 6, 18, 31, tzinfo=ZONE))
        assert len(p.sent) == 1
        await command(m, f'event update --id {event_id} --start "2026-11-07 19:00" --end "2026-11-07 20:00"')
        await m.tick(datetime(2026, 11, 7, 18, 30, tzinfo=ZONE))
        assert len(p.sent) == 2
    asyncio.run(run())


def test_default_reminder_off_zero_and_date_pagination():
    async def run():
        p = Plugin(); m = CustomEvents(p)
        for i in range(10):
            await command(m, f'event add --title 会{i} --start "2026-11-06 19:00"')
        first = await command(m, 'event list --date 2026-11-06')
        assert first['total'] == 10 and len(first['events']) == 8
        assert first['events'][0]['remind_minutes'] == 15
        assert len((await command(m, 'event list --offset 8'))['events']) == 2
        for row in first['events']:
            await command(m, f"event update --id {row['id']} --remind-minutes off")
        await m.tick(datetime(2026, 11, 6, 18, 45, tzinfo=ZONE))
        assert len(p.sent) == 2
    asyncio.run(run())


def test_direct_private_entry_and_group_rejection():
    async def run():
        p = Plugin(); m = CustomEvents(p)
        ctx = SimpleNamespace(event=SimpleNamespace(text_message='我的日程', sender_id='alice'),
            get_bot_uuid=AsyncMock(return_value='bot'), prevent_default=lambda: None,
            prevent_postorder=lambda: None, reply=AsyncMock())
        assert await m.handle(ctx)
        assert ctx.reply.await_count
        ctx.event.text_message = ADD
        assert await m.handle(ctx, is_group=True)
        assert not p.data
        ctx.event.text_message = '你好'
        assert not await m.handle(ctx)
    asyncio.run(run())


def test_real_cli_tool_routes_without_school_preflight(monkeypatch):
    from langbot_plugin.api.entities.builtin.provider.session import Session, LauncherTypes
    from components.cli_tools.henu_cli_safe import HenuCliSafe
    from langbot_plugin.api.proxies.query_based_api import QueryBasedAPIProxy
    from henu_plugin.cli import inspect_cli_command, build_help_payload

    async def run():
        p = Plugin()
        p.custom_events = CustomEvents(p)
        p.plugin_runtime_handler = None
        tool = HenuCliSafe(); tool.plugin = p
        monkeypatch.setattr(tool, '_load_identity_hint', AsyncMock(return_value={'sender_id': 'alice'}))
        monkeypatch.setattr(tool, '_publish_delivery_result', AsyncMock())
        monkeypatch.setattr('langbot_plugin.api.proxies.query_based_api.QueryBasedAPIProxy',
            lambda **kw: SimpleNamespace(get_bot_uuid=AsyncMock(return_value='bot')))
        session = Session(launcher_type=LauncherTypes.PERSON, launcher_id='alice', sender_id='alice')
        assert not tool.should_preload_runtime_context({'command': ADD})
        assert inspect_cli_command(ADD).resolved_tool == 'custom_event'
        assert 'event add' in build_help_payload('event')['summary']
        future = (datetime.now(ZONE) + timedelta(days=1)).strftime('%Y-%m-%d %H:%M')
        result = await tool.call({'command': f'event add --title 班会 --start "{future}"'}, session, 7)
        assert result['success'] and '已添加' in result['reply_text']
        await tool.call({'command': f'event add --title 班会 --start "{future}"'}, session, 7)
        assert (await tool.call({'command': 'event list'}, session, 8))['total'] == 1
        group = Session(launcher_type=LauncherTypes.GROUP, launcher_id='group', sender_id='alice')
        assert not (await tool.call({'command': 'event list'}, group, 9))['success']
    asyncio.run(run())


def test_slow_send_does_not_block_other_users_or_pending_cancellation():
    async def run():
        p = Plugin(); m = CustomEvents(p)
        await command(m, sender='alice')
        await command(m, sender='bob')
        bob_id = (await command(m, 'event list', sender='bob'))['events'][0]['id']
        sending, release = asyncio.Event(), asyncio.Event()
        async def send(*args):
            p.sent.append(args)
            sending.set()
            await release.wait()
        p.send_message = send
        task = asyncio.create_task(m.tick(datetime(2026, 11, 6, 18, 30, tzinfo=ZONE)))
        try:
            await asyncio.wait_for(sending.wait(), 2)
            assert (await asyncio.wait_for(command(m, 'event list', sender='bob'), 2))['total'] == 1
            result = await asyncio.wait_for(command(m, f'event cancel --id {bob_id}', sender='bob'), 2)
            assert result['success']
            release.set()
            await asyncio.wait_for(task, 2)
            assert len(p.sent) == 1 and p.sent[0][2] == 'alice'
            assert not (await command(m, 'event list', sender='bob'))['events']
        finally:
            release.set()
            await task
    asyncio.run(run())


@pytest.mark.parametrize('send_fails', [False, True])
@pytest.mark.parametrize('options', ['--start "2026-11-06T19:00"', '--remind-minutes 030',
    '--start "2026-11-06 19:00" --remind-minutes 30'])
def test_repeated_normalized_timing_preserves_sent_or_uncertain_claim(send_fails, options):
    async def run():
        p = Plugin(); m = CustomEvents(p)
        await command(m)
        p.fail_send = send_fails
        await m.tick(datetime(2026, 11, 6, 18, 30, tzinfo=ZONE))
        before = (await command(m, 'event list'))['events'][0]
        result = await command(m, f"event update --id {before['id']} --title 改名 {options}")
        assert result['success']
        after = (await command(m, 'event list'))['events'][0]
        assert after['delivery'] == before['delivery']
        assert after['claim_id'] == before['claim_id'] and after['claimed_at'] == before['claimed_at']
        await CustomEvents(p).tick(datetime(2026, 11, 6, 18, 31, tzinfo=ZONE))
        assert len(p.sent) == 1
    asyncio.run(run())


@pytest.mark.parametrize('options', ['--location 新地点', '--end "2026-11-06 20:30"',
    '--title 改名 --start "2026-11-06T19:00"'])
def test_ongoing_event_can_be_edited_without_moving_start(options):
    async def run():
        p = Plugin(); m = CustomEvents(p)
        await command(m)
        event_id = (await command(m, 'event list'))['events'][0]['id']
        now = datetime(2026, 11, 6, 19, 10, tzinfo=ZONE)
        result = await m.execute(f'event update --id {event_id} {options}', sender='alice', bot='bot', now=now)
        assert result['success']
        assert not (await m.execute(f'event update --id {event_id} --start "2026-11-06 19:05"',
                                   sender='alice', bot='bot', now=now))['success']
    asyncio.run(run())


def test_inflight_receipt_does_not_overwrite_reschedule_or_unrelated_updates():
    async def run():
        p = Plugin(); m = CustomEvents(p)
        await command(m)
        event_id = (await command(m, 'event list'))['events'][0]['id']
        sending, release = asyncio.Event(), asyncio.Event()
        async def send(*args):
            p.sent.append(args)
            sending.set()
            await release.wait()
        p.send_message = send
        task = asyncio.create_task(m.tick(datetime(2026, 11, 6, 18, 30, tzinfo=ZONE)))
        try:
            await asyncio.wait_for(sending.wait(), 2)
            result = await asyncio.wait_for(command(m, f'event update --id {event_id} --title 改期 '
                '--start "2026-11-07 19:00" --end "2026-11-07 20:00"'), 2)
            assert result['success']
            # An overlapping scan cannot send the already-claimed original timing.
            await m.tick(datetime(2026, 11, 6, 18, 30, tzinfo=ZONE))
            release.set()
            await asyncio.wait_for(task, 2)
            row = (await command(m, 'event list'))['events'][0]
            assert row['title'] == '改期' and not row.get('delivery') and not row.get('claim_id')
            await m.tick(datetime(2026, 11, 7, 18, 30, tzinfo=ZONE))
            assert len(p.sent) == 2
        finally:
            release.set()
            await task
    asyncio.run(run())


def test_successful_receipt_preserves_edits_made_during_send():
    async def run():
        p = Plugin(); m = CustomEvents(p)
        await command(m)
        event_id = (await command(m, 'event list'))['events'][0]['id']
        async def send(*args):
            p.sent.append(args)
            assert (await command(m, f'event update --id {event_id} --title 新标题 --note 新备注'))['success']
        p.send_message = send
        await asyncio.wait_for(m.tick(datetime(2026, 11, 6, 18, 30, tzinfo=ZONE)), 2)
        row = (await command(m, 'event list'))['events'][0]
        assert (row['title'], row['note'], row['delivery']) == ('新标题', '新备注', 'sent')
    asyncio.run(run())


def test_unconfirmed_receipt_write_does_not_allow_resend_after_restart():
    async def run():
        p = Plugin(); m = CustomEvents(p)
        await command(m)
        async def send(*args):
            p.sent.append(args)
            p.fail_write = True
        p.send_message = send
        with pytest.raises(OSError):
            await m.tick(datetime(2026, 11, 6, 18, 30, tzinfo=ZONE))
        p.fail_write = False
        await CustomEvents(p).tick(datetime(2026, 11, 6, 18, 31, tzinfo=ZONE))
        assert len(p.sent) == 1
        assert (await command(m, 'event list'))['events'][0]['delivery'] == 'unknown'
    asyncio.run(run())


def test_old_event_and_cancellation_history_is_retained():
    async def run():
        p = Plugin(); m = CustomEvents(p)
        await command(m)
        event_id = (await command(m, 'event list'))['events'][0]['id']
        await command(m, f'event cancel --id {event_id}')
        result = await m.execute('event add --title 后续会 --start "2027-01-06 19:00"',
            sender='alice', bot='bot', now=datetime(2027, 1, 5, 12, tzinfo=ZONE))
        assert result['success']
        events = next(iter(json.loads(p.data[KEY]).values()))['events']
        assert len(events) == 2 and events[event_id]['cancelled'] is True
    asyncio.run(run())

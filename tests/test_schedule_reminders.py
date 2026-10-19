import asyncio
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from henu_plugin.schedule_reminders import ScheduleReminders, due_courses, KEY

ZONE = ZoneInfo('Asia/Shanghai')


def result():
    row = dict(course='计算机网络', period='第1-2节', time='1-18周 二[1-2]', location='十号楼101')
    return dict(success=True, week_filter_applied=True, day_schedule=[row, row],
                period_times={'1': {'start': '08:00', 'end': '08:45'}, '2': {'start': '08:55', 'end': '09:40'}})


class Plugin:
    def __init__(self):
        self.storage = {KEY: json.dumps({'alice': {'enabled': True, 'bot': 'bot', 'journal': {}}}).encode()}
        self.messages = []
        self.fail = False

    async def get_plugin_storage(self, key):
        return self.storage.get(key)

    async def set_plugin_storage(self, key, value):
        self.storage[key] = value

    async def send_message(self, *args):
        self.messages.append(args)
        if self.fail:
            raise TimeoutError()


def test_class_reminder_survives_restart_without_duplicate_or_second_period(monkeypatch):
    async def run():
        plugin = Plugin()
        async def query(self, sender):
            return result()
        monkeypatch.setattr(ScheduleReminders, 'query', query)
        reminder = ScheduleReminders(plugin)
        await reminder.tick(datetime(2026, 10, 20, 7, 45, tzinfo=ZONE))
        assert len(plugin.messages) == 1
        assert '计算机网络' in str(plugin.messages[0])
        await ScheduleReminders(plugin).tick(datetime(2026, 10, 20, 7, 46, tzinfo=ZONE))
        await reminder.tick(datetime(2026, 10, 20, 8, 40, tzinfo=ZONE))
        assert len(plugin.messages) == 1
    
    
    asyncio.run(run())

def test_ambiguous_send_never_retries_and_disabled_user_gets_nothing(monkeypatch):
    async def run():
        plugin = Plugin()
        plugin.fail = True
        async def query(self, sender):
            return result()
        monkeypatch.setattr(ScheduleReminders, 'query', query)
        await ScheduleReminders(plugin).tick(datetime(2026, 10, 20, 7, 45, tzinfo=ZONE))
        await ScheduleReminders(plugin).tick(datetime(2026, 10, 20, 7, 46, tzinfo=ZONE))
        assert len(plugin.messages) == 1
        data = json.loads(plugin.storage[KEY]);data['alice']['enabled'] = False
        plugin.storage[KEY] = json.dumps(data).encode()
        await ScheduleReminders(plugin).tick(datetime(2026, 10, 21, 7, 45, tzinfo=ZONE))
        assert len(plugin.messages) == 1
        assert data['alice']['last_delivery'] == 'unknown'
    
    
    asyncio.run(run())

def test_no_unfiltered_no_early_or_late_reminder():
    data = result()
    assert not due_courses(data, datetime(2026, 10, 20, 7, 44, tzinfo=ZONE))
    assert not due_courses(data, datetime(2026, 10, 20, 8, 0, tzinfo=ZONE))
    data['week_filter_applied'] = False
    assert not due_courses(data, datetime(2026, 10, 20, 7, 45, tzinfo=ZONE))


def test_invalid_nonempty_clock_is_rejected():
    from henu_plugin.schedule_reminders import valid_period_clock
    assert not valid_period_clock({'1': {}})
    assert not valid_period_clock({'1': {'start': '错误', 'end': '错误'}})
    assert not valid_period_clock({'1': {'start': '25:00', 'end': '26:00'}})
    assert valid_period_clock({'1': {'start': '08:00', 'end': '08:45'}})


def test_background_channel_test_still_sends_if_schedule_query_fails(monkeypatch):
    async def run():
        plugin = Plugin()
        data = json.loads(plugin.storage[KEY])
        data['alice']['test_due'] = '2026-10-20T07:45:00+08:00'
        plugin.storage[KEY] = json.dumps(data).encode()
        async def fail(self, sender):
            raise OSError('schedule unavailable')
        monkeypatch.setattr(ScheduleReminders, 'query', fail)
        await ScheduleReminders(plugin).tick(datetime(2026, 10, 20, 7, 45, tzinfo=ZONE))
        assert len(plugin.messages) == 1
        assert '非真实上课提醒' in str(plugin.messages[0])
    asyncio.run(run())


def test_registry_missing_is_empty_but_read_failure_never_overwrites():
    async def run():
        from langbot_plugin.entities.io.errors import ActionCallError
        plugin = Plugin()
        async def missing(key):
            raise ActionCallError('Storage with key ' + key + ' not found')
        plugin.get_plugin_storage = missing
        assert await ScheduleReminders(plugin).read() == {}
        async def broken(key):
            raise OSError('storage offline')
        plugin.get_plugin_storage = broken
        with pytest.raises(OSError):
            await ScheduleReminders(plugin).read()
    asyncio.run(run())

"""Classroom requests through the public QQ navigation and real CLI/card path."""
import asyncio
import copy
import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from zoneinfo import ZoneInfo

import pytest
from langbot_plugin.api.entities.builtin.platform.message import MessageChain, Plain
from henu_plugin.qq_navigation import handle_navigation


@pytest.fixture
def campus_chat(monkeypatch):
    import henu_plugin.self_study as study
    from components.cli_tools import base
    from henu_plugin.cli import inspect_cli_command
    import langbot_plugin.api.proxies.query_based_api as proxies
    storage = {'shared:period_time': json.dumps(study.KAIFENG_CLOCK).encode()}
    requests, variables = [], {}
    now = [datetime(2026, 10, 24, 15, 20, tzinfo=ZoneInfo('Asia/Shanghai'))]
    monkeypatch.setattr(study, 'campus_now', lambda: now[0])
    active, response = [None], [None]

    async def get_storage(key):
        if key not in storage:
            raise RuntimeError('Native Storage key not found')
        return storage[key]

    async def put_storage(key, value):
        storage[key] = value

    def rpc(tool, params, *args, **kwargs):
        if tool != 'henu_cli':
            return {'success': True}
        spec = inspect_cli_command(params['command'])
        requests.append(copy.deepcopy(spec.params))
        query = {'campusCode': '02', 'campusName': '金明校区', 'buildingCode': '18',
                 'buildingName': '金明综合楼3', 'week': 8, 'dayOfWeek': 6,
                 'period': int(spec.params.get('period') or 3)}
        result = {'success': True, 'cli': {'resolved_tool': 'empty_classroom_query'},
                '_effective_params': spec.params, 'query': query,
                'rooms': [{'roomName': '金明综合楼3301', 'campusCode': '02', 'buildingCode': '18'}],
                'freshness': {'liveChecked': True, 'stale': False}}
        return response[0](result) if response[0] else result

    async def set_var(key, value):
        variables.setdefault(active[0].query_id, {})[key] = value

    proxy = SimpleNamespace(get_bot_uuid=AsyncMock(side_effect=lambda: active[0].bot),
        get_query_vars=AsyncMock(side_effect=lambda: variables.setdefault(active[0].query_id, {})),
        set_query_var=set_var)
    monkeypatch.setattr(proxies, 'QueryBasedAPIProxy', lambda **kwargs: proxy)
    monkeypatch.setattr(base, 'QueryBasedAPIProxy', lambda **kwargs: proxy)

    def plugin():
        return SimpleNamespace(plugin_runtime_handler=Mock(), get_plugin_storage=get_storage,
            get_plugin_storage_keys=AsyncMock(side_effect=lambda: list(storage)),
            set_plugin_storage=put_storage, service=SimpleNamespace(run_tool=rpc,
                run_tool_async=AsyncMock(side_effect=rpc), get_runtime_context=lambda *args: None))

    async def send(instance, text, *, sender='alice', chat='kit', bot='bot', kind='group'):
        ctx = SimpleNamespace(event=SimpleNamespace(launcher_type=kind, launcher_id=chat,
            sender_id=sender, message_chain=MessageChain(root=[Plain(text=text)])),
            query_id=len(variables) + 1, bot=bot, reply=AsyncMock(), is_prevent_postorder=False,
            get_bot_uuid=AsyncMock(return_value=bot), get_query_vars=proxy.get_query_vars,
            set_query_var=set_var)
        ctx.prevent_default = lambda: None
        ctx.prevent_postorder = lambda: setattr(ctx, 'is_prevent_postorder', True)
        active[0] = ctx
        variables[ctx.query_id] = {}
        handled = await handle_navigation(instance, ctx)
        return handled, '\n'.join(str(call.args[0]) for call in ctx.reply.call_args_list)

    return SimpleNamespace(plugin=plugin, send=send, now=now, requests=requests, storage=storage, response=response)


def test_known_place_survives_reload_and_recalculates_current_period(campus_chat):
    c = campus_chat
    async def run():
        await c.send(c.plugin(), '查金明校区金明综合楼3第2大节的空教室')
        c.now[0] = c.now[0].replace(hour=17, minute=20)
        handled, reply = await c.send(c.plugin(), '找自习室')
        assert handled and '金明综合楼3301' in reply
        assert '在哪个校区' not in reply and '查哪栋' not in reply
        assert c.requests[-1]['period'] == 4
        assert c.requests[-1]['campus_code'] == '02' and c.requests[-1]['building_code'] == '18'
        assert c.requests[-1]['saved_session_only'] is True
        assert '17:00–18:40' in reply
    asyncio.run(run())


def test_failed_or_conflicting_query_does_not_replace_known_place(campus_chat):
    c = campus_chat
    async def run():
        p = c.plugin()
        await c.send(p, '查金明校区金明综合楼3第2大节的空教室')
        c.response[0] = lambda r: {**r, 'success': False, 'msg': '地点不匹配'}
        await c.send(p, '查金明校区友兰学堂南楼C座的空教室')
        c.response[0] = None
        _, reply = await c.send(p, '找自习室')
        assert c.requests[-1]['building_code'] == '18'
        assert '友兰' not in reply
        assert '金明综合楼3301' in reply
    asyncio.run(run())


@pytest.mark.parametrize('different', [{'sender': 'bob'}, {'chat': 'other'}, {'bot': 'other'}, {'kind': 'person'}])
def test_known_place_isolated_by_user_chat_and_bot(campus_chat, different):
    c = campus_chat
    async def run():
        await c.send(c.plugin(), '查金明校区金明综合楼3第2大节的空教室')
        count = len(c.requests)
        _, reply = await c.send(c.plugin(), '找自习室', **different)
        assert len(c.requests) == count
        assert '在哪个校区' in reply and '金明综合楼3' not in reply
    asyncio.run(run())


@pytest.mark.parametrize(('hour', 'minute', 'period', 'day', 'clock'), [
    (7, 30, 1, '2026-10-24', '08:00–09:40'),
    (16, 45, 4, '2026-10-24', '17:00–18:40'),
    (22, 30, 1, '2026-10-25', '08:00–09:40')])
def test_default_full_question_uses_next_teaching_slot_outside_class(campus_chat, hour, minute, period, day, clock):
    c = campus_chat
    c.now[0] = c.now[0].replace(hour=hour, minute=minute)
    async def run():
        _, reply = await c.send(c.plugin(), '查金明校区金明综合楼3的空教室')
        assert c.requests[-1]['period'] == period and c.requests[-1]['target_date'] == day
        assert c.requests[-1]['saved_session_only'] is True
        assert '下一教学时段' in reply and clock in reply
    asyncio.run(run())


def test_explicit_period_and_all_day_override_remembered_current_query(campus_chat):
    c = campus_chat
    async def run():
        p = c.plugin()
        await c.send(p, '查金明校区金明综合楼3第2大节的空教室')
        await c.send(p, '找自习室')
        await c.send(p, '第2大节')
        assert c.requests[-1]['period'] == 2
        await c.send(p, '全天')
        assert c.requests[-1]['view'] == 'day_matrix'
        await c.send(p, '现在呢')
        assert c.requests[-1]['period'] == 3 and c.requests[-1]['view'] == 'free'
        count = len(c.requests)
        _, reply = await c.send(p, '明伦')
        assert len(c.requests) == count and '教学楼' in reply
    asyncio.run(run())


@pytest.mark.parametrize('case', ['failure', 'wrong_room', 'partial', 'mixed_day', 'no_cache', 'unverified', 'empty_day'])
def test_unverified_results_never_become_a_default(campus_chat, case):
    c = campus_chat
    def corrupt(result):
        if case == 'failure':
            result['success'] = False
        elif case == 'wrong_room':
            result['rooms'][0]['campusCode'] = '03'
        elif case == 'partial':
            result['freshness']['partial'] = True
        elif case == 'no_cache':
            result['rooms'] = []
            result['freshness'] = {'stale': True, 'cacheHit': False, 'liveChecked': False}
        elif case == 'unverified':
            result['freshness'] = {}
        elif case == 'empty_day':
            result['data'] = {'periods': []}
        else:
            result['data'] = {'periods': [{'query': result['query'],
                'rooms': [{'campusCode': '03', 'buildingCode': '22'}]}]}
        return result
    c.response[0] = corrupt
    async def run():
        await c.send(c.plugin(), '查金明校区金明综合楼3第2大节的空教室')
        count = len(c.requests)
        _, reply = await c.send(c.plugin(), '找自习室')
        assert len(c.requests) == count and '在哪个校区' in reply
    asyncio.run(run())


def test_place_expires_instead_of_becoming_a_permanent_preference(campus_chat, monkeypatch):
    import time
    c = campus_chat
    async def run():
        await c.send(c.plugin(), '查金明校区金明综合楼3第2大节的空教室')
        later = time.time() + 86401
        monkeypatch.setattr(time, 'time', lambda: later)
        count = len(c.requests)
        _, reply = await c.send(c.plugin(), '找自习室')
        assert len(c.requests) == count and '在哪个校区' in reply
    asyncio.run(run())


def test_now_followup_only_handles_an_established_classroom_context(campus_chat):
    c = campus_chat
    async def run():
        handled, reply = await c.send(c.plugin(), '现在呢')
        assert not handled and not reply and not c.requests
    asyncio.run(run())


def test_explicit_new_campus_takes_priority_while_waiting_for_a_building(campus_chat):
    c = campus_chat
    async def run():
        p = c.plugin()
        await c.send(p, '查金明校区金明综合楼3第2大节的空教室')
        await c.send(p, '找自习室')
        await c.send(p, '明伦')
        count = len(c.requests)
        _, reply = await c.send(p, '现在呢')
        assert len(c.requests) == count and '教学楼' in reply
        assert '金明综合楼3' not in reply
    asyncio.run(run())

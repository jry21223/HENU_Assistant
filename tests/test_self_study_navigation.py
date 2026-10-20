import asyncio
import json
import pytest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

from langbot_plugin.api.entities.builtin.platform.message import MessageChain, Plain
from henu_plugin.qq_navigation import handle_navigation

PRODUCTION_CLOCK = {str(n): {'start': start, 'end': end} for n, (start, end) in enumerate([
    ('08:00', '08:45'), ('08:55', '09:40'), ('10:00', '10:45'), ('10:55', '11:40'),
    ('11:45', '12:30'), ('14:05', '14:50'), ('15:00', '15:45'), ('15:55', '16:40'),
    ('17:00', '17:45'), ('17:55', '18:40'), ('19:10', '19:55'), ('20:05', '20:50'), ('20:55', '21:40')], 1)}


@pytest.fixture(autouse=True)
def cached_query_dir(tmp_path, monkeypatch):
    import henu_plugin.self_study as study
    from campus_core import storage_paths
    monkeypatch.setattr(study, 'campus_now', lambda: datetime(2026, 10, 20, 15, 20, tzinfo=ZoneInfo('Asia/Shanghai')))
    monkeypatch.setattr(storage_paths, 'get_empty_classroom_cache_dir', lambda: tmp_path)
    folder = tmp_path / 'query_cache'
    folder.mkdir()
    for campus in ('金明', '明伦'):
        for period in range(1, 6):
            (folder / f'{campus}-{period}.json').write_text(json.dumps({'query': {
                'termCode': '2026,0', 'week': 8, 'dayOfWeek': 2, 'period': period,
                'campusName': campus + '校区', 'buildingCode': '', 'minCapacity': 0}}))
    return folder


def context(text, sender='alice'):
    ctx = SimpleNamespace(event=SimpleNamespace(launcher_type='person', launcher_id=sender,
        sender_id=sender, message_chain=MessageChain(root=[Plain(text=text)])), query_id=1,
        reply=AsyncMock(), is_prevent_postorder=False, get_bot_uuid=AsyncMock(return_value='bot'),
        get_query_vars=AsyncMock(return_value={}), set_query_var=AsyncMock())
    ctx.prevent_default = lambda: None
    ctx.prevent_postorder = lambda: setattr(ctx, 'is_prevent_postorder', True)
    return ctx


def test_generic_building_asks_which_jinming_block_and_keeps_period(monkeypatch):
    from components.cli_tools.henu_cli_safe import HenuCliSafe
    from campus_core.resource_registry import registry
    from campus_core.resource_registry.models import ResourceRecord
    campus = ResourceRecord(resource_id='henu:campus:02', resource_type='campus',
                            display_name='金明校区', canonical_name='金明', location={'campusCode': '02'})
    def search(**kwargs):
        if kwargs['resource_type'] == 'campus':
            return [campus]
        return [ResourceRecord(resource_id=f'henu:building:02:{i}', resource_type='building',
                    display_name=f'金明综合楼{i}', canonical_name=f'金明综合楼{i}',
                    location={'campusCode': '02', 'buildingCode': str(i), 'buildingName': f'金明综合楼{i}'})
                for i in (1, 2)] + [ResourceRecord(resource_id='henu:building:01:0003', resource_type='building',
                    display_name='明伦综合教学楼', canonical_name='综合教学楼',
                    location={'campusCode': '01', 'buildingCode': '0003'})]
    monkeypatch.setattr(registry, 'search_resources', search)
    import campus_core.resource_registry as resources
    preload = []
    monkeypatch.setattr(resources, 'preload_seed_if_needed', lambda: preload.append(True))
    call = AsyncMock(return_value={'success': True, 'rooms': [], 'total': 0})
    monkeypatch.setattr(HenuCliSafe, 'call', call)
    plugin = SimpleNamespace(get_plugin_storage=AsyncMock(return_value=json.dumps(PRODUCTION_CLOCK).encode()))
    async def run():
        await handle_navigation(plugin, context('找自习室'))
        ctx = context('金明综合教学楼第2大节')
        await handle_navigation(plugin, ctx)
        assert '金明综合楼1' in str(ctx.reply.call_args)
        assert '金明综合楼2' in str(ctx.reply.call_args)
        assert '明伦综合教学楼' not in str(ctx.reply.call_args)
        assert preload
        call.assert_not_awaited()
        for invalid in ('²', '9' * 5000, '99'):
            invalid_ctx = context(invalid)
            assert not await handle_navigation(plugin, invalid_ctx)
            assert not invalid_ctx.is_prevent_postorder
        await handle_navigation(plugin, context('1'))
        command = call.call_args.args[0]['command']
        assert '--campus-code 02' in command and '--building-code 1' in command
        assert '--period 2' in command
    asyncio.run(run())


def test_automatic_period_is_held_while_choosing_building(monkeypatch):
    import henu_plugin.self_study as study
    from components.cli_tools.henu_cli_safe import HenuCliSafe
    monkeypatch.setattr(study, '_campus_building_choices', lambda _: [
        {'name': '金明综合楼1', 'campus_code': '02', 'building_code': '0006'},
        {'name': '金明综合楼2', 'campus_code': '02', 'building_code': '17'}])
    call = AsyncMock(return_value={'success': True, 'rooms': [], 'total': 0})
    monkeypatch.setattr(HenuCliSafe, 'call', call)
    plugin = SimpleNamespace(get_plugin_storage=AsyncMock(return_value=json.dumps(PRODUCTION_CLOCK).encode()))
    async def run():
        await handle_navigation(plugin, context('找自习室'))
        await handle_navigation(plugin, context('金明综合教学楼'))
        monkeypatch.setattr(study, 'campus_now', lambda: datetime(2026, 10, 20, 16, 45, tzinfo=ZoneInfo('Asia/Shanghai')))
        await handle_navigation(plugin, context('1'))
        assert '--period 3' in call.call_args.args[0]['command']
    asyncio.run(run())


@pytest.mark.parametrize('entry', ['找自习室', '空教室查询'])
def test_missing_campus_only_asks_campus(monkeypatch, entry):
    from components.cli_tools.henu_cli_safe import HenuCliSafe
    call = AsyncMock(return_value={'success': True, 'data': {'periods': [
        {'period': 2, 'periodName': '第二大节', 'rooms': [{'roomName': '十号楼101'}],
         'freshness': {'stale': False, 'ageSeconds': 20}}]}})
    monkeypatch.setattr(HenuCliSafe, 'call', call)
    plugin = SimpleNamespace(get_plugin_storage=AsyncMock(return_value=b'{}'))
    ctx = context(entry)
    assert asyncio.run(handle_navigation(plugin, ctx))
    assert '在哪个校区' in str(ctx.reply.call_args)
    assert '第几大节' not in str(ctx.reply.call_args)
    call.assert_not_awaited()
    assert '十号楼101' not in str(ctx.reply.call_args)


def test_location_reply_continues_lookup_only_for_same_conversation(monkeypatch):
    from components.cli_tools.henu_cli_safe import HenuCliSafe
    call = AsyncMock(return_value={'success': True, 'data': {'periods': []}})
    monkeypatch.setattr(HenuCliSafe, 'call', call)
    plugin = SimpleNamespace(get_plugin_storage=AsyncMock(return_value=b'{}'))
    async def run():
        initial = context('找自习室')
        assert await handle_navigation(plugin, initial)
        assert '在哪个校区' in str(initial.reply.call_args)
        count = call.await_count
        assert not await handle_navigation(plugin, context('金明校区十号楼', sender='bob'))
        assert call.await_count == count
        assert await handle_navigation(plugin, context('金明校区'))
        assert call.await_count == count
        assert await handle_navigation(plugin, context('十号楼'))
        assert call.await_count == count
        assert await handle_navigation(plugin, context('第2大节'))
        command = call.call_args.args[0]['command']
        assert '--campus-text 金明' in command and '--building-text 十号楼' in command
        assert '--view free' in command and '--period 2' in command
        assert '--freshness cache_only' not in command
        assert await handle_navigation(plugin, context('十号楼'))
    asyncio.run(run())


def test_selected_period_returns_only_that_period_rooms_and_real_login_error(monkeypatch):
    from components.cli_tools.henu_cli_safe import HenuCliSafe
    call = AsyncMock(return_value={'success': False, 'error_code': 'auth_required', 'msg': '校园登录已失效'})
    monkeypatch.setattr(HenuCliSafe, 'call', call)
    plugin = SimpleNamespace(get_plugin_storage=AsyncMock(return_value=b'{}'))
    async def run():
        await handle_navigation(plugin, context('找自习室'))
        ctx = context('金明校区十号楼第2大节')
        await handle_navigation(plugin, ctx)
        assert '校园登录已失效' in str(ctx.reply.call_args)
        assert '我的状态' in str(ctx.reply.call_args) and '绑定账号' not in str(ctx.reply.call_args)
        assert '暂无缓存' not in str(ctx.reply.call_args)
        call.return_value = {'success': True, 'rooms': [{'roomName': '十号楼101'}],
                             'freshness': {'liveChecked': True, 'stale': False}}
        ctx = context('第2大节')
        await handle_navigation(plugin, ctx)
        command = call.call_args.args[0]['command']
        assert '--period 2' in command and 'day_matrix' not in command
        assert '十号楼101' in str(ctx.reply.call_args)
        assert '本人指定第2大节' in str(ctx.reply.call_args)
    asyncio.run(run())


def test_midnight_uses_complete_saved_clock_without_claiming_all_rooms_free(monkeypatch):
    import henu_plugin.self_study as study
    from components.cli_tools.henu_cli_safe import HenuCliSafe
    monkeypatch.setattr(study, 'campus_now', lambda: datetime(2026, 10, 20, 0, 1, tzinfo=ZoneInfo('Asia/Shanghai')))
    plugin = SimpleNamespace(get_plugin_storage=AsyncMock(return_value=json.dumps(PRODUCTION_CLOCK).encode()))
    call = AsyncMock(return_value={'success': True, 'preferences': {}})
    monkeypatch.setattr(HenuCliSafe, 'call', call)
    async def run():
        await handle_navigation(plugin, context('找自习室'))
        ctx = context('金明')
        await handle_navigation(plugin, ctx)
        return ctx
    ctx = asyncio.run(run())
    call.assert_not_awaited()
    assert '当前不在教学时段' in str(ctx.reply.call_args)
    assert '不能据此认定所有教室空闲' in str(ctx.reply.call_args)


def test_unknown_clock_never_asserts_nonteaching_and_attachments_do_not_continue(monkeypatch):
    from langbot_plugin.api.entities.builtin.platform.message import Image
    plugin = SimpleNamespace(get_plugin_storage=AsyncMock(side_effect=OSError('offline')))
    async def run():
        initial = context('找自习室')
        assert await handle_navigation(plugin, initial)
        assert '在哪个校区' in str(initial.reply.call_args)
        campus = context('金明')
        assert await handle_navigation(plugin, campus)
        assert '第几大节' in str(campus.reply.call_args)
        attached = context('第2大节')
        attached.event.message_chain.root.append(Image(url='https://synthetic.invalid/image.png'))
        assert not await handle_navigation(plugin, attached)
        attached.reply.assert_not_awaited()
    asyncio.run(run())


def test_partial_clock_cannot_establish_whole_teaching_day(monkeypatch):
    import henu_plugin.self_study as study
    monkeypatch.setattr(study, 'campus_now', lambda: datetime(2026, 10, 20, 12, 0, tzinfo=ZoneInfo('Asia/Shanghai')))
    plugin = SimpleNamespace(get_plugin_storage=AsyncMock(return_value=b'{"1":{"start":"08:00","end":"08:45"}}'))
    async def run():
        await handle_navigation(plugin, context('找自习室'))
        ctx = context('金明')
        await handle_navigation(plugin, ctx)
        return ctx
    ctx = asyncio.run(run())
    assert '第几大节' in str(ctx.reply.call_args)
    assert '当前不在' not in str(ctx.reply.call_args)


def test_expired_cache_does_not_list_rooms_as_current_options(monkeypatch):
    from components.cli_tools.henu_cli_safe import HenuCliSafe
    call = AsyncMock(return_value={'success': True, 'rooms': [{'roomName': '过期教室101'}],
                                  'freshness': {'stale': True}})
    monkeypatch.setattr(HenuCliSafe, 'call', call)
    plugin = SimpleNamespace(get_plugin_storage=AsyncMock(return_value=b'{}'))
    async def run():
        await handle_navigation(plugin, context('找自习室'))
        ctx = context('金明校区第2大节')
        await handle_navigation(plugin, ctx)
        assert '过期教室101' not in str(ctx.reply.call_args)
        assert '过期' in str(ctx.reply.call_args)
    asyncio.run(run())


@pytest.mark.parametrize('campus', ['金明', '明伦'])
@pytest.mark.parametrize(('hour', 'minute', 'period'), [(8, 30, 1), (11, 50, 2), (15, 20, 3), (17, 30, 4), (20, 30, 5)])
def test_verified_kaifeng_campus_automatically_queries_current_period(monkeypatch, campus, hour, minute, period):
    import henu_plugin.self_study as study
    from components.cli_tools.henu_cli_safe import HenuCliSafe
    monkeypatch.setattr(study, 'campus_now', lambda: datetime(2026, 10, 20, hour, minute, tzinfo=ZoneInfo('Asia/Shanghai')))
    plugin = SimpleNamespace(get_plugin_storage=AsyncMock(return_value=json.dumps(PRODUCTION_CLOCK).encode()))
    call = AsyncMock(return_value={'success': True, 'rooms': [{'roomName': '十号楼101'}], 'freshness': {'stale': False}})
    monkeypatch.setattr(HenuCliSafe, 'call', call)
    async def run():
        await handle_navigation(plugin, context('找自习室'))
        ctx = context(campus)
        await handle_navigation(plugin, ctx)
        command = call.call_args.args[0]['command']
        assert f'--period {period}' in command and f'--campus-text {campus}' in command
        assert '--freshness cache_only' in command and 'day_matrix' not in command
        assert f'当前第{period}大节' in str(ctx.reply.call_args)
        assert '十号楼101' in str(ctx.reply.call_args)
    asyncio.run(run())


def test_conversation_campus_is_reused_without_inventing_a_preference(monkeypatch):
    import henu_plugin.self_study as study
    from components.cli_tools.henu_cli_safe import HenuCliSafe
    monkeypatch.setattr(study, 'campus_now', lambda: datetime(2026, 10, 20, 15, 20, tzinfo=ZoneInfo('Asia/Shanghai')))
    plugin = SimpleNamespace(get_plugin_storage=AsyncMock(return_value=json.dumps(PRODUCTION_CLOCK).encode()))
    call = AsyncMock(return_value={'success': True, 'rooms': [], 'freshness': {'stale': False}})
    monkeypatch.setattr(HenuCliSafe, 'call', call)
    async def run():
        await handle_navigation(plugin, context('找自习室'))
        await handle_navigation(plugin, context('金明'))
        ctx = context('空教室查询')
        assert await handle_navigation(plugin, ctx)
        assert '请回复教学楼' in str(ctx.reply.call_args)
        assert '在哪个校区' not in str(ctx.reply.call_args)
        assert all(c.args[0]['command'].startswith('empty classroom') for c in call.call_args_list)
    asyncio.run(run())


@pytest.mark.parametrize('campus', ['金明', '龙子湖'])
def test_mismatched_clock_or_longzihu_never_applies_kaifeng_mapping(monkeypatch, campus):
    import henu_plugin.self_study as study
    from components.cli_tools.henu_cli_safe import HenuCliSafe
    monkeypatch.setattr(study, 'campus_now', lambda: datetime(2026, 10, 20, 15, 20, tzinfo=ZoneInfo('Asia/Shanghai')))
    clock = json.loads(json.dumps(PRODUCTION_CLOCK))
    if campus == '金明':
        clock['6']['start'] = '14:00'
    plugin = SimpleNamespace(get_plugin_storage=AsyncMock(return_value=json.dumps(clock).encode()))
    call = AsyncMock(return_value={'success': True, 'preferences': {'campus': campus}})
    monkeypatch.setattr(HenuCliSafe, 'call', call)
    async def run():
        await handle_navigation(plugin, context('找自习室'))
        ctx = context(campus)
        assert await handle_navigation(plugin, ctx)
        assert '第几大节' in str(ctx.reply.call_args)
    asyncio.run(run())
    call.assert_not_awaited()


@pytest.mark.parametrize('cache_case', ['empty', 'stale', 'other_week', 'other_period'])
def test_unusable_cache_asks_building_without_starting_school_login(monkeypatch, cached_query_dir, cache_case):
    import os
    import time
    from components.cli_tools.henu_cli_safe import HenuCliSafe
    for file in cached_query_dir.glob('*.json'):
        file.unlink()
    if cache_case != 'empty':
        file = cached_query_dir / 'candidate.json'
        query = {'week': 7 if cache_case == 'other_week' else 8, 'dayOfWeek': 2,
                 'period': 1 if cache_case == 'other_period' else 2,
                 'campusName': '金明校区', 'buildingCode': '', 'minCapacity': 0}
        file.write_text(json.dumps({'query': query}))
        if cache_case == 'stale':
            os.utime(file, (time.time() - 400, time.time() - 400))
    call = AsyncMock(side_effect=AssertionError('school access must not start'))
    monkeypatch.setattr(HenuCliSafe, 'call', call)
    plugin = SimpleNamespace(get_plugin_storage=AsyncMock(return_value=b'{}'))
    async def run():
        await handle_navigation(plugin, context('找自习室'))
        ctx = context('金明第2大节')
        assert await handle_navigation(plugin, ctx)
        call.assert_not_awaited()
        assert '教学楼' in str(ctx.reply.call_args)
        assert '正在查询' not in str(ctx.reply.call_args)
    asyncio.run(run())


@pytest.mark.parametrize('owner_matches', [True, False])
def test_compacted_tool_summary_uses_only_current_conversation_full_card(monkeypatch, owner_matches):
    from components.cli_tools.henu_cli_safe import HenuCliSafe
    call = AsyncMock(return_value={'success': True, 'total': 5, 'reply_text': '找到5间空闲教室'})
    monkeypatch.setattr(HenuCliSafe, 'call', call)
    plugin = SimpleNamespace(get_plugin_storage=AsyncMock(return_value=b'{}'))
    async def run():
        await handle_navigation(plugin, context('找自习室'))
        ctx = context('金明校区金明综合楼1第2大节')
        owner = ['bot', 'person', 'alice', 'alice'] if owner_matches else ['bot', 'person', 'bob', 'bob']
        ctx.get_query_vars.return_value = {'_henu_card': {
            'owner': owner, 'page': '# 河大助手 · 空教室查询\n金明综合楼1201\n金明综合楼1303'}}
        assert await handle_navigation(plugin, ctx)
        sent = str(ctx.reply.call_args)
        if owner_matches:
            assert '金明综合楼1201' in sent and '金明综合楼1303' in sent
        else:
            assert '金明综合楼1201' not in sent
            assert '5' in sent and '清单' in sent
        assert '没有可核验的该时段教室结果' not in sent
    asyncio.run(run())

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock


def test_campus_uses_school_metadata_conflicts_are_not_guessed():
    from henu_plugin.library_availability import region_counts
    rows = region_counts([
        {'id': '43', 'name': '401 第一自习室', 'premisesName': '明伦校区',
         'nameMerge': '明伦校区-明伦四层-401 第一自习室', 'storeyName': '明伦四层', 'free_num': 5, 'total_num': 10},
        {'id': 'bad', 'name': '不明区域', 'nameMerge': '图书馆-二楼-不明区域', 'free_num': 0, 'total_num': 10},
        {'id': 'conflict', 'name': '冲突区域', 'premisesName': '金明校区',
         'nameMerge': '明伦校区-明伦二层-冲突区域', 'free_num': 1, 'total_num': 10},
    ])
    assert rows[0]['campus'] == '明伦校区' and rows[0]['floor'] == '明伦四层'
    assert rows[1]['campus'] == '校区待确认'
    assert rows[2]['campus'] == '校区待确认'
    conflict = region_counts([{'id': 'floor-conflict', 'name': '书库', 'premisesName': '明伦校区',
                              'storeyName': '明伦五层', 'nameMerge': '明伦校区-明伦四层-书库'}])[0]
    assert conflict['campus'] == '明伦校区' and conflict['floor'] == ''


def test_verified_school_regions_keep_their_actual_campus():
    import json
    from pathlib import Path
    from henu_plugin.library_availability import region_counts
    payload = json.loads((Path(__file__).parents[1] / 'docs/evidence/library-regions-20261020.json').read_text())
    rows = region_counts(payload['areas'])
    assert len(rows) == 31
    assert sum(r['campus'] == '金明校区' for r in rows) == 20
    assert sum(r['campus'] == '明伦校区' for r in rows) == 11
    for word in ('第一自习室', '第二自习室', '第三自习室', '东馆', '西馆'):
        selected = [r for r in rows if word in r['location']]
        assert selected and all(r['campus'] == '明伦校区' for r in selected)
    assert all(r['available_count'] is None for r in rows)  # Metadata has no live counts.


def test_interleaved_school_rows_are_grouped_once_per_campus(monkeypatch):
    from henu_plugin import library_availability as lib
    monkeypatch.setattr(lib, 'query_library', AsyncMock(return_value={'success': True, 'locations': [
        {'location': '第一自习室', 'campus': '明伦校区', 'available_count': 5},
        {'location': '七层南自习室', 'campus': '金明校区', 'available_count': 10},
        {'location': '东馆书库', 'campus': '明伦校区', 'available_count': 0}]}))
    ctx = context()
    asyncio.run(lib.handle_library_availability(SimpleNamespace(), ctx, '图书馆座位'))
    sent = str(ctx.reply.call_args)
    assert sent.count('明伦校区') == 1 and sent.count('金明校区') == 1
    assert sent.index('七层南自习室') < sent.index('第一自习室') < sent.index('东馆书库')


def test_cli_library_card_also_shows_campus_not_just_area_names():
    from henu_plugin.qq_cards import result_card
    page = '\n'.join(result_card({'success': True, 'locations': [
        {'location': '401 第一自习室', 'campus': '明伦校区', 'available_count': 5},
        {'location': '701 七层南自习室', 'campus': '金明校区', 'available_count': 10},
    ]}, 'library_query').pages())
    assert '明伦校区' in page and '金明校区' in page
    assert 'area_id' not in page and '剩余 5' in page


def test_final_model_delivery_keeps_campus_floor_and_counts_for_all_regions():
    import json
    from pathlib import Path
    from campus_core.locations import LocationMixin
    from components.cli_tools.henu_cli_safe import HenuCliSafe
    raw = json.loads((Path(__file__).parents[1] / 'docs/evidence/library-regions-20261020.json').read_text())['areas']
    locations = [LocationMixin._location_summary({**r, 'free_num': 2, 'total_num': 10}) for r in raw]
    result = {'success': True, 'source': 'live', 'locations': locations, 'total': 31}
    tool = HenuCliSafe.__new__(HenuCliSafe)
    tool._prepare_delivery_result(result)
    tool._normalize_for_qq_delivery(result)
    options = result['location_options']
    assert len(options) == 31
    assert sum(r['campus'] == '明伦校区' for r in options) == 11
    assert all(r['floor'] and r['available_count'] == 2 for r in options)
    assert len(json.dumps(result, ensure_ascii=False)) <= 8000


def context(text='图书馆座位', sender='alice', kind='person'):
    ctx = SimpleNamespace(event=SimpleNamespace(launcher_type=kind, launcher_id=sender, sender_id=sender),
        query_id=1, reply=AsyncMock(), get_bot_uuid=AsyncMock(return_value='bot'),
        set_query_var=AsyncMock(), is_prevent_postorder=False)
    ctx.prevent_default = lambda: None
    ctx.prevent_postorder = lambda: setattr(ctx, 'is_prevent_postorder', True)
    return ctx


def test_entry_shows_real_region_counts_without_ids_or_unknown_as_zero(monkeypatch):
    from henu_plugin import library_availability as lib
    query = AsyncMock(return_value={'success': True, 'date': '2026-10-20', 'locations': [
        {'location': '金明南走廊', 'area_id': 'internal-secret-id', 'available_count': 58},
        {'location': '明伦阅览室', 'available_count': 0}, {'location': '待核验区域', 'available_count': None}]})
    monkeypatch.setattr(lib, 'query_library', query)
    ctx = context()
    assert asyncio.run(lib.handle_library_availability(SimpleNamespace(), ctx, '图书馆座位'))
    sent = str(ctx.reply.call_args)
    assert '58' in sent and '剩余 0' in sent and '未知' in sent
    assert 'internal-secret-id' not in sent and '区域编号' not in sent
    assert query.await_count == 1


def test_existing_library_card_hides_ids_and_uses_chinese_auth_and_sync_steps():
    from henu_plugin.qq_cards import result_card
    page = '\n'.join(result_card({'success': True, 'locations': [
        {'location': '金明南走廊', 'area_id': 'hidden-internal-id', 'available_count': 58},
        {'location': '明伦阅览室', 'available_count': None}]}, 'library_query').pages())
    assert 'hidden-internal-id' not in page and '区域编号' not in page
    assert '剩余 58' in page and '未知' in page
    auth = result_card({'success': False, 'error_code': 'account_not_bound'}, 'library_query').next_step
    assert '绑定账号' in auth and 'help account' not in auth
    sync = result_card({'success': True, 'next_commands': ['schedule now', 'schedule week']}, 'sync_schedule').next_step
    assert '今天课表' in sync and '本周课表' in sync and 'schedule ' not in sync


def test_public_entry_reads_raw_counts_once_and_fits_31_regions_on_one_page(monkeypatch):
    import json
    from henu_plugin import library_availability as lib
    calls = []
    def post(self, url, **kwargs):
        calls.append((url, kwargs['json']))
        return SimpleNamespace(json=lambda: {'code': 0, 'data': {'area': [
            {'id': str(i), 'name': f'区域{i}', 'nameMerge': f'金明校区-二层-区域{i}', 'free_num': i, 'total_num': 100} for i in range(31)]}})
    monkeypatch.setattr('requests.Session.post', post)
    async def storage(key):
        return json.dumps({'_v4_token': 'synthetic-secret'} if key.endswith(':library_cookie') else {}).encode()
    plugin = SimpleNamespace(get_plugin_storage=storage)
    ctx = context()
    assert asyncio.run(lib.handle_library_availability(plugin, ctx, '图书馆座位'))
    assert len(calls) == 1 and calls[0][0].endswith('/v4/space/pick')
    assert ctx.reply.await_count == 2  # acknowledgement + all 31 regions
    page = str(ctx.reply.call_args)
    assert '区域30' in page and '剩余 30' in page and 'synthetic-secret' not in page


def test_natural_time_followup_is_scoped_and_passes_explicit_interval(monkeypatch):
    from henu_plugin import library_availability as lib
    from datetime import datetime
    monkeypatch.setattr(lib, 'campus_now', lambda: datetime.fromisoformat('2026-10-20T10:00:00+08:00'))
    query = AsyncMock(return_value={'success': True, 'locations': [{'location': '阅览室', 'available_count': None}]})
    monkeypatch.setattr(lib, 'query_library', query)
    plugin = SimpleNamespace()
    async def run():
        await lib.handle_library_availability(plugin, context(), '图书馆座位')
        assert not await lib.handle_library_availability(plugin, context(sender='bob'), '今天14:00-16:00')
        ctx = context()
        assert await lib.handle_library_availability(plugin, ctx, '今天14:00-16:00')
        assert query.call_args.args[2:] == ('2026-10-20', '14:00', '16:00')
        before = query.await_count
        assert await lib.handle_library_availability(plugin, context(), '今天25:00-26:00')
        assert query.await_count == before
    asyncio.run(run())


def test_explicit_window_uses_backend_plan_and_unknown_seat_payload_is_not_zero(monkeypatch):
    import json
    from henu_plugin import library_availability as lib
    calls = []
    def post(self, url, **kwargs):
        body = kwargs['json']; calls.append((url, body))
        if url.endswith('/space/pick'):
            data = {'area': [{'id': str(n), 'name': f'区域{n}', 'free_num': 99, 'total_num': 100} for n in range(3)]}
        elif url.endswith('/Space/map'):
            end = '15:00' if body['id'] == '2' else '18:00'
            data = {'type': '1', 'date': {'reserveType': '3', 'list': [
                {'day': '2026-10-20', 'def_start_time': '08:00', 'def_end_time': end}]}}
        elif url.endswith('/Space/seat'):
            assert body['day'] == '2026-10-20' and body['start_time'] == '14:00' and body['end_time'] == '16:00'
            data = {'list': [{'status': '1'}, {'status': '0'}, {'status': 1}]} if body['id'] == '0' else {}
        else:
            raise AssertionError('Only query endpoints allowed')
        return SimpleNamespace(json=lambda: {'code': 0, 'data': data})
    monkeypatch.setattr('requests.Session.post', post)
    async def storage(key):
        return json.dumps({'_v4_token': 'synthetic-secret'} if key.endswith(':library_cookie') else {}).encode()
    result = asyncio.run(lib.query_library(SimpleNamespace(get_plugin_storage=storage), 'alice', '2026-10-20', '14:00', '16:00'))
    assert [r['available_count'] for r in result['locations']] == [2, None, None]
    assert not any(body.get('id') == '2' for url, body in calls if url.endswith('/Space/seat'))


def test_seat_aggregation_has_four_worker_limit_and_total_deadline(monkeypatch):
    import json
    import threading
    import time
    from henu_plugin import library_availability as lib
    guard = threading.Lock()
    active = peak = started = 0
    def post(self, url, **kwargs):
        nonlocal active, peak, started
        if url.endswith('/space/pick'):
            return SimpleNamespace(json=lambda: {'code': 0, 'data': {'area': [
                {'id': str(i), 'name': f'区域{i}'} for i in range(31)]}})
        with guard:
            active += 1; started += 1; peak = max(peak, active)
        time.sleep(.06)
        with guard:
            active -= 1
        return SimpleNamespace(json=lambda: {'code': 1})
    monkeypatch.setattr('requests.Session.post', post)
    monkeypatch.setattr(lib, 'QUERY_BUDGET', .02)
    async def storage(key):
        return json.dumps({'_v4_token': 'synthetic'} if key.endswith(':library_cookie') else {}).encode()
    result = asyncio.run(lib.query_library(SimpleNamespace(get_plugin_storage=storage), 'alice', '2026-10-20', '14:00', '16:00'))
    assert peak <= 4 and started == 4
    assert len(result['locations']) == 31 and all(r['available_count'] is None for r in result['locations'])


def test_missing_date_count_requests_window_without_guessing_opening_hours(monkeypatch):
    from henu_plugin import library_availability as lib
    query = AsyncMock(return_value={'success': True, 'locations': [
        {'location': '某阅览室', 'available_count': None}]})
    monkeypatch.setattr(lib, 'query_library', query)
    ctx = context()
    assert asyncio.run(lib.handle_library_availability(SimpleNamespace(), ctx, '图书馆座位'))
    assert query.call_args.args[3:] == ('', '')
    assert '未知' in str(ctx.reply.call_args) and '14:00-16:00' in str(ctx.reply.call_args)


def test_saved_account_restores_login_once_without_rebinding(monkeypatch):
    import json
    from components.cli_tools.henu_cli_safe import HenuCliSafe
    from henu_plugin import library_availability as lib
    storage_data = {'user:alice:profile': json.dumps({'student_id': 'saved-account', 'password': 'encrypted-placeholder'}).encode()}
    async def storage(key): return storage_data.get(key, b'{}')
    async def recover(self, params, session, query_id):
        assert params['command'].startswith('library locations --date ')
        storage_data['user:alice:library_cookie'] = json.dumps({'_v4_token': 'renewed'}).encode()
        return {'success': True}
    recovery = AsyncMock(side_effect=recover)
    async def call(self, params, session, query_id): return await recovery(self, params, session, query_id)
    monkeypatch.setattr(HenuCliSafe, 'call', call)
    def post(self, url, **kwargs):
        return SimpleNamespace(json=lambda: {'code': 0, 'data': {'area': [{'name': '阅览室', 'free_num': 12, 'total_num': 20}]}})
    monkeypatch.setattr('requests.Session.post', post)
    result = asyncio.run(lib.query_library(SimpleNamespace(get_plugin_storage=storage), 'alice', '2026-10-20'))
    assert result['success'] and result['locations'][0]['available_count'] == 12
    assert recovery.await_count == 1


def test_expired_login_recovers_only_once_and_never_tells_saved_user_to_rebind(monkeypatch):
    import json
    from components.cli_tools.henu_cli_safe import HenuCliSafe
    from henu_plugin import library_availability as lib
    async def storage(key):
        value = {'student_id': 'saved', 'password': 'encrypted'} if key.endswith(':profile') else {'_v4_token': 'expired'} if key.endswith(':library_cookie') else {}
        return json.dumps(value).encode()
    recovery = AsyncMock(return_value={'success': True})
    async def call(self, params, session, query_id): return await recovery(params, session, query_id)
    monkeypatch.setattr(HenuCliSafe, 'call', call)
    monkeypatch.setattr('requests.Session.post', lambda *a, **k: SimpleNamespace(json=lambda: {'code': 10001}))
    ctx = context()
    assert asyncio.run(lib.handle_library_availability(SimpleNamespace(get_plugin_storage=storage), ctx, '图书馆座位'))
    assert recovery.await_count == 1
    assert '我的状态' in str(ctx.reply.call_args) and '绑定账号' not in str(ctx.reply.call_args)


def test_no_saved_account_is_distinct_and_storage_failure_is_not_empty(monkeypatch):
    from components.cli_tools.henu_cli_safe import HenuCliSafe
    from henu_plugin import library_availability as lib
    recovery = AsyncMock()
    monkeypatch.setattr(HenuCliSafe, 'call', recovery)
    plugin = SimpleNamespace(get_plugin_storage=AsyncMock(return_value=b'{}'))
    result = asyncio.run(lib.query_library(plugin, 'alice', '2026-10-20'))
    assert result['error_code'] == 'account_not_bound'
    recovery.assert_not_awaited()
    plugin.get_plugin_storage = AsyncMock(side_effect=OSError('offline'))
    ctx = context()
    assert asyncio.run(lib.handle_library_availability(plugin, ctx, '图书馆座位'))
    assert '绑定账号' not in str(ctx.reply.call_args) and '剩余 0' not in str(ctx.reply.call_args)


def test_navigation_entry_and_time_followup_reach_availability(monkeypatch):
    from henu_plugin import library_availability as lib
    from henu_plugin.qq_navigation import handle_navigation
    from langbot_plugin.api.entities.builtin.platform.message import MessageChain, Plain
    query = AsyncMock(return_value={'success': True, 'locations': [{'location': '阅览室', 'available_count': 12}]})
    monkeypatch.setattr(lib, 'query_library', query)
    plugin = SimpleNamespace()
    async def run():
        for text in ('图书馆座位', '明天14:00-16:00'):
            ctx = context()
            ctx.event.message_chain = MessageChain(root=[Plain(text=text)])
            assert await handle_navigation(plugin, ctx)
            assert '剩余 12' in str(ctx.reply.call_args)
        assert query.await_count == 2
    asyncio.run(run())


def test_unknown_seat_status_is_never_reported_as_zero(monkeypatch):
    import json
    from henu_plugin import library_availability as lib
    states = [{}, [], '', True, 99, 'unknown', None, 0, '0', 1]
    def post(self, url, **kwargs):
        body = kwargs['json']
        if url.endswith('/space/pick'):
            data = {'area': [{'id': str(i), 'name': f'区域{i}'} for i in range(len(states))]}
        elif url.endswith('/Space/map'):
            data = {'type': '1', 'date': {'reserveType': '3', 'list': [
                {'day': '2026-10-20', 'def_start_time': '08:00', 'def_end_time': '18:00'}]}}
        else:
            data = {'list': [{'status': states[int(body['id'])]}]}
        return SimpleNamespace(json=lambda: {'code': 0, 'data': data})
    monkeypatch.setattr('requests.Session.post', post)
    async def storage(key):
        return json.dumps({'_v4_token': 'synthetic'} if key.endswith(':library_cookie') else {}).encode()
    result = asyncio.run(lib.query_library(SimpleNamespace(get_plugin_storage=storage), 'alice', '2026-10-20', '14:00', '16:00'))
    assert [r['available_count'] for r in result['locations']] == [None] * 7 + [0, 0, 1]


def test_repeated_overlapping_queries_share_one_worker_limit(monkeypatch):
    import json
    import threading
    import time
    from henu_plugin import library_availability as lib
    guard = threading.Lock()
    release = threading.Event()
    active = peak = 0
    def post(self, url, **kwargs):
        nonlocal active, peak
        if url.endswith('/space/pick'):
            return SimpleNamespace(json=lambda: {'code': 0, 'data': {'area': [{'id': '1', 'name': '区域'}]}})
        with guard:
            active += 1; peak = max(peak, active)
        release.wait(.3)
        with guard:
            active -= 1
        return SimpleNamespace(json=lambda: {'code': 1})
    monkeypatch.setattr('requests.Session.post', post)
    monkeypatch.setattr(lib, 'QUERY_BUDGET', .025)
    async def storage(key):
        return json.dumps({'_v4_token': 'synthetic'} if key.endswith(':library_cookie') else {}).encode()
    async def run():
        plugin = SimpleNamespace(get_plugin_storage=storage)
        try:
            for _ in range(2):
                await asyncio.gather(*(lib.query_library(plugin, f'user{i}', '2026-10-20', '14:00', '16:00') for i in range(6)))
            assert peak <= 4
        finally:
            release.set()
    asyncio.run(run())


def test_request_layer_uses_remaining_deadline_and_does_not_query_seats_after_expiry(monkeypatch):
    import json
    import time
    from henu_plugin import library_availability as lib
    observed = []
    def request(self, method, url, **kwargs):
        timeout = kwargs['timeout']
        observed.append((url, timeout.total))
        assert 0 < timeout.total <= .04
        if url.endswith('/space/pick'):
            data = {'area': [{'id': '1', 'name': '区域'}]}
        else:
            time.sleep(.05)
            data = {'type': '1', 'date': {'reserveType': '3', 'list': []}}
        return SimpleNamespace(json=lambda: {'code': 0, 'data': data})
    monkeypatch.setattr('requests.Session.request', request)
    monkeypatch.setattr(lib, 'QUERY_BUDGET', .04)
    async def storage(key):
        return json.dumps({'_v4_token': 'synthetic'} if key.endswith(':library_cookie') else {}).encode()
    result = asyncio.run(lib.query_library(SimpleNamespace(get_plugin_storage=storage), 'alice', '2026-10-20', '14:00', '16:00'))
    assert result['locations'][0]['available_count'] is None
    assert len(observed) == 2 and observed[1][1] <= observed[0][1]


def test_generic_auth_guidance_does_not_rebind_saved_or_unknown_accounts():
    from henu_plugin.qq_cards import result_card
    for code in ('auth_required', 'login_required', 'auth_failed'):
        result = {'success': False, 'error_code': code, 'msg': '登录失败'}
        page = result_card(result, 'library_query')
        assert '我的状态' in page.next_step and '绑定账号' not in page.next_step
    assert '绑定账号' in result_card({'success': False, 'error_code': 'account_not_bound'}, 'library_query').next_step
    rain = result_card({'success': False, 'error_code': 'auth_required'}, 'yuketang_status').next_step
    assert '雨课堂' in rain and '绑定账号' not in rain


def test_query_cards_hide_internal_resource_ids_but_keep_physical_names():
    from henu_plugin.qq_cards import result_card
    seat = {'id': 'internal-seat-id', 'area_id': 'internal-area-id', 'resourceId': 'internal-resource-id', 'seat_no': 'A101'}
    source = {'success': True, 'area': {'id': 'internal-area-id', 'name': '阅览室'}, 'seats': [seat]}
    page = '\n'.join(result_card(source, 'library_query').pages())
    assert 'A101' in page and '阅览室' in page and 'internal-' not in page
    assert seat['id'] == 'internal-seat-id'  # presentation leaves tool data intact
    room = {'success': True, 'data': {'roomName': '十号楼101', 'id': 'internal-room-id', 'periods': [
        {'period': 2, 'rooms': [{'roomName': '十号楼102', 'resourceId': 'internal-resource-id', 'area_id': 'internal-area-id'}]}]}}
    page = '\n'.join(result_card(room, 'empty_classroom_query').pages())
    assert '十号楼101' in page and '十号楼102' in page and 'internal-' not in page


def test_library_followup_does_not_consume_other_task_entries(monkeypatch):
    from henu_plugin import library_availability as lib
    query = AsyncMock(return_value={'success': True, 'locations': []})
    monkeypatch.setattr(lib, 'query_library', query)
    plugin = SimpleNamespace()
    async def run():
        await lib.handle_library_availability(plugin, context(), '图书馆座位')
        for text in ('今天课表', '明天课表', '我的状态', '找自习室', '绑定账号', '课表推送'):
            ctx = context(text)
            assert not await lib.handle_library_availability(plugin, ctx, text)
            ctx.reply.assert_not_awaited()
    asyncio.run(run())

import asyncio
import base64
import io

import pytest

from langbot_plugin.api.entities.builtin.platform.message import Image, Plain
from test_current_time_classroom import campus_chat  # noqa: F401 -- shared public QQ/RPC fixture
from test_qq_unified_responses import harness  # noqa: F401 -- public tool/presentation fixture


def test_scoped_classroom_query_sends_map_and_short_summary(campus_chat):
    c = campus_chat
    def full_result(result):
        result['rooms'] = [{'roomName': f'金明综合楼3{floor}{room:02}', 'campusCode': '02',
                            'buildingCode': '18'} for floor in range(1, 6) for room in range(1, 10)]
        return result
    c.response[0] = full_result
    async def run():
        _, reply = await c.send(c.plugin(), '查金明校区金明综合楼3第2大节的空教室')
        # The public QQ fixture records the actual message chains, not rendering internals.
        assert any(isinstance(item, Image) for chain in c.messages for item in chain.root)
        assert reply.count('金明综合楼3') < 12
        assert '教室清单' in reply
    asyncio.run(run())


@pytest.mark.parametrize('stale', [False, True])
def test_only_fresh_explicit_rooms_turn_green_at_their_reviewed_position(campus_chat, stale):
    from PIL import Image as PillowImage
    c = campus_chat
    c.response[0] = lambda r: {**r, 'freshness': {**r['freshness'], 'stale': stale}}
    async def run():
        await c.send(c.plugin(), '查金明校区金明综合楼3第2大节的空教室')
        image = next(item for chain in c.messages for item in chain.root if isinstance(item, Image))
        png = PillowImage.open(io.BytesIO(base64.b64decode(image.base64)))
        assert png.size == (1280, 1550)
        # Photo: 3301 is north of the third-floor corridor; 3101 was not returned.
        assert png.getpixel((190, 758))[:3] == ((225, 230, 236) if stale else (35, 155, 112))
        assert png.getpixel((190, 1178))[:3] == (225, 230, 236)
    asyncio.run(run())


def test_image_delivery_uncertainty_falls_back_once_without_a_long_list(campus_chat):
    c = campus_chat
    c.media_error[0] = True
    async def run():
        _, reply = await c.send(c.plugin(), '查金明校区金明综合楼3第2大节的空教室')
        assert sum(isinstance(item, Image) for chain in c.messages for item in chain.root) == 1
        assert '示意图未能显示' in reply and '教室清单' in reply
    asyncio.run(run())


def test_complete_list_is_opt_in_and_owner_scoped(campus_chat):
    c = campus_chat
    async def run():
        p = c.plugin()
        await c.send(p, '查金明校区金明综合楼3第2大节的空教室')
        c.messages.clear()
        _, denied = await c.send(p, '教室清单', sender='bob')
        assert '重新查询' in denied and '3301' not in denied
        _, detail = await c.send(p, '教室清单')
        assert '金明综合楼3301' in detail
        assert not any(isinstance(item, Image) for chain in c.messages for item in chain.root)
    asyncio.run(run())


def test_all_day_diagram_keeps_independent_period_states(campus_chat):
    from PIL import Image as PillowImage
    c = campus_chat
    def day(result):
        result.pop('rooms')
        query = result.pop('query')
        result['data'] = {**query, 'date': '2026-10-24', 'periods': [
            {'period': p, 'query': {**query, 'period': p},
             'rooms': [{'roomName': '金明综合楼3301', 'campusCode': '02', 'buildingCode': '18'}],
             'total': 1, 'freshness': {'liveChecked': True, 'stale': p == 2}}
            for p in range(1, 6)]}
        return result
    c.response[0] = day
    async def run():
        _, reply = await c.send(c.plugin(), '查金明校区金明综合楼3全天的空教室')
        image = next(item for chain in c.messages for item in chain.root if isinstance(item, Image))
        png = PillowImage.open(io.BytesIO(base64.b64decode(image.base64)))
        # First period is fresh; second has expired, even though it lists the same room.
        assert png.getpixel((197, 794))[:3] == (35, 155, 112)
        assert png.getpixel((218, 794))[:3] == (225, 230, 236)
        assert '全天' in reply and '缓存已过期' in reply
    asyncio.run(run())


@pytest.mark.parametrize(('number', 'code', 'room'), [('2', '17', '2101'), ('5', '20', '5101'), ('6', '21', '6101')])
def test_other_reviewed_buildings_use_their_own_layout(campus_chat, number, code, room):
    from PIL import Image as PillowImage
    c = campus_chat
    def response(result):
        result['query'].update(buildingCode=code, buildingName=f'金明综合楼{number}')
        result['rooms'] = [{'roomName': f'金明综合楼{room}', 'campusCode': '02', 'buildingCode': code}]
        return result
    c.response[0] = response
    async def run():
        await c.send(c.plugin(), f'查金明校区金明综合楼{number}第2大节的空教室')
        image = next(item for chain in c.messages for item in chain.root if isinstance(item, Image))
        png = PillowImage.open(io.BytesIO(base64.b64decode(image.base64)))
        assert png.info['Title'] == f'金明校区 · 综合楼{number}'
    asyncio.run(run())


@pytest.mark.parametrize('query_change', [{'campusCode': '03', 'campusName': '郑州校区'},
                                       {'buildingCode': '19', 'buildingName': '金明综合楼4'},
                                       {'campusName': '郑州校区'}])
def test_unknown_or_conflicting_scope_never_reuses_another_layout(campus_chat, query_change):
    c = campus_chat
    def response(result):
        result['query'].update(query_change)
        return result
    c.response[0] = response
    async def run():
        _, reply = await c.send(c.plugin(), '查金明校区金明综合楼3第2大节的空教室')
        assert not any(isinstance(item, Image) for chain in c.messages for item in chain.root)
        assert '教室清单' in reply
    asyncio.run(run())


@pytest.mark.parametrize('kind', ['person', 'group'])
def test_natural_language_tool_presentation_uses_the_same_classroom_image(harness, kind):
    from langbot_plugin.api.entities.builtin.provider.session import LauncherTypes
    h = harness
    h.session.launcher_type = LauncherTypes.GROUP if kind == 'group' else LauncherTypes.PERSON
    h.variables[1] = {'henu_sender_id': 'alice', 'henu_launcher_id': 'alice', 'henu_launcher_type': kind}
    command = 'empty classroom query --campus-code 02 --building-code 18 --period 3'
    h.payloads[command] = {'success': True, 'cli': {'resolved_tool': 'empty_classroom_query'},
        'query': {'campusCode': '02', 'buildingCode': '18', 'campusName': '金明校区',
                  'buildingName': '金明综合楼3', 'week': 8, 'dayOfWeek': 6, 'period': 3},
        'rooms': [{'roomName': '金明综合楼3301', 'campusCode': '02', 'buildingCode': '18'}],
        'freshness': {'liveChecked': True}}
    async def run():
        result = await h.tool.call({'command': command}, h.session, 1)
        receipt = await h.presenter.call({'cards': [{'ref': result['display_ref']}]}, h.session, 1)
        assert receipt['success']
        ctx = h.context(1)
        ctx.event.launcher_type = kind
        await h.listener.present(ctx)
        assert sum(isinstance(item, Image) for call in ctx.reply.call_args_list for item in call.args[0].root) == 1, ctx.event.reply
        assert '教室清单' in ctx.event.reply[0]['text']
    asyncio.run(run())


def test_requested_long_list_keeps_existing_next_page_handler(harness):
    from henu_plugin.qq_navigation import handle_navigation
    h = harness
    command = 'empty classroom query --campus-code 02 --building-code 18 --period 3'
    h.payloads[command] = {'success': True, 'cli': {'resolved_tool': 'empty_classroom_query'},
        'query': {'campusCode': '02', 'buildingCode': '18', 'period': 3},
        'rooms': [{'roomName': f'金明综合楼3附属教室{n:03}', 'capacity': 80} for n in range(1, 81)],
        'freshness': {'liveChecked': True}}
    async def run():
        await h.tool.call({'command': command}, h.session, 1)
        first = h.context(2, '教室清单')
        assert await handle_navigation(h.plugin, first)
        assert '下一页' in str(first.reply.call_args)
        second = h.context(3, '下一页')
        assert await h.listener.paging(second)
        assert '080' in str(second.reply.call_args)
    asyncio.run(run())


def test_list_cannot_outlive_the_remaining_school_cache_ttl(campus_chat, monkeypatch):
    import time
    c = campus_chat
    c.response[0] = lambda r: {**r, 'freshness': {'liveChecked': False, 'stale': False,
        'fetchedAt': '2026-10-24T08:00:00Z', 'ageSeconds': 299, 'ttlSeconds': 300}}
    async def run():
        p = c.plugin()
        await c.send(p, '查金明校区金明综合楼3第2大节的空教室')
        later = time.monotonic() + 2
        monkeypatch.setattr(time, 'monotonic', lambda: later)
        _, reply = await c.send(p, '教室清单')
        assert '重新查询' in reply and '3301' not in reply
    asyncio.run(run())


def test_metadata_query_still_has_a_native_card_and_reference(harness):
    h = harness
    command = 'empty classroom campuses'
    h.payloads[command] = {'success': True, 'cli': {'resolved_tool': 'empty_classroom_query'},
                          'data': [{'campusCode': '02', 'campusName': '金明校区'}]}
    async def run():
        result = await h.tool.call({'command': command}, h.session, 1)
        assert result.get('display_ref'), result
        assert (await h.presenter.call({'cards': [{'ref': result['display_ref']}]}, h.session, 1))['success']
        ctx = h.context(1)
        await h.listener.present(ctx)
        assert '金明校区' in ctx.event.reply[0]['text']
    asyncio.run(run())


def test_all_day_summary_is_short_but_requested_list_contains_every_room(campus_chat):
    c = campus_chat
    def day(result):
        result.pop('rooms')
        query = result.pop('query')
        result['data'] = {**query, 'date': '2026-10-24', 'periods': [
            {'period': p, 'query': {**query, 'period': p},
             'rooms': [{'roomName': f'金明综合楼3{p}0{n}'} for n in range(1, 4)],
             'total': 3, 'freshness': {'liveChecked': True}}
            for p in range(1, 6)]}
        return result
    c.response[0] = day
    async def run():
        p = c.plugin()
        _, summary = await c.send(p, '查金明校区金明综合楼3全天的空教室')
        assert '教室清单' in summary
        assert sum(summary.count(f'金明综合楼3{i}0{n}') for i in range(1, 6) for n in range(1, 4)) <= 3
        _, detail = await c.send(p, '教室清单')
        assert all(f'金明综合楼3{i}03' in detail for i in range(1, 6))
    asyncio.run(run())


@pytest.mark.parametrize('prepare_first', [False, True])
def test_source_deadline_applies_before_present_and_before_actual_delivery(harness, monkeypatch, prepare_first):
    import time
    h = harness
    frozen = time.monotonic()
    monkeypatch.setattr(time, 'monotonic', lambda: frozen)
    command = 'empty classroom query --campus-code 02 --building-code 18 --period 3'
    h.payloads[command] = {'success': True, 'cli': {'resolved_tool': 'empty_classroom_query'},
        'query': {'campusCode': '02', 'buildingCode': '18', 'period': 3},
        'rooms': [{'roomName': '金明综合楼3301'}],
        'freshness': {'liveChecked': False, 'fetchedAt': '2026-10-24T08:00:00Z', 'ageSeconds': 299, 'ttlSeconds': 300}}
    async def run():
        result = await h.tool.call({'command': command}, h.session, 1)
        params = {'cards': [{'ref': result['display_ref']}]}
        if prepare_first:
            assert (await h.presenter.call(params, h.session, 1))['success']
        later = time.monotonic() + 2
        monkeypatch.setattr(time, 'monotonic', lambda: later)
        if prepare_first:
            ctx = h.context(1)
            await h.listener.present(ctx)
            assert not any(isinstance(item, Image) for call in ctx.reply.call_args_list for item in call.args[0].root)
            assert '重新查询' in ctx.event.reply[0]['text']
        else:
            assert not (await h.presenter.call(params, h.session, 1))['success']
    asyncio.run(run())


def test_open_list_next_page_does_not_extend_source_deadline(harness, monkeypatch):
    import time
    from henu_plugin.qq_navigation import handle_navigation
    h = harness
    command = 'empty classroom query --campus-code 02 --building-code 18 --period 3'
    h.payloads[command] = {'success': True, 'cli': {'resolved_tool': 'empty_classroom_query'},
        'query': {'campusCode': '02', 'buildingCode': '18', 'period': 3},
        'rooms': [{'roomName': f'金明综合楼3附属教室{n:03}', 'capacity': 80} for n in range(1, 81)],
        'freshness': {'liveChecked': False, 'fetchedAt': '2026-10-24T08:00:00Z', 'ageSeconds': 299, 'ttlSeconds': 300}}
    async def run():
        await h.tool.call({'command': command}, h.session, 1)
        assert await handle_navigation(h.plugin, h.context(2, '教室清单'))
        later = time.monotonic() + 2
        monkeypatch.setattr(time, 'monotonic', lambda: later)
        ctx = h.context(3, '下一页')
        assert await h.listener.paging(ctx)
        assert '重新查询' in str(ctx.reply.call_args) and '080' not in str(ctx.reply.call_args)
    asyncio.run(run())


def test_image_rendering_does_not_renew_the_source_lifetime(harness, monkeypatch):
    import time
    h = harness
    command = 'empty classroom query --campus-code 02 --building-code 18 --period 3'
    h.payloads[command] = {'success': True, 'cli': {'resolved_tool': 'empty_classroom_query'},
        'query': {'campusCode': '02', 'buildingCode': '18', 'period': 3},
        'rooms': [{'roomName': '金明综合楼3301'}],
        'freshness': {'liveChecked': False, 'fetchedAt': '2026-10-24T08:00:00Z', 'ageSeconds': 299, 'ttlSeconds': 300}}
    original = time.monotonic
    offset = [0]
    monkeypatch.setattr(time, 'monotonic', lambda: original() + offset[0])
    async def run():
        # The actual PIL render runs in a worker thread. Advance time while it
        # is running, without replacing rendering, storage or presentation.
        timer = asyncio.get_running_loop().call_later(.01, lambda: offset.__setitem__(0, 2))
        try:
            result = await h.tool.call({'command': command}, h.session, 1)
            assert offset[0] == 2
            assert not (await h.presenter.call({'cards': [{'ref': result['display_ref']}]}, h.session, 1))['success']
        finally:
            timer.cancel()
    asyncio.run(run())


def test_later_read_cannot_renew_an_expired_first_page_of_the_same_query(harness, monkeypatch):
    import time
    h = harness
    first = 'empty classroom query --campus-code 02 --building-code 18 --period 2'
    second = 'empty classroom query --campus-code 02 --building-code 18 --period 3'
    payload = {'success': True, 'cli': {'resolved_tool': 'empty_classroom_query'},
        'query': {'campusCode': '02', 'buildingCode': '18', 'period': 2},
        'rooms': [{'roomName': '金明综合楼3301'}],
        'freshness': {'liveChecked': False, 'fetchedAt': '2026-10-24T08:00:00Z', 'ageSeconds': 299, 'ttlSeconds': 300}}
    h.payloads[first] = payload
    h.payloads[second] = {**payload, 'freshness': {'liveChecked': True}}
    async def run():
        await h.tool.call({'command': first}, h.session, 1)
        later = time.monotonic() + 2
        monkeypatch.setattr(time, 'monotonic', lambda: later)
        await h.tool.call({'command': second}, h.session, 1)
        ctx = h.context(1)
        ctx.event.response_text = ''
        await h.listener.present(ctx)
        assert '重新查询' in ctx.event.reply[0]['text'] and '3301' not in ctx.event.reply[0]['text']
    asyncio.run(run())

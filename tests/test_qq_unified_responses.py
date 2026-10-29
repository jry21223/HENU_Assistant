"""Compare real menu/tool/reply interfaces with fixed school data."""
import asyncio
import copy
import pytest
import shlex
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from langbot_plugin.api.entities.builtin.platform.message import MessageChain, Plain
from langbot_plugin.api.entities.builtin.provider.session import Session, LauncherTypes


@pytest.fixture
def harness(monkeypatch):
    from components.cli_tools.henu_cli_safe import HenuCliSafe
    from components.cli_tools.henu_present import HenuPresent
    from components.event_listener.qq_cards import QQCardsListener
    variables, payloads = {}, {}
    class Proxy:
        def __init__(self, query_id=1, **kwargs): self.query_id = query_id
        async def get_bot_uuid(self): return 'bot'
        async def get_query_vars(self):
            return variables.setdefault(self.query_id, {'henu_sender_id': 'alice', 'henu_launcher_id': 'alice', 'henu_launcher_type': 'person'})
        async def set_query_var(self, key, value): (await self.get_query_vars())[key] = value
    for module in ['langbot_plugin.api.proxies.query_based_api', 'components.cli_tools.base', 'components.cli_tools.henu_present']:
        monkeypatch.setattr(module + '.QueryBasedAPIProxy', Proxy)
    def execute(tool_name, params, *args):
        result = copy.deepcopy(next(v for k, v in payloads.items() if shlex.split(k) == shlex.split(params['command'])))
        result.setdefault('cli', {})['command'] = params['command']
        return result
    plugin = SimpleNamespace(plugin_runtime_handler=Mock(), get_plugin_storage=AsyncMock(return_value=b'{}'), set_plugin_storage=AsyncMock(),
        service=SimpleNamespace(run_tool=Mock(side_effect=execute), run_tool_async=AsyncMock(side_effect=execute),
            get_runtime_context=lambda *args: {'server_time': {'now_iso': '2026-10-20T18:10:00+08:00'}}))
    def context(query_id, text='', sender='alice'):
        proxy = Proxy(query_id=query_id)
        event = SimpleNamespace(launcher_type='person', launcher_id=sender, sender_id=sender,
            message_chain=MessageChain(root=[Plain(text=text)]), response_text='模型不应重新排版课程', reply=None)
        return SimpleNamespace(query_id=query_id, event=event, get_bot_uuid=proxy.get_bot_uuid, get_query_vars=proxy.get_query_vars,
            set_query_var=proxy.set_query_var, reply=AsyncMock(), prevent_default=Mock(), prevent_postorder=Mock())
    tool = HenuCliSafe(); tool.plugin = plugin
    presenter = HenuPresent(); presenter.plugin = plugin
    listener = QQCardsListener.__new__(QQCardsListener); listener.plugin = plugin
    return SimpleNamespace(payloads=payloads, variables=variables, plugin=plugin, context=context, tool=tool, presenter=presenter, listener=listener,
        session=Session(launcher_type=LauncherTypes.PERSON, launcher_id='alice', sender_id='alice'))


def week_payload():
    return {'success': True, 'week_filter_applied': True, 'teaching_week': {'week': 8, 'week_start_date': '2026-10-19'},
            'schedule': {'星期一': [{'course': '数据结构', 'period': '第9-10节', 'location': '金明综合楼2402'}]},
            'cli': {'resolved_tool': 'schedule_query'}}


def test_week_schedule_uses_the_same_card_from_menu_and_agent(harness):
    from henu_plugin.qq_navigation import handle_menu_query
    async def run():
        h = harness; h.payloads['schedule week'] = week_payload()
        menu = h.context(1, '本周课表')
        assert await handle_menu_query(h.plugin, menu)
        direct = menu.reply.call_args.args[0].root[0].text
        result = await h.tool.call({'command': 'schedule week'}, h.session, 2)
        assert '摘要中的省略不影响卡片' in result['llm_hint']
        receipt = await h.presenter.call({'cards': [{'ref': result['display_ref']}]}, h.session, 2)
        assert receipt['success'], receipt
        natural = h.context(2)
        await h.listener.present(natural)
        text = natural.event.reply[0]['text']
        assert text == direct
        assert '本周课表' in text and '星期一' in text and '第9–10节' in text and '金明综合楼2402' in text
        assert '校园查询' not in text and '模型不应重新排版课程' not in text
    asyncio.run(run())


def test_library_menu_and_agent_share_layout_and_scope(harness, monkeypatch):
    from henu_plugin import library_availability as lib
    from datetime import datetime
    monkeypatch.setattr(lib, 'campus_now', lambda: datetime.fromisoformat('2026-10-21T10:00:00+08:00'))
    async def run():
        h = harness
        payload = {'success': True, 'source': 'live', 'date': '2026-10-21', 'locations': [
            {'area_id': '30', 'location': '701 七层南自习室', 'campus': '金明校区', 'floor': '七层', 'available_count': 12},
            {'area_id': '31', 'location': '702 七层北自习室', 'campus': '金明校区', 'floor': '七层', 'available_count': None},
            {'area_id': '46', 'location': '401 第一自习室', 'campus': '明伦校区', 'floor': '四层', 'available_count': 5}],
            'cli': {'resolved_tool': 'library_query'}}
        monkeypatch.setattr(lib, 'query_library', AsyncMock(return_value=copy.deepcopy(payload)))
        menu = h.context(1)
        assert await lib.handle_library_availability(h.plugin, menu, '图书馆座位')
        direct = menu.reply.call_args.args[0].root[0].text
        h.payloads['library locations --date 2026-10-21'] = payload
        result = await h.tool.call({'command': 'library locations --date 2026-10-21'}, h.session, 2)
        assert (await h.presenter.call({'cards': [{'ref': result['display_ref']}]}, h.session, 2))['success']
        natural = h.context(2); await h.listener.present(natural)
        assert natural.event.reply[0]['text'] == direct
        assert (await h.presenter.call({'cards': [{'ref': result['display_ref'], 'campus': '金明校区'}]}, h.session, 2))['success']
        natural = h.context(2); await h.listener.present(natural)
        text = natural.event.reply[0]['text']
        assert '701 七层南自习室' in text and '余座未知' in text and '剩余 12 座' in text
        assert '明伦' not in text and '401' not in text and '30' not in text
    asyncio.run(run())


@pytest.mark.parametrize('library_success', [True, False])
def test_agent_selects_final_results_and_keeps_explanation(harness, library_success):
    async def run():
        h = harness
        h.payloads['preferences get'] = {'success': True, 'preferences': {'library_location': '旧偏好区域'}, 'cli': {'resolved_tool': 'preferences'}}
        h.payloads['schedule week'] = week_payload()
        h.payloads['library locations'] = {'success': library_success, 'msg': '' if library_success else '图书馆暂时没查成功。',
            'locations': [{'location': '七层南自习室', 'campus': '金明校区', 'available_count': 12}] if library_success else [], 'cli': {'resolved_tool': 'library_query'}}
        await h.tool.call({'command': 'preferences get'}, h.session, 1)
        schedule = await h.tool.call({'command': 'schedule week'}, h.session, 1)
        library = await h.tool.call({'command': 'library locations'}, h.session, 1)
        note = '你打算几点到几点去？' if library_success else '课表已查到，图书馆这次没查成。'
        assert (await h.presenter.call({'cards': [{'ref': schedule['display_ref']}, {'ref': library['display_ref']}], 'note': note}, h.session, 1))['success']
        ctx = h.context(1); await h.listener.present(ctx)
        messages = [call.args[0].root[0].text for call in ctx.reply.call_args_list] + [ctx.event.reply[0]['text']]
        text = '\n'.join(messages)
        assert '本周课表' in text and '星期一' in text and '金明综合楼2402' in text
        assert ('剩余 12 座' if library_success else '图书馆暂时没查成功') in text
        assert note in text and '旧偏好区域' not in text
        assert len(messages) == 1 and '下一页' not in text
        assert '模型不应重新排版课程' not in text and schedule['display_ref'] not in text
    asyncio.run(run())


@pytest.mark.parametrize('invalid_kind', ['owner', 'query', 'unknown', 'expired'])
def test_presentation_rejects_foreign_or_expired_references(harness, monkeypatch, invalid_kind):
    async def run():
        h = harness; h.payloads['schedule week'] = week_payload()
        result = await h.tool.call({'command': 'schedule week'}, h.session, 1)
        session, query_id, ref = h.session, 1, result['display_ref']
        if invalid_kind == 'owner': session = Session(launcher_type=LauncherTypes.PERSON, launcher_id='bob', sender_id='bob')
        if invalid_kind == 'query': query_id = 2
        if invalid_kind == 'unknown': ref = 'not-a-result'
        if invalid_kind == 'expired':
            import time
            future = time.monotonic() + 601
            monkeypatch.setattr('henu_plugin.presentation.time.monotonic', lambda: future)
        receipt = await h.presenter.call({'cards': [{'ref': ref}]}, session, query_id)
        assert not receipt['success']
        assert not h.variables.get(query_id, {}).get('_henu_presentation')
    asyncio.run(run())


def test_note_only_asks_for_missing_conditions_without_a_school_query(harness):
    async def run():
        h = harness
        assert (await h.presenter.call({'cards': [], 'note': '你打算几点到几点去？'}, h.session, 1))['success']
        ctx = h.context(1); await h.listener.present(ctx)
        assert '你打算几点到几点去？' in ctx.event.reply[0]['text']
        assert '已完成' not in ctx.event.reply[0]['text'] and '查询完成' not in ctx.event.reply[0]['text']
        h.plugin.service.run_tool.assert_not_called()
        h.plugin.service.run_tool_async.assert_not_called()
    asyncio.run(run())


def test_prepared_presentation_expires_before_delivery(harness, monkeypatch):
    async def run():
        h = harness; h.payloads['schedule week'] = week_payload()
        result = await h.tool.call({'command': 'schedule week'}, h.session, 1)
        assert (await h.presenter.call({'cards': [{'ref': result['display_ref']}]}, h.session, 1))['success']
        import time
        future = time.monotonic() + 601
        monkeypatch.setattr('henu_plugin.presentation.time.monotonic', lambda: future)
        ctx = h.context(1); await h.listener.present(ctx)
        text = ctx.event.reply[0]['text'] if ctx.event.reply else ctx.event.response_text
        assert '金明综合楼2402' not in text
    asyncio.run(run())


def test_capacity_business_card_does_not_paginate_by_row_count(harness):
    async def run():
        h = harness
        h.payloads['schedule day --date 2026-10-21'] = {'success': True, 'date': '2026-10-21', 'week_filter_applied': True,
            'day_schedule': [{'course': f'课程{i}', 'period': '第1-2节', 'location': '十号楼101'} for i in range(12)],
            'cli': {'resolved_tool': 'schedule_query'}}
        result = await h.tool.call({'command': 'schedule day --date 2026-10-21'}, h.session, 1)
        assert (await h.presenter.call({'cards': [{'ref': result['display_ref']}]}, h.session, 1))['success']
        ctx = h.context(1); await h.listener.present(ctx)
        assert not ctx.reply.call_args_list
        text = ctx.event.reply[0]['text']
        assert '课程11' in text and '下一页' not in text and len(text.encode()) <= 3600
    asyncio.run(run())


@pytest.mark.parametrize('menu_text,payload,expected', [
    ('我的状态', {'success': True, 'account': {'student_id': '2026001234', 'has_password': True}, 'cli': {'resolved_tool': 'system_status'}}, ['我的状态', r'20\*\*\*\*34', '账号已保存']),
    ('今天课表', {'success': True, 'date': '2026-10-20', 'week_filter_applied': True, 'day_schedule': [{'course': '数据结构', 'period': '第9-10节', 'location': '金明综合楼2402'}], 'cli': {'resolved_tool': 'schedule_query'}}, ['我的课表', '第9–10节', '金明综合楼2402']),
    ('查金明校区综合楼第8周星期二第4大节的空教室', {'success': True, 'query': {'week': 8, 'dayOfWeek': 2, 'period': 4, 'campusName': '金明校区', 'buildingName': '综合楼1'}, 'rooms': [{'roomName': '金明综合楼1201', 'capacity': 242}], 'cli': {'resolved_tool': 'empty_classroom_query'}}, ['空教室', '第8周', '金明综合楼1201', '242座']),
])
def test_other_menu_entries_share_the_exact_business_layout(harness, menu_text, payload, expected):
    from henu_plugin.qq_navigation import handle_menu_query, menu_command
    async def run():
        h = harness; command = menu_command(menu_text); assert command
        h.payloads[command] = payload
        menu = h.context(1, menu_text); assert await handle_menu_query(h.plugin, menu)
        direct = menu.reply.call_args.args[0].root[0].text
        result = await h.tool.call({'command': command}, h.session, 2)
        assert (await h.presenter.call({'cards': [{'ref': result['display_ref']}]}, h.session, 2))['success']
        ctx = h.context(2); await h.listener.present(ctx)
        text = ctx.event.reply[0]['text']; assert text == direct
        assert all(word in text for word in expected)
        assert '2026001234' not in text
    asyncio.run(run())


def test_authoritative_confirmation_cannot_be_replaced_by_presentation(harness):
    async def run():
        h = harness; h.payloads['schedule week'] = week_payload()
        result = await h.tool.call({'command': 'schedule week'}, h.session, 1)
        exact = '# 河大助手 · 座位预约\n待本人确认 · 尚未提交\n[HENU_ACTIONS_V1:test]'
        h.variables[1]['_henu_card'] = {'owner': ['bot', 'person', 'alice', 'alice'], 'page': exact, 'authoritative': True}
        assert not (await h.presenter.call({'cards': [{'ref': result['display_ref']}], 'note': '我来确认'}, h.session, 1))['success']
        ctx = h.context(1); await h.listener.present(ctx)
        assert ctx.event.reply[0]['text'] == exact
    asyncio.run(run())


def test_new_read_invalidates_prepared_selection_and_agent_can_replace_it(harness):
    async def run():
        h = harness; h.payloads['schedule week'] = week_payload()
        h.payloads['library locations'] = {'success': True, 'date': '2026-10-21', 'locations': [
            {'location': '第一自习室', 'campus': '明伦校区', 'available_count': 5}], 'cli': {'resolved_tool': 'library_query'}}
        old = await h.tool.call({'command': 'schedule week'}, h.session, 1)
        assert (await h.presenter.call({'cards': [{'ref': old['display_ref']}]}, h.session, 1))['success']
        new = await h.tool.call({'command': 'library locations'}, h.session, 1)
        assert h.variables[1]['_henu_presentation'] is None
        assert (await h.presenter.call({'cards': [{'ref': new['display_ref'], 'campus': '明伦校区'}]}, h.session, 1))['success']
        ctx = h.context(1); await h.listener.present(ctx)
        assert '明伦校区' in ctx.event.reply[0]['text'] and '数据结构' not in ctx.event.reply[0]['text']
    asyncio.run(run())


def test_long_combination_keeps_all_first_screens_within_qq_limits(harness):
    async def run():
        h = harness
        h.payloads['schedule day --date 2026-10-21'] = {'success': True, 'date': '2026-10-21', 'week_filter_applied': True,
            'day_schedule': [{'course': f'课程{i}', 'period': '第1-2节', 'location': '十号楼101'} for i in range(70)], 'cli': {'resolved_tool': 'schedule_query'}}
        h.payloads['library locations'] = {'success': True, 'date': '2026-10-21', 'locations': [
            {'location': '七层南自习室', 'campus': '金明校区', 'available_count': 12}], 'cli': {'resolved_tool': 'library_query'}}
        schedule = await h.tool.call({'command': 'schedule day --date 2026-10-21'}, h.session, 1)
        library = await h.tool.call({'command': 'library locations'}, h.session, 1)
        assert (await h.presenter.call({'cards': [{'ref': schedule['display_ref']}, {'ref': library['display_ref']}], 'note': '说' * 1170}, h.session, 1))['success']
        ctx = h.context(1); await h.listener.present(ctx)
        messages = [call.args[0].root[0].text for call in ctx.reply.call_args_list] + [ctx.event.reply[0]['text']]
        assert len(messages) <= 5
        assert all(len(m.encode()) <= 3600 for m in messages)
        assert '剩余 12 座' in '\n'.join(messages) and '下一页' in '\n'.join(messages)
        paging = h.context(2, '下一页'); await h.listener.paging(paging)
        continuation = paging.reply.call_args.args[0].root[0].text
        assert '课程' in continuation and '余座' not in continuation
    asyncio.run(run())


def test_prepared_business_reply_uses_the_actual_sdk_reply_field(harness):
    from langbot_plugin.api.entities import events
    async def run():
        h = harness; h.payloads['schedule week'] = week_payload()
        result = await h.tool.call({'command': 'schedule week'}, h.session, 1)
        assert (await h.presenter.call({'cards': [{'ref': result['display_ref']}]}, h.session, 1))['success']
        ctx = h.context(1)
        ctx.event = events.NormalMessageResponded.model_construct(launcher_type='person', launcher_id='alice', sender_id='alice',
            response_text='已展示', finish_reason='stop', funcs_called=[], prefix='')
        await h.listener.present(ctx)
        field = 'reply_message_chain' if 'reply_message_chain' in type(ctx.event).model_fields else 'reply'
        wire = ctx.event.model_dump(mode='json', exclude_unset=True)[field]
        assert wire[0]['type'] == 'Plain' and wire[0]['text'].startswith('# 河大助手 · 本周课表')
        assert '数据结构' in wire[0]['text'] and '金明综合楼2402' in wire[0]['text']
    asyncio.run(run())


def test_invalid_replacement_does_not_reuse_an_earlier_display_plan(harness):
    async def run():
        h = harness; h.payloads['schedule week'] = week_payload()
        result = await h.tool.call({'command': 'schedule week'}, h.session, 1)
        assert (await h.presenter.call({'cards': [{'ref': result['display_ref']}]}, h.session, 1))['success']
        invalid = await h.presenter.call({'cards': [{'ref': 'unknown-ref'}]}, h.session, 1)
        assert not invalid['success']
        ctx = h.context(1); ctx.event.response_text = '这次结果没整理成功，请重新查询。'
        await h.listener.present(ctx)
        assert '金明综合楼2402' not in ctx.event.reply[0]['text']
        assert '这次结果没整理成功' in ctx.event.reply[0]['text']
    asyncio.run(run())


def test_unavailable_seat_attributes_do_not_mark_success_as_failure(harness):
    async def run():
        h = harness
        assert (await h.presenter.call({'cards': [], 'note': '数据没有标注靠窗位置，无法确认哪些座位靠窗。'}, h.session, 1))['success']
        ctx = h.context(1); await h.listener.present(ctx)
        assert '无法确认哪些座位靠窗' in ctx.event.reply[0]['text']
        assert '未完成' not in ctx.event.reply[0]['text']
    asyncio.run(run())


def test_committed_receipt_clears_an_earlier_read_plan_once(harness):
    from henu_plugin.qq_cards import stage_card
    async def run():
        h = harness; h.payloads['schedule week'] = week_payload()
        result = await h.tool.call({'command': 'schedule week'}, h.session, 1)
        assert (await h.presenter.call({'cards': [{'ref': result['display_ref']}]}, h.session, 1))['success']
        await stage_card(h.plugin, h.session, 1, {'success': False, 'external_committed': True, 'storage_persisted': False, '_resolved_tool_name': 'library_reserve'})
        ctx = h.context(1); await h.listener.present(ctx)
        assert '不要重复提交' in ctx.event.reply[0]['text']
        again = h.context(1); await h.listener.present(again)
        assert again.event.reply is None
    asyncio.run(run())


def seat_payload():
    return {'success': True, 'area': {'id': '30', 'name': '701 七层南自习室'}, 'target_date': '2026-10-21', 'time_window': '08:00-23:00',
            'available_count': 1, 'total_count': 2, 'seats': [{'no': '002', 'status': '1'}], 'cli': {'resolved_tool': 'library_query'},
            '_qq_seat_map': [
                {'no': '001', 'point_x': 0.26, 'point_y': 81.25, 'width': 1.875, 'height': 3.365, 'status': '2', 'status_name': '已预约'},
                {'no': '002', 'point_x': 0.20, 'point_y': 77.50, 'width': 1.875, 'height': 3.65, 'status': '1', 'status_name': '空闲'}]}


def test_seat_queries_send_only_the_hd_map_and_keep_selection_available(harness, monkeypatch):
    from langbot_plugin.api.entities.builtin.platform.message import Image
    async def run():
        h = harness
        command = 'library seats --area-id 30 --date 2026-10-21 --time 08:00 --end-time 23:00'
        h.payloads[command] = seat_payload()
        h.payloads['library locations'] = {'success': True, 'source': 'live', 'locations': [
            {'area_id': '30', 'location': '701 七层南自习室', 'campus': '金明校区'}], 'cli': {'resolved_tool': 'library_query'}}
        await h.tool.call({'command': 'library locations'}, h.session, 1)
        result = await h.tool.call({'command': command}, h.session, 1)
        assert '_qq_seat_map' not in result
        assert (await h.presenter.call({'cards': [{'ref': result['display_ref']}],
                                       'note': '无法生成座位图，这是模型的额外说明。'}, h.session, 1))['success']
        ctx = h.context(1); await h.listener.present(ctx)
        images = [node for call in ctx.reply.call_args_list for node in call.args[0].root if isinstance(node, Image)]
        assert len(images) == 1
        import base64, io
        from PIL import Image as PILImage
        assert PILImage.open(io.BytesIO(base64.b64decode(images[0].base64))).size == (4096, 2560)
        assert ctx.event.reply == []
        assert len(ctx.reply.call_args_list) == 1
        from henu_plugin import library_seat_map as maps
        unavailable = h.context(3, '999')
        assert await maps.handle_library_text(h.plugin, unavailable, '999')
        assert '暂不可选' in unavailable.reply.call_args.args[0].root[0].text
        direct_result = {'success': True, 'seats': ['002'], 'reserved_count': 1, 'image': images[0].base64}
        monkeypatch.setattr(maps, 'query_seats', AsyncMock(return_value=direct_result))
        state = {'area': {'area_id': '30', 'location': '701 七层南自习室', 'campus': '金明校区'}, 'expires': 10**12}
        maps.states(h.plugin)[('bot', 'person', 'alice', 'alice')] = state
        menu = h.context(2)
        await maps.show_map(h.plugin, menu, state, ('2026-10-21', '08:00', '23:00'))
        assert len(menu.reply.call_args_list) == 1
        assert isinstance(menu.reply.call_args.args[0].root[0], Image)
    asyncio.run(run())


def test_ordinary_image_completion_cannot_overwrite_committed_receipt(harness, monkeypatch):
    from PIL import Image as PILImage
    from henu_plugin.qq_cards import stage_card
    import threading
    entered, release = threading.Event(), threading.Event()
    save = PILImage.Image.save
    def slow_save(image, *args, **kwargs):
        entered.set()
        assert release.wait(5)
        return save(image, *args, **kwargs)
    monkeypatch.setattr(PILImage.Image, 'save', slow_save)
    async def run():
        h = harness
        command = 'library seats --area-id 30 --date 2026-10-21 --time 08:00 --end-time 23:00'
        h.payloads[command] = seat_payload()
        read = asyncio.create_task(h.tool.call({'command': command}, h.session, 1))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            await stage_card(h.plugin, h.session, 1, {'success': False, 'external_committed': True, 'storage_persisted': False, '_resolved_tool_name': 'library_reserve'})
        finally:
            release.set()
        await read
        ctx = h.context(1); await h.listener.present(ctx)
        assert '不要重复提交' in ctx.event.reply[0]['text']
    asyncio.run(run())


def test_single_time_seat_image_replaces_old_paging_without_listing_seats(harness):
    async def run():
        h = harness
        h.payloads['schedule week'] = week_payload()
        schedule = await h.tool.call({'command': 'schedule week'}, h.session, 1)
        await h.presenter.call({'cards': [{'ref': schedule['display_ref']}]}, h.session, 1)
        await h.listener.present(h.context(1))
        command = 'library seats --area-id 30 --date 2026-10-21 --time 08:00'
        h.payloads[command] = {**seat_payload(), 'time_window': '08:00',
                               'seats': [{'no': str(i).zfill(3), 'status': '1'} for i in range(101)]}
        result = await h.tool.call({'command': command}, h.session, 2)
        await h.presenter.call({'cards': [{'ref': result['display_ref']}]}, h.session, 2)
        ctx = h.context(2)
        await h.listener.present(ctx)
        assert len(ctx.reply.call_args_list) == 1 and ctx.event.reply == []
        paging = h.context(3, '卡片第1页')
        assert await h.listener.paging(paging)
        text = paging.reply.call_args.args[0].root[0].text
        assert '卡片已过期' in text and '数据结构' not in text and '空闲座位' not in text
    asyncio.run(run())


def test_seat_image_keeps_other_requested_results_and_notes(harness):
    async def run():
        h = harness
        command = 'library seats --area-id 30 --date 2026-10-21 --time 08:00 --end-time 23:00'
        h.payloads[command] = seat_payload()
        h.payloads['schedule week'] = week_payload()
        seat = await h.tool.call({'command': command}, h.session, 1)
        schedule = await h.tool.call({'command': 'schedule week'}, h.session, 1)
        await h.presenter.call({'cards': [{'ref': seat['display_ref']}, {'ref': schedule['display_ref']}],
                                'note': '这是你同时查询的课表。'}, h.session, 1)
        ctx = h.context(1)
        await h.listener.present(ctx)
        text = ctx.event.reply[0]['text']
        assert len(ctx.reply.call_args_list) == 1
        assert '数据结构' in text and '这是你同时查询的课表' in text
        assert '空闲座位' not in text and '选择座位' not in text
    asyncio.run(run())


def test_refresh_seat_image_clears_previous_schedule_paging(harness, monkeypatch):
    from henu_plugin import library_seat_map as maps
    async def run():
        h = harness
        command = 'library seats --area-id 30 --date 2026-10-21 --time 08:00 --end-time 23:00'
        h.payloads[command] = seat_payload()
        seat = await h.tool.call({'command': command}, h.session, 1)
        await h.presenter.call({'cards': [{'ref': seat['display_ref']}]}, h.session, 1)
        first = h.context(1)
        await h.listener.present(first)
        image = first.reply.call_args.args[0].root[0].base64
        h.payloads['schedule week'] = week_payload()
        schedule = await h.tool.call({'command': 'schedule week'}, h.session, 2)
        await h.presenter.call({'cards': [{'ref': schedule['display_ref']}]}, h.session, 2)
        await h.listener.present(h.context(2))
        monkeypatch.setattr(maps, 'query_seats', AsyncMock(return_value={
            'success': True, 'seats': ['002'], 'image': image}))
        refresh = h.context(3, '刷新座位')
        assert await maps.handle_library_text(h.plugin, refresh, '刷新座位')
        assert len(refresh.reply.call_args_list) == 1
        paging = h.context(4, '卡片第1页')
        assert await h.listener.paging(paging)
        text = paging.reply.call_args.args[0].root[0].text
        assert '卡片已过期' in text and '数据结构' not in text
    asyncio.run(run())


def test_new_seat_choice_survives_an_older_image_delivery(harness):
    from langbot_plugin.api.entities.builtin.platform.message import Image
    from henu_plugin import library_seat_map as maps
    async def run():
        h = harness
        old_cmd = 'library seats --area-id 30 --date 2026-10-21 --time 08:00 --end-time 23:00'
        new_cmd = 'library seats --area-id 46 --date 2026-10-22 --time 14:00 --end-time 16:00'
        h.payloads[old_cmd] = seat_payload()
        h.payloads[new_cmd] = {'success': True, 'area': {'id': '46', 'name': '第一自习室'}, 'target_date': '2026-10-22', 'time_window': '14:00-16:00',
            'seats': [{'no': '011', 'status': '1'}], 'cli': {'resolved_tool': 'library_query'}}
        old = await h.tool.call({'command': old_cmd}, h.session, 1)
        assert (await h.presenter.call({'cards': [{'ref': old['display_ref']}]}, h.session, 1))['success']
        ctx = h.context(1)
        async def reply(chain):
            if isinstance(chain.root[0], Image):
                new = await h.tool.call({'command': new_cmd}, h.session, 2)
                assert (await h.presenter.call({'cards': [{'ref': new['display_ref']}]}, h.session, 2))['success']
                await h.listener.present(h.context(2))
        ctx.reply.side_effect = reply
        await h.listener.present(ctx)
        state = maps.states(h.plugin)[('bot', 'person', 'alice', 'alice')]
        assert state['area']['area_id'] == '46' and state['window'] == ('2026-10-22', '14:00', '16:00')
        assert '旧' in ctx.event.reply[0]['text'] or '已更新' in ctx.event.reply[0]['text']
    asyncio.run(run())


def test_failed_image_uses_the_shared_text_fallback_without_resending(harness):
    from langbot_plugin.api.entities.builtin.platform.message import Image
    async def run():
        h = harness
        command = 'library seats --area-id 30 --date 2026-10-21 --time 08:00 --end-time 23:00'
        h.payloads[command] = seat_payload()
        result = await h.tool.call({'command': command}, h.session, 1)
        assert (await h.presenter.call({'cards': [{'ref': result['display_ref']}]}, h.session, 1))['success']
        ctx = h.context(1)
        async def reply(chain):
            if isinstance(chain.root[0], Image): raise ConnectionError('unknown delivery')
        ctx.reply.side_effect = reply
        await h.listener.present(ctx)
        text = ctx.event.reply[0]['text']
        assert '座位图没能显示' in text and '空闲座位：002' in text and '高清图' not in text
        assert len(ctx.reply.call_args_list) == 1
    asyncio.run(run())


def test_combination_over_twenty_pages_is_explicitly_rejected(harness):
    async def run():
        h = harness
        for day in ['2026-10-21', '2026-10-22']:
            h.payloads['schedule day --date '+day] = {'success': True, 'date': day, 'week_filter_applied': True,
                'day_schedule': [{'course': '课程'+str(i)+'字'*90, 'period': '第1-2节', 'location': '十号楼101'} for i in range(160)], 'cli': {'resolved_tool': 'schedule_query'}}
        first = await h.tool.call({'command': 'schedule day --date 2026-10-21'}, h.session, 1)
        second = await h.tool.call({'command': 'schedule day --date 2026-10-22'}, h.session, 1)
        receipt = await h.presenter.call({'cards': [{'ref': first['display_ref']}, {'ref': second['display_ref']}]}, h.session, 1)
        assert not receipt['success'] and receipt['error_code'] == 'too_many_pages'
        assert '20' in receipt['llm_hint'] and not h.variables[1].get('_henu_presentation')
    asyncio.run(run())


def test_query_started_before_a_new_choice_cannot_adopt_its_generation(harness, monkeypatch):
    from PIL import Image as PILImage
    from henu_plugin import library_seat_map as maps
    import threading
    entered, release = threading.Event(), threading.Event()
    save = PILImage.Image.save
    def slow_save(image, *args, **kwargs):
        entered.set(); assert release.wait(5)
        return save(image, *args, **kwargs)
    monkeypatch.setattr(PILImage.Image, 'save', slow_save)
    async def run():
        h = harness
        old_cmd = 'library seats --area-id 30 --date 2026-10-21 --time 08:00 --end-time 23:00'
        new_cmd = 'library seats --area-id 46 --date 2026-10-22 --time 14:00 --end-time 16:00'
        h.payloads[old_cmd] = seat_payload()
        h.payloads[new_cmd] = {'success': True, 'area': {'id': '46', 'name': '第一自习室'}, 'target_date': '2026-10-22', 'time_window': '14:00-16:00',
            'seats': [{'no': '011', 'status': '1'}], 'cli': {'resolved_tool': 'library_query'}}
        old_task = asyncio.create_task(h.tool.call({'command': old_cmd}, h.session, 1))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            new = await h.tool.call({'command': new_cmd}, h.session, 2)
            assert (await h.presenter.call({'cards': [{'ref': new['display_ref']}]}, h.session, 2))['success']
            await h.listener.present(h.context(2))
        finally:
            release.set()
        old = await old_task
        receipt = await h.presenter.call({'cards': [{'ref': old['display_ref']}]}, h.session, 1)
        assert not receipt['success'] and receipt['error_code'] == 'request_superseded'
        state = maps.states(h.plugin)[('bot', 'person', 'alice', 'alice')]
        assert state['area']['area_id'] == '46' and state['window'] == ('2026-10-22', '14:00', '16:00')
    asyncio.run(run())


def test_runtime_restart_during_a_prepared_map_has_an_explicit_fallback(harness):
    async def run():
        h = harness
        command = 'library seats --area-id 30 --date 2026-10-21 --time 08:00 --end-time 23:00'
        h.payloads[command] = seat_payload()
        result = await h.tool.call({'command': command}, h.session, 1)
        assert (await h.presenter.call({'cards': [{'ref': result['display_ref']}]}, h.session, 1))['success']
        h.listener.plugin = SimpleNamespace(plugin_runtime_handler=h.plugin.plugin_runtime_handler)
        ctx = h.context(1); ctx.event.response_text = '已展示。'
        await h.listener.present(ctx)
        assert ctx.event.reply and '已过期' in ctx.event.reply[0]['text']
        assert '已展示' not in ctx.event.reply[0]['text']
    asyncio.run(run())

from henu_plugin.qq_navigation import navigation_config


def test_menu_and_panels_offer_user_tasks_without_executable_writes():
    config = navigation_config()
    items = config['menu']['items']
    assert [item['name'] for item in items] == ['我的校园', '找自习室', '账号与帮助']
    assert any(sub.get('send_message') == '今天课表' for sub in items[0]['sub_menu_items'])
    assert any(sub.get('send_message') == '绑定账号' for sub in items[2]['sub_menu_items'])
    for scope in ('c2c', 'group'):
        panel = config['panels'][scope]
        assert panel['target_type'] == 'all'
        assert len(panel['panel']['items']) <= 20
        assert all(len(i['name'].encode('utf-16-le')) // 2 <= 14 for i in panel['panel']['items'])
    assert '确认' not in str(config)


def test_primary_sdk_listener_handles_help_guide_before_model(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from langbot_plugin.api.entities import events
    from langbot_plugin.api.entities.builtin.platform.message import MessageChain, Plain
    from components.event_listener.identity_capture_safe import SafeIdentityCaptureListener
    from unittest.mock import AsyncMock

    async def run():
        listener = SafeIdentityCaptureListener()
        listener.plugin = SimpleNamespace()
        await listener.initialize()
        ctx = SimpleNamespace(event=SimpleNamespace(text_message='使用帮助', message_chain=MessageChain(root=[Plain(text='使用帮助')]), launcher_type='person'), reply=AsyncMock(), is_prevent_postorder=False, query_id=1)
        ctx.prevent_default = lambda: None
        ctx.prevent_postorder = lambda: setattr(ctx, 'is_prevent_postorder', True)
        # The production SDK dispatches the first component; exercise its actual handler.
        await listener.registered_handlers[events.PersonMessageReceived][-1](ctx)
        sent = str(ctx.reply.call_args)
        assert '首次使用' in sent and '绑定Kit' in sent
        assert ctx.is_prevent_postorder
    asyncio.run(run())


def test_schedule_card_uses_chinese_sections_and_shows_each_course_once():
    from henu_plugin.qq_cards import result_card
    course = {'course': '高等数学', 'period': '第1-2节', 'location': '十号楼101'}
    result = {'success': True, 'current_courses': [course], 'next_course': None, 'day_schedule': [course], 'week_filter_applied': False}
    page = '\n'.join(result_card(result, 'schedule_query').pages())
    assert page.count('高等数学') == 1
    assert 'current_courses' not in page and 'day_schedule' not in page
    assert '供核对' in page and '未按教学周过滤' in page


def test_status_does_not_treat_saved_credentials_as_verified_login():
    from henu_plugin.qq_cards import result_card
    page = result_card({'success': True, 'account': {'account': {'student_id': '2026001234', 'has_password': True}}}, 'system_status').pages()[0]
    assert '不代表当前登录有效' in page
    assert '2026001234' not in page


def test_preferences_card_retains_production_defaults():
    from henu_plugin.qq_cards import result_card
    page = result_card({'success': True, 'preferences': {'library_location': '金明馆', 'library_seat_no': 'A101'}}, 'preferences').pages()[0]
    assert '金明馆' in page and 'A101' in page


def test_navigation_defers_to_existing_production_credential_intake():
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from langbot_plugin.api.entities.builtin.platform.message import MessageChain, Plain
    from henu_plugin.qq_navigation import handle_navigation
    ctx = SimpleNamespace(event=SimpleNamespace(message_chain=MessageChain(root=[Plain(text='绑定账号')])) , reply=AsyncMock())
    listener = SimpleNamespace(_arm_credential_intake=AsyncMock())
    assert asyncio.run(handle_navigation(SimpleNamespace(), ctx, listener=listener)) is False
    ctx.reply.assert_not_called()


def test_normal_message_stage_handles_help_when_earlier_listener_owns_received_event():
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from langbot_plugin.api.entities import events
    from components.event_listener.identity_capture_safe import SafeIdentityCaptureListener
    async def run():
        listener=SafeIdentityCaptureListener();listener.plugin=SimpleNamespace()
        await listener.initialize()
        from langbot_plugin.api.entities.builtin.platform import events as pe, entities, message as pm
        chain=pm.MessageChain(root=[pm.Plain(text='使用帮助')])
        incoming=pe.FriendMessage(sender=entities.Friend(id='alice',nickname='合成',remark=''),message_chain=chain)
        from langbot_plugin.api.entities.builtin.pipeline.query import Query
        original=Query.model_construct(message_chain=chain,message_event=incoming)
        ctx=SimpleNamespace(event=events.PersonNormalMessageReceived(query=original,launcher_type='person',launcher_id='alice',sender_id='alice',text_message='使用帮助',message_event=incoming,message_chain=chain),query_id=3,reply=AsyncMock(),is_prevent_postorder=False)
        ctx.prevent_default=lambda:None
        ctx.prevent_postorder=lambda:setattr(ctx,'is_prevent_postorder',True)
        await listener.registered_handlers[events.PersonNormalMessageReceived][-1](ctx)
        assert '从这里开始' in str(ctx.reply.call_args)
        assert ctx.is_prevent_postorder
    asyncio.run(run())


def test_normal_stage_does_not_swallow_help_with_original_attachment():
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from langbot_plugin.api.entities.builtin.platform.message import MessageChain, Plain, Image
    from henu_plugin.qq_navigation import handle_navigation
    chain=MessageChain(root=[Plain(text='使用帮助'),Image(url='https://synthetic.invalid/image.png')])
    ctx=SimpleNamespace(event=SimpleNamespace(text_message='使用帮助',query=SimpleNamespace(message_chain=chain)),reply=AsyncMock())
    assert asyncio.run(handle_navigation(SimpleNamespace(),ctx)) is False
    ctx.reply.assert_not_called()


def test_static_binding_template_is_usable_without_any_real_credentials():
    from henu_plugin.qq_navigation import account_binding_card
    page=account_binding_card()[0]
    assert "account set --student-id 学号 --password '密码'" in page
    assert 'redacted' not in page and '分别绑定' in page and '取消' in page


def test_study_guidance_does_not_claim_an_operation_is_pending():
    from henu_plugin.qq_navigation import GUIDES
    from henu_plugin.qq_cards import text_card
    page=text_card(*GUIDES['找自习室'])[0]
    assert '待本人核对' not in page and '尚未提交' not in page


def test_menu_day_query_uses_campus_date_and_year_rollover():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from henu_plugin.qq_navigation import menu_command
    clock=datetime(2026,12,31,23,59,tzinfo=ZoneInfo('Asia/Shanghai'))
    assert menu_command('明天课表',clock)=='schedule day --date 2027-01-01'
    assert menu_command('今天课表',clock)=='schedule day --date 2026-12-31'


def test_exact_menu_query_uses_existing_cli_and_staged_card(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from langbot_plugin.api.entities.builtin.platform.message import MessageChain, Plain
    from components.cli_tools.henu_cli_safe import HenuCliSafe
    from henu_plugin.qq_navigation import handle_menu_query
    call=AsyncMock(return_value={'success':True})
    monkeypatch.setattr(HenuCliSafe,'call',call)
    event=SimpleNamespace(launcher_type='person',launcher_id='alice',sender_id='alice',message_chain=MessageChain(root=[Plain(text='我的状态')]))
    ctx=SimpleNamespace(event=event,query_id=9,reply=AsyncMock(),is_prevent_postorder=False,get_query_vars=AsyncMock(return_value={'_henu_card':{'page':'# 河大助手 · 合成状态'}}),set_query_var=AsyncMock())
    ctx.prevent_default=lambda:None
    ctx.prevent_postorder=lambda:setattr(ctx,'is_prevent_postorder',True)
    assert asyncio.run(handle_menu_query(SimpleNamespace(),ctx))
    assert call.call_args.args[0]=={'command':'account status'}
    assert '合成状态' in str(ctx.reply.call_args)


def test_week_schedule_exact_command():
    from henu_plugin.qq_navigation import menu_command
    assert menu_command('本周课表') == 'schedule week'

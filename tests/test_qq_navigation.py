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
    assert '正在上课' in page and '未按教学周过滤' in page


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

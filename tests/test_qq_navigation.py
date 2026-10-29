from henu_plugin.qq_navigation import navigation_config


def test_leave_situation_question_routes_existing_readonly_cli_without_model():
    from henu_plugin.qq_navigation import menu_command
    from henu_plugin.cli import inspect_cli_command
    command = menu_command('查询一下请假情况')
    spec = inspect_cli_command(command)
    assert spec.resolved_tool == 'yunfz_leave_query' and not spec.is_help
    assert spec.params['view'] == 'list'


def test_leave_queries_accept_clear_personal_reads_without_swallowing_other_intents():
    from henu_plugin.qq_navigation import menu_command
    for text in ('我的请假记录', '查询请假情况', '帮我查一下我的请假记录。', '查看请假审批状态？'):
        assert menu_command(text) == 'yunfz leave list'
    for text in ('帮我申请请假', '怎么请假', '查询张三的请假记录', '查询其他人的请假情况',
                 '查询一下请假情况和今天课表', '我的请假情况\n帮我提交申请'):
        assert not menu_command(text)


def test_menu_and_panels_offer_user_tasks_without_executable_writes():
    config = navigation_config()
    items = config['menu']['items']
    assert [item['name'] for item in items] == ['🎓我的校园', '📍找自习室', '⚙️账号帮助']
    assert any(sub.get('send_message') == '今天课表' for sub in items[0]['sub_menu_items'])
    assert any(sub.get('send_message') == '绑定账号' for sub in items[2]['sub_menu_items'])
    for scope in ('c2c', 'group'):
        panel = config['panels'][scope]
        assert panel['target_type'] == 'all'
        assert len(panel['panel']['items']) <= 20
        assert all(len(i['name'].encode('utf-16-le')) // 2 <= 14 for i in panel['panel']['items'])
    assert '确认' not in str(config)


def test_shortcut_icons_keep_commands_and_provider_name_limits():
    config = navigation_config()
    for item in config['menu']['items']:
        assert len(item['name'].encode('utf-16-le')) // 2 <= 10
        for sub in item['sub_menu_items']:
            assert len(sub['name'].encode('utf-16-le')) // 2 <= 14
            assert sub['name'].endswith(sub['send_message'])
            assert sub['name'] != sub['send_message']
    for panel in config['panels'].values():
        for command in panel['panel']['items']:
            assert command['name'] in {'我的状态', '今天课表', '明天课表', '下一节课', '同步课表',
                                       '找自习室', '空教室查询', '图书馆座位', '绑定账号',
                                       '绑定Kit', '使用帮助', '课表推送', '河宝社区', '自动预约帮助'}
            assert len(command['desc'].encode('utf-16-le')) // 2 <= 30


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
    assert '查询时会检查登录' in page
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


def test_study_entries_cannot_fall_back_to_static_how_to_guides():
    from henu_plugin.qq_navigation import GUIDES
    assert '找自习室' not in GUIDES
    assert '空教室查询' not in GUIDES
    assert '尚未提供定时课表推送' not in str(GUIDES)


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


def test_complete_classroom_question_routes_existing_cli_without_model():
    from henu_plugin.qq_navigation import menu_command
    from henu_plugin.cli import inspect_cli_command
    command = menu_command('查金明校区十号楼第8周星期二第2大节的空教室')
    spec = inspect_cli_command(command)
    assert spec.resolved_tool == 'empty_classroom_query' and not spec.is_help
    assert spec.params['campus_text'] == '金明'
    assert spec.params['building_text'] == '十号楼'
    assert (spec.params['week'], spec.params['day_of_week'], spec.params['period']) == (8, 2, 2)
    assert not menu_command('查金明校区十号楼第0周星期二第2大节的空教室')
    assert not menu_command('查金明校区十号楼第8周星期二第99大节的空教室')


def test_library_query_sends_wait_feedback_before_external_lookup(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from langbot_plugin.api.entities.builtin.platform.message import MessageChain, Plain
    from henu_plugin import library_availability
    from henu_plugin.qq_navigation import handle_menu_query
    timeline = []
    async def reply(chain): timeline.append(str(chain))
    async def call(*args, **kwargs):
        assert timeline and '正在查询' in timeline[0]
        return {'success': True, '_resolved_tool_name': 'library_query', 'locations': []}
    monkeypatch.setattr(library_availability, 'query_library', call)
    ctx = SimpleNamespace(event=SimpleNamespace(launcher_type='person', launcher_id='alice', sender_id='alice', message_chain=MessageChain(root=[Plain(text='图书馆座位')])), query_id=1, is_prevent_postorder=False, reply=reply, get_bot_uuid=AsyncMock(return_value='bot'), get_query_vars=AsyncMock(return_value={}), set_query_var=AsyncMock())
    ctx.prevent_default = lambda: None
    ctx.prevent_postorder = lambda: setattr(ctx, 'is_prevent_postorder', True)
    assert asyncio.run(handle_menu_query(SimpleNamespace(), ctx))
    assert len(timeline) == 2


def test_public_group_classroom_query_keeps_group_session_and_private_queries_block(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from langbot_plugin.api.entities.builtin.platform.message import MessageChain, Plain
    from components.cli_tools.henu_cli_safe import HenuCliSafe
    from henu_plugin.qq_navigation import handle_menu_query
    call = AsyncMock(return_value={'success': True, '_resolved_tool_name': 'empty_classroom_query', 'msg': '查询完成'})
    monkeypatch.setattr(HenuCliSafe, 'call', call)
    def ctx(text):
        c = SimpleNamespace(event=SimpleNamespace(launcher_type='group', launcher_id='group1', sender_id='alice', message_chain=MessageChain(root=[Plain(text=text)])), query_id=1, reply=AsyncMock(), is_prevent_postorder=False, get_query_vars=AsyncMock(return_value={}), set_query_var=AsyncMock())
        c.prevent_default = lambda: None
        c.prevent_postorder = lambda: setattr(c, 'is_prevent_postorder', True)
        return c
    async def run():
        public = ctx('查金明校区十号楼第8周星期二第2大节的空教室')
        assert await handle_menu_query(SimpleNamespace(), public)
        call.assert_awaited_once()
        session = call.call_args.args[1]
        assert str(getattr(session.launcher_type, 'value', session.launcher_type)) == 'group'
        assert session.launcher_id == 'group1' and session.sender_id == 'alice'
        call.reset_mock()
        private = ctx('我的状态')
        assert await handle_menu_query(SimpleNamespace(), private)
        call.assert_not_awaited()
        assert '私聊' in str(private.reply.call_args)
    asyncio.run(run())


def test_short_classroom_guide_keeps_missing_filters_for_service_to_resolve():
    from henu_plugin.qq_navigation import menu_command
    from henu_plugin.cli import inspect_cli_command
    spec = inspect_cli_command(menu_command('查金明校区十号楼的空教室'))
    assert spec.resolved_tool == 'empty_classroom_query'
    assert spec.params['week'] == 0 and spec.params['period'] == 0
    assert spec.params['view'] == 'day_matrix'
    assert spec.params['campus_text'] == '金明' and spec.params['building_text'] == '十号楼'


def test_classroom_question_rejects_nonexistent_sixth_large_period():
    from henu_plugin.qq_navigation import menu_command
    assert not menu_command('查金明校区十号楼第6大节的空教室')


def test_hebao_queries_use_read_only_cli_commands():
    from henu_plugin.qq_navigation import menu_command
    from henu_plugin.cli import inspect_cli_command
    expected = {'河宝状态':'yunfz signin statistics', '河宝请假记录':'yunfz leave list',
                '河宝签到任务':'yunfz signin list', '河宝查寝任务':'yunfz checksleep list',
                '河宝活动':'yunfz activity list', '河宝信息收集':'yunfz collection list'}
    for text, command in expected.items():
        assert menu_command(text) == command
        assert inspect_cli_command(command).resolved_tool.startswith('yunfz_')

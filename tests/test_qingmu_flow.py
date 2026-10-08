import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from henu_plugin.qingmu_flow import QingmuCoordinator
from henu_plugin.confirmation_shortcut import ConfirmationShortcut


class Plugin:
    def __init__(self):
        self.storage = {}
    async def get_plugin_storage_keys(self):
        return list(self.storage)
    async def get_plugin_storage(self, key):
        return self.storage[key]
    async def set_plugin_storage(self, key, value):
        self.storage[key] = value


def ctx(command, user='user-a', query=1, launcher=None, kind='person'):
    return SimpleNamespace(query_id=query, event=SimpleNamespace(text_message=command,
        sender_id=user, launcher_id=launcher or user, launcher_type=kind),
        reply=AsyncMock(), prevent_default=Mock(), prevent_postorder=Mock())


BIND = "qingmu account set --platform ucampus --account student-a --password 'StudentSecret!' --school 河南大学"


def test_private_binding_is_encrypted_confirmed_and_never_crosses_user_or_chat():
    async def run():
        plugin = Plugin()
        listener = SimpleNamespace(plugin=plugin)
        flow = QingmuCoordinator(plugin)
        listener._qingmu = flow
        start = ctx(BIND)
        assert await flow.handle(start, is_group=False)
        await flow.tasks[1]
        start.prevent_default.assert_called()
        start.prevent_postorder.assert_called()
        assert all(b'StudentSecret!' not in raw and b'student-a' not in raw for raw in plugin.storage.values())
        assert not (await flow.execute('qingmu status', 'user-a', {'launcher_type':'person','launcher_id':'user-a'}, 9))['platforms'][0]['bound']
        wrong = ctx('确认青木操作', query=2, user='user-b')
        await ConfirmationShortcut(listener).handle(wrong)
        other_chat = ctx('确认青木操作', query=3, launcher='other-chat')
        await ConfirmationShortcut(listener).handle(other_chat)
        confirm = ctx('确认', query=4)
        assert await ConfirmationShortcut(listener).handle(confirm)
        await flow.tasks[4]
        status = await flow.execute('qingmu status', 'user-a', {'launcher_type':'person','launcher_id':'user-a'}, 5)
        assert status['platforms'][0]['bound']
        assert 'StudentSecret!' not in str(status) and 'student-a' not in str(status)
        group = ctx(BIND, user='user-c', kind='group', query=6)
        assert await flow.handle(group, is_group=True)
        assert len(plugin.storage) == 1
    asyncio.run(run())


def test_order_preview_confirmation_receipt_progress_and_unknown_outcome_fence(monkeypatch):
    from henu_plugin.qingmu_client import QingmuClient, Settings, QingmuError
    settings = Settings('https://supplier.test.invalid', 'vendor-user', 'vendor-secret', 'form',
        {'ucampus': {'id': 'service-77', 'name': 'U校园整本（测试服务）', 'billing': '测试计费规则，确认后按供应商实际结算'}},
        {'get_success_code': '1', 'course_id_field': 'id', 'course_name_field': 'name',
         'order_id_field': 'id', 'retry_success_code': '1'}, True)
    monkeypatch.setattr('henu_plugin.qingmu_flow.settings_from_env', lambda: settings)
    monkeypatch.setattr(QingmuClient, 'courses', lambda *_: [{'kcid':'course-22','name':'大学英语'}])
    add = Mock(return_value='supplier-order-88')
    retry = Mock()
    monkeypatch.setattr(QingmuClient, 'place_order', add)
    monkeypatch.setattr(QingmuClient, 'retry', retry)
    monkeypatch.setattr(QingmuClient, 'progress', lambda *_: {'status':'进行中','process':'25%'})
    async def run(unknown):
        plugin = Plugin()
        flow = QingmuCoordinator(plugin)
        conversation = {'launcher_type':'person','launcher_id':'user-a'}
        async def confirm(query):
            p = await flow.valid_pending('user-a', conversation, query)
            return await flow.execute('qingmu confirm', 'user-a', conversation, query, confirmation_token=p['token'])
        await flow.execute(BIND, 'user-a', conversation, 1)
        await confirm(2)
        courses = await flow.execute('qingmu courses --platform ucampus', 'user-a', conversation, 3)
        assert courses['courses'] == [{'no':1,'name':'大学英语'}]
        preview = await flow.execute('qingmu order add --platform ucampus --course 1', 'user-a', conversation, 4)
        assert preview['confirmation_required'] and '付费' in preview['reply_text']
        assert '服务：U校园整本（测试服务）' in preview['reply_text']
        assert not add.called
        if unknown:
            add.side_effect = QingmuError('transport_or_response_error')
        accepted = await confirm(5)
        local_id = accepted['local_order_id']
        assert add.call_count == 1
        assert 'supplier-order-88' not in str(accepted)
        other = await flow.execute(f'qingmu order status --id {local_id}', 'user-b', {'launcher_type':'person','launcher_id':'user-b'}, 6)
        assert not other['success']
        rebuilt = QingmuCoordinator(plugin)
        duplicate = await rebuilt.execute('qingmu order add --platform ucampus --course 1', 'user-a', conversation, 7)
        assert not duplicate['success'] and not duplicate.get('confirmation_required')
        assert add.call_count == 1
        if unknown:
            assert not accepted['success'] and accepted['outcome'] == 'unknown'
        else:
            status = await flow.execute(f'qingmu order status --id {local_id}', 'user-a', conversation, 8)
            assert status['progress']['process'] == '25%'
            await flow.execute(f'qingmu order retry --id {local_id}', 'user-a', conversation, 9)
            assert not retry.called
            assert (await confirm(10))['success']
            assert retry.call_count == 1
        assert all(b'StudentSecret!' not in raw for raw in plugin.storage.values())
    asyncio.run(run(False))
    add.reset_mock(side_effect=True)
    retry.reset_mock()
    asyncio.run(run(True))


def test_actual_listener_intercepts_malformed_credentials_and_tool_cannot_confirm_or_bind():
    from components.event_listener.identity_capture_safe import SafeIdentityCaptureListener
    from components.cli_tools.henu_cli_safe import HenuCliSafe
    from langbot_plugin.api.entities.builtin.provider.session import Session, LauncherTypes
    async def run():
        plugin = Plugin()
        listener = SafeIdentityCaptureListener()
        listener.plugin = plugin
        listener._qingmu = QingmuCoordinator(plugin)
        listener._kit_binding = SimpleNamespace(handle=AsyncMock(return_value=False))
        listener._confirmation_shortcut = ConfirmationShortcut(listener)
        listener._yuketang_login = SimpleNamespace(handle=AsyncMock(return_value=False))
        malformed = ctx("青木 account set --platform ucampus --password 'unterminated", query=20)
        await listener._maybe_handle_sensitive_command(malformed, is_group=False)
        assert malformed.prevent_default.called and malformed.prevent_postorder.called
        await listener._qingmu.tasks[20]
        assert not plugin.storage
        tool = HenuCliSafe()
        tool.plugin = plugin
        tool._load_identity_hint = AsyncMock(return_value={})
        session = Session(sender_id='user-a', launcher_id='user-a', launcher_type=LauncherTypes('person'))
        denied = await tool.call({'command': BIND}, session, 21)
        assert denied['error_code'] == 'direct_private_command_required'
        assert 'StudentSecret!' not in str(denied)
        status = await tool.call({'command':'qingmu status'}, session, 22)
        assert len(status['platforms']) == 3
        denied = await tool.call({'command':'qingmu confirm fake-token'}, session, 23)
        assert not denied['success']
    asyncio.run(run())


def test_explicit_binding_confirmation_does_not_accept_order_preview():
    async def run():
        plugin = Plugin()
        flow = QingmuCoordinator(plugin)
        listener = SimpleNamespace(plugin=plugin, _qingmu=flow)
        from henu_plugin.confirmation import create_pending_operation
        from henu_mcp.core.secure_storage import encrypt_value
        pending = create_pending_operation(storage_key='user-a', canonical_command='qingmu order add --platform ucampus --course 1', query_id=1)
        pending.update(conversation={'launcher_type':'person','launcher_id':'user-a'}, kind='order', tool='qingmu_order_add', params={})
        state = {'schema':'henu.qingmu-state.v1','accounts':{},'courses':{},'orders':{},'pending':pending}
        plugin.storage['user:user-a:qingmu_state'] = encrypt_value(json.dumps(state)).encode()
        flow.handle = AsyncMock(return_value=True)
        await ConfirmationShortcut(listener).handle(ctx('确认青木绑定', query=2))
        flow.handle.assert_not_called()
    asyncio.run(run())


def test_storage_failure_before_send_and_after_receipt_never_allows_retry(monkeypatch):
    from henu_plugin.qingmu_client import Settings, QingmuClient
    settings = Settings('https://supplier.test.invalid','vendor-user','vendor-secret','form',
        {'ucampus':{'id':'77','name':'U校园整本（测试服务）','billing':'测试计费说明'}},
        {'get_success_code':'1','course_id_field':'id','course_name_field':'name','order_id_field':'id'},True)
    monkeypatch.setattr('henu_plugin.qingmu_flow.settings_from_env', lambda: settings)
    monkeypatch.setattr(QingmuClient,'courses',lambda *_:[{'kcid':'22','name':'大学英语'}])
    add = Mock(return_value='88')
    monkeypatch.setattr(QingmuClient, 'place_order', add)
    async def run(after):
        plugin = Plugin()
        flow = QingmuCoordinator(plugin)
        conversation = {'launcher_type':'person','launcher_id':'user-a'}
        async def confirm(n):
            pending = await flow.valid_pending('user-a',conversation,n)
            return await flow.execute('', 'user-a',conversation,n,confirmation_token=pending['token'])
        await flow.execute(BIND,'user-a',conversation,1)
        await confirm(2)
        await flow.execute('qingmu courses --platform ucampus','user-a',conversation,3)
        await flow.execute('qingmu order add --platform ucampus --course 1','user-a',conversation,4)
        original = plugin.set_plugin_storage
        async def save(key,value):
            if not after or add.called:
                raise RuntimeError('synthetic storage failure')
            await original(key,value)
        plugin.set_plugin_storage = save
        result = await confirm(5)
        assert not result['success']
        assert add.call_count == (1 if after else 0)
        plugin.set_plugin_storage = original
        if after:
            assert result['external_committed'] and not result['storage_persisted']
            rebuilt = QingmuCoordinator(plugin)
            duplicate = await rebuilt.execute('qingmu order add --platform ucampus --course 1','user-a',conversation,6)
            assert not duplicate.get('confirmation_required')
    asyncio.run(run(False))
    add.reset_mock()
    asyncio.run(run(True))


def test_multiple_pending_and_same_turn_expired_confirmation_never_execute():
    from henu_mcp.core.secure_storage import encrypt_value
    from henu_plugin.confirmation import create_pending_operation
    async def run():
        plugin = Plugin()
        flow = QingmuCoordinator(plugin)
        listener = SimpleNamespace(plugin=plugin, _qingmu=flow,
            _yuketang_login=SimpleNamespace(handle=AsyncMock()))
        conversation = {'launcher_type':'person','launcher_id':'user-a'}
        await flow.execute(BIND,'user-a',conversation,1)
        p = create_pending_operation(storage_key='user-a',canonical_command='yuketang account set --account x --password demo',query_id=1)
        p['conversation'] = conversation
        plugin.storage['user:user-a:yuketang_pending_credentials'] = encrypt_value(json.dumps(p)).encode()
        q = ctx('确认',query=2)
        await ConfirmationShortcut(listener).handle(q)
        assert '多个' in str(q.reply.call_args_list)
        assert not flow.tasks
        listener._yuketang_login.handle.assert_not_called()
        assert await flow.valid_pending('user-a',conversation,1) is None
        import time
        from unittest.mock import patch
        with patch('henu_plugin.confirmation.time.time',return_value=time.time()+601):
            assert await flow.valid_pending('user-a',conversation,3) is None
    asyncio.run(run())


def test_qingmu_confirmation_roundtrip_through_real_storage_rpc(monkeypatch, tmp_path):
    from tests.test_kit_binding_storage_rpc import storage_api
    async def run():
        async with storage_api(monkeypatch,tmp_path) as plugin:
            flow = QingmuCoordinator(plugin)
            listener = SimpleNamespace(plugin=plugin,_qingmu=flow)
            request = ctx(BIND,query=100)
            await flow.handle(request,is_group=False)
            await flow.tasks[100]
            await ConfirmationShortcut(listener).handle(ctx('确认',query=101))
            assert 101 in flow.tasks
            await flow.tasks[101]
            status = await flow.execute('qingmu status','user-a',{'launcher_type':'person','launcher_id':'user-a'},102)
            assert status['platforms'][0]['bound']
    asyncio.run(run())


def test_shlex_command_and_option_spelling_cannot_bypass_private_intake_or_tool_guard():
    from components.cli_tools.henu_cli_safe import HenuCliSafe
    from langbot_plugin.api.entities.builtin.provider.session import Session, LauncherTypes
    async def run():
        plugin = Plugin()
        flow = QingmuCoordinator(plugin)
        tool = HenuCliSafe()
        tool.plugin = plugin
        tool._load_identity_hint = AsyncMock(return_value={})
        session = Session(sender_id='user-a',launcher_id='user-a',launcher_type=LauncherTypes('person'))
        commands = [BIND.replace('qingmu', "'qingmu'",1), BIND.replace('qingmu',r'q\ingmu',1),
                    BIND.replace('--password','--pass"word"')]
        for number, command in enumerate(commands, 200):
            request = ctx(command,query=number)
            assert await flow.handle(request,is_group=False)
            await flow.tasks[number]
            assert request.prevent_default.called and request.prevent_postorder.called
            result = await tool.call({'command':command},session,number+100)
            assert result['error_code'] == 'direct_private_command_required'
        assert all(b'StudentSecret!' not in raw for raw in plugin.storage.values())
    asyncio.run(run())

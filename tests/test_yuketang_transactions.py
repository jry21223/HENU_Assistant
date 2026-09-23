import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from components.event_listener.identity_capture_safe import SafeIdentityCaptureListener
from henu_plugin.hardened_service import HardenedHenuPluginService
from henu_plugin import bridge_client
from henu_plugin.yuketang_login import YuketangLoginCoordinator
from henu_plugin.confirmation_shortcut import ConfirmationShortcut


class Storage:
    def __init__(self):
        self.data = {}
        self.service = HardenedHenuPluginService(Path(__file__).resolve().parents[1])
        self.fail_user_snapshot = False
    async def get_plugin_storage_keys(self): return list(self.data)
    async def get_plugin_storage(self, key): return self.data[key]
    async def set_plugin_storage(self, key, value):
        if self.fail_user_snapshot and (key.endswith(':snapshot_v2') or key.endswith(':yuketang_config')):
            raise RuntimeError('synthetic storage failure')
        self.data[key] = value


def ctx(number, command):
    return SimpleNamespace(query_id=number,
        event=SimpleNamespace(text_message=command, sender_id='test-a', launcher_id='test-a', launcher_type='person'),
        prevent_default=Mock(), prevent_postorder=Mock(), reply=AsyncMock())


def test_actual_live_listener_commits_then_logs_in_and_storage_failure_never_pushes(monkeypatch):
    async def run(fail):
        plugin = Storage()
        listener = SafeIdentityCaptureListener()
        listener.plugin = plugin
        flow = YuketangLoginCoordinator(listener)
        listener._yuketang_login = flow
        async_sleep = AsyncMock()
        monkeypatch.setattr(asyncio, 'sleep', async_sleep)
        monkeypatch.setattr(bridge_client, 'bridge_settings', lambda: {'url': 'https://test.invalid'})
        def push(user, config):
            assert 'user:test-a:yuketang_config' in plugin.data
            return {'ok': True, 'hash': bridge_client.canonical_hash(config)}
        push_mock = Mock(side_effect=push)
        login_mock = Mock(return_value={'ok': True, 'session_id': 'test-session'})
        monkeypatch.setattr(bridge_client, 'push_config', push_mock)
        monkeypatch.setattr(bridge_client, 'login_password', login_mock)
        monkeypatch.setattr(bridge_client, 'login_result', Mock(return_value={'status': 'success'}))
        start = ctx(1, "yuketang account set --account 13800000000 --password 'Demo123@'")
        await flow.handle(start, is_group=False)
        await flow.tasks[1]
        plugin.fail_user_snapshot = fail
        confirm = ctx(2, '确认')
        await ConfirmationShortcut(listener).handle(confirm)
        await flow.tasks[2]
        if fail:
            assert not push_mock.called and not login_mock.called
            assert '未完成' in str(confirm.reply.call_args.args[0])
        else:
            assert push_mock.called and login_mock.called, str(confirm.reply.call_args.args[0])
            assert '登录成功' in str(confirm.reply.call_args.args[0])
            assert all(b'Demo123@' not in value for value in plugin.data.values())
    asyncio.run(run(False))
    asyncio.run(run(True))


def test_logout_persists_stop_before_bridge_and_reports_remote_failure(monkeypatch):
    async def run(fail_storage, remote_ok):
        plugin = Storage()
        listener = SafeIdentityCaptureListener()
        listener.plugin = plugin
        flow = YuketangLoginCoordinator(listener)
        monkeypatch.setattr(bridge_client, 'bridge_settings', lambda: {'url': 'https://test.invalid'})
        monkeypatch.setattr(bridge_client, 'push_config', lambda user, config: {'ok': True, 'hash': bridge_client.canonical_hash(config)})
        def remote_logout(user):
            assert json.loads(plugin.data['user:test-a:yuketang_config'])['enabled'] is False
            return {'ok': remote_ok}
        logout = Mock(side_effect=remote_logout)
        monkeypatch.setattr(bridge_client, 'logout', logout)
        plugin.fail_user_snapshot = fail_storage
        request = ctx(20, '退出登录雨课堂')
        await flow.handle(request, is_group=False)
        await flow.tasks[20]
        message = str(request.reply.call_args.args[0])
        if fail_storage:
            assert not logout.called
            assert '未完成' in message
        else:
            assert logout.called
            assert ('桥端已确认退出' in message) is remote_ok
            if not remote_ok:
                assert '尚未确认' in message
    asyncio.run(run(False, True))
    asyncio.run(run(False, False))
    asyncio.run(run(True, True))


def test_valid_cookie_skips_login_but_force_does_not(monkeypatch):
    async def run():
        plugin = Storage()
        listener = SafeIdentityCaptureListener()
        listener.plugin = plugin
        flow = YuketangLoginCoordinator(listener)
        monkeypatch.setattr(bridge_client, 'bridge_settings', lambda: None)
        request = ctx(30, '')
        await flow.call_service(request, 'yuketang_account_set', {'account':'13800000000','password':'Demo123@'})
        monkeypatch.setattr(bridge_client, 'fetch_status', lambda user: {'ok':True,'cookie':{'expires_ms':int(time.time()*1000)+7200000}})
        start = Mock(return_value={'ok':True,'session_id':'synthetic'})
        monkeypatch.setattr(bridge_client, 'login_password', start)
        result = await flow.call_service(request, 'yuketang_login', {'force':False})
        assert result['success'] and not start.called and 'login_session_id' not in result
        forced = await flow.call_service(request, 'yuketang_login', {'force':True})
        assert forced['login_session_id'] == 'synthetic' and start.call_count == 1
    asyncio.run(run())

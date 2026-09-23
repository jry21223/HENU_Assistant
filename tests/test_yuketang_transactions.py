import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from components.event_listener.identity_capture_safe import SafeIdentityCaptureListener
from henu_plugin.hardened_service import HardenedHenuPluginService
from henu_plugin import bridge_client
from henu_plugin.yuketang_login import YuketangLoginCoordinator


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
        event=SimpleNamespace(text_message=command, sender_id='test-a', launcher_id='test-a'),
        prevent_default=Mock(), prevent_postorder=Mock(), reply=AsyncMock())


def test_actual_live_listener_commits_then_logs_in_and_storage_failure_never_pushes(monkeypatch):
    async def run(fail):
        plugin = Storage()
        listener = SafeIdentityCaptureListener()
        listener.plugin = plugin
        flow = YuketangLoginCoordinator(listener)
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
        token = str(start.reply.call_args.args[0]).split('yuketang confirm ')[-1]
        plugin.fail_user_snapshot = fail
        confirm = ctx(2, 'yuketang confirm ' + token)
        await flow.handle(confirm, is_group=False)
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

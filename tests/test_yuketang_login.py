import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from henu_plugin.yuketang_login import YuketangLoginCoordinator
from henu_plugin import bridge_client


class Plugin:
    def __init__(self):
        self.storage = {}
    async def get_plugin_storage(self, key):
        return self.storage.get(key, b'')
    async def set_plugin_storage(self, key, value):
        self.storage[key] = value


def context(query_id, text, sender='user-a'):
    return SimpleNamespace(query_id=query_id,
        event=SimpleNamespace(text_message=text, sender_id=sender, launcher_id=sender),
        prevent_default=Mock(), prevent_postorder=Mock(), reply=AsyncMock())


def test_credentials_confirmed_privately_encrypted_and_not_cross_user():
    async def run():
        plugin = Plugin()
        flow = YuketangLoginCoordinator(SimpleNamespace(plugin=plugin))
        ctx = context(1, "yuketang\taccount set --account 13800000000 --password 'Demo@' ")
        assert await flow.handle(ctx, is_group=False)
        await flow.tasks[1]
        raw = next(iter(plugin.storage.values()))
        assert b'Demo@' not in raw and b'13800000000' not in raw
        assert raw.startswith(b'enc:v2:')
        message = str(ctx.reply.call_args.args[0])
        assert '先在雨课堂设置密码' in message
        token = message.split('yuketang confirm ')[-1]
        other = context(2, f'yuketang confirm {token}', sender='user-b')
        assert '没有有效' in await flow.process(other, other.event.text_message, 'user-b')
        ctx.prevent_default.assert_called()
        ctx.prevent_postorder.assert_called()
    asyncio.run(run())


def test_group_credentials_never_reach_storage():
    async def run():
        plugin = Plugin()
        flow = YuketangLoginCoordinator(SimpleNamespace(plugin=plugin))
        ctx = context(1, "雨课堂 账号 绑定 --account 13800000000 --password 'Demo@'")
        assert await flow.handle(ctx, is_group=True)
        assert not plugin.storage and not flow.tasks
        assert '私聊' in str(ctx.reply.call_args.args[0])
    asyncio.run(run())


def test_login_replies_after_async_result_without_repeating_start(monkeypatch):
    async def run():
        flow = YuketangLoginCoordinator(SimpleNamespace(plugin=Plugin()))
        flow.call_service = AsyncMock(return_value={'success': True, 'login_session_id': 'test-session'})
        monkeypatch.setattr(bridge_client, 'login_result', Mock(return_value={'status': 'success'}))
        # Time is a system boundary: no real wait or remote login in this test.
        monkeypatch.setattr(asyncio, 'sleep', AsyncMock())
        ctx = context(8, 'yuketang login')
        await flow.handle(ctx, is_group=False)
        await flow.handle(ctx, is_group=False)
        await flow.tasks[8]
        assert ctx.reply.call_count == 2
        assert '登录成功' in str(ctx.reply.call_args.args[0])
        assert flow.call_service.call_count == 1
    asyncio.run(run())

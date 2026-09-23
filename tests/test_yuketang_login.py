import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from henu_plugin.yuketang_login import YuketangLoginCoordinator
from henu_plugin import bridge_client
from henu_mcp.core.secure_storage import decrypt_value


class Plugin:
    def __init__(self):
        self.storage = {}
    async def get_plugin_storage(self, key):
        return self.storage.get(key, b'')
    async def set_plugin_storage(self, key, value):
        self.storage[key] = value


def context(query_id, text, sender='user-a'):
    return SimpleNamespace(query_id=query_id,
        event=SimpleNamespace(text_message=text, sender_id=sender, launcher_id=sender, launcher_type='person'),
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
        token = json.loads(decrypt_value(raw.decode()))['token']
        assert token not in message
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


def test_force_reaches_service_and_natural_language_group_is_rejected():
    async def run():
        flow = YuketangLoginCoordinator(SimpleNamespace(plugin=Plugin()))
        flow.call_service = AsyncMock(return_value={'success': True, 'reply_text': '当前已登录'})
        request = context(15, 'yuketang login --force')
        await flow.handle(request, is_group=False)
        await flow.tasks[15]
        assert flow.call_service.call_args.args[2] == {'force': True}
        group = context(16, '帮我退出雨课堂')
        assert await flow.handle(group, is_group=True)
        assert '私聊' in str(group.reply.call_args.args[0])
        assert flow.call_service.call_count == 1
    asyncio.run(run())


def test_logout_waits_for_active_login_before_clearing_session(monkeypatch):
    async def run():
        flow = YuketangLoginCoordinator(SimpleNamespace(plugin=Plugin()))
        waiting, finish = asyncio.Event(), asyncio.Event()
        calls = []
        async def service(ctx, name, params):
            calls.append(name)
            return ({'success':True,'login_session_id':'test-session'} if name == 'yuketang_login'
                    else {'success':True,'reply_text':'桥端已确认退出'})
        flow.call_service = service
        async def sleep(_):
            waiting.set()
            await finish.wait()
        monkeypatch.setattr(asyncio, 'sleep', sleep)
        monkeypatch.setattr(bridge_client, 'login_result', lambda sid: {'status':'success'})
        first = context(40, '登录雨课堂')
        await flow.handle(first, is_group=False)
        await asyncio.wait_for(waiting.wait(), timeout=1)
        last = context(41, '退出登录雨课堂')
        await flow.handle(last, is_group=False)
        assert calls == ['yuketang_login']
        assert '尚未完成退出' in str(last.reply.call_args.args[0])
        finish.set()
        await asyncio.wait_for(asyncio.gather(flow.tasks[40], flow.tasks[41]), timeout=1)
        assert calls == ['yuketang_login', 'yuketang_logout']
        assert '桥端已确认退出' in str(last.reply.call_args.args[0])
    asyncio.run(run())


def test_write_ahead_failure_prevents_remote_login_and_unknown_survives_restart():
    async def run():
        plugin = Plugin()
        flow = YuketangLoginCoordinator(SimpleNamespace(plugin=plugin))
        flow.call_service = AsyncMock(return_value={'success':False,'msg':'remote result unknown'})
        request = context(60, 'yuketang login')
        plugin.set_plugin_storage = AsyncMock(side_effect=RuntimeError('storage failure'))
        await flow.handle(request, is_group=False)
        await flow.tasks[60]
        flow.call_service.assert_not_called()
        plugin = Plugin()
        flow = YuketangLoginCoordinator(SimpleNamespace(plugin=plugin))
        flow.call_service = AsyncMock(return_value={'success':False,'msg':'remote result unknown'})
        request = context(61, 'yuketang login')
        await flow.handle(request, is_group=False)
        await flow.tasks[61]
        restarted = YuketangLoginCoordinator(SimpleNamespace(plugin=plugin))
        restarted.call_service = AsyncMock(return_value={'success':False,'reply_text':'退出尚未确认'})
        retry = context(62, 'yuketang login --force')
        await restarted.handle(retry, is_group=False)
        await restarted.tasks[62]
        restarted.call_service.assert_not_called()
        logout = context(63, 'yuketang logout')
        await restarted.handle(logout, is_group=False)
        await restarted.tasks[63]
        assert restarted.call_service.call_args.args[2]['login_inflight_unknown'] is True
        assert '尚未确认' in str(logout.reply.call_args.args[0])
    asyncio.run(run())

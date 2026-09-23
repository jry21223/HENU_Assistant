import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from henu_mcp.core.secure_storage import encrypt_value
from henu_plugin.confirmation import create_pending_operation
from henu_plugin.confirmation_shortcut import ConfirmationShortcut


class Plugin:
    def __init__(self): self.storage = {}
    async def get_plugin_storage_keys(self): return list(self.storage)
    async def get_plugin_storage(self, key): return self.storage[key]
    async def set_plugin_storage(self, key, value): self.storage[key] = value


def ctx(text='确认', user='user-a', query_id=2, launcher='user-a', kind='person'):
    return SimpleNamespace(query_id=query_id,
        event=SimpleNamespace(text_message=text, sender_id=user, launcher_id=launcher, launcher_type=kind),
        prevent_default=Mock(), prevent_postorder=Mock(), reply=AsyncMock())


def pending(command, expires=True):
    p = create_pending_operation(storage_key='user-a', canonical_command=command, query_id=1)
    p['conversation'] = {'launcher_type':'person','launcher_id':'user-a'}
    if not expires: p['expires_at'] = 0
    return p


def test_plain_confirm_routes_private_binding_without_exposing_token():
    async def run():
        plugin = Plugin()
        p = pending("yuketang account set --account 13800000000 --password 'Demo123@'")
        plugin.storage['user:user-a:yuketang_pending_credentials'] = encrypt_value(json.dumps(p)).encode()
        yuke = SimpleNamespace(handle=AsyncMock(return_value=True))
        flow = ConfirmationShortcut(SimpleNamespace(plugin=plugin, _yuketang_login=yuke))
        request = ctx()
        assert await flow.handle(request)
        assert yuke.handle.call_args.kwargs['command'] == 'yuketang confirm ' + p['token']
        request.prevent_default.assert_called()
        request.prevent_postorder.assert_called()
        assert p['token'] not in str(request.reply.call_args_list)
    asyncio.run(run())


def test_expired_other_user_other_chat_and_same_turn_do_not_confirm():
    async def run():
        for request, expired in ((ctx(user='user-b'),False), (ctx(launcher='another-chat'),False), (ctx(query_id=1),False), (ctx(),True)):
            plugin = Plugin()
            p = pending('yuketang exam set --x-access-token fake', expires=not expired)
            plugin.storage['user:user-a:yuketang_pending_credentials'] = encrypt_value(json.dumps(p)).encode()
            yuke = SimpleNamespace(handle=AsyncMock())
            flow = ConfirmationShortcut(SimpleNamespace(plugin=plugin, _yuketang_login=yuke))
            assert await flow.handle(request)
            yuke.handle.assert_not_called()
    asyncio.run(run())


def test_ambiguous_pending_requires_readable_choice():
    async def run():
        plugin = Plugin()
        p = pending('yuketang exam set --x-access-token fake')
        plugin.storage['user:user-a:yuketang_pending_credentials'] = encrypt_value(json.dumps(p)).encode()
        plugin.storage['user:user-a:pending_operation'] = json.dumps(pending('yuketang enable')).encode()
        yuke = SimpleNamespace(handle=AsyncMock())
        flow = ConfirmationShortcut(SimpleNamespace(plugin=plugin, _yuketang_login=yuke))
        request = ctx()
        assert await flow.handle(request)
        assert '确认绑定' in str(request.reply.call_args.args[0])
        assert '确认操作' in str(request.reply.call_args.args[0])
        yuke.handle.assert_not_called()
    asyncio.run(run())


def test_regular_operation_plain_confirm_executes_once_without_showing_token(monkeypatch):
    async def run():
        from pathlib import Path
        from components.cli_tools.henu_cli_safe import HenuCliSafe
        from henu_plugin.hardened_service import HardenedHenuPluginService
        from henu_plugin import bridge_client
        from langbot_plugin.api.entities.builtin.provider.session import Session, LauncherTypes
        plugin = Plugin()
        plugin.service = HardenedHenuPluginService(Path(__file__).resolve().parents[1])
        monkeypatch.setattr(bridge_client, 'bridge_settings', lambda: None)
        tool = HenuCliSafe()
        tool.plugin = plugin
        session = Session(sender_id='user-a',launcher_id='user-a',launcher_type=LauncherTypes('person'))
        preview = await tool.call({'command':'yuketang enable'},session,1)
        p = json.loads(plugin.storage['user:user-a:pending_operation'])
        assert preview['confirmation_command'] == '确认'
        assert p['token'] not in str(preview)
        flow = ConfirmationShortcut(SimpleNamespace(plugin=plugin))
        request = ctx()
        assert await flow.handle(request)
        task = flow.tasks['user-a']
        await task
        assert json.loads(plugin.storage['user:user-a:yuketang_config'])['enabled'] is True
        next_request = ctx(query_id=3)
        await flow.handle(next_request)
        assert '没有有效' in str(next_request.reply.call_args.args[0])
    asyncio.run(run())

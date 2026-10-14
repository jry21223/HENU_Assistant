import asyncio
import json
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from langbot_plugin.api.entities.builtin.platform import message as pm
from henu_plugin.confirmation import create_pending_operation
from henu_plugin.qq_buttons import register_pending, handle_button, pending_keyboard

pytestmark = pytest.mark.skipif(not hasattr(pm, 'HenuMarkdown'), reason='Native keyboard bridge requires production SDK HenuMarkdown support')


class Plugin:
    def __init__(self):
        self.storage = {}
    def get_config(self):
        return {'qq_cards_buttons': True}
    async def get_plugin_storage(self, key):
        return self.storage.get(key)
    async def set_plugin_storage(self, key, value):
        self.storage[key] = value
    async def get_plugin_storage_keys(self):
        return list(self.storage)


def request(data, owner=('bot-a', 'person', 'alice', 'alice'), query=2):
    return SimpleNamespace(query_id=query, is_prevent_postorder=False,
        event=SimpleNamespace(sender_id=owner[3], launcher_type=owner[1], launcher_id=owner[2],
            text_message=data, message_chain=pm.MessageChain([pm.Plain(text=data)])),
        get_bot_uuid=AsyncMock(return_value=owner[0]),
        get_query_vars=AsyncMock(return_value={'_henu_button_interaction': {'data': data}}),
        prevent_default=Mock(), prevent_postorder=Mock(), reply=AsyncMock())


def test_cancel_exact_pending_does_not_call_campus_and_old_confirm_is_inert(monkeypatch):
    from components.cli_tools.henu_cli_safe import HenuCliSafe
    async def run():
        plugin = Plugin()
        pending = create_pending_operation(storage_key='alice', canonical_command='yuketang enable', query_id=1)
        pending['conversation'] = {'launcher_type': 'person', 'launcher_id': 'alice'}
        pending['bot'] = 'bot-a'
        plugin.storage['user:alice:pending_operation'] = json.dumps(pending).encode()
        action = await register_pending(plugin, ('bot-a', 'person', 'alice', 'alice'), 'operation', 'user:alice:pending_operation', pending)
        keyboard = pending_keyboard(action, 'alice')
        buttons = keyboard['content']['rows'][0]['buttons']
        assert [b['action']['type'] for b in buttons] == [1, 1]
        assert buttons[0]['group_id'] == buttons[1]['group_id']
        assert buttons[0]['action']['permission'] == {'type': 0, 'specify_user_ids': ['alice']}
        call = AsyncMock()
        monkeypatch.setattr(HenuCliSafe, 'call', call)
        assert await handle_button(plugin, request(buttons[1]['action']['data']))
        assert await handle_button(plugin, request(buttons[0]['action']['data'], query=3))
        call.assert_not_called()
        assert not json.loads(plugin.storage['user:alice:pending_operation'])
    asyncio.run(run())


def test_schedule_card_serializes_native_read_buttons_and_old_paging_expires():
    from henu_plugin.qq_cards import cache, card_chain
    owner = ('bot-a', 'person', 'alice', 'alice')
    plugin = Plugin()
    cards = cache(plugin)
    cards.put(owner, ['# 河大助手 · 我的课表\npage1', '# 河大助手 · 我的课表\npage2'])
    keyboard = cards.keyboard(owner, cards.entries[owner][1][0], plugin, tool='schedule_query')
    buttons = keyboard['content']['rows'][0]['buttons']
    assert any(b['action']['data'] == 'schedule now' for b in buttons)
    assert all(b['action']['type'] == 2 and b['action']['enter'] for b in buttons)
    chain = card_chain(cards.entries[owner][1][0], keyboard=keyboard)
    assert chain.model_dump(mode='json')[0]['type'] == 'HenuMarkdown'
    old_page = next(b['action']['data'] for b in buttons if b['render_data']['label'] == '下一页')
    assert cards.page(owner, old_page).endswith('page2')
    cards.put(owner, ['# 河大助手 · 新查询\nnew'])
    assert '失效' in cards.page(owner, old_page)


def test_confirmation_is_consumed_before_an_ambiguous_campus_failure(monkeypatch):
    from components.cli_tools.henu_cli import HenuCli
    from components.cli_tools.base import BaseHenuTool
    from langbot_plugin.api.entities.builtin.provider.session import Session, LauncherTypes
    async def run():
        plugin = Plugin()
        pending = create_pending_operation(storage_key='alice', canonical_command='yuketang enable', query_id=1)
        pending['conversation'] = {'launcher_type': 'person', 'launcher_id': 'alice'}
        plugin.storage['user:alice:pending_operation'] = json.dumps(pending).encode()
        attempted = []
        async def campus_call(self, *args):
            attempted.append(1)
            await asyncio.sleep(0)
            raise TimeoutError('synthetic ambiguous failure')
        monkeypatch.setattr(BaseHenuTool, 'call', campus_call)
        tool = HenuCli(); tool.plugin = plugin
        session = Session(sender_id='alice', launcher_id='alice', launcher_type=LauncherTypes.PERSON)
        results = await asyncio.gather(*[tool.call({'command': 'confirm ' + pending['token']}, session, q) for q in (2, 3)], return_exceptions=True)
        assert len(attempted) == 1
        assert any(isinstance(r, dict) and r.get('error_code') == 'confirmation_invalid' for r in results)
    asyncio.run(run())


@pytest.mark.parametrize('case', ['person', 'bot', 'chat', 'expiry', 'same_query', 'untrusted', 'replaced', 'fence_failure'])
def test_invalid_choice_never_calls_campus(monkeypatch, case):
    from components.cli_tools.henu_cli_safe import HenuCliSafe
    async def run():
        plugin = Plugin()
        pending = create_pending_operation(storage_key='alice', canonical_command='yuketang enable', query_id=1)
        pending['conversation'] = {'launcher_type': 'person', 'launcher_id': 'alice'}
        pending['bot'] = 'bot-a'
        plugin.storage['user:alice:pending_operation'] = json.dumps(pending).encode()
        action = await register_pending(plugin, ('bot-a', 'person', 'alice', 'alice'), 'operation', 'user:alice:pending_operation', pending)
        data = pending_keyboard(action, 'alice')['content']['rows'][0]['buttons'][0]['action']['data']
        owner = {'person': ('bot-a', 'person', 'bob', 'bob'), 'bot': ('bot-b', 'person', 'alice', 'alice'),
                 'chat': ('bot-a', 'person', 'another-chat', 'alice')}.get(case, ('bot-a', 'person', 'alice', 'alice'))
        ctx = request(data, owner, query=1 if case == 'same_query' else 2)
        if case == 'expiry':
            monkeypatch.setattr('henu_plugin.qq_buttons.time.time', lambda: action['expires'] + 1)
        elif case == 'untrusted':
            ctx.get_query_vars = AsyncMock(return_value={})
        elif case == 'replaced':
            replacement = dict(pending, token='replacement-token')
            plugin.storage['user:alice:pending_operation'] = json.dumps(replacement).encode()
        elif case == 'fence_failure':
            async def fail(key, value):
                raise OSError('synthetic storage unavailable')
            plugin.set_plugin_storage = fail
        call = AsyncMock()
        monkeypatch.setattr(HenuCliSafe, 'call', call)
        assert await handle_button(plugin, ctx)
        call.assert_not_called()
    asyncio.run(run())


def test_duplicate_confirmation_stays_consumed_after_runtime_restart(monkeypatch):
    from components.cli_tools.henu_cli_safe import HenuCliSafe
    async def run():
        plugin = Plugin()
        pending = create_pending_operation(storage_key='alice', canonical_command='yuketang enable', query_id=1)
        pending['conversation'] = {'launcher_type': 'person', 'launcher_id': 'alice'}
        pending['bot'] = 'bot-a'
        plugin.storage['user:alice:pending_operation'] = json.dumps(pending).encode()
        action = await register_pending(plugin, ('bot-a', 'person', 'alice', 'alice'), 'operation', 'user:alice:pending_operation', pending)
        data = pending_keyboard(action, 'alice')['content']['rows'][0]['buttons'][0]['action']['data']
        call = AsyncMock(side_effect=TimeoutError('synthetic unknown result'))
        monkeypatch.setattr(HenuCliSafe, 'call', call)
        assert await handle_button(plugin, request(data))
        restarted = Plugin(); restarted.storage = plugin.storage
        assert await handle_button(restarted, request(data, query=3))
        call.assert_awaited_once()
    asyncio.run(run())


def test_multi_tool_confirmation_buttons_stay_on_their_own_preview():
    from henu_plugin.qq_cards import cache
    plugin = Plugin()
    owner = ('bot-a', 'person', 'alice', 'alice')
    cards = cache(plugin)
    cards.stage(owner, 10, 'status', ['# 河大助手 · 状态\nread-only'])
    cards.set_controls(owner, 'status', None, 'system_status')
    keyboard = {'content': {'rows': [{'buttons': [{'id': 'confirm'}]}]}}
    pages = cards.stage(owner, 10, 'reservation', ['# 河大助手 · 预约\npending'])
    cards.set_controls(owner, 'reservation', keyboard, 'library_reserve')
    assert cards.keyboard(owner, pages[0], plugin) != keyboard
    assert cards.keyboard(owner, pages[1], plugin) == keyboard
    pages = cards.stage(owner, 10, 'third-read', ['# 河大助手 · 课表\nread-only'])
    assert cards.keyboard(owner, pages[1], plugin) == keyboard
    assert cards.keyboard(owner, pages[0], plugin) != keyboard


def test_real_sdk_first_food_listener_handles_callback_before_the_model(tmp_path, monkeypatch):
    from test_qq_cards_dispatch import MemoryRPC
    from components.event_listener.food_capture import FoodCaptureListener
    from components.cli_tools.henu_food import HenuFood
    from components.cli_tools.henu_cli_safe import HenuCliSafe
    from langbot_plugin.api.entities import events
    from langbot_plugin.api.entities.builtin.platform import events as pe, entities as entities
    from langbot_plugin.entities.io.actions.enums import RuntimeToPluginAction
    from datetime import datetime, timezone
    monkeypatch.chdir(tmp_path)
    async def run():
        rpc = MemoryRPC(None, lambda _: None)
        rpc.variables, rpc.sent = {}, []
        plugin = Plugin(); plugin.plugin_runtime_handler = rpc
        pending = create_pending_operation(storage_key='alice', canonical_command='yuketang enable', query_id=1)
        pending.update(bot='synthetic-bot', conversation={'launcher_type': 'person', 'launcher_id': 'alice'})
        plugin.storage['user:alice:pending_operation'] = json.dumps(pending).encode()
        action = await register_pending(plugin, ('synthetic-bot', 'person', 'alice', 'alice'), 'operation', 'user:alice:pending_operation', pending)
        data = pending_keyboard(action, 'alice')['content']['rows'][0]['buttons'][0]['action']['data']
        rpc.variables['_henu_button_interaction'] = {'data': data}
        primary = FoodCaptureListener(); primary.plugin = plugin; await primary.initialize()
        rpc.plugin_container = SimpleNamespace(components=[SimpleNamespace(manifest=SimpleNamespace(kind='EventListener'), component_instance=primary)])
        chain = pm.MessageChain([pm.Source(id='synthetic-ws-event', time=datetime.now(timezone.utc)), pm.Plain(text=data)])
        source = pe.FriendMessage(sender=entities.Friend(id='alice', nickname='', remark=''), message_chain=chain, time=1)
        event = events.PersonMessageReceived(launcher_type='person', launcher_id='alice', sender_id='alice', message_event=source, message_chain=chain)
        call = AsyncMock(return_value={'success': True, 'msg': 'synthetic completion'})
        food = AsyncMock()
        monkeypatch.setattr(HenuCliSafe, 'call', call)
        monkeypatch.setattr(HenuFood, 'receive_message', food)
        result = await rpc.actions[RuntimeToPluginAction.EMIT_EVENT.value]({'event_context': {
            'query_id': 2, 'eid': 1, 'event_name': 'PersonMessageReceived', 'event': event.model_dump(mode='json')}})
        assert result.data['event_context']['is_prevent_default']
        assert result.data['event_context']['is_prevent_postorder']
        call.assert_awaited_once()
        food.assert_not_called()
        assert rpc.sent[0][0]['text'].startswith('# 河大助手 · 按钮操作')
    asyncio.run(run())


def test_food_button_keeps_exact_revision_and_publishes_once(tmp_path, monkeypatch):
    from henu_plugin.food_drafts import FoodDrafts, chat_scope
    from components.cli_tools.henu_food import HenuFood
    from datetime import datetime, timezone
    import time
    monkeypatch.setenv('HENU_FOOD_DRAFT_DB', str(tmp_path / 'synthetic.sqlite'))
    async def run():
        plugin = Plugin()
        settings = SimpleNamespace(context_secret='synthetic-secret-at-least-32-characters')
        monkeypatch.setattr('components.cli_tools.henu_food.food_settings', lambda: settings)
        monkeypatch.setattr(HenuFood, '_account', lambda self, *args: (None, ''))
        published = AsyncMock(return_value={'success': True, 'reply_text': 'synthetic completion'})
        monkeypatch.setattr('components.cli_tools.henu_food.call_food', published)
        owner = ('bot-a', 'person', 'alice', 'alice')
        store = FoodDrafts()
        scope = chat_scope(settings.context_secret, 'bot-a', 'alice', 'person', 'alice')
        store.receive(scope, 'draft-source', time.time(), 1, '准备投稿', [], '')
        text = store.prepare(scope, 1, 'draft-source', {'venue_name': '示例店铺', 'campus': 'jinming', 'tier': 'hang', 'review_text': '合成测试内容'})
        action = await register_pending(plugin, owner, 'food', scope, store.button_snapshot(scope), mode='command')
        data = pending_keyboard(action, 'alice')['content']['rows'][0]['buttons'][0]['action']['data']
        blocked = request(data)
        await handle_button(plugin, blocked)
        published.assert_not_called()  # Complete preview remains a server gate.
        store.delivered(scope, 'draft-source', text)
        valid = request(data, query=3)
        valid.event.message_chain = pm.MessageChain([pm.Source(id='later-real-message', time=datetime.now(timezone.utc)), pm.Plain(text=data)])
        valid.set_query_var = AsyncMock()
        await handle_button(plugin, valid)
        await handle_button(plugin, request(data, query=4))
        published.assert_awaited_once()
    asyncio.run(run())


def test_kit_button_preserves_real_quote_confirmation(monkeypatch):
    from henu_plugin.qq_buttons import coordinators
    from test_kit_binding import kit_event
    from datetime import datetime, timezone
    monkeypatch.setattr('henu_plugin.kit_binding.ACTIVATION_DELAY_SECONDS', -120)
    monkeypatch.setattr('henu_plugin.kit_binding.kit_settings', lambda: {'bot_uuid': 'bot-a', 'portal_url': 'https://kit.test'})
    calls = []
    def remote(settings, action, body):
        calls.append(action)
        return {'token': 'a' * 43} if action == 'start' else {'state': 'authorized', 'display_name': '示例账号'} if action == 'pending' else {'bound': True, 'display_name': '示例账号'}
    monkeypatch.setattr('henu_plugin.kit_binding.kit_request', remote)
    async def run():
        plugin = Plugin()
        flow = coordinators(plugin).kit
        def ctx(text, q, quote=None):
            c = kit_event(text, q, 'source-' + str(q), quote_ref_idx=quote)
            c.get_bot_uuid = AsyncMock(return_value='bot-a')
            c.event.message_chain = pm.MessageChain([pm.Source(id='source-' + str(q), time=datetime.now(timezone.utc)), pm.Plain(text=text)])
            c.event.message_event.time = c.event.message_chain[0].time.timestamp()
            return c
        await flow.handle(ctx('绑定 HENU KIT', 1), is_group=False)
        preview = ctx('确认', 2)
        await flow.handle(preview, is_group=False)
        chain = preview.reply.call_args.args[0]
        assert chain[0].type == 'HenuMarkdown', str(chain)
        button = chain[0].keyboard['content']['rows'][0]['buttons'][0]
        assert button['action']['type'] == 2 and button['action']['reply'] is True
        click = ctx(button['action']['data'], 3, quote='ref-bot-2')
        click.is_prevent_postorder = False
        await handle_button(plugin, click)
        assert calls.count('confirm') == 1
        for task in flow.watchers.values():
            task.cancel()
    asyncio.run(run())

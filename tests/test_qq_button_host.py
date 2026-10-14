import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

spec = importlib.util.spec_from_file_location('button_host', Path(__file__).parents[1] / 'ops/qq_buttons_host.py')
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)


def test_callback_ack_once_and_query_uses_exact_ws_anchor_without_dify_state():
    async def run():
        adapter = SimpleNamespace(bot=SimpleNamespace(ack_interaction=AsyncMock()),
            config={'enable-henu-buttons': True}, enable_webhook=False,
            ap=SimpleNamespace(query_pool=SimpleNamespace(add_query=AsyncMock())))
        adapter.ap.platform_mgr = SimpleNamespace(bots=[SimpleNamespace(adapter=adapter,
            bot_entity=SimpleNamespace(uuid='synthetic-bot', use_pipeline_uuid='synthetic-pipeline'))])
        data = 'henu-btn:v1:operation:confirm:ABCDEFGHIJKLMNOPQRSTUVWX'
        event = {'id': 'interaction-ack-id', 'type': 11, 'scene': 'c2c', 'chat_type': 2,
            'user_openid': 'alice', 'timestamp': host.datetime.now(host.timezone.utc).isoformat(),
            'data': {'type': 11, 'resolved': {'button_data': data}}}
        assert await host.handle_interaction(adapter, event, 'ws-passive-anchor')
        assert await host.handle_interaction(adapter, event, 'ws-passive-anchor')
        adapter.bot.ack_interaction.assert_awaited_once_with('interaction-ack-id', code=0)
        adapter.ap.query_pool.add_query.assert_awaited_once()
        arguments = adapter.ap.query_pool.add_query.call_args.kwargs
        assert arguments['sender_id'] == 'alice'
        assert arguments['bot_uuid'] == 'synthetic-bot'
        assert arguments['message_event'].source_platform_object.event_id == 'ws-passive-anchor'
        assert arguments['variables']['_henu_button_interaction']['data'] == data
        assert '_dify_form_action' not in arguments['variables']
    asyncio.run(run())

"""Replay real SDK event dispatch with production's existing primary listener."""
import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import langbot_plugin
import pytest
from unittest.mock import AsyncMock
from langbot_plugin.api.entities import events
from langbot_plugin.api.entities.builtin.platform import events as pe, entities as entities, message as pm
from langbot_plugin.api.entities.builtin.provider.session import Session, LauncherTypes
from langbot_plugin.entities.io.actions.enums import RuntimeToPluginAction
from components.event_listener.identity_capture_safe import SafeIdentityCaptureListener
from components.event_listener.qq_cards import QQCardsListener
from components.event_listener.food_capture import FoodCaptureListener
from components.cli_tools.henu_food import HenuFood
from henu_plugin.qq_cards import PREFIX, cache

# Load the actual dispatch handler without starting the SDK's CLI application.
spec = importlib.util.spec_from_file_location("card_sdk_dispatch", Path(langbot_plugin.__file__).parent / "cli/run/handler.py")
sdk_dispatch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sdk_dispatch)
PluginRuntimeHandler = sdk_dispatch.PluginRuntimeHandler


class MemoryRPC(PluginRuntimeHandler):
    async def call_action(self, action, payload, **kwargs):
        name = str(getattr(action, "value", action)).lower()
        if name == "get_query_vars":
            return {"vars": self.variables}
        if name == "get_bot_uuid":
            return {"bot_uuid": "synthetic-bot"}
        if name == "set_query_var":
            self.variables[payload["key"]] = payload["value"]
            return {}
        if name == "reply_message":
            self.sent.append(payload["message_chain"])
            return {}
        raise AssertionError("Unexpected RPC; no real sending/storage is allowed")


def test_sdk_first_matching_listener_preserves_status_card(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    async def run():
        async def initialize_plugin(_):
            pass
        rpc = MemoryRPC(None, initialize_plugin)
        rpc.variables = {"_henu_card": {
            "owner": ["synthetic-bot", "person", "alice", "alice"],
            "page": PREFIX + "账号与服务状态\n\n**已完成**\n本地绑定记录已读取。",
        }}
        plugin = SimpleNamespace(plugin_runtime_handler=rpc)
        primary = SafeIdentityCaptureListener(); primary.plugin = plugin
        cards = QQCardsListener(); cards.plugin = plugin
        await primary.initialize(); await cards.initialize()
        # Production 2.1.0 has a pre-existing sensitive-output redaction hook.
        # Preserve that primary hook when replaying the SDK's first-match rule.
        async def redact(ctx):
            ctx.event.response_text = ctx.event.response_text.replace("password=secret", "password=已隐藏")
        primary.registered_handlers.setdefault(events.NormalMessageResponded, []).insert(0, redact)
        rpc.plugin_container = SimpleNamespace(components=[
            SimpleNamespace(manifest=SimpleNamespace(kind="EventListener"), component_instance=primary),
            SimpleNamespace(manifest=SimpleNamespace(kind="EventListener"), component_instance=cards),
        ])
        event = events.NormalMessageResponded(
            launcher_type="person", launcher_id="alice", sender_id="alice",
            session=Session(launcher_type=LauncherTypes.PERSON, launcher_id="alice", sender_id="alice"),
            prefix="", response_text="账号状态：已绑定，本次检查无异常。", finish_reason="stop", funcs_called=[],
        )
        response = await rpc.actions[RuntimeToPluginAction.EMIT_EVENT.value]({"event_context": {
            "query_id":0, "eid":1, "event_name":"NormalMessageResponded", "event":event.model_dump(mode="json"),
        }})
        context = response.data["event_context"]
        field = "reply_message_chain" if "reply_message_chain" in events.NormalMessageResponded.model_fields else "reply"
        chain = context["event"][field]
        assert chain and chain[0]["text"].startswith(PREFIX), "SDK primary listener dropped the card and kept the AI plain reply"
        assert not context["is_prevent_default"]
        assert rpc.variables["_henu_card"] is None
        # Continue across the same host bridge boundary, with HTTP fully mocked.
        import httpx
        transport_spec = importlib.util.spec_from_file_location("dispatch_transport", Path(__file__).parents[1] / "ops/qq_cards_transport.py")
        transport = importlib.util.module_from_spec(transport_spec); transport_spec.loader.exec_module(transport)
        wire = []
        async def post(client, url, **kwargs):
            wire.append(kwargs["json"])
            return httpx.Response(200, json={"id":"synthetic-reply"})
        monkeypatch.setattr(httpx.AsyncClient,"post",post)
        client = SimpleNamespace(base_url="https://api.sgroup.qq.com", access_token="synthetic", check_access_token=AsyncMock(return_value=True), next_reply_msg_seq=AsyncMock(return_value=1), send_private_text_msg=AsyncMock())
        await transport.send_card(client,"c2c","synthetic-receiver",chain[0]["text"],msg_id="synthetic-source")
        assert len(wire)==1 and wire[0]["msg_type"]==2
        assert wire[0]["markdown"]["content"].startswith(PREFIX)
        assert "keyboard" not in wire[0]
    asyncio.run(run())


@pytest.mark.parametrize("attached", [False, True])
def test_sdk_primary_food_listener_pages_only_pure_control(tmp_path, monkeypatch, attached):
    monkeypatch.chdir(tmp_path)
    receive = AsyncMock()
    monkeypatch.setattr(HenuFood, "receive_message", receive)
    async def run():
        async def initialize_plugin(_):
            pass
        rpc = MemoryRPC(None, initialize_plugin); rpc.variables = {}; rpc.sent = []
        plugin = SimpleNamespace(plugin_runtime_handler=rpc)
        cache(plugin).put(("synthetic-bot", "person", "alice", "alice"), [PREFIX + "第一页", PREFIX + "第二页"])
        food = FoodCaptureListener(); food.plugin = plugin; await food.initialize()
        cards = QQCardsListener(); cards.plugin = plugin; await cards.initialize()
        rpc.plugin_container = SimpleNamespace(components=[
            SimpleNamespace(manifest=SimpleNamespace(kind="EventListener"), component_instance=food),
            SimpleNamespace(manifest=SimpleNamespace(kind="EventListener"), component_instance=cards),
        ])
        parts = [pm.Plain(text="下一页")]
        if attached:
            parts.append(pm.Image(url="https://synthetic.invalid/image.png"))
        chain = pm.MessageChain(parts)
        incoming = pe.FriendMessage(sender=entities.Friend(id="alice", nickname="合成", remark=""), message_chain=chain)
        event = events.PersonMessageReceived(launcher_type="person", launcher_id="alice", sender_id="alice", message_event=incoming, message_chain=chain)
        response = await rpc.actions[RuntimeToPluginAction.EMIT_EVENT.value]({"event_context": {
            "query_id":0, "eid":2, "event_name":"PersonMessageReceived", "event":event.model_dump(mode="json"),
        }})
        if attached:
            receive.assert_awaited_once(); assert rpc.sent == []
            assert not response.data["event_context"]["is_prevent_default"]
        else:
            receive.assert_not_awaited()
            assert rpc.sent == [[{"type":"Plain", "text":PREFIX + "第二页"}]]
            assert response.data["event_context"]["is_prevent_default"]
    asyncio.run(run())


def test_sdk_food_primary_delivers_navigation_before_normal_stage(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    receive=AsyncMock()
    monkeypatch.setattr(HenuFood,'receive_message',receive)
    async def run():
        async def initialize_plugin(_): pass
        rpc=MemoryRPC(None,initialize_plugin);rpc.variables={};rpc.sent=[]
        plugin=SimpleNamespace(plugin_runtime_handler=rpc)
        food=FoodCaptureListener();food.plugin=plugin;await food.initialize()
        rpc.plugin_container=SimpleNamespace(components=[SimpleNamespace(manifest=SimpleNamespace(kind='EventListener'),component_instance=food)])
        chain=pm.MessageChain(root=[pm.Plain(text='使用帮助')])
        incoming=pe.FriendMessage(sender=entities.Friend(id='alice',nickname='合成',remark=''),message_chain=chain)
        event=events.PersonMessageReceived(launcher_type='person',launcher_id='alice',sender_id='alice',message_event=incoming,message_chain=chain)
        response=await rpc.actions[RuntimeToPluginAction.EMIT_EVENT.value]({'event_context':{'query_id':0,'eid':4,'event_name':'PersonMessageReceived','event':event.model_dump(mode='json')}})
        assert '从这里开始' in str(rpc.sent)
        assert response.data['event_context']['is_prevent_default']
        receive.assert_not_awaited()
    asyncio.run(run())

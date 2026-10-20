import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_button_card_on_wire_binds_actual_reply_and_strips_control_metadata(
    monkeypatch,
):
    from henu_plugin.qq_buttons import envelope

    transport = load("qq_transport", Path("ops/qq_cards_transport.py"))
    requests = []

    async def post(self, url, **kwargs):
        requests.append(kwargs["json"])
        return httpx.Response(200, json={"id": "card-123"})

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    client = SimpleNamespace(
        base_url="https://api.sgroup.qq.com",
        access_token="test",
        check_access_token=AsyncMock(return_value=True),
        next_reply_msg_seq=AsyncMock(return_value=1),
        send_private_text_msg=AsyncMock(),
    )

    async def run():
        wire = {"owner": "alice", "actions": [{"id": "a" * 24, "label": "确认操作"}]}
        page = transport.PREFIX + "操作确认\n尚未提交"
        await transport.send_card(
            client, "c2c", "alice", envelope(page, wire), msg_id="inbound"
        )
        assert requests[0]["markdown"]["content"] == page
        button = requests[0]["keyboard"]["content"]["rows"][0]["buttons"][0]
        assert button["action"]["type"] == 1
        assert button["action"]["data"] == "henu:" + "a" * 24
        assert client._henu_buttons["a" * 24]["message_id"] == "card-123"

    asyncio.run(run())


def test_callback_image_uses_passive_event_id_and_upload_does_not_send(monkeypatch):
    import base64, time
    from langbot_plugin.api.entities.builtin.platform.message import MessageChain, Image

    host = load("qq_buttons_image", Path("ops/qq_cards_transport.py"))
    captured = []

    async def post(self, url, **kwargs):
        captured.append(kwargs["json"])
        return httpx.Response(200, json={"id": "image-reply"})

    monkeypatch.setattr(httpx.AsyncClient, "post", post)

    async def run():
        bot = SimpleNamespace(
            base_url="https://api.sgroup.qq.com",
            access_token="test",
            upload_media=AsyncMock(return_value="file-ref"),
        )
        adapter = SimpleNamespace(
            bot=bot,
            _session_event_ids={
                "c2c_alice": {
                    "event_id": "button-event",
                    "posted_at": time.time(),
                    "henu": True,
                }
            },
            _next_msg_seq=lambda anchor: 2,
        )
        source = SimpleNamespace(
            source_platform_object=None, sender=SimpleNamespace(id="alice")
        )
        chain = MessageChain(
            [Image(base64=base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"data").decode())]
        )
        assert await host.reply_synthetic_image(adapter, source, chain)
        assert captured == [
            {
                "msg_type": 7,
                "media": {"file_info": "file-ref"},
                "event_id": "button-event",
                "msg_seq": 2,
            }
        ]
        assert bot.upload_media.call_args.kwargs["file_type"] == 1

    asyncio.run(run())


def test_callback_rejects_other_user_and_old_message_without_dispatch():
    host = load("qq_buttons_host", Path("ops/qq_cards_transport.py"))

    async def run():
        import time

        bot = SimpleNamespace(
            ack_interaction=AsyncMock(),
            _henu_buttons={
                "a" * 24: {
                    "owner": "alice",
                    "target": "alice",
                    "scene": "c2c",
                    "message_id": "card-123",
                    "expires": time.monotonic() + 300,
                    "group": "set1",
                }
            },
        )
        adapter = SimpleNamespace(
            bot=bot,
            ap=SimpleNamespace(query_pool=SimpleNamespace(add_query=AsyncMock())),
        )
        for user, message in [("bob", "card-123"), ("alice", "old-card")]:
            data = {
                "id": "interaction",
                "chat_type": 2,
                "user_openid": user,
                "data": {
                    "resolved": {
                        "button_data": "henu:" + "a" * 24,
                        "message_id": message,
                    }
                },
            }
            assert await host.handle_click(adapter, data, "event")
        adapter.ap.query_pool.add_query.assert_not_awaited()
        assert "a" * 24 in bot._henu_buttons

    asyncio.run(run())


def test_valid_callback_enqueues_one_scoped_plugin_message():
    import time

    host = load("qq_buttons_positive", Path("ops/qq_cards_transport.py"))

    async def run():
        bot = SimpleNamespace(
            ack_interaction=AsyncMock(),
            _henu_buttons={
                "a" * 24: {
                    "owner": "alice",
                    "target": "alice",
                    "scene": "c2c",
                    "message_id": "card-123",
                    "expires": time.monotonic() + 300,
                    "group": "card",
                },
                "b" * 24: {
                    "owner": "alice",
                    "target": "alice",
                    "scene": "c2c",
                    "message_id": "card-123",
                    "expires": time.monotonic() + 300,
                    "group": "card",
                },
            },
        )
        adapter = SimpleNamespace(bot=bot, _session_event_ids={}, _anchor_msg_seq={})
        bot_entry = SimpleNamespace(
            adapter=adapter,
            bot_entity=SimpleNamespace(uuid="henu-bot", use_pipeline_uuid="pipeline"),
        )
        adapter.ap = SimpleNamespace(
            platform_mgr=SimpleNamespace(bots=[bot_entry]),
            query_pool=SimpleNamespace(add_query=AsyncMock()),
        )
        data = {
            "id": "interaction",
            "chat_type": 2,
            "user_openid": "alice",
            "data": {
                "resolved": {
                    "button_data": "henu:" + "a" * 24,
                    "button_id": "a" * 24,
                }
            },
        }
        assert await host.handle_click(adapter, data, "event-123")
        assert await host.handle_click(adapter, data, "event-123")
        call = adapter.ap.query_pool.add_query
        assert call.await_count == 1
        assert call.call_args.kwargs["sender_id"] == "alice"
        assert str(call.call_args.kwargs["message_chain"]) == "HENU_BUTTON " + "a" * 24
        assert adapter._session_event_ids["c2c_alice"]["event_id"] == "event-123"
        assert not bot._henu_buttons

    asyncio.run(run())

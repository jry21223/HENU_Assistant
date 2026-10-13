import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

spec = importlib.util.spec_from_file_location("qq_cards_transport", Path("ops/qq_cards_transport.py"))
transport = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transport)


@pytest.mark.parametrize("kind,path", [("c2c", "users"), ("group", "groups")])
def test_native_wire_has_content_no_template_no_keyboard(monkeypatch, kind, path):
    captured = []
    async def post(self, url, **kwargs):
        captured.append((url, kwargs["json"]))
        return httpx.Response(200, json={"id": "reply-1"})
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    client = SimpleNamespace(base_url="https://api.sgroup.qq.com", access_token="test-token", check_access_token=AsyncMock(return_value=True), next_reply_msg_seq=AsyncMock(return_value=2), send_private_text_msg=AsyncMock(), send_group_text_msg=AsyncMock())
    result = asyncio.run(transport.send_card(client, kind, "receiver", transport.PREFIX + "课表\n\n**已完成**", msg_id="source"))
    assert result == {"id": "reply-1"}
    assert captured[0][0] == f"https://api.sgroup.qq.com/v2/{path}/receiver/messages"
    body = captured[0][1]
    assert body == {"msg_type": 2, "markdown": {"content": transport.PREFIX + "课表\n\n**已完成**"}, "msg_id": "source", "msg_seq": 2}
    client.send_private_text_msg.assert_not_awaited()
    client.send_group_text_msg.assert_not_awaited()


@pytest.mark.parametrize("status,fallback", [(400, True), (403, True), (429, False), (500, False)])
def test_explicit_rejection_falls_back_once_with_new_sequence(monkeypatch, status, fallback):
    monkeypatch.setattr(httpx.AsyncClient, "post", AsyncMock(return_value=httpx.Response(status, text="provider echoed secret")))
    sender = AsyncMock(return_value={"id": "fallback"})
    client = SimpleNamespace(base_url="https://api.sgroup.qq.com", access_token="secret", check_access_token=AsyncMock(return_value=True), next_reply_msg_seq=AsyncMock(side_effect=[1, 2]), send_private_text_msg=sender)
    if fallback:
        assert asyncio.run(transport.send_card(client, "c2c", "receiver", transport.PREFIX + "账号\n\n**需要绑定**", msg_id="source")) == {"id": "fallback"}
        assert sender.await_count == 1
        assert sender.call_args.kwargs["msg_seq"] == 2
        assert "**" not in sender.call_args.args[1]
    else:
        with pytest.raises(ValueError, match=f"HTTP {status}"):
            asyncio.run(transport.send_card(client, "c2c", "receiver", transport.PREFIX + "状态", msg_id="source"))
        sender.assert_not_awaited()


def test_timeout_does_not_send_duplicate(monkeypatch):
    monkeypatch.setattr(httpx.AsyncClient, "post", AsyncMock(side_effect=httpx.ReadTimeout("unknown result")))
    sender = AsyncMock()
    client = SimpleNamespace(base_url="https://api.sgroup.qq.com", access_token="secret", check_access_token=AsyncMock(return_value=True), next_reply_msg_seq=AsyncMock(return_value=1), send_private_text_msg=sender)
    with pytest.raises(httpx.ReadTimeout):
        asyncio.run(transport.send_card(client, "c2c", "receiver", transport.PREFIX + "状态", msg_id="source"))
    sender.assert_not_awaited()

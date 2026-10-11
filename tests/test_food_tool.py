import asyncio
import base64
import hashlib
import hmac
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from components.cli_tools.henu_food import HenuFood
from henu_plugin.food_client import FoodSettings
from henu_plugin.kit_client import KitError

SECRET = "test-food-actor-secret-at-least-32-bytes"
UID = "44444444-4444-4444-8444-444444444444"


@pytest.fixture
def food_http(request):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(405)
            self.end_headers()

        def do_DELETE(self):
            self.send_response(200)
            self.end_headers()

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if body["method"] == "notifications/initialized":
                self.send_response(202)
                self.end_headers()
                return
            if body["method"] == "initialize":
                result = {
                    "protocolVersion": body["params"]["protocolVersion"],
                    "capabilities": {},
                    "serverInfo": {"name": "fixture", "version": "1"},
                }
            elif body["method"] == "tools/list":
                result = {"tools": []}
            else:
                calls.append((body["params"], self.headers.get("Authorization")))
                if getattr(request, "param", "") == "lost_reply":
                    self.close_connection = True
                    return
                text = '我的投稿: {"posts":[]}'
                if body["params"]["name"] == "create_food_post":
                    arguments = body["params"]["arguments"]
                    text = "投稿已发布（创建即公开）: " + json.dumps(
                        {
                            "post": {
                                "id": "11111111-1111-4111-8111-111111111111",
                                "shop": {"name": "大同刀削面"},
                                "campus": "jinming",
                                "tags": ["夯"],
                                "images": [
                                    f"/api/v1/food/posts/fixture/images/{i}"
                                    for i in range(len(arguments.get("images", [])))
                                ],
                                "author": "fixture-author",
                                "hidden": False,
                                "likes": 0,
                            },
                            "request_id": "private-request-fixture",
                        },
                        ensure_ascii=False,
                    )
                    if getattr(request, "param", "") == "malformed_publication":
                        text = '投稿已发布（创建即公开）: {invalid "request_id": "private-fixture"'
                proof = body["params"].get("_meta", {}).get("henukit.dev/food-actor")
                if (
                    proof
                    and json.loads(base64.urlsafe_b64decode(proof["payload"] + "=="))[
                        "kind"
                    ]
                    == "guest"
                ):
                    text += "\n当前使用游客身份，私聊发送“绑定 HENU KIT”可将后续投稿关联到账号；无需提供 UUID。"
                result = {"content": [{"type": "text", "text": text}], "isError": False}
            raw = json.dumps(
                {"jsonrpc": "2.0", "id": body["id"], "result": result}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield (
        FoodSettings(
            f"http://127.0.0.1:{server.server_port}/mcp", "test-token", SECRET
        ),
        calls,
    )
    server.shutdown()
    server.server_close()
    thread.join()


def tool(monkeypatch, settings, sender="alice", bot="bot", hint=None):
    proxy = SimpleNamespace(
        get_query_vars=AsyncMock(return_value=hint or {"henu_sender_id": sender}),
        get_bot_uuid=AsyncMock(return_value=bot),
    )
    monkeypatch.setattr(
        "components.cli_tools.henu_food.QueryBasedAPIProxy", lambda **kwargs: proxy
    )
    monkeypatch.setattr(
        "components.cli_tools.henu_food.food_settings", lambda: settings
    )
    instance = HenuFood()
    instance.plugin = SimpleNamespace(plugin_runtime_handler=object())
    session = SimpleNamespace(
        sender_id=sender, launcher_id=sender, launcher_type="person"
    )
    return instance, session


def claims(call):
    meta = call[0]["_meta"]["henukit.dev/food-actor"]
    raw = base64.urlsafe_b64decode(meta["payload"] + "==")
    sig = base64.urlsafe_b64decode(meta["signature"] + "==")
    assert hmac.compare_digest(
        sig,
        hmac.new(
            SECRET.encode(), b"henukit-food-actor-v1\n" + raw, hashlib.sha256
        ).digest(),
    )
    return json.loads(raw)


def test_bound_then_unbound_guest_and_other_qq(monkeypatch, food_http):
    settings, calls = food_http
    monkeypatch.setattr(
        "components.cli_tools.henu_food.kit_settings", lambda: {"bot_uuid": "bot"}
    )
    resolved = []

    def resolve(config, action, payload):
        resolved.append((action, payload))
        if len(resolved) == 1:
            return {"bound": True, "user_id": UID, "display_name": "同学"}
        raise KitError("NOT_BOUND")

    monkeypatch.setattr("components.cli_tools.henu_food.kit_request", resolve)

    async def run():
        a, s = tool(monkeypatch, settings)
        bound = await a.call({"action": "mine"}, s, 1)
        assert bound["success"]
        guest = await a.call({"action": "mine"}, s, 2)
        assert guest["reply_text"].count("绑定 HENU KIT") == 1
        await a.call({"action": "mine"}, s, 3)
        b, sb = tool(monkeypatch, settings, "bob")
        await b.call({"action": "mine"}, sb, 4)

    asyncio.run(run())
    cs = [claims(c) for c in calls]
    assert cs[0]["user_id"] == UID and cs[0]["kind"] == "bound"
    assert (
        cs[1]["kind"] == "guest"
        and cs[1]["scope"] == cs[2]["scope"]
        and cs[1]["scope"] != cs[3]["scope"]
    )
    assert cs[1]["user_id"] == "" and "alice" not in json.dumps(calls)
    assert len(resolved) == 4 and resolved[0] == ("resolve", {"subject": "alice"})
    assert all(auth == "Bearer test-token" for _, auth in calls)


@pytest.mark.parametrize(
    "params",
    [
        {"action": "mine", "actor_user_id": UID},
        {"action": "mine", "_meta": {}},
        {"action": "create", "bot_uuid": "bot"},
    ],
)
def test_model_identity_rejected(monkeypatch, food_http, params):
    settings, calls = food_http
    instance, s = tool(monkeypatch, settings)
    assert not asyncio.run(instance.call(params, s, 1))["success"]
    assert not calls


def test_missing_or_conflicting_sender_is_rejected(monkeypatch, food_http):
    settings, calls = food_http
    for sender, hint in [
        ("", {"henu_sender_id": ""}),
        ("alice", {"henu_sender_id": "bob"}),
        ("alice", {"henu_sender_id": "alice", "sender_id": "bob"}),
    ]:
        instance, s = tool(monkeypatch, settings, sender, hint=hint)
        assert not asyncio.run(instance.call({"action": "mine"}, s, 1))["success"]
    assert not calls


def test_unavailable_or_wrong_bot_does_not_block_my_posts(monkeypatch, food_http):
    settings, calls = food_http
    monkeypatch.setattr(
        "components.cli_tools.henu_food.kit_settings", lambda: {"bot_uuid": "bot"}
    )

    def unavailable(*args):
        raise KitError()

    monkeypatch.setattr("components.cli_tools.henu_food.kit_request", unavailable)
    params = {"action": "mine"}
    a, s = tool(monkeypatch, settings)
    result = asyncio.run(a.call(params, s, 1))
    assert result["success"] and "暂时" in result["reply_text"]
    b, sb = tool(monkeypatch, settings, bot="other-bot")
    assert asyncio.run(b.call(params, sb, 2))["success"]
    for call in calls:
        c = claims(call)
        assert c["kind"] == "guest"
    assert claims(calls[0])["scope"] != claims(calls[1])["scope"]
    # Cross-language fixture: Go encoding/json's sorted keys and HTML escaping.
    canonical = "{}"
    assert (
        claims(calls[0])["arguments_sha256"]
        == hashlib.sha256(canonical.encode()).hexdigest()
    )


def test_public_read_has_no_binding_lookup(monkeypatch, food_http):
    settings, calls = food_http
    instance, s = tool(monkeypatch, settings)
    monkeypatch.setattr(
        "components.cli_tools.henu_food.kit_settings",
        lambda: pytest.fail("read should not resolve binding"),
    )
    assert asyncio.run(instance.call({"action": "list"}, s, 1))["success"]
    assert (
        "_meta" not in calls[0][0]
        or "henukit.dev/food-actor" not in calls[0][0]["_meta"]
    )

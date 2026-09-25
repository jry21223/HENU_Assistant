#!/usr/bin/env python3
"""Exercise the C2C send-receipt bridge in three extracted LangBot files.

Usage: python3 ops/verify-qq-receipt-route.py API_PY QQOFFICIAL_PY HANDLER_PY
       python3 ops/verify-qq-receipt-route.py --handler-only API_PY HANDLER_PY
The inputs must come from one exact running/candidate image, with the reviewed
QQ source-time patch applied to QQOFFICIAL_PY before its receipt-route patch.
No network or QQ credentials are used by this verifier.
"""

from __future__ import annotations

import ast
import asyncio
import datetime
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def isolated_method(
    path: Path, class_name: str, method_name: str, globals_: dict[str, Any]
) -> Any:
    tree = ast.parse(path.read_text())
    class_node = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    method = next(
        node
        for node in class_node.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == method_name
    )
    method.decorator_list = []
    wrapper = ast.ClassDef(
        name=class_name, bases=[], keywords=[], body=[method], decorator_list=[]
    )
    module = ast.Module(
        body=[
            ast.ImportFrom(
                module="__future__", names=[ast.alias(name="annotations")], level=0
            ),
            wrapper,
        ],
        type_ignores=[],
    )
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), globals_)
    return globals_[class_name]


def isolated_handler(path: Path, globals_: dict[str, Any]) -> Any:
    tree = ast.parse(path.read_text())
    outer = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "RuntimeConnectionHandler"
    )
    init = next(
        node
        for node in outer.body
        if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    )
    method = next(
        node
        for node in init.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "reply_message"
    )
    method.decorator_list = []
    module = ast.Module(
        body=[
            ast.ImportFrom(
                module="__future__", names=[ast.alias(name="annotations")], level=0
            ),
            method,
        ],
        type_ignores=[],
    )
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), globals_)
    return globals_["reply_message"]


def receipt_normalizer(path: Path) -> Any:
    namespace: dict[str, Any] = {"re": re, "datetime": datetime}
    tree = ast.parse(path.read_text())
    function = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "normalize_qq_c2c_send_receipt"
        ),
        None,
    )
    if function is None:
        # The unpatched source still needs to reach the expected red assertion.
        return lambda value: value
    module = ast.Module(
        body=[
            ast.ImportFrom(
                module="__future__", names=[ast.alias(name="annotations")], level=0
            ),
            function,
        ],
        type_ignores=[],
    )
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    return namespace["normalize_qq_c2c_send_receipt"]


def quote_extractor(path: Path) -> Any:
    tree = ast.parse(path.read_text())
    function = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "extract_qq_c2c_quote_ref_idx"
        ),
        None,
    )
    if function is None:
        return lambda *_args: None
    module = ast.Module(
        body=[
            ast.ImportFrom(
                module="__future__", names=[ast.alias(name="annotations")], level=0
            ),
            function,
        ],
        type_ignores=[],
    )
    namespace: dict[str, Any] = {}
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    return namespace["extract_qq_c2c_quote_ref_idx"]


def verify_websocket_gate(path: Path) -> None:
    tree = ast.parse(path.read_text())
    adapter = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "QQOfficialAdapter"
    )
    websocket = next(
        node
        for node in adapter.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_run_websocket"
    )
    calls = [
        node
        for node in ast.walk(websocket)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get_message"
    ]
    require(len(calls) == 1, "QQ WebSocket quote path is missing or ambiguous")
    trusted = [
        keyword.value
        for keyword in calls[0].keywords
        if keyword.arg == "trusted_websocket"
    ]
    require(
        len(trusted) == 1
        and isinstance(trusted[0], ast.Constant)
        and trusted[0].value is True,
        "QQ WebSocket event did not explicitly mark authenticated quote provenance",
    )


async def verify_unified_webhook_gate(path: Path) -> None:
    namespace: dict[str, Any] = {}
    adapter_class = isolated_method(
        path, "QQOfficialAdapter", "handle_unified_webhook", namespace
    )
    adapter = adapter_class()

    class Bot:
        calls = 0

        async def handle_unified_webhook(
            self, _request: Any
        ) -> tuple[dict[str, str], int]:
            self.calls += 1
            return {"code": "ok"}, 200

    adapter.bot = Bot()
    for disabled in (False, None, "false"):
        adapter.enable_webhook = disabled
        result = await adapter.handle_unified_webhook("bot-uuid", "", object())
        require(
            result[1] == 403, "WebSocket Bot accepted unauthenticated unified webhook"
        )
        require(adapter.bot.calls == 0, "WebSocket Bot forwarded an unsigned webhook")

    adapter.enable_webhook = True
    result = await adapter.handle_unified_webhook("bot-uuid", "", object())
    require(
        result[1] == 200 and adapter.bot.calls == 1,
        "configured webhook route regressed",
    )


class Plain:
    def __init__(self, text: str):
        self.text = text


class Image:
    pass


class FakeHTTPResponse:
    def __init__(self, body: Any, status_code: int = 200):
        self.body = body
        self.status_code = status_code
        self.json_calls = 0

    def json(self) -> Any:
        self.json_calls += 1
        if isinstance(self.body, ValueError):
            raise self.body
        return self.body


class FakeHTTPClient:
    response: FakeHTTPResponse

    async def __aenter__(self) -> FakeHTTPClient:
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    async def post(self, *_args: Any, **_kwargs: Any) -> FakeHTTPResponse:
        return self.response


async def verify_quote_extraction(path: Path) -> None:
    extract = quote_extractor(path)
    ref_idx = "R" * 135

    def quote_data(**changes: Any) -> dict[str, Any]:
        data: dict[str, Any] = {
            "message_type": 103,
            "message_scene": {"ext": [f"ref_msg_idx={ref_idx}"]},
            "msg_elements": [{"msg_idx": ref_idx}],
        }
        data.update(changes)
        return data

    require(
        extract("C2C_MESSAGE_CREATE", quote_data()) == ref_idx,
        "valid C2C type-103 quote was lost",
    )
    require(
        extract("C2C_MESSAGE_CREATE", quote_data(message_scene={"ext": []})) == ref_idx,
        "valid msg_elements quote was lost",
    )
    require(
        extract("C2C_MESSAGE_CREATE", quote_data(msg_elements=[])) == ref_idx,
        "valid message_scene quote was lost",
    )
    require(
        extract(
            "C2C_MESSAGE_CREATE",
            quote_data(
                message_scene={"ext": [f"ref_msg_idx={ref_idx}", f"msg_idx={ref_idx}"]}
            ),
        )
        == ref_idx,
        "matching ext aliases were lost",
    )
    for scene, data in (
        ("DIRECT_MESSAGE_CREATE", quote_data()),
        ("GROUP_AT_MESSAGE_CREATE", quote_data()),
        ("C2C_MESSAGE_CREATE", quote_data(message_type=0)),
        ("C2C_MESSAGE_CREATE", quote_data(message_type=True)),
        ("C2C_MESSAGE_CREATE", quote_data(message_type="103")),
        (
            "C2C_MESSAGE_CREATE",
            quote_data(
                message_scene={"ext": [f"ref_msg_idx={ref_idx}", "msg_idx=conflict"]}
            ),
        ),
        (
            "C2C_MESSAGE_CREATE",
            quote_data(
                message_scene={
                    "ext": [f"ref_msg_idx={ref_idx}", f"ref_msg_idx={ref_idx}"]
                }
            ),
        ),
        (
            "C2C_MESSAGE_CREATE",
            quote_data(msg_elements=[{"msg_idx": ref_idx}, {"msg_idx": ref_idx}]),
        ),
        ("C2C_MESSAGE_CREATE", quote_data(msg_elements=[{"msg_idx": "conflict"}])),
        (
            "C2C_MESSAGE_CREATE",
            quote_data(message_scene={"ext": ["ref_msg_idx=bad value"]}),
        ),
        (
            "C2C_MESSAGE_CREATE",
            quote_data(message_scene={"ext": ["ref_msg_idx=" + "R" * 257]}),
        ),
        ("C2C_MESSAGE_CREATE", quote_data(message_scene={"ext": [None]})),
        (
            "C2C_MESSAGE_CREATE",
            quote_data(message_scene={"ext": "ref_msg_idx=" + ref_idx}),
        ),
        ("C2C_MESSAGE_CREATE", quote_data(msg_elements=[{"msg_idx": None}])),
        ("C2C_MESSAGE_CREATE", quote_data(message_scene={"ext": []}, msg_elements=[])),
    ):
        require(
            extract(scene, data) is None,
            "ambiguous, malformed, or non-C2C quote was accepted",
        )

    namespace = {"extract_qq_c2c_quote_ref_idx": extract}
    client_class = isolated_method(path, "QQOfficialClient", "get_message", namespace)
    client = client_class()
    event: dict[str, Any] = {
        "t": "C2C_MESSAGE_CREATE",
        "d": {
            **quote_data(),
            "id": "inbound-id",
            "content": "确认",
            "timestamp": "2026-09-25T12:35:00+08:00",
            "author": {"user_openid": "fixture-sender"},
            "qq_websocket_verified": True,
        },
    }
    converted = await client.get_message(event)
    require(
        "qq_quote_ref_idx" not in converted
        and "qq_quote_present" not in converted
        and "qq_websocket_verified" not in converted,
        "unsigned webhook path carried C2C provenance",
    )
    converted = await client.get_message(event, trusted_websocket=True)
    require(
        converted.get("qq_quote_ref_idx") == ref_idx
        and converted.get("qq_quote_present") is True
        and converted.get("qq_websocket_verified") is True,
        "QQ get_message discarded authenticated C2C provenance or quote index",
    )
    require(
        "message_scene" not in converted and "msg_elements" not in converted,
        "QQ get_message leaked raw quote payload",
    )
    event["t"] = "DIRECT_MESSAGE_CREATE"
    converted = await client.get_message(event, trusted_websocket=True)
    require(
        "qq_quote_ref_idx" not in converted
        and "qq_quote_present" not in converted
        and "qq_websocket_verified" not in converted,
        "QQ get_message carried provenance to channel DIRECT",
    )
    event["t"] = "C2C_MESSAGE_CREATE"
    event["d"]["message_type"] = 0
    converted = await client.get_message(event, trusted_websocket=True)
    require(
        converted.get("qq_websocket_verified") is True
        and "qq_quote_present" not in converted
        and "qq_quote_ref_idx" not in converted,
        "ordinary authenticated C2C command lost provenance or gained a quote",
    )
    event["d"]["message_type"] = 103
    event["d"]["msg_elements"] = [{"msg_idx": "conflict"}]
    converted = await client.get_message(event, trusted_websocket=True)
    require(
        converted.get("qq_websocket_verified") is True
        and converted.get("qq_quote_present") is True
        and "qq_quote_ref_idx" not in converted,
        "conflicting quote lost the fail-closed quote-present marker",
    )
    event["d"]["message_scene"] = {"ext": []}
    event["d"]["msg_elements"] = []
    converted = await client.get_message(event, trusted_websocket=True)
    require(
        converted.get("qq_quote_present") is True
        and "qq_quote_ref_idx" not in converted,
        "missing quote index lost the fail-closed quote-present marker",
    )


async def verify_api(path: Path) -> None:
    namespace: dict[str, Any] = {
        "httpx": SimpleNamespace(AsyncClient=FakeHTTPClient),
        "normalize_qq_c2c_send_receipt": receipt_normalizer(path),
    }
    client_class = isolated_method(
        path, "QQOfficialClient", "send_private_text_msg", namespace
    )
    client = client_class()
    client.base_url = "https://fixture.invalid"
    client.access_token = "fixture"

    async def yes() -> bool:
        return True

    async def sequence(_anchor: str) -> int:
        return 1

    error_logs: list[str] = []

    async def record_error(message: str) -> None:
        error_logs.append(message)

    client.check_access_token = yes
    client.next_reply_msg_seq = sequence
    client.logger = SimpleNamespace(error=record_error)

    ref_idx = "R" * 135
    receipt = {
        "id": "qq-outbound-123",
        "timestamp": "2026-09-25T12:34:56+08:00",
        "ext_info": {"ref_idx": ref_idx, "private": "discard-me"},
        "extra": "discard-me",
    }
    FakeHTTPClient.response = FakeHTTPResponse(receipt)
    result = await client.send_private_text_msg("sender", "preview", "inbound-id")
    require(
        result
        == {"id": receipt["id"], "timestamp": receipt["timestamp"], "ref_idx": ref_idx},
        "200 C2C reply did not return only validated id/timestamp/ref_idx",
    )

    utc_receipt = {
        "id": "qq-outbound-utc",
        "timestamp": "2026-09-25T04:34:56.123Z",
        "ext_info": {"ref_idx": ref_idx},
    }
    FakeHTTPClient.response = FakeHTTPResponse(utc_receipt)
    result = await client.send_private_text_msg("sender", "preview", "inbound-id")
    require(
        result
        == {
            "id": utc_receipt["id"],
            "timestamp": utc_receipt["timestamp"],
            "ref_idx": ref_idx,
        },
        "valid UTC QQ server timestamp was dropped",
    )

    no_quote_receipt = {
        "id": "qq-outbound-no-quote",
        "timestamp": "2026-09-25T04:34:56.123Z",
    }
    FakeHTTPClient.response = FakeHTTPResponse(no_quote_receipt)
    result = await client.send_private_text_msg("sender", "unlink", "inbound-id")
    require(result == no_quote_receipt, "valid unquoted unlink receipt was dropped")

    for invalid in (
        None,
        {},
        {"id": receipt["id"]},
        {"timestamp": receipt["timestamp"]},
        {
            "id": receipt["id"],
            "timestamp": receipt["timestamp"],
            "ext_info": {"ref_idx": ""},
        },
        {
            "id": receipt["id"],
            "timestamp": receipt["timestamp"],
            "ext_info": {"ref_idx": "has space"},
        },
        {
            "id": receipt["id"],
            "timestamp": receipt["timestamp"],
            "ext_info": {"ref_idx": "R" * 257},
        },
        {
            "id": receipt["id"],
            "timestamp": receipt["timestamp"],
            "ext_info": "malformed",
        },
        {
            "id": receipt["id"],
            "timestamp": receipt["timestamp"],
            "ext_info": {"ref_idx": ref_idx},
            "ref_idx": "conflict",
        },
        {
            "id": receipt["id"],
            "timestamp": receipt["timestamp"],
            "ext_info": {"ref_idx": None},
            "ref_idx": ref_idx,
        },
        {
            "id": receipt["id"],
            "timestamp": receipt["timestamp"],
            "ext_info": {"ref_idx": ref_idx},
            "ref_idx": None,
        },
        {"id": "", "timestamp": receipt["timestamp"]},
        {"id": 123, "timestamp": receipt["timestamp"]},
        {"id": "qq\nmalformed", "timestamp": receipt["timestamp"]},
        {"id": "q" * 257, "timestamp": receipt["timestamp"]},
        {"id": receipt["id"], "timestamp": "2026-09-25T12:34:56"},
        {"id": receipt["id"], "timestamp": "garbage"},
        [receipt],
        ValueError("invalid JSON"),
    ):
        FakeHTTPClient.response = FakeHTTPResponse(invalid)
        result = await client.send_private_text_msg("sender", "preview", "inbound-id")
        require(result is None, "missing or malformed QQ send receipt was accepted")

    private_token = "fixture-qq-binding-token-0123456789abcdef0123456789abcdef"
    error_response = FakeHTTPResponse(
        {"error": "echoed https://kit.example/bind?token=" + private_token},
        status_code=400,
    )
    FakeHTTPClient.response = error_response
    try:
        await client.send_private_text_msg(
            "sender", "https://kit.example/bind?token=" + private_token, "inbound-id"
        )
    except ValueError as exc:
        require(
            private_token not in str(exc), "QQ error exception leaked a binding token"
        )
        require("HTTP 400" in str(exc), "QQ error exception lost the HTTP status")
    else:
        raise AssertionError("QQ non-200 send failed to raise an error")
    require(
        error_response.json_calls == 0,
        "QQ non-200 send parsed a private provider error body",
    )
    require(
        error_logs == ["Failed to send private message: HTTP 400"],
        "QQ non-200 log leaked provider content or omitted the HTTP status",
    )


async def verify_adapter(path: Path, normalizer: Any) -> None:
    class EventConverter:
        @staticmethod
        async def yiri2target(event: Any) -> Any:
            return event

    class MessageConverter:
        @staticmethod
        async def yiri2target(chain: list[Any]) -> list[dict[str, str]]:
            return [
                {"type": "text", "content": part.text}
                if type(part) is Plain
                else {"type": "image", "url": "https://fixture.invalid/image"}
                for part in chain
            ]

    class Bot:
        def __init__(self):
            self.sent = 0
            self.receipt: Any = {
                "id": "qq-outbound-123",
                "timestamp": "2026-09-25T12:34:56+08:00",
                "ref_idx": "R" * 135,
            }

        async def send_private_text_msg(self, *_args: Any) -> Any:
            self.sent += 1
            return self.receipt

        async def send_group_text_msg(self, *_args: Any) -> Any:
            self.sent += 1
            return self.receipt

        async def send_channle_private_text_msg(self, *_args: Any) -> Any:
            self.sent += 1
            return self.receipt

        async def send_image_msg(self, *_args: Any, **_kwargs: Any) -> Any:
            self.sent += 1
            return self.receipt

    namespace = {
        "QQOfficialEventConverter": EventConverter,
        "QQOfficialMessageConverter": MessageConverter,
        "platform_message": SimpleNamespace(Plain=Plain),
        "normalize_qq_c2c_send_receipt": normalizer,
    }
    adapter_class = isolated_method(
        path, "QQOfficialAdapter", "reply_message", namespace
    )
    adapter = adapter_class()
    adapter.bot = Bot()

    def event(scene: str) -> Any:
        return SimpleNamespace(
            t=scene,
            user_openid="sender",
            group_openid="group",
            guild_id="guild",
            d_id="inbound-id",
        )

    expected = {"qq_c2c_receipt": adapter.bot.receipt}
    result = await adapter.reply_message(
        event("C2C_MESSAGE_CREATE"), [Plain("preview")]
    )
    require(
        result == expected and adapter.bot.sent == 1,
        "single C2C Plain reply did not propagate receipt",
    )

    for scene, chain in (
        ("C2C_MESSAGE_CREATE", [Plain("one"), Plain("two")]),
        ("C2C_MESSAGE_CREATE", [Image()]),
        ("GROUP_AT_MESSAGE_CREATE", [Plain("group")]),
        ("DIRECT_MESSAGE_CREATE", [Plain("direct")]),
    ):
        result = await adapter.reply_message(event(scene), chain)
        require(result is None, "non-single-Plain or non-C2C reply leaked receipt")

    adapter.bot.receipt = None
    result = await adapter.reply_message(
        event("C2C_MESSAGE_CREATE"), [Plain("preview")]
    )
    require(result is None, "missing QQ receipt was propagated")

    adapter.bot.receipt = {"id": "qq-outbound-123", "timestamp": "garbage"}
    result = await adapter.reply_message(
        event("C2C_MESSAGE_CREATE"), [Plain("preview")]
    )
    require(result is None, "malformed QQ receipt was propagated")

    adapter.bot.receipt = {
        "id": "qq-outbound-no-quote",
        "timestamp": "2026-09-25T04:34:56.123Z",
    }
    result = await adapter.reply_message(event("C2C_MESSAGE_CREATE"), [Plain("unlink")])
    require(
        result == {"qq_c2c_receipt": adapter.bot.receipt},
        "unquoted unlink receipt was dropped",
    )


async def verify_handler(path: Path, normalizer: Any) -> None:
    class FakeActionResponse:
        @staticmethod
        def success(data: dict[str, Any]) -> Any:
            return SimpleNamespace(data=data)

        @staticmethod
        def error(message: str) -> Any:
            raise AssertionError(message)

    class MessageChain:
        def __init__(self, parts: list[Any]):
            self.parts = parts

        @staticmethod
        def model_validate(value: Any) -> Any:
            return value

        def model_dump(self, **_kwargs: Any) -> dict[str, Any]:
            return {
                "root": [
                    {"type": "Plain", "text": part.text}
                    for part in self.parts
                    if type(part) is Plain
                ]
            }

        def __len__(self) -> int:
            return len(self.parts)

        def __getitem__(self, index: int) -> Any:
            return self.parts[index]

    namespace = {
        "handler": SimpleNamespace(ActionResponse=FakeActionResponse),
        "platform_message": SimpleNamespace(MessageChain=MessageChain, Plain=Plain),
        "normalize_qq_c2c_send_receipt": normalizer,
    }
    reply_message = isolated_handler(path, namespace)

    class Adapter:
        value: Any = None

        async def reply_message(self, *_args: Any) -> Any:
            return self.value

    adapter = Adapter()
    query = SimpleNamespace(
        adapter=adapter,
        message_event=SimpleNamespace(
            source_platform_object=SimpleNamespace(t="C2C_MESSAGE_CREATE")
        ),
    )
    debug_messages: list[str] = []
    ap = SimpleNamespace(
        query_pool=SimpleNamespace(cached_queries={1: query}),
        logger=SimpleNamespace(debug=debug_messages.append),
    )
    self = SimpleNamespace(ap=ap)
    namespace["self"] = self
    private_link_token = "fixture-qq-binding-token-0123456789abcdef0123456789abcdef"
    chain = MessageChain(
        [Plain("https://kit.example/bind?token=" + private_link_token)]
    )
    data = {"query_id": 1, "message_chain": chain, "quote_origin": False}
    receipt = {
        "id": "qq-outbound-123",
        "timestamp": "2026-09-25T12:34:56+08:00",
        "ref_idx": "R" * 135,
    }
    adapter.value = {"qq_c2c_receipt": receipt}
    result = await reply_message(data)
    require(
        all(private_link_token not in line for line in debug_messages),
        "REPLY_MESSAGE wrote private reply body or binding token to debug logs",
    )
    require(
        result.data == adapter.value,
        "handler did not return whitelisted receipt in ActionResponse.data",
    )

    for invalid in (
        None,
        {"other": receipt},
        {"qq_c2c_receipt": None},
        {"qq_c2c_receipt": {"id": receipt["id"], "timestamp": "garbage"}},
        {"qq_c2c_receipt": receipt, "extra": "secret"},
    ):
        adapter.value = invalid
        result = await reply_message(data)
        require(result.data == {}, "handler returned non-whitelisted adapter data")

    adapter.value = {"qq_c2c_receipt": receipt}
    for scene, parts in (
        ("DIRECT_MESSAGE_CREATE", [Plain("direct")]),
        ("GROUP_AT_MESSAGE_CREATE", [Plain("group")]),
        ("C2C_MESSAGE_CREATE", [Plain("one"), Plain("two")]),
        ("C2C_MESSAGE_CREATE", [Image()]),
    ):
        query.message_event.source_platform_object.t = scene
        data["message_chain"] = MessageChain(parts)
        result = await reply_message(data)
        require(
            result.data == {},
            "handler returned C2C receipt for wrong scene or reply shape",
        )

    query.message_event.source_platform_object.t = "C2C_MESSAGE_CREATE"
    data["message_chain"] = MessageChain([Plain("unlink")])
    adapter.value = {
        "qq_c2c_receipt": {"id": "qq-unlink", "timestamp": "2026-09-25T04:34:56.123Z"}
    }
    result = await reply_message(data)
    require(
        result.data == adapter.value, "handler dropped valid unquoted unlink receipt"
    )


async def verify(paths: list[Path]) -> None:
    await verify_api(paths[0])
    await verify_quote_extraction(paths[0])
    verify_websocket_gate(paths[1])
    await verify_unified_webhook_gate(paths[1])
    normalizer = receipt_normalizer(paths[0])
    await verify_adapter(paths[1], normalizer)
    await verify_handler(paths[2], normalizer)


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--handler-only":
        asyncio.run(
            verify_handler(Path(sys.argv[3]), receipt_normalizer(Path(sys.argv[2])))
        )
        print(
            "QQ reply handler did not log private bodies and accepted only whitelisted receipts"
        )
    elif len(sys.argv) == 4:
        asyncio.run(verify([Path(path) for path in sys.argv[1:]]))
        print(
            "QQ C2C receipt bridge accepted only validated single-Plain send receipts"
        )
    else:
        raise SystemExit(
            "usage: verify-qq-receipt-route.py [--handler-only] API_PY [QQOFFICIAL_PY] HANDLER_PY"
        )

#!/usr/bin/env python3
"""Verify the main adapter first hop and runtime C2C provenance persistence.

Usage: python3 ops/verify-qq-sdk-quote.py --main-first-hop MAIN_SDK_ROOT QQOFFICIAL_PY
       python3 ops/verify-qq-sdk-quote.py RUNTIME_SDK_ROOT QQOFFICIAL_PY

The main SDK root must be the exact unpatched running-image site-packages.
The runtime root must have qq-binding-sdk-quote.patch applied to events.py.
The verifier uses no network, QQ account, or credentials.
"""

from __future__ import annotations

import ast
import datetime
import hashlib
import sys
from pathlib import Path
from typing import Any


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def load_adapter_friend_message(
    path: Path, platform_events: Any, platform_message: Any
) -> type:
    tree = ast.parse(path.read_text())
    node = next(
        item
        for item in tree.body
        if isinstance(item, ast.ClassDef) and item.name == "QQFriendMessage"
    )
    module = ast.Module(body=[node], type_ignores=[])
    namespace = {
        "platform_events": platform_events,
        "platform_message": platform_message,
    }
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    return namespace["QQFriendMessage"]


def verify(sdk_root: Path, adapter_path: Path, *, main_first_hop: bool = False) -> None:
    sys.path.insert(0, str(sdk_root.resolve()))
    from langbot_plugin.api.entities.builtin.platform import entities, events, message
    from langbot_plugin.api.entities.context import EventContext
    from langbot_plugin.api.entities.events import PersonMessageReceived

    expected_events = (
        sdk_root / "langbot_plugin/api/entities/builtin/platform/events.py"
    )
    require(
        Path(events.__file__).resolve() == expected_events.resolve(),
        "unexpected SDK was imported",
    )
    if main_first_hop:
        require(
            hashlib.sha256(expected_events.read_bytes()).hexdigest()
            == "b14f16fb3752a980791f203af5f0705290701a977723794c7677b8fc2bcf9e7e",
            "main image SDK source differs from reviewed exact base",
        )
    qq_friend_message = load_adapter_friend_message(adapter_path, events, message)
    ref_idx = "R" * 135
    chain = message.MessageChain(
        [
            message.Source(
                id="inbound-id", time=datetime.datetime.now(datetime.timezone.utc)
            ),
            message.Plain(text="确认"),
        ]
    )
    require(
        str(chain) == "确认",
        "quote bridge changed the existing Plain confirmation text",
    )
    friend = entities.Friend(id="test-openid", nickname="C2C_MESSAGE_CREATE", remark="")
    raw_source = {
        "t": "C2C_MESSAGE_CREATE",
        "d_id": "inbound-id",
        "qq_websocket_verified": True,
        "qq_quote_present": True,
        "qq_quote_ref_idx": ref_idx,
        "content": "never-cross-sdk-boundary",
        "user_openid": "never-cross-sdk-boundary",
    }
    message_event = qq_friend_message(
        sender=friend,
        message_chain=chain,
        time=1,
        source_platform_object=raw_source,
    )
    expected = {
        "t": "C2C_MESSAGE_CREATE",
        "d_id": "inbound-id",
        "qq_websocket_verified": True,
        "qq_quote_present": True,
        "qq_quote_ref_idx": ref_idx,
    }
    first_dump = message_event.model_dump()
    require(
        first_dump.get("source_platform_object") == expected,
        "adapter first-hop whitelist failed",
    )
    require(
        "never-cross-sdk-boundary" not in repr(first_dump),
        "adapter leaked raw QQ event fields",
    )
    if main_first_hop:
        return

    event = PersonMessageReceived(
        launcher_type="person",
        launcher_id="test-openid",
        sender_id="test-openid",
        message_event=message_event,
        message_chain=chain,
    )
    context = EventContext(query_id=1, event_name="PersonMessageReceived", event=event)
    for hop in range(3):
        context = EventContext.model_validate(context.model_dump())
        source = context.event.message_event.source_platform_object
        require(source == expected, f"QQ quote marker lost after plugin hop {hop + 1}")
        require(
            str(context.event.message_chain) == "确认",
            "plugin hop altered confirmation text",
        )
        require(
            "never-cross-sdk-boundary" not in repr(context.model_dump()),
            "plugin hop leaked raw QQ fields",
        )

    for invalid_source in (
        {
            "t": "DIRECT_MESSAGE_CREATE",
            "d_id": "inbound-id",
            "qq_websocket_verified": True,
        },
        {
            "t": "C2C_MESSAGE_CREATE",
            "d_id": "inbound-id",
            "qq_websocket_verified": False,
        },
        {
            "t": "C2C_MESSAGE_CREATE",
            "d_id": "inbound-id",
            "qq_quote_ref_idx": ref_idx,
        },
        {
            "t": "C2C_MESSAGE_CREATE",
            "d_id": "inbound-id",
            "qq_websocket_verified": True,
            "qq_quote_present": False,
        },
        {**expected, "unexpected": "secret"},
        None,
    ):
        item = events.FriendMessage(
            sender=friend,
            message_chain=chain,
            source_platform_object=invalid_source,
        )
        require(
            "source_platform_object" not in item.model_dump(),
            "SDK retained invalid or extra QQ source fields",
        )

    mismatched = qq_friend_message(
        sender=friend,
        message_chain=chain,
        source_platform_object={
            **expected,
            "d_id": "other-id",
        },
    )
    require(
        "source_platform_object" not in mismatched.model_dump(),
        "adapter accepted mismatched Source ID",
    )

    malformed_quote = {**expected, "qq_quote_ref_idx": "bad value"}
    malformed = qq_friend_message(
        sender=friend,
        message_chain=chain,
        source_platform_object=malformed_quote,
    )
    expected_no_index = {
        key: value for key, value in expected.items() if key != "qq_quote_ref_idx"
    }
    require(
        malformed.model_dump().get("source_platform_object") == expected_no_index,
        "malformed quote index erased the fail-closed quote-present marker",
    )
    malformed_event = PersonMessageReceived(
        launcher_type="person",
        launcher_id="test-openid",
        sender_id="test-openid",
        message_event=malformed,
        message_chain=chain,
    )
    malformed_context = EventContext(
        query_id=3, event_name="PersonMessageReceived", event=malformed_event
    )
    for hop in range(3):
        malformed_context = EventContext.model_validate(malformed_context.model_dump())
        require(
            malformed_context.event.message_event.source_platform_object
            == expected_no_index,
            f"malformed quote marker lost after plugin hop {hop + 1}",
        )

    no_quote_source = {
        "t": "C2C_MESSAGE_CREATE",
        "d_id": "inbound-id",
        "qq_websocket_verified": True,
    }
    no_quote = qq_friend_message(
        sender=friend,
        message_chain=chain,
        source_platform_object=no_quote_source,
    )
    plain_event = PersonMessageReceived(
        launcher_type="person",
        launcher_id="test-openid",
        sender_id="test-openid",
        message_event=no_quote,
        message_chain=chain,
    )
    plain_context = EventContext(
        query_id=2, event_name="PersonMessageReceived", event=plain_event
    )
    for hop in range(3):
        plain_context = EventContext.model_validate(plain_context.model_dump())
        require(
            plain_context.event.message_event.source_platform_object == no_quote_source,
            f"plain authenticated QQ C2C marker lost after plugin hop {hop + 1}",
        )

    group = entities.Group(
        id="group", name="group", permission=entities.Permission.Member
    )
    member = entities.GroupMember(
        id="sender",
        member_name="sender",
        permission="MEMBER",
        group=group,
        special_title="",
    )
    group_event = events.GroupMessage(
        sender=member, message_chain=chain, source_platform_object=expected
    )
    require(
        "source_platform_object" not in group_event.model_dump(),
        "SDK changed group source serialization",
    )


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--main-first-hop":
        verify(Path(sys.argv[2]), Path(sys.argv[3]), main_first_hop=True)
        print("exact main-image SDK passed adapter first-hop whitelist")
    elif len(sys.argv) == 3:
        verify(Path(sys.argv[1]), Path(sys.argv[2]))
        print(
            "QQ C2C provenance survived three plugin hops with no raw-event or Plain-text leakage"
        )
    else:
        raise SystemExit(
            "usage: verify-qq-sdk-quote.py [--main-first-hop] SDK_ROOT QQOFFICIAL_PY"
        )

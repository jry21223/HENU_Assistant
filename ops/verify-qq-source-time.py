#!/usr/bin/env python3
"""Check the reviewed QQ adapter against the original QQ message timestamp.

Usage: python3 ops/verify-qq-source-time.py /path/to/qqofficial.py
The path must be a copy from the exact running or candidate LangBot image.
"""

from __future__ import annotations

import ast
import asyncio
import datetime
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def converter_class(tree: ast.Module, name: str, namespace: dict[str, Any]) -> Any:
    original = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == name
    )
    method = next(
        node
        for node in original.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "target2yiri"
    )
    isolated = ast.ClassDef(
        name=name,
        bases=[],
        keywords=[],
        body=[method],
        decorator_list=[],
    )
    module = ast.Module(
        body=[
            ast.ImportFrom(
                module="__future__", names=[ast.alias(name="annotations")], level=0
            ),
            isolated,
        ],
        type_ignores=[],
    )
    exec(
        compile(ast.fix_missing_locations(module), "<qq-converter-smoke>", "exec"),
        namespace,
    )
    return namespace[name]


async def verify(path: Path) -> None:
    tree = ast.parse(path.read_text())
    namespace = {
        "datetime": datetime,
        "platform_message": SimpleNamespace(
            Source=lambda **fields: SimpleNamespace(**fields),
            Plain=lambda **fields: SimpleNamespace(**fields),
            MessageChain=lambda items: SimpleNamespace(root=items),
        ),
        "platform_entities": SimpleNamespace(
            Friend=lambda **fields: SimpleNamespace(**fields)
        ),
        "platform_events": SimpleNamespace(
            FriendMessage=lambda **fields: SimpleNamespace(**fields)
        ),
        "image": SimpleNamespace(),
    }
    message_converter = converter_class(tree, "QQOfficialMessageConverter", namespace)
    event_converter = converter_class(tree, "QQOfficialEventConverter", namespace)
    namespace["QQOfficialMessageConverter"] = message_converter

    original_time = "2026-01-02T03:04:05+0800"
    event = SimpleNamespace(
        t="C2C_MESSAGE_CREATE",
        timestamp=original_time,
        content="确认",
        d_id="original-qq-source-id",
        attachments=None,
        content_type=None,
        user_openid="fixture-qq-sender",
    )
    expected = datetime.datetime.strptime(
        original_time, "%Y-%m-%dT%H:%M:%S%z"
    ).timestamp()
    result = await event_converter.target2yiri(event)
    source = result.message_chain.root[0]
    require(source.id == event.d_id, "QQ Source ID changed")
    require(
        source.time.timestamp() == expected,
        "Source.time was rebuilt from replay receipt time",
    )
    require(result.time == int(expected), "FriendMessage time changed")
    require(result.sender.id == event.user_openid, "C2C sender ID changed")
    require(
        result.sender.nickname == "C2C_MESSAGE_CREATE",
        "C2C event provenance is unavailable to the plugin",
    )

    before = datetime.datetime.now().timestamp()
    direct = SimpleNamespace(
        **{**vars(event), "t": "DIRECT_MESSAGE_CREATE", "guild_id": "fixture-guild"}
    )
    other = await event_converter.target2yiri(direct)
    after = datetime.datetime.now().timestamp()
    require(
        before <= other.message_chain.root[0].time.timestamp() <= after,
        "non-C2C Source time changed",
    )
    require(other.sender.id == direct.guild_id, "DIRECT sender ID changed")
    require(
        other.sender.nickname == "DIRECT_MESSAGE_CREATE",
        "DIRECT event was mistaken for a C2C message",
    )
    require(getattr(other, "time", None) is None, "DIRECT time contract changed")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: verify-qq-source-time.py /path/to/qqofficial.py")
    asyncio.run(verify(Path(sys.argv[1])))
    print("QQ Source preserves original C2C time and scene provenance")

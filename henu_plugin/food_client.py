"""Food MCP transport and authenticated host identity, never model identity."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
import secrets
import shlex
import time
from urllib.parse import urlsplit

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from henu_plugin.kit_client import ENV_FILE


@dataclass(frozen=True)
class FoodSettings:
    url: str
    access_token: str
    context_secret: str


def food_settings() -> FoodSettings:
    keys = {
        "HENU_FOOD_MCP_URL",
        "HENU_FOOD_MCP_ACCESS_TOKEN",
        "HENU_FOOD_CONTEXT_SECRET",
    }
    values = {}
    try:
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            key, sep, raw = line.partition("=")
            if sep and key.strip() in keys:
                parts = shlex.split(raw, comments=True)
                values[key.strip()] = parts[0] if parts else ""
    except FileNotFoundError:
        pass
    values = {key: os.environ.get(key, values.get(key, "")).strip() for key in keys}
    url = values["HENU_FOOD_MCP_URL"] or "https://henukit.cn/mcp/food"
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or "?" in url
        or "#" in url
        or not values["HENU_FOOD_MCP_ACCESS_TOKEN"]
        or len(values["HENU_FOOD_CONTEXT_SECRET"]) < 32
    ):
        raise ValueError("incomplete Food configuration")
    return FoodSettings(
        url, values["HENU_FOOD_MCP_ACCESS_TOKEN"], values["HENU_FOOD_CONTEXT_SECRET"]
    )


def actor_metadata(
    settings: FoodSettings,
    tool: str,
    args: dict,
    bot: str,
    sender: str,
    account: dict | None,
    *,
    nonce: str | None = None,
) -> dict:
    # Match Go encoding/json: sorted keys, UTF-8 and HTML/U+2028/U+2029 escaping.
    canonical = json.dumps(
        args, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    for literal, escaped in [
        ("<", "\\u003c"),
        (">", "\\u003e"),
        ("&", "\\u0026"),
        ("\u2028", "\\u2028"),
        ("\u2029", "\\u2029"),
    ]:
        canonical = canonical.replace(literal, escaped)
    scope_input = json.dumps(
        [bot, sender], ensure_ascii=False, separators=(",", ":")
    ).encode()
    scope = hmac.new(
        settings.context_secret.encode(),
        b"henukit-food-scope-v1\n" + scope_input,
        hashlib.sha256,
    ).hexdigest()
    claims = {
        "tool": tool,
        "arguments_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
        "scope": scope,
        "kind": "bound" if account else "guest",
        "user_id": account["user_id"] if account else "",
        "display_name": account["display_name"] if account else "",
        "issued_at": int(time.time()),
        "nonce": nonce or secrets.token_urlsafe(24),
    }
    raw = json.dumps(claims, ensure_ascii=False, separators=(",", ":")).encode()
    signature = hmac.new(
        settings.context_secret.encode(),
        b"henukit-food-actor-v1\n" + raw,
        hashlib.sha256,
    ).digest()
    return {
        "henukit.dev/food-actor": {
            "payload": base64.urlsafe_b64encode(raw).decode().rstrip("="),
            "signature": base64.urlsafe_b64encode(signature).decode().rstrip("="),
        }
    }


async def call_food(
    settings: FoodSettings, tool: str, arguments: dict, meta: dict | None
) -> dict:
    return await asyncio.wait_for(
        _call_food(settings, tool, arguments, meta), timeout=35
    )


async def _call_food(
    settings: FoodSettings, tool: str, arguments: dict, meta: dict | None
) -> dict:
    # Use the SDK's native client for both MCP 1.x/httpx and 2.x/httpx2.
    # Its default follows redirects; keep credentials on the configured origin.
    async with create_mcp_http_client(
        headers={"Authorization": "Bearer " + settings.access_token}
    ) as http_client:
        http_client.follow_redirects = False
        http_client.timeout = 10
        async with streamable_http_client(
            settings.url, http_client=http_client
        ) as streams:
            async with ClientSession(streams[0], streams[1]) as client:
                await client.initialize()
                result = await client.call_tool(tool, arguments, meta=meta)
    # Protocol aliases are stable across SDK versions (2.x uses snake_case attrs).
    wire = result.model_dump(by_alias=True)
    text = "\n".join(
        item["text"] for item in wire["content"] if item.get("type") == "text"
    )
    return {"success": not wire.get("isError", False), "reply_text": text, "msg": text}

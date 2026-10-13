"""Host-side bridge for HENU Plain cards, without changing SDK serialization.

Copy beside LangBot's qqofficial.py and apply qq-cards-host.patch to the exact
reviewed QQOfficialClient. This module never sends a keyboard or uploads images.
"""
from __future__ import annotations

import re

import httpx

PREFIX = "# 河大助手 · "


def is_card(content: str) -> bool:
    return isinstance(content, str) and content.startswith(PREFIX) and len(content.encode("utf-8")) <= 3600


def plain_card(content: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\((https://[^\s)]+)\)", r"\1：\2", content)
    text = re.sub(r"(?m)^#{1,2} ", "", text)
    text = text.replace("**", "")
    return re.sub(r"\\([\\`*_{}\[\]()#!|~])", r"\1", text)


async def send_card(client, target_type: str, target_id: str, content: str, *, msg_id=None, event_id=None, msg_seq=None):
    """Use the same passive source/counter; retry only an explicit rejection."""
    if target_type not in {"c2c", "group"} or not is_card(content):
        raise ValueError("invalid HENU card target or content")
    sender = client.send_private_text_msg if target_type == "c2c" else client.send_group_text_msg
    if not msg_id and not event_id:
        # No automatic proactive Markdown attempt, and no new quota policy.
        return await sender(target_id, plain_card(content), msg_seq=msg_seq)
    if not await client.check_access_token():
        await client.get_access_token()
    sequence = msg_seq if msg_seq is not None else await client.next_reply_msg_seq(msg_id or event_id)
    base = client.base_url.rstrip("/")
    if base not in {"https://api.sgroup.qq.com", "https://sandbox.api.sgroup.qq.com"}:
        raise ValueError("unexpected QQ API origin")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", target_id):
        raise ValueError("invalid QQ target")
    path = "users" if target_type == "c2c" else "groups"
    body = {"msg_type": 2, "markdown": {"content": content}, "msg_seq": sequence}
    if msg_id:
        body["msg_id"] = msg_id
    if event_id:
        body["event_id"] = event_id
    headers = {"Authorization": "QQBot " + client.access_token, "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=15, follow_redirects=False) as http:
        response = await http.post(f"{base}/v2/{path}/{target_id}/messages", headers=headers, json=body)
    if response.status_code == 200:
        # Keep private binding receipt metadata for the existing adapter's
        # normalization; do not log body/text/target/token.
        try:
            return response.json()
        except ValueError:
            return None  # Already accepted: never retry a malformed receipt.
    if response.status_code in {400, 403}:
        fallback_sequence = max(sequence + 1, await client.next_reply_msg_seq(msg_id or event_id))
        return await sender(target_id, plain_card(content), msg_id=msg_id, event_id=event_id, msg_seq=fallback_sequence)
    # Timeouts/network failures and 5xx have unknown delivery status. Do not
    # duplicate sends. Channel methods do not call this helper.
    raise ValueError(f"QQ card delivery failed: HTTP {response.status_code}")

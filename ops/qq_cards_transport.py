"""Host-side bridge for HENU Plain cards, without changing SDK serialization.

Copy beside LangBot's qqofficial.py and apply qq-cards-host.patch to the exact
reviewed QQOfficialClient. Native keyboards are explicit, never image uploads.
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


def validate_keyboard(keyboard, target_type, actor=None):
    if not isinstance(keyboard, dict) or set(keyboard) != {'content'}:
        raise ValueError('invalid inline keyboard')
    rows = keyboard.get('content', {}).get('rows')
    if not isinstance(rows, list) or not 1 <= len(rows) <= 5:
        raise ValueError('invalid keyboard rows')
    ids = set()
    for row in rows:
        buttons = row.get('buttons')
        if not isinstance(buttons, list) or not 1 <= len(buttons) <= 5:
            raise ValueError('invalid keyboard columns')
        for button in buttons:
            identifier = button.get('id')
            render = button.get('render_data') or {}
            action = button.get('action') or {}
            permission = action.get('permission') or {}
            users = permission.get('specify_user_ids')
            if (not isinstance(identifier, str) or identifier in ids or len(identifier) > 64
                    or not isinstance(render.get('label'), str) or not 1 <= len(render['label']) <= 10
                    or action.get('type') not in {1, 2}
                    or not isinstance(action.get('data'), str) or not 1 <= len(action['data']) <= 256
                    or permission.get('type') != 0 or not isinstance(users, list) or len(users) != 1
                    or not isinstance(users[0], str) or not users[0]
                    or actor is not None and users != [actor]
                    or target_type != 'c2c' and action.get('enter') is True):
                raise ValueError('invalid keyboard button or ownership')
            ids.add(identifier)


async def send_card(client, target_type: str, target_id: str, content: str, *, msg_id=None, event_id=None, msg_seq=None, keyboard=None):
    """Use the same passive source/counter; retry only an explicit rejection."""
    if target_type not in {"c2c", "group"} or not is_card(content):
        raise ValueError("invalid HENU card target or content")
    if msg_id and event_id:
        raise ValueError('passive anchors are mutually exclusive')
    if keyboard:
        validate_keyboard(keyboard, target_type, target_id if target_type == 'c2c' else None)
    if not msg_id and not event_id:
        # No automatic proactive Markdown attempt, and no new quota policy.
        sender = client.send_private_text_msg if target_type == 'c2c' else client.send_group_text_msg
        return await sender(target_id, plain_card(content), msg_seq=msg_seq)
    if not await client.check_access_token():
        await client.get_access_token()
    sequence = msg_seq if msg_seq is not None else await client.next_reply_msg_seq(msg_id or event_id)
    limit = 4 if target_type == 'c2c' else 5
    if type(sequence) is not int or not 1 <= sequence <= limit:
        raise ValueError('passive reply budget exhausted')
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
    if keyboard:
        body['keyboard'] = keyboard
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
        if fallback_sequence > limit:
            raise ValueError('passive fallback budget exhausted')
        sender = client.send_private_text_msg if target_type == 'c2c' else client.send_group_text_msg
        return await sender(target_id, plain_card(content), msg_id=msg_id, event_id=event_id, msg_seq=fallback_sequence)
    # Timeouts/network failures and 5xx have unknown delivery status. Do not
    # duplicate sends. Channel methods do not call this helper.
    raise ValueError(f"QQ card delivery failed: HTTP {response.status_code}")

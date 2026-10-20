"""Host-side bridge for HENU Plain cards, without changing SDK serialization.

Copy beside LangBot's qqofficial.py and apply the reviewed host overlays.
Button clicks are routed directly to the plugin, without resuming Dify forms.
"""

from __future__ import annotations

import re
import base64
import json
import time

import httpx

PREFIX = "# 河大助手 · "
MARKER = "\n[HENU_ACTIONS_V1:"


def unpack(content):
    if MARKER not in content:
        return content, None
    page, encoded = content.rsplit(MARKER, 1)
    if len(content.encode()) > 16000 or not encoded.endswith("]"):
        raise ValueError("invalid button envelope")
    wire = json.loads(base64.b64decode(encoded[:-1], altchars=b"-_", validate=True))
    rows = wire.get("actions")
    if (
        not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", str(wire.get("owner") or ""))
        or not isinstance(rows, list)
        or not 1 <= len(rows) <= 25
    ):
        raise ValueError("invalid button envelope")
    seen = set()
    for row in rows:
        handle, label = row.get("id"), row.get("label")
        if (
            not isinstance(handle, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{24}", handle)
            or handle in seen
            or not isinstance(label, str)
            or not 1 <= len(label) <= 10
        ):
            raise ValueError("invalid button action")
        seen.add(handle)
    return page, wire


def is_card(content: str) -> bool:
    if not isinstance(content, str) or not content.startswith(PREFIX):
        return False
    try:
        page, _ = unpack(content)
        return len(page.encode("utf-8")) <= 3600
    except Exception:
        return False


def plain_card(content: str) -> str:
    content, _ = unpack(content)
    content = re.sub(r"!\[[^\]]*\]\([^\n)]*\)", "", content)
    text = re.sub(r"\[([^\]]+)\]\((https://[^\s)]+)\)", r"\1：\2", content)
    text = re.sub(r"(?m)^#{1,2} ", "", text)
    text = text.replace("**", "")
    return re.sub(r"\\([\\`*_{}\[\]()#!|~])", r"\1", text)


async def send_card(
    client,
    target_type: str,
    target_id: str,
    content: str,
    *,
    msg_id=None,
    event_id=None,
    msg_seq=None,
):
    """Use the same passive source/counter; retry only an explicit rejection."""
    if target_type not in {"c2c", "group"} or not is_card(content):
        raise ValueError("invalid HENU card target or content")
    content, wire = unpack(content)
    if wire and (target_type != "c2c" or wire["owner"] != target_id):
        raise ValueError("interactive card requires its private owner")
    sender = (
        client.send_private_text_msg
        if target_type == "c2c"
        else client.send_group_text_msg
    )
    if not msg_id and not event_id:
        # No automatic proactive Markdown attempt, and no new quota policy.
        return await sender(target_id, plain_card(content), msg_seq=msg_seq)
    if not await client.check_access_token():
        await client.get_access_token()
    sequence = (
        msg_seq
        if msg_seq is not None
        else await client.next_reply_msg_seq(msg_id or event_id)
    )
    base = client.base_url.rstrip("/")
    if base not in {"https://api.sgroup.qq.com", "https://sandbox.api.sgroup.qq.com"}:
        raise ValueError("unexpected QQ API origin")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", target_id):
        raise ValueError("invalid QQ target")
    path = "users" if target_type == "c2c" else "groups"
    body = {"msg_type": 2, "markdown": {"content": content}, "msg_seq": sequence}
    if wire:
        buttons = []
        group = wire["actions"][0]["id"]
        for row in wire["actions"]:
            action = {
                "type": 1,
                "permission": {"type": 0, "specify_user_ids": [target_id]},
                "data": "henu:" + row["id"],
            }
            if row["label"].startswith("确认"):
                action["modal"] = {
                    "content": "确认执行当前卡片中的操作？",
                    "confirm_text": "确认",
                    "cancel_text": "取消",
                }
            buttons.append(
                {
                    "id": row["id"],
                    "group_id": group,
                    "render_data": {
                        "label": row["label"],
                        "visited_label": "已选择",
                        "style": 1,
                    },
                    "action": action,
                }
            )
        columns = 5 if len(buttons) > 10 else 2
        body["keyboard"] = {
            "content": {
                "rows": [
                    {"buttons": buttons[i : i + columns]}
                    for i in range(0, len(buttons), columns)
                ]
            }
        }
    if msg_id:
        body["msg_id"] = msg_id
    if event_id:
        body["event_id"] = event_id
    headers = {
        "Authorization": "QQBot " + client.access_token,
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=15, follow_redirects=False) as http:
        response = await http.post(
            f"{base}/v2/{path}/{target_id}/messages", headers=headers, json=body
        )
    if response.status_code == 200:
        # Keep private binding receipt metadata for the existing adapter's
        # normalization; do not log body/text/target/token.
        try:
            receipt = response.json()
            if wire and receipt.get("id"):
                state = getattr(client, "_henu_buttons", {})
                now = time.monotonic()
                state = {k: v for k, v in state.items() if v["expires"] > now}
                for row in wire["actions"]:
                    state[row["id"]] = {
                        "owner": wire["owner"],
                        "scene": target_type,
                        "target": target_id,
                        "message_id": str(receipt["id"]),
                        "expires": now + 300,
                        "group": group,
                    }
                while len(state) > 2048:
                    state.pop(next(iter(state)))
                client._henu_buttons = state
            return receipt
        except ValueError:
            return None  # Already accepted: never retry a malformed receipt.
    if response.status_code in {400, 403}:
        fallback_sequence = max(
            sequence + 1, await client.next_reply_msg_seq(msg_id or event_id)
        )
        return await sender(
            target_id,
            plain_card(content),
            msg_id=msg_id,
            event_id=event_id,
            msg_seq=fallback_sequence,
        )
    # Timeouts/network failures and 5xx have unknown delivery status. Do not
    # duplicate sends. Channel methods do not call this helper.
    raise ValueError(f"QQ card delivery failed: HTTP {response.status_code}")


async def handle_click(adapter, event, event_id):
    """Handle only HENU callbacks. Preserve the existing Dify handler otherwise."""
    resolved = (event.get("data") or {}).get("resolved") or {}
    data = str(resolved.get("button_data") or "")
    if not data.startswith("henu:"):
        return False
    bot = adapter.bot
    handle = data[5:]
    state = getattr(bot, "_henu_buttons", {})
    item = state.get(handle)
    actor = str(event.get("user_openid") or "")
    valid = (
        item
        and item["expires"] > time.monotonic()
        and event_id
        and event.get("chat_type") == 2
        and actor == item["owner"] == item["target"]
        and str(resolved.get("message_id") or "") == item["message_id"]
    )
    if not valid:
        await bot.ack_interaction(event.get("id") or "", code=4)
        return True
    for key, sibling in list(state.items()):
        if sibling["group"] == item["group"]:
            state.pop(key, None)
    await bot.ack_interaction(event.get("id") or "", code=0)
    from langbot_plugin.api.entities.builtin.platform import events, entities, message
    from langbot_plugin.api.entities.builtin.provider.session import LauncherTypes

    chain = message.MessageChain([message.Plain(text="HENU_BUTTON " + handle)])
    synthetic = events.FriendMessage(
        sender=entities.Friend(id=actor, nickname="", remark=""),
        message_chain=chain,
        time=int(time.time()),
        source_platform_object=None,
    )
    if adapter.ap is None:
        return True
    matched = next(
        (b for b in adapter.ap.platform_mgr.bots if b.adapter is adapter), None
    )
    if matched is None:
        return True
    adapter._session_event_ids["c2c_" + actor] = {
        "event_id": event_id,
        "posted_at": time.time(),
        "henu": True,
    }
    adapter._anchor_msg_seq[event_id] = 0
    await adapter.ap.query_pool.add_query(
        bot_uuid=matched.bot_entity.uuid,
        launcher_type=LauncherTypes.PERSON,
        launcher_id=actor,
        sender_id=actor,
        message_event=synthetic,
        message_chain=chain,
        adapter=adapter,
        pipeline_uuid=matched.bot_entity.use_pipeline_uuid,
        variables={"_routed_by_rule": True},
    )
    return True


async def reply_synthetic_image(adapter, source, chain):
    """Image reply to a HENU callback, using its passive event anchor only."""
    from langbot_plugin.api.entities.builtin.platform.message import Image

    if (
        source.source_platform_object is not None
        or len(chain) != 1
        or not isinstance(chain[0], Image)
    ):
        return False
    target = str(getattr(getattr(source, "sender", None), "id", "") or "")
    cached = adapter._session_event_ids.get("c2c_" + target)
    if not cached or not cached.get("henu"):
        return False
    if not cached or time.time() - cached.get("posted_at", 0) > 300:
        raise ValueError("image reply anchor expired")
    data = chain[0].base64
    if not data or len(data) > 20_000_000:
        raise ValueError("invalid seat map image")
    decoded = base64.b64decode(data, validate=True)
    if not decoded.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("seat map must be PNG")
    bot = adapter.bot
    event_id = cached["event_id"]
    sequence = adapter._next_msg_seq(event_id)
    if sequence is None:
        raise ValueError("reply budget exhausted")
    file_info = await bot.upload_media("c2c", target, file_type=1, file_data=data)
    if not file_info:
        raise ValueError("image upload unconfirmed")
    base = bot.base_url.rstrip("/")
    if base not in {"https://api.sgroup.qq.com", "https://sandbox.api.sgroup.qq.com"}:
        raise ValueError("unexpected QQ API origin")
    body = {
        "msg_type": 7,
        "media": {"file_info": file_info},
        "event_id": event_id,
        "msg_seq": sequence,
    }
    async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
        response = await client.post(
            base + "/v2/users/" + target + "/messages",
            headers={
                "Authorization": "QQBot " + bot.access_token,
                "Content-Type": "application/json",
            },
            json=body,
        )
    if response.status_code != 200:
        raise ValueError("QQ image reply HTTP " + str(response.status_code))
    return True

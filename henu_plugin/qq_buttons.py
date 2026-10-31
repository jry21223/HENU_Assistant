"""Short-lived QQ actions, bound to one person, bot, chat and preview."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import secrets
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps

from henu_plugin.confirmation import (
    conversation_context,
    validate_pending_operation,
    operation_fingerprint,
    split_confirm_token,
)
from henu_plugin.qq_cards import scope, card_chain, Card

MARKER = "\n[HENU_ACTIONS_V1:"
COMMAND = re.compile(r"HENU_BUTTON ([A-Za-z0-9_-]{24})")
_preview_command = ContextVar("henu_preview_command", default="")


def _action_fingerprint(payload):
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@dataclass(frozen=True)
class VerifiedButtonAction:
    """Internal receipt minted only after consuming a valid scoped action."""

    handle: str = field(repr=False)
    owner: tuple = field(repr=False)
    query_id: int | str
    expires: float
    payload_fingerprint: str = field(repr=False)

    def matches(self, ctx, bot, payload):
        return (
            scope(bot, ctx.event) == self.owner
            and ctx.query_id == self.query_id
            and time.monotonic() < self.expires
            and _action_fingerprint(payload) == self.payload_fingerprint
        )

    @property
    def source_id(self):
        return "button:" + self.handle


def confirmation_context(call):
    """Retain the real invocation command through awaits and nested read calls."""

    @wraps(call)
    async def wrapped(self, params, session, query_id):
        command = split_confirm_token(
            params.get("command", "") if isinstance(params, dict) else ""
        )[0]
        context_token = _preview_command.set(command)
        try:
            return await call(self, params, session, query_id)
        finally:
            _preview_command.reset(context_token)

    return wrapped


def envelope(page, wire):
    if not wire:
        return page
    data = base64.urlsafe_b64encode(
        json.dumps(wire, ensure_ascii=False, separators=(",", ":")).encode()
    ).decode()
    return page + MARKER + data + "]"


def _state(plugin):
    if not hasattr(plugin, "_qq_actions"):
        plugin._qq_actions = {}
    state = plugin._qq_actions
    now = time.monotonic()
    for key, item in list(state.items()):
        if item["expires"] <= now:
            state.pop(key, None)
    return state


def _actions(plugin, owner, query_id, specs):
    if not owner or owner[1] != "person":
        return None
    state = _state(plugin)
    group = secrets.token_urlsafe(18)
    rows = []
    for label, payload in specs[:25]:
        handle = secrets.token_urlsafe(18)
        state[handle] = {
            "owner": owner,
            "query": query_id,
            "payload": payload,
            "group": group,
            "expires": time.monotonic() + 300,
        }
        rows.append({"id": handle, "label": str(label)[:10]})
    while len(state) > 2048:
        state.pop(next(iter(state)))
    return {"owner": owner[3], "actions": rows}


async def actions(plugin, ctx, specs):
    return _actions(
        plugin,
        scope(str(await ctx.get_bot_uuid() or ""), ctx.event),
        ctx.query_id,
        specs,
    )


async def confirmation_actions(
    plugin, bot, session, query_id, *, expected_command=None
):
    owner = scope(bot, session)
    if not owner or owner[1] != "person":
        return None
    key = re.sub(r"[^0-9A-Za-z._-]+", "_", owner[3]).strip("._-")
    expected = split_confirm_token(
        expected_command if expected_command is not None else _preview_command.get()
    )[0]
    if not expected:
        return None
    try:
        raw = await plugin.get_plugin_storage("user:" + key + ":pending_operation")
        pending = json.loads(raw)
        if (
            pending.get("conversation") != conversation_context(session)
            or pending.get("command") != expected
            or pending.get("fingerprint") != operation_fingerprint(key, expected)
            or int(pending.get("created_query_id") or -1) > query_id
            or float(pending.get("expires_at") or 0) <= time.time()
        ):
            return None
        token = pending["token"]
    except Exception:
        return None
    label = (
        "确认预约"
        if str(pending.get("command") or "").startswith("library reserve ")
        else "确认操作"
    )
    return _actions(
        plugin,
        owner,
        query_id,
        [
            (label, {"kind": "pending_confirm", "token": token}),
            ("取消", {"kind": "pending_cancel", "token": token}),
        ],
    )


async def tell(ctx, text):
    await ctx.reply(card_chain(Card("操作确认", "", (text,)).pages()[0]))


async def handle(plugin, ctx, text=None):
    from langbot_plugin.api.entities.builtin.platform.message import Plain, At, Source

    chain = getattr(getattr(ctx.event, "message_chain", None), "root", [])
    if not chain or any(not isinstance(part, (Plain, At, Source)) for part in chain):
        return False
    text = "".join(part.text for part in chain if isinstance(part, Plain))
    match = COMMAND.fullmatch(text.strip())
    if not match:
        return False
    if getattr(ctx, "is_prevent_postorder", False):
        return True
    ctx.prevent_default()
    ctx.prevent_postorder()
    owner = scope(str(await ctx.get_bot_uuid() or ""), ctx.event)
    state = _state(plugin)
    item = state.get(match[1])
    if not item or item["owner"] != owner or item["query"] == ctx.query_id:
        await tell(ctx, "按钮已失效或不属于当前会话，请重新查询。")
        return True
    # Consume the whole card before the first await. Confirm/cancel and two seat
    # choices cannot both dispatch; network uncertainty never restores a button.
    for handle, sibling in list(state.items()):
        if sibling["group"] == item["group"]:
            state.pop(handle, None)
    try:
        receipt = VerifiedButtonAction(
            match[1],
            owner,
            ctx.query_id,
            item["expires"],
            _action_fingerprint(item["payload"]),
        )
        await dispatch(plugin, ctx, item["payload"], receipt=receipt)
    except Exception as exc:
        print("[henu.buttons] " + type(exc).__name__, flush=True)
        await tell(ctx, "本次操作结果未确认，请先查询状态，不要重复提交。")
    return True


async def dispatch(plugin, ctx, payload, *, receipt=None):
    if payload["kind"].startswith("alert_booking_"):
        alerts = getattr(plugin, "library_seat_alerts", None)
        if alerts is not None:
            return await alerts.handle_action(ctx, payload, receipt=receipt)
        return
    if payload["kind"].startswith("library_"):
        from henu_plugin.library_seat_map import dispatch_library

        return await dispatch_library(plugin, ctx, payload)
    if payload["kind"] not in {"pending_confirm", "pending_cancel"}:
        return
    from components.cli_tools.henu_cli_safe import HenuCliSafe
    from langbot_plugin.api.entities.builtin.provider.session import (
        Session,
        LauncherTypes,
    )

    session = Session(
        sender_id=ctx.event.sender_id,
        launcher_id=ctx.event.launcher_id,
        launcher_type=LauncherTypes.PERSON,
    )
    storage = re.sub(r"[^0-9A-Za-z._-]+", "_", session.sender_id).strip("._-")
    storage_key = "user:" + storage + ":pending_operation"
    tool = HenuCliSafe()
    tool.plugin = plugin
    # Use the production engine's own lock for cancellation, including races
    # with a new preview or ordinary text confirmation.
    lock_factory = getattr(tool, "_confirmation_lock", None)
    if not hasattr(plugin, "_qq_pending_lock"):
        plugin._qq_pending_lock = asyncio.Lock()
    lock = lock_factory(storage) if callable(lock_factory) else plugin._qq_pending_lock
    async with lock:
        pending = json.loads(await plugin.get_plugin_storage(storage_key))
        check = validate_pending_operation(
            pending,
            token=payload["token"],
            storage_key=storage,
            canonical_command=str(pending.get("command") or ""),
            query_id=ctx.query_id,
        )
        if not check.ok or pending.get("conversation") != conversation_context(session):
            await tell(ctx, "这张确认卡已失效，请重新发起操作。")
            return
        if payload["kind"] == "pending_cancel":
            await plugin.set_plugin_storage(storage_key, b"{}")
            await tell(ctx, "已取消，本次没有提交。")
            return
    # The engine revalidates the exact token under its lock, consumes durably,
    # and rechecks the campus plan. Never translate a click to generic “确认”.
    result = await tool.call(
        {"command": "confirm " + payload["token"]}, session, ctx.query_id
    )
    await tell(
        ctx,
        str(
            result.get("reply_text")
            or result.get("msg")
            or "结果未知，请先查询预约记录。"
        ),
    )

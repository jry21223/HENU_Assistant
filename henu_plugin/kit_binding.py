"""QQ-only, pre-model binding commands and durable plain confirmation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import uuid

from langbot_plugin.api.entities.builtin.platform.message import MessageChain, Plain
from henu_mcp.core.secure_storage import encrypt_value, decrypt_value
from henu_plugin.confirmation import (
    create_pending_operation,
    validate_pending_operation,
    conversation_context,
)
from henu_plugin.kit_client import kit_settings, kit_request, KitError

COMMANDS = {
    "绑定 HENU KIT": "start",
    "绑定HENU KIT": "start",
    "HENU KIT 状态": "status",
    "HENU KIT状态": "status",
    "解绑 HENU KIT": "unlink",
    "解绑HENU KIT": "unlink",
}
EXPLICIT_CONFIRM = {
    "确认HENU KIT绑定",
    "确认 HENU KIT 绑定",
    "确认HENU KIT解绑",
    "确认 HENU KIT 解绑",
}


class KitBindingCoordinator:
    def __init__(self, listener):
        self.listener = listener
        self.locks = {}
        self.watchers = {}

    async def _read(self, key):
        try:
            raw = await self.listener.plugin.get_plugin_storage(key)
        except KeyError:
            return None
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode()
        return json.loads(decrypt_value(raw))

    async def _save(self, key, value):
        await self.listener.plugin.set_plugin_storage(
            key, encrypt_value(json.dumps(value)).encode()
        )

    async def handle(self, ctx, *, is_group):
        text = (
            str(
                getattr(ctx.event, "text_message", "")
                or getattr(ctx.event, "message_chain", "")
            )
            .strip()
            .rstrip("。！!")
        )
        action = COMMANDS.get(text)
        confirming = text == "确认" or text in EXPLICIT_CONFIRM
        if not action and not confirming:
            return False
        # Do not interfere with existing plain confirmation when KIT is absent.
        try:
            settings = kit_settings()
            bot = await ctx.get_bot_uuid() if settings else ""
        except Exception:
            if text == "确认":
                return False
            await self._stop_reply(ctx, "HENU KIT 绑定服务暂时不可用，请联系维护者。")
            return True
        if is_group or not settings or bot != settings["bot_uuid"]:
            if text == "确认":
                return False
            await self._stop_reply(
                ctx, "请在指定的 HENU Bot QQ 私聊中操作；如仍不可用，请联系维护者。"
            )
            return True
        sender = str(getattr(ctx.event, "sender_id", "") or "")
        conversation = conversation_context(ctx.event)
        if (
            not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", sender)
            or conversation["launcher_type"] != "person"
            or not conversation["launcher_id"]
        ):
            await self._stop_reply(ctx, "无法确认当前 QQ 身份，本次未执行。")
            return True
        key = "kit-binding:" + hashlib.sha256((bot + ":" + sender).encode()).hexdigest()
        lock = self.locks.setdefault(key, asyncio.Lock())
        if lock.locked():
            await self._stop_reply(ctx, "绑定操作正在处理中，请等待结果。")
            return True
        async with lock:
            try:
                pending = await self._read(key)
                if pending and pending.get("expires_at", 0) <= time.time():
                    await self.listener.plugin.set_plugin_storage(key, b"")
                    pending = None
                if confirming:
                    if not pending:
                        if text == "确认":
                            return False
                        await self._stop_reply(
                            ctx, "没有有效的 HENU KIT 待确认操作，请重新发起。"
                        )
                        return True
                    source_id = await self._source_id(ctx)
                    if not source_id or not pending.get("created_source_id"):
                        await self._stop_reply(
                            ctx, "无法验证原消息标识，请重新发起绑定。"
                        )
                        return True
                    if source_id in {
                        pending["created_source_id"],
                        pending.get("preview_source_id"),
                    }:
                        ctx.prevent_default()
                        ctx.prevent_postorder()
                        return True
                    check = validate_pending_operation(
                        pending,
                        token=pending["token"],
                        storage_key=sender,
                        canonical_command=pending["command"],
                        query_id=ctx.query_id,
                    )
                    if (
                        not check.ok
                        or pending.get("conversation") != conversation
                        or pending.get("bot") != bot
                    ):
                        await self._stop_reply(
                            ctx, "待确认操作已失效或不在原私聊，请重新发起。"
                        )
                        return True
                    if text == "确认" and await self._other_pending(
                        sender, conversation, ctx.query_id
                    ):
                        await self._stop_reply(
                            ctx,
                            "同时有其他操作待确认。本次未执行，请回复“确认HENU KIT绑定”或“确认HENU KIT解绑”选择当前操作。",
                        )
                        return True
                    if ("解绑" in text) != (
                        pending["command"] == "unlink"
                    ) and text in EXPLICIT_CONFIRM:
                        await self._stop_reply(
                            ctx, "确认内容与待办不一致，请重新发起操作。"
                        )
                        return True
                    ctx.prevent_default()
                    ctx.prevent_postorder()
                    await self._confirm(ctx, settings, sender, key, pending, source_id)
                    return True
                ctx.prevent_default()
                ctx.prevent_postorder()
                if action == "status":
                    result = await asyncio.to_thread(
                        kit_request, settings, "status", {"subject": sender}
                    )
                    await self._reply(
                        ctx,
                        ("已绑定 HENU KIT：" + self._name(result))
                        if result.get("bound")
                        else "尚未绑定 HENU KIT。请发送“绑定 HENU KIT”。",
                    )
                    return True
                # QQ's source message identity, not LangBot's resettable query counter.
                source_id = await self._source_id(ctx)
                if not source_id:
                    await self._reply(
                        ctx, "无法验证原消息标识，本次未发起绑定，请联系维护者。"
                    )
                    return True
                request_id = str(
                    uuid.uuid5(uuid.NAMESPACE_URL, f"{bot}:{sender}:{source_id}")
                )
                if not pending or pending.get("request_id") != request_id:
                    pending = create_pending_operation(
                        storage_key=sender,
                        canonical_command=action,
                        query_id=ctx.query_id,
                    )
                    pending.update(
                        conversation=conversation,
                        bot=bot,
                        request_id=request_id,
                        created_source_id=source_id,
                    )
                    await self._save(key, pending)
                if action == "unlink":
                    await self._reply(
                        ctx,
                        "解绑后，Bot 后续操作不再使用此 HENU KIT 账号。请在下一条私聊回复“确认”（五分钟内有效）。",
                    )
                    return True
                result = await asyncio.to_thread(
                    kit_request,
                    settings,
                    "start",
                    {"subject": sender, "request_id": request_id},
                )
                token = result.get("token", "")
                if not isinstance(token, str) or not re.fullmatch(
                    r"[A-Za-z0-9_-]{43}", token
                ):
                    raise KitError()
                pending["link_token"] = token
                await self._save(key, pending)
                await self._reply(
                    ctx,
                    "请打开链接登录 HENU KIT 并授权，仅限本人使用，五分钟内有效：\n"
                    + settings["portal_url"]
                    + "/bind/qq#"
                    + token
                    + "\n网页授权后，我会提示目标账号，请核对后回复“确认”。若没有收到提示，也可回复“确认”查询进度。",
                )
                previous = self.watchers.get(key)
                if previous and not previous.done():
                    previous.cancel()
                self.watchers[key] = asyncio.create_task(
                    self._watch(ctx, settings, sender, key, request_id)
                )
            except KitError as exc:
                messages = {
                    "ALREADY_BOUND": "账号已有绑定，请先发送“HENU KIT 状态”查看，解绑后再换绑。",
                    "LINK_EXPIRED": "绑定链接已过期，请重新发送“绑定 HENU KIT”。",
                    "APPROVAL_EXPIRED": "网页登录已失效，请重新发起绑定。",
                    "RATE_LIMITED": "操作过于频繁，请稍后重试。",
                }
                await self._stop_reply(
                    ctx,
                    messages.get(
                        exc.code,
                        "操作结果暂未确认，请先发送“HENU KIT 状态”查询，不要重复发起。",
                    ),
                )
            except Exception:
                await self._stop_reply(
                    ctx,
                    "绑定记录暂时无法读取或保存，本次结果未确认，请查询“HENU KIT 状态”。",
                )
        return True

    async def _watch(self, ctx, settings, sender, key, request_id):
        try:
            for _ in range(55):
                await asyncio.sleep(4)
                async with self.locks[key]:
                    pending = await self._read(key)
                    if (
                        not pending
                        or pending.get("request_id") != request_id
                        or pending.get("previewed")
                        or pending.get("expires_at", 0) <= time.time()
                    ):
                        return
                    result = await asyncio.to_thread(
                        kit_request,
                        settings,
                        "pending",
                        {"subject": sender, "token": pending["link_token"]},
                    )
                    if result.get("state") == "authorized":
                        await self._reply(
                            ctx,
                            "即将绑定 HENU KIT："
                            + self._name(result)
                            + "。请核对，确认是本人账号后回复“确认”。",
                        )
                        pending["previewed"] = True
                        pending["preview_query_id"] = ctx.query_id
                        pending["preview_source_id"] = pending["created_source_id"]
                        await self._save(key, pending)
                        return
        except asyncio.CancelledError:
            raise
        except Exception:
            # No mutation on a lost notification. A later real-user message
            # can show the account preview and require a new confirmation.
            return
        finally:
            if self.watchers.get(key) is asyncio.current_task():
                self.watchers.pop(key, None)

    async def _confirm(self, ctx, settings, sender, key, pending, source_id):
        action = pending["command"]
        payload = {"subject": sender}
        if action == "start":
            payload["token"] = pending.get("link_token", "")
            result = await asyncio.to_thread(kit_request, settings, "pending", payload)
            if result.get("state") == "pending":
                await self._reply(
                    ctx, "请先打开链接，在 HENU KIT 登录并点击“授权绑定当前账号”。"
                )
                return
            if result.get("state") not in {"authorized", "confirmed"}:
                raise KitError()
            if not pending.get("previewed"):
                await self._reply(
                    ctx,
                    "即将绑定 HENU KIT："
                    + self._name(result)
                    + "。请核对，确认是本人账号后再回复“确认”。",
                )
                pending["previewed"] = True
                pending["preview_query_id"] = ctx.query_id
                pending["preview_source_id"] = source_id
                await self._save(key, pending)
                return
            if pending.get("preview_query_id") == ctx.query_id:
                return
            result = await asyncio.to_thread(kit_request, settings, "confirm", payload)
            message = (
                "已绑定 HENU KIT："
                + self._name(result)
                + "。这不代表学校 IDS 或雨课堂已登录。"
            )
        elif action == "unlink":
            await asyncio.to_thread(kit_request, settings, "unlink", payload)
            message = "已解除 HENU KIT 绑定，后续 Bot 操作不再使用此账号授权。"
        else:
            raise KitError("INVALID_ACTION")
        # Core is durable/idempotent; a lost reply never creates another binding.
        await self.listener.plugin.set_plugin_storage(key, b"")
        await self._reply(ctx, message)

    async def _other_pending(self, sender, conversation, query_id):
        sanitized = re.sub(r"[^0-9A-Za-z._-]+", "_", sender).strip("._-")
        for key, encrypted in (
            (f"user:{sender}:yuketang_pending_credentials", True),
            (f"user:{sanitized}:pending_operation", False),
        ):
            try:
                raw = await self.listener.plugin.get_plugin_storage(key)
            except KeyError:
                continue
            if not raw:
                continue
            if isinstance(raw, bytes):
                raw = raw.decode()
            value = json.loads(decrypt_value(raw) if encrypted else raw)
            if (
                value.get("conversation") == conversation
                and validate_pending_operation(
                    value,
                    token=value.get("token", ""),
                    storage_key=sender if encrypted else sanitized,
                    canonical_command=value.get("command", ""),
                    query_id=query_id,
                ).ok
            ):
                return True
        return False

    @staticmethod
    async def _source_id(ctx):
        chain = getattr(ctx.event, "message_chain", None)
        if chain is None:
            try:
                chain = await ctx.get_query_var("message_chain")
            except Exception:
                return ""
        components = getattr(chain, "root", chain)
        if not isinstance(components, (list, tuple)):
            return ""
        for item in components:
            kind = (
                item.get("type")
                if isinstance(item, dict)
                else getattr(item, "type", "")
            )
            if kind == "Source":
                identity = (
                    item.get("id")
                    if isinstance(item, dict)
                    else getattr(item, "id", "")
                )
                return str(identity) if identity and len(str(identity)) <= 256 else ""
        return ""

    @staticmethod
    def _name(result):
        name = result.get("display_name")
        return (
            str(name).replace("\n", " ").replace("\r", " ")[:120]
            if name
            else "当前账号"
        )

    @staticmethod
    async def _reply(ctx, text):
        await ctx.reply(MessageChain([Plain(text=text)]))

    async def _stop_reply(self, ctx, text):
        ctx.prevent_default()
        ctx.prevent_postorder()
        await self._reply(ctx, text)

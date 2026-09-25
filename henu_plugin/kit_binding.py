"""QQ-only, pre-model binding commands and durable plain confirmation."""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import hmac
import json
import math
import re
import time
import unicodedata
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
USED_SOURCE_SCHEMA = "henu.kit-binding-used-sources.v2"
MAX_SOURCE_AGE_SECONDS = 120
MAX_SOURCE_FUTURE_SECONDS = 0
MAX_QQ_BOT_CLOCK_SKEW_SECONDS = 30
USED_SOURCE_RETENTION_SECONDS = 300
MAX_USED_SOURCES = 4096
ACTIVATION_DELAY_SECONDS = 120
ACTIVATION_SCHEMA = "henu.kit-binding-activation.v1"


class UsedSourceLedgerFull(RuntimeError):
    pass


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

    @staticmethod
    def _scope_key(bot, sender):
        return "kit-binding:" + hashlib.sha256((bot + ":" + sender).encode()).hexdigest()

    async def _activation_not_before(self, bot):
        activation_key = "kit-binding-activation:" + hashlib.sha256(bot.encode()).hexdigest()
        activation = await self._read(activation_key)
        if activation is None:
            activation = {
                "schema": ACTIVATION_SCHEMA,
                "not_before": time.time() + ACTIVATION_DELAY_SECONDS,
            }
            await self._save(activation_key, activation)
        if (
            not isinstance(activation, dict)
            or activation.get("schema") != ACTIVATION_SCHEMA
            or not isinstance(activation.get("not_before"), (int, float))
            or not math.isfinite(activation["not_before"])
        ):
            raise ValueError("invalid KIT binding activation record")
        return activation["not_before"]

    async def _used_source(self, key, sender, source_id):
        """Retain recent Sources; the trusted QQ time check rejects older replay."""
        unknown_key = "kit-binding-used-unknown:" + hashlib.sha256(sender.encode()).hexdigest()
        known_key = (
            "kit-binding-used:" + key.removeprefix("kit-binding:")
            if key
            else None
        )
        now = time.time()
        digest = hashlib.sha256(source_id.encode()).hexdigest()
        ledgers = {}
        for marker_key in (unknown_key, known_key):
            if not marker_key:
                continue
            ledger = await self._read(marker_key)
            if ledger is None:
                ledger = {"schema": USED_SOURCE_SCHEMA, "entries": {}}
            if (
                not isinstance(ledger, dict)
                or ledger.get("schema") != USED_SOURCE_SCHEMA
                or not isinstance(ledger.get("entries"), dict)
            ):
                raise ValueError("invalid KIT confirmation source ledger")
            entries = ledger["entries"]
            for stored_digest, stored_at in list(entries.items()):
                if not isinstance(stored_at, (int, float)) or not math.isfinite(stored_at):
                    raise ValueError("invalid KIT confirmation source time")
                if stored_at < now - USED_SOURCE_RETENTION_SECONDS:
                    del entries[stored_digest]
            if digest in entries:
                return True
            ledgers[marker_key] = ledger
        target = known_key or unknown_key
        ledger = ledgers[target]
        if len(ledger["entries"]) >= MAX_USED_SOURCES:
            raise UsedSourceLedgerFull()
        ledger["entries"][digest] = now
        await self._save(target, ledger)
        return False

    async def _remember_fallback_confirmation(self, ctx, bot):
        """Record a plain confirmation before allowing another flow to handle it."""
        sender = str(getattr(ctx.event, "sender_id", "") or "")
        conversation = conversation_context(ctx.event)
        if (
            not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", sender)
            or conversation["launcher_type"] != "person"
            or not conversation["launcher_id"]
        ):
            return False
        source_id = await self._source_id(ctx)
        if not source_id:
            return False
        key = self._scope_key(bot, sender) if bot else None
        lock_key = key or "kit-binding-unknown:" + hashlib.sha256(sender.encode()).hexdigest()
        lock = self.locks.setdefault(lock_key, asyncio.Lock())
        async with lock:
            return await self._used_source(key, sender, source_id)

    async def _has_active_pending_for_sender(self, sender, conversation):
        """Find KIT ownership before falling through to another Bot's handler."""
        getter = getattr(self.listener.plugin, "get_plugin_storage_keys", None)
        if not callable(getter):
            raise RuntimeError("KIT pending storage cannot be enumerated")
        keys = await getter()
        if keys is None:
            raise RuntimeError("KIT pending storage keys unavailable")
        for key in keys:
            if not isinstance(key, str) or not re.fullmatch(r"kit-binding:[0-9a-f]{64}", key):
                continue
            pending = await self._read(key)
            if not pending:
                continue
            if not isinstance(pending, dict):
                raise ValueError("invalid KIT pending record")
            if pending.get("terminal") is True:
                continue
            candidate_bot = pending.get("bot")
            if not isinstance(candidate_bot, str) or not candidate_bot:
                raise ValueError("KIT pending Bot identity unavailable")
            expected = self._scope_key(candidate_bot, sender)
            if (
                key == expected
                and pending.get("conversation") == conversation
                and pending.get("expires_at", 0) > time.time()
            ):
                return True
        return False

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
        bot = ""
        try:
            if confirming and not is_group:
                bot = await ctx.get_bot_uuid()
            settings = kit_settings()
            if settings and not bot:
                bot = await ctx.get_bot_uuid()
        except Exception:
            if confirming and not is_group and await self._consume_orphan_quote(ctx):
                return True
            if confirming and not is_group:
                try:
                    if await self._remember_fallback_confirmation(ctx, bot):
                        ctx.prevent_default()
                        ctx.prevent_postorder()
                        return True
                except Exception:
                    await self._stop_reply(
                        ctx, "HENU KIT 确认记录暂时不可用，请稍后重试。"
                    )
                    return True
            if text == "确认":
                if is_group:
                    return False
                try:
                    sender = str(getattr(ctx.event, "sender_id", "") or "")
                    conversation = conversation_context(ctx.event)
                    if not await self._has_active_pending_for_sender(
                        sender, conversation
                    ):
                        return False
                except Exception:
                    # An uncertain ownership check must not fall through to a
                    # different confirmation handler.
                    pass
            await self._stop_reply(ctx, "HENU KIT 绑定服务暂时不可用，请联系维护者。")
            return True
        if is_group or not settings or bot != settings["bot_uuid"]:
            if confirming and not is_group and await self._consume_orphan_quote(ctx):
                return True
            if confirming and not is_group:
                try:
                    if await self._remember_fallback_confirmation(ctx, bot):
                        ctx.prevent_default()
                        ctx.prevent_postorder()
                        return True
                except Exception:
                    await self._stop_reply(
                        ctx, "HENU KIT 确认记录暂时不可用，请稍后重试。"
                    )
                    return True
            if text == "确认":
                if is_group:
                    return False
                try:
                    sender = str(getattr(ctx.event, "sender_id", "") or "")
                    conversation = conversation_context(ctx.event)
                    if not await self._has_active_pending_for_sender(
                        sender, conversation
                    ):
                        return False
                except Exception:
                    await self._stop_reply(
                        ctx, "HENU KIT 确认记录暂时不可用，请稍后重试。"
                    )
                    return True
            await self._stop_reply(
                ctx,
                "HENU KIT 绑定服务暂时不可用，请联系维护者。"
                if not settings
                else "请在指定的 HENU Bot QQ 私聊中操作；如仍不可用，请联系维护者。",
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
        key = self._scope_key(bot, sender)
        lock = self.locks.setdefault(key, asyncio.Lock())
        if lock.locked():
            await self._stop_reply(ctx, "绑定操作正在处理中，请等待结果。")
            return True
        async with lock:
            try:
                source_id, source_time = await self._source_details(ctx)
                if not self._valid_c2c_provenance(
                    ctx, sender, source_id, source_time
                ):
                    await self._stop_reply(
                        ctx, "无法验证 QQ 消息来源，本次未执行，请在官方 QQ 私聊重新发送。"
                    )
                    return True
                pending = await self._read(key)
                if pending and pending.get("expires_at", 0) <= time.time():
                    if not confirming:
                        await self.listener.plugin.set_plugin_storage(key, b"")
                    pending = None
                if confirming and pending and pending.get("terminal") is True:
                    await self._stop_reply(
                        ctx,
                        "目标账号提示已失效，请重新发送“绑定 HENU KIT”。",
                    )
                    return True
                if confirming:
                    if not pending:
                        if self._quote_present(ctx, source_id):
                            await self._stop_reply(
                                ctx, "引用的绑定操作已失效，请重新发起。"
                            )
                            return True
                        if text == "确认":
                            if await self._has_active_pending_for_sender(
                                sender, conversation
                            ):
                                await self._stop_reply(
                                    ctx,
                                    "另一个 HENU Bot 中仍有待确认操作，请回到原私聊处理。",
                                )
                                return True
                            if (
                                source_id
                                and await self._used_source(key, sender, source_id)
                            ):
                                ctx.prevent_default()
                                ctx.prevent_postorder()
                                return True
                            return False
                        await self._stop_reply(
                            ctx, "没有有效的 HENU KIT 待确认操作，请重新发起。"
                        )
                        return True
                    activation_not_before = await self._activation_not_before(bot)
                    if source_id and await self._used_source(key, sender, source_id):
                        ctx.prevent_default()
                        ctx.prevent_postorder()
                        return True
                    if time.time() <= activation_not_before:
                        await self._stop_reply(
                            ctx, "HENU KIT 绑定服务正在启用，请稍后重新发起操作。"
                        )
                        return True
                    if source_time is None:
                        await self._stop_reply(
                            ctx, "无法验证 QQ 消息时间，本次未执行，请重新发送消息。"
                        )
                        return True
                    if source_time <= activation_not_before:
                        await self._stop_reply(
                            ctx, "这条 QQ 消息早于绑定服务启用时间，请重新发送消息。"
                        )
                        return True
                    if not source_id or not pending.get("created_source_id"):
                        await self._stop_reply(
                            ctx, "无法验证原消息标识，请重新发起绑定。"
                        )
                        return True
                    created_source_time = pending.get("created_source_time")
                    if (
                        not self._source_fresh(source_time)
                        or not isinstance(created_source_time, (int, float))
                        or not math.isfinite(created_source_time)
                    ):
                        await self._stop_reply(
                            ctx, "无法验证 QQ 消息时间或消息已过期，请重新发起操作。"
                        )
                        return True
                    if (
                        source_time <= created_source_time
                        or source_time
                        < pending.get("created_at", 0) - MAX_QQ_BOT_CLOCK_SKEW_SECONDS
                    ):
                        await self._stop_reply(
                            ctx, "请在发起操作后发送一条新的“确认”消息，本次未执行。"
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
                    quote_ref = None
                    if pending["command"] == "start":
                        quote_ref, quote_valid = self._quote_ref_idx(ctx, source_id)
                        if not quote_valid:
                            await self._stop_reply(
                                ctx,
                                "无法验证 QQ 引用消息，本次未执行。请引用最新的目标账号提示回复“确认”。",
                            )
                            return True
                        if quote_ref is not None:
                            receipt_ref = self._stored_receipt_ref_idx(pending)
                            if (
                                text != "确认"
                                or not self._exact_quoted_confirmation(ctx, source_id)
                                or receipt_ref is None
                                or not hmac.compare_digest(quote_ref, receipt_ref)
                            ):
                                await self._stop_reply(
                                    ctx,
                                    "引用的不是本次提示，本次未执行。请引用最新的目标账号提示回复“确认”。",
                                )
                                return True
                    elif self._quote_present(ctx, source_id):
                        await self._stop_reply(
                            ctx,
                            "解绑请直接发送“确认”，不要引用其他消息；本次未执行。",
                        )
                        return True
                    elif text == "确认" and await self._other_pending(
                        sender, conversation, ctx.query_id
                    ):
                        await self._stop_reply(
                            ctx,
                            "同时有其他操作待确认。本次未执行，请回复“确认HENU KIT解绑”选择当前操作。",
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
                    await self._confirm(
                        ctx,
                        settings,
                        sender,
                        key,
                        pending,
                        source_id,
                        source_time,
                        quote_ref,
                    )
                    return True
                ctx.prevent_default()
                ctx.prevent_postorder()
                activation_not_before = await self._activation_not_before(bot)
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
                if time.time() <= activation_not_before:
                    await self._reply(
                        ctx, "HENU KIT 绑定服务正在启用，请两分钟后重新发送操作。"
                    )
                    return True
                # QQ's source message identity, not LangBot's resettable query counter.
                if not source_id or not self._source_fresh(source_time):
                    await self._reply(
                        ctx,
                        "无法验证 QQ 消息标识或时间，本次未发起操作，请重新发送消息。",
                    )
                    return True
                if source_time <= activation_not_before:
                    await self._reply(
                        ctx, "这条 QQ 消息早于绑定服务启用时间，请重新发送操作。"
                    )
                    return True
                if await self._used_source(key, sender, source_id):
                    await self._reply(
                        ctx, "这条 QQ 消息已处理，请发送一条新消息重新发起操作。"
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
                        created_source_time=source_time,
                    )
                    await self._save(key, pending)
                if action == "unlink":
                    reply_result = await self._reply(
                        ctx,
                        "解绑后，Bot 后续操作不再使用此 HENU KIT 账号。请在下一条私聊回复“确认”（五分钟内有效）。",
                    )
                    pending["confirmation_receipt"] = self._reply_receipt(
                        reply_result, require_ref_idx=False
                    )
                    await self._save(key, pending)
                    if pending["confirmation_receipt"] is None:
                        await self._reply(
                            ctx,
                            "提示消息的发送时间无法确认，本次无法完成解绑，请重新发起。",
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
                    + "\n网页授权后，我会提示目标账号。请核对后引用那条提示回复“确认”。若没有收到提示，可单独发送“确认”查询进度。",
                )
                previous = self.watchers.get(key)
                if previous and not previous.done():
                    previous.cancel()
                self.watchers[key] = asyncio.create_task(
                    self._watch(ctx, settings, sender, key, request_id)
                )
            except UsedSourceLedgerFull:
                await self._stop_reply(
                    ctx, "确认消息过于频繁，本次未执行，请稍后重试。"
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
                        reply_result = await self._reply(
                            ctx,
                            "即将绑定 HENU KIT："
                            + self._name(result)
                            + "。请核对，确认是本人账号后引用这条提示回复“确认”。",
                        )
                        pending["previewed"] = True
                        pending["preview_query_id"] = ctx.query_id
                        pending["preview_source_id"] = pending["created_source_id"]
                        pending["confirmation_receipt"] = self._reply_receipt(
                            reply_result
                        )
                        await self._save(key, pending)
                        if pending["confirmation_receipt"] is None:
                            await self._reply(
                                ctx,
                                "提示消息的引用信息无法确认，本次无法完成绑定，请重新发起。",
                            )
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

    async def _confirm(
        self, ctx, settings, sender, key, pending, source_id, source_time, quote_ref
    ):
        action = pending["command"]
        if (
            action == "start"
            and pending.get("previewed")
            and self._stored_receipt_ref_idx(pending) is None
        ):
            await self._reply(
                ctx,
                "目标账号提示的引用信息无法确认，本次未执行，请重新发送“绑定 HENU KIT”。",
            )
            return
        if action == "unlink" or quote_ref is not None:
            receipt_time = self._stored_receipt_time(pending)
            if receipt_time is None:
                await self._reply(
                    ctx,
                    "提示消息的发送时间无法确认，本次未执行，请重新发起操作。",
                )
                return
            if source_time <= receipt_time:
                await self._reply(
                    ctx,
                    (
                        "请在收到提示后再回复一条新的“确认”消息；本次无法核实消息先后，未执行。"
                        if action == "unlink"
                        else "请在收到目标账号提示后再引用它回复一条新的“确认”消息；本次无法核实消息先后，未执行。"
                    ),
                )
                return
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
            if not pending.get("previewed") or quote_ref is None:
                # Invalidate an earlier prompt before sending a replacement.
                # A failed send or storage write must not leave its quote usable.
                previous_ref_idx = self._stored_receipt_ref_idx(pending)
                pending["confirmation_receipt"] = None
                if previous_ref_idx is None:
                    await self._save(key, pending)
                else:
                    # Retain only an encrypted tombstone until a distinct new
                    # QQ reference is durably saved. A repeated reference or
                    # failed save must not permit a third preview to revive it.
                    await self._save(
                        key,
                        {
                            "terminal": True,
                            "bot": pending["bot"],
                            "conversation": pending["conversation"],
                            "expires_at": pending["expires_at"],
                        },
                    )
                reply_result = await self._reply(
                    ctx,
                    "即将绑定 HENU KIT："
                    + self._name(result)
                    + "。请核对，确认是本人账号后引用这条提示回复“确认”。",
                )
                pending["previewed"] = True
                pending["preview_query_id"] = ctx.query_id
                pending["preview_source_id"] = source_id
                pending["confirmation_receipt"] = self._reply_receipt(reply_result)
                repeated_ref_idx = (
                    previous_ref_idx is not None
                    and pending["confirmation_receipt"] is not None
                    and hmac.compare_digest(
                        previous_ref_idx,
                        pending["confirmation_receipt"]["ref_idx"],
                    )
                )
                if repeated_ref_idx:
                    await self._reply(
                        ctx,
                        "目标账号提示的引用标识重复，本次无法完成绑定，请重新发起。",
                    )
                    return
                if previous_ref_idx is None or pending["confirmation_receipt"] is not None:
                    await self._save(key, pending)
                if pending["confirmation_receipt"] is None:
                    await self._reply(
                        ctx,
                        "提示消息的引用信息无法确认，本次无法完成绑定，请重新发起。",
                    )
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
    def _reply_receipt(reply_result, *, require_ref_idx=True):
        if not isinstance(reply_result, dict):
            return None
        receipt = reply_result.get("qq_c2c_receipt")
        if not isinstance(receipt, dict):
            return None
        message_id = receipt.get("id")
        raw_timestamp = receipt.get("timestamp")
        ref_idx = receipt.get("ref_idx")
        if (
            not isinstance(message_id, str)
            or not message_id.strip()
            or len(message_id) > 256
            or (require_ref_idx and not KitBindingCoordinator._valid_ref_idx(ref_idx))
            or not isinstance(raw_timestamp, str)
            or not re.fullmatch(
                r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})",
                raw_timestamp,
            )
        ):
            return None
        try:
            timestamp = datetime.datetime.fromisoformat(
                raw_timestamp.replace("Z", "+00:00")
            ).timestamp()
        except (OverflowError, ValueError):
            return None
        now = time.time()
        if (
            not math.isfinite(timestamp)
            or timestamp < now - 300
            or timestamp > now + 30
        ):
            return None
        stored = {"id": message_id, "timestamp": timestamp}
        if KitBindingCoordinator._valid_ref_idx(ref_idx):
            stored["ref_idx"] = ref_idx
        return stored

    @staticmethod
    def _stored_receipt_time(pending):
        receipt = pending.get("confirmation_receipt")
        if not isinstance(receipt, dict):
            return None
        message_id = receipt.get("id")
        timestamp = receipt.get("timestamp")
        started_at = pending.get("created_source_time")
        if (
            not isinstance(message_id, str)
            or not message_id.strip()
            or len(message_id) > 256
            or isinstance(timestamp, bool)
            or not isinstance(timestamp, (int, float))
            or not math.isfinite(timestamp)
            or isinstance(started_at, bool)
            or not isinstance(started_at, (int, float))
            or not math.isfinite(started_at)
            or timestamp < started_at
        ):
            return None
        return float(timestamp)

    @staticmethod
    def _stored_receipt_ref_idx(pending):
        if KitBindingCoordinator._stored_receipt_time(pending) is None:
            return None
        ref_idx = pending["confirmation_receipt"].get("ref_idx")
        return ref_idx if KitBindingCoordinator._valid_ref_idx(ref_idx) else None

    @staticmethod
    def _valid_ref_idx(value):
        return (
            isinstance(value, str)
            and 1 <= len(value) <= 256
            and all(33 <= ord(char) <= 126 for char in value)
        )

    @staticmethod
    def _quote_ref_idx(ctx, source_id):
        metadata = KitBindingCoordinator._verified_c2c_metadata(ctx, source_id)
        if metadata is None:
            return None, False
        if metadata.get("qq_quote_present") is True and "qq_quote_ref_idx" not in metadata:
            return None, False
        return metadata.get("qq_quote_ref_idx"), True

    @staticmethod
    def _quote_present(ctx, source_id):
        metadata = KitBindingCoordinator._verified_c2c_metadata(ctx, source_id)
        return metadata is not None and metadata.get("qq_quote_present") is True

    async def _consume_orphan_quote(self, ctx):
        sender = str(getattr(ctx.event, "sender_id", "") or "")
        source_id, source_time = await self._source_details(ctx)
        if self._valid_c2c_provenance(
            ctx, sender, source_id, source_time
        ) and self._quote_present(ctx, source_id):
            await self._stop_reply(ctx, "引用的绑定操作已失效，请重新发起。")
            return True
        return False

    @staticmethod
    def _verified_c2c_metadata(ctx, source_id):
        event = getattr(ctx.event, "message_event", None)
        metadata = (
            event.get("source_platform_object")
            if isinstance(event, dict)
            else getattr(event, "source_platform_object", None)
        )
        if not isinstance(metadata, dict) or set(metadata) not in (
            {"t", "d_id", "qq_websocket_verified"},
            {"t", "d_id", "qq_websocket_verified", "qq_quote_present"},
            {
                "t",
                "d_id",
                "qq_websocket_verified",
                "qq_quote_present",
                "qq_quote_ref_idx",
            },
        ):
            return None
        if (
            metadata.get("t") != "C2C_MESSAGE_CREATE"
            or metadata.get("d_id") != source_id
            or metadata.get("qq_websocket_verified") is not True
            or (
                "qq_quote_present" in metadata
                and metadata["qq_quote_present"] is not True
            )
            or (
                "qq_quote_ref_idx" in metadata
                and not KitBindingCoordinator._valid_ref_idx(
                    metadata["qq_quote_ref_idx"]
                )
            )
        ):
            return None
        return metadata

    @staticmethod
    def _exact_quoted_confirmation(ctx, source_id):
        chain = getattr(ctx.event, "message_chain", None)
        components = getattr(chain, "root", chain)
        if not isinstance(components, (list, tuple)) or len(components) != 2:
            return False
        source, plain = components
        source_kind = (
            source.get("type")
            if isinstance(source, dict)
            else getattr(source, "type", None)
        )
        plain_kind = (
            plain.get("type")
            if isinstance(plain, dict)
            else getattr(plain, "type", None)
        )
        source_value = (
            source.get("id")
            if isinstance(source, dict)
            else getattr(source, "id", None)
        )
        plain_text = (
            plain.get("text")
            if isinstance(plain, dict)
            else getattr(plain, "text", None)
        )
        return (
            source_kind == "Source"
            and plain_kind == "Plain"
            and str(source_value) == source_id
            and plain_text == "确认"
        )

    @staticmethod
    def _valid_c2c_provenance(ctx, sender, source_id, source_time):
        event = getattr(ctx.event, "message_event", None)
        source = event.get("sender") if isinstance(event, dict) else getattr(event, "sender", None)
        if isinstance(source, dict):
            sender_id = source.get("id")
            nickname = source.get("nickname")
        else:
            sender_id = getattr(source, "id", None)
            nickname = getattr(source, "nickname", None)
        event_time = event.get("time") if isinstance(event, dict) else getattr(event, "time", None)
        if isinstance(event_time, datetime.datetime):
            event_time = event_time.timestamp()
        if isinstance(event_time, bool) or not isinstance(event_time, (int, float)):
            return False
        return (
            bool(source_id)
            and KitBindingCoordinator._verified_c2c_metadata(ctx, source_id)
            is not None
            and nickname == "C2C_MESSAGE_CREATE"
            and str(sender_id) == sender
            and source_time is not None
            and math.isfinite(event_time)
            and float(event_time) == source_time
        )

    @staticmethod
    def _source_fresh(source_time):
        if source_time is None:
            return False
        now = time.time()
        return (
            now - MAX_SOURCE_AGE_SECONDS
            <= source_time
            <= now + MAX_SOURCE_FUTURE_SECONDS
        )

    @staticmethod
    async def _source_details(ctx):
        chain = getattr(ctx.event, "message_chain", None)
        if chain is None:
            try:
                chain = await ctx.get_query_var("message_chain")
            except Exception:
                return "", None
        components = getattr(chain, "root", chain)
        if not isinstance(components, (list, tuple)):
            return "", None
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
                if not identity or len(str(identity)) > 256:
                    return "", None
                raw_time = (
                    item.get("time", item.get("timestamp"))
                    if isinstance(item, dict)
                    else getattr(item, "time", None)
                )
                if isinstance(raw_time, datetime.datetime):
                    source_time = raw_time.timestamp()
                elif isinstance(raw_time, (int, float)) and not isinstance(raw_time, bool):
                    source_time = float(raw_time)
                else:
                    source_time = None
                if source_time is not None and not math.isfinite(source_time):
                    source_time = None
                return str(identity), source_time
        return "", None

    @staticmethod
    async def _source_id(ctx):
        source_id, _ = await KitBindingCoordinator._source_details(ctx)
        return source_id

    @staticmethod
    def _name(result):
        name = result.get("display_name")
        cleaned = (
            "".join(
                char
                for char in str(name)
                if unicodedata.category(char) not in {"Cc", "Cf"}
            ).strip()[:120]
            if name
            else ""
        )
        return cleaned or "当前账号"

    @staticmethod
    async def _reply(ctx, text):
        return await ctx.reply(MessageChain([Plain(text=text)]))

    async def _stop_reply(self, ctx, text):
        ctx.prevent_default()
        ctx.prevent_postorder()
        await self._reply(ctx, text)

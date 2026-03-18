from __future__ import annotations

import asyncio
import re

from langbot_plugin.api.definition.components.common.event_listener import EventListener
from langbot_plugin.api.entities.builtin.platform import message as platform_message
from langbot_plugin.api.entities.builtin.provider import message as provider_message
from langbot_plugin.api.entities.builtin.provider import session as provider_session
from langbot_plugin.api.entities import context, events
from langbot_plugin.api.proxies.query_based_api import QueryBasedAPIProxy


class IdentityCaptureListener(EventListener):
    _ACCOUNT_QUERY_PATTERNS = (
        re.compile(r"(我的|当前|现在|本账号).{0,8}(账号|绑定|学号|信息)"),
        re.compile(r"(账号|绑定).{0,6}(信息|状态|情况|详情)"),
        re.compile(r"(我).{0,6}(绑了|绑定了).{0,8}(什么|谁|哪个|哪一个)"),
        re.compile(r"(查|查看|看看|显示).{0,6}(账号|绑定|学号)"),
    )

    async def initialize(self):
        await super().initialize()

        @self.handler(events.PersonMessageReceived)
        async def on_person_message(ctx: context.EventContext):
            await self._persist_identity(ctx)

        @self.handler(events.GroupMessageReceived)
        async def on_group_message(ctx: context.EventContext):
            await self._persist_identity(ctx)

        @self.handler(events.PersonNormalMessageReceived)
        async def on_person_normal_message(ctx: context.EventContext):
            await self._persist_identity(ctx)

        @self.handler(events.GroupNormalMessageReceived)
        async def on_group_normal_message(ctx: context.EventContext):
            await self._persist_identity(ctx)
            await self._maybe_reply_account_status(ctx)

        @self.handler(events.PersonCommandSent)
        async def on_person_command(ctx: context.EventContext):
            await self._persist_identity(ctx)

        @self.handler(events.GroupCommandSent)
        async def on_group_command(ctx: context.EventContext):
            await self._persist_identity(ctx)

        @self.handler(events.PromptPreProcessing)
        async def on_prompt_preprocess(ctx: context.EventContext):
            await self._inject_current_sender_context(ctx)

    async def _persist_identity(self, ctx: context.EventContext) -> None:
        event = ctx.event
        launcher_type = self._normalize_launcher_type(getattr(event, "launcher_type", "") or "")
        await ctx.set_query_var("henu_sender_id", str(getattr(event, "sender_id", "") or ""))
        await ctx.set_query_var("henu_launcher_id", str(getattr(event, "launcher_id", "") or ""))
        await ctx.set_query_var("henu_launcher_type", launcher_type)
        await ctx.set_query_var("henu_sender_name", self._extract_sender_name(event))

    async def _inject_current_sender_context(self, ctx: context.EventContext) -> None:
        service = getattr(self.plugin, "service", None)
        handler = getattr(self.plugin, "plugin_runtime_handler", None)
        if service is None or handler is None:
            return

        try:
            api = QueryBasedAPIProxy(query_id=ctx.query_id, plugin_runtime_handler=handler)
            query_vars = await api.get_query_vars()
        except Exception:
            return

        if not isinstance(query_vars, dict):
            return

        launcher_type = self._normalize_launcher_type(
            query_vars.get("launcher_type") or query_vars.get("henu_launcher_type")
        )
        launcher_id = str(query_vars.get("launcher_id") or query_vars.get("henu_launcher_id") or "").strip()
        sender_id = str(query_vars.get("sender_id") or query_vars.get("henu_sender_id") or "").strip()
        sender_name = str(query_vars.get("sender_name") or query_vars.get("henu_sender_name") or "").strip()
        if launcher_type not in {"group", "person"} or not launcher_id or not sender_id:
            return

        try:
            session = provider_session.Session(
                launcher_type=provider_session.LauncherTypes(launcher_type),
                launcher_id=launcher_id,
                sender_id=sender_id,
            )
        except Exception:
            return

        identity_hint = {
            "sender_id": sender_id,
            "launcher_id": launcher_id,
            "launcher_type": launcher_type,
        }
        account_context = await asyncio.to_thread(
            service.get_sender_account_context,
            session,
            identity_hint,
        )
        if not isinstance(account_context, dict):
            return

        await api.set_query_var(
            "_henu_identity_context",
            {
                "speaker": {
                    "id": sender_id,
                    "name": sender_name,
                },
                "binding": account_context.get("binding") or {},
                "account": account_context.get("account") or {},
            },
        )

        prompt_block = self._format_sender_prompt_block(account_context, sender_name)
        if not prompt_block:
            return

        ctx.event.default_prompt.append(
            provider_message.Message(role="system", content=prompt_block)
        )

    async def _maybe_reply_account_status(self, ctx: context.EventContext) -> None:
        event = ctx.event
        text = str(getattr(event, "text_message", "") or "").strip()
        if not self._is_account_query(text):
            return

        service = getattr(self.plugin, "service", None)
        if service is None:
            return

        session = provider_session.Session(
            launcher_type=provider_session.LauncherTypes(event.launcher_type),
            launcher_id=str(event.launcher_id),
            sender_id=str(event.sender_id),
        )
        identity_hint = {
            "sender_id": str(getattr(event, "sender_id", "") or ""),
            "launcher_id": str(getattr(event, "launcher_id", "") or ""),
            "launcher_type": str(getattr(event, "launcher_type", "") or ""),
        }

        result = await asyncio.to_thread(
            service.run_tool,
            "system_status",
            {},
            session,
            ctx.query_id,
            identity_hint,
        )
        if not isinstance(result, dict):
            return

        reply_text = self._format_account_status(result)
        if not reply_text:
            return

        ctx.prevent_default()
        ctx.event.reply_message_chain = platform_message.MessageChain(
            [platform_message.Plain(text=reply_text)]
        )

    def _is_account_query(self, text: str) -> bool:
        normalized = re.sub(r"\s+", "", text or "")
        if not normalized:
            return False

        for pattern in self._ACCOUNT_QUERY_PATTERNS:
            if pattern.search(normalized):
                return True

        return normalized in {
            "账号",
            "账号信息",
            "绑定信息",
            "当前账号",
            "当前绑定",
            "我的账号",
            "我的账号信息",
            "我的绑定",
            "我的绑定信息",
            "我的学号",
            "我绑定了什么",
            "查看账号",
            "查看绑定",
            "查账号",
            "查绑定",
        }

    def _format_account_status(self, result: dict) -> str:
        if not result.get("success"):
            return str(result.get("msg") or "查询账号状态失败")

        account_wrapper = result.get("account") or {}
        account = account_wrapper.get("account") or {}
        binding = result.get("session_binding") or {}

        qq = str(binding.get("qq") or binding.get("sender_id") or "")
        student_id = str(account.get("student_id") or "").strip()
        has_password = bool(account.get("has_password"))
        location = str(account.get("library_default_location") or "").strip()
        seat_no = str(account.get("library_default_seat_no") or "").strip()
        has_mobile = bool(account.get("has_seminar_mobile"))

        lines = ["当前账号绑定信息"]
        if qq:
            lines.append(f"QQ: {qq}")
        lines.append(f"学号: {student_id or '未绑定'}")
        lines.append(f"密码: {'已保存' if has_password else '未保存'}")
        lines.append(f"图书馆默认区域: {location or '未设置'}")
        lines.append(f"图书馆默认座位: {seat_no or '未设置'}")
        lines.append(f"研讨室手机号: {'已保存' if has_mobile else '未保存'}")

        return "\n".join(lines)

    def _format_sender_prompt_block(self, account_context: dict, sender_name: str) -> str:
        binding = account_context.get("binding") if isinstance(account_context, dict) else {}
        account = account_context.get("account") if isinstance(account_context, dict) else {}
        if not isinstance(binding, dict):
            binding = {}
        if not isinstance(account, dict):
            account = {}

        sender_id = str(binding.get("sender_id") or binding.get("qq") or "").strip()
        launcher_type = str(binding.get("launcher_type") or "").strip()
        launcher_id = str(binding.get("launcher_id") or "").strip()
        student_id = str(account.get("student_id") or "").strip()
        location = str(account.get("library_default_location") or "").strip()
        seat_no = str(account.get("library_default_seat_no") or "").strip()
        has_password = bool(account.get("has_password"))
        has_mobile = bool(account.get("has_seminar_mobile"))

        if not sender_id:
            return ""

        lines = [
            "# HENU Current Speaker Context",
            "",
            "This turn may come from a different user than previous turns. For account, schedule, library, and seminar operations, only use the current speaker information below.",
            "",
            "## Current Speaker",
        ]
        if sender_name:
            lines.append(f"- Name: {sender_name}")
        lines.append(f"- QQ: {sender_id}")
        if launcher_type and launcher_id:
            lines.append(f"- Chat Scope: {launcher_type}_{launcher_id}")
        lines.append(f"- Bound Student ID: {student_id or 'unbound'}")
        lines.append(f"- Password Saved: {'yes' if has_password else 'no'}")
        lines.append(f"- Library Default Location: {location or 'unset'}")
        lines.append(f"- Library Default Seat: {seat_no or 'unset'}")
        lines.append(f"- Seminar Mobile Saved: {'yes' if has_mobile else 'no'}")
        lines.extend(
            [
                "",
                "## Rules",
                "- Never reuse another user's bound account from earlier group messages.",
                "- If the current speaker is unbound, ask them to run setup_account before account-specific actions.",
                "- If the user asks about their own account or schedule, interpret it as the current speaker above.",
            ]
        )
        return "\n".join(lines)

    @staticmethod
    def _normalize_launcher_type(value: object) -> str:
        raw = getattr(value, "value", value)
        text = str(raw or "").strip().lower()
        if text.endswith(".group"):
            return "group"
        if text.endswith(".person"):
            return "person"
        return text

    @staticmethod
    def _extract_sender_name(event: object) -> str:
        message_event = getattr(event, "message_event", None)
        sender = getattr(message_event, "sender", None)
        member_name = getattr(sender, "member_name", None)
        if member_name:
            return str(member_name)
        nickname = getattr(sender, "nickname", None)
        if nickname:
            return str(nickname)
        return ""

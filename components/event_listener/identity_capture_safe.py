from __future__ import annotations

from typing import Any

from langbot_plugin.api.entities import context, events
from langbot_plugin.api.entities.builtin.platform import message as platform_message
from langbot_plugin.api.entities.builtin.provider import message as provider_message
from langbot_plugin.api.entities.builtin.provider import session as provider_session

from components.event_listener.identity_capture import IdentityCaptureListener
from henu_plugin.cli import inspect_cli_command


class SafeIdentityCaptureListener(IdentityCaptureListener):
    """Fresh request context injection and pre-LLM sensitive command handling."""

    _RUNTIME_MARKER = "[HENU_RUNTIME_CONTEXT_V2]"
    _SENSITIVE_PREFIXES = (
        "account set",
        "account bind",
        "calibration set",
        "calibrate set",
    )

    async def initialize(self):
        await super().initialize()

        @self.handler(events.PersonNormalMessageReceived)
        async def on_private_sensitive_command(ctx: context.EventContext):
            await self._maybe_handle_sensitive_command(ctx, is_group=False)

        @self.handler(events.GroupNormalMessageReceived)
        async def on_group_sensitive_command(ctx: context.EventContext):
            await self._maybe_handle_sensitive_command(ctx, is_group=True)

    async def _alter_user_message(self, ctx: context.EventContext) -> None:
        # Do not persist wall-clock time, account state, QQ IDs, or credentials in
        # the user's conversation history. Runtime data belongs in this request's
        # temporary system prompt only.
        return None

    async def _inject_current_sender_context(self, ctx: context.EventContext) -> None:
        query_vars = await self._safe_get_query_vars(ctx)
        original_text = str(
            query_vars.get("user_message_text")
            or query_vars.get("text_message")
            or ""
        ).strip()
        if not original_text:
            return
        if self._prompt_has_runtime_marker(ctx.event.default_prompt):
            return

        runtime_context = await self._get_or_create_runtime_context(ctx)
        if not isinstance(runtime_context, dict):
            return
        prompt_block = self._format_minimal_runtime_prompt(runtime_context)
        if not prompt_block:
            return
        ctx.event.default_prompt.append(
            provider_message.Message(role="system", content=prompt_block)
        )

    async def _get_or_create_runtime_context(self, ctx: context.EventContext) -> dict | None:
        query_vars = await self._safe_get_query_vars(ctx)
        cached = query_vars.get("_henu_runtime_context")
        if (
            isinstance(cached, dict)
            and cached.get("server_time")
            and cached.get("request_query_id") == ctx.query_id
        ):
            return cached

        service = getattr(self.plugin, "service", None)
        if service is None:
            return None

        launcher_type = self._normalize_launcher_type(
            query_vars.get("launcher_type") or query_vars.get("henu_launcher_type")
        )
        launcher_id = str(
            query_vars.get("launcher_id") or query_vars.get("henu_launcher_id") or ""
        ).strip()
        sender_id = str(
            query_vars.get("sender_id") or query_vars.get("henu_sender_id") or ""
        ).strip()
        if launcher_type not in {"group", "person"} or not launcher_id:
            return None
        if launcher_type == "group" and not sender_id:
            return None
        if not sender_id:
            sender_id = launcher_id

        try:
            session = provider_session.Session(
                launcher_type=provider_session.LauncherTypes(launcher_type),
                launcher_id=launcher_id,
                sender_id=sender_id,
            )
        except Exception:
            return None

        identity_hint = {
            "sender_id": sender_id,
            "launcher_id": launcher_id,
            "launcher_type": launcher_type,
        }
        runtime_context = await self._run_with_user_storage(
            session,
            identity_hint,
            service.get_runtime_context,
            session,
            identity_hint,
            self._DEFAULT_TIMEZONE,
        )
        if not isinstance(runtime_context, dict):
            return None
        runtime_context["request_query_id"] = ctx.query_id

        try:
            await ctx.set_query_var("_henu_runtime_context", runtime_context)
            await ctx.set_query_var(
                "_henu_time_context",
                runtime_context.get("server_time")
                if isinstance(runtime_context.get("server_time"), dict)
                else {},
            )
        except Exception:
            pass
        return runtime_context

    def _format_minimal_runtime_prompt(self, runtime_context: dict[str, Any]) -> str:
        server_time = runtime_context.get("server_time")
        account = runtime_context.get("account")
        if not isinstance(server_time, dict):
            return ""
        if not isinstance(account, dict):
            account = {}

        now_iso = str(server_time.get("now_iso") or "").strip()
        now_text = str(server_time.get("now_text") or "").strip()
        weekday = str(server_time.get("weekday_cn") or "").strip()
        timezone = str(
            server_time.get("timezone_effective")
            or server_time.get("timezone")
            or self._DEFAULT_TIMEZONE
        )
        if not now_iso and not now_text:
            return ""

        return "\n".join(
            [
                self._RUNTIME_MARKER,
                f"campus_timezone={timezone}",
                f"campus_now={now_iso or now_text}",
                f"campus_weekday={weekday or '未知'}",
                f"account_bound={'true' if account.get('is_bound') else 'false'}",
                "Interpret 今天/明天/现在/当前/是否过期 only from this block.",
                "Never reuse an older HENU runtime block from conversation history.",
                "Use henu_cli for campus data; external writes require a later user confirmation token.",
            ]
        )

    async def _maybe_handle_sensitive_command(
        self,
        ctx: context.EventContext,
        *,
        is_group: bool,
    ) -> None:
        text = str(getattr(ctx.event, "text_message", "") or "").strip()
        compact = " ".join(text.lower().split())
        if not compact.startswith(self._SENSITIVE_PREFIXES):
            return

        if is_group:
            self._reply_and_stop(
                ctx,
                "账号密码、Cookie 和校准请求只能在私聊中提交；本条消息未发送给模型，也未执行。",
            )
            return

        spec = inspect_cli_command(text)
        if spec.error or spec.resolved_tool not in {"setup_account", "set_calibration_source"}:
            self._reply_and_stop(ctx, spec.error or "敏感命令格式无效。")
            return

        event = ctx.event
        launcher_type = self._normalize_launcher_type(
            getattr(event, "launcher_type", "") or "person"
        )
        launcher_id = str(getattr(event, "launcher_id", "") or "").strip()
        sender_id = str(getattr(event, "sender_id", "") or launcher_id).strip()
        if not launcher_id or not sender_id:
            self._reply_and_stop(ctx, "无法确认当前私聊身份，已拒绝执行。")
            return

        try:
            session = provider_session.Session(
                launcher_type=provider_session.LauncherTypes(launcher_type),
                launcher_id=launcher_id,
                sender_id=sender_id,
            )
            identity_hint = {
                "sender_id": sender_id,
                "launcher_id": launcher_id,
                "launcher_type": launcher_type,
            }
            result = await self._run_with_user_storage(
                session,
                identity_hint,
                self.plugin.service.run_tool,
                spec.resolved_tool,
                spec.params,
                session,
                ctx.query_id,
                identity_hint,
            )
        except Exception as exc:
            self._reply_and_stop(ctx, f"敏感命令处理失败: {exc}")
            return

        if isinstance(result, dict):
            if result.get("success"):
                message = str(result.get("msg") or "操作完成")
                message += "。该命令已在调用模型前处理，密码或 Cookie 未进入模型上下文。"
            else:
                message = str(result.get("msg") or "操作失败")
        else:
            message = "操作失败：服务返回了异常结果。"
        self._reply_and_stop(ctx, message)

    @staticmethod
    def _reply_and_stop(ctx: context.EventContext, text: str) -> None:
        ctx.prevent_default()
        ctx.event.reply_message_chain = platform_message.MessageChain(
            [platform_message.Plain(text=str(text or "操作失败"))]
        )

    def _prompt_has_runtime_marker(self, messages: list[Any]) -> bool:
        return any(self._RUNTIME_MARKER in self._message_text(message) for message in messages)

    @staticmethod
    def _message_text(message: Any) -> str:
        content = getattr(message, "content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "\n".join(
                str(getattr(item, "text", "") or "") for item in content
            )
        return str(content or "")

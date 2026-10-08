from __future__ import annotations

import hashlib
import re
import time
from typing import Any

from langbot_plugin.api.entities import context, events
from langbot_plugin.api.entities.builtin.platform import message as platform_message
from langbot_plugin.api.entities.builtin.provider import message as provider_message
from langbot_plugin.api.entities.builtin.provider import session as provider_session

from components.event_listener.identity_capture import IdentityCaptureListener
from henu_plugin.cli import inspect_cli_command
from henu_plugin.yuketang_login import YuketangLoginCoordinator
from henu_plugin.confirmation_shortcut import ConfirmationShortcut
from henu_plugin.kit_binding import KitBindingCoordinator


class SafeIdentityCaptureListener(IdentityCaptureListener):
    """Fresh request context injection and pre-LLM sensitive command handling."""

    _RUNTIME_MARKER = "[HENU_RUNTIME_CONTEXT_V2]"
    _SENSITIVE_PREFIXES = (
        "account set",
        "account bind",
        "calibration set",
        "calibrate set",
        "yuketang account set",  # 含密码
        "yuketang login",        # 登录动作，仅私聊直处理
        "yuketang 登录",
    )
    _LOGIN_INTENT_PATTERN = re.compile(
        r"^(?:(?:帮我|我要|请|想要|需要)\s*)?"
        r"(?:登[录陆]|登录账号|绑定账号|绑定学号)[吧吗呀啊。！!？?]*$"
    )
    _STUDENT_ID_FIELD_PATTERN = re.compile(
        r"(?is)(?:学号|student[\s_-]*id|账号)\s*[:：=]?\s*\d{8,14}"
    )
    _PASSWORD_FIELD_PATTERN = re.compile(
        r"(?is)(?:密码|password|passwd)\s*[:：=]?\s*\S{6,}"
    )
    _BARE_CREDENTIAL_PATTERN = re.compile(
        r"^\s*\d{8,14}\s+\S{6,}\s*$"
    )
    _PASSWORD_OPTION_PATTERN = re.compile(
        r"(?i)(--password(?:\s+|=))(?:'[^']*'|\"[^\"]*\"|\S+)"
    )
    _STUDENT_OPTION_PATTERN = re.compile(
        r"(?i)(--student-id(?:\s+|=))\d{8,14}"
    )
    _PASSWORD_VALUE_PATTERN = re.compile(
        r"(?is)((?:密码|password|passwd)\s*[:：=]?\s*)"
        r"(?:'[^']*'|\"[^\"]*\"|\S{6,})"
    )
    _STUDENT_VALUE_PATTERN = re.compile(
        r"(?is)((?:学号|student[\s_-]*id|账号)\s*[:：=]?\s*)\d{8,14}"
    )
    _CREDENTIAL_INTAKE_TTL_SECONDS = 300.0
    _credential_intake_deadlines: dict[str, float] = {}

    async def initialize(self):
        await super().initialize()
        self._yuketang_login = YuketangLoginCoordinator(self)
        self._confirmation_shortcut = ConfirmationShortcut(self)
        self._kit_binding = KitBindingCoordinator(self)

        @self.handler(events.PersonMessageReceived)
        async def on_private_yuketang(ctx: context.EventContext):
            if await self._kit_binding.handle(ctx, is_group=False):
                return
            if await self._confirmation_shortcut.handle(ctx):
                return
            await self._yuketang_login.handle(ctx, is_group=False)

        @self.handler(events.GroupMessageReceived)
        async def on_group_yuketang(ctx: context.EventContext):
            if await self._kit_binding.handle(ctx, is_group=True):
                return
            if await self._confirmation_shortcut.handle(ctx):
                return
            await self._yuketang_login.handle(ctx, is_group=True)

        @self.handler(events.PersonNormalMessageReceived)
        async def on_private_sensitive_command(ctx: context.EventContext):
            await self._maybe_handle_sensitive_command(ctx, is_group=False)

        @self.handler(events.GroupNormalMessageReceived)
        async def on_group_sensitive_command(ctx: context.EventContext):
            await self._maybe_handle_sensitive_command(ctx, is_group=True)

        @self.handler(events.NormalMessageResponded)
        async def redact_sensitive_model_output(ctx: context.EventContext):
            ctx.event.response_text = self._redact_sensitive_output(
                str(getattr(ctx.event, "response_text", "") or "")
            )

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
                f"account_login_check={self._login_status_value(account, 'login_verification')}",
                f"last_account_attempt={self._login_status_value(account, 'last_login_attempt')}",
                "The login check is historical evidence, not a guarantee of current session validity. "
                "A failed candidate binding does not replace an existing binding. "
                "Unknown means no verification record, not necessarily unbound. "
                "Use the current tool result for current validity; never ask for a password when the requested tool succeeds.",
                "Interpret 今天/明天/现在/当前/是否过期 only from this block.",
                "Never reuse an older HENU runtime block from conversation history.",
                "Use henu_cli for campus data; external writes require a later user confirmation token.",
            ]
        )

    @staticmethod
    def _login_status_value(account: dict, field: str) -> str:
        value = account.get(field)
        status = value.get('status') if isinstance(value, dict) else None
        return status if status in {'verified', 'failed', 'stale', 'unverified'} else 'unknown'

    async def _maybe_handle_sensitive_command(
        self,
        ctx: context.EventContext,
        *,
        is_group: bool,
    ) -> None:
        kit_binding = getattr(self, '_kit_binding', None)
        if kit_binding is not None and await kit_binding.handle(ctx, is_group=is_group):
            return
        if await self._confirmation_shortcut.handle(ctx):
            return
        if await self._yuketang_login.handle(ctx, is_group=is_group):
            return
        text = str(getattr(ctx.event, "text_message", "") or "").strip()
        compact = " ".join(text.lower().split())
        is_sensitive_command = compact.startswith(self._SENSITIVE_PREFIXES)
        looks_like_credentials = self._looks_like_free_form_credentials(text)
        is_login_intent = bool(self._LOGIN_INTENT_PATTERN.search(text))
        intake_key = self._credential_intake_key(ctx)
        pending_intake = False
        if not is_group and intake_key:
            pending_intake = await self._credential_intake_pending(intake_key)
            if compact in {"取消", "算了", "cancel"} and pending_intake:
                await self._clear_credential_intake(intake_key)
                self._reply_and_stop(ctx, "已取消账号凭据录入。")
                return
            if pending_intake and not is_sensitive_command:
                self._reply_and_stop(
                    ctx,
                    "登录意图后的下一条消息可能包含凭据，本条消息未发送给模型，也未执行。"
                    "请使用：account set --student-id <学号> --password <密码>；"
                    "如需退出请发送“取消”。",
                )
                return
        if not (is_sensitive_command or looks_like_credentials or is_login_intent):
            return

        if is_group:
            self._reply_and_stop(
                ctx,
                "账号密码、Cookie、校准请求、考试令牌和 yuketang 登录只能在私聊中执行；本条消息未发送给模型，也未执行。",
            )
            return

        if looks_like_credentials:
            self._reply_and_stop(
                ctx,
                "检测到可能包含账号密码的消息，本条消息未发送给模型，也未执行。"
                "请在当前私聊中使用：account set --student-id <学号> --password <密码>",
            )
            return

        if is_login_intent and not is_sensitive_command:
            if intake_key:
                await self._arm_credential_intake(intake_key)
            self._reply_and_stop(
                ctx,
                "登录凭据由插件在模型前直接处理。请在当前私聊中发送："
                "account set --student-id <学号> --password <密码>",
            )
            return

        spec = inspect_cli_command(text)
        if intake_key:
            await self._clear_credential_intake(intake_key)
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
                message = str(result.get("reply_text") or result.get("msg") or "操作完成")
                message += "\n该命令已在调用模型前处理，密码或 Cookie 未进入模型上下文。"
            else:
                message = str(result.get("reply_text") or result.get("msg") or "操作失败")
        else:
            message = "操作失败：服务返回了异常结果。"
        self._reply_and_stop(ctx, message)

    @classmethod
    def _looks_like_free_form_credentials(cls, text: str) -> bool:
        value = str(text or "").strip()
        if not value:
            return False
        return bool(
            (
                cls._STUDENT_ID_FIELD_PATTERN.search(value)
                and cls._PASSWORD_FIELD_PATTERN.search(value)
            )
            or cls._BARE_CREDENTIAL_PATTERN.fullmatch(value)
        )

    async def _credential_intake_pending(self, key: str) -> bool:
        deadline = float(type(self)._credential_intake_deadlines.get(key, 0.0) or 0.0)
        if deadline > time.time():
            return True

        storage_key = self._credential_intake_storage_key(key)
        plugin = getattr(self, "plugin", None)
        get_keys = getattr(plugin, "get_plugin_storage_keys", None)
        get_value = getattr(plugin, "get_plugin_storage", None)
        if not callable(get_keys) or not callable(get_value):
            type(self)._credential_intake_deadlines.pop(key, None)
            return False
        try:
            keys = await get_keys()
        except Exception:
            # A key-list outage cannot create a pending intake for a user who
            # has no process-local marker. Exact commands and credential-shaped
            # input remain independently intercepted.
            return False
        if storage_key not in set(keys or []):
            type(self)._credential_intake_deadlines.pop(key, None)
            return False
        try:
            raw = await get_value(storage_key)
            if isinstance(raw, (bytes, bytearray)):
                raw = bytes(raw).decode("ascii")
            deadline = float(str(raw or "0"))
        except Exception:
            # The marker exists, so a value-read failure must remain fail closed.
            return True
        if deadline <= time.time():
            type(self)._credential_intake_deadlines.pop(key, None)
            return False
        type(self)._credential_intake_deadlines[key] = deadline
        return True

    async def _arm_credential_intake(self, key: str) -> None:
        deadline = time.time() + self._CREDENTIAL_INTAKE_TTL_SECONDS
        type(self)._credential_intake_deadlines[key] = deadline
        plugin = getattr(self, "plugin", None)
        setter = getattr(plugin, "set_plugin_storage", None)
        if callable(setter):
            try:
                await setter(
                    self._credential_intake_storage_key(key),
                    repr(deadline).encode("ascii"),
                )
            except Exception:
                # The in-process guard remains active; the exact account command
                # is independently intercepted even if persistence is unavailable.
                pass

    async def _clear_credential_intake(self, key: str) -> None:
        type(self)._credential_intake_deadlines.pop(key, None)
        plugin = getattr(self, "plugin", None)
        setter = getattr(plugin, "set_plugin_storage", None)
        if callable(setter):
            try:
                await setter(self._credential_intake_storage_key(key), b"0")
            except Exception:
                pass

    @staticmethod
    def _credential_intake_storage_key(key: str) -> str:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
        return f"henu:credential-intake:v1:{digest}"

    @classmethod
    def _credential_intake_key(cls, ctx: context.EventContext) -> str:
        event = ctx.event
        launcher_type = cls._normalize_launcher_type(
            getattr(event, "launcher_type", "") or "person"
        )
        launcher_id = str(getattr(event, "launcher_id", "") or "").strip()
        sender_id = str(getattr(event, "sender_id", "") or launcher_id).strip()
        if launcher_type != "person" or not launcher_id or not sender_id:
            return ""
        return f"person:{launcher_id}:{sender_id}"

    @classmethod
    def _redact_sensitive_output(cls, text: str) -> str:
        """Defense in depth: never echo credentials produced by a model."""
        value = str(text or "")
        value = cls._PASSWORD_OPTION_PATTERN.sub(r"\1<已拦截>", value)
        value = cls._PASSWORD_VALUE_PATTERN.sub(r"\1<已拦截>", value)
        if re.fullmatch(
            r"\s*\d{8,14}\s+(?:'[^']*'|\"[^\"]*\"|\S{6,})\s*",
            value,
        ):
            return "<已拦截凭据>"
        if re.search(r"(?i)账号|登录|学号|密码|account|student|password|passwd", value):
            value = re.sub(
                r"(?<!\d)\d{8,14}\s+(?:'[^']*'|\"[^\"]*\"|\S{6,})(?!\S)",
                "<已拦截凭据>",
                value,
            )
        value = cls._STUDENT_OPTION_PATTERN.sub(r"\1<已拦截>", value)
        value = cls._STUDENT_VALUE_PATTERN.sub(r"\1<已拦截>", value)
        return value

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

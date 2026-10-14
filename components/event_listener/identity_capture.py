from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import re
import time
from typing import Any, Callable

from langbot_plugin.api.definition.components.common.event_listener import EventListener
from langbot_plugin.api.entities.builtin.platform import message as platform_message
from langbot_plugin.api.entities.builtin.provider import message as provider_message
from langbot_plugin.api.entities.builtin.provider import session as provider_session
from langbot_plugin.api.entities import context, events

from henu_plugin.storage_adapter import PluginStorageAdapter
from henu_plugin.service import set_current_user_paths
from henu_plugin.cli import (
    account_command_format_error,
    inspect_cli_command,
    is_account_set_intent,
)


logger = logging.getLogger(__name__)


def _resolve_storage_key(session: provider_session.Session, identity_hint: dict) -> str:
    """Resolve storage key from session and identity hint."""
    sender_id = str(identity_hint.get("sender_id") or session.sender_id or "").strip()
    launcher_id = str(identity_hint.get("launcher_id") or session.launcher_id or "").strip()
    qq = sender_id or launcher_id or "unknown"

    storage_key = re.sub(r"[^0-9A-Za-z._-]+", "_", qq).strip("._-")
    if not storage_key:
        storage_key = hashlib.sha1(qq.encode("utf-8")).hexdigest()[:16]
    return storage_key


class IdentityCaptureListener(EventListener):
    _DEFAULT_TIMEZONE = "Asia/Shanghai"
    _ACCOUNT_SET_COOLDOWN_SECONDS = 30.0
    _ACCOUNT_QUERY_PATTERNS = (
        re.compile(r"(我的|当前|现在|本账号).{0,8}(账号|绑定|学号|信息)"),
        re.compile(r"(账号|绑定).{0,6}(信息|状态|情况|详情)"),
        re.compile(r"(我).{0,6}(绑了|绑定了).{0,8}(什么|谁|哪个|哪一个)"),
        re.compile(r"(查|查看|看看|显示).{0,6}(账号|绑定|学号)"),
    )
    _ENRICH_KEYWORDS = (
        "河大",
        "学号",
        "账号",
        "绑定",
        "课表",
        "课程",
        "上课",
        "图书馆",
        "座位",
        "预约",
        "空教室",
        "空闲教室",
        "教室",
        "自习室",
        "上自习",
        "签到",
        "研讨室",
        "节次",
        "校准",
        "xiqueer",
        "今天",
        "明天",
        "现在",
        "当前",
        "下节",
        "下一节",
        "待签到",
        "过期",
    )
    _CLI_PREFIXES = (
        "help",
        "account",
        "preferences",
        "schedule",
        "library",
        "seminar",
        "empty_classroom",
        "空教室",
        "教室",
        "calibration",
        "status",
        "system",
        "whoami",
    )

    async def initialize(self):
        await super().initialize()
        self._account_command_tasks: dict[int, asyncio.Task[None]] = {}
        self._account_command_ack_claimed: set[int] = set()
        self._account_command_result_claimed: set[int] = set()
        self._account_user_tasks: dict[str, asyncio.Task[None]] = {}
        self._account_user_last_started: dict[str, float] = {}

        @self.handler(events.PersonMessageReceived)
        async def on_person_message(ctx: context.EventContext):
            if await self._maybe_handle_account_set(ctx):
                return
            await self._persist_identity(ctx)

        @self.handler(events.GroupMessageReceived)
        async def on_group_message(ctx: context.EventContext):
            if await self._maybe_handle_account_set(ctx):
                return
            await self._persist_identity(ctx)

        @self.handler(events.PersonNormalMessageReceived)
        async def on_person_normal_message(ctx: context.EventContext):
            if await self._maybe_handle_account_set(ctx):
                return
            await self._persist_identity(ctx)
            await self._alter_user_message(ctx)

        @self.handler(events.GroupNormalMessageReceived)
        async def on_group_normal_message(ctx: context.EventContext):
            if await self._maybe_handle_account_set(ctx):
                return
            await self._persist_identity(ctx)
            await self._alter_user_message(ctx)
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
        await self._prime_runtime_context_query_var(ctx)

    async def _prime_runtime_context_query_var(self, ctx: context.EventContext) -> None:
        cached = await self._safe_get_query_var(ctx, "_henu_runtime_context")
        if isinstance(cached, dict):
            return
        try:
            await ctx.set_query_var("_henu_runtime_context", {})
        except Exception:
            return

    async def _inject_current_sender_context(self, ctx: context.EventContext) -> None:
        query_vars = await self._safe_get_query_vars(ctx)
        if not self._should_inject_runtime_context(ctx.event, query_vars):
            return
        runtime_context = await self._get_or_create_runtime_context(ctx)
        if not isinstance(runtime_context, dict):
            return

        sender_name = self._resolve_sender_name_from_query_vars(query_vars, runtime_context)
        prompt_block = self._format_runtime_prompt_block(runtime_context, sender_name)
        if not prompt_block:
            return

        ctx.event.default_prompt.append(
            provider_message.Message(role="system", content=prompt_block)
        )

    async def _alter_user_message(self, ctx: context.EventContext) -> None:
        if getattr(ctx.event, "user_message_alter", None) is not None:
            return

        original_text = str(getattr(ctx.event, "text_message", "") or "").strip()
        if not original_text:
            return
        if not self._should_enrich_text(original_text):
            return

        runtime_context = await self._get_or_create_runtime_context(ctx)
        if not isinstance(runtime_context, dict):
            return

        query_vars = await self._safe_get_query_vars(ctx)
        sender_name = self._resolve_sender_name_from_query_vars(query_vars, runtime_context)
        altered = self._format_user_message_with_context(
            runtime_context,
            sender_name,
            original_text,
        )
        if altered:
            ctx.event.user_message_alter = altered

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

        result = await self._run_with_user_storage(
            session,
            identity_hint,
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
        await ctx.reply(
            platform_message.MessageChain(
                [platform_message.Plain(text=reply_text)]
            )
        )

    async def _maybe_handle_account_set(self, ctx: context.EventContext) -> bool:
        text = self._event_text(ctx.event)
        if not is_account_set_intent(text):
            return False

        # Stop the default pipeline and later plugins before any network await. A
        # slow IDS request must never let plaintext credentials fall through.
        ctx.prevent_default()
        ctx.prevent_postorder()

        query_id = int(getattr(ctx, "query_id", 0) or 0)
        correction = account_command_format_error(text)
        launcher_type = self._normalize_launcher_type(
            getattr(ctx.event, "launcher_type", "")
        )
        spec = inspect_cli_command(text) if not correction else None
        if launcher_type != "person":
            message = (
                "账号密码只允许在私聊中绑定。请撤回群内凭据，并在私聊使用："
                "`account set --student-id <学号> --password '<密码>'`。"
            )
            return await self._reply_account_command_once(ctx, query_id, message)
        sender_id = str(getattr(ctx.event, "sender_id", "") or "").strip()
        if not sender_id:
            return await self._reply_account_command_once(
                ctx,
                query_id,
                "无法确认当前私聊发送者身份，本次未执行登录；请重新打开私聊后再试。",
            )
        if correction:
            return await self._reply_account_command_once(
                ctx, query_id, correction
            )
        if spec is None or spec.resolved_tool != "setup_account":
            message = (spec.error if spec is not None else "") or (
                "不会执行非结构化登录信息。请使用："
                "`account set --student-id <学号> --password '<密码>'`。"
            )
            return await self._reply_account_command_once(ctx, query_id, message)

        if query_id and query_id in self._account_command_tasks:
            return True

        account_key = self._account_command_key(ctx.event)
        user_tasks = getattr(self, "_account_user_tasks", {})
        existing_task = user_tasks.get(account_key)
        if existing_task is not None and not existing_task.done():
            return await self._reply_account_command_once(
                ctx,
                query_id,
                "已有账号验证正在进行，请等待本次结果，勿重复发送密码。",
            )

        now = time.monotonic()
        last_started = getattr(self, "_account_user_last_started", {}).get(
            account_key,
            0.0,
        )
        remaining = self._ACCOUNT_SET_COOLDOWN_SECONDS - (now - last_started)
        if remaining > 0:
            return await self._reply_account_command_once(
                ctx,
                query_id,
                f"账号验证请求过于频繁，请等待 {max(1, int(remaining + 0.999))} 秒后再试。",
            )

        start = asyncio.Event()
        task = asyncio.create_task(
            self._run_account_set_background(ctx, text, query_id, start)
        )
        if query_id:
            self._account_command_tasks[query_id] = task
        self._account_user_tasks[account_key] = task
        self._account_user_last_started[account_key] = now
        task.add_done_callback(
            lambda finished, key=account_key: self._release_account_user_task(
                key,
                finished,
            )
        )
        self._trim_account_command_state()

        ack = "已安全接收账号绑定命令，正在验证；请勿重复发送密码。"
        if not query_id or query_id not in self._account_command_ack_claimed:
            if query_id:
                self._account_command_ack_claimed.add(query_id)
            try:
                await ctx.reply(
                    platform_message.MessageChain(
                        [platform_message.Plain(text=ack)]
                    )
                )
            except Exception as exc:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
                logger.warning(
                    "[henu.account_set.ack] delivery failed: %s",
                    type(exc).__name__,
                )
                return True
        start.set()
        return True

    async def _reply_account_command_once(
        self, ctx: context.EventContext, query_id: int, message: str
    ) -> bool:
        if query_id and query_id in self._account_command_result_claimed:
            return True
        if query_id:
            self._account_command_result_claimed.add(query_id)
        try:
            await ctx.reply(
                platform_message.MessageChain([platform_message.Plain(text=message)])
            )
        except Exception as exc:
            logger.warning(
                "[henu.account_set.reject] delivery failed: %s",
                type(exc).__name__,
            )
        return True

    async def _run_account_set_background(
        self,
        ctx: context.EventContext,
        text: str,
        query_id: int,
        start: asyncio.Event,
    ) -> None:
        try:
            await start.wait()
            message = await self._account_set_response(ctx, text)
            if query_id and query_id in self._account_command_result_claimed:
                return
            # Claim before awaiting the adapter reply so duplicate event handlers
            # cannot race into a second result message.
            if query_id:
                self._account_command_result_claimed.add(query_id)
            await ctx.reply(
                platform_message.MessageChain(
                    [platform_message.Plain(text=message)]
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "[henu.account_set.background] result delivery failed: %s",
                type(exc).__name__,
            )

    def _trim_account_command_state(self) -> None:
        if len(self._account_command_tasks) <= 512:
            return
        keep = sorted(self._account_command_tasks)[-256:]
        self._account_command_tasks = {
            key: self._account_command_tasks[key] for key in keep
        }
        keep_set = set(keep)
        self._account_command_ack_claimed.intersection_update(keep_set)
        self._account_command_result_claimed.intersection_update(keep_set)
        if len(self._account_user_last_started) > 2048:
            newest = sorted(
                self._account_user_last_started.items(),
                key=lambda item: item[1],
                reverse=True,
            )[:1024]
            self._account_user_last_started = dict(newest)

    def _release_account_user_task(
        self,
        account_key: str,
        task: asyncio.Task[None],
    ) -> None:
        if self._account_user_tasks.get(account_key) is task:
            self._account_user_tasks.pop(account_key, None)

    @staticmethod
    def _account_command_key(event: object) -> str:
        launcher_type = str(getattr(event, "launcher_type", "") or "")
        sender_id = str(getattr(event, "sender_id", "") or "").strip()
        launcher_id = str(getattr(event, "launcher_id", "") or "").strip()
        return f"{launcher_type}:{sender_id or launcher_id or 'unknown'}"

    async def _account_set_response(
        self, ctx: context.EventContext, text: str
    ) -> str:
        service = getattr(self.plugin, "service", None)
        if service is None:
            message = "账号服务未初始化，本次未执行登录。"
        else:
            event = ctx.event
            try:
                session = provider_session.Session(
                    launcher_type=provider_session.LauncherTypes(
                        event.launcher_type
                    ),
                    launcher_id=str(event.launcher_id),
                    sender_id=str(event.sender_id),
                )
                identity_hint = {
                    "sender_id": str(getattr(event, "sender_id", "") or ""),
                    "launcher_id": str(getattr(event, "launcher_id", "") or ""),
                    "launcher_type": str(
                        getattr(event, "launcher_type", "") or ""
                    ),
                }
                result = await self._run_with_user_storage(
                    session,
                    identity_hint,
                    service.run_tool,
                    "henu_cli",
                    {"command": text},
                    session,
                    ctx.query_id,
                    identity_hint,
                )
            except Exception as exc:
                result = {
                    "success": False,
                    "msg": f"账号绑定执行异常: {type(exc).__name__}",
                }
            message = str(
                (result or {}).get("reply_text")
                or (result or {}).get("msg")
                or "账号绑定未返回结果。"
            )
        return message

    @staticmethod
    def _event_text(event: object) -> str:
        text = str(getattr(event, "text_message", "") or "").strip()
        if text:
            return text
        message_chain = getattr(event, "message_chain", None)
        return str(message_chain or "").strip()

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
        login_verification = account.get("login_verification")
        if not isinstance(login_verification, dict):
            login_verification = {}
        login_status = str(login_verification.get("status") or "unknown")
        login_status_label = {
            "verified": "成功",
            "stale": "曾成功，可能已过期",
            "unverified": "未执行",
            "failed": "失败",
        }.get(login_status, "无记录")
        login_attempted_at = str(login_verification.get("attempted_at") or "").strip()
        login_error_code = str(login_verification.get("error_code") or "").strip()
        last_login_attempt = account.get("last_login_attempt")
        if not isinstance(last_login_attempt, dict):
            last_login_attempt = {}
        last_attempt_status = str(last_login_attempt.get("status") or "unknown")
        last_attempt_label = {
            "verified": "成功",
            "unverified": "未执行校验",
            "failed": "失败",
        }.get(last_attempt_status, "无记录")
        last_attempt_time = str(last_login_attempt.get("attempted_at") or "").strip()
        last_attempt_error = str(last_login_attempt.get("error_code") or "").strip()

        masked_qq = self._mask_identifier(qq)
        masked_student_id = self._mask_identifier(student_id)

        lines = ["当前账号绑定信息"]
        if masked_qq:
            lines.append(f"QQ: {masked_qq}")
        lines.append(f"学号: {masked_student_id or '未绑定'}")
        lines.append(f"密码: {'已保存' if has_password else '未保存'}")
        lines.append(f"图书馆默认区域: {location or '未设置'}")
        lines.append(f"图书馆默认座位: {seat_no or '未设置'}")
        lines.append(f"研讨室手机号: {'已保存' if has_mobile else '未保存'}")
        login_line = f"最近登录校验: {login_status_label}"
        if login_attempted_at:
            login_line += f"（{login_attempted_at}）"
        if login_error_code:
            login_line += f"；错误码: {login_error_code}"
        lines.append(login_line)
        attempt_line = f"最近账号操作结果: {last_attempt_label}"
        if last_attempt_time:
            attempt_line += f"（{last_attempt_time}）"
        if last_attempt_error:
            attempt_line += f"；错误码: {last_attempt_error}"
        lines.append(attempt_line)

        return "\n".join(lines)

    def _format_runtime_prompt_block(self, runtime_context: dict, sender_name: str) -> str:
        binding = runtime_context.get("binding") if isinstance(runtime_context, dict) else {}
        account = runtime_context.get("account") if isinstance(runtime_context, dict) else {}
        server_time = runtime_context.get("server_time") if isinstance(runtime_context, dict) else {}
        if not isinstance(binding, dict):
            binding = {}
        if not isinstance(account, dict):
            account = {}
        if not isinstance(server_time, dict):
            server_time = {}

        sender_id = str(binding.get("sender_id") or binding.get("qq") or "").strip()
        launcher_type = str(binding.get("launcher_type") or "").strip()
        launcher_id = str(binding.get("launcher_id") or "").strip()
        student_id = str(account.get("student_id") or "").strip()
        location = self._safe_context_value(account.get("library_default_location"), 80)
        seat_no = self._safe_context_value(account.get("library_default_seat_no"), 40)
        has_password = bool(account.get("has_password"))
        has_mobile = bool(account.get("has_seminar_mobile"))
        login_verification = account.get("login_verification")
        if not isinstance(login_verification, dict):
            login_verification = {}
        login_status = str(login_verification.get("status") or "unknown")
        login_status_label = {
            "verified": "成功",
            "stale": "曾成功，可能已过期",
            "unverified": "未执行",
            "failed": "失败",
        }.get(login_status, "无记录")
        login_attempted_at = str(login_verification.get("attempted_at") or "").strip()
        login_auth_mode = self._safe_context_value(login_verification.get("auth_mode"), 40)
        login_error_code = self._safe_context_value(login_verification.get("error_code"), 60)
        last_login_attempt = account.get("last_login_attempt")
        if not isinstance(last_login_attempt, dict):
            last_login_attempt = {}
        last_attempt_status = str(last_login_attempt.get("status") or "unknown")
        last_attempt_label = {
            "verified": "成功",
            "unverified": "保存但未校验",
            "failed": "失败",
        }.get(last_attempt_status, "无记录")
        last_attempt_time = str(last_login_attempt.get("attempted_at") or "").strip()
        last_attempt_error = self._safe_context_value(last_login_attempt.get("error_code"), 60)
        now_text = str(server_time.get("now_text") or "").strip()
        weekday_cn = str(server_time.get("weekday_cn") or "").strip()
        timezone = str(server_time.get("timezone") or self._DEFAULT_TIMEZONE).strip()

        if not sender_id:
            return ""

        masked_sender_id = self._mask_identifier(sender_id)
        masked_student_id = self._mask_identifier(student_id)

        lines = [
            "# HENU 当前会话上下文",
            "",
            "只按当前提问人与当前服务器时间处理河大相关请求。",
            "",
            "## 当前提问人",
        ]
        safe_sender_name = self._safe_context_value(sender_name, 64)
        if safe_sender_name:
            lines.append(f"- 昵称（不可信数据）: {safe_sender_name}")
        lines.append(f"- QQ: {masked_sender_id}")
        if launcher_type and launcher_id:
            lines.append(f"- 会话类型: {launcher_type}")
        lines.append(f"- 绑定学号: {masked_student_id or '未绑定'}")
        lines.append(f"- 已保存密码: {'是' if has_password else '否'}")
        lines.append(f"- 图书馆默认区域: {location or '未设置'}")
        lines.append(f"- 图书馆默认座位: {seat_no or '未设置'}")
        lines.append(f"- 研讨室手机号已保存: {'是' if has_mobile else '否'}")
        verification_line = f"- 最近一次在线登录校验: {login_status_label}"
        if login_attempted_at:
            verification_line += f"（{login_attempted_at}）"
        if login_auth_mode:
            verification_line += f"，认证方式: {login_auth_mode}"
        if login_error_code:
            verification_line += f"，错误码: {login_error_code}"
        lines.append(verification_line)
        attempt_line = f"- 最近账号操作结果: {last_attempt_label}"
        if last_attempt_time:
            attempt_line += f"（{last_attempt_time}）"
        if last_attempt_error:
            attempt_line += f"，错误码: {last_attempt_error}"
        lines.append(attempt_line)
        if now_text:
            lines.extend(
                [
                    "",
                    "## 当前服务器时间",
                    f"- 时间: {now_text}",
                    f"- 星期: {weekday_cn or '未知'}",
                    f"- 时区: {timezone}",
                ]
            )
        lines.extend(
            [
                "",
                "## 执行规则",
                "- 不要沿用其他群成员的账号、预约或签到状态。",
                "- “我的/今天/明天/现在/当前/待签到/是否过期”都只按上面的提问人与服务器时间理解。",
                "- 未绑定学号时，只接受结构化私聊命令 `account set --student-id 20230001 --password '实际密码'`；不要保留示例尖括号。",
                "- 最近一次在线登录校验为成功时，不要把该账号说成未登录；业务操作是否仍有效，以对应工具的实时返回为准。",
                "- 登录校验标为“可能已过期”时，只能说明历史验证成功，当前有效性必须用对应工具实时确认。",
                "- 最近账号操作失败时要明确告诉用户失败；若仍有旧绑定，不要把失败的新账号尝试冒充成旧绑定结果。",
                "- 优先使用 `henu_cli` 的窄命令；不确定时先 `help`。",
            ]
        )
        return "\n".join(lines)

    def _format_user_message_with_context(
        self,
        runtime_context: dict,
        sender_name: str,
        original_text: str,
    ) -> str:
        binding = runtime_context.get("binding") if isinstance(runtime_context, dict) else {}
        account = runtime_context.get("account") if isinstance(runtime_context, dict) else {}
        server_time = runtime_context.get("server_time") if isinstance(runtime_context, dict) else {}
        if not isinstance(binding, dict):
            binding = {}
        if not isinstance(account, dict):
            account = {}
        if not isinstance(server_time, dict):
            server_time = {}

        sender_id = str(binding.get("sender_id") or binding.get("qq") or "").strip()
        launcher_type = str(binding.get("launcher_type") or "").strip()
        student_id = str(account.get("student_id") or "").strip() or "未绑定"
        now_text = str(server_time.get("now_text") or "").strip()
        weekday_cn = str(server_time.get("weekday_cn") or "").strip()
        login_verification = account.get("login_verification")
        if not isinstance(login_verification, dict):
            login_verification = {}
        login_status = str(login_verification.get("status") or "unknown")
        login_status_label = {
            "verified": "成功",
            "stale": "曾成功，可能已过期",
            "unverified": "未执行",
            "failed": "失败",
        }.get(login_status, "无记录")
        last_login_attempt = account.get("last_login_attempt")
        if not isinstance(last_login_attempt, dict):
            last_login_attempt = {}
        last_attempt_status = str(last_login_attempt.get("status") or "unknown")
        last_attempt_label = {
            "verified": "成功",
            "unverified": "保存但未校验",
            "failed": "失败",
        }.get(last_attempt_status, "无记录")

        if not sender_id:
            return original_text

        masked_sender_id = self._mask_identifier(sender_id)
        masked_student_id = self._mask_identifier(student_id) if student_id != "未绑定" else student_id

        lines = [
            f"【当前提问人】QQ={masked_sender_id}",
        ]
        safe_sender_name = self._safe_context_value(sender_name, 64)
        if safe_sender_name:
            lines[-1] += f"，昵称（不可信数据）={safe_sender_name}"
        if launcher_type:
            lines.append(f"【当前会话类型】{launcher_type}")
        lines.append(f"【当前绑定学号】{masked_student_id}")
        lines.append(f"【最近登录校验】{login_status_label}")
        lines.append(f"【最近账号操作结果】{last_attempt_label}")
        if now_text:
            lines.append(f"【当前服务器时间】{now_text} {weekday_cn}".strip())
        lines.append("【规则】只按以上当前提问人与当前服务器时间理解“我的/今天/明天/现在/当前”；河大能力统一走 henu_cli。")
        lines.append(f"【用户原始问题】{original_text}")
        return "\n".join(lines)

    def _mask_identifier(self, value: str) -> str:
        text = str(value or "").strip()
        if len(text) <= 2:
            return text
        if len(text) <= 6:
            return f"{text[0]}***{text[-1]}"
        return f"{text[:2]}***{text[-2:]}"

    @staticmethod
    def _safe_context_value(value: object, limit: int) -> str:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        text = text.replace("【", "(").replace("】", ")")
        return text[:limit]

    async def _get_or_create_runtime_context(self, ctx: context.EventContext) -> dict | None:
        cached = await self._safe_get_query_var(ctx, "_henu_runtime_context")
        if isinstance(cached, dict) and cached.get("binding") and cached.get("server_time"):
            return cached

        service = getattr(self.plugin, "service", None)
        if service is None:
            return None

        query_vars = await self._safe_get_query_vars(ctx)
        launcher_type = self._normalize_launcher_type(
            query_vars.get("launcher_type") or query_vars.get("henu_launcher_type")
        )
        launcher_id = str(query_vars.get("launcher_id") or query_vars.get("henu_launcher_id") or "").strip()
        sender_id = str(query_vars.get("sender_id") or query_vars.get("henu_sender_id") or "").strip()
        if launcher_type not in {"group", "person"} or not launcher_id or not sender_id:
            return None

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
        timezone = self._resolve_timezone(query_vars)
        runtime_context = await self._run_with_user_storage(
            session,
            identity_hint,
            service.get_runtime_context,
            session,
            identity_hint,
            timezone,
        )
        if not isinstance(runtime_context, dict):
            return None

        sender_name = self._resolve_sender_name_from_query_vars(query_vars, runtime_context)
        binding = runtime_context.get("binding") if isinstance(runtime_context.get("binding"), dict) else {}
        account = runtime_context.get("account") if isinstance(runtime_context.get("account"), dict) else {}
        server_time = runtime_context.get("server_time") if isinstance(runtime_context.get("server_time"), dict) else {}

        await ctx.set_query_var("_henu_runtime_context", runtime_context)
        await ctx.set_query_var(
            "_henu_identity_context",
            {
                "speaker": {
                    "id": sender_id,
                    "name": sender_name,
                },
                "binding": binding,
                "account": account,
            },
        )
        await ctx.set_query_var("_henu_time_context", server_time)
        return runtime_context

    async def _run_with_user_storage(
        self,
        session: provider_session.Session,
        identity_hint: dict[str, Any],
        func: Callable[..., Any],
        *args: Any,
    ) -> Any:
        storage_key = _resolve_storage_key(session, identity_hint)
        storage_adapter = PluginStorageAdapter(self.plugin, storage_key)
        user_paths = await storage_adapter.load_all()
        set_current_user_paths(user_paths)
        try:
            return await asyncio.to_thread(func, *args)
        finally:
            try:
                await storage_adapter.save_all()
            finally:
                set_current_user_paths(None)

    async def _safe_get_query_var(self, ctx: context.EventContext, key: str) -> object:
        try:
            query_vars = await ctx.get_query_vars()
            if isinstance(query_vars, dict):
                return query_vars.get(key)
        except Exception:
            return None
        return None

    async def _safe_get_query_vars(self, ctx: context.EventContext) -> dict[str, object]:
        try:
            query_vars = await ctx.get_query_vars()
        except Exception:
            return {}
        return query_vars if isinstance(query_vars, dict) else {}

    def _resolve_sender_name_from_query_vars(
        self,
        query_vars: dict[str, object],
        runtime_context: dict,
    ) -> str:
        sender_name = str(query_vars.get("sender_name") or query_vars.get("henu_sender_name") or "").strip()
        if sender_name:
            return sender_name

        binding = runtime_context.get("binding") if isinstance(runtime_context, dict) else {}
        if not isinstance(binding, dict):
            return ""
        return str(binding.get("sender_name") or "").strip()

    def _resolve_timezone(self, query_vars: dict[str, object]) -> str:
        timezone = str(query_vars.get("timezone") or query_vars.get("henu_timezone") or "").strip()
        return timezone or self._DEFAULT_TIMEZONE

    def _should_enrich_event(self, event: object) -> bool:
        text = str(getattr(event, "text_message", "") or "").strip()
        return self._should_enrich_text(text)

    def _should_inject_runtime_context(
        self,
        event: object,
        query_vars: dict[str, object],
    ) -> bool:
        launcher_type = self._normalize_launcher_type(
            query_vars.get("launcher_type")
            or query_vars.get("henu_launcher_type")
            or getattr(event, "launcher_type", "")
        )
        # Private follow-ups such as “好了没” or “1” still need the result of a
        # just-finished account command, even though they have no HENU keyword.
        return launcher_type == "person" or self._should_enrich_event(event)

    def _should_enrich_text(self, text: str) -> bool:
        normalized = re.sub(r"\s+", "", text or "").lower()
        if not normalized:
            return False
        if normalized.startswith(self._CLI_PREFIXES):
            return True
        return any(keyword in normalized for keyword in self._ENRICH_KEYWORDS)

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

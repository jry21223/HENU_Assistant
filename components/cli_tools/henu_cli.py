from __future__ import annotations

import json
import shlex
from typing import Any

from components.cli_tools.base import BaseHenuTool, _resolve_storage_key
from henu_plugin.cli import inspect_cli_command, redact_cli_command
from henu_plugin.confirmation import (
    WRITE_TOOL_NAMES,
    create_pending_operation,
    pending_storage_key,
    split_confirm_token,
    validate_pending_operation,
)


class HenuCli(BaseHenuTool):
    tool_name = "henu_cli"

    def should_preload_runtime_context(self, params):
        command = params.get("command") if isinstance(params, dict) else ""
        clean_command, _ = split_confirm_token(command)
        if self._is_confirmation_command(clean_command):
            return True
        spec = inspect_cli_command(clean_command)
        return spec.should_preload_runtime_context

    async def call(self, params, session, query_id):
        if not isinstance(params, dict):
            return {"success": False, "msg": "参数必须是对象"}

        identity_hint = await self._load_identity_hint(query_id)
        identity_error = self._validate_identity(session, identity_hint)
        if identity_error:
            return {"success": False, "error_code": "identity_missing", "msg": identity_error}

        raw_command = str(params.get("command") or "").strip()
        if not raw_command:
            return await super().call(params, session, query_id)

        storage_key = _resolve_storage_key(session, identity_hint)
        if self._is_confirmation_command(raw_command):
            return await self._execute_confirmation(
                raw_command=raw_command,
                storage_key=storage_key,
                session=session,
                query_id=query_id,
            )

        clean_command, inline_token = split_confirm_token(raw_command)
        spec = inspect_cli_command(clean_command)

        # Account passwords and calibration cookies are handled directly by the
        # event listener before the model is called. Fail closed if a provider
        # nevertheless attempts to invoke them through the Tool surface.
        if spec.resolved_tool in {"setup_account", "set_calibration_source"}:
            return {
                "success": False,
                "error_code": "direct_private_command_required",
                "msg": "该敏感命令必须在私聊中直接发送，由插件拦截处理，不能经由模型 Tool 调用。",
                "reply_text": "请在私聊中直接发送绑定或校准命令；插件会在调用模型前处理，不会把密码或 Cookie 发给模型。",
            }

        if spec.resolved_tool in WRITE_TOOL_NAMES:
            if inline_token:
                return await self._execute_pending(
                    token=inline_token,
                    storage_key=storage_key,
                    canonical_command=clean_command,
                    session=session,
                    query_id=query_id,
                )
            return await self._request_confirmation(
                storage_key=storage_key,
                canonical_command=clean_command,
                action=spec.action,
                query_id=query_id,
            )

        result = await super().call({"command": clean_command}, session, query_id)
        return self._mark_storage_commit_state(result)

    async def _request_confirmation(
        self,
        *,
        storage_key: str,
        canonical_command: str,
        action: str,
        query_id: int,
    ) -> dict[str, Any]:
        pending = create_pending_operation(
            storage_key=storage_key,
            canonical_command=canonical_command,
            query_id=query_id,
        )
        key = pending_storage_key(storage_key)
        try:
            await self.plugin.set_plugin_storage(
                key,
                json.dumps(pending, ensure_ascii=False).encode("utf-8"),
            )
        except Exception as exc:
            return {
                "success": False,
                "error_code": "confirmation_storage_failed",
                "msg": f"保存待确认操作失败: {exc}",
            }

        token = str(pending["token"])
        confirm_command = f"confirm {token}"
        action_text = action or "外部写操作"
        return {
            "success": False,
            "confirmation_required": True,
            "error_code": "confirmation_required",
            "msg": f"{action_text} 尚未执行，需要用户在下一条消息中明确确认。",
            "reply_text": (
                f"即将执行：{action_text}。本次尚未提交。"
                f"请核对日期、时间、地点和对象；确认无误后回复：{confirm_command}"
            ),
            "confirmation_command": confirm_command,
            "expires_in_seconds": 300,
            "retry_safe": True,
            "llm_hint": "必须等待用户发送新的确认消息；禁止在当前轮次自动调用 confirm。",
            "cli": {"mode": "confirmation", "command": redact_cli_command(canonical_command)},
        }

    async def _execute_confirmation(
        self,
        *,
        raw_command: str,
        storage_key: str,
        session,
        query_id: int,
    ) -> dict[str, Any]:
        try:
            argv = shlex.split(raw_command)
        except ValueError as exc:
            return {"success": False, "msg": f"确认命令解析失败: {exc}"}
        if len(argv) != 2 or argv[0].lower() not in {"confirm", "确认"}:
            return {"success": False, "msg": "确认命令格式为 `confirm <token>`。"}
        pending = await self._load_pending(storage_key)
        canonical_command = str(pending.get("command") or "") if isinstance(pending, dict) else ""
        return await self._execute_pending(
            token=argv[1],
            storage_key=storage_key,
            canonical_command=canonical_command,
            session=session,
            query_id=query_id,
            pending=pending,
        )

    async def _execute_pending(
        self,
        *,
        token: str,
        storage_key: str,
        canonical_command: str,
        session,
        query_id: int,
        pending: Any = None,
    ) -> dict[str, Any]:
        if pending is None:
            pending = await self._load_pending(storage_key)
        check = validate_pending_operation(
            pending,
            token=token,
            storage_key=storage_key,
            canonical_command=canonical_command,
            query_id=query_id,
        )
        if not check.ok:
            return {
                "success": False,
                "error_code": "confirmation_invalid",
                "msg": check.message,
                "retry_safe": True,
            }

        spec = inspect_cli_command(canonical_command)
        if spec.resolved_tool not in WRITE_TOOL_NAMES:
            return {
                "success": False,
                "error_code": "confirmation_scope_invalid",
                "msg": "待确认内容不是允许的外部写操作，已拒绝执行。",
            }

        result = await super().call({"command": canonical_command}, session, query_id)
        result = self._mark_storage_commit_state(result)
        if isinstance(result, dict) and (
            result.get("success") or result.get("external_committed")
        ):
            try:
                await self.plugin.set_plugin_storage(
                    pending_storage_key(storage_key), b"{}"
                )
            except Exception:
                # The Tool result already distinguishes an external commit from
                # local persistence. Do not mask a completed campus operation.
                result.setdefault("confirmation_cleanup_failed", True)
        return result

    async def _load_pending(self, storage_key: str) -> dict[str, Any]:
        try:
            raw = await self.plugin.get_plugin_storage(pending_storage_key(storage_key))
        except Exception:
            return {}
        if not raw:
            return {}
        try:
            if isinstance(raw, str):
                raw = raw.encode("utf-8")
            value = json.loads(bytes(raw).decode("utf-8"))
        except Exception:
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _is_confirmation_command(command: str) -> bool:
        text = str(command or "").lstrip().lower()
        return text.startswith("confirm ") or text.startswith("确认 ")

    @staticmethod
    def _validate_identity(session, identity_hint: dict[str, Any]) -> str:
        launcher_type = getattr(
            getattr(session, "launcher_type", ""),
            "value",
            getattr(session, "launcher_type", ""),
        )
        launcher_type = str(launcher_type or "").lower()
        sender_id = str(
            identity_hint.get("sender_id") or getattr(session, "sender_id", "") or ""
        ).strip()
        launcher_id = str(
            identity_hint.get("launcher_id") or getattr(session, "launcher_id", "") or ""
        ).strip()
        if launcher_type == "group" and sender_id in {"", "0", "None", "none"}:
            return "群聊缺少发送者身份，已拒绝访问任何个人账号或预约数据。"
        if sender_id in {"", "0", "None", "none"} and launcher_id in {
            "",
            "0",
            "None",
            "none",
        }:
            return "无法确认当前用户身份，已拒绝执行。"
        return ""

    @staticmethod
    def _mark_storage_commit_state(result: Any) -> Any:
        if not isinstance(result, dict):
            return result
        tool_result = result.get("tool_result")
        if (
            result.get("success") is False
            and isinstance(tool_result, dict)
            and tool_result.get("success") is True
        ):
            result["external_committed"] = True
            result["storage_persisted"] = False
            result["retry_safe"] = False
            result["msg"] = (
                "校园系统操作已经成功，但本地 Storage 保存失败。"
                "不要重复执行；请先查询当前记录进行反查。"
            )
        return result

    async def _prime_runtime_context_query_var(self, query_id: int) -> None:
        # Never erase the request snapshot created during PromptPreProcessing.
        return None

    async def _ensure_runtime_context(
        self,
        query_id: int,
        session,
        identity_hint: dict[str, Any],
        service: Any,
    ) -> dict[str, Any] | None:
        handler = getattr(self.plugin, "plugin_runtime_handler", None)
        if handler is None:
            return None

        from langbot_plugin.api.proxies.query_based_api import QueryBasedAPIProxy
        from henu_plugin.service import get_current_user_paths

        proxy = QueryBasedAPIProxy(query_id=query_id, plugin_runtime_handler=handler)
        try:
            query_vars = await proxy.get_query_vars()
        except Exception:
            query_vars = {}

        cached = query_vars.get("_henu_runtime_context") if isinstance(query_vars, dict) else None
        if (
            isinstance(cached, dict)
            and cached.get("server_time")
            and cached.get("request_query_id") == query_id
        ):
            return cached

        timezone = self._resolve_timezone(query_vars if isinstance(query_vars, dict) else {})
        runtime_context = await self._run_with_user_storage(
            get_current_user_paths(),
            service.get_runtime_context,
            session,
            identity_hint,
            timezone,
        )
        if isinstance(runtime_context, dict):
            runtime_context["request_query_id"] = query_id
            try:
                await proxy.set_query_var("_henu_runtime_context", runtime_context)
            except Exception:
                pass
            return runtime_context
        return None

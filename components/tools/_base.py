from __future__ import annotations

import asyncio
from typing import Any

from langbot_plugin.api.definition.components.tool.tool import Tool
from langbot_plugin.api.entities.builtin.provider import session as provider_session
from langbot_plugin.api.proxies.query_based_api import QueryBasedAPIProxy


class BaseHenuTool(Tool):
    tool_name = ""
    _TIME_PREFLIGHT_EXEMPT_TOOLS = {
        "system_status",
        "setup_account",
        "set_calibration_source",
    }

    async def call(
        self,
        params: dict[str, Any],
        session: provider_session.Session,
        query_id: int,
    ) -> dict[str, Any]:
        if not self.tool_name:
            return {"success": False, "msg": "tool_name 未设置"}

        service = getattr(self.plugin, "service", None)
        if service is None:
            return {"success": False, "msg": "插件服务未初始化"}

        identity_hint = await self._load_identity_hint(query_id)
        runtime_context = None
        if self.should_preload_runtime_context(params):
            runtime_context = await self._ensure_runtime_context(
                query_id,
                session,
                identity_hint,
                service,
            )

        result = await asyncio.to_thread(
            service.run_tool,
            self.tool_name,
            params,
            session,
            query_id,
            identity_hint,
        )
        if isinstance(result, dict) and isinstance(runtime_context, dict):
            server_time = runtime_context.get("server_time")
            if isinstance(server_time, dict) and server_time:
                result.setdefault("server_time_snapshot", server_time)

        await self._refresh_after_sensitive_success(query_id, params, result)
        self._strip_internal_fields(result)
        return result

    def should_preload_runtime_context(self, params: dict[str, Any]) -> bool:
        return self.tool_name not in self._TIME_PREFLIGHT_EXEMPT_TOOLS

    async def _load_identity_hint(self, query_id: int) -> dict[str, Any]:
        handler = getattr(self.plugin, "plugin_runtime_handler", None)
        if handler is None:
            return {}

        try:
            proxy = QueryBasedAPIProxy(query_id=query_id, plugin_runtime_handler=handler)
            query_vars = await proxy.get_query_vars()
        except Exception:
            return {}

        if not isinstance(query_vars, dict):
            return {}

        sender_id = query_vars.get("henu_sender_id") or query_vars.get("sender_id")
        launcher_id = query_vars.get("henu_launcher_id") or query_vars.get("launcher_id")
        launcher_type = query_vars.get("henu_launcher_type") or query_vars.get("launcher_type")

        result: dict[str, Any] = {}
        if sender_id not in (None, "", 0, "0"):
            result["sender_id"] = sender_id
        if launcher_id not in (None, "", 0, "0"):
            result["launcher_id"] = launcher_id
        if launcher_type not in (None, ""):
            result["launcher_type"] = launcher_type
        return result

    async def _ensure_runtime_context(
        self,
        query_id: int,
        session: provider_session.Session,
        identity_hint: dict[str, Any],
        service: Any,
    ) -> dict[str, Any] | None:
        handler = getattr(self.plugin, "plugin_runtime_handler", None)
        if handler is None:
            return None

        proxy = QueryBasedAPIProxy(query_id=query_id, plugin_runtime_handler=handler)
        query_vars: dict[str, Any]
        try:
            query_vars = await proxy.get_query_vars()
        except Exception:
            query_vars = {}

        cached = query_vars.get("_henu_runtime_context") if isinstance(query_vars, dict) else None
        if isinstance(cached, dict) and cached.get("server_time"):
            return cached

        if self.tool_name in self._TIME_PREFLIGHT_EXEMPT_TOOLS:
            return None

        timezone = self._resolve_timezone(query_vars)
        runtime_context = await asyncio.to_thread(
            service.get_runtime_context,
            session,
            identity_hint,
            timezone,
        )
        if isinstance(runtime_context, dict):
            try:
                await proxy.set_query_var("_henu_runtime_context", runtime_context)
            except Exception:
                pass
            return runtime_context

        return None

    def _resolve_timezone(self, query_vars: dict[str, Any]) -> str:
        if not isinstance(query_vars, dict):
            return "Asia/Shanghai"
        timezone = query_vars.get("timezone") or query_vars.get("henu_timezone")
        text = str(timezone or "").strip()
        return text or "Asia/Shanghai"

    async def _refresh_after_sensitive_success(
        self,
        query_id: int,
        params: dict[str, Any],
        result: Any,
    ) -> None:
        if not isinstance(result, dict):
            return
        resolved_tool = str(result.get("_resolved_tool_name") or self.tool_name)
        if resolved_tool != "setup_account":
            return
        if not result.get("success"):
            return
        effective_params = result.get("_effective_params") if isinstance(result.get("_effective_params"), dict) else params
        if not self._as_bool(effective_params.get("verify_login"), True):
            return

        handler = getattr(self.plugin, "plugin_runtime_handler", None)
        if handler is None:
            return

        proxy = QueryBasedAPIProxy(query_id=query_id, plugin_runtime_handler=handler)
        try:
            await proxy.create_new_conversation()
        except Exception:
            result["conversation_refreshed"] = False
            result["security_notice"] = (
                "账号已验证登录成功，但自动刷新对话失败。为避免上下文泄露，请手动开始新对话后再继续。"
            )
            return

        result["conversation_refreshed"] = True
        result["security_notice"] = (
            "账号已验证登录成功，插件已自动刷新对话上下文，避免后续沿用旧上下文。"
        )

    def _strip_internal_fields(self, result: Any) -> None:
        if not isinstance(result, dict):
            return
        result.pop("_resolved_tool_name", None)
        result.pop("_effective_params", None)

    def _as_bool(self, value: Any, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
        if value is None:
            return default
        return bool(value)

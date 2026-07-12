from __future__ import annotations

import asyncio
import hashlib
import re
import json
from pathlib import Path
from typing import Any, Callable

from langbot_plugin.api.definition.components.tool.tool import Tool
from langbot_plugin.api.entities.builtin.provider import session as provider_session
from langbot_plugin.api.proxies.query_based_api import QueryBasedAPIProxy

from henu_plugin.storage_adapter import PluginStorageAdapter
from henu_plugin.service import get_current_user_paths, set_current_user_paths, SessionIdentity


def _resolve_storage_key(session: provider_session.Session, identity_hint: dict[str, Any]) -> str:
    """Resolve storage key from session and identity hint."""
    sender_id = str(identity_hint.get("sender_id") or session.sender_id or "").strip()
    launcher_id = str(identity_hint.get("launcher_id") or session.launcher_id or "").strip()
    qq = sender_id or launcher_id or "unknown"

    storage_key = re.sub(r"[^0-9A-Za-z._-]+", "_", qq).strip("._-")
    if not storage_key:
        storage_key = hashlib.sha1(qq.encode("utf-8")).hexdigest()[:16]
    return storage_key


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

        # Resolve storage key and load user data from LangBot Storage
        storage_key = _resolve_storage_key(session, identity_hint)
        storage_adapter = PluginStorageAdapter(self.plugin, storage_key)

        # Load user data from Storage to temp files
        user_paths = await storage_adapter.load_all()

        # Set thread-local paths for service to use
        set_current_user_paths(user_paths)

        runtime_context = None
        if self.should_preload_runtime_context(params):
            await self._prime_runtime_context_query_var(query_id)
            runtime_context = await self._ensure_runtime_context(
                query_id,
                session,
                identity_hint,
                service,
            )

        result: dict[str, Any] | None = None
        storage_error: Exception | None = None
        try:
            result = await asyncio.to_thread(
                service.run_tool,
                self.tool_name,
                params,
                session,
                query_id,
                identity_hint,
            )
        finally:
            # Save user data back to Storage after operation
            try:
                await storage_adapter.save_all()
            except Exception as exc:
                storage_error = exc
            # Clear thread-local paths
            set_current_user_paths(None)

        if storage_error is not None:
            failure: dict[str, Any] = {
                "success": False,
                "msg": f"LangBot Storage 保存失败: {storage_error}",
            }
            if isinstance(result, dict):
                failure["tool_result"] = result
            return failure

        if isinstance(result, dict) and isinstance(runtime_context, dict):
            server_time = runtime_context.get("server_time")
            if isinstance(server_time, dict) and server_time:
                result.setdefault("server_time_snapshot", server_time)

        await self._refresh_after_sensitive_success(query_id, params, result)
        self._strip_internal_fields(result)
        self._normalize_for_qq_delivery(result)
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
        storage_paths = get_current_user_paths()
        runtime_context = await self._run_with_user_storage(
            storage_paths,
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

    async def _prime_runtime_context_query_var(self, query_id: int) -> None:
        handler = getattr(self.plugin, "plugin_runtime_handler", None)
        if handler is None:
            return
        try:
            proxy = QueryBasedAPIProxy(query_id=query_id, plugin_runtime_handler=handler)
            await proxy.get_query_vars()
            await proxy.set_query_var("_henu_runtime_context", {})
        except Exception:
            return

    async def _run_with_user_storage(
        self,
        storage_paths,
        func: Callable[..., Any],
        *args: Any,
    ) -> Any:
        if storage_paths is None:
            return await asyncio.to_thread(func, *args)
        return await asyncio.to_thread(self._run_with_user_storage_sync, storage_paths, func, *args)

    @staticmethod
    def _run_with_user_storage_sync(
        storage_paths,
        func: Callable[..., Any],
        *args: Any,
    ) -> Any:
        from henu_plugin import service as service_module
        return service_module._run_in_user_storage(storage_paths, func, *args)

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

    def _normalize_for_qq_delivery(self, result: Any) -> None:
        if not isinstance(result, dict):
            return

        # Normalize msg to plain text first, ensure non-empty fallback.
        msg = result.get("msg")
        if not isinstance(msg, str) or not msg.strip():
            if result.get("success") is False:
                msg = "执行失败"
            else:
                msg = str(msg or "执行完成")
            result["msg"] = msg
        result["msg"] = self._trim_text(str(result.get("msg", "")), 1200)

        # Keep only a small, safe summary payload to avoid QQ 官方 API 长消息 400。
        list_fields = {
            "locations": 12,
            "seats": 12,
            "data": 12,
            "items": 12,
            "rooms": 12,
            "records": 12,
            "tasks": 12,
            "appointments": 12,
            "candidates": 12,
            "courses": 12,
            "day_schedule": 12,
            "current_courses": 12,
            "next_commands": 12,
            "commands": 8,
            "tips": 8,
            "examples": 6,
        }
        for field, limit in list_fields.items():
            value = result.get(field)
            if isinstance(value, list) and len(value) > limit:
                result[f"{field}_truncated"] = len(value) - limit
                result[field] = value[:limit]
            elif isinstance(value, tuple) and len(value) > limit:
                result[f"{field}_truncated"] = len(value) - limit
                result[field] = list(value[:limit])

        # Drop known heavy/optional fields that can trigger oversized payload issues.
        heavy_fields = [
            "detail",
            "apply_info",
            "constraints",
            "filters",
            "storage",
            "resolved_query",
            "session_binding",
            "time_field_semantics",
            "server_time_snapshot",
            "room_filters",
            "raw_rooms",
            "seats_cache",
            "rooms_info",
        ]
        max_payload_chars = 2200
        payload = self._make_payload_json(result)
        if len(payload) <= max_payload_chars:
            return

        for field in heavy_fields:
            result.pop(field, None)
            payload = self._make_payload_json(result)
            if len(payload) <= max_payload_chars:
                return

        # Last resort: keep only msg / success and basic follow-up fields.
        essential = {
            "success": bool(result.get("success")),
            "msg": result.get("msg", ""),
            "next_commands": result.get("next_commands", []),
        }
        result.clear()
        result.update(essential)

    @staticmethod
    def _trim_text(value: str, limit: int) -> str:
        text = str(value or "")
        if len(text) <= limit:
            return text
        return text[: limit - 3].rstrip() + "..."

    @staticmethod
    def _make_payload_json(result: dict[str, Any]) -> str:
        safe_payload = BaseHenuTool._normalize_payload_types(result)
        return json.dumps(safe_payload, ensure_ascii=False)

    @staticmethod
    def _normalize_payload_types(value: Any) -> Any:
        if isinstance(value, dict):
            normalized = {}
            for key, item in value.items():
                normalized[key] = BaseHenuTool._normalize_payload_types(item)
            return normalized
        if isinstance(value, tuple):
            return [BaseHenuTool._normalize_payload_types(item) for item in value]
        if isinstance(value, list):
            return [BaseHenuTool._normalize_payload_types(item) for item in value]
        if isinstance(value, set):
            return [BaseHenuTool._normalize_payload_types(item) for item in sorted(value)]
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, (bytes, bytearray)):
            return value.decode("utf-8", errors="ignore")
        if value is None:
            return None
        if isinstance(value, (str, int, float, bool)):
            return value
        return str(value)

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

from __future__ import annotations

from langbot_plugin.api.entities import context
from langbot_plugin.api.entities.builtin.provider import session as provider_session

from components.event_listener.identity_capture import IdentityCaptureListener


class SafeIdentityCaptureListener(IdentityCaptureListener):
    """Compatibility wrapper for LangBot versions whose get_query_var raises on missing keys.

    The original listener used ctx.get_query_var("_henu_runtime_context") as a cache lookup.
    In current LangBot, a missing query variable is surfaced as a KeyError in the host process,
    which pollutes logs and can make the plugin runtime look broken. This subclass uses
    get_query_vars().get(...) instead, preserving the same behavior without the traceback.
    """

    async def _get_or_create_runtime_context(self, ctx: context.EventContext) -> dict | None:
        query_vars = await self._safe_get_query_vars(ctx)
        cached = query_vars.get("_henu_runtime_context") if isinstance(query_vars, dict) else None
        if isinstance(cached, dict) and cached.get("binding") and cached.get("server_time"):
            return cached

        service = getattr(self.plugin, "service", None)
        if service is None:
            return None

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

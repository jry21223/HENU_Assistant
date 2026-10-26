"""Process-owned work for the plugin's dedicated LangBot worker."""

from __future__ import annotations

import asyncio
from contextvars import Context


def create_background_task(plugin, coroutine):
    """Keep dedicated work outside the originating request's revocable scope.

    A shared worker must retain its invocation boundary. Only an installation
    permanently bound to this connection may outlive the request that started it.
    Older SDKs have no invocation scope and keep their existing task behavior.
    """
    try:
        from langbot_plugin.api.proxies.invocation import current_invocation
    except ImportError:
        return asyncio.create_task(coroutine)

    handler = getattr(plugin, "plugin_runtime_handler", None)
    try:
        active = current_invocation(handler) if handler is not None else None
        if active is None:
            return asyncio.create_task(coroutine)
        bound = getattr(handler, "bound_action_context", None)
        if active.binding is None or bound != active.binding:
            raise RuntimeError("HENU background features require a dedicated installation")
        # The SDK uses the immutable connection binding for outbound requests
        # when no request context is present. Never copy a request capability.
        return Context().run(asyncio.create_task, coroutine)
    except BaseException:
        coroutine.close()
        raise

from __future__ import annotations

import asyncio
from typing import Any

from langbot_plugin.api.definition.components.tool.tool import Tool
from langbot_plugin.api.entities.builtin.provider import session as provider_session
from langbot_plugin.api.proxies.query_based_api import QueryBasedAPIProxy


class BaseHenuTool(Tool):
    tool_name = ""

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

        return await asyncio.to_thread(
            service.run_tool,
            self.tool_name,
            params,
            session,
            query_id,
            identity_hint,
        )

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

        sender_id = query_vars.get("henu_sender_id")
        launcher_id = query_vars.get("henu_launcher_id")
        launcher_type = query_vars.get("henu_launcher_type")

        result: dict[str, Any] = {}
        if sender_id not in (None, "", 0, "0"):
            result["sender_id"] = sender_id
        if launcher_id not in (None, "", 0, "0"):
            result["launcher_id"] = launcher_id
        if launcher_type not in (None, ""):
            result["launcher_type"] = launcher_type
        return result

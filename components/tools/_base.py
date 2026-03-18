from __future__ import annotations

import asyncio
from typing import Any

from langbot_plugin.api.definition.components.tool.tool import Tool
from langbot_plugin.api.entities.builtin.provider import session as provider_session


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

        return await asyncio.to_thread(
            service.run_tool,
            self.tool_name,
            params,
            session,
            query_id,
        )

from __future__ import annotations

from pathlib import Path

from langbot_plugin.api.definition.plugin import BasePlugin

from henu_plugin.service import HenuPluginService


class HenuAssistantPlugin(BasePlugin):
    async def initialize(self) -> None:
        self.service = HenuPluginService(Path(__file__).resolve().parent)

    def __del__(self) -> None:
        pass

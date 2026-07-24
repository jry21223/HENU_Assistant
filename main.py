from __future__ import annotations

from pathlib import Path

from langbot_plugin.api.definition.plugin import BasePlugin

from henu_plugin.hardened_service import HardenedHenuPluginService


class HenuAssistantPlugin(BasePlugin):
    async def initialize(self) -> None:
        self.service = HardenedHenuPluginService(Path(__file__).resolve().parent)

    def __del__(self) -> None:
        pass

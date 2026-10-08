from __future__ import annotations

from pathlib import Path

from langbot_plugin.api.definition.plugin import BasePlugin

from henu_plugin.hardened_service import HardenedHenuPluginService


class HenuAssistantPlugin(BasePlugin):
    async def initialize(self) -> None:
        previous = getattr(self, "_henu_library_scheduler", None)
        if previous is not None:
            await previous.shutdown()
        self.service = HardenedHenuPluginService(Path(__file__).resolve().parent)
        from henu_plugin.library_schedule import LibraryScheduler

        self._henu_library_scheduler = LibraryScheduler(self)
        try:
            await self._henu_library_scheduler.start()
        except Exception:
            raise RuntimeError("persistent library scheduler unavailable") from None

    async def shutdown(self) -> None:
        scheduler = getattr(self, "_henu_library_scheduler", None)
        if scheduler is not None:
            await scheduler.shutdown()

    def __del__(self) -> None:
        scheduler = getattr(self, "_henu_library_scheduler", None)
        if scheduler is not None:
            scheduler.cancel_worker()

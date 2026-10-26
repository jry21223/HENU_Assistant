from __future__ import annotations

from pathlib import Path

from langbot_plugin.api.definition.plugin import BasePlugin

from henu_plugin.hardened_service import HardenedHenuPluginService


class HenuAssistantPlugin(BasePlugin):
    async def initialize(self) -> None:
        self.service = HardenedHenuPluginService(Path(__file__).resolve().parent)
        from henu_plugin.schedule_reminders import ScheduleReminders
        self.schedule_reminders = ScheduleReminders(self)
        self.schedule_reminders.start()
        from henu_plugin.library_automation import LibraryAutomation
        self.library_automation = LibraryAutomation(self)
        self.library_automation.start()

    def __del__(self) -> None:
        pass

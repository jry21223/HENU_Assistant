from __future__ import annotations

import os
from pathlib import Path

from langbot_plugin.api.definition.plugin import BasePlugin

from henu_plugin.hardened_service import HardenedHenuPluginService
from henu_plugin.storage_adapter import PluginStorageAdapter


class HenuAssistantPlugin(BasePlugin):
    async def initialize(self) -> None:
        self.rollback_mirrors_reconciled = (
            await PluginStorageAdapter.reconcile_legacy_snapshots(
                self,
                allow_legacy_import=os.environ.get(
                    "HENU_IMPORT_V204_ROLLBACK",
                    "",
                ).strip()
                == "1",
            )
        )
        self.service = HardenedHenuPluginService(Path(__file__).resolve().parent)

    def __del__(self) -> None:
        pass

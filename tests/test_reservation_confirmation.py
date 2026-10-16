from __future__ import annotations

import asyncio
import json
from pathlib import Path
import pytest

from langbot_plugin.api.entities.builtin.provider.session import LauncherTypes, Session

from components.cli_tools.henu_cli_safe import HenuCliSafe
from henu_plugin.hardened_service import HardenedHenuPluginService
from henu_plugin.cli import inspect_cli_command
from henu_plugin.confirmation import create_pending_operation, pending_storage_key


class MemoryPlugin:
    def __init__(self):
        self.storage = {}
        self.service = HardenedHenuPluginService(Path(__file__).resolve().parents[1])

    async def get_plugin_storage_keys(self):
        return list(self.storage)

    async def get_plugin_storage(self, key):
        return self.storage.get(key, b"{}")

    async def set_plugin_storage(self, key, value):
        self.storage[key] = value


def test_incomplete_reservation_never_creates_confirmation():
    async def run():
        plugin = MemoryPlugin()
        tool = HenuCliSafe()
        tool.plugin = plugin
        session = Session(sender_id="reservation-user", launcher_id="reservation-user",
                          launcher_type=LauncherTypes.PERSON)

        result = await tool.call({"command": (
            'library reserve --location "第一自习室" --seat-no 001 '
            '--date 2026-11-01 --time 08:00'
        )}, session, 1)

        assert result["error_code"] == "confirmation_parameters_incomplete"
        assert not any("pending_operation" in key for key in plugin.storage)
    asyncio.run(run())


def test_reservation_parser_preserves_missing_start_time():
    spec = inspect_cli_command(
        "library reserve --location 区域 --seat-no 001 --date 2026-11-01 --end-time 12:00"
    )
    assert spec.params["preferred_time"] == ""


@pytest.mark.parametrize("suffix", [
    "--end-tmie 12:00 --end-policy server_full_slot",
    "--end-time --end-policy server_full_slot",
    "--end-time 12:00 --time 09:00",
    "--end-time 12:00 --preferred-time 09:00",
    "--end-time 12:00 unexpected",
])
def test_malformed_reservation_cannot_silently_change_authorized_times(suffix):
    spec = inspect_cli_command(
        "library reserve --location 区域 --seat-no 001 --date 2026-11-01 --time 08:00 " + suffix
    )
    assert spec.error
    assert spec.resolved_tool is None


def test_explicit_full_slot_policy_is_visible_in_confirmation():
    async def run():
        plugin = MemoryPlugin()
        tool = HenuCliSafe()
        tool.plugin = plugin
        session = Session(sender_id="reservation-user", launcher_id="reservation-user",
                          launcher_type=LauncherTypes.PERSON)
        result = await tool.call({"command": (
            "library reserve --location 区域 --seat-no 001 --date 2026-11-01 "
            "--time 08:00 --end-policy server_full_slot"
        )}, session, 1)
        assert result["confirmation_required"] is True
        assert "end_policy=server_full_slot" in result["reply_text"]
    asyncio.run(run())


def test_legacy_pending_reservation_is_revalidated_before_execution():
    async def run():
        plugin = MemoryPlugin()
        tool = HenuCliSafe()
        tool.plugin = plugin
        session = Session(sender_id="reservation-user", launcher_id="reservation-user",
                          launcher_type=LauncherTypes.PERSON)
        pending = create_pending_operation(
            storage_key="reservation-user", query_id=1,
            canonical_command=(
                "library reserve --location 区域 --seat-no 001 "
                "--date 2026-11-01 --time 08:00"
            ),
        )
        pending["conversation"] = {"launcher_type": "person", "launcher_id": "reservation-user"}
        plugin.storage[pending_storage_key("reservation-user")] = json.dumps(pending).encode()

        result = await tool.call({"command": "confirm " + pending["token"]}, session, 2)

        assert result["error_code"] == "confirmation_parameters_incomplete"
    asyncio.run(run())

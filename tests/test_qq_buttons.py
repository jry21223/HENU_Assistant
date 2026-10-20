import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from langbot_plugin.api.entities.builtin.platform.message import MessageChain, Plain


def context(text="", user="alice", bot="bot", query=2):
    return SimpleNamespace(
        query_id=query,
        event=SimpleNamespace(
            sender_id=user,
            launcher_id=user,
            launcher_type="person",
            message_chain=MessageChain([Plain(text=text)]),
        ),
        get_bot_uuid=AsyncMock(return_value=bot),
        reply=AsyncMock(),
        prevent_default=Mock(),
        prevent_postorder=Mock(),
    )


def test_button_actions_are_owner_scoped_single_use_and_grouped(monkeypatch):
    from henu_plugin import qq_buttons as buttons

    async def run():
        plugin = SimpleNamespace()
        origin = context(query=1)
        wire = await buttons.actions(
            plugin, origin, [("确认", {"kind": "test"}), ("取消", {"kind": "test"})]
        )
        values = [item["id"] for item in wire["actions"]]
        callback = AsyncMock()
        monkeypatch.setattr(buttons, "dispatch", callback)
        assert await buttons.handle(
            plugin, context("HENU_BUTTON " + values[0], user="bob")
        )
        callback.assert_not_awaited()
        assert await buttons.handle(
            plugin, context("HENU_BUTTON " + values[0], bot="other")
        )
        callback.assert_not_awaited()
        assert await buttons.handle(
            plugin, context("HENU_BUTTON " + values[0], query=1)
        )
        callback.assert_not_awaited()
        assert await buttons.handle(plugin, context("HENU_BUTTON " + values[0]))
        assert callback.await_count == 1
        assert await buttons.handle(
            plugin, context("HENU_BUTTON " + values[1], query=3)
        )
        assert await buttons.handle(
            plugin, context("HENU_BUTTON " + values[0], query=4)
        )
        assert callback.await_count == 1

    asyncio.run(run())


def test_old_confirmation_button_cannot_confirm_new_pending(monkeypatch):
    from henu_plugin import qq_buttons as buttons
    from henu_plugin.confirmation import create_pending_operation
    from components.cli_tools.henu_cli_safe import HenuCliSafe

    async def run():
        plugin = SimpleNamespace()
        old = create_pending_operation(
            storage_key="alice", canonical_command="yuketang enable", query_id=1
        )
        old["conversation"] = {"launcher_type": "person", "launcher_id": "alice"}
        plugin.get_plugin_storage = AsyncMock(return_value=json.dumps(old).encode())
        plugin.set_plugin_storage = AsyncMock()
        wire = await buttons.confirmation_actions(
            plugin, "bot", context(query=1).event, 1
        )
        new = dict(old, token="new-operation-token")
        plugin.get_plugin_storage.return_value = json.dumps(new).encode()
        call = AsyncMock()
        monkeypatch.setattr(HenuCliSafe, "call", call)
        await buttons.handle(plugin, context("HENU_BUTTON " + wire["actions"][0]["id"]))
        call.assert_not_awaited()
        plugin.set_plugin_storage.assert_not_awaited()

    asyncio.run(run())

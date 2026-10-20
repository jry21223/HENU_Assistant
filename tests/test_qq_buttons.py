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
            plugin, "bot", context(query=1).event, 1, expected_command="yuketang enable"
        )
        new = dict(old, token="new-operation-token")
        plugin.get_plugin_storage.return_value = json.dumps(new).encode()
        call = AsyncMock()
        monkeypatch.setattr(HenuCliSafe, "call", call)
        await buttons.handle(plugin, context("HENU_BUTTON " + wire["actions"][0]["id"]))
        call.assert_not_awaited()
        plugin.set_plugin_storage.assert_not_awaited()

    asyncio.run(run())


def test_out_of_order_same_query_previews_never_bind_another_seat():
    from henu_plugin import qq_buttons as buttons
    from henu_plugin.confirmation import create_pending_operation

    async def run():
        command_a = "library reserve --location '区域' --seat-no 001 --date 2026-11-01 --time 08:00 --end-time 23:00"
        command_b = command_a.replace("001", "002")
        pending = create_pending_operation(
            storage_key="alice", canonical_command=command_a, query_id=1
        )
        pending["conversation"] = {"launcher_type": "person", "launcher_id": "alice"}
        plugin = SimpleNamespace(
            get_plugin_storage=AsyncMock(return_value=json.dumps(pending).encode())
        )
        wrong = await buttons.confirmation_actions(
            plugin, "bot", context(query=1).event, 1, expected_command=command_b
        )
        assert wrong is None
        missing = await buttons.confirmation_actions(
            plugin, "bot", context(query=1).event, 1
        )
        assert missing is None
        right = await buttons.confirmation_actions(
            plugin, "bot", context(query=1).event, 2, expected_command=command_a
        )
        assert right and right["actions"][0]["label"] == "确认预约"

    asyncio.run(run())


def test_unbound_staged_confirmation_does_not_tell_user_to_confirm_other_pending(
    monkeypatch,
):
    from henu_plugin.qq_cards import stage_card
    import langbot_plugin.api.proxies.query_based_api as api

    async def run():
        proxy = SimpleNamespace(
            get_bot_uuid=AsyncMock(return_value="bot"), set_query_var=AsyncMock()
        )
        monkeypatch.setattr(api, "QueryBasedAPIProxy", lambda **kwargs: proxy)
        plugin = SimpleNamespace(plugin_runtime_handler=object())
        await stage_card(
            plugin,
            context().event,
            2,
            {
                "confirmation_required": True,
                "success": False,
                "reply_text": "请确认预约002号",
            },
        )
        card = proxy.set_query_var.call_args.args[1]["page"]
        assert "预览已更新" in card and "002" not in card
        assert "HENU_ACTIONS" not in card

    asyncio.run(run())

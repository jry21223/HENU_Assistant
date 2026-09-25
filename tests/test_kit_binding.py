import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from henu_plugin.kit_binding import KitBindingCoordinator


def test_group_and_wrong_bot_never_reach_binding_service(monkeypatch):
    async def run():
        plugin = SimpleNamespace(
            get_plugin_storage=AsyncMock(), set_plugin_storage=AsyncMock()
        )
        flow = KitBindingCoordinator(SimpleNamespace(plugin=plugin))
        request = SimpleNamespace(
            event=SimpleNamespace(
                text_message="绑定 HENU KIT",
                sender_id="alice",
                launcher_type="group",
                launcher_id="group",
            ),
            query_id=1,
            reply=AsyncMock(),
            prevent_default=Mock(),
            prevent_postorder=Mock(),
            get_bot_uuid=AsyncMock(return_value="wrong-bot"),
        )
        assert await flow.handle(request, is_group=True)
        plugin.set_plugin_storage.assert_not_called()
        request.event.launcher_type = "person"
        monkeypatch.setattr(
            "henu_plugin.kit_binding.kit_settings",
            lambda: {"bot_uuid": "configured-bot"},
        )
        assert await flow.handle(request, is_group=False)
        plugin.set_plugin_storage.assert_not_called()

    asyncio.run(run())


def test_binding_preview_then_real_confirmation_and_no_other_chat(monkeypatch):
    from tests.test_confirmation_shortcut import Plugin

    async def run():
        plugin = Plugin()
        flow = KitBindingCoordinator(SimpleNamespace(plugin=plugin))
        monkeypatch.setattr(
            "henu_plugin.kit_binding.kit_settings",
            lambda: {"bot_uuid": "bot", "portal_url": "https://kit.test"},
        )
        calls = []

        def remote(settings, action, body):
            calls.append(action)
            if action == "start":
                return {"token": "a" * 43}
            if action == "pending":
                return {"state": "authorized", "display_name": "小河"}
            if action == "confirm":
                return {"bound": True, "display_name": "小河"}
            raise AssertionError(action)

        monkeypatch.setattr("henu_plugin.kit_binding.kit_request", remote)

        def event(text, query, launcher="alice"):
            return SimpleNamespace(
                event=SimpleNamespace(
                    text_message=text,
                    sender_id="alice",
                    launcher_type="person",
                    launcher_id=launcher,
                    message_chain=SimpleNamespace(
                        root=[{"type": "Source", "id": f"qq-message-{query}"}]
                    ),
                ),
                query_id=query,
                reply=AsyncMock(),
                prevent_default=Mock(),
                prevent_postorder=Mock(),
                get_bot_uuid=AsyncMock(return_value="bot"),
            )

        assert await flow.handle(event("绑定 HENU KIT", 1), is_group=False)
        assert await flow.handle(event("确认", 1), is_group=False)
        assert await flow.handle(event("确认", 2, "other"), is_group=False)
        assert calls == ["start"]
        lost_preview = event("确认", 20)
        lost_preview.reply.side_effect = [RuntimeError("delivery unavailable"), None]
        assert await flow.handle(lost_preview, is_group=False)
        assert "confirm" not in calls
        preview = event("确认", 3)
        assert await flow.handle(preview, is_group=False)
        assert "小河" in str(preview.reply.call_args)
        assert calls == ["start", "pending", "pending"]
        replay = event("确认", 30)
        replay.event.message_chain.root[0]["id"] = "qq-message-3"
        assert await flow.handle(replay, is_group=False)
        assert "confirm" not in calls
        assert await flow.handle(event("确认", 3), is_group=False)
        assert "confirm" not in calls
        assert await flow.handle(event("确认", 4), is_group=False)
        assert calls.count("confirm") == 1
        assert not await flow.handle(event("确认", 5), is_group=False)

    asyncio.run(run())

import asyncio
import hashlib
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from henu_mcp.core.secure_storage import decrypt_value, encrypt_value
from henu_plugin.kit_binding import KitBindingCoordinator


def qq_source(source, query, *, source_time=None):
    return {
        "type": "Source",
        "id": source,
        "time": time.time() if source_time is None else source_time,
    }


def kit_event(text, query, source, *, source_time=None, launcher="alice"):
    component = qq_source(source, query, source_time=source_time)
    return SimpleNamespace(
        event=SimpleNamespace(
            text_message=text,
            sender_id="alice",
            launcher_type="person",
            launcher_id=launcher,
            message_chain=SimpleNamespace(root=[component]),
            message_event=SimpleNamespace(
                sender=SimpleNamespace(id="alice", nickname="C2C_MESSAGE_CREATE"),
                time=component["time"],
            ),
        ),
        query_id=query,
        reply=AsyncMock(),
        prevent_default=Mock(),
        prevent_postorder=Mock(),
        get_bot_uuid=AsyncMock(return_value="bot"),
    )


@pytest.fixture(autouse=True)
def activated_bot(monkeypatch):
    monkeypatch.setattr("henu_plugin.kit_binding.ACTIVATION_DELAY_SECONDS", -120)


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
            return kit_event(text, query, f"qq-message-{query}", launcher=launcher)

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


@pytest.mark.parametrize("earlier_action", ["绑定 HENU KIT", "解绑 HENU KIT"])
def test_completed_confirmation_source_cannot_confirm_later_binding(
    monkeypatch, earlier_action
):
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
            if action == "unlink":
                return {"bound": False}
            raise AssertionError(action)

        monkeypatch.setattr("henu_plugin.kit_binding.kit_request", remote)

        def event(text, query, source):
            return kit_event(text, query, source)

        assert await flow.handle(event(earlier_action, 1, "source-a"), is_group=False)
        if earlier_action == "绑定 HENU KIT":
            assert await flow.handle(event("确认", 2, "source-b"), is_group=False)
        assert await flow.handle(event("确认", 3, "source-c"), is_group=False)
        completed_action = "confirm" if earlier_action == "绑定 HENU KIT" else "unlink"
        assert calls.count(completed_action) == 1
        used_records = [
            value for key, value in plugin.storage.items() if key.startswith("kit-binding-used:")
        ]
        assert used_records
        assert all(value.startswith(b"enc:v2:") for value in used_records)
        assert all(b"source-c" not in value for value in used_records)

        # A new coordinator simulates a restarted LangBot process.
        flow = KitBindingCoordinator(SimpleNamespace(plugin=plugin))

        # A separate later operation has already shown its authorized account.
        assert await flow.handle(event("绑定 HENU KIT", 4, "source-next"), is_group=False)
        assert await flow.handle(event("确认", 5, "source-preview"), is_group=False)
        confirms_before_replay = calls.count("confirm")
        unlinks_before_replay = calls.count("unlink")

        # LangBot assigns a fresh query ID to a replay of the old QQ message.
        assert await flow.handle(event("确认", 6, "source-c"), is_group=False)
        assert calls.count("confirm") == confirms_before_replay
        assert calls.count("unlink") == unlinks_before_replay

        assert await flow.handle(event("确认", 7, "source-d"), is_group=False)
        assert calls.count("confirm") == confirms_before_replay + 1

    asyncio.run(run())


def test_pre_authorization_confirmation_source_cannot_confirm_later_binding(
    monkeypatch,
):
    from tests.test_confirmation_shortcut import Plugin

    async def run():
        plugin = Plugin()
        flow = KitBindingCoordinator(SimpleNamespace(plugin=plugin))
        monkeypatch.setattr(
            "henu_plugin.kit_binding.kit_settings",
            lambda: {"bot_uuid": "bot", "portal_url": "https://kit.test"},
        )
        calls = []
        pending_states = iter(["pending", "authorized", "authorized", "authorized", "authorized"])

        def remote(settings, action, body):
            calls.append(action)
            if action == "start":
                return {"token": "a" * 43}
            if action == "pending":
                return {"state": next(pending_states), "display_name": "小河"}
            if action == "confirm":
                return {"bound": True, "display_name": "小河"}
            raise AssertionError(action)

        monkeypatch.setattr("henu_plugin.kit_binding.kit_request", remote)

        def event(text, query, source):
            return kit_event(text, query, source)

        assert await flow.handle(event("绑定 HENU KIT", 1, "source-a"), is_group=False)
        assert await flow.handle(event("确认", 2, "source-before-approval"), is_group=False)
        assert "confirm" not in calls
        assert await flow.handle(event("确认", 3, "source-preview-a"), is_group=False)
        assert await flow.handle(event("确认", 4, "source-complete-a"), is_group=False)
        assert calls.count("confirm") == 1

        flow = KitBindingCoordinator(SimpleNamespace(plugin=plugin))
        assert await flow.handle(event("绑定 HENU KIT", 5, "source-b"), is_group=False)
        assert await flow.handle(event("确认", 6, "source-preview-b"), is_group=False)
        before_replay = calls.count("confirm")
        assert await flow.handle(event("确认", 7, "source-before-approval"), is_group=False)
        assert calls.count("confirm") == before_replay
        assert await flow.handle(event("确认", 8, "source-fresh"), is_group=False)
        assert calls.count("confirm") == before_replay + 1

    asyncio.run(run())


def test_old_plain_confirmation_without_kit_pending_cannot_confirm_binding(monkeypatch):
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

        def event(text, query, source):
            return kit_event(text, query, source)

        assert not await flow.handle(event("确认", 1, "source-before-kit"), is_group=False)
        flow = KitBindingCoordinator(SimpleNamespace(plugin=plugin))
        assert await flow.handle(event("绑定 HENU KIT", 2, "source-a"), is_group=False)
        assert await flow.handle(event("确认", 3, "source-preview"), is_group=False)
        assert await flow.handle(event("确认", 4, "source-before-kit"), is_group=False)
        assert "confirm" not in calls
        assert await flow.handle(event("确认", 5, "source-fresh"), is_group=False)
        assert calls.count("confirm") == 1

    asyncio.run(run())


def test_completed_binding_request_source_cannot_start_again(monkeypatch):
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
        assert await flow.handle(kit_event("绑定 HENU KIT", 1, "source-start"), is_group=False)
        assert await flow.handle(kit_event("确认", 2, "source-preview"), is_group=False)
        assert await flow.handle(kit_event("确认", 3, "source-complete"), is_group=False)
        assert calls.count("start") == 1

        replay = kit_event("绑定 HENU KIT", 99, "source-start")
        assert await flow.handle(replay, is_group=False)
        assert calls.count("start") == 1
        assert await flow.handle(kit_event("绑定 HENU KIT", 100, "source-fresh"), is_group=False)
        assert calls.count("start") == 2

    asyncio.run(run())


@pytest.mark.parametrize(
    "bad_timestamp", ["stale", "same-second", "future", "slightly-future", "missing"]
)
def test_untrusted_or_old_source_time_cannot_confirm_new_binding(
    monkeypatch, bad_timestamp
):
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

        def event(text, query, source, source_time):
            component = (
                qq_source(source, query, source_time=source_time)
                if source_time is not None
                else {"type": "Source", "id": source}
            )
            return SimpleNamespace(
                event=SimpleNamespace(
                    text_message=text,
                    sender_id="alice",
                    launcher_type="person",
                    launcher_id="alice",
                    message_chain=SimpleNamespace(root=[component]),
                    message_event=SimpleNamespace(
                        sender=SimpleNamespace(
                            id="alice", nickname="C2C_MESSAGE_CREATE"
                        ),
                        time=component.get("time"),
                    ),
                ),
                query_id=query,
                reply=AsyncMock(),
                prevent_default=Mock(),
                prevent_postorder=Mock(),
                get_bot_uuid=AsyncMock(return_value="bot"),
            )

        now = int(time.time())
        bad_time = {
            "stale": now - 3600,
            "same-second": now - 3,
            "future": now + 3600,
            "slightly-future": now + 5,
            "missing": None,
        }[bad_timestamp]
        if bad_timestamp != "same-second":
            assert await flow.handle(
                event("绑定 HENU KIT", 0, "source-old-start", bad_time),
                is_group=False,
            )
            assert "start" not in calls
        assert await flow.handle(event("绑定 HENU KIT", 1, "source-start", now - 3), is_group=False)
        assert await flow.handle(event("确认", 2, "source-preview", now - 2), is_group=False)
        old = event("确认", 100, "source-from-before-release", bad_time)
        assert await flow.handle(old, is_group=False)
        assert "confirm" not in calls
        assert await flow.handle(
            event("确认", 3, "source-fresh", time.time()), is_group=False
        )
        assert calls.count("confirm") == 1

    asyncio.run(run())


@pytest.mark.parametrize("failure_step", ["settings", "bot_uuid"])
def test_settings_failure_keeps_plain_confirmation_owned_by_pending_kit(
    monkeypatch, failure_step
):
    from tests.test_confirmation_shortcut import Plugin

    async def run():
        plugin = Plugin()
        flow = KitBindingCoordinator(SimpleNamespace(plugin=plugin))
        monkeypatch.setattr(
            "henu_plugin.kit_binding.kit_settings",
            lambda: {"bot_uuid": "bot", "portal_url": "https://kit.test"},
        )
        monkeypatch.setattr(
            "henu_plugin.kit_binding.kit_request",
            lambda settings, action, body: {"token": "a" * 43},
        )

        def event(text, query, source):
            return kit_event(text, query, source)

        assert await flow.handle(event("绑定 HENU KIT", 1, "source-a"), is_group=False)
        plugin.storage["user:alice:pending_operation"] = b"legacy-pending"

        def unavailable():
            raise ValueError("incomplete KIT configuration")

        confirmation = event("确认", 2, "source-c")
        if failure_step == "settings":
            monkeypatch.setattr("henu_plugin.kit_binding.kit_settings", unavailable)
        else:
            confirmation.get_bot_uuid.side_effect = RuntimeError("bot unavailable")
        assert await flow.handle(confirmation, is_group=False)
        confirmation.prevent_default.assert_called_once()
        confirmation.prevent_postorder.assert_called_once()
        assert "暂时不可用" in str(confirmation.reply.call_args)
        assert plugin.storage["user:alice:pending_operation"] == b"legacy-pending"

    asyncio.run(run())


def test_other_bot_cannot_route_active_kit_confirmation_to_legacy(monkeypatch):
    from tests.test_confirmation_shortcut import Plugin

    async def run():
        plugin = Plugin()
        flow = KitBindingCoordinator(SimpleNamespace(plugin=plugin))
        monkeypatch.setattr(
            "henu_plugin.kit_binding.kit_settings",
            lambda: {"bot_uuid": "bot-a", "portal_url": "https://kit.test"},
        )
        remote = Mock(return_value={"token": "a" * 43})
        monkeypatch.setattr("henu_plugin.kit_binding.kit_request", remote)

        start = kit_event("绑定 HENU KIT", 1, "source-a")
        start.get_bot_uuid.return_value = "bot-a"
        assert await flow.handle(start, is_group=False)
        plugin.storage["user:alice:pending_operation"] = b"legacy-pending"

        wrong_bot = kit_event("确认", 2, "source-b")
        wrong_bot.get_bot_uuid.return_value = "bot-b"
        assert await flow.handle(wrong_bot, is_group=False)
        wrong_bot.prevent_default.assert_called_once()
        wrong_bot.prevent_postorder.assert_called_once()
        assert "指定的 HENU Bot" in str(wrong_bot.reply.call_args)
        assert plugin.storage["user:alice:pending_operation"] == b"legacy-pending"
        assert remote.call_count == 1

        pending_key = flow._scope_key("bot-a", "alice")
        plugin.storage[pending_key] = b""
        no_kit_pending = kit_event("确认", 3, "source-c")
        no_kit_pending.get_bot_uuid.return_value = "bot-b"
        assert not await flow.handle(no_kit_pending, is_group=False)
        no_kit_pending.prevent_default.assert_not_called()

    asyncio.run(run())


def test_configured_bot_change_cannot_route_old_pending_to_legacy(monkeypatch):
    from tests.test_confirmation_shortcut import Plugin

    async def run():
        plugin = Plugin()
        flow = KitBindingCoordinator(SimpleNamespace(plugin=plugin))
        settings = {"bot_uuid": "bot-a", "portal_url": "https://kit.test"}
        monkeypatch.setattr("henu_plugin.kit_binding.kit_settings", lambda: settings)
        remote = Mock(return_value={"token": "a" * 43})
        monkeypatch.setattr("henu_plugin.kit_binding.kit_request", remote)

        start = kit_event("绑定 HENU KIT", 1, "source-a")
        start.get_bot_uuid.return_value = "bot-a"
        assert await flow.handle(start, is_group=False)
        plugin.storage["user:alice:pending_operation"] = b"legacy-pending"

        settings = {"bot_uuid": "bot-b", "portal_url": "https://kit.test"}
        confirm = kit_event("确认", 2, "source-b")
        confirm.get_bot_uuid.return_value = "bot-b"
        assert await flow.handle(confirm, is_group=False)
        confirm.prevent_default.assert_called_once()
        confirm.prevent_postorder.assert_called_once()
        assert "另一个 HENU Bot" in str(confirm.reply.call_args)
        assert remote.call_count == 1
        assert plugin.storage["user:alice:pending_operation"] == b"legacy-pending"

        plugin.storage[flow._scope_key("bot-a", "alice")] = b""
        no_kit = kit_event("确认", 3, "source-c")
        no_kit.get_bot_uuid.return_value = "bot-b"
        assert not await flow.handle(no_kit, is_group=False)
        no_kit.prevent_default.assert_not_called()

    asyncio.run(run())


@pytest.mark.parametrize("command", ["绑定 HENU KIT", "HENU KIT 状态", "解绑 HENU KIT"])
@pytest.mark.parametrize("bad_provenance", ["missing", "direct", "sender", "time"])
def test_kit_commands_require_matching_c2c_provenance(
    monkeypatch, command, bad_provenance
):
    from tests.test_confirmation_shortcut import Plugin

    async def run():
        plugin = Plugin()
        flow = KitBindingCoordinator(SimpleNamespace(plugin=plugin))
        monkeypatch.setattr(
            "henu_plugin.kit_binding.kit_settings",
            lambda: {"bot_uuid": "bot", "portal_url": "https://kit.test"},
        )
        remote = Mock(return_value={"token": "a" * 43, "bound": False})
        monkeypatch.setattr("henu_plugin.kit_binding.kit_request", remote)
        request = kit_event(command, 1, "source-one")
        if bad_provenance == "missing":
            del request.event.message_event
        elif bad_provenance == "direct":
            request.event.message_event.sender.nickname = "DIRECT_MESSAGE_CREATE"
        elif bad_provenance == "sender":
            request.event.message_event.sender.id = "another-qq"
        else:
            request.event.message_event.time += 1

        assert await flow.handle(request, is_group=False)
        remote.assert_not_called()
        assert "QQ 消息来源" in str(request.reply.call_args)
        assert plugin.storage == {}

    asyncio.run(run())


def test_bad_c2c_provenance_cannot_confirm_kit_or_trigger_legacy(monkeypatch):
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
        assert await flow.handle(kit_event("绑定 HENU KIT", 1, "source-start"), is_group=False)
        direct = kit_event("确认", 2, "source-direct")
        direct.event.message_event.sender.nickname = "DIRECT_MESSAGE_CREATE"
        assert await flow.handle(direct, is_group=False)
        direct.prevent_default.assert_called_once()
        direct.prevent_postorder.assert_called_once()
        assert calls == ["start"]

        plugin.storage["user:alice:pending_operation"] = b"legacy-pending"
        with_legacy = kit_event("确认", 3, "source-direct-with-legacy")
        with_legacy.event.message_event.sender.nickname = "DIRECT_MESSAGE_CREATE"
        assert await flow.handle(with_legacy, is_group=False)
        with_legacy.prevent_default.assert_called_once()
        assert plugin.storage["user:alice:pending_operation"] == b"legacy-pending"

        plugin.storage[flow._scope_key("bot", "alice")] = b""
        legacy = kit_event("确认", 4, "source-legacy")
        legacy.event.message_event.sender.nickname = "DIRECT_MESSAGE_CREATE"
        assert not await flow.handle(legacy, is_group=False)
        legacy.prevent_default.assert_not_called()

    asyncio.run(run())


@pytest.mark.parametrize(
    ("raw_name", "expected"),
    [
        ("小\u202e河\u200b 同学", "小河 同学"),
        ("\x00\x1b\u202e", "当前账号"),
        ("  小河  ", "小河"),
    ],
)
def test_binding_account_name_removes_control_and_direction_marks(raw_name, expected):
    assert KitBindingCoordinator._name({"display_name": raw_name}) == expected


@pytest.mark.parametrize("unavailable_mode", ["none", "error", "uuid"])
def test_no_kit_pending_legacy_confirmation_is_recorded_for_later_replay(
    monkeypatch, unavailable_mode
):
    from tests.test_confirmation_shortcut import Plugin

    async def run():
        plugin = Plugin()
        plugin.storage["user:alice:pending_operation"] = b"legacy-pending"
        flow = KitBindingCoordinator(SimpleNamespace(plugin=plugin))
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

        def unavailable():
            raise ValueError("incomplete KIT configuration")

        monkeypatch.setattr(
            "henu_plugin.kit_binding.kit_settings",
            (lambda: None)
            if unavailable_mode == "none"
            else (
                lambda: {"bot_uuid": "bot", "portal_url": "https://kit.test"}
            )
            if unavailable_mode == "uuid"
            else unavailable,
        )

        def event(text, query, source):
            return kit_event(text, query, source)

        legacy = event("确认", 1, "source-legacy")
        if unavailable_mode == "uuid":
            legacy.get_bot_uuid.side_effect = RuntimeError("bot unavailable")
        assert not await flow.handle(legacy, is_group=False)
        legacy.prevent_default.assert_not_called()
        monkeypatch.setattr(
            "henu_plugin.kit_binding.kit_settings",
            lambda: {"bot_uuid": "bot", "portal_url": "https://kit.test"},
        )
        assert await flow.handle(event("绑定 HENU KIT", 2, "source-start"), is_group=False)
        assert await flow.handle(event("确认HENU KIT绑定", 3, "source-preview"), is_group=False)
        assert await flow.handle(event("确认HENU KIT绑定", 4, "source-legacy"), is_group=False)
        assert "confirm" not in calls
        assert await flow.handle(event("确认HENU KIT绑定", 5, "source-fresh"), is_group=False)
        assert calls.count("confirm") == 1

    asyncio.run(run())


def test_confirmation_storage_failure_never_calls_unlink(monkeypatch):
    from tests.test_confirmation_shortcut import Plugin

    async def run():
        plugin = Plugin()
        flow = KitBindingCoordinator(SimpleNamespace(plugin=plugin))
        monkeypatch.setattr(
            "henu_plugin.kit_binding.kit_settings",
            lambda: {"bot_uuid": "bot", "portal_url": "https://kit.test"},
        )
        remote = Mock(return_value={"bound": False})
        monkeypatch.setattr("henu_plugin.kit_binding.kit_request", remote)

        def event(text, query, source):
            return kit_event(text, query, source)

        assert await flow.handle(event("解绑 HENU KIT", 1, "source-a"), is_group=False)
        save = plugin.set_plugin_storage

        async def fail_ledger_save(key, value):
            if key.startswith("kit-binding-used:"):
                raise OSError("storage unavailable")
            await save(key, value)

        plugin.set_plugin_storage = fail_ledger_save
        request = event("确认", 2, "source-c")
        assert await flow.handle(request, is_group=False)
        remote.assert_not_called()
        assert "暂时无法" in str(request.reply.call_args)

    asyncio.run(run())


def test_many_legacy_confirmations_keep_fallback_available(monkeypatch):
    from tests.test_confirmation_shortcut import Plugin

    async def run():
        plugin = Plugin()
        flow = KitBindingCoordinator(SimpleNamespace(plugin=plugin))
        monkeypatch.setattr(
            "henu_plugin.kit_binding.kit_settings",
            lambda: {"bot_uuid": "bot", "portal_url": "https://kit.test"},
        )

        def event(query):
            return kit_event("确认", query, f"legacy-{query}")

        for query in range(1, 1026):
            assert not await flow.handle(event(query), is_group=False)
        replay = event(1)
        assert await flow.handle(replay, is_group=False)
        replay.prevent_default.assert_called_once()

    asyncio.run(run())


def test_activation_wait_is_durable_and_rejects_pre_activation_messages(monkeypatch):
    from tests.test_confirmation_shortcut import Plugin

    async def run():
        plugin = Plugin()
        flow = KitBindingCoordinator(SimpleNamespace(plugin=plugin))
        monkeypatch.setattr("henu_plugin.kit_binding.ACTIVATION_DELAY_SECONDS", 120)
        monkeypatch.setattr(
            "henu_plugin.kit_binding.kit_settings",
            lambda: {"bot_uuid": "bot", "portal_url": "https://kit.test"},
        )
        remote = Mock(return_value={"token": "a" * 43})
        monkeypatch.setattr("henu_plugin.kit_binding.kit_request", remote)

        first = kit_event("绑定 HENU KIT", 1, "source-first")
        assert await flow.handle(first, is_group=False)
        remote.assert_not_called()
        activation_key = "kit-binding-activation:" + hashlib.sha256(b"bot").hexdigest()
        activation = json.loads(decrypt_value(plugin.storage[activation_key].decode()))
        assert activation["schema"] == "henu.kit-binding-activation.v1"
        assert 119 <= activation["not_before"] - time.time() <= 120

        # Simulate the persisted wait having elapsed, then restart the coordinator.
        activation["not_before"] = time.time() - 1
        plugin.storage[activation_key] = encrypt_value(json.dumps(activation)).encode()
        flow = KitBindingCoordinator(SimpleNamespace(plugin=plugin))
        old = kit_event(
            "绑定 HENU KIT", 2, "source-before-activation",
            source_time=activation["not_before"] - 1,
        )
        assert await flow.handle(old, is_group=False)
        remote.assert_not_called()
        assert await flow.handle(kit_event("绑定 HENU KIT", 3, "source-fresh"), is_group=False)
        assert remote.call_count == 1

    asyncio.run(run())


def test_final_confirmation_requires_source_time_after_account_preview(monkeypatch):
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
        second = int(time.time())
        assert await flow.handle(
            kit_event("绑定 HENU KIT", 1, "source-start", source_time=second - 1),
            is_group=False,
        )
        assert await flow.handle(
            kit_event("确认", 2, "source-preview", source_time=second),
            is_group=False,
        )
        pending_key = next(
            key for key in plugin.storage if key.startswith("kit-binding:")
        )
        pending = json.loads(decrypt_value(plugin.storage[pending_key].decode()))
        previewed_at = pending["previewed_at"]
        assert pending["previewed"] is True

        same_time = kit_event("确认", 3, "source-same-time", source_time=second)
        assert await flow.handle(same_time, is_group=False)
        assert "confirm" not in calls
        assert "看到目标账号提示后" in str(same_time.reply.call_args)

        await asyncio.sleep(max(0, int(previewed_at) + 1 - time.time()) + 0.01)
        assert await flow.handle(
            kit_event("确认", 4, "source-fresh", source_time=int(time.time())),
            is_group=False,
        )
        assert calls.count("confirm") == 1

    asyncio.run(run())


@pytest.mark.parametrize("failure_mode", ["corrupt", "write_error"])
def test_activation_storage_uncertainty_never_starts_binding(monkeypatch, failure_mode):
    from tests.test_confirmation_shortcut import Plugin

    async def run():
        plugin = Plugin()
        flow = KitBindingCoordinator(SimpleNamespace(plugin=plugin))
        monkeypatch.setattr(
            "henu_plugin.kit_binding.kit_settings",
            lambda: {"bot_uuid": "bot", "portal_url": "https://kit.test"},
        )
        remote = Mock(return_value={"token": "a" * 43})
        monkeypatch.setattr("henu_plugin.kit_binding.kit_request", remote)
        activation_key = "kit-binding-activation:" + hashlib.sha256(b"bot").hexdigest()
        if failure_mode == "corrupt":
            plugin.storage[activation_key] = b"invalid-record"
        else:
            save = plugin.set_plugin_storage

            async def fail_activation_save(key, value):
                if key == activation_key:
                    raise OSError("storage unavailable")
                await save(key, value)

            plugin.set_plugin_storage = fail_activation_save

        request = kit_event("绑定 HENU KIT", 1, "source-start")
        assert await flow.handle(request, is_group=False)
        remote.assert_not_called()
        assert "暂时无法读取或保存" in str(request.reply.call_args)

    asyncio.run(run())


def test_used_source_limit_recovers_after_replay_horizon(monkeypatch):
    from tests.test_confirmation_shortcut import Plugin

    async def run():
        plugin = Plugin()
        flow = KitBindingCoordinator(SimpleNamespace(plugin=plugin))
        monkeypatch.setattr("henu_plugin.kit_binding.MAX_USED_SOURCES", 1)
        monkeypatch.setattr(
            "henu_plugin.kit_binding.kit_settings",
            lambda: {"bot_uuid": "bot", "portal_url": "https://kit.test"},
        )
        assert not await flow.handle(kit_event("确认", 1, "source-one"), is_group=False)
        full = kit_event("确认", 2, "source-two")
        assert await flow.handle(full, is_group=False)
        assert "过于频繁" in str(full.reply.call_args)

        ledger_key = next(
            key for key in plugin.storage if key.startswith("kit-binding-used:")
        )
        ledger = json.loads(decrypt_value(plugin.storage[ledger_key].decode()))
        ledger["entries"] = {
            digest: time.time() - 301 for digest in ledger["entries"]
        }
        plugin.storage[ledger_key] = encrypt_value(json.dumps(ledger)).encode()
        assert not await flow.handle(kit_event("确认", 3, "source-two"), is_group=False)

    asyncio.run(run())

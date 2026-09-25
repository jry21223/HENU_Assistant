import asyncio
import datetime
import hashlib
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from henu_mcp.core.secure_storage import decrypt_value, encrypt_value
from henu_plugin.confirmation import create_pending_operation
from henu_plugin.kit_binding import KitBindingCoordinator


def qq_source(source, query, *, source_time=None):
    return {
        "type": "Source",
        "id": source,
        "time": time.time() if source_time is None else source_time,
    }


def qq_receipt(reply_id="qq-bot-message", *, timestamp=None):
    epoch = time.time() if timestamp is None else timestamp
    return {
        "qq_c2c_receipt": {
            "id": reply_id,
            "timestamp": datetime.datetime.fromtimestamp(
                epoch, datetime.timezone.utc
            ).isoformat(),
            "ref_idx": f"ref-{reply_id}",
        }
    }


def kit_event(
    text,
    query,
    source,
    *,
    source_time=None,
    launcher="alice",
    quote_ref_idx=None,
    quote_present=False,
):
    component = qq_source(source, query, source_time=source_time)
    quote_metadata = {
        "t": "C2C_MESSAGE_CREATE",
        "d_id": source,
        "qq_websocket_verified": True,
    }
    if quote_present or quote_ref_idx is not None:
        quote_metadata["qq_quote_present"] = True
    if quote_ref_idx is not None:
        quote_metadata["qq_quote_ref_idx"] = quote_ref_idx
    return SimpleNamespace(
        event=SimpleNamespace(
            text_message=text,
            sender_id="alice",
            launcher_type="person",
            launcher_id=launcher,
            message_chain=SimpleNamespace(
                root=(
                    [component, {"type": "Plain", "text": text}]
                    if quote_present or quote_ref_idx is not None
                    else [component]
                )
            ),
            message_event=SimpleNamespace(
                sender=SimpleNamespace(id="alice", nickname="C2C_MESSAGE_CREATE"),
                time=component["time"],
                source_platform_object=quote_metadata,
            ),
        ),
        query_id=query,
        reply=AsyncMock(side_effect=lambda *_args, **_kwargs: qq_receipt(f"bot-{query}")),
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


@pytest.mark.parametrize("command", ["HENU KIT 状态", "绑定 HENU KIT", "解绑 HENU KIT", "确认"])
@pytest.mark.parametrize("marker", [None, False, "true"])
def test_unverified_qq_message_cannot_enter_any_kit_operation(
    monkeypatch, command, marker
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
        request = kit_event(command, 1, "forged-source")
        if marker is None:
            request.event.message_event.source_platform_object = None
        else:
            request.event.message_event.source_platform_object[
                "qq_websocket_verified"
            ] = marker
        assert await flow.handle(request, is_group=False)
        request.prevent_default.assert_called_once()
        request.prevent_postorder.assert_called_once()
        remote.assert_not_called()
        assert flow._scope_key("bot", "alice") not in plugin.storage

    asyncio.run(run())


@pytest.mark.parametrize("action", ["start", "unlink"])
def test_unverified_qq_confirmation_cannot_use_existing_pending(monkeypatch, action):
    from tests.test_confirmation_shortcut import Plugin

    async def run():
        plugin = Plugin()
        flow = KitBindingCoordinator(SimpleNamespace(plugin=plugin))
        monkeypatch.setattr(
            "henu_plugin.kit_binding.kit_settings",
            lambda: {"bot_uuid": "bot", "portal_url": "https://kit.test"},
        )
        calls = []

        def remote(settings, kind, body):
            calls.append(kind)
            if kind == "start":
                return {"token": "a" * 43}
            if kind == "pending":
                return {"state": "authorized", "display_name": "小河"}
            if kind == "unlink":
                return {"bound": False}
            raise AssertionError(kind)

        monkeypatch.setattr("henu_plugin.kit_binding.kit_request", remote)
        start_command = "绑定 HENU KIT" if action == "start" else "解绑 HENU KIT"
        assert await flow.handle(
            kit_event(start_command, 1, "source-start"), is_group=False
        )
        if action == "start":
            assert await flow.handle(
                kit_event("确认", 2, "source-preview"), is_group=False
            )
        prior_calls = calls[:]
        key = flow._scope_key("bot", "alice")
        prior_pending = plugin.storage[key]
        forged = kit_event(
            "确认",
            3,
            "forged-source",
            quote_ref_idx="ref-bot-2" if action == "start" else None,
        )
        forged.event.message_event.source_platform_object = None
        assert await flow.handle(forged, is_group=False)
        assert calls == prior_calls
        assert plugin.storage[key] == prior_pending
        assert "无法验证 QQ 消息来源" in str(forged.reply.call_args)

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

        def event(text, query, launcher="alice", quote_ref_idx=None):
            return kit_event(
                text,
                query,
                f"qq-message-{query}",
                launcher=launcher,
                quote_ref_idx=quote_ref_idx,
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
        unquoted = event("确认", 4)
        assert await flow.handle(unquoted, is_group=False)
        assert calls.count("confirm") == 0
        assert "引用" in str(unquoted.reply.call_args)
        old_quote = event("确认", 5, quote_ref_idx="ref-bot-3")
        assert await flow.handle(old_quote, is_group=False)
        assert "confirm" not in calls
        assert "不是本次提示" in str(old_quote.reply.call_args)
        quoted = event("确认", 6, quote_ref_idx="ref-bot-4")
        assert await flow.handle(quoted, is_group=False)
        assert calls.count("confirm") == 1
        assert not await flow.handle(event("确认", 7), is_group=False)

    asyncio.run(run())


@pytest.mark.parametrize(
    "bad_metadata",
    [
        "not-a-dict",
        {},
        {"t": "DIRECT_MESSAGE_CREATE", "d_id": "source-confirm", "qq_quote_ref_idx": "ref-bot-2"},
        {"t": "C2C_MESSAGE_CREATE", "d_id": "different-source", "qq_quote_ref_idx": "ref-bot-2"},
        {"t": "C2C_MESSAGE_CREATE", "d_id": "source-confirm", "qq_quote_ref_idx": "ref-bot-2", "unexpected": "x"},
        {"t": "C2C_MESSAGE_CREATE", "d_id": "source-confirm", "qq_quote_ref_idx": "bad\nref"},
        {"ext_info": {"ref_idx": "ref-bot-2"}},
    ],
)
def test_malformed_quote_metadata_cannot_confirm_binding(monkeypatch, bad_metadata):
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
            raise AssertionError(action)

        monkeypatch.setattr("henu_plugin.kit_binding.kit_request", remote)
        assert await flow.handle(
            kit_event("绑定 HENU KIT", 1, "source-start"), is_group=False
        )
        assert await flow.handle(
            kit_event("确认", 2, "source-preview"), is_group=False
        )
        quoted = kit_event(
            "确认", 3, "source-confirm", quote_ref_idx="ref-bot-2"
        )
        quoted.event.message_event.source_platform_object = bad_metadata
        assert await flow.handle(quoted, is_group=False)
        assert calls == ["start", "pending"]
        assert "无法验证 QQ 消息来源" in str(quoted.reply.call_args)

    asyncio.run(run())


@pytest.mark.parametrize("tamper", ["extra-component", "different-plain"])
def test_quoted_binding_requires_exact_confirm_message(monkeypatch, tamper):
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
            raise AssertionError(action)

        monkeypatch.setattr("henu_plugin.kit_binding.kit_request", remote)
        assert await flow.handle(
            kit_event("绑定 HENU KIT", 1, "source-start"), is_group=False
        )
        assert await flow.handle(
            kit_event("确认", 2, "source-preview"), is_group=False
        )
        quoted = kit_event(
            "确认", 3, "source-confirm", quote_ref_idx="ref-bot-2"
        )
        if tamper == "extra-component":
            quoted.event.message_chain.root.append({"type": "Image", "url": "https://example.test/"})
        else:
            quoted.event.message_chain.root[1]["text"] = "其他内容"
        assert await flow.handle(quoted, is_group=False)
        assert calls == ["start", "pending"]
        assert "引用的不是本次提示" in str(quoted.reply.call_args)

    asyncio.run(run())


def test_unlink_plain_confirm_needs_only_send_time_receipt(monkeypatch):
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
            if action == "unlink":
                return {"bound": False}
            raise AssertionError(action)

        monkeypatch.setattr("henu_plugin.kit_binding.kit_request", remote)
        prompt = kit_event("解绑 HENU KIT", 1, "source-unlink")
        receipt = qq_receipt("qq-unlink-prompt")
        del receipt["qq_c2c_receipt"]["ref_idx"]
        prompt.reply = AsyncMock(return_value=receipt)
        assert await flow.handle(prompt, is_group=False)
        saved = json.loads(
            decrypt_value(
                plugin.storage[flow._scope_key("bot", "alice")].decode()
            )
        )
        assert "ref_idx" not in saved["confirmation_receipt"]
        assert await flow.handle(
            kit_event("确认", 2, "source-confirm"), is_group=False
        )
        assert calls == ["unlink"]

    asyncio.run(run())


def test_binding_ref_idx_missing_from_send_receipt_fails_closed(monkeypatch):
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
            raise AssertionError(action)

        monkeypatch.setattr("henu_plugin.kit_binding.kit_request", remote)
        assert await flow.handle(
            kit_event("绑定 HENU KIT", 1, "source-start"), is_group=False
        )
        prompt = kit_event("确认", 2, "source-preview")
        receipt = qq_receipt("qq-preview")
        del receipt["qq_c2c_receipt"]["ref_idx"]
        prompt.reply = AsyncMock(return_value=receipt)
        assert await flow.handle(prompt, is_group=False)
        saved = json.loads(
            decrypt_value(
                plugin.storage[flow._scope_key("bot", "alice")].decode()
            )
        )
        assert saved["confirmation_receipt"] is None
        assert "引用信息无法确认" in str(prompt.reply.call_args)
        assert await flow.handle(
            kit_event("确认", 3, "source-confirm", quote_ref_idx="ref-qq-preview"),
            is_group=False,
        )
        assert calls == ["start", "pending"]
        plain = kit_event("确认", 4, "source-third-plain")
        assert await flow.handle(plain, is_group=False)
        assert calls == ["start", "pending"]
        assert "重新发送“绑定 HENU KIT”" in str(plain.reply.call_args)

    asyncio.run(run())


def test_reused_ref_idx_on_reissued_account_preview_fails_closed(monkeypatch):
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
        assert await flow.handle(
            kit_event("绑定 HENU KIT", 1, "source-start"), is_group=False
        )
        assert await flow.handle(
            kit_event("确认", 2, "source-preview"), is_group=False
        )
        reissued = kit_event("确认", 3, "source-reissue")
        duplicate = qq_receipt("different-qq-message")
        duplicate["qq_c2c_receipt"]["ref_idx"] = "ref-bot-2"
        reissued.reply = AsyncMock(return_value=duplicate)
        assert await flow.handle(reissued, is_group=False)
        saved = json.loads(
            decrypt_value(
                plugin.storage[flow._scope_key("bot", "alice")].decode()
            )
        )
        assert set(saved) == {"terminal", "bot", "conversation", "expires_at"}
        assert saved["terminal"] is True
        assert saved["bot"] == "bot"
        assert saved["conversation"] == {
            "launcher_type": "person",
            "launcher_id": "alice",
        }
        assert saved["expires_at"] > time.time()
        assert "引用标识重复" in str(reissued.reply.call_args)
        before = calls[:]
        third_plain = kit_event("确认", 4, "source-third-plain")
        assert await flow.handle(third_plain, is_group=False)
        assert "重新发送“绑定 HENU KIT”" in str(third_plain.reply.call_args)
        assert calls == before
        assert await flow.handle(
            kit_event("确认", 5, "source-confirm", quote_ref_idx="ref-bot-2"),
            is_group=False,
        )
        assert "confirm" not in calls
        assert await flow.handle(
            kit_event("绑定 HENU KIT", 6, "source-fresh-start"), is_group=False
        )
        assert calls.count("start") == 2

    asyncio.run(run())


def test_terminal_kit_record_does_not_block_other_bot_legacy_confirmation(monkeypatch):
    from henu_plugin.confirmation_shortcut import ConfirmationShortcut
    from tests.test_confirmation_shortcut import Plugin

    async def run():
        plugin = Plugin()
        flow = KitBindingCoordinator(SimpleNamespace(plugin=plugin))
        conversation = {"launcher_type": "person", "launcher_id": "alice"}
        terminal_key = flow._scope_key("bot-a", "alice")
        plugin.storage[terminal_key] = encrypt_value(
            json.dumps(
                {
                    "terminal": True,
                    "bot": "bot-a",
                    "conversation": conversation,
                    "expires_at": time.time() + 300,
                }
            )
        ).encode()
        legacy_pending = create_pending_operation(
            storage_key="alice",
            canonical_command="yuketang account set --account 13800000000 --password Demo123@",
            query_id=1,
        )
        legacy_pending["conversation"] = conversation
        plugin.storage["user:alice:yuketang_pending_credentials"] = encrypt_value(
            json.dumps(legacy_pending)
        ).encode()
        monkeypatch.setattr(
            "henu_plugin.kit_binding.kit_settings",
            lambda: {"bot_uuid": "bot-a", "portal_url": "https://kit.test"},
        )
        request = kit_event("确认", 2, "source-legacy")
        request.get_bot_uuid.return_value = "bot-b"
        assert not await flow.handle(request, is_group=False)
        request.prevent_default.assert_not_called()

        yuketang = SimpleNamespace(handle=AsyncMock(return_value=True))
        legacy = ConfirmationShortcut(SimpleNamespace(plugin=plugin, _yuketang_login=yuketang))
        assert await legacy.handle(request)
        yuketang.handle.assert_awaited_once()
        assert plugin.storage[terminal_key]

    asyncio.run(run())


@pytest.mark.parametrize("expired_kit", [False, True])
@pytest.mark.parametrize("quote_ref_idx", [None, "ref-bot-2"])
def test_stale_quoted_confirmation_never_falls_to_legacy(
    monkeypatch, expired_kit, quote_ref_idx
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
            raise AssertionError(action)

        monkeypatch.setattr("henu_plugin.kit_binding.kit_request", remote)
        if expired_kit:
            assert await flow.handle(
                kit_event("绑定 HENU KIT", 1, "source-start"), is_group=False
            )
            assert await flow.handle(
                kit_event("确认", 2, "source-preview"), is_group=False
            )
            key = flow._scope_key("bot", "alice")
            pending = json.loads(decrypt_value(plugin.storage[key].decode()))
            pending["expires_at"] = 0
            plugin.storage[key] = encrypt_value(json.dumps(pending)).encode()
        plugin.storage["user:alice:pending_operation"] = b"legacy-pending"
        before = calls[:]
        quoted = kit_event(
            "确认",
            3,
            "source-stale",
            quote_ref_idx=quote_ref_idx,
            quote_present=True,
        )
        assert await flow.handle(quoted, is_group=False)
        quoted.prevent_default.assert_called_once()
        quoted.prevent_postorder.assert_called_once()
        assert "引用的绑定操作已失效" in str(quoted.reply.call_args)
        assert calls == before
        assert plugin.storage["user:alice:pending_operation"] == b"legacy-pending"

    asyncio.run(run())


@pytest.mark.parametrize(
    "settings_state", ["missing", "wrong-bot", "error", "uuid-error"]
)
@pytest.mark.parametrize("quote_ref_idx", [None, "ref-stale"])
def test_trusted_quote_without_kit_owner_does_not_reach_legacy(
    monkeypatch, settings_state, quote_ref_idx
):
    from tests.test_confirmation_shortcut import Plugin

    async def run():
        plugin = Plugin()
        plugin.storage["user:alice:pending_operation"] = b"legacy-pending"
        flow = KitBindingCoordinator(SimpleNamespace(plugin=plugin))

        def settings():
            if settings_state == "error":
                raise RuntimeError("settings unavailable")
            if settings_state == "missing":
                return None
            return {
                "bot_uuid": "bot" if settings_state == "uuid-error" else "another-bot",
                "portal_url": "https://kit.test",
            }

        monkeypatch.setattr("henu_plugin.kit_binding.kit_settings", settings)
        quoted = kit_event(
            "确认",
            1,
            "source-quoted",
            quote_present=True,
            quote_ref_idx=quote_ref_idx,
        )
        if settings_state == "uuid-error":
            quoted.get_bot_uuid.side_effect = RuntimeError("Bot identity unavailable")
        assert await flow.handle(quoted, is_group=False)
        quoted.prevent_default.assert_called_once()
        quoted.prevent_postorder.assert_called_once()
        assert "引用的绑定操作已失效" in str(quoted.reply.call_args)
        assert plugin.storage["user:alice:pending_operation"] == b"legacy-pending"

    asyncio.run(run())


@pytest.mark.parametrize("quote_ref_idx", [None, "unrelated-quote"])
def test_quoted_confirmation_cannot_unlink_without_plain_request(
    monkeypatch, quote_ref_idx
):
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
        assert await flow.handle(
            kit_event("解绑 HENU KIT", 1, "source-start"), is_group=False
        )
        quoted = kit_event(
            "确认",
            2,
            "source-quoted",
            quote_ref_idx=quote_ref_idx,
            quote_present=True,
        )
        assert await flow.handle(quoted, is_group=False)
        quoted.prevent_default.assert_called_once()
        assert "请直接发送" in str(quoted.reply.call_args)
        remote.assert_not_called()

    asyncio.run(run())


def test_sdk_quoted_private_event_without_text_message_can_confirm(monkeypatch):
    from langbot_plugin.api.entities.builtin.platform.message import (
        MessageChain,
        Plain,
        Source,
    )
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
        assert await flow.handle(
            kit_event("绑定 HENU KIT", 1, "source-start"), is_group=False
        )
        assert await flow.handle(
            kit_event("确认", 2, "source-preview"), is_group=False
        )
        confirmation = kit_event(
            "确认", 3, "source-confirm", quote_ref_idx="ref-bot-2"
        )
        confirmation.event.text_message = None
        confirmation.event.message_chain = MessageChain(
            [
                Source(
                    id="source-confirm",
                    time=datetime.datetime.now(datetime.timezone.utc),
                ),
                Plain(text="确认"),
            ]
        )
        confirmation.event.message_event.time = (
            confirmation.event.message_chain.root[0].time
        )
        assert await flow.handle(confirmation, is_group=False)
        assert calls.count("confirm") == 1

    asyncio.run(run())


@pytest.mark.parametrize("action", ["绑定 HENU KIT", "解绑 HENU KIT"])
def test_confirmation_prompt_persists_qq_server_receipt(monkeypatch, action):
    from tests.test_confirmation_shortcut import Plugin

    async def run():
        plugin = Plugin()
        flow = KitBindingCoordinator(SimpleNamespace(plugin=plugin))
        monkeypatch.setattr(
            "henu_plugin.kit_binding.kit_settings",
            lambda: {"bot_uuid": "bot", "portal_url": "https://kit.test"},
        )

        def remote(settings, kind, body):
            if kind == "start":
                return {"token": "a" * 43}
            if kind == "pending":
                return {"state": "authorized", "display_name": "小河"}
            raise AssertionError(kind)

        monkeypatch.setattr("henu_plugin.kit_binding.kit_request", remote)
        assert await flow.handle(kit_event(action, 1, "source-start"), is_group=False)
        if action == "绑定 HENU KIT":
            assert await flow.handle(
                kit_event("确认", 2, "source-preview"), is_group=False
            )
        pending_key = flow._scope_key("bot", "alice")
        pending = json.loads(decrypt_value(plugin.storage[pending_key].decode()))
        receipt = pending["confirmation_receipt"]
        assert receipt["id"] == ("bot-2" if action == "绑定 HENU KIT" else "bot-1")
        assert receipt["ref_idx"] == (
            "ref-bot-2" if action == "绑定 HENU KIT" else "ref-bot-1"
        )
        assert isinstance(receipt["timestamp"], (int, float))
        assert abs(receipt["timestamp"] - time.time()) < 10

    asyncio.run(run())


@pytest.mark.parametrize(
    ("action", "case", "should_commit"),
    [
        ("start", "old-delayed", False),
        ("start", "same-second", False),
        ("start", "missing", False),
        ("start", "malformed", False),
        ("start", "negative-skew-valid", True),
        ("start", "positive-skew-delayed-valid", True),
        ("unlink", "old-delayed", False),
        ("unlink", "same-second", False),
        ("unlink", "missing", False),
        ("unlink", "negative-skew-valid", True),
        ("unlink", "positive-skew-delayed-valid", True),
    ],
)
def test_final_confirmation_uses_qq_receipt_clock(
    monkeypatch, action, case, should_commit
):
    from tests.test_confirmation_shortcut import Plugin

    async def run():
        plugin = Plugin()
        flow = KitBindingCoordinator(SimpleNamespace(plugin=plugin))
        monkeypatch.setattr(
            "henu_plugin.kit_binding.kit_settings",
            lambda: {"bot_uuid": "bot", "portal_url": "https://kit.test"},
        )
        now = time.time()
        # In the positive-skew case QQ is 30 s ahead when the Bot previews at
        # now-40 and the user confirms at now-35. Both events arrive later,
        # when their original QQ timestamps are no longer future to the Bot.
        receipt_time = (
            now - 70
            if case == "negative-skew-valid"
            else now - 10
            if case == "positive-skew-delayed-valid"
            else now
        )
        if case == "same-second":
            receipt_time = float(int(now) - 1)
        source_time = (
            now - 65
            if case == "negative-skew-valid"
            else now - 5
            if case == "positive-skew-delayed-valid"
            else receipt_time
            if case == "same-second"
            else now - 1
        )
        pending = create_pending_operation(
            storage_key="alice", canonical_command=action, query_id=1, now=now - 80
        )
        pending.update(
            bot="bot",
            conversation={"launcher_type": "person", "launcher_id": "alice"},
            created_source_id="source-start",
            created_source_time=now - 90,
            request_id="request-one",
        )
        if action == "start":
            pending.update(
                link_token="a" * 43,
                previewed=True,
                preview_query_id=1,
                preview_source_id="source-start",
            )
        if case == "malformed":
            pending["confirmation_receipt"] = {
                "id": "",
                "timestamp": receipt_time,
                "ref_idx": "ref-preview",
            }
        elif case != "missing":
            pending["confirmation_receipt"] = {
                "id": "qq-preview-message",
                "timestamp": receipt_time,
                "ref_idx": "ref-preview",
            }
        key = flow._scope_key("bot", "alice")
        plugin.storage[key] = encrypt_value(json.dumps(pending)).encode()
        calls = []

        def remote(settings, kind, body):
            calls.append(kind)
            if kind == "pending":
                return {"state": "authorized", "display_name": "小河"}
            if kind == "confirm":
                return {"bound": True, "display_name": "小河"}
            if kind == "unlink":
                return {"bound": False}
            raise AssertionError(kind)

        monkeypatch.setattr("henu_plugin.kit_binding.kit_request", remote)
        assert await flow.handle(
            kit_event(
                "确认",
                2,
                "source-confirm",
                source_time=source_time,
                quote_ref_idx="ref-preview" if action == "start" else None,
            ),
            is_group=False,
        )
        if should_commit:
            assert calls.count("confirm" if action == "start" else "unlink") == 1
        else:
            assert calls == []

    asyncio.run(run())


def test_immediate_positive_qq_clock_skew_fails_closed_at_start(monkeypatch):
    from tests.test_confirmation_shortcut import Plugin

    async def run():
        plugin = Plugin()
        flow = KitBindingCoordinator(SimpleNamespace(plugin=plugin))
        monkeypatch.setattr(
            "henu_plugin.kit_binding.kit_settings",
            lambda: {"bot_uuid": "bot", "portal_url": "https://kit.test"},
        )
        remote = Mock()
        monkeypatch.setattr("henu_plugin.kit_binding.kit_request", remote)
        request = kit_event(
            "绑定 HENU KIT", 1, "source-ahead", source_time=time.time() + 30
        )
        assert await flow.handle(request, is_group=False)
        remote.assert_not_called()
        assert "QQ 消息标识或时间" in str(request.reply.call_args)

    asyncio.run(run())


@pytest.mark.parametrize("missing_receipt", [False, True])
def test_async_authorization_preview_persists_qq_receipt(monkeypatch, missing_receipt):
    from tests.test_confirmation_shortcut import Plugin

    async def run():
        plugin = Plugin()
        flow = KitBindingCoordinator(SimpleNamespace(plugin=plugin))
        key = flow._scope_key("bot", "alice")
        flow.locks[key] = asyncio.Lock()
        pending = create_pending_operation(
            storage_key="alice", canonical_command="start", query_id=1
        )
        pending.update(
            bot="bot",
            conversation={"launcher_type": "person", "launcher_id": "alice"},
            request_id="request-one",
            created_source_id="source-start",
            created_source_time=time.time() - 1,
            link_token="a" * 43,
        )
        plugin.storage[key] = encrypt_value(json.dumps(pending)).encode()
        monkeypatch.setattr("henu_plugin.kit_binding.asyncio.sleep", AsyncMock())
        monkeypatch.setattr(
            "henu_plugin.kit_binding.kit_settings",
            lambda: {"bot_uuid": "bot", "portal_url": "https://kit.test"},
        )
        calls = []

        def remote(settings, action, body):
            calls.append(action)
            if action == "pending":
                return {"state": "authorized", "display_name": "小河"}
            raise AssertionError(action)

        monkeypatch.setattr(
            "henu_plugin.kit_binding.kit_request", remote,
        )
        ctx = kit_event("绑定 HENU KIT", 1, "source-start")
        if missing_receipt:
            ctx.reply = AsyncMock(return_value=None)
        await flow._watch(ctx, {"bot_uuid": "bot"}, "alice", key, "request-one")
        saved = json.loads(decrypt_value(plugin.storage[key].decode()))
        assert saved["previewed"] is True
        if missing_receipt:
            assert saved["confirmation_receipt"] is None
            assert ctx.reply.call_count == 2
            assert "提示消息的引用信息无法确认" in str(ctx.reply.call_args)
            assert await flow.handle(
                kit_event(
                    "确认", 2, "source-confirm", quote_ref_idx="ref-bot-1"
                ),
                is_group=False,
            )
            assert calls == ["pending"]
        else:
            assert saved["confirmation_receipt"]["id"] == "bot-1"
            assert isinstance(saved["confirmation_receipt"]["timestamp"], (int, float))

    asyncio.run(run())


@pytest.mark.parametrize("action", ["start", "unlink"])
@pytest.mark.parametrize(
    "bad_reply",
    [
        None,
        {},
        {"qq_c2c_receipt": {"id": "", "timestamp": "2026-09-25T12:00:00Z"}},
        {"qq_c2c_receipt": {"id": "bot-id", "timestamp": "2026-09-25 12:00:00"}},
    ],
)
def test_missing_or_bad_receipt_never_calls_core_on_confirmation(
    monkeypatch, action, bad_reply
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

        def remote(settings, kind, body):
            calls.append(kind)
            if kind == "start":
                return {"token": "a" * 43}
            if kind == "pending":
                return {"state": "authorized", "display_name": "小河"}
            raise AssertionError(kind)

        monkeypatch.setattr("henu_plugin.kit_binding.kit_request", remote)
        command = "绑定 HENU KIT" if action == "start" else "解绑 HENU KIT"
        start = kit_event(command, 1, "source-start")
        if action == "unlink":
            start.reply = AsyncMock(return_value=bad_reply)
        assert await flow.handle(start, is_group=False)
        if action == "start":
            preview = kit_event("确认", 2, "source-preview")
            preview.reply = AsyncMock(return_value=bad_reply)
            assert await flow.handle(preview, is_group=False)
            prompt = preview
        else:
            prompt = start
        assert prompt.reply.call_count == 2
        assert (
            "提示消息的引用信息无法确认"
            if action == "start"
            else "提示消息的发送时间无法确认"
        ) in str(prompt.reply.call_args)
        pending_key = flow._scope_key("bot", "alice")
        saved = json.loads(decrypt_value(plugin.storage[pending_key].decode()))
        assert saved["confirmation_receipt"] is None
        before = calls[:]
        assert await flow.handle(
            kit_event("确认", 3, "source-confirm", quote_ref_idx="ref-bot-2"),
            is_group=False,
        )
        assert calls == before

    asyncio.run(run())


def test_unlink_receipt_storage_failure_never_calls_core(monkeypatch):
    from tests.test_confirmation_shortcut import Plugin

    async def run():
        plugin = Plugin()
        flow = KitBindingCoordinator(SimpleNamespace(plugin=plugin))
        monkeypatch.setattr(
            "henu_plugin.kit_binding.kit_settings",
            lambda: {"bot_uuid": "bot", "portal_url": "https://kit.test"},
        )
        remote = Mock()
        monkeypatch.setattr("henu_plugin.kit_binding.kit_request", remote)
        key = flow._scope_key("bot", "alice")
        save = plugin.set_plugin_storage
        pending_writes = 0

        async def fail_receipt_save(storage_key, value):
            nonlocal pending_writes
            if storage_key == key:
                pending_writes += 1
                if pending_writes == 2:
                    raise OSError("storage unavailable")
            await save(storage_key, value)

        plugin.set_plugin_storage = fail_receipt_save
        assert await flow.handle(
            kit_event("解绑 HENU KIT", 1, "source-unlink"), is_group=False
        )
        saved = json.loads(decrypt_value(plugin.storage[key].decode()))
        assert "confirmation_receipt" not in saved
        assert await flow.handle(
            kit_event("确认", 2, "source-confirm"), is_group=False
        )
        remote.assert_not_called()

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

        def event(text, query, source, quote_ref_idx=None):
            return kit_event(text, query, source, quote_ref_idx=quote_ref_idx)

        assert await flow.handle(event(earlier_action, 1, "source-a"), is_group=False)
        if earlier_action == "绑定 HENU KIT":
            assert await flow.handle(event("确认", 2, "source-b"), is_group=False)
        first_ref = "ref-bot-2" if earlier_action == "绑定 HENU KIT" else None
        assert await flow.handle(
            event("确认", 3, "source-c", first_ref), is_group=False
        )
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
        assert await flow.handle(
            event("确认", 6, "source-c", first_ref), is_group=False
        )
        assert calls.count("confirm") == confirms_before_replay
        assert calls.count("unlink") == unlinks_before_replay

        assert await flow.handle(
            event("确认", 7, "source-d", "ref-bot-5"), is_group=False
        )
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

        def event(text, query, source, quote_ref_idx=None):
            return kit_event(text, query, source, quote_ref_idx=quote_ref_idx)

        assert await flow.handle(event("绑定 HENU KIT", 1, "source-a"), is_group=False)
        assert await flow.handle(event("确认", 2, "source-before-approval"), is_group=False)
        assert "confirm" not in calls
        assert await flow.handle(event("确认", 3, "source-preview-a"), is_group=False)
        assert await flow.handle(
            event("确认", 4, "source-complete-a", "ref-bot-3"),
            is_group=False,
        )
        assert calls.count("confirm") == 1

        flow = KitBindingCoordinator(SimpleNamespace(plugin=plugin))
        assert await flow.handle(event("绑定 HENU KIT", 5, "source-b"), is_group=False)
        assert await flow.handle(event("确认", 6, "source-preview-b"), is_group=False)
        before_replay = calls.count("confirm")
        assert await flow.handle(event("确认", 7, "source-before-approval"), is_group=False)
        assert calls.count("confirm") == before_replay
        assert await flow.handle(
            event("确认", 8, "source-fresh", "ref-bot-6"), is_group=False
        )
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

        def event(text, query, source, quote_ref_idx=None):
            return kit_event(text, query, source, quote_ref_idx=quote_ref_idx)

        assert not await flow.handle(event("确认", 1, "source-before-kit"), is_group=False)
        flow = KitBindingCoordinator(SimpleNamespace(plugin=plugin))
        assert await flow.handle(event("绑定 HENU KIT", 2, "source-a"), is_group=False)
        assert await flow.handle(event("确认", 3, "source-preview"), is_group=False)
        assert await flow.handle(event("确认", 4, "source-before-kit"), is_group=False)
        assert "confirm" not in calls
        assert await flow.handle(
            event("确认", 5, "source-fresh", "ref-bot-3"), is_group=False
        )
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
        assert await flow.handle(
            kit_event(
                "确认", 3, "source-complete", quote_ref_idx="ref-bot-2"
            ),
            is_group=False,
        )
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

        def event(text, query, source, source_time, quote_ref_idx=None):
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
                    message_chain=SimpleNamespace(
                        root=(
                            [component, {"type": "Plain", "text": text}]
                            if quote_ref_idx is not None
                            else [component]
                        )
                    ),
                    message_event=SimpleNamespace(
                        sender=SimpleNamespace(
                            id="alice", nickname="C2C_MESSAGE_CREATE"
                        ),
                        time=component.get("time"),
                        source_platform_object=(
                            {
                                "t": "C2C_MESSAGE_CREATE",
                                "d_id": source,
                                "qq_websocket_verified": True,
                                "qq_quote_present": True,
                                "qq_quote_ref_idx": quote_ref_idx,
                            }
                            if quote_ref_idx is not None
                            else {
                                "t": "C2C_MESSAGE_CREATE",
                                "d_id": source,
                                "qq_websocket_verified": True,
                            }
                        ),
                    ),
                ),
                query_id=query,
                reply=AsyncMock(
                    side_effect=lambda *_args, **_kwargs: qq_receipt(f"bot-{query}")
                ),
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
        old = event(
            "确认",
            100,
            "source-from-before-release",
            bad_time,
            "ref-bot-2",
        )
        assert await flow.handle(old, is_group=False)
        assert "confirm" not in calls
        assert await flow.handle(
            event("确认", 3, "source-fresh", time.time(), "ref-bot-2"),
            is_group=False,
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
        assert await flow.handle(legacy, is_group=False)
        legacy.prevent_default.assert_called_once()
        assert "无法验证 QQ 消息来源" in str(legacy.reply.call_args)

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
        assert await flow.handle(
            kit_event("确认", 5, "source-fresh", quote_ref_idx="ref-bot-3"),
            is_group=False,
        )
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


def test_final_confirmation_requires_source_time_after_qq_receipt(monkeypatch):
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
        receipt_time = pending["confirmation_receipt"]["timestamp"]
        assert pending["previewed"] is True

        same_time = kit_event(
            "确认",
            3,
            "source-same-time",
            source_time=second,
            quote_ref_idx="ref-bot-2",
        )
        assert await flow.handle(same_time, is_group=False)
        assert "confirm" not in calls
        assert "无法核实消息先后" in str(same_time.reply.call_args)

        await asyncio.sleep(max(0, int(receipt_time) + 1 - time.time()) + 0.01)
        assert await flow.handle(
            kit_event(
                "确认",
                4,
                "source-fresh",
                source_time=int(time.time()),
                quote_ref_idx="ref-bot-2",
            ),
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

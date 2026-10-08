from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from components.event_listener.identity_capture_safe import SafeIdentityCaptureListener


@pytest.fixture(autouse=True)
def _reset_pending_intake_state():
    SafeIdentityCaptureListener._credential_intake_deadlines.clear()
    yield
    SafeIdentityCaptureListener._credential_intake_deadlines.clear()


class _FakeContext:
    def __init__(self, text: str, user_id: str = "10001") -> None:
        self.event = SimpleNamespace(
            text_message=text,
            reply_message_chain=None,
            launcher_type="person",
            launcher_id=user_id,
            sender_id=user_id,
        )
        self.query_id = "query-1"
        self.prevented = False

    def prevent_default(self) -> None:
        self.prevented = True


class _FakeStoragePlugin:
    def __init__(self, storage: dict[str, bytes]) -> None:
        self.storage = storage

    async def get_plugin_storage_keys(self):
        return list(self.storage)

    async def get_plugin_storage(self, key: str):
        return self.storage[key]

    async def set_plugin_storage(self, key: str, value: bytes):
        self.storage[key] = value


class _UnavailableStoragePlugin:
    async def get_plugin_storage_keys(self):
        raise RuntimeError("storage unavailable")


def _reply_text(ctx: _FakeContext) -> str:
    chain = ctx.event.reply_message_chain
    return " ".join(str(getattr(item, "text", "")) for item in chain)


def test_private_free_form_credentials_are_stopped_before_the_model() -> None:
    listener = SafeIdentityCaptureListener.__new__(SafeIdentityCaptureListener)

    for message in (
        "2023000000 ExamplePassword123",
        "学号: 2023000000，密码: ExamplePassword123",
        "密码: ExamplePassword123，学号: 2023000000",
        "2023000000 abcdefgh",
        "2023000000 12345678",
    ):
        ctx = _FakeContext(message)
        asyncio.run(listener._maybe_handle_sensitive_command(ctx, is_group=False))

        assert ctx.prevented is True
        reply = _reply_text(ctx)
        assert "ExamplePassword123" not in reply
        assert "account set --student-id <学号> --password <密码>" in reply


def test_login_intent_is_handled_without_asking_the_model_for_a_password() -> None:
    listener = SafeIdentityCaptureListener.__new__(SafeIdentityCaptureListener)
    ctx = _FakeContext("帮我登录")

    asyncio.run(listener._maybe_handle_sensitive_command(ctx, is_group=False))

    assert ctx.prevented is True
    reply = _reply_text(ctx)
    assert "私聊" in reply
    assert "account set --student-id <学号> --password <密码>" in reply


def test_group_login_or_credentials_are_stopped_without_echoing_values() -> None:
    listener = SafeIdentityCaptureListener.__new__(SafeIdentityCaptureListener)

    for message in ("帮我登录", "2023000000 ExamplePassword123"):
        ctx = _FakeContext(message)
        asyncio.run(listener._maybe_handle_sensitive_command(ctx, is_group=True))

        assert ctx.prevented is True
        assert "ExamplePassword123" not in _reply_text(ctx)
        assert "私聊" in _reply_text(ctx)


def test_login_status_question_is_not_misclassified_as_credential_intake() -> None:
    listener = SafeIdentityCaptureListener.__new__(SafeIdentityCaptureListener)
    ctx = _FakeContext("查询登录状态")

    asyncio.run(listener._maybe_handle_sensitive_command(ctx, is_group=False))

    assert ctx.prevented is False
    assert ctx.event.reply_message_chain is None


def test_login_intent_arms_private_intake_and_blocks_password_only_followup() -> None:
    listener = SafeIdentityCaptureListener.__new__(SafeIdentityCaptureListener)
    intent = _FakeContext("帮我登录")
    password = _FakeContext("OnlyLettersPassword")

    asyncio.run(listener._maybe_handle_sensitive_command(intent, is_group=False))
    asyncio.run(listener._maybe_handle_sensitive_command(password, is_group=False))

    assert intent.prevented is True
    assert password.prevented is True
    assert "OnlyLettersPassword" not in _reply_text(password)
    assert "未发送给模型" in _reply_text(password)


def test_pending_credential_intake_is_scoped_per_private_user() -> None:
    listener = SafeIdentityCaptureListener.__new__(SafeIdentityCaptureListener)

    asyncio.run(
        listener._maybe_handle_sensitive_command(
            _FakeContext("帮我登录", user_id="10001"),
            is_group=False,
        )
    )
    unrelated = _FakeContext("普通问题", user_id="10002")
    asyncio.run(listener._maybe_handle_sensitive_command(unrelated, is_group=False))

    assert unrelated.prevented is False


def test_pending_credential_intake_survives_listener_recreation() -> None:
    first_listener = SafeIdentityCaptureListener.__new__(SafeIdentityCaptureListener)
    second_listener = SafeIdentityCaptureListener.__new__(SafeIdentityCaptureListener)

    asyncio.run(
        first_listener._maybe_handle_sensitive_command(
            _FakeContext("帮我登录", user_id="restart-user"),
            is_group=False,
        )
    )
    password = _FakeContext("OnlyLettersPassword", user_id="restart-user")
    asyncio.run(
        second_listener._maybe_handle_sensitive_command(password, is_group=False)
    )

    assert password.prevented is True
    assert "OnlyLettersPassword" not in _reply_text(password)


def test_pending_credential_intake_survives_process_local_state_loss() -> None:
    storage: dict[str, bytes] = {}
    first_listener = SafeIdentityCaptureListener.__new__(SafeIdentityCaptureListener)
    first_listener.plugin = _FakeStoragePlugin(storage)

    asyncio.run(
        first_listener._maybe_handle_sensitive_command(
            _FakeContext("帮我登录", user_id="persistent-user"),
            is_group=False,
        )
    )
    SafeIdentityCaptureListener._credential_intake_deadlines.clear()

    second_listener = SafeIdentityCaptureListener.__new__(SafeIdentityCaptureListener)
    second_listener.plugin = _FakeStoragePlugin(storage)
    password = _FakeContext("OnlyLettersPassword", user_id="persistent-user")
    asyncio.run(
        second_listener._maybe_handle_sensitive_command(password, is_group=False)
    )

    assert password.prevented is True
    assert "OnlyLettersPassword" not in _reply_text(password)


def test_storage_list_outage_does_not_invent_pending_intake() -> None:
    listener = SafeIdentityCaptureListener.__new__(SafeIdentityCaptureListener)
    listener.plugin = _UnavailableStoragePlugin()
    ordinary = _FakeContext("查询今天课表", user_id="ordinary-user")

    asyncio.run(listener._maybe_handle_sensitive_command(ordinary, is_group=False))

    assert ordinary.prevented is False


def test_outgoing_model_text_redacts_credentials_as_defense_in_depth() -> None:
    for text, secrets in (
        (
            "请发送 account set --student-id 2023000000 "
            "--password 'Never Echo This Secret'",
            ("2023000000", "Never Echo This Secret"),
        ),
        (
            "登录凭据 2023000000 ExamplePassword123",
            ("2023000000", "ExamplePassword123"),
        ),
        ("2023000000 ExamplePassword123", ("2023000000", "ExamplePassword123")),
        (
            "密码: 'My Secret 123'，学号: 2023000000",
            ("2023000000", "My Secret 123"),
        ),
        (
            "账号 2023000000 NeverEchoThisSecret",
            ("2023000000", "NeverEchoThisSecret"),
        ),
    ):
        redacted = SafeIdentityCaptureListener._redact_sensitive_output(text)

        assert all(secret not in redacted for secret in secrets)
        assert "<已拦截" in redacted


def test_outgoing_dlp_does_not_redact_course_or_order_identifiers() -> None:
    for text in (
        "普通订单 2023000000 AB123456",
        "课程 04500142 理解当代中国大学英语",
    ):
        assert SafeIdentityCaptureListener._redact_sensitive_output(text) == text

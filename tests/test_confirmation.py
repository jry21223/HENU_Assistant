from henu_plugin.confirmation import (
    create_pending_operation,
    split_confirm_token,
    validate_pending_operation,
)


def test_confirmation_requires_a_later_query_and_exact_parameters() -> None:
    pending = create_pending_operation(
        storage_key="10001",
        canonical_command="library cancel --record-id 7",
        query_id=10,
        now=1000,
    )

    same_turn = validate_pending_operation(
        pending,
        token=pending["token"],
        storage_key="10001",
        canonical_command="library cancel --record-id 7",
        query_id=10,
        now=1001,
    )
    assert not same_turn.ok
    assert "下一条消息" in same_turn.message

    changed = validate_pending_operation(
        pending,
        token=pending["token"],
        storage_key="10001",
        canonical_command="library cancel --record-id 8",
        query_id=11,
        now=1001,
    )
    assert not changed.ok

    accepted = validate_pending_operation(
        pending,
        token=pending["token"],
        storage_key="10001",
        canonical_command="library cancel --record-id 7",
        query_id=11,
        now=1001,
    )
    assert accepted.ok


def test_confirmation_expires() -> None:
    pending = create_pending_operation(
        storage_key="10001",
        canonical_command="seminar signin --auto-scan",
        query_id=1,
        ttl_seconds=30,
        now=100,
    )
    check = validate_pending_operation(
        pending,
        token=pending["token"],
        storage_key="10001",
        canonical_command=pending["command"],
        query_id=2,
        now=131,
    )
    assert not check.ok
    assert "过期" in check.message


def test_inline_confirm_token_is_removed_before_fingerprinting() -> None:
    command, token = split_confirm_token(
        "library cancel --record-id 7 --confirm-token='abc def'"
    )
    assert command == "library cancel --record-id 7"
    assert token == "abc def"

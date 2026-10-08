import asyncio
import json
import unittest
from unittest.mock import patch

from components.cli_tools.base import BaseHenuTool
from components.cli_tools.henu_cli import HenuCli
from henu_plugin.confirmation import (
    create_pending_operation,
    pending_storage_key,
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


class _ConfirmationPlugin:
    def __init__(self) -> None:
        self.storage: dict[str, bytes] = {}

    async def get_plugin_storage(self, key: str) -> bytes:
        await asyncio.sleep(0)
        return self.storage.get(key, b"")

    async def set_plugin_storage(self, key: str, value: bytes) -> None:
        await asyncio.sleep(0)
        self.storage[key] = value


class ConfirmationExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        HenuCli._confirmation_locks.clear()
        self.plugin = _ConfirmationPlugin()
        self.tool = object.__new__(HenuCli)
        self.tool.plugin = self.plugin
        self.storage_key = "qq-10001"
        self.command = "library cancel --record-id 7"
        self.pending = create_pending_operation(
            storage_key=self.storage_key,
            canonical_command=self.command,
            query_id=10,
        )
        self.plugin.storage[pending_storage_key(self.storage_key)] = json.dumps(
            self.pending
        ).encode("utf-8")

    async def asyncTearDown(self) -> None:
        HenuCli._confirmation_locks.clear()

    async def test_same_token_concurrent_confirm_executes_external_write_once(self) -> None:
        calls = 0

        async def fake_base_call(_tool, _params, _session, _query_id):
            nonlocal calls
            calls += 1
            await asyncio.sleep(0)
            return {"success": True}

        with patch.object(BaseHenuTool, "call", new=fake_base_call):
            results = await asyncio.gather(
                self.tool._execute_pending(
                    token=str(self.pending["token"]),
                    storage_key=self.storage_key,
                    canonical_command=self.command,
                    session=object(),
                    query_id=11,
                ),
                self.tool._execute_pending(
                    token=str(self.pending["token"]),
                    storage_key=self.storage_key,
                    canonical_command=self.command,
                    session=object(),
                    query_id=12,
                ),
            )

        self.assertEqual(calls, 1)
        self.assertEqual(sum(result.get("success") is True for result in results), 1)
        receipt = json.loads(
            self.plugin.storage[pending_storage_key(self.storage_key)].decode("utf-8")
        )
        self.assertEqual(receipt["schema"], "henu.confirmation-receipt.v1")
        self.assertEqual(receipt["status"], "committed")

    async def test_cancellation_after_external_commit_cannot_replay_token(self) -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def cancellation_safe_base(_tool, _params, _session, _query_id):
            nonlocal calls
            calls += 1
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
                raise
            return {"success": True}

        with patch.object(BaseHenuTool, "call", new=cancellation_safe_base):
            first = asyncio.create_task(
                self.tool._execute_pending(
                    token=str(self.pending["token"]),
                    storage_key=self.storage_key,
                    canonical_command=self.command,
                    session=object(),
                    query_id=11,
                )
            )
            await asyncio.wait_for(started.wait(), timeout=1)
            first.cancel()
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await first

            replay = await self.tool._execute_pending(
                token=str(self.pending["token"]),
                storage_key=self.storage_key,
                canonical_command=self.command,
                session=object(),
                query_id=12,
            )

        self.assertEqual(calls, 1)
        self.assertEqual(replay.get("error_code"), "confirmation_invalid")
        receipt = json.loads(
            self.plugin.storage[pending_storage_key(self.storage_key)].decode("utf-8")
        )
        self.assertEqual(receipt["schema"], "henu.confirmation-receipt.v1")
        self.assertEqual(receipt["status"], "uncertain")

    async def test_new_pending_request_waits_for_an_inflight_confirmation(self) -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        async def blocked_base(_tool, _params, _session, _query_id):
            started.set()
            await release.wait()
            return {"success": True}

        with patch.object(BaseHenuTool, "call", new=blocked_base):
            confirming = asyncio.create_task(
                self.tool._execute_pending(
                    token=str(self.pending["token"]),
                    storage_key=self.storage_key,
                    canonical_command=self.command,
                    session=object(),
                    query_id=11,
                )
            )
            await asyncio.wait_for(started.wait(), timeout=1)
            requesting = asyncio.create_task(
                self.tool._request_confirmation(
                    storage_key=self.storage_key,
                    canonical_command="seminar cancel --record-id 9",
                    action="取消研讨室预约",
                    parameter_summary="record_id=9",
                    query_id=12,
                )
            )
            await asyncio.sleep(0)
            self.assertFalse(requesting.done())

            release.set()
            await confirming
            await requesting

        latest = json.loads(
            self.plugin.storage[pending_storage_key(self.storage_key)].decode("utf-8")
        )
        self.assertEqual(latest["schema"], "henu.pending-operation.v1")
        self.assertEqual(latest["command"], "seminar cancel --record-id 9")

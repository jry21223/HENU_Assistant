"""One-shot worker and private CLI seams; synthetic membership/school boundaries."""

import asyncio
import datetime as dt
import json

import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from henu_mcp.core.secure_storage import encrypt_value
from henu_plugin.cli import inspect_cli_command


def test_schedule_cli_requires_separate_execution_and_usage_times():
    command = "library schedule add --run-at 2026-10-10T07:00:00+08:00 --location 金明馆北区 --seat-no 201 --date 2026-10-11 --time 08:00 --end-time 10:00"
    spec = inspect_cli_command(command)
    assert spec.resolved_tool == "library_schedule_add"
    assert spec.params["run_at"] == "2026-10-10T07:00:00+08:00"
    assert spec.params["target_date"] == "2026-10-11"
    assert inspect_cli_command(command + " --password secret").error
    assert inspect_cli_command(command + " --date 2026-10-12").error


class Plugin:
    def __init__(self, root):
        from henu_plugin.hardened_service import HardenedHenuPluginService

        self.storage = {
            "user:alice:profile": json.dumps(
                {
                    "student_id": "synthetic-student",
                    "password": encrypt_value("SyntheticPassword!"),
                }
            ).encode()
        }
        self.service = HardenedHenuPluginService(root)

    async def get_plugin_storage(self, key):
        return self.storage[key]

    async def set_plugin_storage(self, key, raw):
        self.storage[key] = raw


def context(command, query=1, sender="alice", launcher=None, kind="person"):
    return SimpleNamespace(
        query_id=query,
        event=SimpleNamespace(
            text_message=command,
            sender_id=sender,
            launcher_id=launcher or sender,
            launcher_type=kind,
        ),
        reply=AsyncMock(),
        prevent_default=Mock(),
        prevent_postorder=Mock(),
    )


COMMAND = "library schedule add --run-at 2026-10-10T07:00:00+08:00 --location 金明馆北区 --seat-no 201 --date 2026-10-11 --time 08:00 --end-time 10:00"
CONVERSATION = {"launcher_type": "person", "launcher_id": "alice"}


def test_private_confirm_and_due_worker_submit_once(core, monkeypatch, tmp_path):
    from henu_plugin.library_schedule import LibraryScheduler
    from henu_plugin.confirmation_shortcut import ConfirmationShortcut

    clock = [dt.datetime.fromisoformat("2026-10-10T06:59:00+08:00")]
    school = Mock(
        return_value={"success": True, "date": "2026-10-11", "msg": "test receipt"}
    )
    monkeypatch.setattr("mcp_server.library_reserve", school)

    async def run():
        plugin = Plugin(tmp_path)
        scheduler = LibraryScheduler(plugin, clock=lambda: clock[0])
        plugin._henu_library_scheduler = scheduler
        await scheduler.start()
        initial = context(COMMAND)
        assert await scheduler.handle(initial, is_group=False)
        assert "确认定时预约" in str(initial.reply.call_args)
        assert not school.called
        shortcut = ConfirmationShortcut(SimpleNamespace(plugin=plugin))
        await shortcut.handle(context("确认定时预约", query=2, sender="bob"))
        await shortcut.handle(context("确认定时预约", query=3, launcher="other"))
        await shortcut.handle(context("确认定时预约", query=4))
        result = await scheduler.execute(
            "library schedule list", "alice", CONVERSATION, 5
        )
        assert len(result["jobs"]) == 1 and result["jobs"][0]["status"] == "pending"
        assert all(
            b"SyntheticPassword!" not in raw and b"synthetic-student" not in raw
            for key, raw in plugin.storage.items()
            if "profile" not in key
        )
        await scheduler.run_due()
        school.assert_not_called()
        clock[0] += dt.timedelta(seconds=60)
        await asyncio.gather(scheduler.run_due(), scheduler.run_due())
        assert school.call_count == 1
        assert school.call_args.kwargs["max_attempts"] == 1
        assert school.call_args.kwargs["retry_until"] == ""
        assert school.call_args.kwargs["target_date"] == "2026-10-11"
        result = await scheduler.execute(
            "library schedule list", "alice", CONVERSATION, 6
        )
        assert result["jobs"][0]["status"] == "succeeded"
        await scheduler.shutdown()

    asyncio.run(run())


def test_unknown_school_receipt_blocks_same_booking_with_new_run_time(
    core, monkeypatch, tmp_path
):
    from henu_plugin.library_schedule import LibraryScheduler

    school = Mock(
        return_value={"success": False, "msg": "request timeout SyntheticPassword!"}
    )
    monkeypatch.setattr("mcp_server.library_reserve", school)
    clock = [dt.datetime.fromisoformat("2026-10-10T06:59:00+08:00")]

    async def run():
        plugin = Plugin(tmp_path)
        scheduler = LibraryScheduler(plugin, clock=lambda: clock[0])
        await scheduler.start()
        await scheduler.execute(COMMAND, "alice", CONVERSATION, 1)
        pending = await scheduler.valid_pending("alice", CONVERSATION, 2)
        await scheduler.execute(
            "library schedule confirm",
            "alice",
            CONVERSATION,
            2,
            confirmation_token=pending["token"],
        )
        clock[0] += dt.timedelta(seconds=60)
        await scheduler.run_due()
        jobs = (
            await scheduler.execute("library schedule list", "alice", CONVERSATION, 3)
        )["jobs"]
        assert jobs[0]["status"] == "unknown" and "SyntheticPassword!" not in str(jobs)
        retry = await scheduler.execute(
            COMMAND.replace("07:00:00", "07:01:00"), "alice", CONVERSATION, 4
        )
        assert not retry["success"] and not retry.get("confirmation_required")
        assert school.call_count == 1
        await scheduler.shutdown()

    asyncio.run(run())


@pytest.mark.parametrize(
    "condition,expected",
    [
        ("revoked", "blocked"),
        ("outage", "blocked"),
        ("account", "blocked"),
        ("cancel", "cancelled"),
        ("missed", "missed"),
    ],
)
def test_execution_checks_fresh_dependencies_or_cancel_before_school(
    core, monkeypatch, tmp_path, condition, expected
):
    from henu_plugin.library_schedule import LibraryScheduler

    data, calls, response = core
    clock = [dt.datetime.fromisoformat("2026-10-10T06:59:00+08:00")]
    school = Mock(return_value={"success": True})
    monkeypatch.setattr("mcp_server.library_reserve", school)

    async def run():
        plugin = Plugin(tmp_path)
        scheduler = LibraryScheduler(plugin, clock=lambda: clock[0])
        await scheduler.start()
        await scheduler.execute(COMMAND, "alice", CONVERSATION, 1)
        pending = await scheduler.valid_pending("alice", CONVERSATION, 2)
        created = await scheduler.execute(
            "library schedule confirm",
            "alice",
            CONVERSATION,
            2,
            confirmation_token=pending["token"],
        )
        task_id = created["id"]
        if condition == "revoked":
            data.update(plan="free", lifetime=False, allowed=False, benefits=[])
        elif condition == "outage":
            response.status_code = 503
        elif condition == "account":
            profile = json.loads(plugin.storage["user:alice:profile"])
            profile["student_id"] = "different-student"
            plugin.storage["user:alice:profile"] = json.dumps(profile).encode()
        elif condition == "cancel":
            data.update(plan="free", lifetime=False, allowed=False, benefits=[])
            cancelled = await scheduler.execute(
                "library schedule cancel --id " + task_id, "alice", CONVERSATION, 3
            )
            assert cancelled["success"]
        clock[0] += dt.timedelta(seconds=180 if condition == "missed" else 60)
        await scheduler.run_due()
        status = await scheduler.execute(
            "library schedule status --id " + task_id, "alice", CONVERSATION, 4
        )
        assert status["job"]["status"] == expected
        assert not (
            await scheduler.execute(
                "library schedule status --id " + task_id,
                "bob",
                {"launcher_type": "person", "launcher_id": "bob"},
                5,
            )
        )["success"]
        school.assert_not_called()
        await scheduler.shutdown()

    asyncio.run(run())


def test_same_student_rebinding_cannot_reissue_accepted_booking(
    core, monkeypatch, tmp_path
):
    from henu_plugin.library_schedule import LibraryScheduler

    clock = [dt.datetime.fromisoformat("2026-10-10T06:59:00+08:00")]
    school = Mock(return_value={"success": True})
    monkeypatch.setattr("mcp_server.library_reserve", school)

    async def run():
        plugin = Plugin(tmp_path)
        scheduler = LibraryScheduler(plugin, clock=lambda: clock[0])
        await scheduler.start()
        await scheduler.execute(COMMAND, "alice", CONVERSATION, 1)
        pending = await scheduler.valid_pending("alice", CONVERSATION, 2)
        await scheduler.execute(
            "library schedule confirm",
            "alice",
            CONVERSATION,
            2,
            confirmation_token=pending["token"],
        )
        clock[0] += dt.timedelta(seconds=60)
        await scheduler.run_due()
        profile = json.loads(plugin.storage["user:alice:profile"])
        profile["password"] = encrypt_value("AnotherSyntheticPassword!")
        plugin.storage["user:alice:profile"] = json.dumps(profile).encode()
        retry = await scheduler.execute(
            COMMAND.replace("07:00:00", "07:01:00"), "alice", CONVERSATION, 3
        )
        assert (
            not retry.get("confirmation_required")
            and retry["error_code"] == "duplicate_booking"
        )
        await scheduler.shutdown()

    asyncio.run(run())


@pytest.mark.parametrize("crash", [False, True])
def test_actual_process_reopen_preserves_claim_and_never_reissues(
    core, tmp_path, monkeypatch, crash
):
    import os
    from pathlib import Path
    import subprocess
    import sys

    monkeypatch.setenv("HENU_MASTER_KEY", "persistent-synthetic-master-key")
    helper = Path(__file__).parent / "support" / "library_schedule_process.py"

    def invoke(stage, expected=0):
        env = dict(
            os.environ,
            PYTHONPATH=str(Path(__file__).resolve().parents[1]),
            PYTHONDONTWRITEBYTECODE="1",
        )
        child = subprocess.run(
            [sys.executable, str(helper), str(tmp_path), stage],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert child.returncode == expected, child.stderr
        return json.loads(child.stdout) if expected == 0 else None

    assert invoke("create")["jobs"][0]["status"] == "pending"
    # Task records contain neither raw credentials nor the synthetic student ID.
    assert all(
        b"SyntheticPassword!" not in path.read_bytes()
        and b"synthetic-student" not in path.read_bytes()
        for path in tmp_path.iterdir()
        if path.is_file()
        and path.name != "school-calls"
        and b'"student_id"' not in path.read_bytes()
    )
    invoke("crash" if crash else "due", 77 if crash else 0)
    reopened = invoke("reopen")
    assert reopened["jobs"][0]["status"] == ("unknown" if crash else "succeeded")
    assert len((tmp_path / "school-calls").read_text().splitlines()) == 1


@pytest.mark.parametrize(
    "bad_run,bad_usage",
    [
        ("2026-10-10T07:00:00", "2026-10-11"),
        ("2026-10-10T07:00:00Z", "2026-10-11"),
        ("2026-10-10T07:00:00+09:00", "2026-10-11"),
        ("2026-10-10T06:58:00+08:00", "2026-10-11"),
        ("2026-11-11T07:00:00+08:00", "2026-11-12"),
        ("2026-10-10T07:00:00+08:00", "2026-10-09"),
    ],
)
def test_bad_dates_timezones_and_unbounded_future_never_create(
    core, tmp_path, bad_run, bad_usage
):
    from henu_plugin.library_schedule import LibraryScheduler

    async def run():
        plugin = Plugin(tmp_path)
        scheduler = LibraryScheduler(
            plugin, clock=lambda: dt.datetime.fromisoformat("2026-10-10T06:59:00+08:00")
        )
        await scheduler.start()
        command = COMMAND.replace("2026-10-10T07:00:00+08:00", bad_run).replace(
            "--date 2026-10-11", "--date " + bad_usage
        )
        rejected = await scheduler.execute(command, "alice", CONVERSATION, 1)
        assert not rejected["success"] and not rejected.get("confirmation_required")
        assert (
            await scheduler.execute("library schedule list", "alice", CONVERSATION, 2)
        )["jobs"] == []
        await scheduler.shutdown()

    asyncio.run(run())


def test_storage_write_and_startup_outages_never_submit_or_overwrite(
    core, monkeypatch, tmp_path
):
    from henu_plugin.library_schedule import LibraryScheduler, KEY

    school = Mock(return_value={"success": True})
    monkeypatch.setattr("mcp_server.library_reserve", school)
    clock = [dt.datetime.fromisoformat("2026-10-10T06:59:00+08:00")]

    async def run():
        plugin = Plugin(tmp_path)
        scheduler = LibraryScheduler(plugin, clock=lambda: clock[0])
        await scheduler.start()
        await scheduler.execute(COMMAND, "alice", CONVERSATION, 1)
        pending = await scheduler.valid_pending("alice", CONVERSATION, 2)
        await scheduler.execute(
            "library schedule confirm",
            "alice",
            CONVERSATION,
            2,
            confirmation_token=pending["token"],
        )
        original = plugin.storage[KEY]
        plugin.set_plugin_storage = AsyncMock(side_effect=OSError("secret SDK path"))
        clock[0] += dt.timedelta(seconds=60)
        with pytest.raises(OSError):
            await scheduler.run_due()
        assert plugin.storage[KEY] == original
        school.assert_not_called()
        await scheduler.shutdown()
        plugin.get_plugin_storage = AsyncMock(side_effect=OSError("SDK outage"))
        restored = LibraryScheduler(plugin, clock=lambda: clock[0])
        with pytest.raises(OSError):
            await restored.start()
        assert not restored.ready
        await restored.run_due()
        school.assert_not_called()
        assert plugin.storage[KEY] == original

    asyncio.run(run())


def test_concurrent_confirm_and_ambiguous_plain_confirm_are_safe(core, tmp_path):
    from henu_plugin.library_schedule import LibraryScheduler
    from henu_plugin.confirmation_shortcut import ConfirmationShortcut
    from henu_plugin.qingmu_flow import get_coordinator

    async def run():
        plugin = Plugin(tmp_path)
        scheduler = LibraryScheduler(
            plugin, clock=lambda: dt.datetime.fromisoformat("2026-10-10T06:59:00+08:00")
        )
        plugin._henu_library_scheduler = scheduler
        await scheduler.start()
        await scheduler.execute(COMMAND, "alice", CONVERSATION, 1)
        # Real other pending binding; plain confirmation must not choose the first.
        await get_coordinator(plugin).execute(
            "qingmu account set --platform ucampus --account synthetic --password secret --school 河南大学",
            "alice",
            CONVERSATION,
            2,
        )
        shortcut = ConfirmationShortcut(SimpleNamespace(plugin=plugin))
        ambiguous = context("确认", query=3)
        await shortcut.handle(ambiguous)
        assert "多个操作" in str(ambiguous.reply.call_args)
        assert (
            await scheduler.execute("library schedule list", "alice", CONVERSATION, 4)
        )["jobs"] == []
        pending = await scheduler.valid_pending("alice", CONVERSATION, 5)
        confirmations = await asyncio.gather(
            *(
                scheduler.execute(
                    "library schedule confirm",
                    "alice",
                    CONVERSATION,
                    5 + i,
                    confirmation_token=pending["token"],
                )
                for i in range(2)
            )
        )
        assert sum(value["success"] for value in confirmations) == 1
        assert (
            len(
                (
                    await scheduler.execute(
                        "library schedule list", "alice", CONVERSATION, 7
                    )
                )["jobs"]
            )
            == 1
        )
        assert not (
            await scheduler.execute(
                "library schedule confirm --id anything", "alice", CONVERSATION, 8
            )
        )["success"]
        await scheduler.shutdown()

    asyncio.run(run())


def test_school_wait_does_not_hold_ledger_lock_and_shutdown_drains(
    core, monkeypatch, tmp_path
):
    import threading
    from henu_plugin.library_schedule import LibraryScheduler

    entered = threading.Event()
    release = threading.Event()
    clock = [dt.datetime.fromisoformat("2026-10-10T06:59:00+08:00")]

    def school(**params):
        entered.set()
        assert release.wait(5)
        return {"success": True}

    monkeypatch.setattr("mcp_server.library_reserve", school)

    async def run():
        plugin = Plugin(tmp_path)
        scheduler = LibraryScheduler(plugin, clock=lambda: clock[0])
        await scheduler.start()
        for query, command in [(1, COMMAND), (3, COMMAND.replace("201", "202"))]:
            await scheduler.execute(command, "alice", CONVERSATION, query)
            pending = await scheduler.valid_pending("alice", CONVERSATION, query + 1)
            await scheduler.execute(
                "library schedule confirm",
                "alice",
                CONVERSATION,
                query + 1,
                confirmation_token=pending["token"],
            )
        jobs = (
            await scheduler.execute("library schedule list", "alice", CONVERSATION, 5)
        )["jobs"]
        clock[0] += dt.timedelta(seconds=60)
        executing = asyncio.create_task(scheduler.run_due())
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            current = await asyncio.wait_for(
                scheduler.execute("library schedule list", "alice", CONVERSATION, 6),
                0.5,
            )
            assert current["jobs"][0]["status"] == "running"
            cancel = await asyncio.wait_for(
                scheduler.execute(
                    "library schedule cancel --id " + jobs[1]["id"],
                    "alice",
                    CONVERSATION,
                    7,
                ),
                0.5,
            )
            assert cancel["success"]
            shutting_down = asyncio.create_task(scheduler.shutdown())
            await asyncio.sleep(0.02)
            assert not shutting_down.done()
        finally:
            release.set()
        await executing
        await shutting_down
        statuses = (
            await scheduler.execute("library schedule list", "alice", CONVERSATION, 8)
        )["jobs"]
        assert [value["status"] for value in statuses] == ["succeeded", "cancelled"]

    asyncio.run(run())


def test_plugin_initialize_shutdown_and_reinitialize_owns_one_worker(core, tmp_path):
    from main import HenuAssistantPlugin

    async def run():
        plugin = HenuAssistantPlugin()
        storage = {}

        async def read(key):
            if key not in storage:
                raise KeyError(key)
            return storage[key]

        async def write(key, raw):
            storage[key] = raw

        plugin.get_plugin_storage = read
        plugin.set_plugin_storage = write
        await plugin.initialize()
        await plugin.initialize()  # Reload this instance waits for its previous worker.
        await plugin.shutdown()
        assert storage == {}
        await plugin.shutdown()

    asyncio.run(run())


def test_model_tool_cannot_submit_confirmation_and_groups_do_not_store(core, tmp_path):
    from components.cli_tools.henu_cli_safe import HenuCliSafe
    from henu_plugin.library_schedule import LibraryScheduler
    from henu_plugin.confirmation_shortcut import ConfirmationShortcut
    from langbot_plugin.api.entities.builtin.provider.session import (
        Session,
        LauncherTypes,
    )

    async def run():
        plugin = Plugin(tmp_path)
        clock = [dt.datetime.fromisoformat("2026-10-10T06:59:00+08:00")]
        scheduler = LibraryScheduler(plugin, clock=lambda: clock[0])
        plugin._henu_library_scheduler = scheduler
        await scheduler.start()
        tool = HenuCliSafe()
        tool.plugin = plugin
        session = Session(
            sender_id="alice", launcher_id="alice", launcher_type=LauncherTypes.PERSON
        )
        preview = await tool.call({"command": COMMAND}, session, 1)
        assert preview["confirmation_required"]
        pending = await scheduler.valid_pending("alice", CONVERSATION, 2)
        denied = await tool.call(
            {
                "command": "library schedule confirm " + pending["token"],
                "confirmation_token": pending["token"],
            },
            session,
            2,
        )
        assert not denied["success"]
        assert (await tool.call({"command": "library schedule list"}, session, 3))[
            "jobs"
        ] == []
        group = context(COMMAND, query=4, kind="group")
        await scheduler.handle(group, is_group=True)
        group.prevent_default.assert_called_once()
        group.prevent_postorder.assert_called_once()
        assert "本人私聊" in str(group.reply.call_args)
        clock[0] += dt.timedelta(seconds=301)
        shortcut = ConfirmationShortcut(SimpleNamespace(plugin=plugin))
        await shortcut.handle(context("确认定时预约", query=5))
        assert (await tool.call({"command": "library schedule list"}, session, 6))[
            "jobs"
        ] == []
        await scheduler.shutdown()

    asyncio.run(run())


def test_concurrent_lifecycle_start_rejects_second_owner(core, tmp_path):
    from henu_plugin.library_schedule import LibraryScheduler, KEY

    async def run():
        plugin = Plugin(tmp_path)
        read = plugin.get_plugin_storage

        async def slow_sdk(key):
            if key == KEY:
                await asyncio.sleep(0.02)
            return await read(key)

        plugin.get_plugin_storage = slow_sdk
        first = LibraryScheduler(plugin)
        second = LibraryScheduler(plugin)
        starts = await asyncio.gather(
            first.start(), second.start(), return_exceptions=True
        )
        assert sum(isinstance(value, RuntimeError) for value in starts) == 1
        await first.shutdown()
        await second.shutdown()

    asyncio.run(run())


@pytest.mark.parametrize("change", ["revoked", "missed", "stopped"])
def test_busy_runtime_cannot_execute_later_with_stale_qualification(
    core, monkeypatch, tmp_path, change
):
    import threading
    from henu_plugin.library_schedule import LibraryScheduler
    from henu_plugin.service import _RUNTIME_STATE_LOCK

    clock = [dt.datetime.fromisoformat("2026-10-10T06:59:00+08:00")]
    school = Mock(return_value={"success": True})
    monkeypatch.setattr("mcp_server.library_reserve", school)
    held = threading.Event()
    release = threading.Event()

    def occupy_runtime():
        with _RUNTIME_STATE_LOCK:
            held.set()
            release.wait(5)

    async def run():
        plugin = Plugin(tmp_path)
        scheduler = LibraryScheduler(plugin, clock=lambda: clock[0])
        await scheduler.start()
        await scheduler.execute(COMMAND, "alice", CONVERSATION, 1)
        pending = await scheduler.valid_pending("alice", CONVERSATION, 2)
        await scheduler.execute(
            "library schedule confirm",
            "alice",
            CONVERSATION,
            2,
            confirmation_token=pending["token"],
        )
        busy = asyncio.create_task(asyncio.to_thread(occupy_runtime))
        assert await asyncio.to_thread(held.wait, 2)
        clock[0] += dt.timedelta(seconds=60)
        executing = asyncio.create_task(scheduler.run_due())
        try:
            # Give qualification a chance to finish and reach the occupied executor.
            await asyncio.sleep(0.05)
            if change == "revoked":
                core[0].update(plan="free", lifetime=False, allowed=False, benefits=[])
            elif change == "missed":
                clock[0] += dt.timedelta(seconds=91)
            else:
                await scheduler.shutdown()
        finally:
            release.set()
        await busy
        await executing
        school.assert_not_called()
        status = (
            await scheduler.execute("library schedule list", "alice", CONVERSATION, 3)
        )["jobs"][0]["status"]
        assert status in {"blocked", "missed"}
        await scheduler.shutdown()

    asyncio.run(run())


@pytest.mark.parametrize("action", ["create", "confirm"])
def test_shutdown_during_membership_wait_refuses_new_persistence(
    core, monkeypatch, tmp_path, action
):
    import threading
    import requests
    from henu_plugin.library_schedule import LibraryScheduler

    entered = threading.Event()
    release = threading.Event()
    armed = [False]
    original = requests.post

    def slow_core(*args, **kwargs):
        if armed[0]:
            entered.set()
            assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(requests, "post", slow_core)

    async def run():
        plugin = Plugin(tmp_path)
        scheduler = LibraryScheduler(
            plugin, clock=lambda: dt.datetime.fromisoformat("2026-10-10T06:59:00+08:00")
        )
        await scheduler.start()
        token = None
        if action == "confirm":
            await scheduler.execute(COMMAND, "alice", CONVERSATION, 1)
            token = (await scheduler.valid_pending("alice", CONVERSATION, 2))["token"]
        before = dict(plugin.storage)
        armed[0] = True
        submitting = asyncio.create_task(
            scheduler.execute(
                COMMAND if action == "create" else "library schedule confirm",
                "alice",
                CONVERSATION,
                2,
                confirmation_token=token,
            )
        )
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            await scheduler.shutdown()
        finally:
            release.set()
        refused = await submitting
        assert (
            not refused["success"] and refused.get("error_code") == "worker_unavailable"
        )
        assert plugin.storage == before

    asyncio.run(run())

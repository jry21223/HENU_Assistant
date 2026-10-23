"""Exercise the real reminder/Storage/service path with only school I/O replaced."""
import asyncio
import json
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from henu_mcp.tools import server_impl
from henu_plugin import hardened_service, schedule_reminders
from henu_plugin.hardened_service import HardenedHenuPluginService
from henu_plugin.schedule_reminders import KEY, ScheduleReminders
from henu_plugin.service import get_current_user_paths
from henu_plugin.storage_adapter import PluginStorageAdapter

ZONE = ZoneInfo("Asia/Shanghai")
PERIODS = {"1": {"start": "08:00", "end": "08:45"},
           "2": {"start": "08:55", "end": "09:40"}}
ROW = {"course": "计算机网络", "period": "第1-2节", "time": "1-18周 二[1-2]",
       "location": "十号楼101"}


def snapshot(now, sid="offline-student"):
    return {"generated_at": now.isoformat(), "_subject_student_id": sid,
            "schedule": {"星期二": [ROW]}}


class Plugin:
    def __init__(self, base, now):
        self.storage = {
            KEY: json.dumps({"alice": {"enabled": True, "bot": "offline-bot", "journal": {}}}).encode(),
            "user:alice:profile": json.dumps({"student_id": "offline-student", "password": "offline-only"}).encode(),
            "user:alice:schedule": json.dumps(snapshot(now - timedelta(days=3))).encode(),
            "shared:period_time": json.dumps(PERIODS).encode(),
        }
        self.service = HardenedHenuPluginService(base)
        self.calls = []
        self.messages = []
        self.outcome = "success"
        self.sync_duration = timedelta(0)
        self.sync_started = threading.Event()
        self.sync_release = None

    async def get_plugin_storage(self, key):
        return self.storage.get(key, b"{}")

    async def set_plugin_storage(self, key, value):
        self.storage[key] = value

    async def send_message(self, *args):
        self.messages.append(args)


@pytest.fixture
def reminder_env(tmp_path, monkeypatch):
    import requests
    class Clock:
        now = datetime(2026, 10, 20, 7, 45, tzinfo=ZONE)

    clock = Clock()
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock.now.astimezone(tz) if tz else clock.now.replace(tzinfo=None)

    monkeypatch.setattr(schedule_reminders, "datetime", FixedDatetime)
    monkeypatch.setattr(server_impl, "_now_dt", lambda timezone: clock.now)
    if hasattr(hardened_service, "time"):
        monkeypatch.setattr(hardened_service.time, "time", lambda: clock.now.timestamp())
    monkeypatch.setattr(PluginStorageAdapter, "_shared_temp_dir", tmp_path / "staging")
    PluginStorageAdapter._user_locks.clear()
    PluginStorageAdapter._shared_locks.clear()

    def no_network(*args, **kwargs):
        raise AssertionError("School/other network calls are forbidden in reminder tests")
    monkeypatch.setattr(requests.sessions.Session, "request", no_network)

    def school_sync(self, params):
        plugin.calls.append(clock.now)
        plugin.sync_started.set()
        if plugin.sync_release is not None:
            assert plugin.sync_release.wait(timeout=2)
        paths = get_current_user_paths()
        assert paths is not None
        if plugin.outcome == "locked":
            return {"success": False, "error_code": "account_locked"}
        if plugin.outcome == "timeout":
            raise TimeoutError("offline ambiguous authentication")
        clock.now += plugin.sync_duration
        data = snapshot(clock.now)
        paths.schedule_file.write_text(json.dumps(data))
        (paths.output_dir / "schedule_clean_latest.json").write_text(json.dumps(data))
        paths.xk_cookie_file.write_text('{"JSESSIONID":"offline-new-cookie"}')
        return {"success": True}
    monkeypatch.setattr(HardenedHenuPluginService, "_sync_schedule", school_sync)
    plugin = Plugin(tmp_path, clock.now)
    return plugin, clock


async def saved_files(plugin, sender="alice"):
    adapter = PluginStorageAdapter(plugin, sender)
    try:
        paths = await adapter.load_all()
        return json.loads(paths.schedule_file.read_text()), json.loads(paths.xk_cookie_file.read_text())
    finally:
        await adapter.abort()


async def scan(plugin, clock):
    reminder = ScheduleReminders(plugin)
    await reminder.refresh_tick(clock.now)
    await reminder.tick(clock.now)


def test_ticks_and_restart_refresh_once_and_persist_schedule_and_cookie(reminder_env):
    plugin, clock = reminder_env
    async def run():
        await scan(plugin, clock)
        clock.now += timedelta(seconds=30)
        await scan(plugin, clock)
        clock.now += timedelta(seconds=30)
        await scan(plugin, clock)
        assert len(plugin.calls) == 1
        saved, cookies = await saved_files(plugin)
        assert saved["generated_at"] == "2026-10-20T07:45:00+08:00"
        assert cookies == {"JSESSIONID": "offline-new-cookie"}
        assert len(plugin.messages) == 1
        assert "计算机网络" in str(plugin.messages[0])
    asyncio.run(run())


@pytest.mark.parametrize("outcome", ["locked", "timeout"])
def test_failed_authentication_pauses_across_restarts_and_days(reminder_env, outcome):
    plugin, clock = reminder_env
    plugin.outcome = outcome
    async def run():
        before, _ = await saved_files(plugin)
        await scan(plugin, clock)
        clock.now += timedelta(seconds=30)
        await scan(plugin, clock)
        clock.now += timedelta(days=2)
        await scan(plugin, clock)
        assert len(plugin.calls) == 1
        after, cookies = await saved_files(plugin)
        assert before == after and cookies == {}
        assert len(plugin.messages) == 1
    asyncio.run(run())


def test_refresh_waits_full_day_even_after_restart(reminder_env):
    plugin, clock = reminder_env
    async def run():
        await scan(plugin, clock)
        clock.now += timedelta(hours=23, minutes=59)
        await scan(plugin, clock)
        assert len(plugin.calls) == 1
        clock.now += timedelta(minutes=2)
        await scan(plugin, clock)
        assert len(plugin.calls) == 2
    asyncio.run(run())


def test_same_school_account_on_two_chat_identities_shares_daily_limit(reminder_env):
    plugin, clock = reminder_env
    plugin.storage[KEY] = json.dumps({name: {"enabled": True, "bot": "bot", "journal": {}}
                                     for name in ["alice", "bob"]}).encode()
    for suffix in ["profile", "schedule"]:
        plugin.storage[f"user:bob:{suffix}"] = plugin.storage[f"user:alice:{suffix}"]
    async def run():
        await asyncio.gather(ScheduleReminders(plugin).refresh_tick(clock.now),
                             ScheduleReminders(plugin).refresh_tick(clock.now))
        assert len(plugin.calls) == 1
    asyncio.run(run())


def test_manual_success_resumes_paused_refresh_without_immediate_auth(reminder_env):
    plugin, clock = reminder_env
    plugin.outcome = "locked"
    async def run():
        await scan(plugin, clock)
        clock.now += timedelta(minutes=10)
        adapter = PluginStorageAdapter(plugin, "alice")
        paths = await adapter.load_all()
        paths.schedule_file.write_text(json.dumps(snapshot(clock.now)))
        await adapter.save_all()
        plugin.outcome = "success"
        await scan(plugin, clock)
        assert len(plugin.calls) == 1
        clock.now += timedelta(days=1, seconds=1)
        await scan(plugin, clock)
        assert len(plugin.calls) == 2
    asyncio.run(run())


def test_failed_storage_commit_never_retries_school_auth(reminder_env):
    plugin, clock = reminder_env
    original_write = plugin.set_plugin_storage
    async def broken_user_commit(key, value):
        if key.startswith("user:"):
            raise OSError("offline commit failure")
        await original_write(key, value)
    plugin.set_plugin_storage = broken_user_commit
    async def run():
        before, _ = await saved_files(plugin)
        await scan(plugin, clock)
        plugin.set_plugin_storage = original_write
        clock.now += timedelta(days=2)
        await scan(plugin, clock)
        assert len(plugin.calls) == 1
        after, _ = await saved_files(plugin)
        assert before == after
    asyncio.run(run())


def test_disabled_reminder_never_contacts_school(reminder_env):
    plugin, clock = reminder_env
    plugin.storage[KEY] = b'{"alice":{"enabled":false}}'
    asyncio.run(ScheduleReminders(plugin).tick(clock.now))
    assert plugin.calls == [] and plugin.messages == []


def test_applied_auto_snapshot_with_lost_response_and_failed_rollback_stays_paused(reminder_env):
    plugin, clock = reminder_env
    plugin.sync_duration = timedelta(seconds=2)
    original_write = plugin.set_plugin_storage
    lost = False
    async def lost_response(key, value):
        nonlocal lost
        if lost and key.startswith("user:"):
            raise OSError("rollback unavailable")
        if key.startswith("user:") and key.endswith((":schedule", ":snapshot")):
            await original_write(key, value)
            lost = True
            raise OSError("write applied but response lost")
        await original_write(key, value)
    plugin.set_plugin_storage = lost_response
    async def run():
        await scan(plugin, clock)
        plugin.set_plugin_storage = original_write
        clock.now += timedelta(seconds=30)
        await scan(plugin, clock)
        clock.now += timedelta(days=2)
        await scan(plugin, clock)
        assert lost
        assert len(plugin.calls) == 1
    asyncio.run(run())


def test_reminders_read_saved_schedule_while_writer_holds_user_lock(reminder_env):
    plugin, clock = reminder_env
    async def run():
        writer = PluginStorageAdapter(plugin, "alice")
        await writer.load_all()
        try:
            await asyncio.wait_for(ScheduleReminders(plugin).tick(clock.now), timeout=0.25)
            assert len(plugin.messages) == 1
        finally:
            await writer.abort()
    asyncio.run(run())


def test_slow_school_refresh_does_not_block_local_reminder_delivery(reminder_env):
    plugin, clock = reminder_env
    plugin.sync_release = threading.Event()
    async def run():
        reminder = ScheduleReminders(plugin)
        background = asyncio.create_task(reminder.refresh_tick(clock.now))
        try:
            assert await asyncio.to_thread(plugin.sync_started.wait, 1)
            await asyncio.wait_for(reminder.tick(clock.now), timeout=0.25)
            assert len(plugin.messages) == 1
            assert not background.done()
        finally:
            plugin.sync_release.set()
            await background
    asyncio.run(run())


def test_manual_recovery_is_recognized_after_more_than_a_day_offline(reminder_env):
    plugin, clock = reminder_env
    plugin.outcome = "locked"
    async def run():
        await scan(plugin, clock)
        clock.now += timedelta(minutes=10)
        adapter = PluginStorageAdapter(plugin, "alice")
        paths = await adapter.load_all()
        paths.schedule_file.write_text(json.dumps(snapshot(clock.now)))
        await adapter.save_all()
        clock.now += timedelta(days=2)
        plugin.outcome = "success"
        await scan(plugin, clock)
        await scan(plugin, clock)
        assert len(plugin.calls) == 2
    asyncio.run(run())


def test_read_only_storage_cannot_commit(reminder_env):
    plugin, _ = reminder_env
    async def run():
        from henu_plugin.storage_adapter import StorageAdapterError
        adapter = PluginStorageAdapter(plugin, "alice", read_only=True)
        try:
            await adapter.load_all()
            with pytest.raises(StorageAdapterError):
                await adapter.save_all()
        finally:
            await adapter.abort()
    asyncio.run(run())


@pytest.mark.skipif(not hasattr(PluginStorageAdapter, "_encode_snapshot"),
                    reason="Atomic snapshot recovery is a production Storage overlay")
def test_read_only_incomplete_snapshot_load_never_repairs_or_writes(reminder_env):
    plugin, _ = reminder_env
    async def run():
        writer = PluginStorageAdapter(plugin, "alice")
        paths = await writer.load_all()
        paths.xk_cookie_file.write_text('{"JSESSIONID":"offline-recovery"}')
        await writer.save_all()
        key = "user:alice:snapshot_v2"
        raw = json.loads(await plugin.get_plugin_storage(key))
        raw["legacy_mirror_complete"] = False
        plugin.storage[key] = json.dumps(raw).encode()
        before = dict(plugin.storage)
        async def no_writes(key, value):
            raise AssertionError("Read-only materialization cannot repair Storage")
        plugin.set_plugin_storage = no_writes
        reader = PluginStorageAdapter(plugin, "alice", read_only=True)
        try:
            saved = await reader.load_all()
            assert json.loads(saved.xk_cookie_file.read_text()) == {"JSESSIONID": "offline-recovery"}
            assert plugin.storage == before
        finally:
            await reader.abort()
    asyncio.run(run())

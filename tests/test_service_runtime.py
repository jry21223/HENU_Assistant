from __future__ import annotations

import asyncio
import base64
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from henu_plugin.hardened_service import HardenedHenuPluginService
from henu_plugin import service as service_module
from henu_plugin.service import get_current_user_paths, set_current_user_paths
from henu_plugin.storage_adapter import UserStoragePaths


def _user_paths(root: Path) -> UserStoragePaths:
    user_root = root / "user"
    output_dir = user_root / "output"
    shared_dir = root / "shared"
    return UserStoragePaths(
        user_root=user_root,
        profile_file=user_root / "profile.json",
        xk_cookie_file=user_root / "xk_cookies.json",
        library_cookie_file=user_root / "library_cookies.json",
        seminar_signin_task_file=user_root / "seminar_signin_tasks.json",
        schedule_file=user_root / "schedule_clean_latest.json",
        yunfz_token_file=user_root / "yunfz_token.json",
        cas_cookie_file=user_root / "cas_cookies.json",
        course_monitor_config_file=output_dir / "course_monitor_config.json",
        course_monitor_state_file=output_dir / "course_monitor_state.json",
        xiqueer_request_file=user_root / "xiqueer_period_time_request.json",
        output_dir=output_dir,
        shared_data_dir=shared_dir,
    )


def _session(user_id: str):
    return SimpleNamespace(
        sender_id=user_id,
        launcher_id=f"private-{user_id}",
        launcher_type=SimpleNamespace(value="person"),
    )


def _snapshot_file(storage: dict[str, bytes], user_id: str, file_key: str) -> bytes:
    snapshot = json.loads(storage[f"user:{user_id}:snapshot_v2"].decode("utf-8"))
    return base64.b64decode(
        snapshot["files"][f"user:{user_id}:{file_key}"],
        validate=True,
    )


async def _run_for_user(
    service: HardenedHenuPluginService,
    paths: UserStoragePaths,
    tool_name: str,
    params: dict[str, object],
    user_id: str,
):
    set_current_user_paths(paths)
    try:
        return await service.run_tool_async(
            tool_name,
            params,
            _session(user_id),
            1,
        )
    finally:
        set_current_user_paths(None)


def test_real_run_tool_activates_current_storage_without_mcp_worker_compat(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from campus_core import storage_paths
    from henu_mcp import api as henu_api
    from henu_mcp.tools import server_impl

    paths = _user_paths(tmp_path / "request")
    observed: dict[str, Path] = {}

    def system_status(*, timezone: str) -> dict[str, object]:
        observed["profile"] = server_impl.PROFILE_FILE
        observed["shared"] = storage_paths.get_shared_data_dir()
        observed["xiqueer"] = server_impl.XIQUEER_REQUEST_FILE
        return {"success": True, "timezone": timezone}

    monkeypatch.setattr(henu_api, "system_status", system_status)
    service = HardenedHenuPluginService(tmp_path / "plugin")
    session = SimpleNamespace(
        sender_id="10001",
        launcher_id="private-1",
        launcher_type=SimpleNamespace(value="person"),
    )

    set_current_user_paths(paths)
    try:
        result = service.run_tool("system_status", {}, session, 1)
    finally:
        set_current_user_paths(None)

    assert result["success"] is True, result
    assert observed == {
        "profile": paths.profile_file,
        "shared": (tmp_path / "plugin" / "data" / "shared"),
        "xiqueer": paths.xiqueer_request_file,
    }


def test_async_cli_monitor_releases_runtime_lock_between_users(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from henu_mcp import api as henu_api

    first_round_finished = threading.Event()
    round_arguments: list[tuple[int, int]] = []

    def monitor_round(
        *,
        config_json: str,
        max_checks: int,
        duration_seconds: int,
        send_notifications: bool,
    ) -> dict[str, object]:
        del config_json, send_notifications
        round_arguments.append((max_checks, duration_seconds))
        first_round_finished.set()
        return {
            "success": True,
            "checks": 1,
            "interval_seconds": 60,
            "results": [{"success": True, "round": len(round_arguments)}],
        }

    monkeypatch.setattr(henu_api, "course_monitor_run", monitor_round)
    monkeypatch.setattr(
        henu_api,
        "system_status",
        lambda *, timezone: {"success": True, "timezone": timezone},
    )
    service = HardenedHenuPluginService(tmp_path / "plugin")

    async def scenario() -> None:
        monitor_task = asyncio.create_task(
            _run_for_user(
                service,
                _user_paths(tmp_path / "user-a"),
                "henu_cli",
                {
                    "command": (
                        "course monitor run --max-checks 0 "
                        "--duration-seconds 180"
                    )
                },
                "10001",
            )
        )
        assert await asyncio.to_thread(first_round_finished.wait, 1)

        probe = await asyncio.wait_for(
            _run_for_user(
                service,
                _user_paths(tmp_path / "user-b"),
                "system_status",
                {},
                "10002",
            ),
            timeout=1,
        )
        assert probe["success"] is True

        monitor_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await monitor_task

    asyncio.run(scenario())

    assert round_arguments == [(1, 0)]


def test_async_monitor_cancellation_releases_runtime_lock(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from henu_mcp import api as henu_api

    first_round_started = threading.Event()
    release_first_round = threading.Event()
    calls = 0

    def monitor_round(**_kwargs) -> dict[str, object]:
        nonlocal calls
        calls += 1
        first_round_started.set()
        release_first_round.wait(timeout=2)
        return {
            "success": True,
            "checks": 1,
            "interval_seconds": 60,
            "results": [{"success": True}],
        }

    monkeypatch.setattr(henu_api, "course_monitor_run", monitor_round)
    monkeypatch.setattr(
        henu_api,
        "system_status",
        lambda *, timezone: {"success": True, "timezone": timezone},
    )
    service = HardenedHenuPluginService(tmp_path / "plugin")
    paths = _user_paths(tmp_path / "user")

    def runtime_lock_is_available() -> bool:
        acquired = service_module._RUNTIME_STATE_LOCK.acquire(timeout=0.5)
        if acquired:
            service_module._RUNTIME_STATE_LOCK.release()
        return acquired

    async def scenario() -> None:
        monitor_task = asyncio.create_task(
            _run_for_user(
                service,
                paths,
                "course_monitor_run",
                {"max_checks": 0, "duration_seconds": 180},
                "10001",
            )
        )
        assert await asyncio.to_thread(first_round_started.wait, 1)
        monitor_task.cancel()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not monitor_task.done()
        monitor_task.cancel()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        cancellation_waited_for_round = not monitor_task.done()
        release_first_round.set()
        with pytest.raises(asyncio.CancelledError):
            await monitor_task

        assert cancellation_waited_for_round
        assert await asyncio.to_thread(runtime_lock_is_available)
        probe = await asyncio.wait_for(
            _run_for_user(service, paths, "system_status", {}, "10001"),
            timeout=1,
        )
        assert probe["success"] is True

    asyncio.run(scenario())

    assert calls == 1


def test_hardened_async_monitor_validates_original_limits_before_splitting(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from henu_mcp import api as henu_api

    calls = 0

    def monitor_round(**_kwargs) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"success": True, "checks": 1, "interval_seconds": 60}

    monkeypatch.setattr(henu_api, "course_monitor_run", monitor_round)
    service = HardenedHenuPluginService(tmp_path / "plugin")
    paths = _user_paths(tmp_path / "user")

    async def scenario() -> None:
        too_many_checks = await _run_for_user(
            service,
            paths,
            "course_monitor_run",
            {"max_checks": 4, "duration_seconds": 0},
            "10001",
        )
        too_long_cli = await _run_for_user(
            service,
            paths,
            "henu_cli",
            {"command": "course monitor run --duration-seconds 181"},
            "10001",
        )
        assert too_many_checks["error_code"] == "limit_exceeded"
        assert too_long_cli["error_code"] == "limit_exceeded"

    asyncio.run(scenario())

    assert calls == 0


def test_async_monitor_runs_each_check_in_a_separate_round(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from henu_mcp import api as henu_api

    round_arguments: list[tuple[int, int]] = []

    def monitor_round(**kwargs) -> dict[str, object]:
        round_arguments.append(
            (int(kwargs["max_checks"]), int(kwargs["duration_seconds"]))
        )
        return {
            "success": True,
            "checks": 1,
            "interval_seconds": 0,
            "results": [{"success": True, "round": len(round_arguments)}],
        }

    monkeypatch.setattr(henu_api, "course_monitor_run", monitor_round)
    monkeypatch.setattr(service_module, "_COURSE_MONITOR_MIN_INTERVAL_SECONDS", 0)
    service = HardenedHenuPluginService(tmp_path / "plugin")

    result = asyncio.run(
        _run_for_user(
            service,
            _user_paths(tmp_path / "user"),
            "course_monitor_run",
            {"max_checks": 2, "duration_seconds": 0},
            "10001",
        )
    )

    assert round_arguments == [(1, 0), (1, 0)]
    assert result["success"] is True
    assert result["checks"] == 2
    assert [item["round"] for item in result["results"]] == [1, 2]


def test_async_cli_monitor_keeps_hardened_cli_finalization(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from henu_mcp import api as henu_api

    def monitor_round(**_kwargs) -> dict[str, object]:
        return {
            "success": True,
            "msg": "选课余量监控运行完成；未执行任何选课提交。",
            "checks": 1,
            "interval_seconds": 60,
            "results": [{"example_date": "2026-03-30"}],
        }

    monkeypatch.setattr(henu_api, "course_monitor_run", monitor_round)
    service = HardenedHenuPluginService(tmp_path / "plugin")
    paths = _user_paths(tmp_path / "user")
    params = {"command": "course monitor run --max-checks 1"}

    set_current_user_paths(paths)
    try:
        sync_result = service.run_tool("henu_cli", params, _session("10001"), 1)
    finally:
        set_current_user_paths(None)
    async_result = asyncio.run(
        _run_for_user(service, paths, "henu_cli", params, "10001")
    )

    assert async_result == sync_result
    assert async_result["results"] == [{"example_date": "YYYY-MM-DD"}]


def test_base_component_calls_service_async_entrypoint() -> None:
    from components.cli_tools.base import BaseHenuTool

    calls: list[tuple[str, dict[str, object]]] = []

    class AsyncOnlyService:
        async def run_tool_async(
            self,
            tool_name,
            params,
            session,
            query_id,
            identity_hint,
        ):
            del session, query_id, identity_hint
            calls.append((tool_name, params))
            return {"success": True, "msg": "async entrypoint"}

        def run_tool(self, *_args, **_kwargs):
            raise AssertionError("BaseHenuTool must not call the sync entrypoint")

    class FakePlugin:
        service = AsyncOnlyService()
        plugin_runtime_handler = None

        async def get_plugin_storage(self, _storage_key):
            return b"{}"

        async def set_plugin_storage(self, _storage_key, _payload):
            raise AssertionError("unchanged storage must not be written")

    tool = BaseHenuTool()
    tool.tool_name = "system_status"
    tool.plugin = FakePlugin()

    result = asyncio.run(tool.call({}, _session("10001"), 1))

    assert result["success"] is True
    assert calls == [("system_status", {})]


def test_same_user_pure_tool_bypasses_storage_while_monitor_sleeps(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from components.cli_tools.base import BaseHenuTool
    from henu_mcp import api as henu_api
    from henu_plugin.storage_adapter import PluginStorageAdapter

    monitor_sleep_started = asyncio.Event()
    release_monitor_sleep = asyncio.Event()
    real_sleep = asyncio.sleep

    def monitor_round(**_kwargs) -> dict[str, object]:
        return {
            "success": True,
            "checks": 1,
            "interval_seconds": 60,
            "results": [],
        }

    def pure_plan(**_kwargs) -> dict[str, object]:
        return {"success": True, "plans": ["responsive"]}

    async def controlled_sleep(delay: float) -> None:
        if delay >= 60:
            monitor_sleep_started.set()
            await release_monitor_sleep.wait()
            return
        await real_sleep(delay)

    class FakePlugin:
        plugin_runtime_handler = None

        def __init__(self) -> None:
            self.storage: dict[str, bytes] = {}
            self.service = HardenedHenuPluginService(tmp_path / "plugin")

        async def get_plugin_storage(self, storage_key):
            return self.storage.get(storage_key, b"")

        async def set_plugin_storage(self, storage_key, payload):
            self.storage[storage_key] = payload

    monkeypatch.setattr(henu_api, "course_monitor_run", monitor_round)
    monkeypatch.setattr(henu_api, "course_selection_plan", pure_plan)
    monkeypatch.setattr(service_module.asyncio, "sleep", controlled_sleep)
    monkeypatch.setattr(PluginStorageAdapter, "_shared_temp_dir", tmp_path / "staging")
    PluginStorageAdapter._user_locks.clear()
    PluginStorageAdapter._shared_locks.clear()

    plugin = FakePlugin()
    monitor_tool = BaseHenuTool()
    monitor_tool.tool_name = "course_monitor_run"
    monitor_tool.plugin = plugin
    pure_tool = BaseHenuTool()
    pure_tool.tool_name = "course_selection_plan"
    pure_tool.plugin = plugin

    async def scenario() -> dict[str, object] | None:
        monitor = asyncio.create_task(
            monitor_tool.call({"max_checks": 2}, _session("10001"), 1)
        )
        await asyncio.wait_for(monitor_sleep_started.wait(), timeout=1)
        result: dict[str, object] | None = None
        try:
            result = await asyncio.wait_for(
                pure_tool.call({}, _session("10001"), 2),
                timeout=0.2,
            )
        except TimeoutError:
            pass
        finally:
            release_monitor_sleep.set()
            await monitor
        return result

    try:
        result = asyncio.run(scenario())
    finally:
        PluginStorageAdapter._user_locks.clear()
        PluginStorageAdapter._shared_locks.clear()

    assert result is not None
    assert result["success"] is True
    assert result["plans"] == ["responsive"]


def test_base_component_aborts_storage_when_preflight_raises(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from components.cli_tools.base import BaseHenuTool
    from henu_plugin.storage_adapter import PluginStorageAdapter

    class FakePlugin:
        service = object()
        plugin_runtime_handler = None

        async def get_plugin_storage(self, _storage_key):
            return b"{}"

        async def set_plugin_storage(self, _storage_key, _payload):
            raise AssertionError("aborted preflight must not persist storage")

    class FailingPreflightTool(BaseHenuTool):
        tool_name = "schedule_query"

        async def _prime_runtime_context_query_var(self, _query_id):
            raise RuntimeError("preflight failed")

    staging = tmp_path / "staging"
    monkeypatch.setattr(PluginStorageAdapter, "_shared_temp_dir", staging)
    PluginStorageAdapter._user_locks.clear()
    PluginStorageAdapter._shared_locks.clear()
    plugin = FakePlugin()
    tool = FailingPreflightTool()
    tool.plugin = plugin

    async def scenario() -> None:
        with pytest.raises(RuntimeError, match="preflight failed"):
            await tool.call({}, _session("10001"), 1)
        assert get_current_user_paths() is None
        assert list(staging.iterdir()) == []

        retry = PluginStorageAdapter(plugin, "10001")
        await asyncio.wait_for(retry.load_all(), timeout=1)
        await retry.save_all()

    asyncio.run(scenario())


def test_base_component_aborts_storage_when_preflight_is_cancelled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from components.cli_tools.base import BaseHenuTool
    from henu_plugin.storage_adapter import PluginStorageAdapter

    class FakePlugin:
        service = object()
        plugin_runtime_handler = None

        async def get_plugin_storage(self, _storage_key):
            return b"{}"

        async def set_plugin_storage(self, _storage_key, _payload):
            raise AssertionError("cancelled preflight must not persist storage")

    class BlockingPreflightTool(BaseHenuTool):
        tool_name = "schedule_query"

        def __init__(self) -> None:
            self.started = asyncio.Event()

        async def _prime_runtime_context_query_var(self, _query_id):
            self.started.set()
            await asyncio.Event().wait()

    staging = tmp_path / "staging"
    monkeypatch.setattr(PluginStorageAdapter, "_shared_temp_dir", staging)
    PluginStorageAdapter._user_locks.clear()
    PluginStorageAdapter._shared_locks.clear()
    plugin = FakePlugin()

    async def scenario() -> None:
        tool = BlockingPreflightTool()
        tool.plugin = plugin
        first = asyncio.create_task(tool.call({}, _session("10001"), 1))
        await asyncio.wait_for(tool.started.wait(), timeout=1)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first

        assert get_current_user_paths() is None
        assert list(staging.iterdir()) == []
        retry = PluginStorageAdapter(plugin, "10001")
        await asyncio.wait_for(retry.load_all(), timeout=1)
        await retry.save_all()

    asyncio.run(scenario())


def test_base_component_cancellation_joins_generic_worker_before_storage_cleanup(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from components.cli_tools.base import BaseHenuTool
    from henu_mcp import api as henu_api
    from henu_mcp.tools import server_impl
    from henu_plugin.storage_adapter import PluginStorageAdapter

    worker_started = threading.Event()
    release_worker = threading.Event()

    def delayed_status(*, timezone: str) -> dict[str, object]:
        worker_started.set()
        release_worker.wait(timeout=2)
        server_impl.PROFILE_FILE.write_text('{"late_profile":true}', encoding="utf-8")
        server_impl.LIBRARY_COOKIE_FILE.write_text(
            '{"late_cookie":true}',
            encoding="utf-8",
        )
        return {"success": True, "timezone": timezone}

    class FakePlugin:
        plugin_runtime_handler = None

        def __init__(self) -> None:
            self.storage: dict[str, bytes] = {}
            self.service = HardenedHenuPluginService(tmp_path / "plugin")

        async def get_plugin_storage(self, storage_key):
            return self.storage.get(storage_key, b"")

        async def set_plugin_storage(self, storage_key, payload):
            self.storage[storage_key] = payload

    monkeypatch.setattr(henu_api, "system_status", delayed_status)
    monkeypatch.setattr(PluginStorageAdapter, "_shared_temp_dir", tmp_path / "staging")
    PluginStorageAdapter._user_locks.clear()
    PluginStorageAdapter._shared_locks.clear()
    plugin = FakePlugin()
    tool = BaseHenuTool()
    tool.tool_name = "system_status"
    tool.plugin = plugin

    async def scenario() -> None:
        first_task = asyncio.create_task(tool.call({}, _session("10001"), 1))
        assert await asyncio.to_thread(worker_started.wait, 1)

        first_task.cancel()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        first_task.cancel()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        cancellation_waited_for_worker = not first_task.done()

        second_adapter = PluginStorageAdapter(plugin, "10001")
        second_load = asyncio.create_task(second_adapter.load_all())
        await asyncio.sleep(0)
        same_user_lock_still_held = not second_load.done()

        release_worker.set()
        with pytest.raises(asyncio.CancelledError):
            await first_task
        second_paths = await asyncio.wait_for(second_load, timeout=1)
        assert second_paths.profile_file.read_text(encoding="utf-8") == (
            '{"late_profile":true}'
        )
        assert second_paths.library_cookie_file.read_text(encoding="utf-8") == (
            '{"late_cookie":true}'
        )
        await second_adapter.save_all()

        assert cancellation_waited_for_worker
        assert same_user_lock_still_held

    try:
        asyncio.run(scenario())
    finally:
        release_worker.set()
        PluginStorageAdapter._user_locks.clear()
        PluginStorageAdapter._shared_locks.clear()

    assert _snapshot_file(plugin.storage, "10001", "profile") == b'{"late_profile":true}'
    assert _snapshot_file(plugin.storage, "10001", "library_cookie") == b'{"late_cookie":true}'


def test_identity_listener_cancellation_joins_worker_before_storage_cleanup(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from components.event_listener.identity_capture import IdentityCaptureListener
    from henu_plugin.storage_adapter import PluginStorageAdapter

    worker_started = threading.Event()
    release_worker = threading.Event()

    def delayed_profile_migration() -> dict[str, object]:
        paths = get_current_user_paths()
        assert paths is not None
        worker_started.set()
        release_worker.wait(timeout=2)
        paths.profile_file.write_text('{"migrated":true}', encoding="utf-8")
        return {"success": True}

    class FakePlugin:
        def __init__(self) -> None:
            self.storage: dict[str, bytes] = {}

        async def get_plugin_storage(self, storage_key):
            return self.storage.get(storage_key, b"")

        async def set_plugin_storage(self, storage_key, payload):
            self.storage[storage_key] = payload

    monkeypatch.setattr(PluginStorageAdapter, "_shared_temp_dir", tmp_path / "staging")
    PluginStorageAdapter._user_locks.clear()
    PluginStorageAdapter._shared_locks.clear()
    plugin = FakePlugin()
    listener = IdentityCaptureListener()
    listener.plugin = plugin

    async def scenario() -> None:
        first = asyncio.create_task(
            listener._run_with_user_storage(
                _session("10001"),
                {},
                delayed_profile_migration,
            )
        )
        assert await asyncio.to_thread(worker_started.wait, 1)
        first.cancel()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        first.cancel()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        cancellation_waited_for_worker = not first.done()

        second_adapter = PluginStorageAdapter(plugin, "10001")
        second_load = asyncio.create_task(second_adapter.load_all())
        await asyncio.sleep(0)
        same_user_lock_still_held = not second_load.done()

        release_worker.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        second_paths = await asyncio.wait_for(second_load, timeout=1)
        assert second_paths.profile_file.read_text(encoding="utf-8") == (
            '{"migrated":true}'
        )
        await second_adapter.save_all()

        assert cancellation_waited_for_worker
        assert same_user_lock_still_held
        assert get_current_user_paths() is None

    try:
        asyncio.run(scenario())
    finally:
        release_worker.set()
        PluginStorageAdapter._user_locks.clear()
        PluginStorageAdapter._shared_locks.clear()

    assert _snapshot_file(plugin.storage, "10001", "profile") == b'{"migrated":true}'


def test_base_component_cancellation_during_save_finishes_all_storage_writes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from components.cli_tools.base import BaseHenuTool
    from henu_mcp import api as henu_api
    from henu_mcp.tools import server_impl
    from henu_plugin.storage_adapter import PluginStorageAdapter

    def write_two_files(*, timezone: str) -> dict[str, object]:
        server_impl.PROFILE_FILE.write_text('{"profile":true}', encoding="utf-8")
        server_impl.LIBRARY_COOKIE_FILE.write_text(
            '{"cookie":true}',
            encoding="utf-8",
        )
        return {"success": True, "timezone": timezone}

    class BlockingSavePlugin:
        plugin_runtime_handler = None

        def __init__(self) -> None:
            self.storage: dict[str, bytes] = {}
            self.service = HardenedHenuPluginService(tmp_path / "plugin")
            self.cookie_save_started = asyncio.Event()
            self.release_cookie_save = asyncio.Event()

        async def get_plugin_storage(self, storage_key):
            return self.storage.get(storage_key, b"")

        async def set_plugin_storage(self, storage_key, payload):
            if storage_key == "user:10001:snapshot_v2":
                self.cookie_save_started.set()
                await self.release_cookie_save.wait()
            self.storage[storage_key] = payload

    monkeypatch.setattr(henu_api, "system_status", write_two_files)
    monkeypatch.setattr(PluginStorageAdapter, "_shared_temp_dir", tmp_path / "staging")
    PluginStorageAdapter._user_locks.clear()
    PluginStorageAdapter._shared_locks.clear()

    async def scenario() -> BlockingSavePlugin:
        plugin = BlockingSavePlugin()
        tool = BaseHenuTool()
        tool.tool_name = "system_status"
        tool.plugin = plugin
        first = asyncio.create_task(tool.call({}, _session("10001"), 1))
        await asyncio.wait_for(plugin.cookie_save_started.wait(), timeout=1)

        first.cancel()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        first.cancel()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        cancellation_waited_for_save = not first.done()

        second_adapter = PluginStorageAdapter(plugin, "10001")
        second_load = asyncio.create_task(second_adapter.load_all())
        await asyncio.sleep(0)
        same_user_lock_still_held = not second_load.done()

        plugin.release_cookie_save.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        second_paths = await asyncio.wait_for(second_load, timeout=1)
        assert second_paths.profile_file.read_text(encoding="utf-8") == (
            '{"profile":true}'
        )
        assert second_paths.library_cookie_file.read_text(encoding="utf-8") == (
            '{"cookie":true}'
        )
        await second_adapter.save_all()

        assert cancellation_waited_for_save
        assert same_user_lock_still_held
        return plugin

    try:
        plugin = asyncio.run(scenario())
    finally:
        PluginStorageAdapter._user_locks.clear()
        PluginStorageAdapter._shared_locks.clear()

    assert _snapshot_file(plugin.storage, "10001", "profile") == b'{"profile":true}'
    assert _snapshot_file(plugin.storage, "10001", "library_cookie") == b'{"cookie":true}'


def test_identity_listener_preserves_cancellation_when_storage_save_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from components.event_listener.identity_capture import IdentityCaptureListener
    from henu_plugin.storage_adapter import PluginStorageAdapter

    worker_started = threading.Event()
    release_worker = threading.Event()

    def delayed_write() -> None:
        paths = get_current_user_paths()
        assert paths is not None
        worker_started.set()
        release_worker.wait(timeout=2)
        paths.profile_file.write_text('{"changed":true}', encoding="utf-8")

    class FailingSavePlugin:
        async def get_plugin_storage(self, _storage_key):
            return b"{}"

        async def set_plugin_storage(self, storage_key, _payload):
            raise RuntimeError(f"save failed: {storage_key}")

    staging = tmp_path / "staging"
    monkeypatch.setattr(PluginStorageAdapter, "_shared_temp_dir", staging)
    PluginStorageAdapter._user_locks.clear()
    PluginStorageAdapter._shared_locks.clear()
    plugin = FailingSavePlugin()
    listener = IdentityCaptureListener()
    listener.plugin = plugin

    async def scenario() -> None:
        task = asyncio.create_task(
            listener._run_with_user_storage(
                _session("10001"),
                {},
                delayed_write,
            )
        )
        assert await asyncio.to_thread(worker_started.wait, 1)
        task.cancel()
        release_worker.set()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert get_current_user_paths() is None
        assert list(staging.iterdir()) == []
        retry = PluginStorageAdapter(plugin, "10001")
        await asyncio.wait_for(retry.load_all(), timeout=1)
        await retry.abort()

    try:
        asyncio.run(scenario())
    finally:
        release_worker.set()
        PluginStorageAdapter._user_locks.clear()
        PluginStorageAdapter._shared_locks.clear()

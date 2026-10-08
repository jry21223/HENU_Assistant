from __future__ import annotations

import contextlib
import threading
import time

import anyio

from henu_mcp.api import ToolSpec


class RecordingRuntime:
    def __init__(self) -> None:
        self.activations: list[tuple[str, int]] = []

    @contextlib.contextmanager
    def activate(self, scope: str):
        self.activations.append((scope, threading.get_ident()))
        yield


def test_stateful_execution_activates_runtime_in_a_worker_thread() -> None:
    from henu_mcp.executor import CampusToolExecutor

    event_loop_thread = threading.get_ident()
    handler_threads: list[int] = []

    def status(value: str = "ok") -> dict[str, object]:
        handler_threads.append(threading.get_ident())
        return {"success": True, "value": value}

    runtime = RecordingRuntime()
    executor = CampusToolExecutor(
        specs=(ToolSpec("status", status, "stateful"),),
        runtime=runtime,
    )

    result = anyio.run(executor.execute, "status", {"value": "ready"}, "student-1")

    assert result == {"success": True, "value": "ready"}
    assert runtime.activations == [("student-1", handler_threads[0])]
    assert handler_threads[0] != event_loop_thread


def test_stateful_executions_cannot_overlap_runtime_activation() -> None:
    from henu_mcp.executor import CampusToolExecutor

    active = 0
    maximum_active = 0
    counter_lock = threading.Lock()

    def stateful_work() -> dict[str, bool]:
        nonlocal active, maximum_active
        with counter_lock:
            active += 1
            maximum_active = max(maximum_active, active)
        time.sleep(0.03)
        with counter_lock:
            active -= 1
        return {"success": True}

    executor = CampusToolExecutor(
        specs=(ToolSpec("stateful_work", stateful_work, "stateful"),),
        runtime=RecordingRuntime(),
    )

    async def run_concurrently() -> None:
        async with anyio.create_task_group() as group:
            group.start_soon(executor.execute, "stateful_work", {}, "student-1")
            group.start_soon(executor.execute, "stateful_work", {}, "student-2")

    anyio.run(run_concurrently)

    assert maximum_active == 1


def test_stateful_queue_does_not_exhaust_workers_needed_by_pure_tools() -> None:
    from henu_mcp.executor import CampusToolExecutor

    stateful_started = threading.Event()
    release_stateful = threading.Event()

    def held_stateful() -> dict[str, bool]:
        stateful_started.set()
        release_stateful.wait(timeout=2)
        return {"success": True}

    def pure_probe() -> dict[str, bool]:
        return {"success": True}

    executor = CampusToolExecutor(
        specs=(
            ToolSpec("held_stateful", held_stateful, "stateful"),
            ToolSpec("pure_probe", pure_probe, "pure"),
        ),
        runtime=RecordingRuntime(),
    )

    async def exercise_pool() -> None:
        async with anyio.create_task_group() as group:
            for _ in range(40):
                group.start_soon(executor.execute, "held_stateful", {}, "student-1")
            await anyio.to_thread.run_sync(stateful_started.wait, 1)
            with anyio.fail_after(0.5):
                assert await executor.execute("pure_probe") == {"success": True}
            release_stateful.set()

    try:
        anyio.run(exercise_pool)
    finally:
        release_stateful.set()


def test_course_monitor_is_cancellable_between_rounds() -> None:
    from henu_mcp.executor import CampusToolExecutor

    calls = 0

    def monitor_round(
        config_json: str = "",
        max_checks: int = 1,
        duration_seconds: int = 0,
        send_notifications: bool = True,
    ) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {
            "success": True,
            "msg": "monitor round",
            "checks": 1,
            "interval_seconds": 60,
            "results": [{"success": True, "round": calls}],
        }

    executor = CampusToolExecutor(
        specs=(ToolSpec("course_monitor_run", monitor_round, "stateful"),),
        runtime=RecordingRuntime(),
    )

    async def cancel_during_interval() -> None:
        with anyio.move_on_after(0.1) as cancel_scope:
            await executor.execute(
                "course_monitor_run",
                {"max_checks": 0, "duration_seconds": 600},
                "student-1",
            )
        assert cancel_scope.cancel_called

    anyio.run(cancel_during_interval)

    assert calls == 1


def test_failed_course_monitor_round_still_waits_before_retrying() -> None:
    from henu_mcp.executor import CampusToolExecutor

    calls = 0

    def failed_round(
        config_json: str = "",
        max_checks: int = 1,
        duration_seconds: int = 0,
        send_notifications: bool = True,
    ) -> dict[str, object]:
        del config_json, max_checks, duration_seconds, send_notifications
        nonlocal calls
        calls += 1
        return {"success": False, "msg": "temporary failure"}

    executor = CampusToolExecutor(
        specs=(ToolSpec("course_monitor_run", failed_round, "stateful"),),
        runtime=RecordingRuntime(),
    )

    async def cancel_during_default_interval() -> None:
        with anyio.move_on_after(0.1) as cancel_scope:
            await executor.execute(
                "course_monitor_run",
                {"max_checks": 0, "duration_seconds": 600},
            )
        assert cancel_scope.cancel_called

    anyio.run(cancel_during_default_interval)

    assert calls == 1


def test_background_worker_and_manual_tool_share_the_stateful_gate() -> None:
    from henu_mcp.executor import CampusToolExecutor

    active = 0
    maximum_active = 0
    counter_lock = threading.Lock()

    def enter_boundary() -> dict[str, bool]:
        nonlocal active, maximum_active
        with counter_lock:
            active += 1
            maximum_active = max(maximum_active, active)
        time.sleep(0.03)
        with counter_lock:
            active -= 1
        return {"success": True}

    executor = CampusToolExecutor(
        specs=(ToolSpec("seminar_signin", enter_boundary, "stateful"),),
        runtime=RecordingRuntime(),
    )

    async def exercise_boundary() -> None:
        async with anyio.create_task_group() as group:
            group.start_soon(executor.execute, "seminar_signin")
            group.start_soon(executor.execute_background, enter_boundary)

    anyio.run(exercise_boundary)

    assert maximum_active == 1


def test_fixed_filesystem_runtime_scopes_and_restores_shared_storage(
    tmp_path,
) -> None:
    from campus_core import storage_paths
    from henu_mcp.runtime import FixedFilesystemRuntime

    original_shared_dir = storage_paths.get_shared_data_dir()
    runtime = FixedFilesystemRuntime(tmp_path)

    with runtime.activate("student-1"):
        assert storage_paths.get_shared_data_dir() == tmp_path / "data" / "shared"

    assert storage_paths.get_shared_data_dir() == original_shared_dir

from __future__ import annotations

import functools
import threading
from collections.abc import Callable, Iterable
from typing import Any, TypeVar

import anyio

from henu_mcp.api import MCP_TOOL_SPECS, ToolSpec
from henu_mcp.runtime import NullRuntimeAdapter, RuntimeAdapter


_RUNTIME_EXECUTION_LOCK = threading.RLock()
_RUNTIME_GATE: anyio.Lock | None = None
_STATEFUL_GATES: dict[str, anyio.Lock] = {}
_STATEFUL_GATE_LOCK = threading.Lock()
_COURSE_MONITOR_MIN_INTERVAL_SECONDS = 60
_ResultT = TypeVar("_ResultT")


def _runtime_gate() -> anyio.Lock:
    global _RUNTIME_GATE
    with _STATEFUL_GATE_LOCK:
        if _RUNTIME_GATE is None:
            _RUNTIME_GATE = anyio.Lock()
        return _RUNTIME_GATE


def _stateful_gate(scope: str) -> anyio.Lock:
    key = str(scope or "default")
    with _STATEFUL_GATE_LOCK:
        return _STATEFUL_GATES.setdefault(key, anyio.Lock())


class UnknownCampusToolError(KeyError):
    pass


class CampusToolExecutor:
    """Execute public campus tools without exposing transport concerns."""

    def __init__(
        self,
        *,
        specs: Iterable[ToolSpec] = MCP_TOOL_SPECS,
        runtime: RuntimeAdapter | None = None,
    ) -> None:
        spec_list = tuple(specs)
        spec_map = {spec.name: spec for spec in spec_list}
        if len(spec_map) != len(spec_list):
            raise ValueError("tool spec names must be unique")
        self._specs = spec_map
        self._runtime = runtime or NullRuntimeAdapter()

    def _execute_stateful(self, spec: ToolSpec, arguments: dict[str, Any], scope: str) -> dict[str, Any]:
        return self._run_in_runtime(
            functools.partial(spec.handler, **arguments),
            scope=scope,
        )

    def _run_in_runtime(self, callback: Callable[[], _ResultT], *, scope: str = "default") -> _ResultT:
        with _RUNTIME_EXECUTION_LOCK:
            with self._runtime.activate(str(scope)):
                return callback()

    @staticmethod
    def _execute_pure(spec: ToolSpec, arguments: dict[str, Any]) -> dict[str, Any]:
        return spec.handler(**arguments)

    async def _execute_stateful_async(
        self,
        spec: ToolSpec,
        arguments: dict[str, Any],
        scope: str,
    ) -> dict[str, Any]:
        async with _stateful_gate(scope):
            async with _runtime_gate():
                return await anyio.to_thread.run_sync(
                    functools.partial(self._execute_stateful, spec, arguments, scope),
                )

    async def execute_background(
        self,
        callback: Callable[[], _ResultT],
        *,
        scope: str = "background",
    ) -> _ResultT:
        """Run a background callback through the same gate as stateful tools."""
        async with _stateful_gate(scope):
            async with _runtime_gate():
                return await anyio.to_thread.run_sync(
                    functools.partial(self._run_in_runtime, callback, scope=scope),
                )

    async def _execute_course_monitor(
        self,
        spec: ToolSpec,
        arguments: dict[str, Any],
        scope: str,
    ) -> dict[str, Any]:
        """Run monitor rounds in workers and keep the interval async/cancellable."""
        max_checks = int(arguments.get("max_checks", 1))
        duration_seconds = int(arguments.get("duration_seconds", 0))
        if max_checks <= 0 and duration_seconds <= 0:
            max_checks = 1

        round_arguments = dict(arguments)
        round_arguments["max_checks"] = 1
        round_arguments["duration_seconds"] = 0
        started = anyio.current_time()
        checks = 0
        interval_seconds = 0
        results: list[dict[str, Any]] = []
        successes: list[bool] = []

        while True:
            round_result = await self._execute_stateful_async(spec, round_arguments, scope)
            successes.append(bool(round_result.get("success")))
            interval_seconds = max(
                _COURSE_MONITOR_MIN_INTERVAL_SECONDS,
                int(round_result.get("interval_seconds") or _COURSE_MONITOR_MIN_INTERVAL_SECONDS),
            )
            round_results = round_result.get("results")
            if isinstance(round_results, list):
                results.extend(item for item in round_results if isinstance(item, dict))
            else:
                results.append(round_result)
            checks += max(1, int(round_result.get("checks") or 0))

            if max_checks > 0 and checks >= max_checks:
                break

            elapsed = anyio.current_time() - started
            if duration_seconds > 0 and elapsed >= duration_seconds:
                break
            delay = float(interval_seconds)
            if duration_seconds > 0:
                delay = min(delay, max(0.0, duration_seconds - elapsed))
            await anyio.sleep(delay)
            if duration_seconds > 0 and anyio.current_time() - started >= duration_seconds:
                break

        return {
            "success": all(successes),
            "msg": "选课余量监控运行完成；未执行任何选课提交。",
            "checks": checks,
            "interval_seconds": interval_seconds,
            "results": results,
        }

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        scope: str = "default",
    ) -> dict[str, Any]:
        try:
            spec = self._specs[name]
        except KeyError as exc:
            raise UnknownCampusToolError(name) from exc

        call_arguments = dict(arguments or {})
        if spec.execution_mode == "pure":
            return await anyio.to_thread.run_sync(
                functools.partial(self._execute_pure, spec, call_arguments),
            )

        if spec.name == "course_monitor_run":
            return await self._execute_course_monitor(spec, call_arguments, str(scope))
        return await self._execute_stateful_async(spec, call_arguments, str(scope))


__all__ = ["CampusToolExecutor", "UnknownCampusToolError"]

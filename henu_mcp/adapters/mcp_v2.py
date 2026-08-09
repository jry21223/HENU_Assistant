from __future__ import annotations

import functools
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import anyio
from mcp.server import MCPServer

from henu_mcp.api import MCP_TOOL_SPECS, ToolSpec
from henu_mcp.executor import CampusToolExecutor
from henu_mcp.runtime import RuntimeAdapter
from henu_mcp.tools import server_impl as _impl
from henu_mcp.version import __version__


logger = logging.getLogger(__name__)


def _bind_tool(spec: ToolSpec, executor: CampusToolExecutor) -> Callable[..., Any]:
    @functools.wraps(spec.handler)
    async def execute_tool(**arguments: Any) -> dict[str, Any]:
        return await executor.execute(spec.name, arguments)

    return execute_tool


def _seminar_worker_lifespan(
    executor: CampusToolExecutor,
    interval_seconds: float,
) -> Callable[[MCPServer], Any]:
    @asynccontextmanager
    async def lifespan(_server: MCPServer) -> AsyncIterator[dict[str, Any]]:
        async def run_worker() -> None:
            while True:
                try:
                    await executor.execute_background(
                        functools.partial(
                            _impl._process_seminar_signin_tasks,
                            due_only=True,
                            trigger="background",
                        ),
                        scope="seminar-background",
                    )
                except Exception:
                    logger.exception(
                        "Seminar background scan failed; persisted state was left unchanged"
                    )
                await anyio.sleep(interval_seconds)

        async with anyio.create_task_group() as task_group:
            task_group.start_soon(run_worker)
            try:
                yield {}
            finally:
                task_group.cancel_scope.cancel()

    return lifespan


def create_mcp_server(
    *,
    executor: CampusToolExecutor | None = None,
    runtime: RuntimeAdapter | None = None,
    seminar_interval_seconds: float = _impl.SEMINAR_AUTO_SIGNIN_INTERVAL_SECONDS,
    enable_seminar_worker: bool = True,
) -> MCPServer:
    if executor is not None and runtime is not None:
        raise ValueError("pass executor or runtime, not both")

    tool_executor = executor or CampusToolExecutor(runtime=runtime)
    lifespan = (
        _seminar_worker_lifespan(
            tool_executor,
            max(0.01, float(seminar_interval_seconds)),
        )
        if enable_seminar_worker
        else None
    )
    server = MCPServer(
        "henu-campus-unified",
        description="河南大学校园服务统一 MCP 服务器",
        version=__version__,
        lifespan=lifespan,
    )
    for spec in MCP_TOOL_SPECS:
        server.add_tool(
            _bind_tool(spec, tool_executor),
            name=spec.name,
            structured_output=True,
        )
    return server


__all__ = ["create_mcp_server"]

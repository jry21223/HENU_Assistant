from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from henu_mcp import api
from henu_mcp.adapters.mcp_v2 import create_mcp_server
from henu_mcp.executor import CampusToolExecutor
from henu_mcp.runtime import FixedFilesystemRuntime
from henu_mcp.version import __version__


Transport = Literal["stdio", "streamable-http", "sse"]
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
NO_SUBMIT_CODE = "not_implemented"


class RunnableMCPServer(Protocol):
    def run(self, transport: str, **kwargs: Any) -> None: ...


@dataclass(frozen=True, slots=True)
class RunnerConfig:
    transport: Transport = "stdio"
    host: str = "127.0.0.1"
    port: int = 8001
    path: str = "/mcp"
    sse_path: str = "/sse"
    message_path: str = "/messages/"
    stateless_http: bool = False
    json_response: bool = False
    data_root: Path | None = None
    enable_background_workers: bool = True


_runtime = FixedFilesystemRuntime(Path(__file__).resolve().parent)
executor = CampusToolExecutor(runtime=_runtime)
mcp = create_mcp_server(executor=executor)


def build_server(config: RunnerConfig):
    data_root = (config.data_root or Path(__file__).resolve().parent).resolve()
    runtime = FixedFilesystemRuntime(data_root)
    configured_executor = CampusToolExecutor(runtime=runtime)
    return create_mcp_server(
        executor=configured_executor,
        enable_seminar_worker=config.enable_background_workers,
    )


# Backwards-compatible module exports now point at the transport-free API.
setup_account = api.setup_account
show_account = api.show_account
check_login = api.check_login
system_status = api.system_status
get_server_time = api.get_server_time

sync_schedule = api.sync_schedule
fetch_schedule = api.fetch_schedule
schedule_query = api.schedule_query
set_calibration_source = api.set_calibration_source
get_period_time_config = api.get_period_time_config
get_xiqueer_calibration_request = api.get_xiqueer_calibration_request
set_xiqueer_calibration_request = api.set_xiqueer_calibration_request
test_xiqueer_period_time_request = api.test_xiqueer_period_time_request
auto_calibrate_period_time = api.auto_calibrate_period_time
set_period_time = api.set_period_time
rebuild_clean_schedule_from_latest_grid = api.rebuild_clean_schedule_from_latest_grid
list_output_files = api.list_output_files

smart_course_selection = api.smart_course_selection
smart_course_select = api.smart_course_select
course_selection_query = api.course_selection_query
course_selection_plan = api.course_selection_plan
course_selection_submit = api.course_selection_submit
course_monitor_config = api.course_monitor_config
course_monitor_once = api.course_monitor_once
course_monitor_run = api.course_monitor_run
course_monitor_notify_test = api.course_monitor_notify_test

library_query = api.library_query
library_reserve = api.library_reserve
library_auto_signin = api.library_auto_signin
library_cancel = api.library_cancel

seminar_group = api.seminar_group
seminar_query = api.seminar_query
seminar_signin = api.seminar_signin
seminar_cancel = api.seminar_cancel
seminar_reserve = api.seminar_reserve

yunfz_leave_query = api.yunfz_leave_query
yunfz_signin_query = api.yunfz_signin_query
yunfz_checksleep_query = api.yunfz_checksleep_query
yunfz_activity_query = api.yunfz_activity_query
yunfz_collection_query = api.yunfz_collection_query

empty_classroom_query = api.empty_classroom_query
empty_classroom_sync = api.empty_classroom_sync
resource_registry_query = api.resource_registry_query
resource_registry_sync = api.resource_registry_sync


def __getattr__(name: str) -> Any:
    return getattr(api, name)


def run_server(server: RunnableMCPServer, config: RunnerConfig) -> None:
    if config.transport == "stdio":
        server.run("stdio")
        return

    if config.host not in LOOPBACK_HOSTS:
        raise ValueError("HTTP transports are restricted to loopback hosts until authentication is configured")

    if config.transport == "sse":
        server.run(
            "sse",
            host=config.host,
            port=config.port,
            sse_path=config.sse_path,
            message_path=config.message_path,
        )
        return

    server.run(
        "streamable-http",
        host=config.host,
        port=config.port,
        streamable_http_path=config.path,
        stateless_http=config.stateless_http,
        json_response=config.json_response,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="HENU unified MCP server (schedule + library)")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http", "sse"],
        default="stdio",
        help="MCP transport type",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Loopback host for HTTP transports")
    parser.add_argument("--port", type=int, default=8001, help="Port for HTTP transports")
    parser.add_argument("--path", default="/mcp", help="Endpoint path for streamable-http transport")
    parser.add_argument("--sse-path", default="/sse", help="Event endpoint path for SSE transport")
    parser.add_argument(
        "--message-path",
        default="/messages/",
        help="Client message endpoint path for SSE transport",
    )
    parser.add_argument(
        "--stateless-http",
        action="store_true",
        help="Enable stateless HTTP mode for streamable-http transport",
    )
    parser.add_argument(
        "--json-response",
        action="store_true",
        help="Enable JSON response mode for streamable-http transport",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=None,
        help="Runtime data root; diagnostics and tests should pass an isolated directory",
    )
    parser.add_argument(
        "--disable-background-workers",
        action="store_true",
        help="Disable seminar background processing for diagnostics and tests",
    )
    return parser


def main() -> None:
    parser = build_parser()
    namespace = parser.parse_args()
    config = RunnerConfig(
        transport=namespace.transport,
        host=namespace.host,
        port=namespace.port,
        path=namespace.path,
        sse_path=namespace.sse_path,
        message_path=namespace.message_path,
        stateless_http=namespace.stateless_http,
        json_response=namespace.json_response,
        data_root=namespace.data_root,
        enable_background_workers=not namespace.disable_background_workers,
    )
    try:
        run_server(build_server(config), config)
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()

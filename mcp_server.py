from __future__ import annotations

import argparse
from typing import Any

from henu_mcp.tools import server_impl as _impl
from henu_mcp.tools import account, course, empty_classroom, library, resources, schedule, seminar, yunfz


mcp = _impl.mcp
NO_SUBMIT_CODE = "not_implemented"

setup_account = account.setup_account
show_account = account.show_account
check_login = account.check_login
system_status = account.system_status
get_server_time = account.get_server_time

sync_schedule = schedule.sync_schedule
fetch_schedule = schedule.fetch_schedule
schedule_query = schedule.schedule_query
set_calibration_source = schedule.set_calibration_source
get_period_time_config = schedule.get_period_time_config
get_xiqueer_calibration_request = schedule.get_xiqueer_calibration_request
set_xiqueer_calibration_request = schedule.set_xiqueer_calibration_request
test_xiqueer_period_time_request = schedule.test_xiqueer_period_time_request
auto_calibrate_period_time = schedule.auto_calibrate_period_time
set_period_time = schedule.set_period_time
rebuild_clean_schedule_from_latest_grid = schedule.rebuild_clean_schedule_from_latest_grid
list_output_files = schedule.list_output_files

smart_course_selection = course.smart_course_selection
smart_course_select = course.smart_course_select
course_selection_query = course.course_selection_query
course_selection_plan = course.course_selection_plan
course_selection_submit = course.course_selection_submit
course_monitor_config = course.course_monitor_config
course_monitor_once = course.course_monitor_once
course_monitor_run = course.course_monitor_run
course_monitor_notify_test = course.course_monitor_notify_test

library_query = library.library_query
library_reserve = library.library_reserve
library_auto_signin = library.library_auto_signin
library_cancel = library.library_cancel

seminar_group = seminar.seminar_group
seminar_query = seminar.seminar_query
seminar_signin = seminar.seminar_signin
seminar_cancel = seminar.seminar_cancel
seminar_reserve = seminar.seminar_reserve

yunfz_leave_query = yunfz.yunfz_leave_query
yunfz_signin_query = yunfz.yunfz_signin_query
yunfz_checksleep_query = yunfz.yunfz_checksleep_query
yunfz_activity_query = yunfz.yunfz_activity_query
yunfz_collection_query = yunfz.yunfz_collection_query

empty_classroom_query = empty_classroom.empty_classroom_query
empty_classroom_sync = empty_classroom.empty_classroom_sync
resource_registry_query = resources.resource_registry_query
resource_registry_sync = resources.resource_registry_sync


def __getattr__(name: str) -> Any:
    return getattr(_impl, name)


def main() -> None:
    parser = argparse.ArgumentParser(description="HENU unified MCP server (schedule + library)")
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http", "sse"],
        default="stdio",
        help="MCP transport type",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host for HTTP transports")
    parser.add_argument("--port", type=int, default=8001, help="Port for HTTP transports")
    parser.add_argument("--path", default="/mcp", help="HTTP endpoint path for streamable-http transport")
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
    args = parser.parse_args()

    if args.transport in ("streamable-http", "sse"):
        mcp.settings.host = args.host
        mcp.settings.port = args.port
    if args.transport == "streamable-http":
        mcp.settings.streamable_http_path = args.path
        mcp.settings.stateless_http = args.stateless_http
        mcp.settings.json_response = args.json_response

    _impl._ensure_seminar_auto_signin_worker()
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()

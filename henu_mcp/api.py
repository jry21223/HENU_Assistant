from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from henu_mcp.tools import server_impl as _impl


ExecutionMode = Literal["pure", "stateful"]
ToolHandler = Callable[..., dict[str, Any]]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """Transport-neutral description of one public campus tool."""

    name: str
    handler: ToolHandler
    execution_mode: ExecutionMode

    @property
    def description(self) -> str:
        return inspect.getdoc(self.handler) or ""

    @property
    def signature(self) -> inspect.Signature:
        return inspect.signature(self.handler)


# Public business API. These are aliases rather than wrappers so every delivery
# surface observes the same signature, annotations and docstring.
setup_account = _impl.setup_account
show_account = _impl.show_account
check_login = _impl.check_login
system_status = _impl.system_status
get_server_time = _impl.get_server_time

sync_schedule = _impl.sync_schedule
fetch_schedule = _impl.fetch_schedule
schedule_query = _impl.schedule_query
set_calibration_source = _impl.set_calibration_source
get_period_time_config = _impl.get_period_time_config
get_xiqueer_calibration_request = _impl.get_xiqueer_calibration_request
set_xiqueer_calibration_request = _impl.set_xiqueer_calibration_request
test_xiqueer_period_time_request = _impl.test_xiqueer_period_time_request
auto_calibrate_period_time = _impl.auto_calibrate_period_time
set_period_time = _impl.set_period_time
rebuild_clean_schedule_from_latest_grid = _impl.rebuild_clean_schedule_from_latest_grid
list_output_files = _impl.list_output_files

smart_course_selection = _impl.smart_course_selection
smart_course_select = _impl.smart_course_select
course_selection_query = _impl.course_selection_query
course_selection_plan = _impl.course_selection_plan
course_selection_submit = _impl.course_selection_submit
course_monitor_config = _impl.course_monitor_config
course_monitor_once = _impl.course_monitor_once
course_monitor_run = _impl.course_monitor_run
course_monitor_notify_test = _impl.course_monitor_notify_test

library_query = _impl.library_query
library_reserve = _impl.library_reserve
library_auto_signin = _impl.library_auto_signin
library_cancel = _impl.library_cancel

seminar_group = _impl.seminar_group
seminar_query = _impl.seminar_query
seminar_signin = _impl.seminar_signin
seminar_cancel = _impl.seminar_cancel
seminar_reserve = _impl.seminar_reserve

yunfz_leave_query = _impl.yunfz_leave_query
yunfz_signin_query = _impl.yunfz_signin_query
yunfz_checksleep_query = _impl.yunfz_checksleep_query
yunfz_activity_query = _impl.yunfz_activity_query
yunfz_collection_query = _impl.yunfz_collection_query

empty_classroom_query = _impl.empty_classroom_query
empty_classroom_sync = _impl.empty_classroom_sync
resource_registry_query = _impl.resource_registry_query
resource_registry_sync = _impl.resource_registry_sync


_MCP_TOOL_NAMES = (
    "smart_course_selection",
    "smart_course_select",
    "setup_account",
    "sync_schedule",
    "library_query",
    "library_reserve",
    "library_auto_signin",
    "library_cancel",
    "course_selection_query",
    "course_selection_plan",
    "course_selection_submit",
    "course_monitor_config",
    "course_monitor_once",
    "course_monitor_run",
    "course_monitor_notify_test",
    "seminar_group",
    "seminar_query",
    "seminar_signin",
    "seminar_cancel",
    "seminar_reserve",
    "schedule_query",
    "set_calibration_source",
    "system_status",
    "yunfz_leave_query",
    "yunfz_signin_query",
    "yunfz_checksleep_query",
    "yunfz_activity_query",
    "yunfz_collection_query",
    "empty_classroom_query",
    "empty_classroom_sync",
    "resource_registry_query",
    "resource_registry_sync",
)

_PURE_TOOL_NAMES = {
    "smart_course_selection",
    "smart_course_select",
    "course_selection_plan",
    "course_selection_submit",
}


def _serialize_stateful_handler(handler: ToolHandler) -> ToolHandler:
    @functools.wraps(handler)
    def serialized(*args: Any, **kwargs: Any) -> dict[str, Any]:
        from henu_mcp.runtime import runtime_state_transaction

        with runtime_state_transaction():
            return handler(*args, **kwargs)

    return serialized


for _stateful_name in set(_MCP_TOOL_NAMES) - _PURE_TOOL_NAMES:
    globals()[_stateful_name] = _serialize_stateful_handler(globals()[_stateful_name])


MCP_TOOL_SPECS = tuple(
    ToolSpec(
        name=name,
        handler=globals()[name],
        execution_mode="pure" if name in _PURE_TOOL_NAMES else "stateful",
    )
    for name in _MCP_TOOL_NAMES
)


__all__ = [
    "ExecutionMode",
    "MCP_TOOL_SPECS",
    "ToolSpec",
    "setup_account",
    "show_account",
    "check_login",
    "system_status",
    "get_server_time",
    "sync_schedule",
    "fetch_schedule",
    "schedule_query",
    "set_calibration_source",
    "get_period_time_config",
    "get_xiqueer_calibration_request",
    "set_xiqueer_calibration_request",
    "test_xiqueer_period_time_request",
    "auto_calibrate_period_time",
    "set_period_time",
    "rebuild_clean_schedule_from_latest_grid",
    "list_output_files",
    "smart_course_selection",
    "smart_course_select",
    "course_selection_query",
    "course_selection_plan",
    "course_selection_submit",
    "course_monitor_config",
    "course_monitor_once",
    "course_monitor_run",
    "course_monitor_notify_test",
    "library_query",
    "library_reserve",
    "library_auto_signin",
    "library_cancel",
    "seminar_group",
    "seminar_query",
    "seminar_signin",
    "seminar_cancel",
    "seminar_reserve",
    "yunfz_leave_query",
    "yunfz_signin_query",
    "yunfz_checksleep_query",
    "yunfz_activity_query",
    "yunfz_collection_query",
    "empty_classroom_query",
    "empty_classroom_sync",
    "resource_registry_query",
    "resource_registry_sync",
]

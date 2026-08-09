from __future__ import annotations

import inspect


EXPECTED_MCP_TOOL_NAMES = (
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


def test_api_is_the_transport_free_authority_for_mcp_tools() -> None:
    from henu_mcp.api import MCP_TOOL_SPECS

    assert tuple(spec.name for spec in MCP_TOOL_SPECS) == EXPECTED_MCP_TOOL_NAMES
    assert all(spec.execution_mode in {"pure", "stateful"} for spec in MCP_TOOL_SPECS)
    assert {
        spec.name
        for spec in MCP_TOOL_SPECS
        if spec.execution_mode == "pure"
    } == {
        "smart_course_selection",
        "smart_course_select",
        "course_selection_plan",
        "course_selection_submit",
    }
    assert all(spec.handler.__name__ == spec.name for spec in MCP_TOOL_SPECS)
    assert all(inspect.signature(spec.handler) == spec.signature for spec in MCP_TOOL_SPECS)
    assert all(spec.description == inspect.getdoc(spec.handler) for spec in MCP_TOOL_SPECS)


def test_api_explicitly_exports_the_shared_facade() -> None:
    from henu_mcp import api

    required_facade = {
        "show_account",
        "get_server_time",
        "setup_account",
        *EXPECTED_MCP_TOOL_NAMES,
    }

    assert required_facade <= set(api.__all__)
    assert all(callable(getattr(api, name)) for name in required_facade)


def test_api_import_does_not_load_the_mcp_transport() -> None:
    import subprocess
    import sys

    code = "import sys; import henu_mcp.api; assert 'mcp' not in sys.modules"
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr

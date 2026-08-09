from __future__ import annotations

from pathlib import Path
import threading

import anyio
import pytest


EXPECTED_PARAMETERS = {
    "smart_course_selection": ("source_path", "excel_path", "json_path", "user_class", "sheet_name", "semester", "mode", "like_early8", "avoid_early8", "compact_days", "target_days", "avoid_evening", "allow_unscheduled", "include_common", "include_course_options", "top_k", "max_combinations"),
    "smart_course_select": ("excel_path", "user_class", "sheet_name", "semester", "like_early8", "avoid_early8", "compact_days", "target_days", "avoid_evening", "allow_unscheduled", "top_k"),
    "setup_account": ("student_id", "password", "library_location", "library_seat_no", "verify_login", "calibrate_period_time"),
    "sync_schedule": ("xn", "xq", "auto_calibrate"),
    "library_query": ("view", "record_type", "page", "limit", "target_date", "location", "area_id", "preferred_time", "preferred_end_time"),
    "library_reserve": ("location", "seat_no", "target_date", "preferred_time", "preferred_end_time", "resource_id", "retry_until", "retry_interval_seconds", "max_attempts"),
    "library_auto_signin": ("record_id",),
    "library_cancel": ("record_id", "record_type"),
    "course_selection_query": ("view", "xktype"),
    "course_selection_plan": ("candidates_json", "existing_schedule_json", "preferences_json", "top_k"),
    "course_selection_submit": ("payload_json",),
    "course_monitor_config": ("config_json", "merge"),
    "course_monitor_once": ("config_json", "send_notifications"),
    "course_monitor_run": ("config_json", "max_checks", "duration_seconds", "send_notifications"),
    "course_monitor_notify_test": ("config_json",),
    "seminar_group": ("action", "group_name", "member_ids", "note"),
    "seminar_query": ("view", "target_date", "members", "name", "room", "start_time", "end_time", "library_ids", "library_names", "floor_ids", "floor_names", "category_ids", "category_names", "boutique_ids", "boutique_names", "page", "area_id", "record_type", "limit", "mode", "status"),
    "seminar_signin": ("record_id", "auto_scan"),
    "seminar_cancel": ("record_id",),
    "seminar_reserve": ("area_id", "target_date", "start_time", "end_time", "end_date", "title", "title_id", "content", "mobile", "group_name", "member_ids", "is_open", "cate_id", "time_ranges_json", "resource_id"),
    "schedule_query": ("view", "timezone", "target_date", "auto_calibrate"),
    "set_calibration_source": ("data", "cookie", "user_agent"),
    "system_status": ("timezone",),
    "yunfz_leave_query": ("view", "leave_id", "page", "page_size"),
    "yunfz_signin_query": ("view", "page", "page_size"),
    "yunfz_checksleep_query": ("view", "page", "page_size"),
    "yunfz_activity_query": ("view", "page", "page_size"),
    "yunfz_collection_query": ("view", "page", "page_size"),
    "empty_classroom_query": ("view", "term_code", "week", "day_of_week", "period", "campus_code", "building_code", "campus_text", "building_text", "classroom_text", "type_code", "min_capacity", "keyword", "room_id", "freshness", "force_refresh", "ttl_seconds", "max_stale_seconds"),
    "empty_classroom_sync": ("term_code", "campus_code", "building_code", "type_code", "force_refresh"),
    "resource_registry_query": ("view", "query", "resource_type", "campus_code", "building_code", "limit"),
    "resource_registry_sync": ("scope", "force_refresh"),
}


def test_requirements_pin_the_migrated_sdk_version() -> None:
    requirements = (Path(__file__).resolve().parents[1] / "requirements.txt").read_text(encoding="utf-8").splitlines()

    assert "mcp==2.0.0" in requirements


def test_mcp_server_publishes_the_2_1_0_tool_contract() -> None:
    from henu_mcp.adapters.mcp_v2 import create_mcp_server

    server = create_mcp_server()
    tools = anyio.run(server.list_tools)

    assert server.name == "henu-campus-unified"
    assert server.version == "2.1.0"
    assert tuple(tool.name for tool in tools) == tuple(EXPECTED_PARAMETERS)
    for tool in tools:
        assert tuple(tool.input_schema["properties"]) == EXPECTED_PARAMETERS[tool.name]
        assert tool.output_schema is not None
        assert tool.output_schema["type"] == "object"
        wire_tool = tool.model_dump(by_alias=True, mode="json", exclude_none=True)
        assert "inputSchema" in wire_tool
        assert "outputSchema" in wire_tool
        assert "input_schema" not in wire_tool
        assert "output_schema" not in wire_tool


def test_mcp_tool_registration_explicitly_enables_structured_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mcp.server import MCPServer

    from henu_mcp.adapters.mcp_v2 import create_mcp_server

    original_add_tool = MCPServer.add_tool
    structured_output_values: list[object] = []

    def recording_add_tool(self, function, *args, **kwargs):
        structured_output_values.append(kwargs.get("structured_output"))
        return original_add_tool(self, function, *args, **kwargs)

    monkeypatch.setattr(MCPServer, "add_tool", recording_add_tool)

    create_mcp_server()

    assert structured_output_values == [True] * len(EXPECTED_PARAMETERS)


def test_mcp_tool_call_keeps_structured_business_results() -> None:
    from henu_mcp.adapters.mcp_v2 import create_mcp_server

    server = create_mcp_server()

    async def call_tool():
        return await server.call_tool("course_selection_submit", {})

    result = anyio.run(call_tool)

    assert result.is_error is False
    assert result.structured_content == {
        "success": False,
        "code": "not_implemented",
        "msg": "选课提交端点需要在选课开放后通过真实请求确认，当前版本不执行真实提交。",
    }
    assert result.content
    wire_result = result.model_dump(by_alias=True, mode="json", exclude_none=True)
    assert wire_result["structuredContent"] == result.structured_content
    assert wire_result["isError"] is False
    assert "structured_content" not in wire_result
    assert "is_error" not in wire_result


def test_mcp_lifespan_runs_and_cancels_the_serialized_seminar_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from henu_mcp.adapters.mcp_v2 import create_mcp_server
    from henu_mcp.tools import server_impl

    worker_called = threading.Event()
    calls = 0

    def scan_tasks(*, due_only: bool, trigger: str):
        nonlocal calls
        calls += 1
        assert due_only is True
        assert trigger == "background"
        worker_called.set()
        return {"success": True}

    monkeypatch.setattr(server_impl, "_process_seminar_signin_tasks", scan_tasks)
    server = create_mcp_server(seminar_interval_seconds=0.01)

    async def exercise_lifespan() -> None:
        assert server.settings.lifespan is not None
        async with server.settings.lifespan(server):
            assert await anyio.to_thread.run_sync(worker_called.wait, 1)

    anyio.run(exercise_lifespan)

    assert calls >= 1


def test_mcp_lifespan_logs_background_seminar_failures(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from henu_mcp.adapters.mcp_v2 import create_mcp_server
    from henu_mcp.tools import server_impl

    worker_failed = threading.Event()

    def broken_scan(*, due_only: bool, trigger: str):
        assert due_only is True
        assert trigger == "background"
        worker_failed.set()
        raise server_impl.SeminarTaskStateError("研讨室任务状态无效")

    monkeypatch.setattr(server_impl, "_process_seminar_signin_tasks", broken_scan)
    server = create_mcp_server(seminar_interval_seconds=0.05)

    async def exercise_lifespan() -> None:
        assert server.settings.lifespan is not None
        async with server.settings.lifespan(server):
            assert await anyio.to_thread.run_sync(worker_failed.wait, 1)
            await anyio.sleep(0.01)

    with caplog.at_level("ERROR", logger="henu_mcp.adapters.mcp_v2"):
        anyio.run(exercise_lifespan)

    assert "Seminar background scan failed" in caplog.text
    assert "研讨室任务状态无效" in caplog.text

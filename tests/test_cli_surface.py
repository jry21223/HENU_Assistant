from __future__ import annotations

from pathlib import Path
import json
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from henu_plugin.cli import (  # noqa: E402
    build_help_payload,
    build_next_commands,
    inspect_cli_command,
    redact_cli_command,
    redact_cli_params,
)
from components.cli_tools.henu_cli_safe import HenuCliSafe  # noqa: E402
from components.cli_tools.base import BaseHenuTool  # noqa: E402


def test_root_help_exposes_empty_classroom_and_resource_topics() -> None:
    payload = build_help_payload("")
    commands = "\n".join(payload["commands"])
    examples = "\n".join(payload["examples"])

    assert "empty classroom" in commands
    assert "resource" in commands
    assert "help empty classroom" in examples
    assert "help resource" in examples


def test_empty_classroom_parser_forwards_rich_filters() -> None:
    spec = inspect_cli_command(
        "empty classroom query --classroom-text 十号楼101 --type-code 05 "
        "--min-capacity 80 --keyword 多媒体 --room-id 0001 --term-code 2026,1 "
        "--week 1 --day-of-week 1 --period 1 --ttl-seconds 60 --max-stale-seconds 600"
    )

    assert spec.resolved_tool == "empty_classroom_query"
    assert spec.params["classroom_text"] == "十号楼101"
    assert spec.params["type_code"] == "05"
    assert spec.params["min_capacity"] == 80
    assert spec.params["keyword"] == "多媒体"
    assert spec.params["room_id"] == "0001"
    assert spec.params["ttl_seconds"] == 60
    assert spec.params["max_stale_seconds"] == 600


def test_empty_classroom_accepts_space_dialect_and_reports_all_missing_parameters() -> None:
    spec = inspect_cli_command("empty classroom query")

    assert spec.resolved_tool is None
    assert spec.error == (
        "空教室查询缺少参数：--term-code <学期>、--week <教学周>、"
        "--day-of-week <星期1-7>、--period <大节1-5>。"
    )
    assert spec.help_topic == "empty_classroom"
    assert spec.missing_params == (
        "--term-code <学期>",
        "--week <教学周>",
        "--day-of-week <星期1-7>",
        "--period <大节1-5>",
    )


def test_empty_classroom_rejects_agent_snake_case_dialect() -> None:
    spec = inspect_cli_command("empty_classroom query")

    assert spec.resolved_tool is None
    assert "empty classroom" in str(spec.error)


def test_empty_classroom_help_topic_documents_rich_views() -> None:
    payload = build_help_payload("empty classroom")
    commands = "\n".join(payload["commands"])

    assert "day_matrix" in commands
    assert "occupancy" in commands
    assert "--classroom-text" in commands


def test_library_locations_reply_exposes_names_ids_and_next_command() -> None:
    tool = HenuCliSafe.__new__(HenuCliSafe)
    result = {
        "success": True,
        "msg": "操作成功",
        "date": "2026-07-13",
        "source": "live",
        "locations": [
            {"location": "明伦校区图书馆", "area_id": "101"},
            {"location": "金明校区图书馆北区", "area_id": "202"},
        ],
        "total": 2,
        "cli": {"command": "library locations", "mode": "exec"},
    }

    reply = tool._build_reply_text(result)
    hint = tool._build_llm_hint(result)

    assert "明伦校区图书馆" in reply
    assert "area_id: 101" in reply
    assert "library seats --location" in reply
    assert "唯一有效参数来源" in hint


def test_library_seats_reply_exposes_seat_numbers_and_reserve_command() -> None:
    tool = HenuCliSafe.__new__(HenuCliSafe)
    reply = tool._build_reply_text(
        {
            "success": True,
            "seats": [{"seat_no": "A-101", "status": "可预约", "location": "明伦校区图书馆"}],
            "total": 1,
        }
    )

    assert "A-101" in reply
    assert "library reserve --location" in reply


def test_failed_list_result_does_not_look_like_a_success() -> None:
    tool = HenuCliSafe.__new__(HenuCliSafe)

    reply = tool._build_reply_text(
        {
            "success": False,
            "msg": "查询可用座位失败：登录失效",
            "seats": [],
        }
    )

    assert reply == "查询可用座位失败：登录失效"
    assert "查询完成" not in reply


def test_base_payload_keeps_library_locations_when_compacting() -> None:
    tool = BaseHenuTool.__new__(BaseHenuTool)
    result = {
        "success": True,
        "msg": "操作成功",
        "locations": [{"location": f"区域-{index}", "area_id": str(index)} for index in range(20)],
        "next_commands": ["library seats --location <区域>"],
    }

    tool._normalize_for_qq_delivery(result)

    assert result["locations"]
    assert result["locations_truncated"] == 8


def test_current_schedule_reply_uses_current_courses_not_first_daily_course() -> None:
    tool = HenuCliSafe.__new__(HenuCliSafe)
    result = {
        "success": True,
        "msg": "按星期和时间匹配到 1 门课程（未验证教学周）",
        "week_filter_applied": False,
        "day_schedule": [
            {"course": "早已结束的课程", "time": "1-2节", "classroom": "A101"},
            {"course": "当前课程", "time": "3-4节", "classroom": "B201"},
        ],
        "current_courses": [
            {
                "course": "当前课程",
                "time": "3-4节",
                "classroom": "B201",
                "clock_start": "10:10",
                "clock_end": "11:50",
            }
        ],
        "next_course": None,
    }

    reply = tool._build_reply_text(result)

    assert "当前课程" in reply
    assert "早已结束的课程" not in reply
    assert "未验证教学周" in reply


def test_week_schedule_keeps_a_compact_semantic_summary_under_qq_budget() -> None:
    tool = HenuCliSafe.__new__(HenuCliSafe)
    schedule = {
        f"星期{day}": [
            {
                "course": f"课程-{day}-{index}-" + ("超长课程名" * 100),
                "time": f"{index}-{index + 1}节",
                "classroom": "综合教学楼" + ("很长" * 40),
            }
            for index in range(1, 12)
        ]
        for day in "一二三四五"
    }
    result = {
        "success": True,
        "msg": "完整周课表（未按教学周过滤）",
        "week_filter_applied": False,
        "schedule": schedule,
    }

    tool._prepare_delivery_result(result)
    tool._normalize_for_qq_delivery(result)

    assert result["schedule_summary"]
    assert result["schedule_summary"][0]["courses"]
    assert "课程-一-1" in result["reply_text"]
    assert "未按教学周过滤" in result["reply_text"]
    assert len(json.dumps(result, ensure_ascii=False)) <= 2200


def test_pathological_option_lists_still_fit_the_qq_payload_budget() -> None:
    tool = BaseHenuTool.__new__(BaseHenuTool)
    result = {
        "success": True,
        "msg": "查询完成",
        "location_options": [
            {
                "location": "超长区域" * 100,
                "area_id": str(index) * 100,
                "description": "冗长说明" * 100,
            }
            for index in range(1000)
        ],
        "next_commands": ["library seats --location <区域>"],
    }

    tool._normalize_for_qq_delivery(result)

    assert len(json.dumps(result, ensure_ascii=False)) <= 2200
    assert result["success"] is True
    assert result.get("payload_truncated") is True


def test_course_status_reply_explains_closed_or_unknown_state() -> None:
    tool = HenuCliSafe.__new__(HenuCliSafe)

    closed = tool._build_reply_text(
        {
            "success": True,
            "is_open": False,
            "status_code": "closed",
            "message": "当前不在选课时间范围内",
            "time_range": {"message": "2026-09-01 09:00 至 2026-09-07 18:00"},
        }
    )
    unknown = tool._build_reply_text(
        {"success": True, "is_open": None, "status_code": "unknown", "message": ""}
    )

    assert "当前未开放" in closed
    assert "2026-09-01" in closed
    assert "暂时无法确认" in unknown
    assert "操作完成" not in closed + unknown


def test_library_seat_reply_names_reservation_semantics_and_defaults() -> None:
    tool = HenuCliSafe.__new__(HenuCliSafe)

    reply = tool._build_reply_text(
        {
            "success": True,
            "query_semantics": "reservation_availability",
            "date": "2026-08-12",
            "time_window": "08:00-12:00",
            "defaulted_fields": ["date", "start_time"],
            "area": {"name": "第一自习室"},
            "seats": [{"seat_no": "001", "status": "可预约"}],
            "total_count": 195,
            "available_count": 192,
        }
    )

    assert "预约余位" in reply
    assert "不代表当前在馆空座" in reply
    assert "默认" in reply
    assert "2026-08-12" in reply


def test_empty_seminar_reply_does_not_invent_closed_or_fully_booked_reason() -> None:
    tool = HenuCliSafe.__new__(HenuCliSafe)

    reply = tool._build_reply_text(
        {
            "success": True,
            "msg": "没有匹配当前条件的研讨室",
            "empty_reason": "no_matching_rooms",
            "applied_filters": {"date": "2026-08-11", "members": ""},
            "rooms": [],
        }
    )

    assert "没有匹配" in reply
    assert "不能据此判断" in reply
    assert "已约满" not in reply
    assert "未开放" not in reply


def test_sensitive_cli_values_are_redacted_in_commands_and_params() -> None:
    command = redact_cli_command(
        "account set --student-id 1 --password 'secret value' "
        "--cookie COOKIEVALUE --data '{\"private\":\"payload\"}'"
    )

    assert "secret value" not in command
    assert "COOKIEVALUE" not in command
    assert "payload" not in command
    assert command.count("<redacted>") == 3
    assert redact_cli_params({"password": "secret", "cookie": "cookie", "data": "data", "area_id": "43"}) == {
        "password": "<redacted>",
        "cookie": "<redacted>",
        "data": "<redacted>",
        "area_id": "43",
    }


def test_service_cli_result_does_not_echo_sensitive_account_command() -> None:
    from henu_plugin.service import HenuPluginService  # noqa: WPS433

    service = HenuPluginService.__new__(HenuPluginService)
    service._tool_dispatch = {"setup_account": lambda params: {"success": True, "msg": "账号已保存"}}

    result = service._henu_cli(
        {"command": "account set --student-id 1 --password 'secret value'"}
    )

    assert "secret value" not in str(result)
    assert result["cli"]["command"].endswith("--password <redacted>")
    assert result["_effective_params"]["password"] == "<redacted>"


def test_library_location_summary_keeps_all_area_ids_under_qq_budget() -> None:
    import json

    tool = HenuCliSafe.__new__(HenuCliSafe)
    result = {
        "success": True,
        "msg": "操作成功",
        "date": "2026-07-13",
        "source": "live",
        "locations": [
            {"location": f"区域-{index}", "area_id": str(index)}
            for index in range(1, 34)
        ] + [{"location": "第一自习室", "area_id": "43"}],
        "total": 34,
        "returned_count": 34,
        "truncated": False,
        "cli": {"command": "library locations", "mode": "exec"},
        "next_commands": ["library seats --area-id <ID>"],
    }

    tool._prepare_delivery_result(result)
    tool._normalize_for_qq_delivery(result)

    assert len(result["location_options"]) == 34
    assert {item["area_id"] for item in result["location_options"]} == {*(str(index) for index in range(1, 34)), "43"}
    assert "location_options" in result["reply_text"]
    assert "43" in result["reply_text"]
    assert len(json.dumps(result, ensure_ascii=False)) <= 2200


def test_library_seat_summary_keeps_counts_and_seat_options() -> None:
    import json

    tool = HenuCliSafe.__new__(HenuCliSafe)
    result = {
        "success": True,
        "msg": "查询成功，194/195 个座位可用",
        "area": {"id": "43", "name": "第一自习室"},
        "target_date": "2026-07-13",
        "time_window": "08:00-12:00",
        "total_count": 195,
        "available_count": 194,
        "status_counts": {"1": 194, "0": 1},
        "seats": [
            {"seat_no": str(index), "id": str(index), "status": "可预约"}
            for index in range(1, 196)
        ],
        "cli": {"command": "library seats --area-id 43", "mode": "exec"},
        "next_commands": ["library reserve --area-id 43"],
    }

    tool._prepare_delivery_result(result)
    tool._normalize_for_qq_delivery(result)

    assert result["total_count"] == 195
    assert result["available_count"] == 194
    assert len(result["seat_options"]) == 10
    assert "194/195" in result["reply_text"]
    assert len(json.dumps(result, ensure_ascii=False)) <= 2200


def test_live_empty_library_result_is_explicit_and_has_no_reservation_followup() -> None:
    tool = HenuCliSafe.__new__(HenuCliSafe)
    result = {
        "success": False,
        "error_code": "live_empty",
        "msg": "图书馆实时区域接口返回空列表，暂不能确认可预约区域",
        "source": "live_empty",
        "is_live": True,
        "date": "2026-07-13",
        "locations": [],
        "fallback_locations": [{"location": "静态参考", "area_id": "43"}],
    }

    tool._prepare_delivery_result(result)
    next_commands = build_next_commands(inspect_cli_command("library locations"), result)

    assert "开放状态" in result["reply_text"]
    assert "现场" not in result["reply_text"]
    assert "library reserve" not in " ".join(next_commands)


def test_static_library_fallback_is_marked_non_live_and_stays_within_budget() -> None:
    import json

    tool = HenuCliSafe.__new__(HenuCliSafe)
    locations = [
        {"location": f"静态区域-{index}", "area_id": str(index), "source": "static_fallback"}
        for index in range(45)
    ]
    result = {
        "success": False,
        "error_code": "auth_required",
        "msg": "未绑定账号，无法获取实时可预约区域；以下仅为非实时静态参考",
        "date": "2026-07-13",
        "locations": locations,
        "fallback_locations": locations,
        "total": 45,
        "returned_count": 45,
        "truncated": False,
        "source": "static_fallback",
        "is_live": False,
    }

    tool._prepare_delivery_result(result)
    tool._normalize_for_qq_delivery(result)

    assert result["success"] is False
    assert result["source"] == "static_fallback"
    assert result["is_live"] is False
    assert len(json.dumps(result, ensure_ascii=False)) <= 2200


def test_other_list_results_receive_a_nonempty_summary() -> None:
    tool = HenuCliSafe.__new__(HenuCliSafe)
    result = {"success": True, "msg": "课程计划生成成功", "plans": [{"name": "方案 A", "status": "可行"}]}

    tool._prepare_delivery_result(result)

    assert "方案 A" in result["reply_text"]


def test_direct_tool_large_lists_keep_a_machine_readable_result_summary() -> None:
    import json

    tool = BaseHenuTool.__new__(BaseHenuTool)
    result = {
        "success": True,
        "msg": "研讨室查询完成",
        "rooms": [{"id": str(index), "name": f"房间-{index}", "status": "可用"} for index in range(195)],
    }

    tool._normalize_for_qq_delivery(result)

    assert result["result_summary"]["rooms"]["total"] == 195
    assert result["result_summary"]["rooms"]["items"][0]["id"] == "0"
    assert len(json.dumps(result, ensure_ascii=False)) <= 2200

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from henu_plugin.cli import build_help_payload, inspect_cli_command  # noqa: E402
from components.cli_tools.henu_cli_safe import HenuCliSafe  # noqa: E402
from components.cli_tools.base import BaseHenuTool  # noqa: E402


def test_root_help_exposes_empty_classroom_and_resource_topics() -> None:
    payload = build_help_payload("")
    commands = "\n".join(payload["commands"])
    examples = "\n".join(payload["examples"])

    assert "empty_classroom" in commands
    assert "resource" in commands
    assert "help empty_classroom" in examples
    assert "help resource" in examples


def test_empty_classroom_parser_forwards_rich_filters() -> None:
    spec = inspect_cli_command(
        "empty_classroom query --classroom-text 十号楼101 --type-code 05 "
        "--min-capacity 80 --keyword 多媒体 --room-id 0001 --ttl-seconds 60 --max-stale-seconds 600"
    )

    assert spec.resolved_tool == "empty_classroom_query"
    assert spec.params["classroom_text"] == "十号楼101"
    assert spec.params["type_code"] == "05"
    assert spec.params["min_capacity"] == 80
    assert spec.params["keyword"] == "多媒体"
    assert spec.params["room_id"] == "0001"
    assert spec.params["ttl_seconds"] == 60
    assert spec.params["max_stale_seconds"] == 600


def test_empty_classroom_help_topic_documents_rich_views() -> None:
    payload = build_help_payload("empty_classroom")
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

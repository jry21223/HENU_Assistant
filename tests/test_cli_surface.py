from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI_SOURCE = (ROOT / "henu_cli.py").read_text(encoding="utf-8")
WRAPPER_SOURCE = (ROOT / "scripts" / "henu_campus_mcp.py").read_text(encoding="utf-8")


def test_agent_cli_exposes_rich_empty_classroom_filters() -> None:
    for option in (
        "--classroom_text",
        "--type_code",
        "--min_capacity",
        "--keyword",
        "--room_id",
        "--ttl_seconds",
        "--max_stale_seconds",
    ):
        assert option in CLI_SOURCE


def test_agent_cli_exposes_resource_id_and_building_code() -> None:
    assert "--resource_id" in CLI_SOURCE
    assert "--building_code" in CLI_SOURCE


def test_skill_wrapper_exports_smart_course_selection() -> None:
    assert "smart_course_selection" in WRAPPER_SOURCE
    assert '"smart_course_selection"' in WRAPPER_SOURCE

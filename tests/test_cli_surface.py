from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from henu_plugin.cli import build_help_payload, inspect_cli_command  # noqa: E402


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

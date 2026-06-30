from __future__ import annotations

from pathlib import Path


WORKSPACE = Path(__file__).resolve().parents[2]
CORE_TOP_LEVEL_FILES = {
    "course_schedule.py",
    "schedule_cleaner.py",
    "secure_storage.py",
    "course_selection.py",
    "course_monitor.py",
    "course_planner.py",
}
PROJECTS = ("mcp-server", "agent-skill", "langbot-plugin")


def test_shared_core_files_are_not_at_project_roots() -> None:
    offenders = [
        f"{project}/{filename}"
        for project in PROJECTS
        for filename in CORE_TOP_LEVEL_FILES
        if (WORKSPACE / project / filename).exists()
    ]

    assert offenders == []


def test_mcp_server_is_facade_sized() -> None:
    root = Path(__file__).resolve().parents[1]
    line_count = len((root / "mcp_server.py").read_text(encoding="utf-8").splitlines())

    assert line_count < 900


def test_root_mcp_server_keeps_public_exports_and_no_submit_boundary() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "mcp_server.py").read_text(encoding="utf-8")

    for name in (
        "setup_account",
        "sync_schedule",
        "schedule_query",
        "library_query",
        "course_selection_submit",
        "empty_classroom_query",
    ):
        assert f"{name} =" in source or f"def {name}(" in source

    assert '"code": "not_implemented"' in source or '"not_implemented"' in source

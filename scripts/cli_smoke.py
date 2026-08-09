#!/usr/bin/env python3
"""Exercise the complete Agent parser and safe end-to-end CLI contracts."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "henu_cli.py"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import henu_cli  # noqa: E402


PARSER_CASES: dict[str, list[str]] = {
    "setup_account": ["setup_account", "--student_id", "2026000000", "--password", "probe"],
    "sync_schedule": ["sync_schedule"],
    "schedule_query": ["schedule_query"],
    "smart_course_selection": ["smart_course_selection"],
    "smart_course_select": ["smart_course_select"],
    "course_selection_query": ["course_selection_query"],
    "course_selection_plan": ["course_selection_plan", "--candidates_json", "[]"],
    "course_selection_submit": ["course_selection_submit"],
    "course_monitor_config": ["course_monitor_config"],
    "course_monitor_once": ["course_monitor_once"],
    "course_monitor_run": ["course_monitor_run"],
    "course_monitor_notify_test": ["course_monitor_notify_test"],
    "library_query": ["library_query"],
    "library_reserve": ["library_reserve"],
    "library_auto_signin": ["library_auto_signin"],
    "library_cancel": ["library_cancel", "--record_id", "probe"],
    "seminar_group": ["seminar_group"],
    "seminar_query": ["seminar_query"],
    "seminar_signin": ["seminar_signin"],
    "seminar_reserve": [
        "seminar_reserve",
        "--area_id",
        "probe",
        "--content",
        "仅用于解析器契约检查，不会提交校园系统",
    ],
    "seminar_cancel": ["seminar_cancel", "--record_id", "probe"],
    "set_calibration_source": [
        "set_calibration_source",
        "--data",
        "probe",
        "--cookie",
        "probe",
    ],
    "system_status": ["system_status"],
    "empty_classroom_query": ["empty_classroom_query"],
    "empty_classroom_sync": [
        "empty_classroom_sync",
        "--term_code",
        "probe",
        "--campus_code",
        "probe",
        "--building_code",
        "probe",
    ],
    "resource_registry_query": ["resource_registry_query"],
    "resource_registry_sync": ["resource_registry_sync"],
    "yunfz_leave_query": ["yunfz_leave_query"],
    "yunfz_signin_query": ["yunfz_signin_query"],
    "yunfz_checksleep_query": ["yunfz_checksleep_query"],
    "yunfz_activity_query": ["yunfz_activity_query"],
    "yunfz_collection_query": ["yunfz_collection_query"],
}


def _run_direct(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *arguments],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _run_isolated(runtime_root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    bootstrap = (
        "import sys\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "from pathlib import Path\n"
        "from henu_mcp.runtime import FixedFilesystemRuntime\n"
        "import henu_cli\n"
        f"sys.argv = [{str(CLI)!r}, *{list(arguments)!r}]\n"
        f"with FixedFilesystemRuntime(Path({str(runtime_root)!r})).activate('cli-smoke'):\n"
        "    henu_cli.main()\n"
    )
    return subprocess.run(
        [sys.executable, "-c", bootstrap],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _json_result(completed: subprocess.CompletedProcess[str], label: str) -> dict[str, Any]:
    if completed.returncode != 0:
        raise AssertionError(f"{label} exited {completed.returncode}: {completed.stderr}")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"{label} did not emit one JSON result: {completed.stdout!r}") from exc
    if not isinstance(payload, dict):
        raise AssertionError(f"{label} emitted a non-object JSON result")
    return payload


def _assert_complete_parser_surface() -> None:
    parser = henu_cli.build_parser()
    subparsers = next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    available = set(subparsers.choices)
    expected = set(PARSER_CASES)
    if available != expected:
        raise AssertionError(
            f"parser smoke cases drifted: missing={sorted(available - expected)}, "
            f"stale={sorted(expected - available)}"
        )
    for command, argv in PARSER_CASES.items():
        parsed = parser.parse_args(argv)
        if parsed.command != command:
            raise AssertionError(f"{command} parsed as {parsed.command!r}")


def main() -> int:
    _assert_complete_parser_surface()

    help_result = _run_direct("--help")
    if help_result.returncode != 0 or "河南大学校园助手" not in help_result.stdout:
        raise AssertionError(f"CLI help failed: {help_result.stderr}")

    invalid = _run_direct("schedule_query", "--view", "not-a-view")
    if invalid.returncode != 2 or "invalid choice" not in invalid.stderr:
        raise AssertionError("argparse invalid-choice contract failed")

    with tempfile.TemporaryDirectory(prefix="henu-agent-cli-smoke-") as temporary:
        runtime_root = Path(temporary)
        status = _json_result(
            _run_isolated(runtime_root, "system_status", "--timezone", "UTC"),
            "system_status",
        )
        if status.get("success") is not True:
            raise AssertionError(f"system_status failed: {status}")
        account = (status.get("account") or {}).get("account") or {}
        if account.get("has_password") is not False:
            raise AssertionError("fresh system_status unexpectedly found credentials")

        no_credentials = _json_result(
            _run_isolated(runtime_root, "library_query", "--view", "current"),
            "library_query without credentials",
        )
        if no_credentials.get("success") is not False:
            raise AssertionError("library_query unexpectedly succeeded without credentials")

        candidates = json.dumps(
            [[{"id": "probe", "name": "Probe", "weekday": 1, "periods": [1]}]],
            separators=(",", ":"),
        )
        plan = _json_result(
            _run_isolated(
                runtime_root,
                "course_selection_plan",
                "--candidates_json",
                candidates,
            ),
            "course_selection_plan",
        )
        if plan.get("success") is not True or plan.get("count") != 1:
            raise AssertionError(f"course_selection_plan contract failed: {plan}")

        submit = _json_result(
            _run_isolated(runtime_root, "course_selection_submit"),
            "course_selection_submit",
        )
        if submit.get("success") is not False or submit.get("code") != "not_implemented":
            raise AssertionError(f"course_selection_submit contract failed: {submit}")

    print(f"Agent CLI smoke passed: {len(PARSER_CASES)} parser commands")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

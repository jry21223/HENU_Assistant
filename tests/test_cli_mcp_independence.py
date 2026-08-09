from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "henu_cli.py"

API_FUNCTIONS = (
    "course_monitor_config",
    "course_monitor_notify_test",
    "course_monitor_once",
    "course_monitor_run",
    "course_selection_plan",
    "course_selection_query",
    "course_selection_submit",
    "empty_classroom_query",
    "empty_classroom_sync",
    "library_auto_signin",
    "library_cancel",
    "library_query",
    "library_reserve",
    "resource_registry_query",
    "resource_registry_sync",
    "schedule_query",
    "seminar_cancel",
    "seminar_group",
    "seminar_query",
    "seminar_reserve",
    "seminar_signin",
    "set_calibration_source",
    "smart_course_select",
    "smart_course_selection",
    "setup_account",
    "sync_schedule",
    "system_status",
    "yunfz_activity_query",
    "yunfz_checksleep_query",
    "yunfz_collection_query",
    "yunfz_leave_query",
    "yunfz_signin_query",
)


def _blocked_mcp_environment(tmp_path: Path) -> dict[str, str]:
    shim_dir = tmp_path / "import-shim"
    shim_dir.mkdir()
    shim_dir.joinpath("sitecustomize.py").write_text(
        f"""
import importlib.abc
import sys
import types


class _BlockMcpAdapter(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "mcp_server" or fullname == "mcp" or fullname.startswith("mcp."):
            raise ModuleNotFoundError(f"blocked MCP adapter import: {{fullname}}")
        return None


sys.meta_path.insert(0, _BlockMcpAdapter())

import henu_mcp

api = types.ModuleType("henu_mcp.api")


def _unavailable(*args, **kwargs):
    return {{"success": False, "msg": "stubbed API operation"}}


for name in {API_FUNCTIONS!r}:
    setattr(api, name, _unavailable)


def system_status(*, timezone="Asia/Shanghai"):
    return {{
        "success": True,
        "msg": "status available without MCP adapter",
        "timezone": timezone,
    }}


api.system_status = system_status
sys.modules["henu_mcp.api"] = api
setattr(henu_mcp, "api", api)
""",
        encoding="utf-8",
    )

    env = os.environ.copy()
    python_path = [str(shim_dir), str(ROOT), str(ROOT / "scripts")]
    if env.get("PYTHONPATH"):
        python_path.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(python_path)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _run_with_blocked_mcp(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        cwd=ROOT,
        env=_blocked_mcp_environment(tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )


def test_help_works_when_mcp_adapter_cannot_be_imported(tmp_path: Path) -> None:
    result = _run_with_blocked_mcp(tmp_path, "--help")

    assert result.returncode == 0, result.stderr
    assert "河南大学校园助手" in result.stdout
    assert "system_status" in result.stdout


def test_system_status_works_when_mcp_adapter_cannot_be_imported(tmp_path: Path) -> None:
    result = _run_with_blocked_mcp(tmp_path, "system_status", "--timezone", "UTC")

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "success": True,
        "msg": "status available without MCP adapter",
        "timezone": "UTC",
    }


def test_parser_works_when_mcp_adapter_cannot_be_imported(tmp_path: Path) -> None:
    code = """
import json
import henu_cli

args = henu_cli.build_parser().parse_args([
    "schedule_query",
    "--view", "day",
    "--target_date", "2026-08-09",
])
print(json.dumps({
    "command": args.command,
    "view": args.view,
    "target_date": args.target_date,
}, sort_keys=True))
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=_blocked_mcp_environment(tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "command": "schedule_query",
        "target_date": "2026-08-09",
        "view": "day",
    }


def test_skill_wrapper_exports_api_without_importing_mcp_adapter(tmp_path: Path) -> None:
    code = """
import json
import henu_campus_mcp

print(json.dumps(sorted(henu_campus_mcp.__all__)))
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=_blocked_mcp_environment(tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == sorted(API_FUNCTIONS)

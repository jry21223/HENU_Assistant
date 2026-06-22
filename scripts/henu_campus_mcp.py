#!/usr/bin/env python3
"""
HENU Campus Skill API Wrapper.
Expose the same functional surface as mcp_server.py for CLI/Skill usage.
"""

from __future__ import annotations

import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from mcp_server import (  # noqa: E402
    course_monitor_config,
    course_monitor_notify_test,
    course_monitor_once,
    course_monitor_run,
    course_selection_plan,
    course_selection_query,
    course_selection_submit,
    empty_classroom_query,
    empty_classroom_sync,
    library_auto_signin,
    library_cancel,
    library_query,
    library_reserve,
    resource_registry_query,
    resource_registry_sync,
    schedule_query,
    seminar_cancel,
    seminar_group,
    seminar_query,
    seminar_reserve,
    seminar_signin,
    set_calibration_source,
    setup_account,
    sync_schedule,
    system_status,
    yunfz_activity_query,
    yunfz_checksleep_query,
    yunfz_collection_query,
    yunfz_leave_query,
    yunfz_signin_query,
)

__all__ = [
    "setup_account",
    "sync_schedule",
    "schedule_query",
    "course_selection_query",
    "course_selection_plan",
    "course_selection_submit",
    "course_monitor_config",
    "course_monitor_once",
    "course_monitor_run",
    "course_monitor_notify_test",
    "empty_classroom_query",
    "empty_classroom_sync",
    "library_query",
    "library_reserve",
    "library_auto_signin",
    "library_cancel",
    "resource_registry_query",
    "resource_registry_sync",
    "seminar_group",
    "seminar_query",
    "seminar_signin",
    "seminar_reserve",
    "seminar_cancel",
    "set_calibration_source",
    "system_status",
    "yunfz_leave_query",
    "yunfz_signin_query",
    "yunfz_checksleep_query",
    "yunfz_activity_query",
    "yunfz_collection_query",
]

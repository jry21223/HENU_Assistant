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
    library_auto_signin,
    library_cancel,
    library_query,
    library_reserve,
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
)

__all__ = [
    "setup_account",
    "sync_schedule",
    "schedule_query",
    "library_query",
    "library_reserve",
    "library_auto_signin",
    "library_cancel",
    "seminar_group",
    "seminar_query",
    "seminar_signin",
    "seminar_reserve",
    "seminar_cancel",
    "set_calibration_source",
    "system_status",
]

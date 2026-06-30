from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


_PATH_ATTRS = (
    "COOKIE_FILE",
    "PROFILE_FILE",
    "OUTPUT_DIR",
    "LIBRARY_COOKIE_FILE",
    "SEMINAR_SIGNIN_TASK_FILE",
    "HEBAO_TOKEN_FILE",
    "CAS_COOKIE_FILE",
    "PERIOD_TIME_FILE",
    "PERIOD_CALIBRATION_STATE_FILE",
    "XIQUEER_REQUEST_FILE",
)


def _target_modules() -> tuple[Any, ...]:
    from henu_mcp.core import course_schedule
    from henu_mcp.tools import server_impl

    return (course_schedule, server_impl)


def snapshot_runtime_paths() -> dict[Any, dict[str, Any]]:
    return {
        module: {
            attr: getattr(module, attr)
            for attr in _PATH_ATTRS
            if hasattr(module, attr)
        }
        for module in _target_modules()
    }


def restore_runtime_paths(snapshot: dict[Any, dict[str, Any]]) -> None:
    for module, values in snapshot.items():
        for attr, value in values.items():
            setattr(module, attr, value)


def set_runtime_paths(
    *,
    xk_cookie_file: Path,
    profile_file: Path,
    output_dir: Path,
    library_cookie_file: Path,
    seminar_signin_task_file: Path,
    hebao_token_file: Path,
    cas_cookie_file: Path,
    period_time_file: Path,
    period_calibration_state_file: Path,
    xiqueer_request_file: Path,
) -> None:
    from henu_mcp.core import course_schedule
    from henu_mcp.tools import server_impl

    course_schedule.COOKIE_FILE = xk_cookie_file
    course_schedule.PROFILE_FILE = profile_file
    course_schedule.OUTPUT_DIR = output_dir

    server_impl.COOKIE_FILE = xk_cookie_file
    server_impl.PROFILE_FILE = profile_file
    server_impl.OUTPUT_DIR = output_dir
    server_impl.LIBRARY_COOKIE_FILE = library_cookie_file
    server_impl.SEMINAR_SIGNIN_TASK_FILE = seminar_signin_task_file
    server_impl.HEBAO_TOKEN_FILE = hebao_token_file
    server_impl.CAS_COOKIE_FILE = cas_cookie_file
    server_impl.PERIOD_TIME_FILE = period_time_file
    server_impl.PERIOD_CALIBRATION_STATE_FILE = period_calibration_state_file
    server_impl.XIQUEER_REQUEST_FILE = xiqueer_request_file


@contextmanager
def activated_runtime_paths(**paths: Path) -> Iterator[None]:
    snapshot = snapshot_runtime_paths()
    set_runtime_paths(**paths)
    try:
        yield
    finally:
        restore_runtime_paths(snapshot)

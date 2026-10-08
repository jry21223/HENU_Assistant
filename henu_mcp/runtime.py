from __future__ import annotations

from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import tempfile
from typing import Any, Iterator, Protocol


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


class RuntimeAdapter(Protocol):
    """Activates the filesystem/runtime state for one execution scope."""

    def activate(self, scope: str) -> AbstractContextManager[None]: ...


@dataclass(frozen=True, slots=True)
class NullRuntimeAdapter:
    """Runtime adapter for the process-wide default paths."""

    @contextmanager
    def activate(self, scope: str) -> Iterator[None]:
        del scope
        yield


@dataclass(frozen=True, slots=True)
class FixedFilesystemRuntime:
    """Bind every stateful tool call to a fixed filesystem root."""

    root: Path

    def _paths(self) -> dict[str, Path]:
        root = self.root.resolve()
        return {
            "xk_cookie_file": root / "henu_cookies.json",
            "profile_file": root / "henu_profile.json",
            "output_dir": root / "output",
            "library_cookie_file": root / "henu_library_cookies.json",
            "seminar_signin_task_file": root / "seminar_signin_tasks.json",
            "hebao_token_file": root / "henu_yunfz_token.json",
            "cas_cookie_file": root / "henu_cas_cookies.json",
            "period_time_file": root / "period_time_config.json",
            "period_calibration_state_file": root / "period_time_calibration_state.json",
            "xiqueer_request_file": root / "xiqueer_period_time_request.json",
        }

    @contextmanager
    def activate(self, scope: str) -> Iterator[None]:
        from campus_core.storage_paths import activated_base_dir

        del scope
        self.root.mkdir(parents=True, exist_ok=True)
        with activated_base_dir(self.root), activated_runtime_paths(**self._paths()):
            with runtime_state_transaction():
                yield


class TemporaryFilesystemRuntime:
    """Own an isolated temporary runtime root with one directory per scope."""

    def __init__(self, *, parent: Path | None = None) -> None:
        if parent is not None:
            parent.mkdir(parents=True, exist_ok=True)
        self._temporary = tempfile.TemporaryDirectory(
            prefix="henu-runtime-",
            dir=str(parent) if parent is not None else None,
        )
        self.root = Path(self._temporary.name)

    def root_for_scope(self, scope: str) -> Path:
        text = str(scope or "default")
        slug = re.sub(r"[^0-9A-Za-z._-]+", "_", text).strip("._-") or "scope"
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
        return self.root / f"{slug[:48]}-{digest}"

    @contextmanager
    def activate(self, scope: str) -> Iterator[None]:
        with FixedFilesystemRuntime(self.root_for_scope(scope)).activate(scope):
            yield

    def close(self) -> None:
        self._temporary.cleanup()

    def __enter__(self) -> "TemporaryFilesystemRuntime":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


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


@contextmanager
def runtime_state_transaction() -> Iterator[None]:
    """Lock the active runtime's complete stateful operation domain."""
    from campus_core.atomic_io import file_transaction
    from henu_mcp.tools import server_impl

    lock_target = Path(server_impl.PROFILE_FILE).resolve().parent / ".henu-runtime-state"
    with file_transaction(lock_target):
        yield

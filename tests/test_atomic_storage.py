from __future__ import annotations

import os
import gc
from pathlib import Path

import pytest


def test_transient_transaction_locks_do_not_leak(tmp_path: Path) -> None:
    from campus_core import atomic_io

    baseline = len(atomic_io._PROCESS_LOCKS)
    for index in range(200):
        with atomic_io.file_transaction(tmp_path / str(index)):
            pass
    gc.collect()

    assert len(atomic_io._PROCESS_LOCKS) <= baseline + 1


def test_atomic_write_failure_preserves_previous_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from campus_core import atomic_io

    destination = tmp_path / "profile.json"
    destination.write_text("old", encoding="utf-8")

    def fail_replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(atomic_io.os, "replace", fail_replace)

    with pytest.raises(OSError, match="simulated replace failure"):
        atomic_io.atomic_write_text(destination, "new")

    assert destination.read_text(encoding="utf-8") == "old"
    assert list(tmp_path.glob(".profile.json.*.tmp")) == []


def test_profile_cookie_seminar_monitor_and_registry_use_durable_same_dir_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from campus_core import atomic_io
    from campus_core.resource_registry import storage as registry_storage
    from henu_mcp.core import course_monitor, course_schedule, secure_storage
    from henu_mcp.tools import server_impl

    real_replace = atomic_io.os.replace
    real_fsync = atomic_io.os.fsync
    replacements: list[tuple[Path, Path]] = []
    fsync_calls: list[int] = []

    def recording_replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
        replacements.append((Path(source), Path(target)))
        real_replace(source, target)

    def recording_fsync(fd: int) -> None:
        fsync_calls.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(atomic_io.os, "replace", recording_replace)
    monkeypatch.setattr(atomic_io.os, "fsync", recording_fsync)

    profile_path = tmp_path / "profile" / "profile.json"
    cookie_path = tmp_path / "cookie" / "cookies.json"
    seminar_path = tmp_path / "seminar" / "tasks.json"
    monitor_dir = tmp_path / "monitor"
    registry_dir = tmp_path / "registry"

    secure_storage.save_encrypted_profile(profile_path, {"student_id": "1"})
    course_schedule.save_json(cookie_path, {"route": "safe"})
    monkeypatch.setattr(server_impl, "SEMINAR_SIGNIN_TASK_FILE", seminar_path)
    server_impl._save_seminar_signin_tasks([{"task_id": "task-1"}])
    monkeypatch.setattr(course_schedule, "OUTPUT_DIR", monitor_dir)
    course_monitor.save_monitor_config({"targets": []}, merge=False)
    monkeypatch.setattr(registry_storage, "get_resource_registry_dir", lambda: registry_dir)
    registry_storage.save_resources({"room-1": {"resourceId": "room-1"}})

    expected_targets = {
        profile_path,
        cookie_path,
        seminar_path,
        monitor_dir / "course_monitor_config.json",
        registry_dir / "registry_state.json",
    }
    actual_targets = {target for _, target in replacements}
    assert expected_targets <= actual_targets
    for source, target in replacements:
        assert source.parent == target.parent
        assert source.name.startswith(f".{target.name}.")
        assert source.suffix == ".tmp"
    assert len(fsync_calls) >= len(replacements)

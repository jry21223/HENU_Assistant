from __future__ import annotations

import multiprocessing
import os
import time
from pathlib import Path

from campus_core.empty_classroom.lock import FileLock


def _hold_file_lock(lock_path: str, ready, release, crash: bool) -> None:
    lock = FileLock(Path(lock_path), timeout=2.0, stale_after=0.05)
    if not lock.acquire():
        os._exit(2)
    ready.set()
    if crash:
        os._exit(0)
    release.wait(5)
    lock.release()


def test_crashed_owner_is_released_by_the_kernel(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    lock_path = tmp_path / "crash.lock"
    process = context.Process(
        target=_hold_file_lock,
        args=(str(lock_path), ready, release, True),
    )
    process.start()
    assert ready.wait(5)
    process.join(5)
    assert process.exitcode == 0

    recovered = FileLock(lock_path, timeout=0.5)
    assert recovered.acquire() is True
    recovered.release()


def test_live_owner_is_not_stolen_or_released_by_a_contender(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    lock_path = tmp_path / "live.lock"
    process = context.Process(
        target=_hold_file_lock,
        args=(str(lock_path), ready, release, False),
    )
    process.start()
    assert ready.wait(5)
    time.sleep(0.1)

    contender = FileLock(lock_path, timeout=0.15, stale_after=0.05)
    assert contender.acquire() is False
    contender.release()
    still_blocked = FileLock(lock_path, timeout=0.15)
    assert still_blocked.acquire() is False

    release.set()
    process.join(5)
    assert process.exitcode == 0

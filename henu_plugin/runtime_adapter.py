"""Runtime adapter for one materialized LangBot Storage transaction."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from campus_core.storage_paths import activated_base_dir
from henu_mcp.runtime import activated_runtime_paths, runtime_state_transaction
from henu_plugin.storage_adapter import (
    SHARED_CALIBRATION_FILE,
    SHARED_PERIOD_TIME_FILE,
    UserStoragePaths,
)


@dataclass(frozen=True, slots=True)
class LangBotStorageRuntime:
    """Activate the file facade backed by one LangBot Storage staging tree."""

    base_dir: Path
    paths: UserStoragePaths

    @contextmanager
    def activate(self, scope: str) -> Iterator[None]:
        del scope
        shared_dir = self.paths.shared_data_dir
        with activated_base_dir(self.base_dir), activated_runtime_paths(
            xk_cookie_file=self.paths.xk_cookie_file,
            profile_file=self.paths.profile_file,
            output_dir=self.paths.output_dir,
            library_cookie_file=self.paths.library_cookie_file,
            seminar_signin_task_file=self.paths.seminar_signin_task_file,
            hebao_token_file=self.paths.yunfz_token_file,
            cas_cookie_file=self.paths.cas_cookie_file,
            period_time_file=shared_dir / SHARED_PERIOD_TIME_FILE,
            period_calibration_state_file=shared_dir / SHARED_CALIBRATION_FILE,
            xiqueer_request_file=self.paths.xiqueer_request_file,
        ):
            with runtime_state_transaction():
                yield


__all__ = ["LangBotStorageRuntime"]

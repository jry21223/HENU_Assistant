from __future__ import annotations

import json

import pytest

from henu_mcp.runtime import activated_runtime_paths
from henu_mcp.tools import server_impl as api


@pytest.fixture
def account_files(tmp_path):
    paths = {
        "xk_cookie_file": tmp_path / "xk.json",
        "profile_file": tmp_path / "profile.json",
        "output_dir": tmp_path / "output",
        "library_cookie_file": tmp_path / "library.json",
        "seminar_signin_task_file": tmp_path / "tasks.json",
        "hebao_token_file": tmp_path / "hebao.json",
        "cas_cookie_file": tmp_path / "cas.json",
        "period_time_file": tmp_path / "period.json",
        "period_calibration_state_file": tmp_path / "calibration.json",
        "xiqueer_request_file": tmp_path / "xiqueer.json",
    }
    paths["output_dir"].mkdir()
    with activated_runtime_paths(**paths):
        api.save_account("old-student", "offline-old-password", verify_login=False)
        paths["xk_cookie_file"].write_text(json.dumps({"JSESSIONID": "old-session"}))
        yield paths

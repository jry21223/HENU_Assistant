from __future__ import annotations

import json
import sys
from io import StringIO
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import henu_cli
from henu_mcp.tools import server_impl


def _run_cli(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *args: str,
) -> dict:
    monkeypatch.setattr(sys, "argv", ["henu_cli.py", *args])
    henu_cli.main()
    return json.loads(capsys.readouterr().out)


def test_preferences_set_get_and_explicit_clear(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(server_impl, "PROFILE_FILE", tmp_path / "henu_profile.json")

    saved = _run_cli(
        monkeypatch,
        capsys,
        "preferences",
        "set",
        "--library_location",
        "金明七层南自习室",
        "--library_seat_no",
        "001",
    )
    assert saved["success"] is True
    assert saved["preferences"] == {
        "library_location": "金明七层南自习室",
        "library_seat_no": "001",
    }

    shown = _run_cli(monkeypatch, capsys, "preferences", "get")
    assert shown["preferences"] == saved["preferences"]

    cleared = _run_cli(
        monkeypatch,
        capsys,
        "preferences",
        "clear",
        "--library_location",
        "--library_seat_no",
    )
    assert cleared["success"] is True
    assert cleared["preferences"] == {
        "library_location": "",
        "library_seat_no": "",
    }


def test_preferences_clear_requires_an_explicit_field(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(server_impl, "PROFILE_FILE", tmp_path / "henu_profile.json")

    result = _run_cli(monkeypatch, capsys, "preferences", "clear")

    assert result["success"] is False
    assert result["code"] == "missing_params"
    assert result["missing_params"] == ["library_location", "library_seat_no"]


def test_library_reserve_cli_does_not_invent_date_or_start_time(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    captured: dict = {}

    def fake_library_reserve(**kwargs):
        captured.update(kwargs)
        return {"success": False, "code": "missing_params"}

    monkeypatch.setattr(henu_cli, "library_reserve", fake_library_reserve)

    _run_cli(
        monkeypatch,
        capsys,
        "library_reserve",
        "--location",
        "金明七层南自习室",
        "--seat_no",
        "001",
    )

    assert captured["target_date"] == ""
    assert captured["preferred_time"] == ""
    assert captured["preferred_end_time"] == ""
    assert captured["end_policy"] == "ask"


def test_library_reserve_missing_authorization_fails_before_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        server_impl,
        "HenuCampusBot",
        pytest.fail,
    )

    result = server_impl.library_reserve(location="金明七层南自习室", seat_no="001")

    assert result["success"] is False
    assert result["code"] == "missing_params"
    assert result["missing_params"] == ["target_date", "preferred_time", "preferred_end_time"]


def test_library_reserve_requires_end_time_or_explicit_server_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server_impl, "HenuCampusBot", pytest.fail)

    result = server_impl.library_reserve(
        location="金明七层南自习室",
        seat_no="001",
        target_date="2099-01-02",
        preferred_time="08:00",
    )

    assert result["success"] is False
    assert result["code"] == "missing_params"
    assert result["missing_params"] == ["preferred_end_time"]
    assert "server_full_slot" in result["msg"]


def test_library_reserve_server_full_slot_is_explicit_and_keeps_end_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    class Bot:
        def reserve(self, *args, **kwargs):
            captured.update(kwargs)
            return {"success": True, "msg": "offline fixture"}

        def get_cookies(self):
            return {}

    monkeypatch.setattr(server_impl, "HenuCampusBot", object())
    monkeypatch.setattr(
        server_impl,
        "_effective_profile",
        lambda: {"student_id": "fixture-student", "password": "placeholder-secret"},
    )
    monkeypatch.setattr(server_impl, "_build_library_bot", lambda *_args, **_kwargs: Bot())
    monkeypatch.setattr(server_impl, "_save_library_cookies", lambda *_args, **_kwargs: None)

    result = server_impl.library_reserve(
        location="金明七层南自习室",
        seat_no="001",
        target_date="2099-01-02",
        preferred_time="08:00",
        end_policy="server_full_slot",
    )

    assert result["success"] is True
    assert captured["preferred_end_time"] == ""


def test_setup_account_help_documents_login_format(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["henu_cli.py", "setup_account", "--help"])

    with pytest.raises(SystemExit) as exc_info:
        henu_cli.main()

    assert exc_info.value.code == 0
    help_text = capsys.readouterr().out
    assert "统一身份认证账号" in help_text
    assert "特殊字符" in help_text
    assert "默认安全读取密码" in help_text
    assert "--password-stdin" in help_text
    assert "可能暴露在 shell history/进程参数中" in help_text


def test_setup_account_missing_credentials_explains_expected_values() -> None:
    result = server_impl.save_account(student_id="", password="", verify_login=False)

    assert result["success"] is False
    assert "统一身份认证账号" in result["msg"]
    assert "统一身份认证密码" in result["msg"]


def test_setup_account_getpass_keeps_password_out_of_argv(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: dict = {}
    placeholder = "placeholder-secret"
    monkeypatch.setattr(henu_cli.getpass, "getpass", lambda _prompt: placeholder)
    monkeypatch.setattr(
        henu_cli,
        "setup_account",
        lambda **kwargs: captured.update(kwargs) or {"success": True},
    )

    result = _run_cli(monkeypatch, capsys, "setup_account", "--student_id", "2024000000")

    assert result["success"] is True
    assert captured["password"] == placeholder
    assert placeholder not in " ".join(sys.argv)


def test_setup_account_password_stdin_reads_one_line(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: dict = {}
    monkeypatch.setattr(sys, "stdin", StringIO("placeholder-secret\nignored\n"))
    monkeypatch.setattr(
        henu_cli,
        "setup_account",
        lambda **kwargs: captured.update(kwargs) or {"success": True},
    )

    _run_cli(
        monkeypatch,
        capsys,
        "setup_account",
        "--student_id",
        "2024000000",
        "--password-stdin",
    )

    assert captured["password"] == "placeholder-secret"


def test_setup_account_legacy_password_argument_warns(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(henu_cli, "setup_account", lambda **_kwargs: {"success": True})
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "henu_cli.py",
            "setup_account",
            "--student_id",
            "2024000000",
            "--password",
            "placeholder-secret",
        ],
    )

    henu_cli.main()

    captured = capsys.readouterr()
    assert json.loads(captured.out)["success"] is True
    assert "shell history" in captured.err


def test_setup_account_without_verification_never_calibrates_or_claims_verified(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server_impl, "PROFILE_FILE", tmp_path / "henu_profile.json")
    monkeypatch.setattr(
        server_impl,
        "auto_calibrate_period_time",
        lambda **_kwargs: pytest.fail("calibration must not run without verified login"),
    )

    result = server_impl.setup_account(
        student_id="2024000000",
        password="placeholder-secret",
        verify_login=False,
        calibrate_period_time=True,
    )

    assert result["success"] is True
    assert result["verified"] is False
    assert result["msg"] == "账号已保存，尚未验证登录"
    assert result["period_time_calibration"]["skipped"] is True

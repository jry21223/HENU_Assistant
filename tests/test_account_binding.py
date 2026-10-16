from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

import pytest
import requests

from henu_mcp.tools import server_impl as api


def school_http(
    monkeypatch, *, subject="new-student", reject=False, lose_context=False
):
    state = {"signed_in": False, "context_reads": 0}
    calls = []

    def request(session, method, url, **kwargs):
        calls.append((method.upper(), url))
        parsed = urlparse(url)
        if "SetMainInfo.jsp" in parsed.path:
            if state["signed_in"]:
                state["context_reads"] += 1
                if lose_context and state["context_reads"] > 1:
                    raise requests.ConnectionError("offline context response lost")
            who = (
                "old-student"
                if session.cookies.get("JSESSIONID") == "old-session"
                else (subject if state["signed_in"] else "")
            )
            body = f"var _loginid = '{who}'; var _usertype = 'STU';"
        elif parsed.hostname == "ids.henu.edu.cn":
            if method.upper() == "POST":
                if reject:
                    body = "用户名或密码错误"
                else:
                    state["signed_in"] = True
                    session.cookies.set(
                        "JSESSIONID", "new-session", domain="xk.henu.edu.cn"
                    )
                    body = "signed in"
            else:
                body = (
                    '<input name="execution" value="e1">'
                    '<input id="pwdEncryptSalt" value="0123456789abcdef">'
                )
        else:
            pytest.fail(f"unexpected school request: {method} {url}")
        response = requests.Response()
        response.status_code = 200
        response.url = url
        response._content = body.encode()
        return response

    monkeypatch.setattr(requests.Session, "request", request)
    return calls


def test_saved_session_cannot_verify_a_new_password(account_files, monkeypatch):
    school_http(monkeypatch, reject=True)

    result = api.save_account("new-student", "offline-wrong-password")

    assert result["success"] is False
    assert api.show_account()["account"]["student_id"] == "old-student"


def test_authenticated_subject_must_match_before_replacing_binding(
    account_files, monkeypatch
):
    school_http(monkeypatch, subject="another-student")
    old_session = account_files["xk_cookie_file"].read_bytes()

    result = api.save_account("new-student", "offline-new-password")

    assert result["success"] is False
    assert result["auth"]["error_code"] == "account_mismatch"
    assert api.show_account()["account"]["student_id"] == "old-student"
    assert account_files["xk_cookie_file"].read_bytes() == old_session


def test_storage_failure_restores_the_previous_binding(account_files, monkeypatch):
    school_http(monkeypatch)
    original = {
        key: account_files[key].read_bytes()
        for key in ("profile_file", "xk_cookie_file")
    }
    write_text = Path.write_text

    def fail_cookie_write(path, *args, **kwargs):
        if path == account_files["xk_cookie_file"]:
            raise OSError("offline disk failure")
        return write_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_cookie_write)

    result = api.save_account("new-student", "offline-new-password")

    assert result["success"] is False
    assert result["error_code"] == "binding_persist_failed"
    assert api.show_account()["account"]["student_id"] == "old-student"
    for key, content in original.items():
        assert account_files[key].read_bytes() == content


def test_verified_rebinding_replaces_account_scoped_sessions_and_defaults(
    account_files, monkeypatch
):
    api.save_account(
        "old-student",
        "offline-old-password",
        verify_login=False,
        library_location="old-library",
        library_seat_no="009",
    )
    for key in (
        "library_cookie_file",
        "cas_cookie_file",
        "hebao_token_file",
        "seminar_signin_task_file",
    ):
        account_files[key].write_text('{"fixture":"old-account"}')
    stale_schedule = account_files["output_dir"] / "schedule_clean_old.json"
    stale_schedule.write_text('{"schedule":{"old":"account"}}')
    calls = school_http(monkeypatch)

    result = api.save_account("new-student", "offline-new-password")

    assert result["success"] is True
    assert result["verified"] is True
    assert result["context"]["login_id"] == "new-student"
    assert result["binding_changed"] is True
    assert api.show_account()["account"]["library_default_seat_no"] == ""
    assert api.show_account()["account"]["library_default_location"] == ""
    for key in ("library_cookie_file", "cas_cookie_file", "hebao_token_file"):
        assert json.loads(account_files[key].read_text()) == {}
    assert json.loads(account_files["seminar_signin_task_file"].read_text()) == {
        "tasks": []
    }
    assert not stale_schedule.exists()
    assert (
        json.loads(account_files["xk_cookie_file"].read_text())["JSESSIONID"]
        == "new-session"
    )
    assert sum(method == "POST" for method, _ in calls) == 1


def test_unverified_setup_does_not_contact_school_or_calibrate(
    account_files, monkeypatch
):
    def forbidden(*_args, **_kwargs):
        pytest.fail("unverified save must not contact the school")

    monkeypatch.setattr(requests.Session, "request", forbidden)

    result = api.setup_account("new-student", "offline-placeholder", verify_login=False)

    assert result["success"] is True
    assert result["verified"] is False
    assert result["auth"]["mode"] == ""
    assert result["period_time_calibration"]["skipped"] is True
    assert json.loads(account_files["xk_cookie_file"].read_text()) == {}


def test_interrupted_subject_check_keeps_previous_binding(account_files, monkeypatch):
    school_http(monkeypatch, lose_context=True)
    original = account_files["profile_file"].read_bytes()

    result = api.save_account("new-student", "offline-new-password")

    assert result["success"] is False
    assert result["verified"] is False
    assert account_files["profile_file"].read_bytes() == original

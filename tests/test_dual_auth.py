from __future__ import annotations

from henu_mcp.core.course_schedule import HenuXkClient
from henu_mcp.core.kingo_auth import AuthResult


def _client(monkeypatch) -> HenuXkClient:
    client = HenuXkClient("student", "password")
    monkeypatch.setattr(client, "_check_logged_in", lambda: False)
    return client


def test_ids_success_never_calls_kingo(monkeypatch) -> None:
    client = _client(monkeypatch)
    monkeypatch.setattr(
        client,
        "_login_via_ids",
        lambda: AuthResult(True, mode="ids_cas", message="ok"),
    )
    calls = 0

    def kingo(**_kwargs):
        nonlocal calls
        calls += 1
        return AuthResult(True, mode="xk_kingo")

    monkeypatch.setattr("henu_mcp.core.course_schedule.login_via_kingo", kingo)

    assert client.login() is True
    assert calls == 0
    assert client.get_auth_info()["mode"] == "ids_cas"


def test_ids_failure_calls_kingo_once_and_reports_degraded_mode(monkeypatch) -> None:
    client = _client(monkeypatch)
    monkeypatch.setattr(
        client,
        "_login_via_ids",
        lambda: AuthResult(False, error_code="service_error", message="IDS failed"),
    )
    calls = 0

    def kingo(**_kwargs):
        nonlocal calls
        calls += 1
        return AuthResult(True, mode="xk_kingo", degraded=True, message="ok")

    monkeypatch.setattr("henu_mcp.core.course_schedule.login_via_kingo", kingo)

    assert client.login() is True
    assert calls == 1
    auth = client.get_auth_info()
    assert auth["mode"] == "xk_kingo"
    assert auth["degraded"] is True
    assert auth["ids_error_code"] == "service_error"
    assert client.get_cookies()["_auth_mode"] == "xk_kingo"


def test_both_failures_are_combined_without_response_bodies(monkeypatch) -> None:
    client = _client(monkeypatch)
    monkeypatch.setattr(
        client,
        "_login_via_ids",
        lambda: AuthResult(False, error_code="network_error", message="IDS network failed"),
    )
    monkeypatch.setattr(
        "henu_mcp.core.course_schedule.login_via_kingo",
        lambda **_kwargs: AuthResult(
            False,
            error_code="captcha_required",
            message="xk captcha required",
        ),
    )

    assert client.login() is False
    auth = client.get_auth_info()
    assert auth["error_code"] == "captcha_required"
    assert auth["message"] == "IDS: IDS network failed；xk: xk captcha required"
    assert "password" not in auth["message"].lower()


def test_kingo_persistence_does_not_write_cas_jar(monkeypatch) -> None:
    from henu_mcp.tools import server_impl

    saved: list[dict] = []
    cas_saved: list[dict] = []
    monkeypatch.setattr(server_impl, "save_json", lambda _path, data: saved.append(data))
    monkeypatch.setattr(server_impl, "_save_cas_cookies", lambda data: cas_saved.append(data))

    server_impl._save_xk_cookies(
        {
            "JSESSIONID": "xk-session",
            "route": "xk-route",
            "CASTGC": "must-not-persist",
            "_auth_mode": "xk_kingo",
        }
    )

    assert saved == [
        {
            "JSESSIONID": "xk-session",
            "route": "xk-route",
            "_auth_mode": "xk_kingo",
        }
    ]
    assert cas_saved == []

from __future__ import annotations

import pytest

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


def test_ids_captcha_requirement_fails_closed_without_kingo(monkeypatch) -> None:
    client = _client(monkeypatch)
    monkeypatch.setattr(
        client,
        "_login_via_ids",
        lambda: AuthResult(
            False,
            error_code="captcha_required",
            message="IDS 统一认证需要验证码或额外校验",
        ),
    )
    calls = 0

    def kingo(**_kwargs):
        nonlocal calls
        calls += 1
        return AuthResult(True, mode="xk_kingo")

    monkeypatch.setattr("henu_mcp.core.course_schedule.login_via_kingo", kingo)

    assert client.login() is False
    assert calls == 0
    auth = client.get_auth_info()
    assert auth["error_code"] == "captcha_required"
    assert "请先在浏览器完成验证" in auth["message"]
    assert "勿连续尝试" in auth["message"]


@pytest.mark.parametrize(
    ("error_code", "message"),
    [
        ("additional_verification_required", "IDS 需要额外校验"),
        ("account_locked", "IDS 账号已锁定"),
        ("rate_limited", "IDS 尝试次数过多"),
        ("invalid_credentials", "IDS 拒绝了账号或密码"),
        ("auth_state_unknown", "IDS 提交后状态未知"),
        ("service_error", "IDS 返回未识别错误"),
    ],
)
def test_ids_auth_risks_fail_closed_without_kingo(
    monkeypatch,
    error_code,
    message,
) -> None:
    client = _client(monkeypatch)
    monkeypatch.setattr(
        client,
        "_login_via_ids",
        lambda: AuthResult(False, error_code=error_code, message=message),
    )
    calls = 0

    def kingo(**_kwargs):
        nonlocal calls
        calls += 1
        return AuthResult(True, mode="xk_kingo")

    monkeypatch.setattr("henu_mcp.core.course_schedule.login_via_kingo", kingo)

    assert client.login() is False
    assert calls == 0
    assert client.get_auth_info()["error_code"] == error_code


def test_ids_pre_auth_unavailable_calls_kingo_once_and_reports_degraded_mode(monkeypatch) -> None:
    client = _client(monkeypatch)
    monkeypatch.setattr(
        client,
        "_login_via_ids",
        lambda: AuthResult(False, error_code="ids_unavailable", message="IDS unavailable before auth"),
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
    assert auth["ids_error_code"] == "ids_unavailable"
    assert client.get_cookies()["_auth_mode"] == "xk_kingo"


def test_both_failures_are_combined_without_response_bodies(monkeypatch) -> None:
    client = _client(monkeypatch)
    monkeypatch.setattr(
        client,
        "_login_via_ids",
        lambda: AuthResult(False, error_code="ids_unavailable", message="IDS network failed before auth"),
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
    assert auth["message"] == "IDS: IDS network failed before auth；xk: xk captcha required"
    assert "password" not in auth["message"].lower()


@pytest.mark.parametrize(
    ("text", "status_code", "expected_code"),
    [
        ("请输入验证码", 200, "captcha_required"),
        ("请先完成二次验证", 200, "additional_verification_required"),
        ("账号已被锁定", 200, "account_locked"),
        ("尝试次数过多，请稍后重试", 200, "rate_limited"),
        ("用户名或密码错误", 200, "invalid_credentials"),
        ("服务维护中", 503, "ids_unavailable"),
    ],
)
def test_ids_error_classifies_auth_risks(text, status_code, expected_code) -> None:
    assert HenuXkClient._ids_error(text, status_code)[0] == expected_code


def test_ids_entry_network_failure_is_explicitly_safe_to_fallback(monkeypatch) -> None:
    client = _client(monkeypatch)

    def fail_before_auth(*_args, **_kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(client.session, "get", fail_before_auth)

    result = client._login_via_ids()

    assert result.error_code == "ids_unavailable"


def test_ids_post_failure_marks_auth_state_unknown(monkeypatch) -> None:
    client = _client(monkeypatch)

    class Response:
        status_code = 200
        url = "https://ids.example/login"
        content = (
            b'<input name="execution" value="e1">'
            b'<input id="pwdEncryptSalt" value="0123456789abcdef">'
        )

    monkeypatch.setattr(client.session, "get", lambda *_args, **_kwargs: Response())

    def fail_after_auth(*_args, **_kwargs):
        raise OSError("connection reset")

    monkeypatch.setattr(client.session, "post", fail_after_auth)

    result = client._login_via_ids()

    assert result.error_code == "auth_state_unknown"


def test_ids_post_service_unavailable_is_not_safe_to_fallback(monkeypatch) -> None:
    client = _client(monkeypatch)

    class LoginPage:
        status_code = 200
        url = "https://ids.example/login"
        content = (
            b'<input name="execution" value="e1">'
            b'<input id="pwdEncryptSalt" value="0123456789abcdef">'
        )

    class ServiceUnavailable:
        status_code = 503
        url = "https://ids.example/login"
        content = "服务维护中".encode()

    monkeypatch.setattr(client.session, "get", lambda *_args, **_kwargs: LoginPage())
    monkeypatch.setattr(client.session, "post", lambda *_args, **_kwargs: ServiceUnavailable())

    result = client._login_via_ids()

    assert result.error_code == "auth_state_unknown"


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

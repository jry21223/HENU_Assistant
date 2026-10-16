from __future__ import annotations

from urllib.parse import urlparse

import requests
import pytest

from henu_mcp.core.course_schedule import HenuXkClient


def install_school_http(monkeypatch, *, page="请输入验证码", status=200,
                        post_result=None, unavailable=False, authenticated=False,
                        second_get_error=False):
    """Offline adapter at the school HTTP seam, with an observable request log."""
    calls = []
    state = {"authenticated": authenticated, "ids_gets": 0}
    form = ('<input name="execution" value="e1">'
            '<input id="pwdEncryptSalt" value="0123456789abcdef">')

    def request(_session, method, url, **_kwargs):
        calls.append((method.upper(), url))
        parsed = urlparse(url)
        body, code = "", 200
        if "SetMainInfo.jsp" in parsed.path:
            body = "var _loginid = 'student';" if state["authenticated"] else ""
        elif parsed.hostname == "ids.henu.edu.cn":
            if unavailable:
                raise requests.ConnectionError("offline IDS unavailable")
            if method.upper() == "POST":
                if isinstance(post_result, Exception):
                    raise post_result
                body, code = post_result or ("用户名或密码错误", 200)
                if body == "signed_in":
                    state["authenticated"] = True
            else:
                state["ids_gets"] += 1
                if second_get_error and state["ids_gets"] > 1:
                    raise requests.ConnectionError("later IDS request failed")
                body, code = (form if page == "form" else page), status
        elif parsed.path.endswith("/cas/login.action"):
            body = ('<script>var _ssessionid="SESSION";</script>'
                    '<input id="hid_flag" value="1"><input id="randnumber" value="">')
        elif "getTempDeskey" in url:
            body = "93446178378580382645177"
        elif "getTempNowtime" in url:
            body = "1700000000000"
        elif parsed.path.endswith("/cas/logon.action"):
            state["authenticated"] = True
            body = '{"status":200,"result":"/frame/homes.action"}'
        response = requests.Response()
        response.status_code = code
        response.url = url
        response._content = body.encode()
        return response

    monkeypatch.setattr(requests.Session, "request", request)
    return calls


def test_ids_challenge_never_retries_through_kingo(monkeypatch):
    calls = install_school_http(monkeypatch)
    client = HenuXkClient("student", "offline-test-password")

    assert client.login() is False

    assert client.get_auth_info()["error_code"] == "captcha_required"
    assert not any("/cas/login.action" in url for _, url in calls)
    assert not any(method == "POST" for method, _ in calls)


@pytest.mark.parametrize("outcome", [
    requests.ReadTimeout("offline response lost"),
    ("服务维护中", 503),
])
def test_submitted_ids_credentials_with_unknown_outcome_are_not_replayed(monkeypatch, outcome):
    calls = install_school_http(monkeypatch, page="form", post_result=outcome)
    client = HenuXkClient("student", "offline-test-password")

    assert client.login() is False

    assert client.get_auth_info()["error_code"] == "auth_state_unknown"
    assert sum(method == "POST" for method, _ in calls) == 1
    assert not any("/cas/login.action" in url for _, url in calls)


@pytest.mark.parametrize(("page", "status", "expected"), [
    ("需要完成额外校验", 200, "additional_verification_required"),
    ("账号已被锁定", 200, "account_locked"),
    ("Too Many Requests", 429, "rate_limited"),
    ("Forbidden", 403, "access_denied"),
    ("form", 429, "rate_limited"),
    ("用户名或密码错误", 200, "invalid_credentials"),
    ("unrecognized response", 200, "protocol_error"),
])
def test_ids_rejections_preserve_reason_and_never_try_alternative_login(monkeypatch, page, status, expected):
    calls = install_school_http(monkeypatch, page=page, status=status)
    client = HenuXkClient("student", "offline-test-password")

    assert client.login() is False

    assert client.get_auth_info()["error_code"] == expected
    assert not any(method == "POST" for method, _ in calls)
    assert not any("/cas/login.action" in url for _, url in calls)


@pytest.mark.parametrize(("unavailable", "status"), [(True, 200), (False, 503)])
def test_ids_unavailable_before_credentials_can_still_use_kingo(monkeypatch, unavailable, status):
    calls = install_school_http(monkeypatch, page="form", unavailable=unavailable, status=status)
    client = HenuXkClient("student", "offline-test-password")

    assert client.login() is True

    assert client.get_auth_info()["mode"] == "xk_kingo"
    assert client.get_auth_info()["degraded"] is True
    assert sum(method == "POST" for method, _ in calls) == 1


def test_successful_ids_login_does_not_need_kingo(monkeypatch):
    calls = install_school_http(monkeypatch, page="form", post_result=("signed_in", 200))
    client = HenuXkClient("student", "offline-test-password")

    assert client.login() is True
    assert client.get_auth_info()["mode"] == "ids_cas"
    assert not any("/cas/login.action" in url for _, url in calls)


def test_existing_ids_session_is_preserved(monkeypatch):
    calls = install_school_http(monkeypatch, authenticated=True)
    client = HenuXkClient("student", "offline-test-password", {"_auth_mode": "ids_cas"})

    assert client.login() is True
    assert client.get_auth_info()["mode"] == "ids_cas"
    assert len(calls) == 1


@pytest.mark.parametrize(("page", "status", "expected"), [
    ("请输入验证码", 200, "captcha_required"),
    ("Too Many Requests", 429, "rate_limited"),
    ("unknown IDS response", 200, "protocol_error"),
])
def test_first_ids_rejection_cannot_be_overwritten_by_a_later_network_failure(monkeypatch, page, status, expected):
    calls = install_school_http(
        monkeypatch, page=page, status=status, second_get_error=True,
    )
    client = HenuXkClient("student", "offline-test-password")

    assert client.login() is False
    assert client.get_auth_info()["error_code"] == expected
    assert not any(method == "POST" for method, _ in calls)
    assert not any("/cas/login.action" in url for _, url in calls)


@pytest.mark.parametrize(("message", "expected"), [
    ("账号已被锁定", "account_locked"),
    ("尝试次数过多", "rate_limited"),
    ("需要完成额外校验", "additional_verification_required"),
    ("请输入验证码", "captcha_required"),
])
def test_explicit_rejection_on_a_valid_login_form_stops_before_credentials(monkeypatch, message, expected):
    form = ('<input name="execution" value="e1">'
            '<input id="pwdEncryptSalt" value="0123456789abcdef">')
    calls = install_school_http(monkeypatch, page=form + '<div class="authError">' + message + '</div>')
    client = HenuXkClient("student", "offline-test-password")

    assert client.login() is False
    assert client.get_auth_info()["error_code"] == expected
    assert not any(method == "POST" for method, _ in calls)


def test_hidden_captcha_input_and_scripts_do_not_block_normal_login(monkeypatch):
    form = ('<input name="execution" value="e1">'
            '<input id="pwdEncryptSalt" value="0123456789abcdef">'
            '<input type="hidden" name="captcha">'
            '<script>var captchaMessage = "请输入验证码";</script>')
    calls = install_school_http(monkeypatch, page=form, post_result=("signed_in", 200))
    client = HenuXkClient("student", "offline-test-password")

    assert client.login() is True
    assert client.get_auth_info()["mode"] == "ids_cas"
    assert sum(method == "POST" for method, _ in calls) == 1


@pytest.mark.parametrize("message", ["用户名或密码错误", "请输入验证码"])
def test_post_5xx_remains_unknown_even_when_its_body_looks_definitive(monkeypatch, message):
    calls = install_school_http(monkeypatch, page="form", post_result=(message, 503))
    client = HenuXkClient("student", "offline-test-password")

    assert client.login() is False
    assert client.get_auth_info()["error_code"] == "auth_state_unknown"
    assert sum(method == "POST" for method, _ in calls) == 1

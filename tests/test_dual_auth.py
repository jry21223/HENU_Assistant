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


def test_ids_service_error_fails_closed_without_kingo(monkeypatch) -> None:
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

    assert client.login() is False
    assert calls == 0
    auth = client.get_auth_info()
    assert auth["error_code"] == "service_error"
    assert "勿连续尝试" in auth["message"]


def test_ids_network_error_fails_closed_without_kingo(monkeypatch) -> None:
    client = _client(monkeypatch)
    monkeypatch.setattr(
        client,
        "_login_via_ids",
        lambda: AuthResult(False, error_code="network_error", message="IDS network failed"),
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
    assert auth["error_code"] == "network_error"
    assert "勿连续尝试" in auth["message"]
    assert "password" not in auth["message"].lower()


def test_ids_verification_challenge_fails_closed_without_kingo_retry(
    monkeypatch,
) -> None:
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
    assert "勿连续尝试" in auth["message"]
    assert "浏览器" in auth["message"]


def test_ids_lock_message_is_classified_as_account_locked() -> None:
    error_code, message = HenuXkClient._ids_error("密码错误次数过多，账号已锁定")
    rate_limit_code, _ = HenuXkClient._ids_error("", status_code=429)

    assert error_code == "account_locked"
    assert "锁定" in message
    assert rate_limit_code == "rate_limited"


def test_ids_normal_login_page_static_captcha_markup_is_not_a_challenge() -> None:
    html = """
    <script>
      var captchaSwitch = "1";
      var needCaptcha = "";
    </script>
    <span>验证码登录</span>
    <div class="captcha item" id="captchaDiv">
      <input id="captcha" placeholder="请输入验证码">
    </div>
    <input name="execution" value="e1">
    <input id="pwdEncryptSalt" value="0123456789abcdef">
    """

    error_code, _ = HenuXkClient._ids_error(html)

    assert error_code == "service_error"


def test_ids_active_captcha_flag_is_classified_as_challenge() -> None:
    html = """
    <script>
      var captchaSwitch = "1";
      var needCaptcha = "true";
    </script>
    """

    error_code, message = HenuXkClient._ids_error(html)

    assert error_code == "captcha_required"
    assert "验证码" in message


def test_ids_active_captcha_on_initial_page_never_submits_credentials(monkeypatch) -> None:
    client = _client(monkeypatch)

    class Response:
        status_code = 200
        url = "https://ids.example/login"
        content = (
            b'<script>var needCaptcha = "true";</script>'
            b'<input name="execution" value="e1">'
            b'<input id="pwdEncryptSalt" value="0123456789abcdef">'
        )

    monkeypatch.setattr(client.session, "get", lambda *_args, **_kwargs: Response())
    monkeypatch.setattr(
        client.session,
        "post",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("active captcha page must not submit credentials")
        ),
    )

    result = client._login_via_ids()

    assert result.success is False
    assert result.error_code == "captcha_required"


def test_ids_invalid_credentials_take_priority_over_static_captcha_markup() -> None:
    html = """
    <script>var captchaSwitch = "1"; var needCaptcha = "";</script>
    <span>用户名或密码错误</span>
    <span>验证码登录</span>
    """

    error_code, _ = HenuXkClient._ids_error(html)

    assert error_code == "invalid_credentials"


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
    monkeypatch.setattr(
        client.session,
        "post",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("connection reset")
        ),
    )

    result = client._login_via_ids()

    assert result.error_code == "auth_state_unknown"


def test_ids_pre_auth_unavailable_is_the_only_kingo_fallback(monkeypatch) -> None:
    client = _client(monkeypatch)
    monkeypatch.setattr(
        client,
        "_login_via_ids",
        lambda: AuthResult(
            False,
            error_code="ids_unavailable",
            message="IDS unavailable before auth",
        ),
    )
    calls = 0

    def kingo(**_kwargs):
        nonlocal calls
        calls += 1
        return AuthResult(True, mode="xk_kingo", degraded=True, message="ok")

    monkeypatch.setattr("henu_mcp.core.course_schedule.login_via_kingo", kingo)

    assert client.login() is True
    assert calls == 1


def test_ids_auth_state_unknown_never_falls_back_to_kingo(monkeypatch) -> None:
    client = _client(monkeypatch)
    monkeypatch.setattr(
        client,
        "_login_via_ids",
        lambda: AuthResult(
            False,
            error_code="auth_state_unknown",
            message="IDS submitted but unknown",
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


def test_kingo_persistence_does_not_write_cas_jar(monkeypatch) -> None:
    from henu_mcp.tools import server_impl

    saved: list[dict] = []
    cas_saved: list[dict] = []
    monkeypatch.setattr(server_impl, "save_json", lambda _path, data: saved.append(data))
    monkeypatch.setattr(server_impl, "_save_cas_cookies", lambda data, *_args: cas_saved.append(data))

    server_impl._save_xk_cookies(
        {
            "JSESSIONID": "xk-session",
            "route": "xk-route",
            "CASTGC": "must-not-persist",
            "_auth_mode": "xk_kingo",
        },
        subject_student_id="20230001",
    )

    assert saved == [
        {
            "JSESSIONID": "xk-session",
            "route": "xk-route",
            "_auth_mode": "xk_kingo",
            "_subject_student_id": "20230001",
        }
    ]
    assert cas_saved == []

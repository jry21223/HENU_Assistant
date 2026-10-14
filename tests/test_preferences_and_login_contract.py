from __future__ import annotations

import inspect

import pytest

from henu_mcp.core.course_schedule import HenuXkClient
from henu_mcp.core.kingo_auth import AuthResult
from henu_mcp.tools import server_impl


@pytest.fixture()
def isolated_profile(monkeypatch: pytest.MonkeyPatch, tmp_path):
    profile_file = tmp_path / "henu_profile.json"
    monkeypatch.setattr(server_impl, "PROFILE_FILE", profile_file)
    return profile_file


def test_preferences_tools_set_get_and_explicitly_clear_library_defaults(
    isolated_profile,
) -> None:
    for tool_name in ("preferences_get", "preferences_set", "preferences_clear"):
        assert server_impl.mcp._tool_manager.get_tool(tool_name) is not None

    updated = server_impl.preferences_set(
        library_location="第一自习室",
        library_seat_no="001",
    )

    assert updated == {
        "success": True,
        "msg": "偏好已更新",
        "preferences": {
            "library_location": "第一自习室",
            "library_seat_no": "001",
            "library_start_time": "",
            "library_end_policy": "ask",
            "empty_classroom_campus_code": "",
            "empty_classroom_building_code": "",
            "empty_classroom_term_code": "",
        },
        "updated": ["library_location", "library_seat_no"],
    }
    assert server_impl.preferences_get()["preferences"]["library_seat_no"] == "001"

    cleared = server_impl.preferences_clear(
        keys=["library_location", "library_seat_no"]
    )

    assert cleared["success"] is True
    assert cleared["cleared"] == ["library_location", "library_seat_no"]
    assert cleared["preferences"]["library_location"] == ""
    assert cleared["preferences"]["library_seat_no"] == ""


def test_preferences_set_validates_start_time_and_end_policy(isolated_profile) -> None:
    invalid_time = server_impl.preferences_set(library_start_time="随便")
    invalid_policy = server_impl.preferences_set(library_end_policy="12:00")

    assert invalid_time == {
        "success": False,
        "code": "invalid_library_start_time",
        "msg": "library_start_time 必须为 HH:MM，例如 08:00",
    }
    assert invalid_policy == {
        "success": False,
        "code": "invalid_library_end_policy",
        "msg": "library_end_policy 仅支持 ask/server_full_slot",
    }


def test_library_query_uses_saved_start_time_without_authorizing_a_write(
    isolated_profile,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server_impl._save_profile_fields(
        {
            "student_id": "offline-student",
            "password": "offline-password",
            "library_location": "第一自习室",
        }
    )
    server_impl.preferences_set(library_start_time="09:30")
    captured: dict[str, str] = {}

    class FakeBot:
        def list_available_seats(self, **kwargs):
            captured.update(kwargs)
            return {"success": True, "seats": []}

        def get_cookies(self):
            return {}

        def get_cas_cookies(self):
            return {}

    monkeypatch.setattr(server_impl, "HenuCampusBot", object)
    monkeypatch.setattr(server_impl, "_build_library_bot", lambda *_args: FakeBot())
    monkeypatch.setattr(server_impl, "_save_library_cookies", lambda _cookies: None)
    monkeypatch.setattr(server_impl, "_save_cas_cookies", lambda _cookies: None)

    result = server_impl.library_query(view="seats")

    assert result["success"] is True
    assert captured["location_name"] == "第一自习室"
    assert captured["preferred_time"] == "09:30"


def test_empty_classroom_query_uses_saved_query_defaults(
    isolated_profile,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server_impl.preferences_set(
        empty_classroom_campus_code="01",
        empty_classroom_building_code="0013",
        empty_classroom_term_code="2026,1",
    )
    captured: dict[str, object] = {}

    class FakeResult:
        total = 0

        def to_dict(self):
            return {"rooms": [], "total": 0}

    def fake_query_free_classrooms(**kwargs):
        captured.update(kwargs)
        return FakeResult()

    class FakeClient:
        def fetch_buildings(self, campus_code):
            assert campus_code == "01"
            return [type("Building", (), {"building_code": "0013"})()]

    monkeypatch.setattr(server_impl, "EmptyClassroomClient", object)
    monkeypatch.setattr(server_impl, "query_free_classrooms", fake_query_free_classrooms)
    monkeypatch.setattr(server_impl, "_get_empty_classroom_client", lambda: FakeClient())

    result = server_impl.empty_classroom_query(
        view="free",
        week=1,
        day_of_week=1,
        period=1,
    )

    assert result["success"] is True
    assert captured["term_code"] == "2026,1"
    assert captured["campus_code"] == "01"
    assert captured["building_code"] == "0013"


def test_empty_classroom_explicit_text_overrides_all_saved_location_defaults(
    isolated_profile,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server_impl.preferences_set(
        empty_classroom_campus_code="01",
        empty_classroom_building_code="0013",
        empty_classroom_term_code="2026,1",
    )
    captured: dict[str, object] = {}

    class FakeResult:
        total = 0

        def to_dict(self):
            return {"rooms": [], "total": 0}

    class FakeClient:
        def fetch_buildings(self, campus_code):
            assert campus_code == "02"
            return [type("Building", (), {"building_code": "0099"})()]

    monkeypatch.setattr(server_impl, "EmptyClassroomClient", object)
    monkeypatch.setattr(
        server_impl,
        "_resolve_campus_and_building",
        lambda *_args: {"campus_code": "02", "building_code": "0099"},
    )
    monkeypatch.setattr(server_impl, "_get_empty_classroom_client", lambda: FakeClient())
    monkeypatch.setattr(
        server_impl,
        "query_free_classrooms",
        lambda **kwargs: (captured.update(kwargs), FakeResult())[1],
    )

    result = server_impl.empty_classroom_query(
        view="free",
        campus_text="金明校区",
        building_text="综合楼",
        week=1,
        day_of_week=1,
        period=1,
    )

    assert result["success"] is True
    assert captured["campus_code"] == "02"
    assert captured["building_code"] == "0099"


def test_empty_classroom_explicit_campus_does_not_inherit_saved_building(
    isolated_profile,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server_impl.preferences_set(
        empty_classroom_campus_code="01",
        empty_classroom_building_code="0013",
        empty_classroom_term_code="2026,1",
    )
    captured: dict[str, object] = {}

    class FakeResult:
        total = 0

        def to_dict(self):
            return {"rooms": [], "total": 0}

    monkeypatch.setattr(server_impl, "EmptyClassroomClient", object)
    monkeypatch.setattr(server_impl, "_get_empty_classroom_client", lambda: object())
    monkeypatch.setattr(
        server_impl,
        "query_free_classrooms",
        lambda **kwargs: (captured.update(kwargs), FakeResult())[1],
    )

    result = server_impl.empty_classroom_query(
        view="free",
        campus_code="02",
        week=1,
        day_of_week=1,
        period=1,
    )

    assert result["success"] is True
    assert captured["campus_code"] == "02"
    assert captured["building_code"] == ""


def test_empty_classroom_rejects_saved_building_from_another_campus(
    isolated_profile,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server_impl.preferences_set(
        empty_classroom_campus_code="02",
        empty_classroom_building_code="0013",
        empty_classroom_term_code="2026,1",
    )

    class FakeClient:
        def fetch_buildings(self, campus_code):
            assert campus_code == "02"
            return [type("Building", (), {"building_code": "0099"})()]

    monkeypatch.setattr(server_impl, "EmptyClassroomClient", object)
    monkeypatch.setattr(server_impl, "_get_empty_classroom_client", lambda: FakeClient())
    monkeypatch.setattr(
        server_impl,
        "query_free_classrooms",
        lambda **_kwargs: pytest.fail("cross-campus building must fail before query"),
    )

    result = server_impl.empty_classroom_query(
        view="free",
        week=1,
        day_of_week=1,
        period=1,
    )

    assert result["success"] is False
    assert result["code"] == "building_not_in_campus"


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        (
            {
                "location": "第一自习室",
                "seat_no": "001",
                "preferred_time": "08:00",
                "end_policy": "server_full_slot",
            },
            "missing_target_date",
        ),
        (
            {
                "location": "第一自习室",
                "seat_no": "001",
                "target_date": "2026-09-20",
                "end_policy": "server_full_slot",
            },
            "missing_preferred_time",
        ),
        (
            {
                "location": "第一自习室",
                "seat_no": "001",
                "target_date": "2026-09-20",
                "preferred_time": "随便",
                "end_policy": "server_full_slot",
            },
            "invalid_preferred_time",
        ),
        (
            {
                "location": "第一自习室",
                "seat_no": "001",
                "target_date": "2026-09-20",
                "preferred_time": "08:00",
            },
            "missing_end_strategy",
        ),
        (
            {
                "location": "第一自习室",
                "seat_no": "001",
                "target_date": "2026-09-20",
                "preferred_time": "08:00",
                "end_policy": "ask",
            },
            "end_time_confirmation_required",
        ),
    ],
)
def test_library_reserve_fails_closed_before_any_network_call(
    monkeypatch: pytest.MonkeyPatch,
    kwargs: dict[str, str],
    code: str,
) -> None:
    monkeypatch.setattr(
        server_impl,
        "_build_library_bot",
        lambda *_args, **_kwargs: pytest.fail("reservation validation must run before network"),
    )

    result = server_impl.library_reserve(**kwargs)

    assert result["success"] is False
    assert result["code"] == code


def test_setup_account_public_schema_explains_login_format_and_password_semantics() -> None:
    tool = server_impl.mcp._tool_manager.get_tool("setup_account")
    assert tool is not None
    assert "河南大学完整学号" in tool.description
    assert "统一身份认证密码" in tool.description
    assert "QQ 密码" in tool.description
    assert "verify_login=false" in tool.description
    assert tool.parameters["required"] == ["student_id", "password"]

    signature = inspect.signature(server_impl.setup_account)
    assert signature.parameters["student_id"].annotation == "str"
    assert signature.parameters["password"].annotation == "str"


def test_setup_account_missing_credentials_has_machine_readable_error() -> None:
    result = server_impl.setup_account(
        student_id="",
        password="",
        verify_login=False,
        calibrate_period_time=False,
    )

    assert result == {
        "success": False,
        "code": "missing_credentials",
        "msg": "student_id/password 不能为空",
    }


@pytest.mark.parametrize(
    ("error_code", "ids_message"),
    [
        ("captcha_required", "IDS 统一认证需要验证码"),
        ("additional_verification_required", "IDS 统一认证需要额外校验"),
        ("account_locked", "IDS 统一认证账号已锁定"),
        ("rate_limited", "IDS 统一认证请求过于频繁"),
        ("invalid_credentials", "IDS 统一认证拒绝了账号或密码"),
        ("auth_state_unknown", "IDS 认证请求已提交但状态未知"),
        ("service_error", "IDS 返回未识别错误"),
    ],
)
def test_ids_challenge_or_lock_fails_closed_without_kingo_retry(
    monkeypatch: pytest.MonkeyPatch,
    error_code: str,
    ids_message: str,
) -> None:
    client = HenuXkClient("offline-student", "offline-password")
    monkeypatch.setattr(client, "_check_logged_in", lambda: False)
    monkeypatch.setattr(
        client,
        "_login_via_ids",
        lambda: AuthResult(False, error_code=error_code, message=ids_message),
    )
    kingo_calls = 0

    def fake_kingo(**_kwargs):
        nonlocal kingo_calls
        kingo_calls += 1
        return AuthResult(True, mode="xk_kingo")

    monkeypatch.setattr("henu_mcp.core.course_schedule.login_via_kingo", fake_kingo)

    assert client.login() is False
    assert kingo_calls == 0
    auth = client.get_auth_info()
    assert auth["error_code"] == error_code
    assert "请先在浏览器完成验证或稍后重试" in auth["message"]
    assert "勿连续尝试" in auth["message"]


@pytest.mark.parametrize(
    ("response_text", "status_code", "error_code"),
    [
        ("请输入验证码", 200, "captcha_required"),
        ("需要完成额外校验", 200, "additional_verification_required"),
        ("失败次数过多，账号已锁定", 200, "account_locked"),
        ("Too Many Requests", 429, "rate_limited"),
        ("用户名或密码错误", 200, "invalid_credentials"),
        ("服务维护中", 503, "ids_unavailable"),
    ],
)
def test_ids_error_classifies_fail_closed_responses(
    response_text: str,
    status_code: int,
    error_code: str,
) -> None:
    code, _message = HenuXkClient._ids_error(response_text, status_code)

    assert code == error_code


def test_ids_post_exception_marks_auth_state_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = HenuXkClient("offline-student", "offline-password")

    class LoginPage:
        status_code = 200
        url = "https://ids.example/login"
        content = (
            b'<input name="execution" value="e1">'
            b'<input id="pwdEncryptSalt" value="0123456789abcdef">'
        )

    def fail_post(*_args, **_kwargs):
        raise OSError("connection reset")

    monkeypatch.setattr(client, "_check_logged_in", lambda: False)
    monkeypatch.setattr(client.session, "get", lambda *_args, **_kwargs: LoginPage())
    monkeypatch.setattr(client.session, "post", fail_post)

    result = client._login_via_ids()

    assert result.error_code == "auth_state_unknown"


def test_ids_post_5xx_marks_auth_state_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = HenuXkClient("offline-student", "offline-password")

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

    monkeypatch.setattr(client, "_check_logged_in", lambda: False)
    monkeypatch.setattr(client.session, "get", lambda *_args, **_kwargs: LoginPage())
    monkeypatch.setattr(client.session, "post", lambda *_args, **_kwargs: ServiceUnavailable())

    result = client._login_via_ids()

    assert result.error_code == "auth_state_unknown"


def test_setup_account_returns_fail_closed_ids_error_without_network_retry(
    isolated_profile,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(HenuXkClient, "_check_logged_in", lambda _self: False)
    monkeypatch.setattr(
        HenuXkClient,
        "_login_via_ids",
        lambda _self: AuthResult(
            False,
            error_code="account_locked",
            message="IDS 统一认证账号已锁定",
        ),
    )
    monkeypatch.setattr(
        "henu_mcp.core.course_schedule.login_via_kingo",
        lambda **_kwargs: pytest.fail("locked IDS account must not retry through xk"),
    )

    result = server_impl.setup_account(
        student_id="offline-student",
        password="offline-password",
        calibrate_period_time=False,
    )

    assert result["success"] is False
    assert result["code"] == "account_locked"
    assert "请先在浏览器完成验证或稍后重试" in result["msg"]
    assert "勿连续尝试" in result["msg"]

from __future__ import annotations

from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from henu_plugin.cli import (  # noqa: E402
    account_command_format_error,
    build_help_payload,
    inspect_cli_command,
    is_account_set_intent,
    redact_cli_command,
)
from henu_plugin import service as service_module  # noqa: E402
from henu_plugin.service import HenuPluginService, SessionIdentity  # noqa: E402
from henu_mcp.tools import server_impl  # noqa: E402
from components.cli_tools.henu_cli_safe import HenuCliSafe  # noqa: E402
from components.cli_tools.base import BaseHenuTool  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_login_guard(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HENU_MASTER_KEY", "test-only-master-key")
    monkeypatch.setattr(
        server_impl,
        "LOGIN_GUARD_FILE",
        tmp_path / "login_attempt_guard.json",
    )


def test_root_help_exposes_empty_classroom_and_resource_topics() -> None:
    payload = build_help_payload("")
    commands = "\n".join(payload["commands"])
    examples = "\n".join(payload["examples"])

    assert "empty_classroom" in commands
    assert "resource" in commands
    assert "help empty_classroom" in examples
    assert "help resource" in examples


def test_empty_classroom_parser_forwards_rich_filters() -> None:
    spec = inspect_cli_command(
        "empty_classroom query --classroom-text 十号楼101 --type-code 05 "
        "--min-capacity 80 --keyword 多媒体 --room-id 0001 --ttl-seconds 60 --max-stale-seconds 600"
    )

    assert spec.resolved_tool == "empty_classroom_query"
    assert spec.params["classroom_text"] == "十号楼101"
    assert spec.params["type_code"] == "05"
    assert spec.params["min_capacity"] == 80
    assert spec.params["keyword"] == "多媒体"
    assert spec.params["room_id"] == "0001"
    assert spec.params["ttl_seconds"] == 60
    assert spec.params["max_stale_seconds"] == 600


def test_empty_classroom_help_topic_documents_rich_views() -> None:
    payload = build_help_payload("empty_classroom")
    commands = "\n".join(payload["commands"])

    assert "day_matrix" in commands
    assert "occupancy" in commands
    assert "--classroom-text" in commands


def test_library_locations_reply_exposes_names_ids_and_next_command() -> None:
    tool = HenuCliSafe.__new__(HenuCliSafe)
    result = {
        "success": True,
        "msg": "操作成功",
        "date": "2026-07-13",
        "source": "live",
        "locations": [
            {"location": "明伦校区图书馆", "area_id": "101"},
            {"location": "金明校区图书馆北区", "area_id": "202"},
        ],
        "total": 2,
        "cli": {"command": "library locations", "mode": "exec"},
    }

    reply = tool._build_reply_text(result)
    hint = tool._build_llm_hint(result)

    assert "明伦校区图书馆" in reply
    assert "area_id: 101" in reply
    assert "library seats --location" in reply
    assert "唯一有效参数来源" in hint


def test_library_seats_reply_exposes_seat_numbers_and_reserve_command() -> None:
    tool = HenuCliSafe.__new__(HenuCliSafe)
    reply = tool._build_reply_text(
        {
            "success": True,
            "seats": [{"seat_no": "A-101", "status": "可预约", "location": "明伦校区图书馆"}],
            "total": 1,
        }
    )

    assert "A-101" in reply
    assert "library reserve --location" in reply


def test_base_payload_keeps_library_locations_when_compacting() -> None:
    tool = BaseHenuTool.__new__(BaseHenuTool)
    result = {
        "success": True,
        "msg": "操作成功",
        "locations": [{"location": f"区域-{index}", "area_id": str(index)} for index in range(20)],
        "next_commands": ["library seats --location <区域>"],
    }

    tool._normalize_for_qq_delivery(result)

    assert result["locations"]
    assert result["locations_truncated"] == 8


def test_preferences_cli_exposes_get_set_and_clear_contract() -> None:
    get_spec = inspect_cli_command("preferences get")
    set_spec = inspect_cli_command(
        "preferences set --library-location 金明校区图书馆 --library-seat-no 001"
    )
    clear_spec = inspect_cli_command(
        "preferences clear --library-location --library-seat-no"
    )

    assert get_spec.resolved_tool == "preferences"
    assert get_spec.params == {"action": "get"}
    assert set_spec.resolved_tool == "preferences"
    assert set_spec.params == {
        "action": "set",
        "library_location": "金明校区图书馆",
        "library_seat_no": "001",
    }
    assert clear_spec.resolved_tool == "preferences"
    assert clear_spec.params == {
        "action": "clear",
        "clear_library_location": True,
        "clear_library_seat_no": True,
    }


def test_preferences_service_can_set_and_explicitly_clear_library_defaults(
    monkeypatch,
) -> None:
    profile = {
        "library_location": "明伦第一自习室",
        "library_seat_no": "009",
        "location": "旧版明伦区域",
        "seat_no": "旧版座位",
    }

    def load_profile():
        return dict(profile)

    def save_fields(fields):
        profile.update(fields)

    monkeypatch.setattr("henu_plugin.service.server_impl._effective_profile", load_profile)
    monkeypatch.setattr("henu_plugin.service.server_impl._save_profile_fields", save_fields)
    service = HenuPluginService(ROOT)

    set_result = service._preferences(
        {
            "action": "set",
            "library_location": "金明七层南自习室",
            "library_seat_no": "001",
        }
    )
    clear_result = service._preferences(
        {
            "action": "clear",
            "clear_library_location": True,
            "clear_library_seat_no": True,
        }
    )

    assert set_result["success"] is True
    assert clear_result["success"] is True
    assert clear_result["preferences"] == {
        "library_location": "",
        "library_seat_no": "",
    }
    assert profile["library_location"] == ""
    assert profile["library_seat_no"] == ""
    assert profile["location"] == ""
    assert profile["seat_no"] == ""


def test_library_reserve_cli_does_not_invent_date_or_time_defaults() -> None:
    spec = inspect_cli_command(
        "library reserve --location 金明七层南自习室 --seat-no 001"
    )

    assert spec.resolved_tool == "library_reserve"
    assert spec.params["target_date"] == ""
    assert spec.params["preferred_time"] == ""
    assert spec.params["preferred_end_time"] == ""
    assert spec.params["end_policy"] == ""


def test_library_reserve_service_returns_structured_missing_params_without_submit(
    monkeypatch,
) -> None:
    submitted = []
    monkeypatch.setattr(
        "henu_plugin.service.mcp_server.library_reserve",
        lambda **kwargs: submitted.append(kwargs) or {"success": True},
    )
    service = HenuPluginService(ROOT)

    result = service._library_reserve(
        {
            "location": "金明七层南自习室",
            "seat_no": "001",
            "target_date": "",
            "preferred_time": "",
            "preferred_end_time": "",
            "end_policy": "",
        }
    )

    assert result["success"] is False
    assert result["error_code"] == "missing_params"
    assert result["missing_params"] == [
        "target_date",
        "preferred_time",
        "end_policy",
    ]
    assert submitted == []


def test_library_reserve_server_full_slot_keeps_user_start_and_omits_end(
    monkeypatch,
) -> None:
    submitted = []
    monkeypatch.setattr(
        "henu_plugin.service.mcp_server.library_reserve",
        lambda **kwargs: submitted.append(kwargs) or {"success": True},
    )
    service = HenuPluginService(ROOT)

    result = service._library_reserve(
        {
            "location": "金明七层南自习室",
            "seat_no": "001",
            "target_date": "2026-09-20",
            "preferred_time": "08:00",
            "preferred_end_time": "",
            "end_policy": "server_full_slot",
        }
    )

    assert result["success"] is True
    assert submitted[0]["target_date"] == "2026-09-20"
    assert submitted[0]["preferred_time"] == "08:00"
    assert submitted[0]["preferred_end_time"] == ""


def test_library_reserve_ask_policy_requires_explicit_end_time(monkeypatch) -> None:
    submitted = []
    monkeypatch.setattr(
        "henu_plugin.service.mcp_server.library_reserve",
        lambda **kwargs: submitted.append(kwargs) or {"success": True},
    )
    service = HenuPluginService(ROOT)

    result = service._library_reserve(
        {
            "target_date": "2026-09-20",
            "preferred_time": "08:00",
            "preferred_end_time": "",
            "end_policy": "ask",
        }
    )

    assert result["success"] is False
    assert result["error_code"] == "missing_params"
    assert result["missing_params"] == ["preferred_end_time"]
    assert submitted == []


def test_library_reserve_end_time_is_backward_compatible_explicit_end(
    monkeypatch,
) -> None:
    submitted = []
    monkeypatch.setattr(
        "henu_plugin.service.mcp_server.library_reserve",
        lambda **kwargs: submitted.append(kwargs) or {"success": True},
    )
    spec = inspect_cli_command(
        "library reserve --location 金明七层南自习室 --seat-no 001 "
        "--date 2026-09-20 --time 08:00 --end-time 12:00"
    )
    result = HenuPluginService(ROOT)._library_reserve(spec.params)

    assert spec.params["end_policy"] == "explicit_end"
    assert result["success"] is True
    assert result["end_policy"] == "explicit_end"
    assert submitted[0]["preferred_end_time"] == "12:00"


def test_account_parser_accepts_typographic_quotes_without_saving_quote_marks() -> None:
    spec = inspect_cli_command(
        "account set --student-id “20230001” --password ‘Demo Pass’"
    )

    assert spec.resolved_tool == "setup_account"
    assert spec.params["student_id"] == "20230001"
    assert spec.params["password"] == "Demo Pass"


def test_typographic_quoted_password_is_fully_redacted() -> None:
    redacted = redact_cli_command(
        "account set --student-id 20230001 --password ‘Demo Pass’"
    )

    assert "Demo" not in redacted
    assert "Pass" not in redacted
    assert "<redacted>" in redacted


def test_account_placeholder_and_common_typos_get_secret_safe_corrections() -> None:
    placeholder = inspect_cli_command(
        "account set --student-id <20230001> --password <DemoSecret>"
    )
    typo = account_command_format_error(
        "count set --student-id 20230001 --password DemoSecret"
    )
    glued = account_command_format_error(
        "account set--student-id 20230001 --password DemoSecret"
    )
    glued_student_id = account_command_format_error(
        "account set --student-id20230001 --password DemoSecret"
    )
    glued_password = account_command_format_error(
        "account set --student-id 20230001--password DemoSecret"
    )

    assert placeholder.error
    assert "尖括号" in placeholder.error
    assert "DemoSecret" not in placeholder.error
    assert "account set --student-id" in typo
    assert "DemoSecret" not in typo
    assert "缺少空格" in glued
    assert "DemoSecret" not in glued
    assert "缺少空格" in glued_student_id
    assert "DemoSecret" not in glued_student_id
    assert "缺少空格" in glued_password
    assert "DemoSecret" not in glued_password


def test_account_set_rejects_unquoted_password_tail_and_unknown_options() -> None:
    unquoted = inspect_cli_command(
        "account set --student-id 20230001 --password Demo Pass"
    )
    unknown = inspect_cli_command(
        "account set --student-id 20230001 --password DemoSecret --unexpected yes"
    )
    redacted = redact_cli_command(
        "account set --student-id 20230001 --password Demo Pass"
    )

    assert unquoted.resolved_tool is None
    assert "未执行登录" in unquoted.error
    assert "Demo" not in unquoted.error and "Pass" not in unquoted.error
    assert unknown.resolved_tool is None
    assert "未执行登录" in unknown.error
    assert redacted == "account set --student-id <redacted> --password <redacted>"


def test_account_login_intent_detection_keeps_structured_command_out_of_llm() -> None:
    assert is_account_set_intent(
        "account set --student-id 20230001 --password 'DemoSecret'"
    )
    assert is_account_set_intent("请帮我绑定，学号 20230001，密码 DemoSecret")
    assert is_account_set_intent("学号 20230001 密码 DemoSecret")
    assert is_account_set_intent("--student-id 20230001 --password DemoSecret")
    assert is_account_set_intent("password DemoSecret 用户名 20230001")
    assert is_account_set_intent("账号 20230001 passwd DemoSecret")
    assert is_account_set_intent("student id 20230001 密码 DemoSecret")
    assert not is_account_set_intent("DemoSecret")
    assert not is_account_set_intent("帮我查一下账号状态")


def test_setup_account_without_verification_skips_calibration(monkeypatch) -> None:
    monkeypatch.setattr(
        server_impl,
        "save_account",
        lambda **_kwargs: {
            "success": True,
            "account": {"student_id": "20230001"},
            "auth": {},
            "context": {},
        },
    )
    monkeypatch.setattr(
        server_impl,
        "auto_calibrate_period_time",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("calibration must not run without verification")
        ),
    )

    result = server_impl.setup_account(
        student_id="20230001",
        password="DemoSecret",
        verify_login=False,
        calibrate_period_time=True,
    )

    assert result["success"] is True
    assert result["verified"] is False
    assert result["msg"] == "账号已保存，尚未验证登录"
    assert result["period_time_calibration"] == {
        "skipped": True,
        "reason": "login_not_verified",
    }


def test_save_account_persists_successful_login_verification(monkeypatch) -> None:
    saved_fields = []
    cleared = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            self.auth_result = type("Auth", (), {"message": "IDS 统一认证登录成功"})()

        def login(self) -> bool:
            return True

        def get_cookies(self):
            return {"_auth_mode": "ids_cas"}

        def fetch_user_context(self):
            return {"login_id": "20230001", "user_type": "STU"}

        def get_auth_info(self):
            return {
                "mode": "ids_cas",
                "error_code": "",
                "message": "IDS 统一认证登录成功",
            }

    monkeypatch.setattr(server_impl, "HenuXkClient", FakeClient)
    monkeypatch.setattr(server_impl, "_load_xk_cookies", lambda: {})
    monkeypatch.setattr(server_impl, "_save_xk_cookies", lambda *_args: None)
    monkeypatch.setattr(
        server_impl,
        "_clear_account_auth_artifacts",
        lambda: cleared.append(True),
    )
    monkeypatch.setattr(
        server_impl,
        "_save_profile_fields",
        lambda fields: saved_fields.append(fields),
    )
    monkeypatch.setattr(
        server_impl,
        "_mask_profile",
        lambda _profile: {"student_id": "20230001"},
    )
    monkeypatch.setattr(server_impl, "_effective_profile", lambda: {})

    result = server_impl.save_account("20230001", "DemoSecret", verify_login=True)

    assert result["success"] is True
    assert result["binding_changed"] is True
    assert cleared == [True]
    assert saved_fields[-1]["student_id"] == "20230001"
    assert saved_fields[-1]["password"] == "DemoSecret"
    assert saved_fields[-1]["seminar_groups"] == {}
    assert saved_fields[-1]["seminar_mobile"] == ""
    verification = saved_fields[-1]["login_verification"]
    assert verification["status"] == "verified"
    assert verification["verified"] is True
    assert verification["auth_mode"] == "ids_cas"
    assert verification["attempted_at"]


def test_save_account_persists_failed_verification_without_credentials(monkeypatch) -> None:
    saved_fields = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            self.auth_result = type("Auth", (), {"message": "账号或密码错误"})()

        def login(self) -> bool:
            return False

        def get_auth_info(self):
            return {
                "mode": "",
                "error_code": "invalid_credentials",
                "message": "账号或密码错误",
            }

    monkeypatch.setattr(server_impl, "HenuXkClient", FakeClient)
    monkeypatch.setattr(server_impl, "_load_xk_cookies", lambda: {})
    monkeypatch.setattr(
        server_impl,
        "_save_profile_fields",
        lambda fields: saved_fields.append(fields),
    )

    result = server_impl.save_account("20230001", "DemoSecret", verify_login=True)

    assert result["success"] is False
    assert len(saved_fields) == 1
    assert "student_id" not in saved_fields[0]
    assert "password" not in saved_fields[0]
    verification = saved_fields[0]["last_login_attempt"]
    assert verification["status"] == "failed"
    assert verification["verified"] is False
    assert verification["error_code"] == "invalid_credentials"
    assert verification["subject_student_id"] == "20230001"
    assert "login_verification" not in saved_fields[0]


def test_save_account_marks_no_verify_binding_as_unverified(monkeypatch) -> None:
    saved_fields = []
    cleared = []
    monkeypatch.setattr(
        server_impl,
        "_save_profile_fields",
        lambda fields: saved_fields.append(fields),
    )
    monkeypatch.setattr(
        server_impl,
        "_mask_profile",
        lambda _profile: {"student_id": "20230001"},
    )
    monkeypatch.setattr(server_impl, "_effective_profile", lambda: {})
    monkeypatch.setattr(
        server_impl,
        "_clear_account_auth_artifacts",
        lambda: cleared.append(True),
    )

    result = server_impl.save_account("20230001", "DemoSecret", verify_login=False)

    assert result["success"] is True
    assert result["binding_changed"] is True
    assert cleared == [True]
    verification = saved_fields[-1]["login_verification"]
    assert verification["status"] == "unverified"
    assert verification["verified"] is False


def test_save_account_never_reuses_old_cookie_and_rejects_subject_mismatch(
    monkeypatch,
) -> None:
    saved_fields = []
    received_saved_cookies = []

    class FakeClient:
        def __init__(self, *_args, saved_cookies=None, **_kwargs) -> None:
            received_saved_cookies.append(saved_cookies)
            self.auth_result = type("Auth", (), {"message": "IDS 统一认证登录成功"})()

        def login(self) -> bool:
            return True

        def get_cookies(self):
            return {"_auth_mode": "ids_cas"}

        def fetch_user_context(self):
            return {"login_id": "20230000", "user_type": "STU"}

        def get_auth_info(self):
            return {"mode": "ids_cas", "error_code": "", "message": "IDS 统一认证登录成功"}

    monkeypatch.setattr(server_impl, "HenuXkClient", FakeClient)
    monkeypatch.setattr(
        server_impl,
        "_load_xk_cookies",
        lambda: (_ for _ in ()).throw(AssertionError("account set must not load old cookies")),
    )
    monkeypatch.setattr(
        server_impl,
        "_save_xk_cookies",
        lambda _cookies: (_ for _ in ()).throw(AssertionError("mismatched session must not persist")),
    )
    monkeypatch.setattr(
        server_impl,
        "_clear_account_auth_artifacts",
        lambda: (_ for _ in ()).throw(
            AssertionError("failed candidate must not clear current auth")
        ),
    )
    monkeypatch.setattr(
        server_impl,
        "_save_profile_fields",
        lambda fields: saved_fields.append(fields),
    )
    monkeypatch.setattr(
        server_impl,
        "_effective_profile",
        lambda: {
            "student_id": "20230000",
            "login_verification": {
                "status": "verified",
                "verified": True,
                "subject_student_id": "20230000",
                "attempted_at": "2026-09-21T09:00:00+08:00",
                "verified_at": "2026-09-21T09:00:00+08:00",
            },
        },
    )

    result = server_impl.save_account("20230001", "DemoSecret", verify_login=True)

    assert result["success"] is False
    assert result["auth"]["error_code"] == "account_mismatch"
    assert received_saved_cookies == [None]
    assert "student_id" not in saved_fields[-1]
    assert "password" not in saved_fields[-1]
    assert "login_verification" not in saved_fields[-1]
    assert saved_fields[-1]["last_login_attempt"]["subject_student_id"] == "20230001"


def test_login_verification_summary_marks_old_success_as_stale() -> None:
    summary = server_impl._login_verification_summary(
        {
            "student_id": "20230001",
            "login_verification": {
                "status": "verified",
                "verified": True,
                "subject_student_id": "20230001",
                "attempted_at": "2020-01-01T00:00:00+08:00",
                "verified_at": "2020-01-01T00:00:00+08:00",
            },
        }
    )

    assert summary["status"] == "stale"
    assert summary["verified"] is False
    assert summary["historically_verified"] is True


def test_login_attempt_guard_blocks_after_three_risky_failures(monkeypatch) -> None:
    fixed_now = server_impl.datetime.fromisoformat("2026-09-21T12:00:00+08:00")
    monkeypatch.setattr(server_impl, "_now_dt", lambda *_args, **_kwargs: fixed_now)
    profile = {}
    for _ in range(3):
        server_impl._next_login_attempt_guard(profile, "20230001")

    retry_after = server_impl._login_attempt_guard_retry_after(profile, "20230001")

    assert retry_after == 24 * 3600
    assert "20230001" not in server_impl.LOGIN_GUARD_FILE.read_text(encoding="utf-8")


def test_login_attempt_guard_is_shared_across_profiles_without_eviction(monkeypatch) -> None:
    fixed_now = server_impl.datetime.fromisoformat("2026-09-21T12:00:00+08:00")
    monkeypatch.setattr(server_impl, "_now_dt", lambda *_args, **_kwargs: fixed_now)
    for _ in range(3):
        server_impl._next_login_attempt_guard({}, "20230001")
    for index in range(20):
        server_impl._next_login_attempt_guard({}, f"2024{index:04d}")

    retry_after = server_impl._login_attempt_guard_retry_after(
        {"unrelated": "qq-profile"},
        "20230001",
    )

    assert retry_after == 24 * 3600
    store = server_impl._load_login_guard_store()
    assert server_impl._student_guard_hash("20230001") in store


def test_unwritable_shared_guard_fails_before_remote_auth(monkeypatch) -> None:
    calls = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            calls.append("remote_auth")

    monkeypatch.setattr(server_impl, "HenuXkClient", FakeClient)
    monkeypatch.setattr(
        server_impl,
        "_save_login_guard_store",
        lambda _guards: (_ for _ in ()).throw(OSError("read only")),
    )

    with pytest.raises(OSError, match="read only"):
        server_impl.save_account("20230001", "DemoSecret", verify_login=True)

    assert calls == []


def test_binding_persist_failure_restores_previous_files(monkeypatch) -> None:
    restored = []
    monkeypatch.setattr(server_impl, "_effective_profile", lambda: {})
    monkeypatch.setattr(server_impl, "_mask_profile", lambda _profile: {})
    monkeypatch.setattr(server_impl, "_snapshot_account_binding_files", lambda: {server_impl.PROFILE_FILE: b"old"})
    monkeypatch.setattr(server_impl, "_clear_account_auth_artifacts", lambda: None)
    monkeypatch.setattr(server_impl, "_restore_account_binding_files", lambda snapshot: restored.append(snapshot))
    monkeypatch.setattr(
        server_impl,
        "_save_profile_fields",
        lambda _fields: (_ for _ in ()).throw(OSError("disk full")),
    )

    result = server_impl.save_account("20230001", "DemoSecret", verify_login=False)

    assert result["success"] is False
    assert result["error_code"] == "binding_persist_failed"
    assert restored == [{server_impl.PROFILE_FILE: b"old"}]


def test_service_boundary_rejects_group_account_set(monkeypatch) -> None:
    service = HenuPluginService(ROOT)
    monkeypatch.setattr(
        service,
        "_setup_account",
        lambda _params: (_ for _ in ()).throw(
            AssertionError("group account set must not reach authentication")
        ),
    )
    service_module._CURRENT_IDENTITY.value = SessionIdentity(
        qq="10001",
        storage_key="10001",
        launcher_type="group",
        launcher_id="20001",
        sender_id="10001",
    )
    try:
        result = service._henu_cli(
            {
                "command": (
                    "account set --student-id 20230001 "
                    "--password 'DemoSecret'"
                )
            }
        )
    finally:
        service_module._CURRENT_IDENTITY.value = None

    assert result["success"] is False
    assert result["error_code"] == "private_chat_required"
    assert "DemoSecret" not in str(result)


def test_service_boundary_rejects_account_set_without_sender(monkeypatch) -> None:
    service = HenuPluginService(ROOT)
    monkeypatch.setattr(
        service,
        "_setup_account",
        lambda _params: (_ for _ in ()).throw(
            AssertionError("missing sender must not reach authentication")
        ),
    )
    service_module._CURRENT_IDENTITY.value = SessionIdentity(
        qq="20001",
        storage_key="20001",
        launcher_type="person",
        launcher_id="20001",
        sender_id="",
    )
    try:
        result = service._henu_cli(
            {"command": "account set --student-id 20230001 --password 'DemoSecret'"}
        )
    finally:
        service_module._CURRENT_IDENTITY.value = None

    assert result["success"] is False
    assert result["error_code"] == "private_chat_required"
    assert "DemoSecret" not in str(result)


def test_account_help_documents_exact_ids_format_and_stop_retry_failures() -> None:
    payload = build_help_payload("account set")
    text = "\n".join(payload["commands"] + payload["examples"] + payload["tips"])

    assert "account set --student-id" in text
    assert "ids.henu.edu.cn" in text
    assert "QQ、手机号或邮箱" in text
    assert "验证码" in text
    assert "停止重试" in text


def test_henu_cli_prompt_forbids_guessing_reservation_end_time() -> None:
    prompt = (ROOT / "components/cli_tools/henu_cli.yaml").read_text(encoding="utf-8")

    assert "绝不等于 12:00" in prompt
    assert "--end-policy ask" in prompt
    assert "server_full_slot" in prompt
    assert "校区是硬约束" in prompt

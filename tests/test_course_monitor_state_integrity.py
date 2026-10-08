from __future__ import annotations

import json

import pytest

from henu_mcp.core import course_monitor, course_schedule
from henu_mcp.tools import server_impl


_FIXTURE_HTML = """
<table>
  <tr>
    <td>SEC-001</td><td>网络工程 1 班</td><td>金明校区</td><td>测试教师</td>
    <td>必修</td><td>30</td><td>29</td><td>1</td><td>星期一 1-2 节</td><td>测试教室</td>
  </tr>
</table>
"""


def _fixture_config() -> str:
    return json.dumps(
        {
            "fixture_html": _FIXTURE_HTML,
            "fixture_course_id": "COURSE-001",
            "targets": [{"course_id": "COURSE-001"}],
            "notify": {
                "type": "feishu",
                "webhook_env": "COURSE_MONITOR_TEST_WEBHOOK",
            },
        }
    )


def test_monitor_once_fails_closed_when_existing_state_json_is_corrupt(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(course_schedule, "OUTPUT_DIR", tmp_path)
    monkeypatch.setenv("COURSE_MONITOR_TEST_WEBHOOK", "https://notify.invalid/webhook")
    state_path = course_monitor.get_monitor_state_file()
    original = b'{"items":'
    state_path.write_bytes(original)
    notification_requests: list[object] = []

    class _Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        @staticmethod
        def read() -> bytes:
            return b"{}"

    def record_notification(request, **_kwargs):
        notification_requests.append(request)
        return _Response()

    monkeypatch.setattr(course_monitor.urllib.request, "urlopen", record_notification)

    with pytest.raises(course_monitor.MonitorStateError) as captured:
        server_impl.course_monitor_once(_fixture_config(), send_notifications=True)

    assert type(captured.value) is course_monitor.MonitorStateError
    assert state_path.read_bytes() == original
    assert notification_requests == []


def test_monitor_once_fails_closed_when_existing_state_encoding_is_corrupt(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(course_schedule, "OUTPUT_DIR", tmp_path)
    state_path = course_monitor.get_monitor_state_file()
    original = b"\xff"
    state_path.write_bytes(original)
    notification_requests: list[object] = []

    def record_notification(request, **_kwargs):
        notification_requests.append(request)
        raise AssertionError("notification must not be attempted")

    monkeypatch.setattr(course_monitor.urllib.request, "urlopen", record_notification)

    with pytest.raises(course_monitor.MonitorStateError) as captured:
        server_impl.course_monitor_once(_fixture_config(), send_notifications=True)

    assert type(captured.value) is course_monitor.MonitorStateError
    assert state_path.read_bytes() == original
    assert notification_requests == []


def test_monitor_once_fails_closed_when_existing_state_is_not_an_object(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(course_schedule, "OUTPUT_DIR", tmp_path)
    state_path = course_monitor.get_monitor_state_file()
    original = b"[]"
    state_path.write_bytes(original)

    with pytest.raises(course_monitor.MonitorStateError) as captured:
        server_impl.course_monitor_once(_fixture_config(), send_notifications=False)

    assert type(captured.value) is course_monitor.MonitorStateError
    assert state_path.read_bytes() == original


def test_monitor_once_fails_closed_when_existing_state_cannot_be_read(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(course_schedule, "OUTPUT_DIR", tmp_path)
    monkeypatch.setenv("COURSE_MONITOR_TEST_WEBHOOK", "https://notify.invalid/webhook")
    state_path = course_monitor.get_monitor_state_file()
    original = b'{"items": {}}'
    state_path.write_bytes(original)
    original_read_text = type(state_path).read_text
    notification_requests: list[object] = []

    def unreadable_state(path, *args, **kwargs):
        if path == state_path:
            raise PermissionError("state is unreadable")
        return original_read_text(path, *args, **kwargs)

    def record_notification(request, **_kwargs):
        notification_requests.append(request)
        raise AssertionError("notification must not be attempted")

    monkeypatch.setattr(type(state_path), "read_text", unreadable_state)
    monkeypatch.setattr(course_monitor.urllib.request, "urlopen", record_notification)

    with pytest.raises(course_monitor.MonitorStateError) as captured:
        server_impl.course_monitor_once(_fixture_config(), send_notifications=True)

    assert type(captured.value) is course_monitor.MonitorStateError
    assert state_path.read_bytes() == original
    assert notification_requests == []


@pytest.mark.parametrize("broken", (b'{"targets":', b"[]", b"\xff"))
def test_existing_invalid_monitor_config_fails_closed_without_overwrite_or_network(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    broken: bytes,
) -> None:
    monkeypatch.setattr(course_schedule, "OUTPUT_DIR", tmp_path)
    config_path = course_monitor.get_monitor_config_file()
    config_path.write_bytes(broken)
    network_calls: list[object] = []

    def record_network(*_args, **_kwargs):
        network_calls.append(object())
        raise AssertionError("invalid monitor config must fail before network")

    monkeypatch.setattr(server_impl, "_resolve_account", record_network)

    with pytest.raises(course_monitor.MonitorConfigError):
        course_monitor.load_monitor_config()
    with pytest.raises(course_monitor.MonitorConfigError):
        course_monitor.save_monitor_config({"interval_seconds": 120}, merge=True)

    result = server_impl.course_monitor_once(send_notifications=True)
    assert result["success"] is False
    assert "监控配置 JSON 无效" in result["msg"]
    assert config_path.read_bytes() == broken
    assert network_calls == []


def test_unreadable_monitor_config_fails_closed_without_overwrite(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(course_schedule, "OUTPUT_DIR", tmp_path)
    config_path = course_monitor.get_monitor_config_file()
    original = b'{"targets": []}'
    config_path.write_bytes(original)
    original_read_text = type(config_path).read_text

    def unreadable_config(path, *args, **kwargs):
        if path == config_path:
            raise PermissionError("config is unreadable")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(type(config_path), "read_text", unreadable_config)

    with pytest.raises(course_monitor.MonitorConfigError, match="无法读取"):
        course_monitor.load_monitor_config()
    with pytest.raises(course_monitor.MonitorConfigError, match="无法读取"):
        course_monitor.save_monitor_config({"interval_seconds": 120}, merge=True)
    assert config_path.read_bytes() == original


def test_failed_notification_does_not_advance_baseline_and_retries_next_round(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(course_schedule, "OUTPUT_DIR", tmp_path)
    attempts: list[int] = []

    def flaky_notify(alerts, _config):
        attempts.append(len(alerts))
        if len(attempts) == 1:
            return {"success": False, "sent": False, "msg": "timeout"}
        return {"success": True, "sent": True, "msg": "ok"}

    monkeypatch.setattr(server_impl, "notify_alerts", flaky_notify)

    first = server_impl.course_monitor_once(_fixture_config(), send_notifications=True)
    second = server_impl.course_monitor_once(_fixture_config(), send_notifications=True)
    third = server_impl.course_monitor_once(_fixture_config(), send_notifications=True)

    assert first["success"] is False
    assert first["alerts_count"] == 1
    assert second["success"] is True
    assert second["alerts_count"] == 1
    assert third["success"] is True
    assert third["alerts_count"] == 0
    assert attempts == [1, 1, 0]


def test_feishu_business_error_is_not_reported_as_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        @staticmethod
        def read() -> bytes:
            return b'{"code":19001,"msg":"invalid webhook"}'

    monkeypatch.setattr(
        course_monitor.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(),
    )

    result = course_monitor.send_feishu_text(
        "https://notify.invalid/webhook",
        "message",
    )

    assert result["success"] is False
    assert result["status_code"] == 200

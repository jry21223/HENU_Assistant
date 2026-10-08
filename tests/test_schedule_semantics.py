from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from henu_mcp.tools import server_impl


def test_current_schedule_marks_teaching_week_as_unverified(monkeypatch) -> None:
    now = dt.datetime(2026, 8, 11, 11, 25, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(server_impl, "auto_calibrate_period_time", lambda force=False: {})
    monkeypatch.setattr(
        server_impl,
        "load_latest_clean_schedule",
        lambda _output: {
            "schedule": {
                "星期二": [
                    {
                        "course": "时间匹配课程",
                        "time": "1-18周 周二[3-4节]",
                        "classroom": "B201",
                    }
                ]
            }
        },
    )
    monkeypatch.setattr(
        server_impl,
        "_load_period_times",
        lambda: {
            "3": {"start": "10:10", "end": "10:55"},
            "4": {"start": "11:05", "end": "11:50"},
        },
    )
    monkeypatch.setattr(server_impl, "_now_dt", lambda _timezone="Asia/Shanghai": now)

    result = server_impl.get_current_course_status()

    assert result["success"] is True
    assert result["week_filter_applied"] is False
    assert result["current_courses"][0]["course"] == "时间匹配课程"
    assert "未验证教学周" in result["msg"]
    assert "正在上的课程" not in result["msg"]


def test_failed_seminar_query_is_not_rewritten_as_no_matching_rooms(monkeypatch) -> None:
    class FakeBot:
        def seminar_filter_options(self):
            return {"success": True, "filters": {}}

        def seminar_list(self, _payload):
            return {"success": False, "msg": "认证失效", "rooms": []}

        def get_cookies(self):
            return {}

    monkeypatch.setattr(
        server_impl,
        "_effective_profile",
        lambda: {"student_id": "test-user", "password": "test-secret"},
    )
    monkeypatch.setattr(server_impl, "_build_library_bot", lambda *_args: FakeBot())
    monkeypatch.setattr(server_impl, "_save_library_cookies", lambda *_args: None)

    result = server_impl._seminar_rooms_impl(target_date="2026-08-12")

    assert result["success"] is False
    assert result["msg"] == "认证失效"
    assert "empty_reason" not in result

from __future__ import annotations

import requests
import pytest

from henu_mcp.tools import server_impl


def test_reservation_requires_an_explicit_date_before_any_school_request(monkeypatch):
    calls = []

    def request(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("reservation validation must run before school HTTP")

    monkeypatch.setattr(requests.Session, "request", request)
    result = server_impl.library_reserve(
        location="第一自习室", seat_no="001",
        preferred_time="08:00", preferred_end_time="12:00",
    )

    assert result["success"] is False
    assert result["code"] == "missing_target_date"
    assert calls == []


@pytest.mark.parametrize(("changes", "expected"), [
    ({"target_date": "2026-02-30"}, "invalid_target_date"),
    ({"target_date": "20261101"}, "invalid_target_date"),
    ({"preferred_time": ""}, "missing_preferred_time"),
    ({"preferred_time": "随便"}, "invalid_preferred_time"),
    ({"preferred_time": "24:00"}, "invalid_preferred_time"),
    ({"preferred_end_time": "24:30"}, "invalid_preferred_end_time"),
    ({"preferred_end_time": "08:00"}, "invalid_time_range"),
    ({"preferred_end_time": ""}, "missing_end_strategy"),
    ({"preferred_end_time": "", "end_policy": "ask"}, "end_time_confirmation_required"),
    ({"end_policy": "invented"}, "invalid_end_policy"),
])
def test_invalid_reservations_fail_before_school_http(monkeypatch, changes, expected):
    def request(*_args, **_kwargs):
        pytest.fail("invalid reservation reached school HTTP")

    monkeypatch.setattr(requests.Session, "request", request)
    params = {
        "location": "第一自习室", "seat_no": "001",
        "target_date": "2026-11-01", "preferred_time": "08:00",
        "preferred_end_time": "12:00",
    }
    params.update(changes)

    result = server_impl.library_reserve(**params)

    assert result["success"] is False
    assert result["code"] == expected


def test_omitted_start_time_cannot_become_0800():
    result = server_impl.library_reserve(
        location="第一自习室", seat_no="001",
        target_date="2026-11-01", preferred_end_time="12:00",
    )
    assert result["code"] == "missing_preferred_time"


@pytest.mark.parametrize(("end", "policy"), [
    ("12:00", ""), ("", "server_full_slot"), ("12:00", "server_full_slot"),
])
def test_explicit_reservation_authorization_reaches_the_account_check(monkeypatch, tmp_path, end, policy):
    monkeypatch.setattr(server_impl, "PROFILE_FILE", tmp_path / "missing-profile.json")

    result = server_impl.library_reserve(
        location="区域", seat_no="001", target_date="2026-11-01",
        preferred_time="08:00", preferred_end_time=end, end_policy=policy,
    )

    assert result == {"success": False, "msg": "缺少账号"}

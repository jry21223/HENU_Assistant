from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from campus_core import seat_reservation
from campus_core.seat_reservation import SeatReservationMixin
from henu_mcp.tools import server_impl


class ReservationProbe(SeatReservationMixin):
    def _is_token_valid(self) -> bool:
        return True

    def _reserve_once(self, **arguments):
        return {"success": True, "retryable": False, "arguments": arguments}


class NoSideEffectProbe(SeatReservationMixin):
    def __init__(self) -> None:
        self.calls: list[str] = []

    def _unexpected(self, name: str):
        self.calls.append(name)
        raise AssertionError(f"{name} must not run for an invalid or expired retry deadline")

    def _is_token_valid(self) -> bool:
        return self._unexpected("_is_token_valid")

    def login(self) -> bool:
        return self._unexpected("login")

    def _reserve_once(self, **arguments):
        del arguments
        return self._unexpected("_reserve_once")


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch):
    now = dt.datetime(2026, 8, 9, 7, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(seat_reservation, "_now_dt", lambda: now)


@pytest.mark.parametrize(
    ("retry_until", "expected"),
    [
        ("08:30", "2026-08-09T08:30:00+08:00"),
        ("8：3", "2026-08-09T08:03:00+08:00"),
        ("2026-08-10T08:30:00", "2026-08-10T08:30:00+08:00"),
        ("2026-08-10T00:30:00Z", "2026-08-10T00:30:00+00:00"),
    ],
)
def test_reserve_preserves_retry_deadline_date_and_timezone(retry_until: str, expected: str) -> None:
    result = ReservationProbe().reserve(
        "第一自习室",
        "001",
        "2026-08-10",
        retry_until=retry_until,
    )

    assert result["success"] is True
    assert result["retry_until"] == expected


def test_reserve_rejects_invalid_retry_deadline() -> None:
    result = ReservationProbe().reserve(
        "第一自习室",
        "001",
        "2026-08-10",
        retry_until="tomorrow morning",
    )

    assert result == {"success": False, "msg": "retry_until 格式必须为 HH:MM 或 ISO 日期时间"}


def test_reserve_rejects_out_of_range_clock_time() -> None:
    result = ReservationProbe().reserve(
        "第一自习室",
        "001",
        "2026-08-10",
        retry_until="24:00",
    )

    assert result == {"success": False, "msg": "retry_until 格式必须为 HH:MM 或 ISO 日期时间"}


def test_retry_clock_time_uses_the_shared_hhmm_normalizer(monkeypatch) -> None:
    calls: list[str] = []

    def normalize(value: str) -> str:
        calls.append(value)
        return "08:03"

    monkeypatch.setattr(seat_reservation.TimeUtilsMixin, "_to_hhmm", staticmethod(normalize))

    result = ReservationProbe().reserve(
        "第一自习室",
        "001",
        "2026-08-10",
        retry_until="8：3",
    )

    assert result["retry_until"] == "2026-08-09T08:03:00+08:00"
    assert calls == ["8：3"]


def test_naive_iso_retry_uses_the_current_clock_timezone(monkeypatch) -> None:
    current_timezone = dt.timezone(dt.timedelta(hours=5, minutes=45))
    monkeypatch.setattr(
        seat_reservation,
        "_now_dt",
        lambda: dt.datetime(2026, 8, 9, 7, 0, tzinfo=current_timezone),
    )

    result = ReservationProbe().reserve(
        "第一自习室",
        "001",
        "2026-08-10",
        retry_until="2026-08-10T08:30:00",
    )

    assert result["retry_until"] == "2026-08-10T08:30:00+05:45"


@pytest.mark.parametrize(
    ("retry_until", "expected_message"),
    [
        ("tomorrow morning", "retry_until 格式必须为 HH:MM 或 ISO 日期时间"),
        ("06:59", "retry_until 已过期，未执行登录或预约"),
    ],
)
def test_invalid_or_expired_retry_deadline_has_no_auth_reservation_or_write_side_effects(
    retry_until: str,
    expected_message: str,
) -> None:
    probe = NoSideEffectProbe()

    result = probe.reserve(
        "第一自习室",
        "001",
        "2026-08-10",
        retry_until=retry_until,
    )

    assert result["success"] is False
    assert result["msg"] == expected_message
    assert probe.calls == []


def test_deadline_reached_after_auth_stops_before_reservation(monkeypatch) -> None:
    deadline = dt.datetime(2026, 8, 9, 8, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    clock = iter((deadline - dt.timedelta(seconds=1), deadline))
    calls: list[str] = []

    class Probe(SeatReservationMixin):
        def _is_token_valid(self) -> bool:
            calls.append("token")
            return True

        def _reserve_once(self, **arguments):
            del arguments
            calls.append("reserve")
            return {"success": True}

    monkeypatch.setattr(seat_reservation, "_now_dt", lambda: next(clock, deadline))
    result = Probe().reserve(
        "第一自习室",
        "001",
        "2026-08-10",
        retry_until="08:30",
    )

    assert result == {"success": False, "msg": "未执行预约尝试"}
    assert calls == ["token"]


@pytest.mark.parametrize("retry_until", ["24:00", "tomorrow morning", "06:59"])
def test_public_library_wrapper_rejects_retry_before_login(
    retry_until: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(server_impl, "HenuCampusBot", object)
    monkeypatch.setattr(
        server_impl,
        "_effective_profile",
        lambda: {
            "student_id": "student",
            "password": "secret",
            "library_location": "第一自习室",
            "library_seat_no": "001",
        },
    )
    monkeypatch.setattr(
        server_impl,
        "_build_library_bot",
        lambda *_args: calls.append("login"),
    )

    result = server_impl._library_reserve_impl(retry_until=retry_until)

    assert result["success"] is False
    assert calls == []


def test_public_library_wrapper_freezes_clock_deadline_before_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str] = []

    class Bot:
        def reserve(self, *_args, retry_until: str, **_kwargs):
            captured.append(retry_until)
            return {"success": False, "msg": "probe"}

        def get_cookies(self):
            return {}

        def get_cas_cookies(self):
            return {}

    now = dt.datetime(2026, 8, 9, 23, 58, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(seat_reservation, "_now_dt", lambda: now)
    monkeypatch.setattr(server_impl, "HenuCampusBot", object)
    monkeypatch.setattr(
        server_impl,
        "_effective_profile",
        lambda: {
            "student_id": "student",
            "password": "secret",
            "library_location": "第一自习室",
            "library_seat_no": "001",
        },
    )
    monkeypatch.setattr(server_impl, "_build_library_bot", lambda *_args: Bot())
    monkeypatch.setattr(server_impl, "_save_library_cookies", lambda *_args: None)
    monkeypatch.setattr(server_impl, "_save_cas_cookies", lambda *_args: None)

    result = server_impl._library_reserve_impl(
        target_date="2026-08-10",
        retry_until="23:59",
    )

    assert result["msg"] == "probe"
    assert captured == ["2026-08-09T23:59:00+08:00"]

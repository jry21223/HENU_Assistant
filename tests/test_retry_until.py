from __future__ import annotations

import datetime as dt
from unittest.mock import patch

from campus_core import seat_reservation
from campus_core.seat_reservation import SeatReservationMixin


def test_retry_until_parses_clock_time_on_current_day() -> None:
    now = dt.datetime(2026, 7, 13, 7, 30, tzinfo=dt.timezone(dt.timedelta(hours=8)))

    with patch.object(seat_reservation, "_now_dt", return_value=now):
        parsed = SeatReservationMixin._parse_retry_until("08:15")

    assert parsed == now.replace(hour=8, minute=15, second=0, microsecond=0)


def test_retry_until_preserves_iso_date_and_adds_local_timezone_when_missing() -> None:
    timezone = dt.timezone(dt.timedelta(hours=8))
    now = dt.datetime(2026, 7, 13, 7, 30, tzinfo=timezone)

    with patch.object(seat_reservation, "_now_dt", return_value=now):
        parsed = SeatReservationMixin._parse_retry_until("2026-07-14T08:15:00")

    assert parsed == dt.datetime(2026, 7, 14, 8, 15, tzinfo=timezone)


def test_retry_until_rejects_invalid_clock_time() -> None:
    assert SeatReservationMixin._parse_retry_until("25:99") is None

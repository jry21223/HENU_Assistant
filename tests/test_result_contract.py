from __future__ import annotations

from campus_core.locations import LocationMixin
from campus_core.seat_reservation import SeatReservationMixin


class _LocationClient(LocationMixin):
    def __init__(self, areas):
        self._areas = areas

    def _is_token_valid(self):
        return True

    def _fetch_pick_areas(self, target_date):
        return self._areas


class _SeatClient(SeatReservationMixin):
    def _is_token_valid(self):
        return True

    def _resolve_area(self, location_name, target_date):
        return "43", "第一自习室"

    def _get_space_map(self, area_id):
        return {"spaceId": "space-1"}

    def _build_reservation_plan(self, area_id, space_map, target_date, preferred_time="08:00", preferred_end_time=""):
        return {"seat_query": {}, "time_window": "08:00-12:00"}

    def _query_seats(self, seat_query):
        return [{"id": "1", "name": "A-101", "status": "1"}, {"id": "2", "name": "A-102", "status": "0"}]


def test_live_empty_locations_are_not_reported_as_success() -> None:
    result = _LocationClient([]).list_locations("2026-07-13")

    assert result["success"] is False
    assert result["error_code"] == "live_empty"
    assert result["source"] == "live_empty"
    assert result["is_live"] is True
    assert result["locations"] == []
    assert result["fallback_locations"]
    assert result["returned_count"] == 0
    assert result["truncated"] is False


def test_live_locations_and_seats_expose_machine_readable_counts() -> None:
    locations = _LocationClient(
        [{"name": "第一自习室", "id": 43}, {"name": "第二自习室", "id": 44}]
    ).list_locations("2026-07-13")
    seats = _SeatClient().list_available_seats(area_id="43", target_date="2026-07-13")

    assert locations["success"] is True
    assert locations["returned_count"] == 2
    assert locations["truncated"] is False
    assert {row["area_id"] for row in locations["locations"]} == {"43", "44"}
    assert seats["success"] is True
    assert seats["total_count"] == 2
    assert seats["available_count"] == 1
    assert seats["returned_count"] == 1
    assert seats["status_counts"] == {"1": 1, "0": 1}
    assert seats["seats"][0]["seat_no"] == "A-101"
    assert seats["seats"][0]["no"] == "A-101"

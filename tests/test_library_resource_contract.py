from __future__ import annotations

from unittest.mock import patch

from campus_core.resource_registry import sync as registry_sync
from campus_core.resource_registry.sync import (
    normalize_library_location,
    normalize_library_seat,
)
from henu_mcp.tools import server_impl


def test_library_dto_normalization_accepts_public_query_shapes() -> None:
    assert normalize_library_location(
        {"location": "第一自习室", "area_id": "43"}
    ) == ("43", "第一自习室")
    assert normalize_library_seat({"no": "A-101"}, fallback_area_id="43") == (
        "A-101",
        "43",
    )


def test_registry_sync_reuses_normalization_for_single_area_seat_results() -> None:
    records = []
    with patch.object(registry_sync, "upsert_resource", side_effect=records.append):
        result = registry_sync.sync_library_resources(
            [{"location": "第一自习室", "area_id": "43"}],
            [{"no": "A-101", "status": "1"}],
        )

    assert result == {"success": True, "synced_count": 2}
    assert [record.resource_type for record in records] == [
        "library_area",
        "library_seat",
    ]
    assert records[1].location["areaId"] == "43"
    assert records[1].location["seatNo"] == "A-101"


class _LibraryBot:
    def list_available_seats(self, **kwargs):
        return {
            "success": True,
            "area": {"id": "43", "name": "第一自习室"},
            "seats": [{"no": "A-101", "status": "1"}],
        }

    def get_cookies(self):
        return {}

    def get_cas_cookies(self):
        return {}


def test_library_seat_wrapper_uses_resolved_area_for_enrichment() -> None:
    bot = _LibraryBot()
    with (
        patch.object(server_impl, "HenuCampusBot", object()),
        patch.object(
            server_impl,
            "_effective_profile",
            return_value={"student_id": "20230001", "password": "secret"},
        ),
        patch.object(server_impl, "_build_library_bot", return_value=bot),
        patch.object(server_impl, "_save_library_cookies"),
        patch.object(server_impl, "_save_cas_cookies"),
        patch.object(server_impl, "_enrich_library_seats") as enrich,
    ):
        result = server_impl._library_seats_impl(location="第一自习室")

    enrich.assert_called_once_with(result["seats"], "43")

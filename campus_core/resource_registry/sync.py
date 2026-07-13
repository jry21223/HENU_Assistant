"""资源同步器。

提供三个同步器接口，将上游系统数据同步到全局 registry：
- sync_classrooms_from_empty_classroom(): 从空教室查询结果同步教室资源
- sync_library_resources(): 从图书馆查询结果同步区域/座位资源
- sync_seminar_resources(): 从研讨室查询结果同步房间资源
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .alias import generate_aliases, normalize_building_name, normalize_room_name
from .models import (
    RESOURCE_TYPE_CLASSROOM,
    RESOURCE_TYPE_CAMPUS,
    RESOURCE_TYPE_BUILDING,
    ResourceRecord,
    build_resource_id,
)
from .registry import upsert_resource


def _now_iso() -> str:
    return datetime.now().isoformat()


def _first_text(data: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def normalize_library_location(location: dict[str, Any]) -> tuple[str, str]:
    """Return the canonical area ID and name from a library location DTO."""
    if not isinstance(location, dict):
        return "", ""
    return (
        _first_text(location, "areaId", "area_id", "id"),
        _first_text(location, "areaName", "area_name", "name", "location"),
    )


def normalize_library_seat(
    seat: dict[str, Any],
    fallback_area_id: str = "",
) -> tuple[str, str]:
    """Return the canonical seat number and area ID from a seat DTO."""
    if not isinstance(seat, dict):
        return "", str(fallback_area_id or "").strip()
    return (
        _first_text(seat, "seatNo", "seat_no", "no", "name"),
        _first_text(seat, "areaId", "area_id") or str(fallback_area_id or "").strip(),
    )


# ── 教室同步 ──────────────────────────────────────────────


def sync_classrooms_from_metadata(
    campuses: list[dict[str, str]],
    buildings_by_campus: dict[str, list[dict[str, str]]],
    classrooms_by_building: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """从校区/楼房/教室元数据同步教室资源到 registry。

    Args:
        campuses: [{"code": "01", "name": "明伦校区"}, ...]
        buildings_by_campus: {"01": [{"code": "0013", "name": "十号楼"}, ...], ...}
        classrooms_by_building: {"0013": [{"room_id": "0000231", "room_name": "十号楼101", "capacity": 160}, ...], ...}

    Returns:
        {"success": bool, "synced_count": int, "errors": [str, ...]}
    """
    synced = 0
    errors: list[str] = []

    # 同步校区资源
    campus_map: dict[str, str] = {}
    for c in campuses:
        code = c.get("code", c.get("campus_code", ""))
        name = c.get("name", c.get("campus_name", ""))
        if not code or not name:
            continue
        campus_map[code] = name

        campus_rid = build_resource_id("campus", campus_code=code)
        record = ResourceRecord(
            resource_id=campus_rid,
            resource_type=RESOURCE_TYPE_CAMPUS,
            display_name=name,
            canonical_name=name,
            aliases=[name],
            source={"system": "xk"},
            location={"campusCode": code, "campusName": name},
            updated_at=_now_iso(),
        )
        upsert_resource(record)
        synced += 1

    # 同步楼房资源
    building_map: dict[str, dict[str, str]] = {}  # building_code → {code, name, campus}
    for campus_code, buildings in buildings_by_campus.items():
        campus_name = campus_map.get(campus_code, campus_code)
        for b in buildings:
            b_code = b.get("code", b.get("building_code", ""))
            b_name = b.get("name", b.get("building_name", ""))
            if not b_code or not b_name:
                continue
            building_map[b_code] = {"code": b_code, "name": b_name, "campus_code": campus_code}

            cn_name = normalize_building_name(b_name)
            building_rid = build_resource_id("building", campus_code=campus_code, building_code=b_code)

            record = ResourceRecord(
                resource_id=building_rid,
                resource_type=RESOURCE_TYPE_BUILDING,
                display_name=f"{campus_name} {cn_name}",
                canonical_name=cn_name,
                aliases=generate_aliases(campus_name, b_name),
                source={"system": "xk", "source_building_code": b_code},
                location={"campusCode": campus_code, "campusName": campus_name, "buildingCode": b_code, "buildingName": b_name},
                updated_at=_now_iso(),
            )
            upsert_resource(record)
            synced += 1

    # 同步教室资源
    for building_code, classrooms in classrooms_by_building.items():
        bld_info = building_map.get(building_code, {})
        bld_name = bld_info.get("name", building_code)
        campus_code = bld_info.get("campus_code", "")
        campus_name = campus_map.get(campus_code, campus_code)

        for cr in classrooms:
            room_id = cr.get("room_id", cr.get("roomId", ""))
            room_name = cr.get("room_name", cr.get("roomName", ""))
            capacity = cr.get("capacity", 0)
            type_name = cr.get("type_name", cr.get("typeName", ""))

            if not room_id or not room_name:
                continue

            rid = build_resource_id(
                "classroom",
                campus_code=campus_code,
                building_code=building_code,
                room_id=room_id,
            )

            cn_building = normalize_building_name(bld_name)
            cn_room = normalize_room_name(room_name)
            display_name = f"{campus_name} {cn_building}{cn_room}"

            record = ResourceRecord(
                resource_id=rid,
                resource_type=RESOURCE_TYPE_CLASSROOM,
                display_name=display_name,
                canonical_name=f"{cn_building}{cn_room}",
                aliases=generate_aliases(campus_name, bld_name, room_name),
                source={
                    "system": "xk",
                    "source_room_code": room_name,
                    "source_building_code": building_code,
                },
                location={
                    "campusCode": campus_code,
                    "campusName": campus_name,
                    "buildingCode": building_code,
                    "buildingName": bld_name,
                    "roomName": room_name,
                    "capacity": capacity,
                    "typeName": type_name,
                },
                attributes={"capacity": capacity, "type_name": type_name},
                updated_at=_now_iso(),
            )
            upsert_resource(record)
            synced += 1

    return {"success": True, "synced_count": synced, "errors": errors}


# ── 图书馆同步 ────────────────────────────────────────────


def sync_library_resources(
    locations: list[dict[str, Any]],
    seats: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """从图书馆查询结果同步区域和座位资源。

    Args:
        locations: library_query(view="locations") 的返回数据。
        seats: library_query(view="seats") 的返回数据（可选）。

    Returns:
        {"success": bool, "synced_count": int}
    """
    synced = 0

    library_id = "henu_library"  # 河南大学图书馆统一 ID
    fallback_area_id = ""
    if len(locations) == 1:
        fallback_area_id, _ = normalize_library_location(locations[0])

    for loc in locations:
        area_id, area_name = normalize_library_location(loc)
        if not area_id or not area_name:
            continue

        rid = build_resource_id("library_area", library_id=library_id, area_id=area_id)

        record = ResourceRecord(
            resource_id=rid,
            resource_type="library_area",
            display_name=f"图书馆 {area_name}",
            canonical_name=area_name,
            aliases=[area_name, f"图书馆{area_name}"],
            source={"system": "library", "source_id": area_id},
            location={"libraryId": library_id, "areaId": area_id, "areaName": area_name},
            updated_at=_now_iso(),
        )
        upsert_resource(record)
        synced += 1

    if seats:
        for seat in seats:
            seat_no, area_id = normalize_library_seat(seat, fallback_area_id)
            if not seat_no or not area_id:
                continue

            rid = build_resource_id(
                "library_seat",
                library_id=library_id,
                area_id=area_id,
                seat_no=seat_no,
            )

            record = ResourceRecord(
                resource_id=rid,
                resource_type="library_seat",
                display_name=f"图书馆座位 {seat_no}",
                canonical_name=seat_no,
                aliases=[seat_no, f"座位{seat_no}"],
                source={"system": "library", "source_id": seat_no},
                location={"libraryId": library_id, "areaId": area_id, "seatNo": seat_no},
                updated_at=_now_iso(),
            )
            upsert_resource(record)
            synced += 1

    return {"success": True, "synced_count": synced}


# ── 研讨室同步 ────────────────────────────────────────────


def sync_seminar_resources(
    rooms: list[dict[str, Any]],
) -> dict[str, Any]:
    """从研讨室查询结果同步房间资源。

    Args:
        rooms: seminar_query(view="rooms") 的返回数据。

    Returns:
        {"success": bool, "synced_count": int}
    """
    synced = 0

    for room in rooms:
        area_id = str(room.get("areaId", room.get("area_id", room.get("id", ""))))
        room_name = str(room.get("roomName", room.get("room_name", room.get("name", ""))))
        if not area_id:
            continue

        rid = build_resource_id("seminar_room", area_id=area_id)

        capacity = room.get("capacity", room.get("seatCount", 0))
        floor = room.get("floor", room.get("floorName", ""))

        record = ResourceRecord(
            resource_id=rid,
            resource_type="seminar_room",
            display_name=f"研讨室 {room_name}" if room_name else f"研讨室 {area_id}",
            canonical_name=room_name or area_id,
            aliases=[room_name, f"研讨室{room_name}"] if room_name else [],
            source={"system": "seminar", "source_id": area_id},
            location={"areaId": area_id, "roomName": room_name, "capacity": capacity, "floor": floor},
            attributes={"capacity": capacity, "floor": floor},
            updated_at=_now_iso(),
        )
        upsert_resource(record)
        synced += 1

    return {"success": True, "synced_count": synced}

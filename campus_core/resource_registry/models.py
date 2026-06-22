"""全局资源编号映射 — 数据模型。

统一管理教室、图书馆区域/座位、研讨室、楼房、校区的资源标识。
资源 ID 规则：
    henu:classroom:xk:<campusCode>:<buildingCode>:<roomCode>
    henu:library:area:<libraryId>:<areaId>
    henu:library:seat:<libraryId>:<areaId>:<seatNo>
    henu:seminar:room:<areaId>
    henu:building:<campusCode>:<buildingCode>
    henu:campus:<campusCode>
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# ── 资源类型枚举 ──────────────────────────────────────────

RESOURCE_TYPE_CLASSROOM = "classroom"
RESOURCE_TYPE_LIBRARY_AREA = "library_area"
RESOURCE_TYPE_LIBRARY_SEAT = "library_seat"
RESOURCE_TYPE_SEMINAR_ROOM = "seminar_room"
RESOURCE_TYPE_BUILDING = "building"
RESOURCE_TYPE_CAMPUS = "campus"

ALL_RESOURCE_TYPES = {
    RESOURCE_TYPE_CLASSROOM,
    RESOURCE_TYPE_LIBRARY_AREA,
    RESOURCE_TYPE_LIBRARY_SEAT,
    RESOURCE_TYPE_SEMINAR_ROOM,
    RESOURCE_TYPE_BUILDING,
    RESOURCE_TYPE_CAMPUS,
}


def build_resource_id(resource_type: str, **parts: str) -> str:
    """构建标准资源 ID。

    Args:
        resource_type: classroom / library_area / library_seat / seminar_room / building / campus
        **parts: 各类型所需的部件。

    Returns:
        如 "henu:classroom:xk:01:0013:0000231"
    """
    prefix = "henu"
    if resource_type == RESOURCE_TYPE_CLASSROOM:
        return f"{prefix}:classroom:xk:{parts['campus_code']}:{parts['building_code']}:{parts['room_id']}"
    if resource_type == RESOURCE_TYPE_LIBRARY_AREA:
        return f"{prefix}:library:area:{parts['library_id']}:{parts['area_id']}"
    if resource_type == RESOURCE_TYPE_LIBRARY_SEAT:
        return f"{prefix}:library:seat:{parts['library_id']}:{parts['area_id']}:{parts['seat_no']}"
    if resource_type == RESOURCE_TYPE_SEMINAR_ROOM:
        return f"{prefix}:seminar:room:{parts['area_id']}"
    if resource_type == RESOURCE_TYPE_BUILDING:
        return f"{prefix}:building:{parts['campus_code']}:{parts['building_code']}"
    if resource_type == RESOURCE_TYPE_CAMPUS:
        return f"{prefix}:campus:{parts['campus_code']}"
    raise ValueError(f"未知资源类型: {resource_type}")


def parse_resource_id(resource_id: str) -> dict[str, str]:
    """解析资源 ID 为部件字典。

    Args:
        resource_id: 如 "henu:classroom:xk:01:0013:0000231"

    Returns:
        {"type": "classroom", "campus_code": "01", "building_code": "0013", "room_id": "0000231", ...}
    """
    parts = resource_id.split(":")
    result: dict[str, str] = {"resource_id": resource_id}

    if len(parts) < 3 or parts[0] != "henu":
        result["type"] = "unknown"
        return result

    category = parts[1]
    subcategory = parts[2] if len(parts) > 2 else ""

    if category == "classroom" and subcategory == "xk":
        result["type"] = RESOURCE_TYPE_CLASSROOM
        result["campus_code"] = parts[3] if len(parts) > 3 else ""
        result["building_code"] = parts[4] if len(parts) > 4 else ""
        result["room_id"] = parts[5] if len(parts) > 5 else ""
    elif category == "library":
        if subcategory == "area":
            result["type"] = RESOURCE_TYPE_LIBRARY_AREA
            result["library_id"] = parts[3] if len(parts) > 3 else ""
            result["area_id"] = parts[4] if len(parts) > 4 else ""
        elif subcategory == "seat":
            result["type"] = RESOURCE_TYPE_LIBRARY_SEAT
            result["library_id"] = parts[3] if len(parts) > 3 else ""
            result["area_id"] = parts[4] if len(parts) > 4 else ""
            result["seat_no"] = parts[5] if len(parts) > 5 else ""
    elif category == "seminar" and subcategory == "room":
        result["type"] = RESOURCE_TYPE_SEMINAR_ROOM
        result["area_id"] = parts[3] if len(parts) > 3 else ""
    elif category == "building":
        result["type"] = RESOURCE_TYPE_BUILDING
        result["campus_code"] = parts[2] if len(parts) > 2 else ""
        result["building_code"] = parts[3] if len(parts) > 3 else ""
    elif category == "campus":
        result["type"] = RESOURCE_TYPE_CAMPUS
        result["campus_code"] = parts[2] if len(parts) > 2 else ""
    else:
        result["type"] = "unknown"

    return result


@dataclass(frozen=True)
class ResourceRecord:
    """全局资源记录。"""

    resource_id: str
    resource_type: str
    display_name: str  # "明伦校区 十号楼101"
    canonical_name: str  # "十号楼101"
    aliases: list[str] = field(default_factory=list)
    source: dict[str, Any] = field(default_factory=dict)
    location: dict[str, Any] = field(default_factory=dict)
    attributes: dict[str, Any] = field(default_factory=dict)
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "resourceId": self.resource_id,
            "resourceType": self.resource_type,
            "displayName": self.display_name,
            "canonicalName": self.canonical_name,
            "aliases": self.aliases,
            "source": self.source,
            "location": self.location,
            "attributes": self.attributes,
            "updatedAt": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResourceRecord:
        return cls(
            resource_id=data.get("resourceId", data.get("resource_id", "")),
            resource_type=data.get("resourceType", data.get("resource_type", "")),
            display_name=data.get("displayName", data.get("display_name", "")),
            canonical_name=data.get("canonicalName", data.get("canonical_name", "")),
            aliases=data.get("aliases", []),
            source=data.get("source", {}),
            location=data.get("location", {}),
            attributes=data.get("attributes", {}),
            updated_at=data.get("updatedAt", data.get("updated_at", "")),
        )


@dataclass(frozen=True)
class ResolveCandidate:
    """自然语言解析候选项。"""

    resource_id: str
    score: float  # 0.0 - 1.0
    display_name: str
    resource_type: str
    matched_alias: str  # 匹配到的别名

    def to_dict(self) -> dict[str, Any]:
        return {
            "resourceId": self.resource_id,
            "score": self.score,
            "displayName": self.display_name,
            "resourceType": self.resource_type,
            "matchedAlias": self.matched_alias,
        }

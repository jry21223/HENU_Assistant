"""资源 Registry — CRUD 操作。

提供资源的增删改查，构建并维护别名反向索引。
"""

from __future__ import annotations

import re
from datetime import datetime

from .models import (
    ResourceRecord,
    build_resource_id,
    matches_campus,
)
from .storage import (
    ResourceBundle,
    get_resource_record,
    load_resources,
    lookup_by_alias,
    upsert_resource_bundles,
)


def _now_iso() -> str:
    return datetime.now().isoformat()


def upsert_resource(record: ResourceRecord) -> ResourceRecord:
    """插入或更新一条资源记录。同时更新别名索引。"""
    upsert_resources([record])
    return record


def upsert_resources(records: list[ResourceRecord]) -> list[ResourceRecord]:
    """批量更新资源和派生索引，整批只提交一次快照。"""
    bundles = []
    for record in records:
        data = record.to_dict()
        if not data.get('updatedAt'):
            data['updatedAt'] = _now_iso()
        aliases = set(record.aliases)
        aliases.update([record.canonical_name, record.display_name])
        bundles.append(ResourceBundle(
            record=data, aliases={a.strip() for a in aliases if a.strip()},
            source_system=str(record.source.get('system') or ''),
            source_id=str(record.source.get('source_id') or ''),
        ))
    upsert_resource_bundles(bundles)
    return records


def get_resource(resource_id: str) -> ResourceRecord | None:
    """按 resource_id 获取资源记录。"""
    d = get_resource_record(resource_id)
    if d:
        return ResourceRecord.from_dict(d)
    return None


def search_resources(
    query: str = "",
    resource_type: str = "",
    campus_code: str = "",
    building_code: str = "",
    limit: int = 20,
) -> list[ResourceRecord]:
    """搜索资源。

    匹配优先级：
    1. 精确别名匹配（alias index）
    2. display_name 包含 query
    3. canonical_name 包含 query
    """
    results: list[ResourceRecord] = []
    seen_ids: set[str] = set()

    query_norm = re.sub(r"\s+", "", query.strip().lower()) if query else ""

    # 1. 别名索引精确匹配
    if query_norm:
        matched_ids = lookup_by_alias(query_norm)
        for rid in matched_ids:
            record = get_resource(rid)
            if record and rid not in seen_ids:
                if resource_type and record.resource_type != resource_type:
                    continue
                if not matches_campus(record.location, campus_code):
                    continue
                results.append(record)
                seen_ids.add(rid)

    # 2. 全文搜索
    all_records = load_resources()
    for rid, d in all_records.items():
        if rid in seen_ids:
            continue
        if resource_type and d.get("resourceType", "") != resource_type:
            continue

        # campus_code 过滤
        loc = d.get("location", {})
        if not matches_campus(loc, campus_code):
            continue
        if building_code and loc.get("buildingCode", loc.get("building_code", "")) != building_code:
            continue

        # 文本匹配（去空格后比较，双向：查询包含别名 或 别名包含查询）
        if query_norm:
            display = re.sub(r"\s+", "", (d.get("displayName", "") or "").lower())
            canonical = re.sub(r"\s+", "", (d.get("canonicalName", "") or "").lower())
            aliases_list = [re.sub(r"\s+", "", a.lower()) for a in (d.get("aliases", []) or [])]
            matched = (
                query_norm in display
                or query_norm in canonical
                or any(query_norm in a for a in aliases_list)
                or any(a in query_norm for a in aliases_list if len(a) >= 2)
            )
            if not matched:
                continue

        record = ResourceRecord.from_dict(d)
        results.append(record)
        seen_ids.add(rid)

        if len(results) >= limit:
            break

    return results[:limit]


def list_resources(
    resource_type: str = "",
    campus_code: str = "",
    building_code: str = "",
    limit: int = 100,
) -> list[ResourceRecord]:
    """列出资源。返回满足过滤条件的所有记录。"""
    return search_resources(
        query="",
        resource_type=resource_type,
        campus_code=campus_code,
        building_code=building_code,
        limit=limit,
    )


def get_stats() -> dict[str, int]:
    """获取 registry 统计信息。"""
    all_records = load_resources()
    counts: dict[str, int] = {}
    for d in all_records.values():
        rt = d.get("resourceType", "unknown")
        counts[rt] = counts.get(rt, 0) + 1
    counts["total"] = len(all_records)
    return counts


def ensure_classroom_resource(
    campus_code: str,
    campus_name: str,
    building_code: str,
    building_name: str,
    room_id: str,
    room_name: str,
    capacity: int = 0,
    type_name: str = "",
) -> ResourceRecord:
    """确保教室资源存在，不存在则自动创建。

    用于空教室查询时增量写入 registry。
    """
    resource_id = build_resource_id(
        "classroom",
        campus_code=campus_code,
        building_code=building_code,
        room_id=room_id,
    )

    existing = get_resource(resource_id)
    if existing:
        return existing

    from .alias import generate_aliases, normalize_building_name, normalize_room_name

    cn_building = normalize_building_name(building_name)
    cn_room = normalize_room_name(room_name)
    display_name = f"{campus_name} {cn_building}{cn_room}"
    aliases_list = generate_aliases(campus_name, building_name, room_name)
    aliases_list.append(f"{cn_building}{cn_room}")

    record = ResourceRecord(
        resource_id=resource_id,
        resource_type="classroom",
        display_name=display_name,
        canonical_name=f"{cn_building}{cn_room}",
        aliases=list(set(aliases_list)),
        source={
            "system": "xk",
            "source_room_code": room_name,
            "source_building_code": building_code,
        },
        location={
            "campusCode": campus_code,
            "campusName": campus_name,
            "buildingCode": building_code,
            "buildingName": building_name,
            "roomName": room_name,
            "capacity": capacity,
            "typeName": type_name,
        },
        attributes={"auto_created": True},
        updated_at=_now_iso(),
    )

    return upsert_resource(record)

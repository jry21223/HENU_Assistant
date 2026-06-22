"""空教室查询核心逻辑。

提供 query_free_classrooms() 和 sync_schedule() 两个主要入口。

查询流程：
1. 根据 campus_code/building_code 确定需要查询的楼房
2. 按刷新策略检查/刷新缓存
3. 解析课表得到所有占用信息
4. 排除在目标时间有课程占用的教室
5. 返回空闲教室列表 + freshness 信息
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from .client import EmptyClassroomClient
from .lock import FileLock, lock_path_for
from .models import FreeClassroomResult, Freshness, ScheduleCell, SyncResult
from .parser import parse_schedule_html
from .storage import (
    compute_query_hash,
    format_iso,
    get_locks_dir,
    get_parsed_schedule_age,
    get_query_cache_age,
    get_schedule_html_age,
    load_parsed_schedule,
    load_query_cache,
    load_schedule_html,
    save_parsed_schedule,
    save_query_cache,
    save_schedule_html,
)

# ── 默认 TTL ──────────────────────────────────────────────

DEFAULT_TTL_SECONDS = 300  # 5 分钟
DEFAULT_MAX_STALE_SECONDS = 86400  # 24 小时
LOCK_TIMEOUT_SECONDS = 8.0


# ── 公开 API ──────────────────────────────────────────────


def sync_schedule(
    client: EmptyClassroomClient,
    term_code: str,
    campus_code: str,
    building_code: str,
    type_code: str = "",
    force_refresh: bool = False,
) -> SyncResult:
    """同步指定楼房的教室课表缓存。

    Args:
        client: 已认证的 EmptyClassroomClient。
        term_code: 学期代码。
        campus_code: 校区代码。
        building_code: 楼房代码。
        type_code: 教室类型代码，空表示全部。
        force_refresh: 是否强制刷新（跳过缓存检查）。

    Returns:
        SyncResult 包含同步状态和统计信息。
    """
    now = format_iso()

    # 检查缓存
    if not force_refresh:
        html_age = get_schedule_html_age(term_code, campus_code, building_code)
        if html_age < DEFAULT_TTL_SECONDS:
            parsed = load_parsed_schedule(term_code, campus_code, building_code)
            if parsed:
                return SyncResult(
                    term_code=term_code,
                    campus_code=campus_code,
                    building_code=building_code,
                    sync_status="skipped",
                    room_count=len(parsed.get("classrooms", [])),
                    schedule_cell_count=len(parsed.get("cells", [])),
                    updated_at=format_iso(time.time() - html_age),
                )

    # 加锁刷新
    locks_dir = get_locks_dir()
    lock_path = lock_path_for(term_code, campus_code, building_code, locks_dir)
    lock = FileLock(lock_path, timeout=LOCK_TIMEOUT_SECONDS)

    if not lock.acquire():
        # 无法获取锁，尝试返回旧缓存
        parsed = load_parsed_schedule(term_code, campus_code, building_code)
        if parsed:
            return SyncResult(
                term_code=term_code,
                campus_code=campus_code,
                building_code=building_code,
                sync_status="skipped",
                room_count=len(parsed.get("classrooms", [])),
                schedule_cell_count=len(parsed.get("cells", [])),
                updated_at="",
                error="另一刷新任务正在进行中，返回旧缓存",
            )
        return SyncResult(
            term_code=term_code,
            campus_code=campus_code,
            building_code=building_code,
            sync_status="failed",
            room_count=0,
            schedule_cell_count=0,
            updated_at=now,
            error="无法获取刷新锁且无旧缓存",
        )

    try:
        # 再次检查缓存（双重检查锁）
        if not force_refresh:
            html_age = get_schedule_html_age(term_code, campus_code, building_code)
            if html_age < DEFAULT_TTL_SECONDS:
                parsed = load_parsed_schedule(term_code, campus_code, building_code)
                if parsed:
                    return SyncResult(
                        term_code=term_code,
                        campus_code=campus_code,
                        building_code=building_code,
                        sync_status="skipped",
                        room_count=len(parsed.get("classrooms", [])),
                        schedule_cell_count=len(parsed.get("cells", [])),
                        updated_at=format_iso(time.time() - html_age),
                    )

        # 拉取上游 HTML
        html_text = client.fetch_schedule_html(
            term_code=term_code,
            campus_code=campus_code,
            building_code=building_code,
            type_code=type_code,
        )

        if not html_text:
            # 上游请求失败，尝试返回旧缓存
            parsed = load_parsed_schedule(term_code, campus_code, building_code)
            if parsed:
                return SyncResult(
                    term_code=term_code,
                    campus_code=campus_code,
                    building_code=building_code,
                    sync_status="skipped",
                    room_count=len(parsed.get("classrooms", [])),
                    schedule_cell_count=len(parsed.get("cells", [])),
                    updated_at="",
                    error="上游请求失败，返回旧缓存",
                )
            return SyncResult(
                term_code=term_code,
                campus_code=campus_code,
                building_code=building_code,
                sync_status="failed",
                room_count=0,
                schedule_cell_count=0,
                updated_at=now,
                error="上游请求失败且无旧缓存",
            )

        # 保存原始 HTML
        save_schedule_html(html_text, term_code, campus_code, building_code)

        # 解析
        parsed = parse_schedule_html(
            html_text,
            term_code=term_code,
            campus_code=campus_code,
            building_code=building_code,
        )

        # 保存解析结果
        save_parsed_schedule(parsed, term_code, campus_code, building_code)

        return SyncResult(
            term_code=term_code,
            campus_code=campus_code,
            building_code=building_code,
            sync_status="success",
            room_count=len(parsed.get("classrooms", [])),
            schedule_cell_count=len(parsed.get("cells", [])),
            updated_at=now,
        )

    except Exception as exc:
        return SyncResult(
            term_code=term_code,
            campus_code=campus_code,
            building_code=building_code,
            sync_status="failed",
            room_count=0,
            schedule_cell_count=0,
            updated_at=now,
            error=f"{exc.__class__.__name__}: {exc}",
        )
    finally:
        lock.release()


def query_free_classrooms(
    client: EmptyClassroomClient,
    term_code: str,
    week: int,
    day_of_week: int,
    period: int,
    campus_code: str = "",
    building_code: str = "",
    type_code: str = "",
    min_capacity: int = 0,
    keyword: str = "",
    freshness: str = "cache_first",
    force_refresh: bool = False,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    max_stale_seconds: int = DEFAULT_MAX_STALE_SECONDS,
) -> FreeClassroomResult:
    """查询空教室。

    Args:
        client: 已认证的 EmptyClassroomClient。
        term_code: 学期代码，如 "2025,1"。
        week: 教学周。
        day_of_week: 星期，1-7。
        period: 大节，1-5 或 9。
        campus_code: 校区代码。
        building_code: 楼房代码。
        type_code: 教室类型代码。
        min_capacity: 最小容量。
        keyword: 教室名称关键词。
        freshness: 刷新策略 (cache_first | live | stale_while_revalidate | cache_only)。
        force_refresh: 强制刷新。
        ttl_seconds: 缓存 TTL（秒）。
        max_stale_seconds: 最大容忍过期时间（秒）。

    Returns:
        FreeClassroomResult 包含空闲教室列表和 freshness 信息。
    """
    now = time.time()
    fetched_at = ""
    parsed_at = ""
    live_checked = False
    strategy = freshness

    # 计算查询缓存哈希
    query_hash = compute_query_hash(
        term_code=term_code,
        week=week,
        day_of_week=day_of_week,
        period=period,
        campus_code=campus_code,
        building_code=building_code,
        type_code=type_code,
        min_capacity=min_capacity,
        keyword=keyword,
    )

    # ── cache_only 模式 ──
    if freshness == "cache_only":
        cached = load_query_cache(query_hash, ttl_seconds=999999)
        if cached:
            return _build_result_from_cache(cached, strategy, False, False, ttl_seconds)
        # 无缓存也返回空结果
        return _empty_result(
            term_code, week, day_of_week, period, campus_code, building_code,
            strategy, False, ttl_seconds,
        )

    # ── cache_first 模式 ──
    if freshness == "cache_first" and not force_refresh:
        cached = load_query_cache(query_hash, ttl_seconds=ttl_seconds)
        if cached:
            age = get_query_cache_age(query_hash)
            return _build_result_from_cache(
                cached, strategy, False, False, ttl_seconds,
            )
        # 缓存未命中，继续刷新

    # ── stale_while_revalidate 模式 ──
    if freshness == "stale_while_revalidate" and not force_refresh:
        cached = load_query_cache(query_hash, ttl_seconds=ttl_seconds)
        if cached:
            # 先返回旧缓存，后台刷新标记
            return _build_result_from_cache(
                cached, strategy, False, True, ttl_seconds,
            )
        # 无缓存，继续刷新

    # ── live 模式 / 需要刷新 ──
    # 确定需要刷新的楼房
    buildings_to_refresh = _resolve_buildings(client, campus_code, building_code)

    all_classrooms: list[dict[str, Any]] = []
    all_cells: list[dict[str, Any]] = []
    refresh_errors: list[str] = []

    for bld in buildings_to_refresh:
        bld_campus = bld.get("campus_code", campus_code)
        bld_code = bld.get("building_code", "")

        # 检查缓存是否有效
        if not force_refresh and freshness not in ("live",):
            html_age = get_schedule_html_age(term_code, bld_campus, bld_code)
            if html_age < ttl_seconds:
                parsed = load_parsed_schedule(term_code, bld_campus, bld_code)
                if parsed:
                    all_classrooms.extend(parsed.get("classrooms", []))
                    all_cells.extend(parsed.get("cells", []))
                    continue

        # 需要刷新
        sync_result = sync_schedule(
            client=client,
            term_code=term_code,
            campus_code=bld_campus,
            building_code=bld_code,
            type_code=type_code,
            force_refresh=force_refresh or freshness == "live",
        )

        if sync_result.sync_status == "failed":
            refresh_errors.append(
                f"{bld_campus}/{bld_code}: {sync_result.error}"
            )
        else:
            live_checked = True
            parsed = load_parsed_schedule(term_code, bld_campus, bld_code)
            if parsed:
                all_classrooms.extend(parsed.get("classrooms", []))
                all_cells.extend(parsed.get("cells", []))

    # 确定缓存时间戳
    if live_checked:
        fetched_at = format_iso(now)
        parsed_at = format_iso(now)
    else:
        # 使用已有缓存的时间戳
        first_age = float("inf")
        for bld in buildings_to_refresh:
            age = get_parsed_schedule_age(
                term_code, bld.get("campus_code", campus_code), bld.get("building_code", "")
            )
            first_age = min(first_age, age)
        if first_age < float("inf"):
            fetched_at = format_iso(now - first_age)
            parsed_at = fetched_at
        else:
            fetched_at = format_iso(now)
            parsed_at = format_iso(now)

    # ── 计算空闲教室 ──
    free_rooms = _compute_free_rooms(
        all_classrooms=all_classrooms,
        all_cells=all_cells,
        week=week,
        day_of_week=day_of_week,
        period=period,
        campus_code=campus_code,
        building_code=building_code,
        min_capacity=min_capacity,
        keyword=keyword,
    )

    # ── 构建 freshness ──
    cache_age = min(
        get_query_cache_age(query_hash),
        get_parsed_schedule_age(term_code, campus_code, building_code)
        if building_code else float("inf"),
    )
    if cache_age == float("inf"):
        cache_age = 0

    freshness_obj = Freshness(
        strategy=strategy,
        source="xk_room_schedule",
        fetched_at=fetched_at,
        parsed_at=parsed_at,
        ttl_seconds=ttl_seconds,
        age_seconds=int(cache_age),
        stale=cache_age > ttl_seconds,
        live_checked=live_checked,
    )

    # ── 构建查询摘要 ──
    query_summary: dict[str, Any] = {
        "termCode": term_code,
        "week": week,
        "dayOfWeek": day_of_week,
        "dayName": _day_name(day_of_week),
        "period": period,
        "periodName": _period_name(period),
        "campusCode": campus_code,
        "buildingCode": building_code,
        "minCapacity": min_capacity,
    }

    # 补充校区/楼房名称
    campus_map = client.get_campus_map()
    building_map: dict[str, str] = {}
    if campus_code:
        building_map = client.get_building_map(campus_code)

    if campus_code and campus_code in campus_map:
        query_summary["campusName"] = campus_map[campus_code]
    if building_code and building_code in building_map:
        query_summary["buildingName"] = building_map[building_code]

    # 补充每个 room 的 campusName/buildingName + resource_id
    _enrich_rooms_with_resource_id(
        free_rooms,
        campus_map=campus_map,
        building_map=building_map,
        default_campus_code=campus_code,
        default_building_code=building_code,
    )

    # ── 构建 resolved 块 ──
    resolved: dict[str, Any] = {}
    if campus_code and campus_code in campus_map:
        resolved["campus"] = {"campusCode": campus_code, "campusName": campus_map[campus_code]}
    if building_code and building_code in building_map:
        resolved["building"] = {"buildingCode": building_code, "buildingName": building_map[building_code]}

    result = FreeClassroomResult(
        query=query_summary,
        total=len(free_rooms),
        rooms=free_rooms,
        freshness=freshness_obj.to_dict(),
        resolved=resolved,
    )

    # 缓存查询结果
    if free_rooms or live_checked:
        save_query_cache(query_hash, result.to_dict())

    return result


# ── 内部辅助函数 ──────────────────────────────────────────


def _resolve_buildings(
    client: EmptyClassroomClient,
    campus_code: str,
    building_code: str,
) -> list[dict[str, str]]:
    """解析需要查询的楼房列表。"""
    if building_code:
        return [{"campus_code": campus_code, "building_code": building_code}]

    if campus_code:
        buildings = client.fetch_buildings(campus_code)
        return [
            {"campus_code": campus_code, "building_code": b.building_code}
            for b in buildings
        ]

    # 无筛选条件 → 查所有校区所有楼房
    result: list[dict[str, str]] = []
    campuses = client.fetch_campuses()
    for campus in campuses:
        buildings = client.fetch_buildings(campus.campus_code)
        for b in buildings:
            result.append(
                {"campus_code": campus.campus_code, "building_code": b.building_code}
            )
    return result


def _compute_free_rooms(
    all_classrooms: list[dict[str, Any]],
    all_cells: list[dict[str, Any]],
    week: int,
    day_of_week: int,
    period: int,
    campus_code: str = "",
    building_code: str = "",
    min_capacity: int = 0,
    keyword: str = "",
) -> list[dict[str, Any]]:
    """计算空闲教室列表。

    排除在目标 (week, day_of_week, period) 有课程占用的教室。
    """
    # 找出被占用的教室
    occupied_rooms: set[str] = set()
    for cell_dict in all_cells:
        cell = ScheduleCell(
            term_code=cell_dict.get("termCode", cell_dict.get("term_code", "")),
            room_id=cell_dict.get("roomId", cell_dict.get("room_id", "")),
            room_name=cell_dict.get("roomName", cell_dict.get("room_name", "")),
            day_of_week=cell_dict.get("dayOfWeek", cell_dict.get("day_of_week", 0)),
            period=cell_dict.get("period", 0),
            week_bitmap=cell_dict.get("weeks", cell_dict.get("week_bitmap", [])),
            week_expr=cell_dict.get("weekExpr", cell_dict.get("week_expr", "")),
            section_text=cell_dict.get("sectionText", cell_dict.get("section_text", "")),
            course_name=cell_dict.get("courseName", cell_dict.get("course_name", "")),
            teacher_name=cell_dict.get("teacherName", cell_dict.get("teacher_name", "")),
            class_name=cell_dict.get("className", cell_dict.get("class_name", "")),
            student_count=cell_dict.get("studentCount", cell_dict.get("student_count", 0)),
            department_name=cell_dict.get("departmentName", cell_dict.get("department_name", "")),
            raw_text=cell_dict.get("rawText", cell_dict.get("raw_text", "")),
        )
        if cell.occupies(week, day_of_week, period):
            occupied_rooms.add(cell.room_name)

    # 从所有教室中排除被占用的
    free: list[dict[str, Any]] = []
    for room in all_classrooms:
        room_name = room.get("room_name", "")
        capacity = room.get("capacity", 0)

        if room_name in occupied_rooms:
            continue
        if min_capacity > 0 and capacity < min_capacity:
            continue
        if keyword and keyword not in room_name:
            continue

        free.append(
            {
                "roomId": room.get("room_id", ""),
                "roomName": room_name,
                "campusCode": campus_code,
                "campusName": "",
                "buildingCode": building_code,
                "buildingName": "",
                "capacity": capacity,
                "typeName": room.get("type_name", ""),
                "status": "free",
            }
        )

    # 按教室名排序
    free.sort(key=lambda r: r["roomName"])
    return free


def _build_result_from_cache(
    cached: dict[str, Any],
    strategy: str,
    stale: bool,
    needs_refresh: bool,
    ttl_seconds: int,
) -> FreeClassroomResult:
    """从缓存构建结果。"""
    freshness_dict = cached.get("freshness", {})
    freshness_dict["strategy"] = strategy
    freshness_dict["stale"] = stale
    freshness_dict["liveChecked"] = False
    if needs_refresh:
        freshness_dict["stale"] = True
        freshness_dict["_needsRefresh"] = True

    return FreeClassroomResult(
        query=cached.get("query", {}),
        total=cached.get("total", 0),
        rooms=cached.get("rooms", []),
        freshness=freshness_dict,
        resolved=cached.get("resolved", {}),
    )


def _empty_result(
    term_code: str,
    week: int,
    day_of_week: int,
    period: int,
    campus_code: str,
    building_code: str,
    strategy: str,
    live_checked: bool,
    ttl_seconds: int,
) -> FreeClassroomResult:
    """构建空结果。"""
    freshness_obj = Freshness(
        strategy=strategy,
        source="xk_room_schedule",
        fetched_at=format_iso(),
        parsed_at=format_iso(),
        ttl_seconds=ttl_seconds,
        age_seconds=0,
        stale=False,
        live_checked=live_checked,
    )
    return FreeClassroomResult(
        query={
            "termCode": term_code,
            "week": week,
            "dayOfWeek": day_of_week,
            "dayName": _day_name(day_of_week),
            "period": period,
            "periodName": _period_name(period),
            "campusCode": campus_code,
            "buildingCode": building_code,
        },
        total=0,
        rooms=[],
        freshness=freshness_obj.to_dict(),
    )


def _day_name(day_of_week: int) -> str:
    names = ["", "星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    return names[day_of_week] if 1 <= day_of_week <= 7 else ""


def _period_name(period: int) -> str:
    names = {1: "第一大节", 2: "第二大节", 3: "第三大节", 4: "第四大节", 5: "第五大节", 9: "中午时段"}
    return names.get(period, "")


def _enrich_rooms_with_resource_id(
    rooms: list[dict[str, Any]],
    campus_map: dict[str, str],
    building_map: dict[str, str],
    default_campus_code: str = "",
    default_building_code: str = "",
) -> None:
    """为每个 room 补充 resource_id 和校区/楼房名称。

    优先使用 room 自身的 campusCode/buildingCode，fallback 到默认值。
    通过 resource_registry.ensure_classroom_resource 自动创建/获取 resource_id。
    """
    try:
        from ..resource_registry import ensure_classroom_resource, build_resource_id
    except ImportError:
        # resource_registry 不可用时不报错
        for room in rooms:
            rc = room.get("campusCode", default_campus_code)
            rb = room.get("buildingCode", default_building_code)
            if not room.get("campusName") and rc in campus_map:
                room["campusName"] = campus_map[rc]
            if not room.get("buildingName") and rb in building_map:
                room["buildingName"] = building_map[rb]
        return

    for room in rooms:
        rc = room.get("campusCode", default_campus_code)
        rb = room.get("buildingCode", default_building_code)
        room_name = room.get("roomName", "")

        # 补充 campusName/buildingName
        if not room.get("campusName") and rc in campus_map:
            room["campusName"] = campus_map[rc]
        if not room.get("buildingName") and rb in building_map:
            room["buildingName"] = building_map[rb]

        # 获取或创建 resource_id
        if rc and rb and room_name:
            try:
                record = ensure_classroom_resource(
                    campus_code=rc,
                    campus_name=campus_map.get(rc, rc),
                    building_code=rb,
                    building_name=building_map.get(rb, rb),
                    room_id=room.get("roomId", room_name),
                    room_name=room_name,
                    capacity=room.get("capacity", 0),
                    type_name=room.get("typeName", ""),
                )
                room["resourceId"] = record.resource_id
                room["canonicalName"] = record.canonical_name
                room["aliases"] = record.aliases
                room["location"] = record.location
                room["sourceMapping"] = record.source
            except Exception:
                # resource_registry 写入失败时不阻塞查询
                pass

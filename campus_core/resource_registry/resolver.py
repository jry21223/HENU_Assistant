"""自然语言资源解析器。

将用户自然语言输入解析为候选资源 ID 列表。

输入示例：
- "明伦十号楼101"
- "十号楼101"
- "金明综合楼"
- "图书馆二楼"
- "研讨室A203"

输出：按匹配分数排序的 ResolveCandidate 列表。
"""

from __future__ import annotations

import re
from typing import Any

from .alias import normalize, normalize_building_name, normalize_campus_name, normalize_room_name
from .models import (
    RESOURCE_TYPE_CLASSROOM,
    RESOURCE_TYPE_LIBRARY_AREA,
    RESOURCE_TYPE_LIBRARY_SEAT,
    RESOURCE_TYPE_SEMINAR_ROOM,
    ResolveCandidate,
)
from .registry import get_resource, search_resources


def resolve_resource(
    text: str,
    resource_type: str = "",
    campus_code: str = "",
    limit: int = 10,
) -> dict[str, Any]:
    """自然语言资源解析主入口。

    Args:
        text: 用户输入，如 "明伦十号楼101"。
        resource_type: 限制资源类型。
        campus_code: 限制校区。
        limit: 最大返回候选数。

    Returns:
        {"success": bool, "candidates": [ResolveCandidate.to_dict(), ...], "msg": str}
    """
    if not text or not text.strip():
        return {"success": False, "msg": "请输入查询文本", "candidates": []}

    text = text.strip()
    normalized = normalize(text)

    candidates: list[ResolveCandidate] = []
    seen_ids: set[str] = set()

    # ── 策略 1: 精确别名索引匹配 ──
    from .storage import lookup_by_alias

    exact_ids = lookup_by_alias(normalized.lower())
    for rid in exact_ids:
        if rid in seen_ids:
            continue
        record = get_resource(rid)
        if record:
            if resource_type and record.resource_type != resource_type:
                continue
            candidates.append(
                ResolveCandidate(
                    resource_id=rid,
                    score=1.0,
                    display_name=record.display_name,
                    resource_type=record.resource_type,
                    matched_alias=normalized,
                )
            )
            seen_ids.add(rid)

    # ── 策略 2: 关键词模糊搜索 ──
    if len(candidates) < limit:
        fuzzy_results = search_resources(
            query=normalized,
            resource_type=resource_type,
            campus_code=campus_code,
            limit=limit - len(candidates),
        )
        for record in fuzzy_results:
            if record.resource_id in seen_ids:
                continue
            score = _compute_match_score(normalized, record)
            candidates.append(
                ResolveCandidate(
                    resource_id=record.resource_id,
                    score=score,
                    display_name=record.display_name,
                    resource_type=record.resource_type,
                    matched_alias=normalized,
                )
            )
            seen_ids.add(record.resource_id)

    # ── 策略 3: 拆解文本尝试匹配 ──
    if len(candidates) < limit:
        parsed_candidates = _parse_and_match(text, resource_type, campus_code, limit - len(candidates))
        for c in parsed_candidates:
            if c.resource_id not in seen_ids:
                candidates.append(c)
                seen_ids.add(c.resource_id)

    # 按 score 降序排序
    candidates.sort(key=lambda c: c.score, reverse=True)

    return {
        "success": True,
        "msg": f"找到 {len(candidates)} 个候选项" if candidates else "未找到匹配资源",
        "candidates": [c.to_dict() for c in candidates[:limit]],
    }


def _compute_match_score(query: str, record: Any) -> float:
    """计算文本匹配分数（去空格后比较）。"""
    q = re.sub(r"\s+", "", query.lower())
    score = 0.0

    cn = re.sub(r"\s+", "", record.canonical_name.lower())
    dn = re.sub(r"\s+", "", record.display_name.lower())

    # 精确匹配 canonical_name
    if cn == q:
        return 0.95

    # 精确匹配 display_name
    if dn == q:
        return 0.9

    # 部分匹配
    if q in cn:
        score = max(score, 0.7)
    if q in dn:
        score = max(score, 0.6)

    # 别名匹配（去空格，双向）
    for alias in record.aliases:
        a = re.sub(r"\s+", "", alias.lower())
        if not a or len(a) < 2:
            continue
        if q == a:
            score = max(score, 0.85)
        elif q in a:
            score = max(score, 0.6)
        elif a in q:
            score = max(score, 0.5)

    return score


def _parse_and_match(
    text: str,
    resource_type: str = "",
    campus_code: str = "",
    limit: int = 10,
) -> list[ResolveCandidate]:
    """拆解用户输入后分别匹配。

    尝试提取校区名、楼房名、教室名等信息，组合搜索。
    """
    candidates: list[ResolveCandidate] = []
    text = text.strip()

    # 识别校区
    detected_campus = ""
    campus_names = ["明伦校区", "金明校区", "郑州校区", "明伦", "金明", "郑州"]
    for name in campus_names:
        if name in text:
            detected_campus = normalize_campus_name(name)
            break

    # 如果 detect 到了校区，限制搜索范围
    search_campus = campus_code or ""
    if detected_campus:
        # 搜索该校区下的所有资源
        search_results = search_resources(
            query=text,
            resource_type=resource_type,
            campus_code="",  # 不用 code，用文本搜索
            limit=limit,
        )
        for record in search_results:
            if detected_campus in record.display_name or detected_campus in str(record.location):
                candidates.append(
                    ResolveCandidate(
                        resource_id=record.resource_id,
                        score=0.7,
                        display_name=record.display_name,
                        resource_type=record.resource_type,
                        matched_alias=text,
                    )
                )

    # 尝试提取"研讨室"关键词
    if "研讨室" in text or "研讨" in text:
        room_match = re.search(r"研讨室?\s*([A-Za-z]?\d+)", text, re.I)
        if room_match:
            room_code = room_match.group(1).upper()
            search_results = search_resources(
                query=room_code,
                resource_type=RESOURCE_TYPE_SEMINAR_ROOM,
                limit=limit,
            )
            for record in search_results:
                candidates.append(
                    ResolveCandidate(
                        resource_id=record.resource_id,
                        score=0.8,
                        display_name=record.display_name,
                        resource_type=record.resource_type,
                        matched_alias=f"研讨室{room_code}",
                    )
                )

    # 尝试提取"图书馆"关键词
    if "图书馆" in text:
        search_results = search_resources(
            query=text,
            resource_type=RESOURCE_TYPE_LIBRARY_AREA,
            limit=limit,
        )
        for record in search_results:
            candidates.append(
                ResolveCandidate(
                    resource_id=record.resource_id,
                    score=0.6,
                    display_name=record.display_name,
                    resource_type=record.resource_type,
                    matched_alias=text,
                )
            )

    return candidates

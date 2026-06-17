from __future__ import annotations

import itertools
import json
import re
from typing import Any


WEEKDAY_ALIASES = {
    "1": 1,
    "一": 1,
    "周一": 1,
    "星期一": 1,
    "monday": 1,
    "mon": 1,
    "2": 2,
    "二": 2,
    "周二": 2,
    "星期二": 2,
    "tuesday": 2,
    "tue": 2,
    "3": 3,
    "三": 3,
    "周三": 3,
    "星期三": 3,
    "wednesday": 3,
    "wed": 3,
    "4": 4,
    "四": 4,
    "周四": 4,
    "星期四": 4,
    "thursday": 4,
    "thu": 4,
    "5": 5,
    "五": 5,
    "周五": 5,
    "星期五": 5,
    "friday": 5,
    "fri": 5,
    "6": 6,
    "六": 6,
    "周六": 6,
    "星期六": 6,
    "saturday": 6,
    "sat": 6,
    "7": 7,
    "日": 7,
    "天": 7,
    "周日": 7,
    "周天": 7,
    "星期日": 7,
    "星期天": 7,
    "sunday": 7,
    "sun": 7,
}


def _as_list(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    return [value]


def _parse_ints(value: Any) -> list[int]:
    if isinstance(value, int):
        return [value]
    if isinstance(value, list):
        out: list[int] = []
        for item in value:
            out.extend(_parse_ints(item))
        return out
    text = str(value or "")
    nums: list[int] = []
    for start, end in re.findall(r"(\d+)\s*[-~]\s*(\d+)", text):
        nums.extend(range(int(start), int(end) + 1))
    singles = re.sub(r"\d+\s*[-~]\s*\d+", " ", text)
    nums.extend(int(item) for item in re.findall(r"\d+", singles))
    return sorted(set(nums))


def _parse_weekday(value: Any) -> int | None:
    if isinstance(value, int) and 1 <= value <= 7:
        return value
    text = str(value or "").strip().lower()
    return WEEKDAY_ALIASES.get(text)


def _normalize_meeting(raw: dict[str, Any]) -> dict[str, Any]:
    weekday = _parse_weekday(raw.get("weekday") or raw.get("day") or raw.get("week_day"))
    periods = _parse_ints(raw.get("periods") or raw.get("period") or raw.get("sections") or raw.get("time"))
    weeks = _parse_ints(raw.get("weeks") or raw.get("week") or raw.get("teaching_weeks"))
    return {
        "weekday": weekday,
        "periods": periods,
        "weeks": weeks,
        "room": raw.get("room") or raw.get("location") or "",
    }


def normalize_course_section(section: dict[str, Any]) -> dict[str, Any]:
    meetings_raw = _as_list(section.get("meetings"))
    if not meetings_raw:
        meetings_raw = _as_list(section.get("meeting"))
    if not meetings_raw:
        meetings_raw = [section]
    meetings = [_normalize_meeting(item if isinstance(item, dict) else {"time": item}) for item in meetings_raw]
    return {
        **section,
        "id": str(section.get("id") or section.get("section_id") or section.get("code") or section.get("name") or ""),
        "course_id": str(section.get("course_id") or section.get("course") or section.get("name") or ""),
        "name": str(section.get("name") or section.get("course_name") or section.get("course") or ""),
        "meetings": meetings,
    }


def meetings_conflict(a: dict[str, Any], b: dict[str, Any]) -> bool:
    ma = _normalize_meeting(a)
    mb = _normalize_meeting(b)
    if ma["weekday"] is None or mb["weekday"] is None or ma["weekday"] != mb["weekday"]:
        return False
    if not set(ma["periods"]).intersection(mb["periods"]):
        return False
    weeks_a = set(ma["weeks"])
    weeks_b = set(mb["weeks"])
    if weeks_a and weeks_b and not weeks_a.intersection(weeks_b):
        return False
    return True


def section_conflicts(a: dict[str, Any], b: dict[str, Any]) -> bool:
    sa = normalize_course_section(a)
    sb = normalize_course_section(b)
    return any(meetings_conflict(ma, mb) for ma in sa["meetings"] for mb in sb["meetings"])


def plan_conflicts(plan: list[dict[str, Any]], existing_schedule: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    all_existing = existing_schedule or []
    for left, right in itertools.combinations(plan, 2):
        if section_conflicts(left, right):
            conflicts.append({"left": normalize_course_section(left), "right": normalize_course_section(right)})
    for selected in plan:
        for existing in all_existing:
            if section_conflicts(selected, existing):
                conflicts.append({"left": normalize_course_section(selected), "right": normalize_course_section(existing)})
    return conflicts


def score_plan(plan: list[dict[str, Any]], preferences: dict[str, Any] | None = None) -> float:
    prefs = preferences or {}
    score = 0.0
    meetings = [meeting for section in plan for meeting in normalize_course_section(section)["meetings"]]
    days = {meeting["weekday"] for meeting in meetings if meeting["weekday"] is not None}
    periods = [period for meeting in meetings for period in meeting["periods"]]
    if prefs.get("morning"):
        score += sum(1 for period in periods if period <= 4) * 2
        score -= sum(1 for period in periods if period >= 9)
    if prefs.get("avoid_evening"):
        score -= sum(1 for period in periods if period >= 11) * 3
    if prefs.get("compact_days"):
        score -= len(days) * 2
    preferred_days = set(_parse_ints(prefs.get("preferred_days")))
    if preferred_days:
        score += len(days.intersection(preferred_days)) * 2
        score -= len(days - preferred_days)
    return score


def _load_jsonish(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, str):
        return json.loads(value)
    return value


def generate_ranked_plans(
    candidates: Any,
    existing_schedule: Any,
    preferences: Any,
    top_k: int = 10,
) -> dict[str, Any]:
    candidate_groups = _load_jsonish(candidates, [])
    existing = _load_jsonish(existing_schedule, [])
    prefs = _load_jsonish(preferences, {})
    if isinstance(candidate_groups, dict):
        candidate_groups = list(candidate_groups.values())
    groups = [group if isinstance(group, list) else [group] for group in candidate_groups]
    ranked: list[dict[str, Any]] = []
    for combo in itertools.product(*groups):
        plan = [normalize_course_section(item) for item in combo if isinstance(item, dict)]
        conflicts = plan_conflicts(plan, existing if isinstance(existing, list) else [])
        if conflicts:
            continue
        ranked.append({"score": score_plan(plan, prefs if isinstance(prefs, dict) else {}), "plan": plan})
    ranked.sort(key=lambda item: item["score"], reverse=True)
    return {"success": True, "count": len(ranked), "plans": ranked[: max(1, int(top_k or 10))]}

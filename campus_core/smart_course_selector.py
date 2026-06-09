"""Shared smart course selector for HENU Assistant.

This module is designed to be reused by all HENU Assistant delivery branches:
Langbot plugin, MCP server, and OpenClaw skill. It reads the teaching office
Excel export, filters class-related professional courses plus grade-wide public
courses, detects time conflicts, and returns structured plans that can later be
fed into an automatic course-selection submitter.
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = "henu.smart_course_selection.v1"

WEEKDAY_NAME = {
    1: "星期一",
    2: "星期二",
    3: "星期三",
    4: "星期四",
    5: "星期五",
    6: "星期六",
    7: "星期日",
}
WEEKDAY_ALIASES = {
    "一": 1,
    "星期一": 1,
    "周一": 1,
    "1": 1,
    "二": 2,
    "星期二": 2,
    "周二": 2,
    "2": 2,
    "三": 3,
    "星期三": 3,
    "周三": 3,
    "3": 3,
    "四": 4,
    "星期四": 4,
    "周四": 4,
    "4": 4,
    "五": 5,
    "星期五": 5,
    "周五": 5,
    "5": 5,
    "六": 6,
    "星期六": 6,
    "周六": 6,
    "6": 6,
    "日": 7,
    "天": 7,
    "星期日": 7,
    "星期天": 7,
    "周日": 7,
    "周天": 7,
    "7": 7,
}
MAJORS = ("软工", "网工", "卓越班")
EMPTY_VALUES = {"", "nan", "none", "null", "282", "未排", "待定"}


def text(value: Any) -> str:
    if value is None:
        return ""
    value = str(value).strip()
    if value.lower() in {"nan", "none", "null"}:
        return ""
    if value.endswith(".0") and value[:-2].isdigit():
        value = value[:-2]
    return value


def normalize_class_name(value: Any) -> str:
    value = text(value).replace("（", "(").replace("）", ")")
    value = value.replace("，", "、").replace(",", "、")
    return re.sub(r"\s+", "", value)


def dedupe(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return result


def split_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [text(item) for item in value if text(item)]
    return [item for item in str(value or "").split("、") if item]


def parse_user_class(user_class: str) -> dict[str, str]:
    """Parse a class label such as 25软工1 / 24网工9 / 23卓越班."""
    normalized = normalize_class_name(user_class)
    match = re.match(r"^(\d{2})(软工|网工|卓越班)(\d+)?$", normalized)
    if not match:
        return {"normalized": normalized, "grade": "", "major": "", "class_no": ""}
    grade, major, class_no = match.group(1), match.group(2), match.group(3) or ""
    return {
        "normalized": f"{grade}{major}{class_no}",
        "grade": grade,
        "major": major,
        "class_no": class_no,
    }


def parse_bracket_value(value: Any) -> tuple[str, str]:
    """Parse strings like [CODE]Name into (CODE, Name)."""
    value = text(value)
    match = re.match(r"^\[([^\]]+)\](.+)$", value)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return "", value


def parse_week_range(value: Any) -> tuple[int | None, int | None, str]:
    value = text(value)
    if value.lower() in EMPTY_VALUES:
        return None, None, ""
    match = re.search(r"(\d+)\s*-\s*(\d+)", value)
    if match:
        start, end = int(match.group(1)), int(match.group(2))
        return start, end, f"{start}-{end}"
    match = re.search(r"\d+", value)
    if match:
        week = int(match.group(0))
        return week, week, str(week)
    return None, None, value


def parse_odd_even(value: Any) -> str:
    value = text(value)
    if value.lower() in EMPTY_VALUES:
        return "每周"
    if "单" in value:
        return "单周"
    if "双" in value:
        return "双周"
    return value or "每周"


def parse_weekday(value: Any) -> tuple[int | None, str]:
    value = text(value)
    if value.lower() in EMPTY_VALUES:
        return None, "未排"
    weekday = WEEKDAY_ALIASES.get(value) or WEEKDAY_ALIASES.get(value[-1:])
    if weekday:
        return weekday, WEEKDAY_NAME[weekday]
    return None, value or "未排"


def parse_sections(value: Any, length_value: Any = None) -> tuple[int | None, int | None, str]:
    value = text(value)
    if value.lower() in EMPTY_VALUES:
        return None, None, ""
    numbers = [int(item) for item in re.findall(r"\d+", value)]
    if len(numbers) >= 2:
        return numbers[0], numbers[1], f"{numbers[0]}-{numbers[1]}节"
    if len(numbers) == 1:
        length_text = text(length_value)
        length = int(length_text) if length_text.isdigit() else 1
        end = numbers[0] + max(length, 1) - 1
        return numbers[0], end, f"{numbers[0]}-{end}节"
    return None, None, value


def infer_class_scope(class_name: Any) -> dict[str, Any]:
    """Infer applicable class scope from teaching-class name."""
    normalized = normalize_class_name(class_name)
    directions = [
        item
        for item in re.findall(r"\((.*?)\)", normalized)
        if item and "选课" not in item and "班" not in item and not item.isdigit()
    ]

    year_match = re.fullmatch(r"20(\d{2})", normalized)
    if year_match:
        grade = year_match.group(1)
        return {
            "grade": grade,
            "majors": "",
            "class_numbers": "",
            "applicable_classes": [f"{grade}级全体"],
            "course_category": "全年级公共课",
            "is_grade_common": True,
            "is_major_selection_pool": False,
            "direction_tags": directions,
        }

    grade_match = re.search(r"(\d{2})", normalized)
    grade = grade_match.group(1) if grade_match else ""
    if grade and re.fullmatch(rf"{grade}选课\d+班", normalized):
        return {
            "grade": grade,
            "majors": "",
            "class_numbers": "",
            "applicable_classes": [f"{grade}级全体"],
            "course_category": "全年级公共课",
            "is_grade_common": True,
            "is_major_selection_pool": False,
            "direction_tags": directions,
        }

    cleaned = re.sub(r"\([^)]*\)", "", normalized)
    tokens = [token for token in re.split(r"[+、/]+", cleaned) if token]
    current_grade, current_major = grade, ""
    applicable: list[str] = []
    majors: list[str] = []
    class_numbers: list[str] = []

    for token in tokens:
        full = re.match(r"^(\d{2})(软工|网工|卓越班)(\d+)?$", token)
        partial = re.match(r"^(软工|网工|卓越班)(\d+)?$", token)
        number_only = re.match(r"^(\d+)$", token)
        if full:
            current_grade, current_major, class_no = full.group(1), full.group(2), full.group(3) or ""
        elif partial and current_grade:
            current_major, class_no = partial.group(1), partial.group(2) or ""
        elif number_only and current_grade and current_major and current_major != "卓越班":
            class_no = number_only.group(1)
        else:
            continue

        majors.append(current_major)
        if current_major == "卓越班" and not class_no:
            applicable.append(f"{current_grade}卓越班")
        elif class_no:
            class_numbers.append(class_no)
            applicable.append(f"{current_grade}{current_major}{class_no}")
        else:
            applicable.append(f"{current_grade}{current_major}专业全体")

    if not applicable and grade:
        for major in MAJORS:
            if major in normalized:
                majors.append(major)
                applicable.append(f"{grade}卓越班" if major == "卓越班" else f"{grade}{major}专业全体")

    is_pool = "选课" in normalized and any(major in normalized for major in ("软工", "网工"))
    return {
        "grade": grade,
        "majors": "、".join(dedupe(majors)),
        "class_numbers": "、".join(dedupe(class_numbers)),
        "applicable_classes": dedupe(applicable),
        "course_category": "专业选课班" if is_pool else "班级对应专业课",
        "is_grade_common": False,
        "is_major_selection_pool": is_pool,
        "direction_tags": directions,
    }


def find_header_row(rows: list[list[Any]]) -> int:
    required = {"课程", "周次", "星期", "节次", "任课教师"}
    for index, row in enumerate(rows):
        if required.issubset({text(cell) for cell in row}):
            return index
    raise ValueError("未找到包含 课程、周次、星期、节次、任课教师 的课表表头")


def row_value(row: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in row:
            return row[name]
    return ""


def read_excel_records(excel_path: str | Path, sheet_name: str = "2026-2027-1学期") -> list[dict[str, Any]]:
    """Read raw meeting records from the teaching-office Excel export."""
    try:
        import pandas as pd
    except ImportError as exc:
        raise ImportError("读取 Excel 需要安装 pandas 和 openpyxl：pip install pandas openpyxl") from exc

    raw = pd.read_excel(excel_path, sheet_name=sheet_name, header=None)
    header_row = find_header_row(raw.values.tolist())
    df = pd.read_excel(excel_path, sheet_name=sheet_name, header=header_row).dropna(how="all")
    records: list[dict[str, Any]] = []

    for index, row_obj in df.iterrows():
        row = {str(key).strip(): value for key, value in row_obj.items()}
        course_code, course_name = parse_bracket_value(row_value(row, "课程"))
        if not course_name or course_name == "课程":
            continue
        teacher_id, teacher_name = parse_bracket_value(row_value(row, "任课教师"))
        week_start, week_end, week_range = parse_week_range(row_value(row, "周次"))
        weekday_no, weekday_name = parse_weekday(row_value(row, "星期"))
        start_section, end_section, section_text = parse_sections(row_value(row, "节次"), row_value(row, "连上节数"))
        location = text(row_value(row, "上课地点"))
        if location == "282":
            location = ""
        class_name = text(row_value(row, "上课班级名称", "上课班组"))
        credit_text = text(row_value(row, "学分"))
        records.append(
            {
                "campus": text(row_value(row, "校区")),
                "department": text(row_value(row, "承担单位")),
                "course_code": course_code,
                "course_name": course_name,
                "credit": float(credit_text) if re.fullmatch(r"\d+(\.\d+)?", credit_text) else None,
                "teaching_class_no": text(row_value(row, "上课班号")),
                "teacher_id": teacher_id,
                "teacher_name": teacher_name,
                "class_name": class_name,
                "time_block": {
                    "weekday_no": weekday_no,
                    "weekday_name": weekday_name,
                    "start_section": start_section,
                    "end_section": end_section,
                    "section_text": section_text,
                    "week_range": week_range,
                    "week_start": week_start,
                    "week_end": week_end,
                    "odd_even": parse_odd_even(row_value(row, "单双周")),
                    "location": location,
                    "source_row": int(index) + header_row + 2,
                },
                "source_row": int(index) + header_row + 2,
            }
        )
    return records


def build_time_summary(blocks: list[dict[str, Any]]) -> str:
    items: list[str] = []
    for block in blocks:
        section = (
            f"{block.get('start_section')}-{block.get('end_section')}节"
            if block.get("start_section") and block.get("end_section")
            else "未排时间"
        )
        odd_even = "" if (block.get("odd_even") or "每周") == "每周" else f" {block.get('odd_even')}"
        location = f" {block.get('location')}" if block.get("location") else ""
        week = f"{block.get('week_range')} " if block.get("week_range") else ""
        items.append(f"{week}{block.get('weekday_name') or '未排'} {section}{odd_even}{location}".strip())
    return "；".join(items)


def records_to_course_options(records: list[dict[str, Any]], semester: str = "") -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], dict[str, Any]] = {}
    for record in records:
        key = (
            record["course_code"],
            record["course_name"],
            record["teaching_class_no"],
            record["teacher_id"],
            record["teacher_name"],
            record["class_name"],
        )
        if key not in grouped:
            scope = infer_class_scope(record["class_name"])
            grouped[key] = {
                "course_option_key": "-".join(str(item) for item in key if item),
                "semester": semester,
                "campus": record["campus"],
                "department": record["department"],
                "course_code": record["course_code"],
                "course_name": record["course_name"],
                "credit": record["credit"],
                "teaching_class_no": record["teaching_class_no"],
                "teacher_id": record["teacher_id"],
                "teacher_name": record["teacher_name"],
                "class_name": record["class_name"],
                "grade": scope["grade"],
                "majors": scope["majors"],
                "class_numbers": scope["class_numbers"],
                "applicable_classes": "、".join(scope["applicable_classes"]),
                "course_category": scope["course_category"],
                "is_grade_common": "是" if scope["is_grade_common"] else "否",
                "is_major_selection_pool": "是" if scope["is_major_selection_pool"] else "否",
                "direction_tags": "、".join(scope["direction_tags"]),
                "time_blocks": [],
                "source_rows": [],
            }
        grouped[key]["time_blocks"].append(record["time_block"])
        grouped[key]["source_rows"].append(str(record["source_row"]))

    options: list[dict[str, Any]] = []
    for option in grouped.values():
        blocks = option.pop("time_blocks")
        days = sorted({int(block["weekday_no"]) for block in blocks if block.get("weekday_no")})
        option["meeting_count"] = len(blocks)
        option["active_day_count"] = len(days)
        option["active_weekdays"] = "、".join(WEEKDAY_NAME[day] for day in days)
        option["has_early8"] = "是" if any(block.get("start_section") == 1 for block in blocks) else "否"
        option["has_unscheduled"] = "是" if any(not block.get("weekday_no") for block in blocks) else "否"
        option["total_periods_per_cycle"] = sum(
            (block.get("end_section") or 0) - (block.get("start_section") or 0) + 1
            for block in blocks
            if block.get("start_section") and block.get("end_section")
        )
        option["time_summary"] = build_time_summary(blocks)
        option["source_rows"] = "、".join(option["source_rows"])
        option["time_blocks_json"] = json.dumps(blocks, ensure_ascii=False)
        options.append(option)
    return sorted(options, key=lambda item: (item.get("grade", ""), item.get("course_name", ""), item.get("teaching_class_no", "")))


def parse_time_blocks(option: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(option.get("time_blocks"), list):
        return option["time_blocks"]
    try:
        loaded = json.loads(option.get("time_blocks_json") or "[]")
        return loaded if isinstance(loaded, list) else []
    except json.JSONDecodeError:
        return []


def option_matches_user_class(option: dict[str, Any], user_class: str) -> bool:
    user = parse_user_class(user_class)
    if not user["grade"]:
        return False
    applicable = split_list(option.get("applicable_classes", ""))
    return (
        user["normalized"] in applicable
        or f"{user['grade']}级全体" in applicable
        or f"{user['grade']}{user['major']}专业全体" in applicable
    )


def week_overlap(left: dict[str, Any], right: dict[str, Any]) -> bool:
    values = [left.get("week_start"), left.get("week_end"), right.get("week_start"), right.get("week_end")]
    if not all(isinstance(item, int) for item in values):
        return True
    return max(values[0], values[2]) <= min(values[1], values[3])


def blocks_conflict(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if not left.get("weekday_no") or not right.get("weekday_no") or left["weekday_no"] != right["weekday_no"]:
        return False
    if not week_overlap(left, right):
        return False
    if (
        left.get("odd_even", "每周") != "每周"
        and right.get("odd_even", "每周") != "每周"
        and left.get("odd_even") != right.get("odd_even")
    ):
        return False
    starts = [left.get("start_section"), right.get("start_section")]
    ends = [left.get("end_section"), right.get("end_section")]
    if not all(isinstance(item, int) for item in starts + ends):
        return False
    return max(starts) <= min(ends)


def options_conflict(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return any(blocks_conflict(a, b) for a in parse_time_blocks(left) for b in parse_time_blocks(right))


def active_days(options: list[dict[str, Any]]) -> list[int]:
    return sorted({int(block["weekday_no"]) for option in options for block in parse_time_blocks(option) if block.get("weekday_no")})


def gap_penalty(options: list[dict[str, Any]]) -> int:
    by_day: dict[int, list[tuple[int, int]]] = {}
    for option in options:
        for block in parse_time_blocks(option):
            day, start, end = block.get("weekday_no"), block.get("start_section"), block.get("end_section")
            if isinstance(day, int) and isinstance(start, int) and isinstance(end, int):
                by_day.setdefault(day, []).append((start, end))

    penalty = 0
    for slots in by_day.values():
        last_end = None
        for start, end in sorted(slots):
            if last_end is not None and start > last_end + 1:
                penalty += start - last_end - 1
            last_end = max(last_end or end, end)
    return penalty


def score_schedule(options: list[dict[str, Any]], preferences: dict[str, Any] | None = None) -> float:
    preferences = preferences or {}
    score = 0.0
    for option in options:
        for block in parse_time_blocks(option):
            start = block.get("start_section")
            if start == 1:
                if preferences.get("like_early8") is True:
                    score += 8
                elif preferences.get("like_early8") is False:
                    score -= 8
            if isinstance(start, int) and start >= 9 and preferences.get("avoid_evening"):
                score -= 5
            if not block.get("weekday_no"):
                score += -1 if preferences.get("allow_unscheduled", True) else -50

    days = active_days(options)
    if preferences.get("compact_days"):
        target_days = int(preferences.get("target_days", 3))
        score -= 10 * max(0, len(days) - target_days)
        score += 3 * max(0, 5 - len(days))
        score -= 1.5 * gap_penalty(options)
    score += sum(1 for option in options if option.get("has_unscheduled") == "否")
    return score


def normalize_preferences(
    *,
    like_early8: bool | None = None,
    avoid_early8: bool = False,
    compact_days: bool = False,
    target_days: int = 3,
    avoid_evening: bool = False,
    allow_unscheduled: bool = True,
) -> dict[str, Any]:
    early8 = True if like_early8 is True else False if avoid_early8 else None
    return {
        "like_early8": early8,
        "compact_days": bool(compact_days),
        "target_days": max(1, int(target_days or 3)),
        "avoid_evening": bool(avoid_evening),
        "allow_unscheduled": bool(allow_unscheduled),
    }


def compact_time_block(block: dict[str, Any]) -> dict[str, Any]:
    return {
        "weekday_no": block.get("weekday_no"),
        "weekday_name": block.get("weekday_name") or "未排",
        "start_section": block.get("start_section"),
        "end_section": block.get("end_section"),
        "section_text": block.get("section_text")
        or (
            f"{block.get('start_section')}-{block.get('end_section')}节"
            if block.get("start_section") and block.get("end_section")
            else "未排时间"
        ),
        "week_range": block.get("week_range") or "",
        "week_start": block.get("week_start"),
        "week_end": block.get("week_end"),
        "odd_even": block.get("odd_even") or "每周",
        "location": block.get("location") or "",
    }


def course_option_to_structured(option: dict[str, Any], include_time_blocks: bool = True) -> dict[str, Any]:
    item = {
        "course_option_key": option.get("course_option_key") or "",
        "course_code": option.get("course_code") or "",
        "course_name": option.get("course_name") or "",
        "credit": option.get("credit"),
        "teaching_class_no": option.get("teaching_class_no") or "",
        "teacher_id": option.get("teacher_id") or "",
        "teacher_name": option.get("teacher_name") or "",
        "class_name": option.get("class_name") or "",
        "course_category": option.get("course_category") or "",
        "grade": option.get("grade") or "",
        "majors": split_list(option.get("majors")),
        "class_numbers": split_list(option.get("class_numbers")),
        "applicable_classes": split_list(option.get("applicable_classes")),
        "direction_tags": split_list(option.get("direction_tags")),
        "is_grade_common": option.get("is_grade_common") == "是" or option.get("is_grade_common") is True,
        "is_major_selection_pool": option.get("is_major_selection_pool") == "是" or option.get("is_major_selection_pool") is True,
        "meeting_count": int(option.get("meeting_count") or 0),
        "active_day_count": int(option.get("active_day_count") or 0),
        "active_weekdays": split_list(option.get("active_weekdays")),
        "has_early8": option.get("has_early8") == "是" or option.get("has_early8") is True,
        "has_unscheduled": option.get("has_unscheduled") == "是" or option.get("has_unscheduled") is True,
        "total_periods_per_cycle": int(option.get("total_periods_per_cycle") or 0),
        "time_summary": option.get("time_summary") or "",
    }
    if include_time_blocks:
        item["time_blocks"] = [compact_time_block(block) for block in parse_time_blocks(option)]
    return item


def build_selection_action(option: dict[str, Any], priority: int) -> dict[str, Any]:
    return {
        "priority": priority,
        "action": "select_course_option",
        "course_option_key": option.get("course_option_key") or "",
        "course_code": option.get("course_code") or "",
        "course_name": option.get("course_name") or "",
        "teaching_class_no": option.get("teaching_class_no") or "",
        "teacher_id": option.get("teacher_id") or "",
        "teacher_name": option.get("teacher_name") or "",
        "class_name": option.get("class_name") or "",
        "dry_run": True,
        "requires_submitter": True,
    }


def build_calendar(options: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    calendar: dict[str, list[dict[str, Any]]] = {name: [] for name in WEEKDAY_NAME.values()}
    unscheduled: list[dict[str, Any]] = []
    for option in options:
        base = {
            "course_option_key": option.get("course_option_key") or "",
            "course_code": option.get("course_code") or "",
            "course_name": option.get("course_name") or "",
            "teaching_class_no": option.get("teaching_class_no") or "",
            "teacher_name": option.get("teacher_name") or "",
            "course_category": option.get("course_category") or "",
        }
        for block in parse_time_blocks(option):
            item = {**base, **compact_time_block(block)}
            day_name = item.get("weekday_name") or "未排"
            if day_name in calendar:
                calendar[day_name].append(item)
            else:
                unscheduled.append(item)
    for items in calendar.values():
        items.sort(key=lambda item: (item.get("start_section") or 999, item.get("end_section") or 999, item.get("course_name") or ""))
    if unscheduled:
        calendar["未排"] = unscheduled
    return {day: items for day, items in calendar.items() if items}


def detect_conflicts(options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    for i, left in enumerate(options):
        for right in options[i + 1 :]:
            if not options_conflict(left, right):
                continue
            conflicts.append(
                {
                    "left": course_option_to_structured(left, include_time_blocks=False),
                    "right": course_option_to_structured(right, include_time_blocks=False),
                }
            )
    return conflicts


@dataclass
class PlanResult:
    score: float
    active_days: list[int]
    options: list[dict[str, Any]]
    warnings: list[str]

    def to_dict(self, rank: int | None = None) -> dict[str, Any]:
        actions = [build_selection_action(option, priority=index + 1) for index, option in enumerate(self.options)]
        result = {
            "score": self.score,
            "active_days": [WEEKDAY_NAME.get(day, str(day)) for day in self.active_days],
            "active_day_count": len(self.active_days),
            "warnings": self.warnings,
            "courses": [course_option_to_structured(option) for option in self.options],
            "calendar": build_calendar(self.options),
            "conflicts": detect_conflicts(self.options),
            "selection_actions": actions,
        }
        if rank is not None:
            result["rank"] = rank
        return result


class HenuSmartCourseSelector:
    """Smart course selector for filtering and planning."""

    def __init__(self, course_options: list[dict[str, Any]], metadata: dict[str, Any] | None = None):
        self.course_options = list(course_options)
        self.metadata = metadata or {}

    @classmethod
    def from_excel(
        cls,
        excel_path: str | Path,
        sheet_name: str = "2026-2027-1学期",
        semester: str = "",
    ) -> "HenuSmartCourseSelector":
        records = read_excel_records(excel_path, sheet_name=sheet_name)
        options = records_to_course_options(records, semester=semester)
        return cls(
            options,
            {
                "source_type": "excel",
                "source_file": str(excel_path),
                "source_sheet": sheet_name,
                "semester": semester,
                "record_count": len(records),
                "course_option_count": len(options),
            },
        )

    @classmethod
    def from_json(cls, json_path: str | Path) -> "HenuSmartCourseSelector":
        with open(json_path, "r", encoding="utf-8") as file:
            data = json.load(file)
        if isinstance(data, list):
            return cls(data, {"source_type": "json", "source_file": str(json_path), "course_option_count": len(data)})
        options = data.get("course_options", [])
        metadata = {key: value for key, value in data.items() if key != "course_options"}
        metadata.setdefault("source_type", "json")
        metadata.setdefault("source_file", str(json_path))
        metadata.setdefault("course_option_count", len(options))
        return cls(options, metadata)

    @classmethod
    def from_path(
        cls,
        path: str | Path,
        sheet_name: str = "2026-2027-1学期",
        semester: str = "",
    ) -> "HenuSmartCourseSelector":
        source = Path(path)
        if source.suffix.lower() == ".json":
            return cls.from_json(source)
        return cls.from_excel(source, sheet_name=sheet_name, semester=semester)

    def to_json(self, json_path: str | Path) -> None:
        with open(json_path, "w", encoding="utf-8") as file:
            json.dump({**self.metadata, "course_options": self.course_options}, file, ensure_ascii=False, indent=2)

    def filter_options_for_class(
        self,
        user_class: str,
        include_common: bool = True,
        allow_unscheduled: bool = True,
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for option in self.course_options:
            if not option_matches_user_class(option, user_class):
                continue
            if not include_common and (option.get("is_grade_common") == "是" or option.get("is_grade_common") is True):
                continue
            if not allow_unscheduled and (option.get("has_unscheduled") == "是" or option.get("has_unscheduled") is True):
                continue
            result.append(option)
        return result

    @staticmethod
    def group_by_course(options: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for option in options:
            grouped.setdefault((str(option.get("course_code", "")), str(option.get("course_name", ""))), []).append(option)
        return grouped

    def plan_schedule(
        self,
        user_class: str,
        preferences: dict[str, Any] | None = None,
        include_common: bool = True,
        top_k: int = 3,
        max_combinations: int = 200_000,
    ) -> list[PlanResult]:
        preferences = preferences or {}
        candidates = self.filter_options_for_class(
            user_class,
            include_common=include_common,
            allow_unscheduled=preferences.get("allow_unscheduled", True),
        )
        groups = sorted(self.group_by_course(candidates).items(), key=lambda item: len(item[1]))
        if not groups:
            return [PlanResult(0, [], [], [f"未找到班级 {user_class} 的匹配课程。"])]

        best: list[PlanResult] = []
        warnings: list[str] = []
        explored = 0

        def add_result(chosen: list[dict[str, Any]]) -> None:
            best.append(PlanResult(score_schedule(chosen, preferences), active_days(chosen), chosen[:], warnings[:]))
            best.sort(key=lambda item: item.score, reverse=True)
            del best[top_k:]

        def backtrack(index: int, chosen: list[dict[str, Any]]) -> None:
            nonlocal explored
            if explored >= max_combinations:
                return
            if index == len(groups):
                explored += 1
                add_result(chosen)
                return
            for option in sorted(groups[index][1], key=lambda item: score_schedule([item], preferences), reverse=True):
                if any(options_conflict(option, selected) for selected in chosen):
                    continue
                chosen.append(option)
                backtrack(index + 1, chosen)
                chosen.pop()

        backtrack(0, [])
        if explored >= max_combinations:
            warnings.append(f"搜索达到 max_combinations={max_combinations}，结果可能不是全局最优。")
        if not best:
            return [PlanResult(0, [], [], ["没有找到无冲突课表。可放宽偏好或允许未排时间课程。"])]
        for item in best:
            item.warnings.extend(warnings)
        return best


def output_schema() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "top_level_fields": [
            "success",
            "schema_version",
            "mode",
            "source",
            "request",
            "summary",
            "course_options",
            "plans",
            "automation_contract",
            "warnings",
        ],
        "selection_action_fields": [
            "priority",
            "action",
            "course_option_key",
            "course_code",
            "course_name",
            "teaching_class_no",
            "teacher_id",
            "teacher_name",
            "class_name",
            "dry_run",
            "requires_submitter",
        ],
        "notes": [
            "selection_actions 是后续自动选课提交器的输入契约；当前模块只生成 dry_run 动作，不提交教务系统。",
            "course_option_key、course_code、teaching_class_no 是后续提交器需要优先使用的稳定定位字段。",
            "calendar 便于前端直接渲染，courses 便于后端保存和复核。",
        ],
    }


def build_summary(candidates: list[dict[str, Any]], plans: list[PlanResult] | None = None) -> dict[str, Any]:
    grouped = HenuSmartCourseSelector.group_by_course(candidates)
    categories: dict[str, int] = {}
    for option in candidates:
        category = str(option.get("course_category") or "未分类")
        categories[category] = categories.get(category, 0) + 1
    return {
        "candidate_course_option_count": len(candidates),
        "candidate_course_count": len(grouped),
        "category_counts": categories,
        "has_unscheduled_count": sum(1 for item in candidates if item.get("has_unscheduled") == "是" or item.get("has_unscheduled") is True),
        "early8_option_count": sum(1 for item in candidates if item.get("has_early8") == "是" or item.get("has_early8") is True),
        "plan_count": len(plans or []),
    }


def build_smart_course_selection_response(
    *,
    source_path: str = "",
    excel_path: str = "",
    json_path: str = "",
    sheet_name: str = "2026-2027-1学期",
    semester: str = "",
    user_class: str = "",
    mode: str = "plan",
    like_early8: bool | None = None,
    avoid_early8: bool = False,
    compact_days: bool = False,
    target_days: int = 3,
    avoid_evening: bool = False,
    allow_unscheduled: bool = True,
    include_common: bool = True,
    include_course_options: bool | None = None,
    top_k: int = 3,
    max_combinations: int = 200_000,
) -> dict[str, Any]:
    """Build the public structured response used by MCP/Langbot/OpenClaw adapters."""
    selected_mode = str(mode or "plan").strip().lower()
    if selected_mode in {"schema", "contract"}:
        return {
            "success": True,
            "schema_version": SCHEMA_VERSION,
            "mode": "schema",
            "schema": output_schema(),
            "automation_contract": automation_contract(),
            "warnings": [],
        }

    path = str(source_path or excel_path or json_path or "").strip()
    class_text = str(user_class or "").strip()
    if not path:
        return failure_response("source_path/excel_path/json_path 不能为空", mode=selected_mode)
    if not class_text:
        return failure_response("user_class 不能为空，例如 25软工1", mode=selected_mode)

    preferences = normalize_preferences(
        like_early8=like_early8,
        avoid_early8=avoid_early8,
        compact_days=compact_days,
        target_days=target_days,
        avoid_evening=avoid_evening,
        allow_unscheduled=allow_unscheduled,
    )

    try:
        selector = HenuSmartCourseSelector.from_path(path, sheet_name=sheet_name, semester=semester)
        candidates = selector.filter_options_for_class(
            class_text,
            include_common=include_common,
            allow_unscheduled=preferences.get("allow_unscheduled", True),
        )
        plans: list[PlanResult] = []
        if selected_mode in {"plan", "recommend", "select", "selection"}:
            plans = selector.plan_schedule(
                class_text,
                preferences=preferences,
                include_common=include_common,
                top_k=max(1, int(top_k or 3)),
                max_combinations=max(1, int(max_combinations or 200_000)),
            )
        elif selected_mode not in {"filter", "options"}:
            return failure_response(f"不支持的 mode: {selected_mode}", mode=selected_mode)

        should_include_options = include_course_options if include_course_options is not None else selected_mode in {"filter", "options"}
        warnings = [warning for plan in plans for warning in plan.warnings]
        return {
            "success": True,
            "schema_version": SCHEMA_VERSION,
            "mode": "filter" if selected_mode in {"filter", "options"} else "plan",
            "msg": build_message(class_text, candidates, plans, selected_mode),
            "source": selector.metadata,
            "request": {
                "user_class": class_text,
                "class_profile": parse_user_class(class_text),
                "include_common": bool(include_common),
                "preferences": preferences,
            },
            "summary": build_summary(candidates, plans),
            "course_options": [course_option_to_structured(item) for item in candidates] if should_include_options else [],
            "plans": [plan.to_dict(rank=index + 1) for index, plan in enumerate(plans)],
            "automation_contract": automation_contract(),
            "warnings": dedupe(warnings),
        }
    except Exception as exc:
        return failure_response(f"智能选课失败: {exc}", mode=selected_mode)


def build_message(user_class: str, candidates: list[dict[str, Any]], plans: list[PlanResult], mode: str) -> str:
    if mode in {"filter", "options"}:
        return f"已为 {user_class} 筛选出 {len(candidates)} 个课程选项"
    return f"已为 {user_class} 生成 {len(plans)} 个推荐课表方案；候选课程选项 {len(candidates)} 个"


def automation_contract() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "ready_for_auto_selection_submitter": True,
        "current_module_submits_to_jw_system": False,
        "submitter_input_field": "plans[].selection_actions",
        "stable_key_fields": ["course_option_key", "course_code", "teaching_class_no"],
        "calendar_render_field": "plans[].calendar",
        "audit_fields": ["plans[].courses", "plans[].conflicts", "request.preferences"],
    }


def failure_response(message: str, mode: str = "plan") -> dict[str, Any]:
    return {
        "success": False,
        "schema_version": SCHEMA_VERSION,
        "mode": mode,
        "msg": message,
        "source": {},
        "request": {},
        "summary": {},
        "course_options": [],
        "plans": [],
        "automation_contract": automation_contract(),
        "warnings": [message],
    }


def parse_cli_like_early8(args: argparse.Namespace) -> bool | None:
    if args.like_early8:
        return True
    if args.avoid_early8:
        return False
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="HENU Assistant smart course selector")
    parser.add_argument("source", help="教务导出的 Excel 或已清洗 JSON 文件路径")
    parser.add_argument("--sheet", default="2026-2027-1学期", help="Excel 工作表名")
    parser.add_argument("--class", dest="user_class", required=True, help="班级，例如 25软工1")
    parser.add_argument("--semester", default="", help="学期标识，例如 2026-2027-1")
    parser.add_argument("--mode", choices=["schema", "filter", "plan"], default="plan")
    parser.add_argument("--like-early8", action="store_true", help="偏好早八")
    parser.add_argument("--avoid-early8", action="store_true", help="避免早八")
    parser.add_argument("--compact-days", action="store_true", help="尽量把课集中在更少天数")
    parser.add_argument("--target-days", type=int, default=3, help="集中排课目标天数")
    parser.add_argument("--avoid-evening", action="store_true", help="避免晚课")
    parser.add_argument("--no-unscheduled", action="store_true", help="排除未排时间课程")
    parser.add_argument("--include-options", action="store_true", help="plan 模式也返回候选 course_options")
    parser.add_argument("--top-k", type=int, default=3, help="返回推荐方案数量")
    args = parser.parse_args()

    response = build_smart_course_selection_response(
        source_path=args.source,
        sheet_name=args.sheet,
        semester=args.semester,
        user_class=args.user_class,
        mode=args.mode,
        like_early8=parse_cli_like_early8(args),
        compact_days=args.compact_days,
        target_days=args.target_days,
        avoid_evening=args.avoid_evening,
        allow_unscheduled=not args.no_unscheduled,
        include_course_options=args.include_options,
        top_k=args.top_k,
    )
    print(json.dumps(response, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

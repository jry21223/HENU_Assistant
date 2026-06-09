"""Smart course selection for HENU Assistant.

Reads the teaching-office Excel schedule, filters courses for a class, detects
conflicts, and ranks schedule plans by preferences such as early classes and
compact teaching days.
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

WEEKDAY_NAME = {1: "星期一", 2: "星期二", 3: "星期三", 4: "星期四", 5: "星期五", 6: "星期六", 7: "星期日"}
WEEKDAY_ALIASES = {
    "一": 1, "星期一": 1, "周一": 1, "1": 1,
    "二": 2, "星期二": 2, "周二": 2, "2": 2,
    "三": 3, "星期三": 3, "周三": 3, "3": 3,
    "四": 4, "星期四": 4, "周四": 4, "4": 4,
    "五": 5, "星期五": 5, "周五": 5, "5": 5,
    "六": 6, "星期六": 6, "周六": 6, "6": 6,
    "日": 7, "天": 7, "星期日": 7, "星期天": 7, "周日": 7, "周天": 7, "7": 7,
}
MAJORS = ("软工", "网工", "卓越班")
EMPTY = {"", "nan", "none", "null", "282", "未排", "待定"}


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


def parse_user_class(user_class: str) -> dict[str, str]:
    normalized = normalize_class_name(user_class)
    match = re.match(r"^(\d{2})(软工|网工|卓越班)(\d+)?$", normalized)
    if not match:
        return {"normalized": normalized, "grade": "", "major": "", "class_no": ""}
    grade, major, class_no = match.group(1), match.group(2), match.group(3) or ""
    return {"normalized": f"{grade}{major}{class_no}", "grade": grade, "major": major, "class_no": class_no}


def parse_bracket(value: Any) -> tuple[str, str]:
    value = text(value)
    match = re.match(r"^\[([^\]]+)\](.+)$", value)
    return (match.group(1).strip(), match.group(2).strip()) if match else ("", value)


def parse_week_range(value: Any) -> tuple[int | None, int | None, str]:
    value = text(value)
    if value.lower() in EMPTY:
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
    if value.lower() in EMPTY:
        return "每周"
    if "单" in value:
        return "单周"
    if "双" in value:
        return "双周"
    return value or "每周"


def parse_weekday(value: Any) -> tuple[int | None, str]:
    value = text(value)
    if value.lower() in EMPTY:
        return None, "未排"
    weekday = WEEKDAY_ALIASES.get(value) or WEEKDAY_ALIASES.get(value[-1:])
    return (weekday, WEEKDAY_NAME[weekday]) if weekday else (None, value or "未排")


def parse_sections(value: Any, length_value: Any = None) -> tuple[int | None, int | None, str]:
    value = text(value)
    if value.lower() in EMPTY:
        return None, None, ""
    numbers = [int(item) for item in re.findall(r"\d+", value)]
    if len(numbers) >= 2:
        return numbers[0], numbers[1], f"{numbers[0]}-{numbers[1]}节"
    if len(numbers) == 1:
        length_text = text(length_value)
        length = int(length_text) if length_text.isdigit() else 1
        return numbers[0], numbers[0] + max(length, 1) - 1, f"{numbers[0]}-{numbers[0] + max(length, 1) - 1}节"
    return None, None, value


def dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return result


def infer_class_scope(class_name: Any) -> dict[str, Any]:
    normalized = normalize_class_name(class_name)
    directions = [item for item in re.findall(r"\((.*?)\)", normalized) if item and "选课" not in item and "班" not in item and not item.isdigit()]

    year_match = re.fullmatch(r"20(\d{2})", normalized)
    if year_match:
        grade = year_match.group(1)
        return {"grade": grade, "majors": "", "class_numbers": "", "applicable_classes": [f"{grade}级全体"], "course_category": "全年级公共课", "is_grade_common": True, "is_major_selection_pool": False, "direction_tags": directions}

    grade_match = re.search(r"(\d{2})", normalized)
    grade = grade_match.group(1) if grade_match else ""
    if grade and re.fullmatch(rf"{grade}选课\d+班", normalized):
        return {"grade": grade, "majors": "", "class_numbers": "", "applicable_classes": [f"{grade}级全体"], "course_category": "全年级公共课", "is_grade_common": True, "is_major_selection_pool": False, "direction_tags": directions}

    cleaned = re.sub(r"\([^)]*\)", "", normalized)
    tokens = [token for token in re.split(r"[+、/]+", cleaned) if token]
    current_grade, current_major = grade, ""
    applicable: list[str] = []
    majors: list[str] = []
    class_numbers: list[str] = []

    for token in tokens:
        full = re.match(r"^(\d{2})(软工|网工|卓越班)(\d+)?$", token)
        partial = re.match(r"^(软工|网工|卓越班)(\d+)?$", token)
        only_number = re.match(r"^(\d+)$", token)
        if full:
            current_grade, current_major, class_no = full.group(1), full.group(2), full.group(3) or ""
        elif partial and current_grade:
            current_major, class_no = partial.group(1), partial.group(2) or ""
        elif only_number and current_grade and current_major and current_major != "卓越班":
            class_no = only_number.group(1)
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
    category = "专业选课班" if is_pool else "班级对应专业课"
    return {"grade": grade, "majors": "、".join(dedupe(majors)), "class_numbers": "、".join(dedupe(class_numbers)), "applicable_classes": dedupe(applicable), "course_category": category, "is_grade_common": False, "is_major_selection_pool": is_pool, "direction_tags": directions}


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
        course_code, course_name = parse_bracket(row_value(row, "课程"))
        if not course_name or course_name == "课程":
            continue
        teacher_id, teacher_name = parse_bracket(row_value(row, "任课教师"))
        week_start, week_end, week_range = parse_week_range(row_value(row, "周次"))
        weekday_no, weekday_name = parse_weekday(row_value(row, "星期"))
        start_section, end_section, section_text = parse_sections(row_value(row, "节次"), row_value(row, "连上节数"))
        location = text(row_value(row, "上课地点"))
        if location == "282":
            location = ""
        class_name = text(row_value(row, "上课班级名称", "上课班组"))
        records.append({
            "campus": text(row_value(row, "校区")),
            "department": text(row_value(row, "承担单位")),
            "course_code": course_code,
            "course_name": course_name,
            "credit": float(text(row_value(row, "学分")) or 0) if re.fullmatch(r"\d+(\.\d+)?", text(row_value(row, "学分"))) else None,
            "teaching_class_no": text(row_value(row, "上课班号")),
            "teacher_id": teacher_id,
            "teacher_name": teacher_name,
            "class_name": class_name,
            "time_block": {"weekday_no": weekday_no, "weekday_name": weekday_name, "start_section": start_section, "end_section": end_section, "section_text": section_text, "week_range": week_range, "week_start": week_start, "week_end": week_end, "odd_even": parse_odd_even(row_value(row, "单双周")), "location": location, "source_row": int(index) + header_row + 2},
            "source_row": int(index) + header_row + 2,
        })
    return records


def build_time_summary(blocks: list[dict[str, Any]]) -> str:
    items = []
    for block in blocks:
        section = f"{block.get('start_section')}-{block.get('end_section')}节" if block.get("start_section") and block.get("end_section") else "未排时间"
        odd_even = "" if (block.get("odd_even") or "每周") == "每周" else f" {block.get('odd_even')}"
        location = f" {block.get('location')}" if block.get("location") else ""
        week = f"{block.get('week_range')} " if block.get("week_range") else ""
        items.append(f"{week}{block.get('weekday_name') or '未排'} {section}{odd_even}{location}".strip())
    return "；".join(items)


def records_to_course_options(records: list[dict[str, Any]], semester: str = "") -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], dict[str, Any]] = {}
    for record in records:
        key = (record["course_code"], record["course_name"], record["teaching_class_no"], record["teacher_id"], record["teacher_name"], record["class_name"])
        if key not in grouped:
            scope = infer_class_scope(record["class_name"])
            grouped[key] = {"course_option_key": "-".join([str(item) for item in key if item]), "semester": semester, "campus": record["campus"], "department": record["department"], "course_code": record["course_code"], "course_name": record["course_name"], "credit": record["credit"], "teaching_class_no": record["teaching_class_no"], "teacher_id": record["teacher_id"], "teacher_name": record["teacher_name"], "class_name": record["class_name"], "grade": scope["grade"], "majors": scope["majors"], "class_numbers": scope["class_numbers"], "applicable_classes": "、".join(scope["applicable_classes"]), "course_category": scope["course_category"], "is_grade_common": "是" if scope["is_grade_common"] else "否", "is_major_selection_pool": "是" if scope["is_major_selection_pool"] else "否", "direction_tags": "、".join(scope["direction_tags"]), "time_blocks": [], "source_rows": []}
        grouped[key]["time_blocks"].append(record["time_block"])
        grouped[key]["source_rows"].append(str(record["source_row"]))

    options = []
    for option in grouped.values():
        blocks = option.pop("time_blocks")
        days = sorted({int(block["weekday_no"]) for block in blocks if block.get("weekday_no")})
        option["meeting_count"] = len(blocks)
        option["active_day_count"] = len(days)
        option["active_weekdays"] = "、".join(WEEKDAY_NAME[day] for day in days)
        option["has_early8"] = "是" if any(block.get("start_section") == 1 for block in blocks) else "否"
        option["has_unscheduled"] = "是" if any(not block.get("weekday_no") for block in blocks) else "否"
        option["total_periods_per_cycle"] = sum((block.get("end_section") or 0) - (block.get("start_section") or 0) + 1 for block in blocks if block.get("start_section") and block.get("end_section"))
        option["time_summary"] = build_time_summary(blocks)
        option["source_rows"] = "、".join(option["source_rows"])
        option["time_blocks_json"] = json.dumps(blocks, ensure_ascii=False)
        options.append(option)
    return sorted(options, key=lambda item: (item.get("grade", ""), item.get("course_name", ""), item.get("teaching_class_no", "")))


def parse_time_blocks(option: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(option.get("time_blocks"), list):
        return option["time_blocks"]
    try:
        return json.loads(option.get("time_blocks_json") or "[]")
    except json.JSONDecodeError:
        return []


def option_matches_user_class(option: dict[str, Any], user_class: str) -> bool:
    user = parse_user_class(user_class)
    if not user["grade"]:
        return False
    applicable = [item for item in str(option.get("applicable_classes", "")).split("、") if item]
    return user["normalized"] in applicable or f"{user['grade']}级全体" in applicable or f"{user['grade']}{user['major']}专业全体" in applicable


def week_overlap(a: dict[str, Any], b: dict[str, Any]) -> bool:
    values = [a.get("week_start"), a.get("week_end"), b.get("week_start"), b.get("week_end")]
    if not all(isinstance(item, int) for item in values):
        return True
    return max(values[0], values[2]) <= min(values[1], values[3])


def blocks_conflict(a: dict[str, Any], b: dict[str, Any]) -> bool:
    if not a.get("weekday_no") or not b.get("weekday_no") or a["weekday_no"] != b["weekday_no"]:
        return False
    if not week_overlap(a, b):
        return False
    if a.get("odd_even", "每周") != "每周" and b.get("odd_even", "每周") != "每周" and a.get("odd_even") != b.get("odd_even"):
        return False
    starts = [a.get("start_section"), b.get("start_section")]
    ends = [a.get("end_section"), b.get("end_section")]
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
                score += 8 if preferences.get("like_early8") is True else -8 if preferences.get("like_early8") is False else 0
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


@dataclass
class PlanResult:
    score: float
    active_days: list[int]
    options: list[dict[str, Any]]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {"score": self.score, "active_days": [WEEKDAY_NAME.get(day, str(day)) for day in self.active_days], "warnings": self.warnings, "options": [{"course_code": item.get("course_code"), "course_name": item.get("course_name"), "teaching_class_no": item.get("teaching_class_no"), "teacher_name": item.get("teacher_name"), "class_name": item.get("class_name"), "course_category": item.get("course_category"), "time_summary": item.get("time_summary"), "course_option_key": item.get("course_option_key")} for item in self.options]}


class HenuSmartCourseSelector:
    def __init__(self, course_options: list[dict[str, Any]], metadata: dict[str, Any] | None = None):
        self.course_options = list(course_options)
        self.metadata = metadata or {}

    @classmethod
    def from_excel(cls, excel_path: str | Path, sheet_name: str = "2026-2027-1学期", semester: str = "") -> "HenuSmartCourseSelector":
        records = read_excel_records(excel_path, sheet_name=sheet_name)
        options = records_to_course_options(records, semester=semester)
        return cls(options, {"source_file": str(excel_path), "source_sheet": sheet_name, "semester": semester, "record_count": len(records), "course_option_count": len(options)})

    @classmethod
    def from_json(cls, json_path: str | Path) -> "HenuSmartCourseSelector":
        with open(json_path, "r", encoding="utf-8") as file:
            data = json.load(file)
        return cls(data if isinstance(data, list) else data.get("course_options", []), {} if isinstance(data, list) else {key: value for key, value in data.items() if key != "course_options"})

    def to_json(self, json_path: str | Path) -> None:
        with open(json_path, "w", encoding="utf-8") as file:
            json.dump({**self.metadata, "course_options": self.course_options}, file, ensure_ascii=False, indent=2)

    def filter_options_for_class(self, user_class: str, include_common: bool = True, allow_unscheduled: bool = True) -> list[dict[str, Any]]:
        result = []
        for option in self.course_options:
            if not option_matches_user_class(option, user_class):
                continue
            if not include_common and option.get("is_grade_common") == "是":
                continue
            if not allow_unscheduled and option.get("has_unscheduled") == "是":
                continue
            result.append(option)
        return result

    @staticmethod
    def group_by_course(options: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for option in options:
            grouped.setdefault((str(option.get("course_code", "")), str(option.get("course_name", ""))), []).append(option)
        return grouped

    def plan_schedule(self, user_class: str, preferences: dict[str, Any] | None = None, include_common: bool = True, top_k: int = 3, max_combinations: int = 200_000) -> list[PlanResult]:
        preferences = preferences or {}
        candidates = self.filter_options_for_class(user_class, include_common=include_common, allow_unscheduled=preferences.get("allow_unscheduled", True))
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HENU Assistant smart course selector")
    parser.add_argument("excel")
    parser.add_argument("--sheet", default="2026-2027-1学期")
    parser.add_argument("--class", dest="user_class", default="25软工1")
    parser.add_argument("--semester", default="")
    parser.add_argument("--like-early8", action="store_true")
    parser.add_argument("--avoid-early8", action="store_true")
    parser.add_argument("--compact-days", action="store_true")
    parser.add_argument("--target-days", type=int, default=3)
    parser.add_argument("--avoid-evening", action="store_true")
    args = parser.parse_args()
    like_early8 = True if args.like_early8 else False if args.avoid_early8 else None
    selector = HenuSmartCourseSelector.from_excel(args.excel, sheet_name=args.sheet, semester=args.semester)
    plans = selector.plan_schedule(args.user_class, {"like_early8": like_early8, "compact_days": args.compact_days, "target_days": args.target_days, "avoid_evening": args.avoid_evening, "allow_unscheduled": True})
    print(json.dumps([plan.to_dict() for plan in plans], ensure_ascii=False, indent=2))

"""Calculate teaching weeks from reviewed public calendars, never from chat input."""

from __future__ import annotations

import tomllib
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

CALENDAR_FILE = (
    Path(__file__).resolve().parents[2] / "campus_core/config/teaching_calendar.toml"
)
CAMPUS_TIMEZONE = ZoneInfo("Asia/Shanghai")


def teaching_week(target: date | datetime | None = None) -> dict:
    if target is None:
        target = datetime.now(CAMPUS_TIMEZONE)
    if isinstance(target, datetime):
        if target.tzinfo is None:
            target = target.replace(tzinfo=CAMPUS_TIMEZONE)
        target = target.astimezone(CAMPUS_TIMEZONE).date()
    base = {
        "date": target.isoformat(),
        "timezone": "Asia/Shanghai",
        "calculated": True,
        "available": False,
        "week": None,
    }
    try:
        semesters = tomllib.loads(CALENDAR_FILE.read_text(encoding="utf-8"))["semesters"]
        matches = []
        for semester in semesters:
            start = date.fromisoformat(semester["start"])
            end = date.fromisoformat(semester["last_teaching_day"])
            if start.weekday() != 0 or end < start or not semester.get("source"):
                continue
            week = (target - start).days // 7 + 1
            if start <= target <= end and 1 <= week <= int(semester["weeks"]):
                matches.append((semester, start, week))
        if len(matches) != 1:
            return {
                **base,
                "reason": "该日期不在已核实的教学日历内；管理员需维护校历，用户无需提供当前周次。",
            }
        semester, start, week = matches[0]
        return {
            **base,
            "available": True,
            "week": week,
            "week_start_date": (start + timedelta(weeks=week - 1)).isoformat(),
            "semester_start": start.isoformat(),
            "term_name": semester["name"],
            "academic_year": semester["academic_year"],
            "semester": semester["semester"],
            "source": semester["source"],
            "verified_on": semester["verified_on"],
        }
    except (OSError, ValueError, KeyError, TypeError):
        return {**base, "reason": "教学日历配置不可用，无法确认教学周。"}


def matches_term_name(name: str, calendar: dict) -> bool:
    """Match an upstream term label without inventing its opaque term code."""
    normalized = (
        re.sub(r"\s+", "", str(name))
        .replace("—", "-")
        .replace("–", "-")
        .replace("～", "-")
        .replace("~", "-")
    )
    if str(calendar.get("academic_year") or "") not in normalized:
        return False
    labels = {
        1: ("第一学期", "第1学期", "秋季学期", "-1学期"),
        2: ("第二学期", "第2学期", "春季学期", "-2学期"),
    }
    return any(
        label in normalized for label in labels.get(calendar.get("semester"), ())
    )

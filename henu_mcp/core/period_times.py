from __future__ import annotations

import json
import re
from typing import Any


def is_hhmm(text: str) -> bool:
    """Return whether *text* is a valid 24-hour ``HH:MM`` value."""
    return bool(re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", text or ""))


def to_minutes(hhmm: str) -> int:
    hour, minute = hhmm.split(":")
    return int(hour) * 60 + int(minute)


def minutes_to_hhmm(value: int) -> str:
    hour = max(0, value) // 60
    minute = max(0, value) % 60
    return f"{hour:02d}:{minute:02d}"


def normalize_teaching_period_times(
    period_times: dict[str, dict[str, str]],
) -> tuple[dict[str, dict[str, str]], dict[str, Any]]:
    """Sort, filter known short midday periods, and renumber periods."""
    items: list[tuple[int, str, str]] = []
    for key, cfg in (period_times or {}).items():
        if not isinstance(cfg, dict):
            continue
        start = str(cfg.get("start", "")).strip()
        end = str(cfg.get("end", "")).strip()
        if not (is_hhmm(start) and is_hhmm(end)) or to_minutes(start) >= to_minutes(end):
            continue
        try:
            period_no = int(str(key))
        except (TypeError, ValueError):
            period_no = 999
        items.append((period_no, start, end))

    if not items:
        return {}, {"applied": False, "removed_midday_count": 0}
    items.sort(key=lambda item: (to_minutes(item[1]), item[0]))

    removed_midday: list[tuple[int, str, str]] = []
    kept_items: list[tuple[int, str, str]] = []
    for period_no, start, end in items:
        start_min = to_minutes(start)
        duration = to_minutes(end) - start_min
        if 12 * 60 <= start_min <= 14 * 60 + 10 and 20 <= duration <= 35:
            removed_midday.append((period_no, start, end))
        else:
            kept_items.append((period_no, start, end))

    if removed_midday and len(kept_items) >= 10:
        items = kept_items
    else:
        removed_midday = []

    normalized = {
        str(index): {"start": start, "end": end}
        for index, (_, start, end) in enumerate(items, start=1)
    }
    return normalized, {
        "applied": True,
        "removed_midday_count": len(removed_midday),
        "removed_midday_periods": [
            {"period": period, "start": start, "end": end}
            for period, start, end in removed_midday
        ],
    }


def extract_period_times_from_xiqueer_json(text: str) -> dict[str, dict[str, str]]:
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return {}
    rows = payload.get("sksj") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return {}

    result: dict[str, dict[str, str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        match = re.search(r"第\s*(\d+)\s*节", str(row.get("jieci", "") or ""))
        start = str(row.get("time", "") or "").strip().zfill(5)
        if not match or not is_hhmm(start):
            continue
        try:
            duration = int(str(row.get("shichang", "") or "").strip())
        except (TypeError, ValueError):
            continue
        if not 0 < duration <= 180:
            continue
        end = minutes_to_hhmm(to_minutes(start) + duration)
        if is_hhmm(end):
            result[str(int(match.group(1)))] = {"start": start, "end": end}
    return result


def extract_period_times_from_text(text: str) -> dict[str, dict[str, str]]:
    candidates: dict[str, dict[str, str]] = {}
    patterns = (
        (
            re.compile(
                r"第?\s*(\d{1,2})\s*节[^0-9]{0,30}([0-2]?\d:[0-5]\d)"
                r"\s*(?:-|~|—|–|至)\s*([0-2]?\d:[0-5]\d)",
                re.I,
            ),
            (1, 2, 3),
        ),
        (
            re.compile(
                r"([0-2]?\d:[0-5]\d)\s*(?:-|~|—|–|至)\s*([0-2]?\d:[0-5]\d)"
                r"[^第]{0,30}第?\s*(\d{1,2})\s*节",
                re.I,
            ),
            (3, 1, 2),
        ),
    )
    for pattern, (period_index, start_index, end_index) in patterns:
        for match in pattern.finditer(text or ""):
            period = str(int(match.group(period_index)))
            start = match.group(start_index).zfill(5)
            end = match.group(end_index).zfill(5)
            if is_hhmm(start) and is_hhmm(end) and to_minutes(start) < to_minutes(end):
                candidates[period] = {"start": start, "end": end}
    return candidates

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from henu_mcp.core import course_schedule


CONFIG_FILE = course_schedule.OUTPUT_DIR / "course_monitor_config.json"
STATE_FILE = course_schedule.OUTPUT_DIR / "course_monitor_state.json"
MIN_INTERVAL_SECONDS = 60
DEFAULT_CONFIG: dict[str, Any] = {
    "targets": [],
    "preferences": {
        "compact_days": True,
        "avoid_evening": True,
        "class_keywords": ["25网工4"],
    },
    "notify": {
        "type": "feishu",
        "webhook_env": "FEISHU_WEBHOOK",
        "secret_env": "FEISHU_SECRET",
    },
    "interval_seconds": MIN_INTERVAL_SECONDS,
}


def _now_iso() -> str:
    try:
        return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    except Exception:
        return datetime.now().isoformat()


def _text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def _parse_int(value: Any) -> int | None:
    text = _text(value)
    match = re.search(r"-?\d+", text)
    return int(match.group(0)) if match else None


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def get_monitor_config_file() -> Path:
    return course_schedule.OUTPUT_DIR / "course_monitor_config.json"


def get_monitor_state_file() -> Path:
    return course_schedule.OUTPUT_DIR / "course_monitor_state.json"


def _deep_merge(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def normalize_monitor_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    normalized = _deep_merge(DEFAULT_CONFIG, config or {})
    targets = normalized.get("targets")
    normalized["targets"] = targets if isinstance(targets, list) else []
    preferences = normalized.get("preferences")
    normalized["preferences"] = preferences if isinstance(preferences, dict) else dict(DEFAULT_CONFIG["preferences"])
    notify = normalized.get("notify")
    normalized["notify"] = notify if isinstance(notify, dict) else dict(DEFAULT_CONFIG["notify"])
    try:
        interval = int(normalized.get("interval_seconds") or MIN_INTERVAL_SECONDS)
    except Exception:
        interval = MIN_INTERVAL_SECONDS
    normalized["interval_seconds"] = max(MIN_INTERVAL_SECONDS, interval)
    return normalized


def load_monitor_config() -> dict[str, Any]:
    return normalize_monitor_config(_load_json(get_monitor_config_file(), {}))


def save_monitor_config(config: dict[str, Any], merge: bool = True) -> dict[str, Any]:
    current = load_monitor_config() if merge else {}
    saved = normalize_monitor_config(_deep_merge(current, config) if merge else config)
    _save_json(get_monitor_config_file(), saved)
    return saved


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._current_row: list[str] | None = None
        self._current_cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "tr":
            self._current_row = []
        elif tag.lower() in {"td", "th"} and self._current_row is not None:
            self._current_cell = []

    def handle_data(self, data: str) -> None:
        if self._current_cell is not None:
            self._current_cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in {"td", "th"} and self._current_cell is not None and self._current_row is not None:
            self._current_row.append(_text("".join(self._current_cell)))
            self._current_cell = None
        elif lowered == "tr" and self._current_row is not None:
            if any(cell for cell in self._current_row):
                self.rows.append(self._current_row)
            self._current_row = None


@dataclass
class CourseAvailability:
    course_id: str = ""
    course_name: str = ""
    section_code: str = ""
    class_name: str = ""
    campus: str = ""
    teacher: str = ""
    capacity: int | None = None
    selected_text: str = ""
    selected_count: int | None = None
    available: int | None = None
    time_text: str = ""
    location: str = ""
    raw_cells: list[str] | None = None

    @property
    def key(self) -> str:
        return "|".join([self.course_id, self.course_name, self.section_code, self.teacher, self.class_name])

    def searchable_text(self) -> str:
        return " ".join(
            [
                self.course_id,
                self.course_name,
                self.section_code,
                self.class_name,
                self.teacher,
                self.time_text,
                self.location,
                " ".join(self.raw_cells or []),
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "course_id": self.course_id,
            "course_name": self.course_name,
            "section_code": self.section_code,
            "class_name": self.class_name,
            "campus": self.campus,
            "teacher": self.teacher,
            "capacity": self.capacity,
            "selected_text": self.selected_text,
            "selected_count": self.selected_count,
            "available": self.available,
            "time_text": self.time_text,
            "location": self.location,
            "raw_cells": self.raw_cells or [],
        }


def parse_teaching_class_rows(html: str, course_id: str = "", course_name: str = "") -> list[dict[str, Any]]:
    parser = _TableParser()
    parser.feed(html or "")
    rows: list[dict[str, Any]] = []
    for cells in parser.rows:
        joined = "".join(cells)
        if "上课班号" in joined and "可选人数" in joined:
            continue
        if len(cells) < 8:
            continue
        item = CourseAvailability(
            course_id=str(course_id or ""),
            course_name=str(course_name or ""),
            section_code=cells[0] if len(cells) > 0 else "",
            class_name=cells[1] if len(cells) > 1 else "",
            campus=cells[2] if len(cells) > 2 else "",
            teacher=cells[3] if len(cells) > 3 else "",
            capacity=_parse_int(cells[5] if len(cells) > 5 else ""),
            selected_text=cells[6] if len(cells) > 6 else "",
            selected_count=_parse_int(cells[6] if len(cells) > 6 else ""),
            available=_parse_int(cells[7] if len(cells) > 7 else ""),
            time_text=cells[8] if len(cells) > 8 else "",
            location=cells[9] if len(cells) > 9 else "",
            raw_cells=cells,
        )
        rows.append(item.to_dict())
    return rows


def _field_matches(actual: str, expected: str) -> bool:
    if not expected:
        return True
    return _compact(expected) in _compact(actual)


def match_target(row: dict[str, Any], target: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    field_pairs = [
        ("course_id", "course_id"),
        ("course_name", "course_name"),
        ("teacher", "teacher"),
        ("section_code", "section_code"),
    ]
    for target_field, row_field in field_pairs:
        expected = _text(target.get(target_field))
        if expected and not _field_matches(_text(row.get(row_field)), expected):
            return False, []
        if expected:
            reasons.append(f"{target_field}={expected}")

    haystack = _compact(CourseAvailability(**{k: row.get(k) for k in CourseAvailability.__dataclass_fields__ if k != "raw_cells"}, raw_cells=row.get("raw_cells") or []).searchable_text())
    for keyword in target.get("keywords") or []:
        keyword_text = _compact(keyword)
        if keyword_text and keyword_text not in haystack:
            return False, []
        if keyword_text:
            reasons.append(f"keyword={keyword}")
    return True, reasons


def evaluate_matches(rows: list[dict[str, Any]], targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for row in rows:
        for target in targets:
            matched, reasons = match_target(row, target if isinstance(target, dict) else {})
            if matched:
                available = row.get("available")
                matches.append(
                    {
                        **row,
                        "target": target,
                        "match_reasons": reasons,
                        "has_available": isinstance(available, int) and available > 0,
                    }
                )
    matches.sort(key=lambda item: (not item.get("has_available"), -(item.get("available") or 0), item.get("section_code") or ""))
    return matches


def load_monitor_state() -> dict[str, Any]:
    state = _load_json(get_monitor_state_file(), {})
    return state if isinstance(state, dict) else {}


def save_monitor_state(state: dict[str, Any]) -> None:
    _save_json(get_monitor_state_file(), state)


def evaluate_alerts(matches: list[dict[str, Any]], state: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    previous_state = state or {}
    previous_items = previous_state.get("items") if isinstance(previous_state.get("items"), dict) else {}
    current_items: dict[str, Any] = {}
    alerts: list[dict[str, Any]] = []
    for match in matches:
        key = str(match.get("section_code") or match.get("key") or CourseAvailability(**{k: match.get(k) for k in CourseAvailability.__dataclass_fields__ if k != "raw_cells"}, raw_cells=match.get("raw_cells") or []).key)
        available = match.get("available")
        previous = previous_items.get(key) if isinstance(previous_items, dict) else None
        previous_available = previous.get("available") if isinstance(previous, dict) else None
        current_items[key] = {
            "available": available,
            "has_available": match.get("has_available"),
            "updated_at": _now_iso(),
        }
        should_alert = False
        if isinstance(available, int) and available > 0:
            if previous is None:
                should_alert = True
            elif not isinstance(previous_available, int) or previous_available <= 0:
                should_alert = True
            elif available > previous_available:
                should_alert = True
        if should_alert:
            alerts.append(
                {
                    "type": "course_available",
                    "previous_available": previous_available,
                    "current_available": available,
                    "course": match,
                }
            )
    new_state = {
        "updated_at": _now_iso(),
        "items": current_items,
    }
    return alerts, new_state


def build_feishu_message(alerts: list[dict[str, Any]]) -> str:
    lines = ["选课余量提醒"]
    for alert in alerts:
        course = alert.get("course") or {}
        lines.append(
            " - {name} {section} {teacher} 可选 {available} 人，时间：{time}，地点：{location}".format(
                name=course.get("course_name") or course.get("course_id") or "未知课程",
                section=course.get("section_code") or course.get("class_name") or "未知教学班",
                teacher=course.get("teacher") or "未知教师",
                available=course.get("available"),
                time=course.get("time_text") or "未解析",
                location=course.get("location") or "未解析",
            )
        )
    lines.append("脚本只提醒，不会自动选课或提交。")
    return "\n".join(lines)


def _feishu_signed_url(webhook: str, secret: str) -> tuple[str, dict[str, Any]]:
    if not secret:
        return webhook, {}
    timestamp = str(int(time.time()))
    string_to_sign = f"{timestamp}\n{secret}".encode("utf-8")
    sign = base64.b64encode(hmac.new(string_to_sign, b"", digestmod=hashlib.sha256).digest()).decode("utf-8")
    return webhook, {"timestamp": timestamp, "sign": sign}


def send_feishu_text(webhook: str, text: str, secret: str = "") -> dict[str, Any]:
    if not webhook:
        return {"success": False, "msg": "缺少飞书 Webhook"}
    url, extra = _feishu_signed_url(webhook, secret)
    payload = {
        **extra,
        "msg_type": "text",
        "content": {"text": text},
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        parsed = json.loads(body) if body else {}
        return {"success": True, "status_code": resp.status, "response": parsed}
    except Exception as exc:
        return {"success": False, "msg": f"飞书通知失败: {exc}"}


def notify_alerts(alerts: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
    if not alerts:
        return {"success": True, "sent": False, "msg": "无提醒"}
    notify = config.get("notify") or {}
    if notify.get("type", "feishu") != "feishu":
        return {"success": False, "sent": False, "msg": "当前仅支持 feishu 通知"}
    webhook = os.environ.get(str(notify.get("webhook_env") or "FEISHU_WEBHOOK"), "")
    secret = os.environ.get(str(notify.get("secret_env") or "FEISHU_SECRET"), "")
    result = send_feishu_text(webhook, build_feishu_message(alerts), secret=secret)
    result["sent"] = bool(result.get("success"))
    return result


def test_notification(config: dict[str, Any] | None = None) -> dict[str, Any]:
    normalized = normalize_monitor_config(config or load_monitor_config())
    sample = [
        {
            "course": {
                "course_name": "测试课程",
                "section_code": "TEST-001",
                "teacher": "测试教师",
                "available": 1,
                "time_text": "星期一 1-2节",
                "location": "测试教室",
            }
        }
    ]
    return notify_alerts(sample, normalized)

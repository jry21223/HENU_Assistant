from __future__ import annotations

import datetime as dt
import re
from typing import Any

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo


def _now_dt() -> dt.datetime:
    """获取当前北京时间（带时区信息）"""
    try:
        return dt.datetime.now(ZoneInfo("Asia/Shanghai"))
    except Exception:
        return dt.datetime.utcnow() + dt.timedelta(hours=8)


class TimeUtilsMixin:
    @staticmethod
    def _to_hhmm(raw_time: Any) -> str:
        if raw_time is None:
            return ""
        text = str(raw_time).strip()
        if not text:
            return ""

        match = re.search(r"(\d{1,2})[:：](\d{1,2})", text)
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2))
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                return f"{hour:02d}:{minute:02d}"

        match = re.search(r"(\d{1,2})点(?:(\d{1,2})分?)?", text)
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2) or "0")
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                return f"{hour:02d}:{minute:02d}"

        compact = re.sub(r"\D", "", text)
        if len(compact) in (3, 4):
            if len(compact) == 3:
                hour = int(compact[0])
                minute = int(compact[1:])
            else:
                hour = int(compact[:2])
                minute = int(compact[2:])
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                return f"{hour:02d}:{minute:02d}"

        match = re.search(r"(\d{2}:\d{2})", text)
        return match.group(1) if match else text

    @staticmethod
    def _time_to_minutes(raw_time: Any) -> int | None:
        hhmm = TimeUtilsMixin._to_hhmm(raw_time)
        if not hhmm:
            return None
        try:
            hour, minute = hhmm.split(":")
            return int(hour) * 60 + int(minute)
        except Exception:
            return None

    @staticmethod
    def _minutes_to_hhmm(value: int) -> str:
        hour = max(0, value) // 60
        minute = max(0, value) % 60
        return f"{hour:02d}:{minute:02d}"

    @classmethod
    def _format_time_window(cls, start_time: Any = "", end_time: Any = "") -> str:
        start_hhmm = cls._to_hhmm(start_time)
        end_hhmm = cls._to_hhmm(end_time)
        if start_hhmm and end_hhmm:
            return f"{start_hhmm}-{end_hhmm}"
        return start_hhmm or end_hhmm or ""

    @staticmethod
    def _normalize_seat_no(value: Any) -> str:
        text = str(value or "").strip()
        return text.lstrip("0") or "0"

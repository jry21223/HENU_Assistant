from __future__ import annotations

import re
from datetime import date
from typing import Any

from henu_mcp.core.period_times import is_hhmm


def reservation_request_error(
    *,
    target_date: str,
    preferred_time: str,
    preferred_end_time: str = "",
    end_policy: str = "",
) -> dict[str, Any] | None:
    """Validate explicit reservation intent without touching school systems."""
    day = str(target_date or "").strip()
    start = str(preferred_time or "").strip()
    end = str(preferred_end_time or "").strip()
    policy = str(end_policy or "").strip().lower()

    def error(code: str, message: str) -> dict[str, Any]:
        return {"success": False, "code": code, "msg": message}

    if not day:
        return error("missing_target_date", "预约必须明确提供 target_date（YYYY-MM-DD），不能默认明天")
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            raise ValueError
        date.fromisoformat(day)
    except ValueError:
        return error("invalid_target_date", "target_date 必须为有效的 YYYY-MM-DD 日期")
    if not start:
        return error("missing_preferred_time", "预约必须明确提供 preferred_time（HH:MM）")
    if not is_hhmm(start):
        return error("invalid_preferred_time", "preferred_time 必须为有效的 24 小时制 HH:MM")
    if end and not is_hhmm(end):
        return error("invalid_preferred_end_time", "preferred_end_time 必须为有效的 24 小时制 HH:MM")
    if end and start >= end:
        return error("invalid_time_range", "preferred_time 必须早于 preferred_end_time")
    if policy and policy not in {"ask", "server_full_slot"}:
        return error("invalid_end_policy", "end_policy 仅支持 ask/server_full_slot")
    if not end:
        if policy == "ask":
            return error("end_time_confirmation_required", "请先向用户确认结束时间，再提交预约")
        if policy != "server_full_slot":
            return error("missing_end_strategy", "请提供 preferred_end_time，或明确选择 end_policy=server_full_slot")
    return None

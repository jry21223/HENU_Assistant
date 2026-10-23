"""Local reminder reads and durable, account-wide daily school refreshes."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from henu_mcp.core.schedule_cleaner import WEEKDAY_ORDER
from henu_mcp.core.schedule_weeks import filter_courses

ZONE = ZoneInfo("Asia/Shanghai")
REFRESH_INTERVAL = timedelta(days=1)
logger = logging.getLogger(__name__)
_account_locks: dict[tuple[int, str], asyncio.Lock] = {}


def _timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value or ""))
        return parsed if parsed.tzinfo is not None else None
    except ValueError:
        return None


def _profile(paths):
    data = json.loads(paths.profile_file.read_text())
    return data if isinstance(data, dict) else {}


def _snapshot(paths, student_id):
    data = json.loads(paths.schedule_file.read_text())
    if not isinstance(data, dict) or not student_id or data.get("_subject_student_id") != student_id:
        return None
    schedule = data.get("schedule")
    if (not isinstance(schedule, dict) or not schedule
            or not set(schedule).issubset(WEEKDAY_ORDER)
            or any(not isinstance(rows, list) or any(
                not isinstance(row, dict) or not str(row.get("course") or "").strip()
                for row in rows) for rows in schedule.values())):
        return None
    return data


def _fresh(generated, now):
    return generated is not None and -timedelta(minutes=5) <= now - generated < REFRESH_INTERVAL


async def _read_guard(plugin, key):
    try:
        raw = await plugin.get_plugin_storage(key)
    except Exception as exc:
        from langbot_plugin.entities.io.errors import ActionCallError
        if isinstance(exc, (KeyError, FileNotFoundError)) or (
            isinstance(exc, ActionCallError) and re.fullmatch(
                r"(?:ActionCallError: )*Storage with key " + re.escape(key) + r" not found", str(exc))):
            return {}
        raise
    data = json.loads(raw or "{}")
    if not isinstance(data, dict):
        raise ValueError("invalid reminder refresh state")
    return data


async def _write_guard(plugin, key, data):
    await plugin.set_plugin_storage(key, json.dumps(data).encode())


async def refresh_if_due(plugin, sender, adapter, paths, now):
    """Return whether the caller must reload Storage after a refresh attempt.

    The claim is paused BEFORE school I/O. Crashes, timeouts, auth failures and
    failed commits therefore require a later successful manual sync to resume.
    """
    profile = _profile(paths)
    student_id = str(profile.get("student_id") or "").strip()
    if not student_id or not profile.get("password"):
        return False
    key = "schedule_refresh:v1:" + hashlib.sha256(student_id.encode()).hexdigest()
    lock_key = (id(asyncio.get_running_loop()), key)
    async with _account_locks.setdefault(lock_key, asyncio.Lock()):
        guard = await _read_guard(plugin, key)
        saved = _snapshot(paths, student_id)
        generated = _timestamp((saved or {}).get("generated_at"))
        attempted = _timestamp(guard.get("last_attempt_at"))
        if guard and (attempted is None or not isinstance(guard.get("paused"), bool)):
            return False
        if guard.get("paused"):
            # Only a newer, fresh, subject-matching manual sync can resume it.
            if (generated and generated > attempted and generated <= now + timedelta(minutes=5)
                    and not (saved or {}).get("_reminder_refresh_id")):
                await _write_guard(plugin, key, {**guard, "paused": False,
                                               "last_attempt_at": generated.isoformat(),
                                               "last_success_at": generated.isoformat()})
            return False
        if _fresh(generated, now) or (attempted and now - attempted < REFRESH_INTERVAL):
            return False

        claim = {"last_attempt_at": now.isoformat(), "paused": True, "attempt_id": uuid.uuid4().hex}
        await _write_guard(plugin, key, claim)
        try:
            from langbot_plugin.api.entities.builtin.provider.session import Session, LauncherTypes
            session = Session(launcher_type=LauncherTypes.PERSON, launcher_id=sender, sender_id=sender)
            result = await asyncio.to_thread(plugin.service.run_tool, "sync_schedule",
                                            {"auto_calibrate": False}, session, 0)
            updated = _snapshot(paths, student_id)
            updated_at = _timestamp((updated or {}).get("generated_at"))
            if not result.get("success") or not _fresh(updated_at, now):
                return True
            updated["_reminder_refresh_id"] = claim["attempt_id"]
            paths.schedule_file.write_text(json.dumps(updated, ensure_ascii=False), encoding="utf-8")
            await adapter.save_all()
            await _write_guard(plugin, key, {**claim, "paused": False,
                                           "last_success_at": updated_at.isoformat()})
        except Exception:
            logger.warning("Class reminder refresh paused; a successful manual sync is required")
        return True


def read_saved_schedule(paths, now):
    """Filter saved data without the interactive service or its network locks."""
    student_id = str(_profile(paths).get("student_id") or "").strip()
    saved = _snapshot(paths, student_id)
    if saved is None:
        return {"success": False, "error_code": "saved_schedule_unavailable"}
    weekday = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")[now.weekday()]
    filtered = filter_courses(saved["schedule"].get(weekday, []), now.date())
    if not filtered.get("success"):
        return filtered
    periods = json.loads((paths.shared_data_dir / "period_time_config.json").read_text())
    return {**filtered, "day_schedule": filtered["schedule"], "period_times": periods}

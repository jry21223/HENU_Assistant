from __future__ import annotations

import argparse
import json
import re
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
from mcp.server.fastmcp import FastMCP

from henu_mcp.core.cas_session import (
    extract_cas_cookies as _extract_ids_cas_cookies,
    merge_cas_cookies as _merge_cas_cookies,
)
from henu_mcp.core.course_schedule import (
    COOKIE_FILE,
    DEFAULT_HOME_URL,
    OUTPUT_DIR,
    PROFILE_FILE,
    HenuXkClient,
    load_json,
    run_fetch,
    save_json,
)
from henu_mcp.core.course_planner import generate_ranked_plans
from henu_mcp.core.course_monitor import (
    DEFAULT_CONFIG as COURSE_MONITOR_DEFAULT_CONFIG,
    MIN_INTERVAL_SECONDS as COURSE_MONITOR_MIN_INTERVAL_SECONDS,
    evaluate_alerts,
    evaluate_matches,
    get_monitor_config_file,
    load_monitor_config,
    load_monitor_state,
    normalize_monitor_config,
    notify_alerts,
    parse_teaching_class_rows,
    save_monitor_config,
    save_monitor_state,
    test_notification,
)
from henu_mcp.core.course_selection import HenuCourseSelectionClient
from henu_mcp.core.period_times import (
    extract_period_times_from_text as _extract_period_times_from_text,
    extract_period_times_from_xiqueer_json as _extract_period_times_from_xiqueer_json,
    is_hhmm as _is_hhmm,
    minutes_to_hhmm as _minutes_to_hhmm,
    normalize_teaching_period_times as _normalize_teaching_period_times,
    to_minutes as _to_minutes,
)
from henu_mcp.core.schedule_cleaner import clean_schedule_grid_file, load_latest_clean_schedule
from henu_mcp.core.secure_storage import (
    encrypt_value,
    decrypt_value,
    is_encrypted,
    load_encrypted_profile,
    save_encrypted_profile,
)

mcp = FastMCP("henu-campus-unified")
BASE_DIR = Path(__file__).resolve().parents[2]
PERIOD_TIME_FILE = BASE_DIR / "period_time_config.json"
PERIOD_CALIBRATION_STATE_FILE = BASE_DIR / "period_time_calibration_state.json"
XIQUEER_REQUEST_FILE = BASE_DIR / "xiqueer_period_time_request.json"
CAMPUS_CORE_EXPECTED_FILE = BASE_DIR / "campus_core" / "bot.py"

LIBRARY_COOKIE_FILE = BASE_DIR / "henu_library_cookies.json"
HEBAO_TOKEN_FILE = BASE_DIR / "henu_yunfz_token.json"
CAS_COOKIE_FILE = BASE_DIR / "henu_cas_cookies.json"
SEMINAR_SIGNIN_TASK_FILE = BASE_DIR / "seminar_signin_tasks.json"
SEMINAR_AUTO_SIGNIN_INTERVAL_SECONDS = 30
WEEKDAY_CN = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
DEFAULT_PERIOD_TIMES: dict[str, dict[str, str]] = {
    "1": {"start": "08:00", "end": "08:45"},
    "2": {"start": "08:55", "end": "09:40"},
    "3": {"start": "10:00", "end": "10:45"},
    "4": {"start": "10:55", "end": "11:40"},
    "5": {"start": "11:45", "end": "12:30"},
    "6": {"start": "13:00", "end": "13:30"},
    "7": {"start": "13:30", "end": "14:00"},
    "8": {"start": "14:05", "end": "14:50"},
    "9": {"start": "15:00", "end": "15:45"},
    "10": {"start": "15:55", "end": "16:40"},
    "11": {"start": "17:00", "end": "17:45"},
    "12": {"start": "17:55", "end": "18:40"},
    "13": {"start": "19:10", "end": "19:55"},
    "14": {"start": "20:05", "end": "20:50"},
    "15": {"start": "20:55", "end": "21:40"},
}
_SEMINAR_SIGNIN_TASK_LOCK = threading.Lock()
_SEMINAR_AUTO_SIGNIN_THREAD: threading.Thread | None = None
_SEMINAR_AUTO_SIGNIN_THREAD_LOCK = threading.Lock()
_LAST_LIBRARY_LOGIN_ERROR = ""

# 尝试导入校园核心模块
HenuCampusBot = None
try:
    from campus_core import HenuCampusBot
except Exception:
    pass

# 空教室查询模块
EmptyClassroomClient = None
query_free_classrooms = None  # type: ignore[assignment]
_sync_schedule_impl = None  # type: ignore[assignment]
try:
    from campus_core.empty_classroom import (
        EmptyClassroomClient,
        query_free_classrooms,
    )
    from campus_core.empty_classroom.query import sync_schedule as _sync_schedule_impl
except Exception:
    pass

# 全局资源编号映射模块
_resource_registry_available = False
try:
    from campus_core.resource_registry import (  # noqa: F811
        resolve_resource,
        search_resources,
        list_resources as _list_registry_resources,
        get_stats as _get_registry_stats,
        upsert_resource,
        get_resource,
        sync_classrooms_from_metadata,
        sync_library_resources,
        sync_seminar_resources,
    )
    _resource_registry_available = True
except Exception:
    pass

def _now_dt(timezone: str = "Asia/Shanghai") -> datetime:
    try:
        return datetime.now(ZoneInfo(timezone))
    except Exception:
        return datetime.now(ZoneInfo("Asia/Shanghai"))


def _load_period_times() -> dict[str, dict[str, str]]:
    if PERIOD_TIME_FILE.exists():
        try:
            data = json.loads(PERIOD_TIME_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cleaned: dict[str, dict[str, str]] = {}
                for k, v in data.items():
                    if not isinstance(v, dict):
                        continue
                    start = str(v.get("start", "")).strip()
                    end = str(v.get("end", "")).strip()
                    if _is_hhmm(start) and _is_hhmm(end):
                        cleaned[str(k)] = {"start": start, "end": end}
                if cleaned:
                    normalized, meta = _normalize_teaching_period_times(cleaned)
                    if normalized and normalized != cleaned and meta.get("removed_midday_count", 0) > 0:
                        _save_period_times(normalized)
                    return normalized or cleaned
        except Exception:
            pass

    PERIOD_TIME_FILE.write_text(
        json.dumps(DEFAULT_PERIOD_TIMES, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return dict(DEFAULT_PERIOD_TIMES)


def _save_period_times(period_times: dict[str, dict[str, str]]) -> None:
    PERIOD_TIME_FILE.write_text(
        json.dumps(period_times, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _load_calibration_state() -> dict[str, Any]:
    if not PERIOD_CALIBRATION_STATE_FILE.exists():
        return {}
    try:
        data = json.loads(PERIOD_CALIBRATION_STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_calibration_state(state: dict[str, Any]) -> None:
    PERIOD_CALIBRATION_STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _decode_resp_text(resp: requests.Response) -> str:
    content = resp.content or b""
    for enc in ("utf-8", "gbk", "gb2312"):
        try:
            return content.decode(enc)
        except Exception:
            continue
    return content.decode("utf-8", errors="ignore")


def _load_xiqueer_request_config() -> dict[str, Any]:
    if not XIQUEER_REQUEST_FILE.exists():
        return {}
    try:
        data = json.loads(XIQUEER_REQUEST_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_xiqueer_request_config(config: dict[str, Any]) -> None:
    XIQUEER_REQUEST_FILE.write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _xiqueer_config_summary(config: dict[str, Any]) -> dict[str, Any]:
    headers = config.get("headers") if isinstance(config.get("headers"), dict) else {}
    data_text = str(config.get("data", "") or "")
    return {
        "exists": bool(config),
        "config_file": str(XIQUEER_REQUEST_FILE),
        "url": str(config.get("url", "") or ""),
        "header_keys": sorted(list(headers.keys())),
        "has_cookie": "Cookie" in headers or "cookie" in headers,
        "data_length": len(data_text),
    }


def _fetch_xiqueer_period_times() -> dict[str, Any]:
    config = _load_xiqueer_request_config()
    if not config:
        return {"success": False, "msg": f"未配置 {XIQUEER_REQUEST_FILE}"}

    url = str(config.get("url", "") or "").strip()
    data_text = str(config.get("data", "") or "")
    headers = config.get("headers") if isinstance(config.get("headers"), dict) else {}
    timeout = int(config.get("timeout", 25) or 25)

    if not url:
        return {"success": False, "msg": "xiqueer 配置缺少 url"}
    if not data_text:
        return {"success": False, "msg": "xiqueer 配置缺少 data"}

    # 交给 requests 自动处理 Content-Length / Connection
    filtered_headers = {k: str(v) for k, v in headers.items() if k.lower() not in {"content-length", "connection"}}

    session = requests.Session()
    session.trust_env = False
    try:
        resp = session.post(url, headers=filtered_headers or None, data=data_text, timeout=timeout)
        text = _decode_resp_text(resp)
        period_times = _extract_period_times_from_xiqueer_json(text)
        return {
            "success": len(period_times) >= 4,
            "status_code": resp.status_code,
            "url": str(resp.url),
            "matched_period_count": len(period_times),
            "period_times": period_times,
            "raw_text": text,
            "msg": "xiqueer 节次时间获取成功" if len(period_times) >= 4 else "xiqueer 返回中未解析到足够节次时间",
        }
    except Exception as exc:
        return {"success": False, "msg": f"xiqueer 请求失败: {exc}"}


def _fetch_timetable_text_candidates(sid: str, pwd: str) -> list[tuple[str, str]]:
    """
    返回 [(source_url, text)] 候选列表。
    """
    candidates: list[tuple[str, str]] = []
    ts = int(_now_dt().timestamp())
    urls = [
        f"https://xk.henu.edu.cn/public/SchoolTimetable.jsp?t={ts}",
        "https://xk.henu.edu.cn/public/SchoolTimetable.jsp",
        "http://xk.henu.edu.cn/public/SchoolTimetable.jsp",
    ]

    # 1) 用课表登录会话尝试
    if sid and pwd:
        try:
            client = HenuXkClient(sid, pwd, saved_cookies=_load_xk_cookies() or None)
            if client.login():
                _save_xk_cookies(client.get_cookies())
                home = client.fetch_page(DEFAULT_HOME_URL)
                referers = [home.get("final_url", ""), client._cas_auth_url(), ""]
                for u in urls:
                    for ref in referers:
                        try:
                            page = client.fetch_page(u, referer=ref or None)
                            candidates.append((str(page.get("final_url", u)), str(page.get("text", ""))))
                        except Exception:
                            continue
        except Exception:
            pass

    # 2) 用公开登录页会话尝试
    try:
        cas_auth_url = "https://ids.henu.edu.cn/authserver/login?service=https://xk.henu.edu.cn/caslogin"
        session = requests.Session()
        session.trust_env = False
        session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            }
        )
        session.get(cas_auth_url, timeout=20)
        for u in urls:
            try:
                resp = session.get(
                    u,
                    headers={"Referer": cas_auth_url},
                    allow_redirects=True,
                    timeout=20,
                )
                candidates.append((str(resp.url), _decode_resp_text(resp)))
            except Exception:
                continue
    except Exception:
        pass

    return candidates


def _auto_calibrate_period_time_impl(force: bool = False) -> dict[str, Any]:
    now = _now_dt()
    state = _load_calibration_state()
    if not force and state.get("last_attempt_at"):
        try:
            last = datetime.fromisoformat(str(state["last_attempt_at"]))
            if now - last < timedelta(hours=6):
                return {
                    "success": bool(state.get("success")),
                    "skipped": True,
                    "reason": "最近 6 小时内已尝试校准",
                    "state": state,
                }
        except Exception:
            pass

    period_times = _load_period_times()
    updated_count = 0
    best_source = ""
    best_matches: dict[str, dict[str, str]] = {}
    normalization_meta: dict[str, Any] = {"applied": False, "removed_midday_count": 0}
    xiqueer_result = _fetch_xiqueer_period_times()

    # 1) 优先 xiqueer API
    if xiqueer_result.get("success"):
        matches = xiqueer_result.get("period_times") or {}
        if isinstance(matches, dict):
            best_matches = {str(k): v for k, v in matches.items() if isinstance(v, dict)}
            best_source = f"xiqueer_api:{xiqueer_result.get('url', '')}"

    # 2) 回退网页解析
    if len(best_matches) < 4:
        sid, pwd = _resolve_account("", "", use_saved_account=True)
        text_candidates = _fetch_timetable_text_candidates(sid, pwd)
        for source, text in text_candidates:
            matches = _extract_period_times_from_text(text)
            if len(matches) > len(best_matches):
                best_matches = matches
                best_source = source

    if len(best_matches) >= 4:
        normalized_matches, normalization_meta = _normalize_teaching_period_times(best_matches)
        if normalized_matches:
            best_matches = normalized_matches

    if len(best_matches) >= 4:
        new_period_times = dict(best_matches)
        for period, cfg in new_period_times.items():
            old = period_times.get(period)
            if old != cfg:
                period_times[period] = cfg
                updated_count += 1
        stale_periods = [k for k in list(period_times.keys()) if k not in new_period_times]
        for stale_key in stale_periods:
            period_times.pop(stale_key, None)
            updated_count += 1
        if updated_count > 0:
            _save_period_times(period_times)

    success = len(best_matches) >= 4
    if success and best_source.startswith("xiqueer_api:"):
        message = "已从 xiqueer 接口自动校准"
    elif success:
        message = "已从教务作息页面自动校准"
    else:
        message = "未抓取到可用节次时间，保留现有配置"
    if success and int(normalization_meta.get("removed_midday_count", 0) or 0) > 0:
        message += f"，已剔除中午短节次 {int(normalization_meta.get('removed_midday_count', 0) or 0)} 个"

    new_state = {
        "last_attempt_at": now.isoformat(timespec="seconds"),
        "success": success,
        "matched_period_count": len(best_matches),
        "updated_period_count": updated_count,
        "source": best_source,
        "message": message,
        "xiqueer_success": bool(xiqueer_result.get("success")),
        "xiqueer_msg": str(xiqueer_result.get("msg", "")),
        "xiqueer_matched_period_count": int(xiqueer_result.get("matched_period_count", 0) or 0),
        "normalization": normalization_meta,
    }
    _save_calibration_state(new_state)

    return {
        "success": success,
        "skipped": False,
        "matched_period_count": len(best_matches),
        "updated_period_count": updated_count,
        "source": best_source,
        "period_times_matched": best_matches,
        "current_period_times": period_times,
        "normalization": normalization_meta,
        "xiqueer_result": {
            "success": bool(xiqueer_result.get("success")),
            "msg": str(xiqueer_result.get("msg", "")),
            "matched_period_count": int(xiqueer_result.get("matched_period_count", 0) or 0),
            "url": str(xiqueer_result.get("url", "")),
            "status_code": xiqueer_result.get("status_code"),
        },
        "msg": new_state["message"],
    }


def _extract_period_range(course_item: dict[str, Any]) -> tuple[int, int] | None:
    period_text = str(course_item.get("period", "") or "")
    m = re.search(r"第(\d+)(?:-(\d+))?节", period_text)
    if m:
        start = int(m.group(1))
        end = int(m.group(2) or m.group(1))
        return (start, end)

    time_text = str(course_item.get("time", "") or "")
    m2 = re.search(r"\[(\d+)-(\d+)\]", time_text)
    if m2:
        return (int(m2.group(1)), int(m2.group(2)))
    return None


def _course_with_clock(
    course_item: dict[str, Any],
    period_times: dict[str, dict[str, str]],
) -> dict[str, Any] | None:
    period_range = _extract_period_range(course_item)
    if not period_range:
        return None
    start_idx, end_idx = period_range
    start_cfg = period_times.get(str(start_idx))
    end_cfg = period_times.get(str(end_idx))
    if not start_cfg or not end_cfg:
        return None

    start_hhmm = start_cfg["start"]
    end_hhmm = end_cfg["end"]
    return {
        **course_item,
        "period_start": start_idx,
        "period_end": end_idx,
        "clock_start": start_hhmm,
        "clock_end": end_hhmm,
        "clock_start_minutes": _to_minutes(start_hhmm),
        "clock_end_minutes": _to_minutes(end_hhmm),
    }


def get_current_course_status(
    timezone: str = "Asia/Shanghai",
    auto_calibrate: bool = True,
) -> dict[str, Any]:
    """
    查询当前课程状态：当前课程 + 下一节课程，并附带节次时钟映射结果。
    """
    calibration: dict[str, Any] = {}
    if auto_calibrate:
        calibration = auto_calibrate_period_time(force=False)

    try:
        schedule_data = load_latest_clean_schedule(OUTPUT_DIR)
    except Exception as exc:
        return {"success": False, "msg": f"未找到课表，请先同步：{exc}", "period_time_calibration": calibration}

    period_times = _load_period_times()
    now = _now_dt(timezone)
    now_minutes = now.hour * 60 + now.minute
    weekday_cn = WEEKDAY_CN[now.weekday()]
    day_courses = list((schedule_data.get("schedule") or {}).get(weekday_cn, []) or [])

    current_courses: list[dict[str, Any]] = []
    future_courses: list[tuple[int, dict[str, Any]]] = []

    for item in day_courses:
        with_clock = _course_with_clock(item, period_times)
        if not with_clock:
            continue
        start_min = int(with_clock.get("clock_start_minutes", 0))
        end_min = int(with_clock.get("clock_end_minutes", 0))
        if start_min <= now_minutes <= end_min:
            current_courses.append(with_clock)
        elif now_minutes < start_min:
            future_courses.append((start_min, with_clock))

    future_courses.sort(key=lambda x: x[0])
    next_course = future_courses[0][1] if future_courses else None
    if current_courses:
        msg = f"当前共有 {len(current_courses)} 门正在上的课程"
    elif next_course:
        msg = (
            "当前没有正在上的课，下一节是"
            f"{next_course.get('clock_start', '')}-{next_course.get('clock_end', '')} "
            f"{next_course.get('course', '')}"
        ).strip()
    elif day_courses:
        msg = "当前没有正在上的课，且今天后续没有课程"
    else:
        msg = "今天课表为空"

    return {
        "success": True,
        "now": now.strftime("%Y-%m-%d %H:%M:%S"),
        "timezone": timezone,
        "weekday": weekday_cn,
        "msg": msg,
        "day_schedule": day_courses,
        "day_schedule_count": len(day_courses),
        "current_courses": current_courses,
        "next_course": next_course,
        "period_times": period_times,
        "period_time_calibration": calibration,
    }


def _effective_profile() -> dict[str, Any]:
    """加载并解密配置文件"""
    return load_encrypted_profile(PROFILE_FILE)


def _mask_profile(profile: dict[str, Any]) -> dict[str, Any]:
    location, seat_no = _resolve_library_defaults()
    seminar_groups = _load_seminar_groups()
    seminar_tasks = _load_seminar_signin_tasks()
    return {
        "student_id": str(profile.get("student_id", "") or ""),
        "has_password": bool(profile.get("password")),
        "library_default_location": location,
        "library_default_seat_no": seat_no,
        "seminar_groups_total": len(seminar_groups),
        "seminar_signin_tasks_total": len(seminar_tasks),
        "has_seminar_mobile": bool(str(profile.get("seminar_mobile") or profile.get("mobile") or "").strip()),
        "profile_file": str(PROFILE_FILE),
        "cookie_file": str(COOKIE_FILE),
        "library_cookie_file": str(LIBRARY_COOKIE_FILE),
        "seminar_signin_task_file": str(SEMINAR_SIGNIN_TASK_FILE),
    }


def _resolve_account(student_id: str, password: str, use_saved_account: bool = True) -> tuple[str, str]:
    sid = str(student_id or "").strip()
    pwd = str(password or "")
    if not use_saved_account:
        return sid, pwd

    profile = _effective_profile()
    sid = sid or str(profile.get("student_id", "") or "")
    pwd = pwd or str(profile.get("password", "") or "")
    return sid, pwd


def _save_profile_fields(fields: dict[str, Any]) -> None:
    """加密并保存配置字段"""
    profile = load_encrypted_profile(PROFILE_FILE)
    profile.update(fields)
    save_encrypted_profile(PROFILE_FILE, profile)


def _resolve_library_defaults() -> tuple[str, str]:
    local_profile = _effective_profile()
    location = str(
        local_profile.get("library_location")
        or local_profile.get("location")
        or ""
    ).strip()
    seat_no = str(
        local_profile.get("library_seat_no")
        or local_profile.get("seat_no")
        or ""
    ).strip()
    return location, seat_no


def _parse_csv_text(value: str | list[str] | tuple[str, ...] | None) -> list[str]:
    if isinstance(value, (list, tuple)):
        items = [str(item or "").strip() for item in value]
    else:
        text = str(value or "").strip()
        if not text:
            return []
        items = re.split(r"[\s,，;；]+", text)
    return [item for item in items if item]


def _load_seminar_groups() -> dict[str, Any]:
    profile = _effective_profile()
    groups = profile.get("seminar_groups") or {}
    return groups if isinstance(groups, dict) else {}


def _save_seminar_groups(groups: dict[str, Any]) -> None:
    _save_profile_fields({"seminar_groups": groups})


def _seminar_group_summary(name: str, item: dict[str, Any]) -> dict[str, Any]:
    member_ids = [str(member).strip() for member in (item.get("member_ids") or []) if str(member).strip()]
    return {
        "group_name": name,
        "member_ids": member_ids,
        "member_count": len(member_ids),
        "total_with_self": len(member_ids) + 1,
        "note": str(item.get("note") or ""),
        "updated_at": str(item.get("updated_at") or ""),
    }


def _saved_seminar_mobile() -> str:
    profile = _effective_profile()
    return str(profile.get("seminar_mobile") or profile.get("mobile") or "").strip()


def _load_seminar_signin_tasks() -> list[dict[str, Any]]:
    data = load_json(SEMINAR_SIGNIN_TASK_FILE)
    tasks = data.get("tasks") or []
    if not isinstance(tasks, list):
        return []
    return [item for item in tasks if isinstance(item, dict)]


def _save_seminar_signin_tasks(tasks: list[dict[str, Any]]) -> None:
    save_json(SEMINAR_SIGNIN_TASK_FILE, {"tasks": tasks})


def _parse_dt_text(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed = None

    if parsed is None:
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue

    if parsed is None:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    return parsed


def _format_dt_text(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.astimezone(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")


def _seminar_record_summary(record: dict[str, Any]) -> dict[str, Any]:
    room_name = (
        record.get("nameMerge")
        or record.get("name")
        or record.get("area")
        or record.get("areaName")
        or ""
    )
    return {
        "id": str(record.get("id") or ""),
        "area_id": str(record.get("area_id") or record.get("areaId") or ""),
        "room_name": str(room_name or ""),
        "title": str(record.get("title") or ""),
        "status": str(record.get("status") or ""),
        "status_name": str(record.get("status_name") or record.get("statusName") or ""),
        "show_time": str(record.get("show_time") or record.get("showTime") or ""),
        "begin_time": str(record.get("begin_time") or record.get("beginTime") or ""),
        "end_time": str(record.get("end_time") or record.get("endTime") or ""),
        "owner": str(record.get("owner") or ""),
        "member_name": str(record.get("member_name") or record.get("memberName") or ""),
        "member_id": str(record.get("member_id") or record.get("memberId") or ""),
    }


def _seminar_task_summary(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": str(task.get("task_id") or ""),
        "record_id": str(task.get("record_id") or ""),
        "record_type": str(task.get("record_type") or ""),
        "area_id": str(task.get("area_id") or ""),
        "room_name": str(task.get("room_name") or ""),
        "title": str(task.get("title") or ""),
        "start_date": str(task.get("start_date") or ""),
        "start_time": str(task.get("start_time") or ""),
        "end_date": str(task.get("end_date") or ""),
        "end_time": str(task.get("end_time") or ""),
        "sign_at": str(task.get("sign_at") or ""),
        "status": str(task.get("status") or ""),
        "attempts": int(task.get("attempts") or 0),
        "last_msg": str(task.get("last_msg") or ""),
        "created_at": str(task.get("created_at") or ""),
        "updated_at": str(task.get("updated_at") or ""),
        "last_result": task.get("last_result") or {},
    }


def _seminar_minutes_from_dt_text(value: Any) -> tuple[int | None, int]:
    parsed = _parse_dt_text(value)
    if parsed is None:
        return None, 0
    local = parsed.astimezone(ZoneInfo("Asia/Shanghai"))
    return local.hour * 60 + local.minute, local.second


def _seminar_slot_start_minutes(slot: dict[str, Any]) -> int | None:
    raw = slot.get("begin_num")
    try:
        if raw is not None and str(raw).strip() != "":
            return int(raw)
    except (TypeError, ValueError):
        pass
    minutes, _ = _seminar_minutes_from_dt_text(slot.get("begin_timestamp"))
    return minutes


def _seminar_slot_end_boundary_minutes(slot: dict[str, Any]) -> int | None:
    raw = slot.get("end_num")
    seconds = 0
    parsed_minutes, parsed_seconds = _seminar_minutes_from_dt_text(slot.get("end_timestamp"))
    if parsed_minutes is not None:
        seconds = parsed_seconds
    try:
        if raw is not None and str(raw).strip() != "":
            value = int(raw)
            return value + 1 if seconds >= 59 else value
    except (TypeError, ValueError):
        pass
    if parsed_minutes is None:
        return None
    return parsed_minutes + 1 if seconds >= 59 else parsed_minutes


def _seminar_format_slot_label(start_min: int, end_min: int) -> str:
    return f"{_minutes_to_hhmm(start_min)}-{_minutes_to_hhmm(end_min)}"


def _seminar_day_text_from_timestamp(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text.split(" ")[0].strip()


def _seminar_attach_day_to_slots(slots: list[dict[str, Any]], day_text: str, area_id: str = "") -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for slot in slots:
        if not isinstance(slot, dict):
            continue
        start_time = str(slot.get("start_time") or "").strip()
        end_time = str(slot.get("end_time") or "").strip()
        begin_num = slot.get("begin_num")
        end_num = slot.get("end_num")
        item = {
            **slot,
            "areaId": area_id,
            "begin_timestamp": f"{day_text} {start_time}:00".strip() if day_text and start_time else "",
            "end_timestamp": f"{day_text} {end_time}:00".strip() if day_text and end_time else "",
        }
        if begin_num is None and start_time:
            item["begin_num"] = _to_minutes(start_time)
        if end_num is None and end_time:
            item["end_num"] = _to_minutes(end_time)
        items.append(item)
    return items


def _seminar_normalize_occupied_slots(raw_slots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for slot in raw_slots:
        if not isinstance(slot, dict):
            continue
        start_min = _seminar_slot_start_minutes(slot)
        end_boundary = _seminar_slot_end_boundary_minutes(slot)
        if start_min is None or end_boundary is None or start_min >= end_boundary:
            continue
        items.append(
            {
                "start_time": _minutes_to_hhmm(start_min),
                "end_time": _minutes_to_hhmm(end_boundary),
                "label": _seminar_format_slot_label(start_min, end_boundary),
                "begin_timestamp": str(slot.get("begin_timestamp") or ""),
                "end_timestamp": str(slot.get("end_timestamp") or ""),
                "begin_num": start_min,
                "end_num": end_boundary,
            }
        )
    items.sort(key=lambda item: (item.get("begin_num", 0), item.get("end_num", 0)))
    return items


def _seminar_compute_available_slots(
    open_start_min: int | None,
    open_end_min: int | None,
    occupied_slots: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if open_start_min is None or open_end_min is None or open_start_min >= open_end_min:
        return []

    merged: list[tuple[int, int]] = []
    for slot in occupied_slots:
        try:
            start_min = int(slot.get("begin_num"))
            end_min = int(slot.get("end_num"))
        except (TypeError, ValueError):
            continue
        start_min = max(open_start_min, start_min)
        end_min = min(open_end_min, end_min)
        if start_min >= end_min:
            continue
        if not merged or start_min > merged[-1][1]:
            merged.append((start_min, end_min))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end_min))

    free: list[dict[str, Any]] = []
    cursor = open_start_min
    for start_min, end_min in merged:
        if cursor < start_min:
            free.append(
                {
                    "start_time": _minutes_to_hhmm(cursor),
                    "end_time": _minutes_to_hhmm(start_min),
                    "label": _seminar_format_slot_label(cursor, start_min),
                }
            )
        cursor = max(cursor, end_min)
    if cursor < open_end_min:
        free.append(
            {
                "start_time": _minutes_to_hhmm(cursor),
                "end_time": _minutes_to_hhmm(open_end_min),
                "label": _seminar_format_slot_label(cursor, open_end_min),
            }
        )
    return free


def _seminar_room_summary(room: dict[str, Any]) -> dict[str, Any]:
    raw_occupied = room.get("date") or []
    occupied_slots = _seminar_normalize_occupied_slots(raw_occupied if isinstance(raw_occupied, list) else [])

    open_start_min = None
    open_end_min = None
    try:
        raw_start = room.get("begin_num")
        if raw_start is not None and str(raw_start).strip() != "":
            open_start_min = int(raw_start)
    except (TypeError, ValueError):
        pass
    try:
        raw_end = room.get("end_num")
        if raw_end is not None and str(raw_end).strip() != "":
            open_end_min = int(raw_end)
    except (TypeError, ValueError):
        pass
    if open_start_min is None:
        open_start_min, _ = _seminar_minutes_from_dt_text(room.get("start_timestamp"))
    if open_end_min is None:
        open_end_min, _ = _seminar_minutes_from_dt_text(room.get("end_timestamp"))

    day_text = (
        _seminar_day_text_from_timestamp(room.get("start_timestamp"))
        or _seminar_day_text_from_timestamp(room.get("end_timestamp"))
    )
    area_id = str(room.get("id") or "")
    available_slots = _seminar_compute_available_slots(open_start_min, open_end_min, occupied_slots)
    available_slots = _seminar_attach_day_to_slots(available_slots, day_text, area_id=area_id)
    open_label = ""
    if open_start_min is not None and open_end_min is not None and open_start_min < open_end_min:
        open_label = _seminar_format_slot_label(open_start_min, open_end_min)

    return {
        **room,
        "date": available_slots,
        "date_semantics": "available_slots",
        "time_field_warning": "date/available_slots 表示可预约时段；occupied_slots 表示已占用时段",
        "open_time_range": {
            "start_time": _minutes_to_hhmm(open_start_min) if open_start_min is not None else "",
            "end_time": _minutes_to_hhmm(open_end_min) if open_end_min is not None else "",
            "label": open_label,
        },
        "raw_occupied_date": raw_occupied,
        "occupied_slots": occupied_slots,
        "available_slots": available_slots,
        "is_fully_available": bool(not occupied_slots and available_slots),
    }


def _normalize_compare_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def _extract_seminar_record_id(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    for key in ("id", "record_id", "recordId", "book_id", "bookId", "apply_id", "applyId"):
        value = str(data.get(key) or "").strip()
        if value:
            return value
    return ""


def _task_time_text(task: dict[str, Any]) -> str:
    return " ".join(
        [
            str(task.get("start_date") or ""),
            str(task.get("start_time") or ""),
            str(task.get("end_date") or ""),
            str(task.get("end_time") or ""),
        ]
    )


def _record_time_text(record: dict[str, Any]) -> str:
    return " ".join(
        [
            str(record.get("show_time") or record.get("showTime") or ""),
            str(record.get("begin_time") or record.get("beginTime") or ""),
            str(record.get("end_time") or record.get("endTime") or ""),
        ]
    )


def _seminar_task_matches_record(task: dict[str, Any], record: dict[str, Any]) -> bool:
    task_record_id = str(task.get("record_id") or "").strip()
    record_id = str(record.get("id") or "").strip()
    if task_record_id and record_id:
        return task_record_id == record_id

    task_area_id = str(task.get("area_id") or "").strip()
    record_area_id = str(record.get("area_id") or record.get("areaId") or "").strip()
    if task_area_id and record_area_id and task_area_id != record_area_id:
        return False

    task_title = _normalize_compare_text(task.get("title"))
    record_title = _normalize_compare_text(record.get("title"))
    if task_title and record_title and task_title != record_title:
        return False

    task_room = _normalize_compare_text(task.get("room_name"))
    if task_room:
        room_candidates = [
            _normalize_compare_text(record.get("nameMerge")),
            _normalize_compare_text(record.get("name")),
            _normalize_compare_text(record.get("area")),
            _normalize_compare_text(record.get("areaName")),
        ]
        room_candidates = [item for item in room_candidates if item]
        if room_candidates and not any(task_room in item or item in task_room for item in room_candidates):
            return False

    task_time = _normalize_compare_text(_task_time_text(task))
    record_time = _normalize_compare_text(_record_time_text(record))
    if task_time and record_time:
        for segment in (
            str(task.get("start_date") or ""),
            str(task.get("start_time") or ""),
            str(task.get("end_date") or ""),
            str(task.get("end_time") or ""),
        ):
            normalized = _normalize_compare_text(segment)
            if normalized and normalized not in record_time:
                return False

    return True


def _resolve_seminar_record_for_task(bot: Any, task: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    record_id_text = str(task.get("record_id") or "").strip()
    record_type_text = str(task.get("record_type") or "1").strip() or "1"
    if record_type_text not in {"1", "2"}:
        record_type_text = "1"

    for page in range(1, 4):
        result = bot.list_seminar_records(
            record_type=record_type_text,
            page=page,
            limit=20,
            mode="books",
        )
        if not result.get("success"):
            return None, str(result.get("msg") or "查询研讨室预约记录失败")
        records = result.get("records") or []
        for record in records:
            if not isinstance(record, dict):
                continue
            if record_id_text and str(record.get("id") or "").strip() == record_id_text:
                return record, ""
            if _seminar_task_matches_record(task, record):
                return record, ""
        if len(records) < 20:
            break

    return None, "未找到对应的研讨室预约记录"


def _find_seminar_record_by_id(bot: Any, record_id: str) -> dict[str, Any] | None:
    record_id_text = str(record_id or "").strip()
    if not record_id_text:
        return None

    for record_type_text in ("1", "2"):
        for page in range(1, 4):
            result = bot.list_seminar_records(
                record_type=record_type_text,
                page=page,
                limit=20,
                mode="books",
            )
            if not result.get("success"):
                break
            records = result.get("records") or []
            for record in records:
                if str(record.get("id") or "").strip() == record_id_text:
                    return record
            if len(records) < 20:
                break
    return None


def _build_seminar_signin_task(result: dict[str, Any]) -> dict[str, Any] | None:
    if not result.get("success"):
        return None

    payload = result.get("payload_summary") or {}
    area_id = str(payload.get("area_id") or "").strip()
    times = payload.get("time") or []
    if not area_id or not isinstance(times, list) or not times:
        return None

    first_time = times[0] if isinstance(times[0], dict) else {}
    last_time = times[-1] if isinstance(times[-1], dict) else first_time
    start_date = str(payload.get("start_date") or "").strip()
    end_date = str(payload.get("end_date") or start_date).strip()
    start_time = str(first_time.get("start_time") or "").strip()
    end_time = str(last_time.get("end_time") or "").strip()
    if not start_date or not start_time:
        return None

    start_dt = _parse_dt_text(f"{start_date} {start_time}")
    if start_dt is None:
        return None

    end_dt = _parse_dt_text(f"{end_date or start_date} {end_time}") if end_time else None
    sign_at = start_dt - timedelta(minutes=10)
    response_data = result.get("data") or {}
    now_text = _format_dt_text(_now_dt())
    record_type_text = str(result.get("record_type") or "").strip() or str(
        (result.get("detail_summary") or {}).get("type_id") or "1"
    ).strip()
    if record_type_text not in {"1", "2"}:
        record_type_text = "1"

    return {
        "task_id": uuid.uuid4().hex,
        "record_id": _extract_seminar_record_id(response_data),
        "record_type": record_type_text,
        "area_id": area_id,
        "room_name": str(result.get("room_name") or (result.get("detail_summary") or {}).get("room_name") or "").strip(),
        "title": str(response_data.get("title") or result.get("title") or "").strip()
        if isinstance(response_data, dict)
        else "",
        "start_date": start_date,
        "start_time": start_time,
        "end_date": end_date,
        "end_time": end_time,
        "start_at": _format_dt_text(start_dt),
        "end_at": _format_dt_text(end_dt),
        "sign_at": _format_dt_text(sign_at),
        "status": "pending",
        "attempts": 0,
        "last_msg": "",
        "created_at": now_text,
        "updated_at": now_text,
    }


def _upsert_seminar_signin_task(task: dict[str, Any]) -> dict[str, Any]:
    with _SEMINAR_SIGNIN_TASK_LOCK:
        tasks = _load_seminar_signin_tasks()
        for item in tasks:
            if not isinstance(item, dict):
                continue
            same_record = task.get("record_id") and str(item.get("record_id") or "") == str(task.get("record_id") or "")
            same_slot = (
                str(item.get("area_id") or "") == str(task.get("area_id") or "")
                and str(item.get("start_date") or "") == str(task.get("start_date") or "")
                and str(item.get("start_time") or "") == str(task.get("start_time") or "")
                and str(item.get("end_date") or "") == str(task.get("end_date") or "")
                and str(item.get("end_time") or "") == str(task.get("end_time") or "")
            )
            if not same_record and not same_slot:
                continue
            item.update({k: v for k, v in task.items() if v not in ("", None)})
            item["status"] = "pending"
            item["updated_at"] = _format_dt_text(_now_dt())
            _save_seminar_signin_tasks(tasks)
            return item
        tasks.append(task)
        _save_seminar_signin_tasks(tasks)
    return task


def _update_seminar_signin_tasks_for_record(record_id: str, **fields: Any) -> int:
    record_id_text = str(record_id or "").strip()
    if not record_id_text:
        return 0

    updated = 0
    with _SEMINAR_SIGNIN_TASK_LOCK:
        tasks = _load_seminar_signin_tasks()
        now_text = _format_dt_text(_now_dt())
        for task in tasks:
            if str(task.get("record_id") or "").strip() != record_id_text:
                continue
            task.update(fields)
            task["updated_at"] = now_text
            updated += 1
        if updated:
            _save_seminar_signin_tasks(tasks)
    return updated


def _update_seminar_signin_tasks_for_record_snapshot(record: dict[str, Any], **fields: Any) -> int:
    if not isinstance(record, dict):
        return 0

    updated = 0
    with _SEMINAR_SIGNIN_TASK_LOCK:
        tasks = _load_seminar_signin_tasks()
        now_text = _format_dt_text(_now_dt())
        for task in tasks:
            if not _seminar_task_matches_record(task, record):
                continue
            task.update(fields)
            if str(task.get("record_id") or "").strip() == "":
                task["record_id"] = str(record.get("id") or "").strip()
            task["updated_at"] = now_text
            updated += 1
        if updated:
            _save_seminar_signin_tasks(tasks)
    return updated


def _process_seminar_signin_tasks(
    *,
    due_only: bool = True,
    task_id: str = "",
    trigger: str = "manual",
) -> dict[str, Any]:
    if HenuCampusBot is None:
        return {"success": False, "msg": "图书馆模块不可用", "tasks": []}

    with _SEMINAR_SIGNIN_TASK_LOCK:
        tasks = _load_seminar_signin_tasks()

    if not tasks:
        return {
            "success": True,
            "msg": "当前没有待处理的研讨室签到任务",
            "trigger": trigger,
            "tasks": [],
            "processed_count": 0,
            "success_count": 0,
            "failed_count": 0,
        }

    target_task_id = str(task_id or "").strip()
    now = _now_dt()
    due_tasks: list[dict[str, Any]] = []
    for task in tasks:
        status_text = str(task.get("status") or "").strip() or "pending"
        if status_text in {"success", "cancelled", "expired"}:
            continue
        if target_task_id and str(task.get("task_id") or "").strip() != target_task_id:
            continue
        sign_at = _parse_dt_text(task.get("sign_at"))
        start_at = _parse_dt_text(task.get("start_at"))
        if due_only and sign_at and sign_at > now:
            continue
        if start_at and now > start_at + timedelta(minutes=30):
            task["status"] = "expired"
            task["last_msg"] = "已超过签到可处理时间"
            task["updated_at"] = _format_dt_text(now)
            continue
        due_tasks.append(task)

    with _SEMINAR_SIGNIN_TASK_LOCK:
        _save_seminar_signin_tasks(tasks)

    if not due_tasks:
        return {
            "success": True,
            "msg": "当前没有到点的研讨室签到任务",
            "trigger": trigger,
            "tasks": [_seminar_task_summary(task) for task in tasks],
            "processed_count": 0,
            "success_count": 0,
            "failed_count": 0,
        }

    profile = _effective_profile()
    sid, pwd = str(profile.get("student_id", "")), str(profile.get("password", ""))
    if not sid or not pwd:
        return {"success": False, "msg": "缺少账号", "tasks": [_seminar_task_summary(task) for task in tasks]}

    bot = _build_library_bot(sid, pwd)
    if not bot:
        return _library_login_failed({"tasks": [_seminar_task_summary(task) for task in tasks]})

    success_count = 0
    failed_count = 0
    processed_count = 0
    now_text = _format_dt_text(now)
    for task in due_tasks:
        processed_count += 1
        task["attempts"] = int(task.get("attempts") or 0) + 1
        task["updated_at"] = now_text

        record, record_msg = _resolve_seminar_record_for_task(bot, task)
        if record is None:
            task["last_msg"] = record_msg or "未找到对应预约记录"
            failed_count += 1
            continue

        record_id_text = str(record.get("id") or "").strip()
        if record_id_text:
            task["record_id"] = record_id_text

        sign_result = bot.sign_in_seminar_record(record_id_text)
        task["last_result"] = sign_result
        task["last_msg"] = str(sign_result.get("msg") or "")
        if sign_result.get("success"):
            task["status"] = "success"
            task["record"] = _seminar_record_summary(record)
            success_count += 1
            continue

        message_text = str(sign_result.get("msg") or "")
        if any(keyword in message_text for keyword in ("已签到", "签到成功")):
            task["status"] = "success"
            task["record"] = _seminar_record_summary(record)
            success_count += 1
        elif any(keyword in message_text for keyword in ("已取消", "不存在", "已违约", "无此记录")):
            task["status"] = "cancelled"
            failed_count += 1
        else:
            failed_count += 1

    _save_library_cookies(bot.get_cookies())
    with _SEMINAR_SIGNIN_TASK_LOCK:
        _save_seminar_signin_tasks(tasks)

    return {
        "success": True,
        "msg": "研讨室自动签到扫描完成",
        "trigger": trigger,
        "tasks": [_seminar_task_summary(task) for task in tasks],
        "processed_count": processed_count,
        "success_count": success_count,
        "failed_count": failed_count,
        "now": now_text,
    }


def _ensure_seminar_auto_signin_worker() -> None:
    global _SEMINAR_AUTO_SIGNIN_THREAD
    with _SEMINAR_AUTO_SIGNIN_THREAD_LOCK:
        if _SEMINAR_AUTO_SIGNIN_THREAD and _SEMINAR_AUTO_SIGNIN_THREAD.is_alive():
            return

        def _worker() -> None:
            while True:
                try:
                    _process_seminar_signin_tasks(due_only=True, trigger="background")
                except Exception:
                    pass
                time.sleep(SEMINAR_AUTO_SIGNIN_INTERVAL_SECONDS)

        _SEMINAR_AUTO_SIGNIN_THREAD = threading.Thread(
            target=_worker,
            name="seminar-auto-signin",
            daemon=True,
        )
        _SEMINAR_AUTO_SIGNIN_THREAD.start()


def _resolve_seminar_members(
    student_id: str,
    group_name: str = "",
    member_ids: str = "",
) -> tuple[list[str], str]:
    sid = str(student_id or "").strip()
    manual = _parse_csv_text(member_ids)
    if manual:
        unique = []
        seen: set[str] = set()
        for item in manual:
            if item == sid or item in seen:
                continue
            seen.add(item)
            unique.append(item)
        return unique, ""

    group_text = str(group_name or "").strip()
    if not group_text:
        return [], ""

    groups = _load_seminar_groups()
    raw = groups.get(group_text)
    if not isinstance(raw, dict):
        return [], f"未找到 group: {group_text}"

    unique = []
    seen: set[str] = set()
    for item in raw.get("member_ids") or []:
        text = str(item or "").strip()
        if not text or text == sid or text in seen:
            continue
        seen.add(text)
        unique.append(text)
    return unique, ""


def _resolve_option_ids_by_names(
    options: list[dict[str, Any]],
    names: list[str],
    *,
    id_key: str = "id",
) -> list[str]:
    if not names:
        return []
    results: list[str] = []
    target_names = [str(name or "").strip() for name in names if str(name or "").strip()]
    for option in options or []:
        option_names = {
            str(option.get("name") or "").strip(),
            str(option.get("enname") or "").strip(),
            str(option.get("label") or "").strip(),
            str(option.get("title") or "").strip(),
        }
        option_names = {item for item in option_names if item}
        if not option_names:
            continue
        for raw_name in target_names:
            if raw_name in option_names or any(raw_name in item or item in raw_name for item in option_names):
                option_id = str(option.get(id_key) or "").strip()
                if option_id and option_id not in results:
                    results.append(option_id)
                break
    return results


def _resolve_floor_ids(
    storey_options: list[dict[str, Any]],
    floor_names: list[str],
    library_ids: list[str],
    floor_ids: list[str],
) -> list[str]:
    results: list[str] = []
    if floor_ids:
        results.extend([item for item in floor_ids if item])

    if not floor_names:
        return results

    target_names = [str(name or "").strip() for name in floor_names if str(name or "").strip()]
    for storey in storey_options or []:
        storey_name = str(storey.get("name") or storey.get("enname") or "").strip()
        if not storey_name:
            continue
        if not any(
            floor_name == storey_name or floor_name in storey_name or storey_name in floor_name
            for floor_name in target_names
        ):
            continue
        for row in storey.get("list") or []:
            floor_id = str(row.get("id") or "").strip()
            parent_id = str(row.get("parentId") or "").strip()
            if not floor_id:
                continue
            if library_ids and parent_id and parent_id not in library_ids:
                continue
            if floor_id not in results:
                results.append(floor_id)
    return results


def _target_library_date(target_date: str | None = None) -> str:
    if not target_date:
        return (_now_dt().date() + timedelta(days=1)).strftime("%Y-%m-%d")
    try:
        datetime.strptime(str(target_date), "%Y-%m-%d")
    except ValueError:
        raise ValueError("target_date 格式必须为 YYYY-MM-DD")
    return str(target_date)


def _load_library_cookies() -> dict[str, Any]:
    return load_json(LIBRARY_COOKIE_FILE)


def _save_library_cookies(cookies: dict[str, Any]) -> None:
    save_json(LIBRARY_COOKIE_FILE, cookies)


def _load_cas_cookies() -> dict[str, Any]:
    """Load this user's IDS CAS cookie jar for cross-service login reuse."""
    return _merge_cas_cookies(
        load_json(CAS_COOKIE_FILE),
        load_json(COOKIE_FILE),
    )


def _save_cas_cookies(cookies: dict[str, Any]) -> None:
    """Save this user's IDS CAS cookie jar for reuse across campus services."""
    clean = _extract_ids_cas_cookies(cookies)
    if clean:
        save_json(CAS_COOKIE_FILE, clean)


def _load_xk_cookies() -> dict[str, Any]:
    return {
        **load_json(COOKIE_FILE),
        **_load_cas_cookies(),
    }


def _save_xk_cookies(cookies: dict[str, Any]) -> None:
    save_json(COOKIE_FILE, cookies)
    _save_cas_cookies(cookies)


def _set_library_login_error(message: str) -> None:
    global _LAST_LIBRARY_LOGIN_ERROR
    _LAST_LIBRARY_LOGIN_ERROR = str(message or "").strip()


def _library_login_error_message(default: str = "图书馆登录失败") -> str:
    text = str(_LAST_LIBRARY_LOGIN_ERROR or "").strip()
    return text or default


def _library_login_failed(extra: dict[str, Any] | None = None, default: str = "图书馆登录失败") -> dict[str, Any]:
    result = {"success": False, "msg": _library_login_error_message(default)}
    if extra:
        result.update(extra)
    return result


def _build_library_bot(student_id: str, password: str):
    if HenuCampusBot is None:
        raise RuntimeError(f"校园核心模块不可用: {CAMPUS_CORE_EXPECTED_FILE}")

    _set_library_login_error("")
    stored = _load_library_cookies() or {}
    cas_cookies = _load_cas_cookies()
    bot = HenuCampusBot(student_id, password, stored or None, cas_cookies or None)  # type: ignore

    if bot.login():
        _save_library_cookies(bot.get_cookies())
        _save_cas_cookies(bot.get_cas_cookies())
        _set_library_login_error("")
        return bot

    first_error = str(getattr(bot, "get_last_error", lambda: "")() or "").strip()
    if stored:
        fresh_bot = HenuCampusBot(student_id, password, None, cas_cookies or None)  # type: ignore
        if fresh_bot.login():
            _save_library_cookies(fresh_bot.get_cookies())
            _save_cas_cookies(fresh_bot.get_cas_cookies())
            _set_library_login_error("")
            return fresh_bot
        fresh_error = str(getattr(fresh_bot, "get_last_error", lambda: "")() or "").strip()
        _set_library_login_error(fresh_error or first_error)
        return None

    _set_library_login_error(first_error)
    return None


def _latest_grid_file() -> Path | None:
    files = sorted(Path(OUTPUT_DIR).glob("schedule_grid_*.html"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


# ==================== 河宝社区 Helpers ====================

_LAST_HEBAO_LOGIN_ERROR = ""


def _set_hebao_login_error(message: str) -> None:
    global _LAST_HEBAO_LOGIN_ERROR
    _LAST_HEBAO_LOGIN_ERROR = str(message or "").strip()


def _hebao_login_error_message(default: str = "河宝社区登录失败") -> str:
    text = str(_LAST_HEBAO_LOGIN_ERROR or "").strip()
    return text or default


def _hebao_login_failed(extra: dict[str, Any] | None = None, default: str = "河宝社区登录失败") -> dict[str, Any]:
    result = {"success": False, "msg": _hebao_login_error_message(default)}
    if extra:
        result.update(extra)
    return result


def _load_hebao_token() -> dict[str, Any]:
    return load_json(HEBAO_TOKEN_FILE)


def _save_hebao_token(token: str) -> None:
    save_json(HEBAO_TOKEN_FILE, {"token": token})


def _build_hebao_bot(student_id: str, password: str):
    if HenuCampusBot is None:
        raise RuntimeError(f"校园核心模块不可用: {CAMPUS_CORE_EXPECTED_FILE}")

    _set_hebao_login_error("")
    stored = _load_hebao_token()
    stored_token = stored.get("token", "") if stored else ""
    cas_cookies = _load_cas_cookies()
    bot = HenuCampusBot(student_id, password, None, cas_cookies or None, stored_token or None)  # type: ignore
    if bot.hebao_login():
        token = bot.get_hebao_token()
        if token:
            _save_hebao_token(token)
        _save_cas_cookies(bot.get_hebao_cas_cookies())
        _set_hebao_login_error("")
        return bot

    first_error = str(getattr(bot, "get_hebao_last_error", lambda: "")() or "").strip()
    if stored_token:
        fresh_bot = HenuCampusBot(student_id, password, None, cas_cookies or None, None)  # type: ignore
        if fresh_bot.hebao_login():
            token = fresh_bot.get_hebao_token()
            if token:
                _save_hebao_token(token)
            _save_cas_cookies(fresh_bot.get_hebao_cas_cookies())
            _set_hebao_login_error("")
            return fresh_bot
        fresh_error = str(getattr(fresh_bot, "get_hebao_last_error", lambda: "")() or "").strip()
        _set_hebao_login_error(fresh_error or first_error)
        return None

    _set_hebao_login_error(first_error)
    return None


def get_server_time(timezone: str = "Asia/Shanghai") -> dict[str, Any]:
    """获取服务器当前时间（用于判断当前正在上的课）。"""
    now = _now_dt(timezone)
    return {
        "success": True,
        "timezone": timezone,
        "now_iso": now.isoformat(timespec="seconds"),
        "now_text": now.strftime("%Y-%m-%d %H:%M:%S"),
        "weekday_index": now.weekday(),
        "weekday_cn": WEEKDAY_CN[now.weekday()],
    }


def _resolve_schedule_target_date(
    target_date: str = "",
    timezone: str = "Asia/Shanghai",
) -> tuple[datetime.date, str]:
    text = str(target_date or "").strip()
    if not text:
        value = _now_dt(timezone).date()
        return value, value.strftime("%Y-%m-%d")
    try:
        value = datetime.strptime(text, "%Y-%m-%d").date()
    except Exception as exc:
        raise ValueError("target_date 格式必须为 YYYY-MM-DD") from exc
    return value, text


def get_period_time_config() -> dict[str, Any]:
    """读取节次时间映射配置（第几节 -> 开始/结束时间）。"""
    period_times = _load_period_times()
    return {
        "success": True,
        "config_file": str(PERIOD_TIME_FILE),
        "calibration_state_file": str(PERIOD_CALIBRATION_STATE_FILE),
        "calibration_state": _load_calibration_state(),
        "xiqueer_request": _xiqueer_config_summary(_load_xiqueer_request_config()),
        "period_times": period_times,
    }


def get_xiqueer_calibration_request() -> dict[str, Any]:
    """查看 xiqueer 自动校准请求配置（不回传完整 data）。"""
    return {"success": True, **_xiqueer_config_summary(_load_xiqueer_request_config())}


def set_xiqueer_calibration_request(
    data: str,
    cookie: str,
    user_agent: str = "KingoPalm/2.6.449 (iPhone; iOS 26.3; Scale/3.00)",
    url: str = "http://api.xiqueer.com/manager/wap/wapController.jsp",
) -> dict[str, Any]:
    """
    设置 xiqueer 节次时间请求参数（使用抓包得到的 data/cookie）。
    """
    data_text = str(data or "")
    cookie_text = str(cookie or "")
    ua = str(user_agent or "").strip()
    u = str(url or "").strip()
    if not data_text:
        return {"success": False, "msg": "data 不能为空"}
    if not cookie_text:
        return {"success": False, "msg": "cookie 不能为空"}
    if not u:
        return {"success": False, "msg": "url 不能为空"}

    headers: dict[str, str] = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "*/*",
        "Accept-Language": "zh-Hans-CN;q=1",
        "Cookie": cookie_text,
    }
    if ua:
        headers["User-Agent"] = ua

    config = {"url": u, "headers": headers, "data": data_text, "timeout": 25}
    _save_xiqueer_request_config(config)
    return {
        "success": True,
        "msg": "xiqueer 请求配置已保存",
        **_xiqueer_config_summary(config),
    }


def test_xiqueer_period_time_request() -> dict[str, Any]:
    """测试 xiqueer 请求并解析节次时间。"""
    result = _fetch_xiqueer_period_times()
    return {
        "success": bool(result.get("success")),
        "msg": str(result.get("msg", "")),
        "url": str(result.get("url", "")),
        "status_code": result.get("status_code"),
        "matched_period_count": int(result.get("matched_period_count", 0) or 0),
        "period_times": result.get("period_times", {}),
        "request_summary": _xiqueer_config_summary(_load_xiqueer_request_config()),
    }


def auto_calibrate_period_time(force: bool = False) -> dict[str, Any]:
    """尝试从教务作息页面自动校准节次时间。"""
    result = _auto_calibrate_period_time_impl(force=force)
    result["config_file"] = str(PERIOD_TIME_FILE)
    result["calibration_state_file"] = str(PERIOD_CALIBRATION_STATE_FILE)
    return result


def set_period_time(period: int, start_time: str, end_time: str) -> dict[str, Any]:
    """设置某一节次的开始/结束时间。示例: set_period_time(3, '10:00', '10:45')"""
    if period <= 0:
        return {"success": False, "msg": "period 必须大于 0"}
    start = str(start_time or "").strip()
    end = str(end_time or "").strip()
    if not _is_hhmm(start) or not _is_hhmm(end):
        return {"success": False, "msg": "start_time/end_time 格式必须为 HH:MM"}
    if _to_minutes(start) >= _to_minutes(end):
        return {"success": False, "msg": "开始时间必须早于结束时间"}

    period_times = _load_period_times()
    period_times[str(period)] = {"start": start, "end": end}
    _save_period_times(period_times)
    return {
        "success": True,
        "msg": "已更新节次时间",
        "period": period,
        "start": start,
        "end": end,
        "config_file": str(PERIOD_TIME_FILE),
    }


def show_account() -> dict[str, Any]:
    """显示当前课表账号配置（密码不明文返回）。"""
    return {"success": True, "account": _mask_profile(_effective_profile())}


def save_account(
    student_id: str,
    password: str,
    verify_login: bool = True,
    home_url: str = DEFAULT_HOME_URL,
    library_location: str = "",
    library_seat_no: str = "",
) -> dict[str, Any]:
    """保存课表账号；可选立即验证统一认证登录。"""
    sid = str(student_id or "").strip()
    pwd = str(password or "")
    if not sid or not pwd:
        return {"success": False, "msg": "student_id/password 不能为空"}

    context: dict[str, Any] = {}
    if verify_login:
        client = HenuXkClient(sid, pwd, saved_cookies=_load_xk_cookies() or None)
        if not client.login():
            return {"success": False, "msg": "登录失败，账号或密码可能错误"}
        _save_xk_cookies(client.get_cookies())
        context = client.fetch_user_context()

    fields: dict[str, Any] = {"student_id": sid, "password": pwd}
    if str(library_location or "").strip():
        fields["library_location"] = str(library_location).strip()
    if str(library_seat_no or "").strip():
        fields["library_seat_no"] = str(library_seat_no).strip()
    _save_profile_fields(fields)
    return {
        "success": True,
        "msg": "账号已保存",
        "account": _mask_profile(_effective_profile()),
        "context": {
            "login_id": context.get("login_id", ""),
            "user_type": context.get("user_type", ""),
            "current_xn": context.get("current_xn", ""),
            "current_xq": context.get("current_xq", ""),
        }
        if context
        else {},
    }


def check_login(
    student_id: str = "",
    password: str = "",
    use_saved_account: bool = True,
) -> dict[str, Any]:
    """检查账号当前是否可登录课表系统。"""
    sid, pwd = _resolve_account(student_id, password, use_saved_account=use_saved_account)
    if not sid:
        return {"success": False, "msg": "缺少学号，请先 save_account 或传入 student_id"}
    if not pwd:
        return {"success": False, "msg": "缺少密码，请先 save_account 或传入 password"}

    client = HenuXkClient(sid, pwd, saved_cookies=_load_xk_cookies() or None)
    ok = client.login()
    context = client.fetch_user_context()
    if ok:
        _save_xk_cookies(client.get_cookies())

    return {
        "success": ok,
        "msg": "登录成功" if ok else "登录失败",
        "login_id": context.get("login_id", ""),
        "user_type": context.get("user_type", ""),
        "current_xn": context.get("current_xn", ""),
        "current_xq": context.get("current_xq", ""),
        "school_code": context.get("school_code", ""),
    }


def fetch_schedule(
    student_id: str = "",
    password: str = "",
    xn: str | None = None,
    xq: str | None = None,
    schedule_url: str | None = None,
    home_url: str = DEFAULT_HOME_URL,
    use_saved_account: bool = True,
    save_account_after_success: bool = False,
) -> dict[str, Any]:
    """登录并抓取课表，同时自动输出 clean json/md。"""
    sid, pwd = _resolve_account(student_id, password, use_saved_account=use_saved_account)
    if not sid:
        return {"success": False, "msg": "缺少学号，请先 save_account 或传入 student_id"}
    if not pwd:
        return {"success": False, "msg": "缺少密码，请先 save_account 或传入 password"}

    result = run_fetch(
        student_id=sid,
        password=pwd,
        home_url=home_url,
        schedule_url=schedule_url,
        xn=xn,
        xq=xq,
    )

    if result.get("success") and save_account_after_success:
        _save_profile_fields({"student_id": sid, "password": pwd})

    # 兜底：若 clean 文件未生成，尝试从最新 schedule_grid 手工重建
    if result.get("success") and not result.get("clean_schedule_file_json"):
        grid_file = (result.get("generated_files") or {}).get("schedule_grid_file")
        if not grid_file:
            latest = _latest_grid_file()
            grid_file = str(latest) if latest else ""
        if grid_file:
            try:
                cleaned = clean_schedule_grid_file(grid_file, OUTPUT_DIR)
                result["clean_schedule_file_json"] = cleaned["files"].get("clean_schedule_json", "")
                result["clean_schedule_file_md"] = cleaned["files"].get("clean_schedule_md", "")
                result.setdefault("generated_files", {}).update(cleaned["files"])
            except Exception as exc:
                result["clean_schedule_error"] = str(exc)

    return result


def _latest_schedule_impl() -> dict[str, Any]:
    """
    【必须调用】获取完整课表 - 返回一周的所有课程安排

    功能：返回结构化的课表数据，按星期组织

    重要：不要编造课表，必须调用此工具获取真实的课程安排。
    """
    try:
        data = load_latest_clean_schedule(OUTPUT_DIR)
        return {"success": True, "schedule": data.get("schedule", {})}
    except Exception as e:
        return {"success": False, "msg": f"获取课表失败: {e}"}


def _day_schedule_impl(
    target_date: str = "",
    timezone: str = "Asia/Shanghai",
) -> dict[str, Any]:
    """
    获取某一天的课表。

    注意：当前视图按星期几从完整周课表中提取，不按教学周做额外过滤。
    """
    try:
        target_day, date_text = _resolve_schedule_target_date(target_date=target_date, timezone=timezone)
    except ValueError as exc:
        return {"success": False, "msg": str(exc)}

    try:
        data = load_latest_clean_schedule(OUTPUT_DIR)
    except Exception as exc:
        return {"success": False, "msg": f"获取课表失败: {exc}"}

    weekday_cn = WEEKDAY_CN[target_day.weekday()]
    schedule = data.get("schedule", {}) or {}
    day_courses = list(schedule.get(weekday_cn, []) or [])
    today = _now_dt(timezone).date()
    day_offset = (target_day - today).days

    if day_courses:
        msg = f"{date_text} {weekday_cn} 共 {len(day_courses)} 门课（未按教学周过滤）"
    else:
        msg = f"{date_text} {weekday_cn} 课表为空（按星期提取，未按教学周过滤）"

    return {
        "success": True,
        "date": date_text,
        "weekday": weekday_cn,
        "schedule": day_courses,
        "schedule_count": len(day_courses),
        "day_offset": day_offset,
        "is_today": day_offset == 0,
        "is_tomorrow": day_offset == 1,
        "week_filter_applied": False,
        "msg": msg,
    }


def _week_schedule_compat_impl() -> dict[str, Any]:
    """
    兼容旧客户端的 week 视图。

    由于当前周次无法从教务系统稳定获取，此处返回未按教学周过滤的完整周课表。
    """
    result = _latest_schedule_impl()
    if not result.get("success"):
        return result
    result["week_filter_applied"] = False
    result["msg"] = "week 视图已降级为完整周课表：当前周次无法稳定获取，结果未按教学周过滤。"
    return result


def _current_course_impl(
    timezone: str = "Asia/Shanghai",
    auto_calibrate: bool = True,
) -> dict[str, Any]:
    """
    【必须调用】查询当前课程状态 - 获取正在上的课和下一节课

    功能：基于当前时间和课表，返回：
    - 当前正在上的课程（如果有）
    - 下一节课的信息

    重要：不要猜测或编造课程信息，必须调用此工具获取准确的实时数据。
    """
    return get_current_course_status(
        timezone=timezone,
        auto_calibrate=auto_calibrate,
    )


def rebuild_clean_schedule_from_latest_grid() -> dict[str, Any]:
    """用最新 schedule_grid_*.html 重新生成 clean json/md。"""
    grid_file = _latest_grid_file()
    if not grid_file:
        return {"success": False, "msg": f"未找到 {OUTPUT_DIR}/schedule_grid_*.html"}
    try:
        cleaned = clean_schedule_grid_file(grid_file, OUTPUT_DIR)
    except Exception as exc:
        return {"success": False, "msg": f"重建失败: {exc}"}
    return {
        "success": True,
        "msg": "重建完成",
        "grid_file": str(grid_file),
        "files": cleaned.get("files", {}),
    }


def list_output_files(limit: int = 20) -> list[dict[str, Any]]:
    """列出 output 目录下的结果文件。"""
    output_dir = Path(OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(output_dir.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)[: max(1, limit)]
    return [
        {
            "name": p.name,
            "path": str(p),
            "size": p.stat().st_size,
            "mtime": int(p.stat().st_mtime),
        }
        for p in files
        if p.is_file()
    ]


def _library_locations_impl(target_date: str = "") -> dict[str, Any]:
    """
    【必须调用】查看图书馆区域列表 - 获取所有可预约的图书馆区域

    功能：优先返回指定日期的实时可预约区域名称和ID，账号不可用时返回内置兜底列表

    重要：不要编造区域信息，必须调用此工具获取准确的区域列表。
    """
    if HenuCampusBot is None:
        return {"success": False, "msg": f"校园核心模块不可用: {CAMPUS_CORE_EXPECTED_FILE}", "locations": []}

    target_day = str(target_date or "").strip()
    if not target_day:
        target_day = (_now_dt().date() + timedelta(days=1)).strftime("%Y-%m-%d")

    profile = _effective_profile()
    sid, pwd = str(profile.get("student_id", "")), str(profile.get("password", ""))
    if sid and pwd:
        bot = _build_library_bot(sid, pwd)
        if not bot:
            result = _library_login_failed(
                {
                    "date": target_day,
                    "locations": HenuCampusBot.static_locations(),
                    "source": "static_fallback",
                    "is_live": False,
                }
            )
            return result
        result = bot.list_locations(target_day)
        _save_library_cookies(bot.get_cookies())
        if hasattr(bot, "get_cas_cookies"):
            _save_cas_cookies(bot.get_cas_cookies())
        # 增量 sync 到 resource_registry
        _enrich_library_locations(result.get("locations", []))
        return result

    locations = HenuCampusBot.static_locations()
    _enrich_library_locations(locations)
    return {
        "success": True,
        "msg": "未绑定账号，返回内置兜底区域；绑定后可获取实时可预约区域",
        "date": target_day,
        "locations": locations,
        "source": "static_fallback",
        "is_live": False,
    }


def _library_seats_impl(
    location: str = "",
    area_id: str = "",
    target_date: str = "",
    preferred_time: str = "08:00",
    preferred_end_time: str = "",
) -> dict[str, Any]:
    """
    查询指定图书馆区域在指定日期/时间段的当前可用座位。
    """
    if HenuCampusBot is None:
        return {"success": False, "msg": "图书馆模块不可用", "seats": []}

    profile = _effective_profile()
    sid, pwd = str(profile.get("student_id", "")), str(profile.get("password", ""))
    if not sid or not pwd:
        return {"success": False, "msg": "缺少账号", "seats": []}

    target_location = str(location or profile.get("library_location", "")).strip()
    target_area_id = str(area_id or "").strip()
    if not target_area_id and not target_location:
        return {"success": False, "msg": "请提供 location 或 area_id，或在 setup_account 中设置默认图书馆区域", "seats": []}

    bot = _build_library_bot(sid, pwd)
    if not bot:
        return _library_login_failed({"seats": []})

    result = bot.list_available_seats(
        location_name=target_location,
        area_id=target_area_id,
        target_date=str(target_date or ""),
        preferred_time=str(preferred_time or "08:00"),
        preferred_end_time=str(preferred_end_time or ""),
    )
    _save_library_cookies(bot.get_cookies())
    if hasattr(bot, "get_cas_cookies"):
        _save_cas_cookies(bot.get_cas_cookies())
    # 增量 sync 到 resource_registry
    _enrich_library_seats(result.get("seats", []), target_area_id)
    return result


def _library_reserve_impl(
    location: str = "",
    seat_no: str = "",
    target_date: str = "",
    preferred_time: str = "08:00",
    preferred_end_time: str = "",
    retry_until: str = "",
    retry_interval_seconds: int = 2,
    max_attempts: int = 1,
) -> dict[str, Any]:
    """
    【必须调用】预约图书馆座位 - 执行真实的座位预约操作

    功能：向图书馆系统提交座位预约请求

    重要：不要假装预约成功，必须调用此工具执行真实的预约操作。
    预约结果会返回成功或失败的详细信息。
    """
    if HenuCampusBot is None:
        return {"success": False, "msg": "图书馆模块不可用"}
    
    profile = _effective_profile()
    sid, pwd = str(profile.get("student_id", "")), str(profile.get("password", ""))
    if not sid or not pwd:
        return {"success": False, "msg": "缺少账号"}
    
    # 使用默认值
    target_location = str(location or profile.get("library_location", "")).strip()
    target_seat = str(seat_no or profile.get("library_seat_no", "")).strip()
    
    if not target_location or not target_seat:
        return {"success": False, "msg": "请提供 location/seat_no 或在 setup_account 中设置默认值"}
    
    # 日期默认为明天
    if not target_date:
        target_date = (_now_dt().date() + timedelta(days=1)).strftime("%Y-%m-%d")
    
    bot = _build_library_bot(sid, pwd)
    if not bot:
        return _library_login_failed()

    result = bot.reserve(
        target_location,
        target_seat,
        target_date,
        preferred_time=str(preferred_time or "08:00"),
        preferred_end_time=str(preferred_end_time or ""),
        retry_until=str(retry_until or ""),
        retry_interval_seconds=retry_interval_seconds,
        max_attempts=max_attempts,
    )
    _save_library_cookies(bot.get_cookies())
    if hasattr(bot, "get_cas_cookies"):
        _save_cas_cookies(bot.get_cas_cookies())
    
    response = dict(result) if isinstance(result, dict) else {"success": False, "msg": "图书馆预约返回异常"}
    response.setdefault("date", target_date)
    return response


def _library_records_impl(record_type: str = "1", page: int = 1, limit: int = 20) -> dict[str, Any]:
    """
    【必须调用】查询图书馆预约记录 - 获取真实的预约历史

    功能：从图书馆系统查询预约记录

    重要：不要编造预约记录，必须调用此工具获取真实数据。
    """
    if HenuCampusBot is None:
        return {"success": False, "msg": "图书馆模块不可用", "records": []}
    
    profile = _effective_profile()
    sid, pwd = str(profile.get("student_id", "")), str(profile.get("password", ""))
    if not sid or not pwd:
        return {"success": False, "msg": "缺少账号", "records": []}
    
    bot = _build_library_bot(sid, pwd)
    if not bot:
        return _library_login_failed({"records": []})

    _save_library_cookies(bot.get_cookies())
    return bot.list_seat_records(record_type=record_type, page=page, limit=limit)


def _library_current_impl() -> dict[str, Any]:
    """
    查询图书馆当前预约，用于判断是否存在可签到记录。
    """
    if HenuCampusBot is None:
        return {"success": False, "msg": "图书馆模块不可用", "appointments": []}

    profile = _effective_profile()
    sid, pwd = str(profile.get("student_id", "")), str(profile.get("password", ""))
    if not sid or not pwd:
        return {"success": False, "msg": "缺少账号", "appointments": []}

    bot = _build_library_bot(sid, pwd)
    if not bot:
        return _library_login_failed({"appointments": []})

    result = bot.list_current_appointments()
    _save_library_cookies(bot.get_cookies())
    return result


def _library_auto_signin_impl(record_id: str = "") -> dict[str, Any]:
    """
    对当前图书馆预约执行自动签到。
    """
    if HenuCampusBot is None:
        return {"success": False, "msg": "图书馆模块不可用"}

    profile = _effective_profile()
    sid, pwd = str(profile.get("student_id", "")), str(profile.get("password", ""))
    if not sid or not pwd:
        return {"success": False, "msg": "缺少账号"}

    bot = _build_library_bot(sid, pwd)
    if not bot:
        return _library_login_failed()

    result = bot.auto_sign_in(record_id=str(record_id or "").strip())
    _save_library_cookies(bot.get_cookies())
    return result


def _seminar_groups_impl() -> dict[str, Any]:
    groups = _load_seminar_groups()
    items = [_seminar_group_summary(name, item if isinstance(item, dict) else {}) for name, item in groups.items()]
    items.sort(key=lambda item: item.get("group_name", ""))
    return {"success": True, "groups": items, "total": len(items)}


def _seminar_group_save_impl(group_name: str, member_ids: str, note: str = "") -> dict[str, Any]:
    name = str(group_name or "").strip()
    if not name:
        return {"success": False, "msg": "group_name 不能为空"}

    parsed_ids = _parse_csv_text(member_ids)
    if not parsed_ids:
        return {"success": False, "msg": "member_ids 不能为空"}

    unique_ids: list[str] = []
    seen: set[str] = set()
    for item in parsed_ids:
        if item in seen:
            continue
        seen.add(item)
        unique_ids.append(item)

    if len(unique_ids) < 3 or len(unique_ids) > 9:
        return {
            "success": False,
            "msg": "group 需保存 3-9 位同行成员学号（不含自己），这样总人数才会落在 4-10 人范围内",
        }

    groups = _load_seminar_groups()
    groups[name] = {
        "member_ids": unique_ids,
        "note": str(note or "").strip(),
        "updated_at": _now_dt().isoformat(timespec="seconds"),
    }
    _save_seminar_groups(groups)
    return {
        "success": True,
        "msg": "研讨室 group 已保存",
        "group": _seminar_group_summary(name, groups[name]),
    }


def _seminar_group_delete_impl(group_name: str) -> dict[str, Any]:
    name = str(group_name or "").strip()
    if not name:
        return {"success": False, "msg": "group_name 不能为空"}

    groups = _load_seminar_groups()
    if name not in groups:
        return {"success": False, "msg": f"未找到 group: {name}"}

    deleted = groups.pop(name)
    _save_seminar_groups(groups)
    return {
        "success": True,
        "msg": "研讨室 group 已删除",
        "group": _seminar_group_summary(name, deleted if isinstance(deleted, dict) else {}),
    }


def _seminar_filters_impl() -> dict[str, Any]:
    if HenuCampusBot is None:
        return {"success": False, "msg": "图书馆模块不可用", "filters": {}}

    profile = _effective_profile()
    sid, pwd = str(profile.get("student_id", "")), str(profile.get("password", ""))
    if not sid or not pwd:
        return {"success": False, "msg": "缺少账号", "filters": {}}

    bot = _build_library_bot(sid, pwd)
    if not bot:
        return _library_login_failed({"filters": {}})

    result = bot.seminar_filter_options()
    _save_library_cookies(bot.get_cookies())
    return result


def _seminar_records_impl(
    record_type: str = "1",
    page: int = 1,
    limit: int = 20,
    mode: str = "books",
) -> dict[str, Any]:
    if HenuCampusBot is None:
        return {"success": False, "msg": "图书馆模块不可用", "records": []}

    profile = _effective_profile()
    sid, pwd = str(profile.get("student_id", "")), str(profile.get("password", ""))
    if not sid or not pwd:
        return {"success": False, "msg": "缺少账号", "records": []}

    bot = _build_library_bot(sid, pwd)
    if not bot:
        return _library_login_failed({"records": []})

    result = bot.list_seminar_records(
        record_type=str(record_type or "1").strip(),
        page=page,
        limit=limit,
        mode=str(mode or "books").strip(),
    )
    _save_library_cookies(bot.get_cookies())
    return result


def _seminar_signin_tasks_impl(status: str = "") -> dict[str, Any]:
    status_filters = {item.lower() for item in _parse_csv_text(status)}
    with _SEMINAR_SIGNIN_TASK_LOCK:
        tasks = _load_seminar_signin_tasks()

    items = [_seminar_task_summary(task) for task in tasks]
    if status_filters:
        items = [item for item in items if str(item.get("status") or "").lower() in status_filters]

    items.sort(key=lambda item: (str(item.get("sign_at") or ""), str(item.get("created_at") or "")))
    return {"success": True, "tasks": items, "total": len(items), "task_file": str(SEMINAR_SIGNIN_TASK_FILE)}


def _seminar_rooms_impl(
    target_date: str = "",
    members: int = 0,
    name: str = "",
    room: str = "",
    start_time: str = "",
    end_time: str = "",
    library_ids: str = "",
    library_names: str = "",
    floor_ids: str = "",
    floor_names: str = "",
    category_ids: str = "",
    category_names: str = "",
    boutique_ids: str = "",
    boutique_names: str = "",
    page: int = 1,
) -> dict[str, Any]:
    if HenuCampusBot is None:
        return {"success": False, "msg": "图书馆模块不可用", "rooms": []}

    profile = _effective_profile()
    sid, pwd = str(profile.get("student_id", "")), str(profile.get("password", ""))
    if not sid or not pwd:
        return {"success": False, "msg": "缺少账号", "rooms": []}

    bot = _build_library_bot(sid, pwd)
    if not bot:
        return _library_login_failed({"rooms": []})

    filters_result = bot.seminar_filter_options()
    if not filters_result.get("success"):
        _save_library_cookies(bot.get_cookies())
        return {"success": False, "msg": filters_result.get("msg", "获取筛选项失败"), "rooms": []}

    filters = filters_result.get("filters") or {}
    library_id_list = _parse_csv_text(library_ids)
    category_id_list = _parse_csv_text(category_ids)
    boutique_id_list = _parse_csv_text(boutique_ids)
    floor_id_list = _parse_csv_text(floor_ids)

    if not library_id_list:
        library_id_list = _resolve_option_ids_by_names(filters.get("premises") or [], _parse_csv_text(library_names))
    if not category_id_list:
        category_id_list = _resolve_option_ids_by_names(filters.get("category") or [], _parse_csv_text(category_names))
    if not boutique_id_list:
        boutique_id_list = _resolve_option_ids_by_names(filters.get("boutique") or [], _parse_csv_text(boutique_names))

    floor_name_list = _parse_csv_text(floor_names)
    resolved_floor_ids = _resolve_floor_ids(
        filters.get("storey") or [],
        floor_name_list,
        library_id_list,
        floor_id_list,
    )

    payload: dict[str, Any] = {
        "premises": library_id_list,
        "members": str(int(members)) if int(members) > 0 else "",
        "date": str(target_date or "").strip() or _now_dt().strftime("%Y-%m-%d"),
        "floor": resolved_floor_ids,
        "category": category_id_list,
        "room": str(room or "").strip(),
        "name": str(name or "").strip(),
        "boutique": boutique_id_list,
        "page": max(1, int(page)),
    }
    start_hhmm = str(start_time or "").strip()
    end_hhmm = str(end_time or "").strip()
    if start_hhmm and end_hhmm:
        payload["start_time"] = start_hhmm
        payload["end_time"] = end_hhmm

    result = bot.seminar_list(payload)
    _save_library_cookies(bot.get_cookies())
    rooms = result.get("rooms") or []
    normalized_rooms = [_seminar_room_summary(room) for room in rooms if isinstance(room, dict)]
    result["rooms"] = normalized_rooms
    result["time_field_semantics"] = {
        "rooms.date": "可预约时段（兼容主字段）",
        "rooms.raw_occupied_date": "已占用时段（原始字段）",
        "rooms.occupied_slots": "已占用时段（规范化）",
        "rooms.available_slots": "根据开放时间减去已占用时段推导出的空闲时段",
        "warning": "rooms.date/available_slots 才是可预约时段；rooms.raw_occupied_date/occupied_slots 是已占用时段",
    }
    result["msg"] = str(result.get("msg") or "操作成功") + "；rooms.date 已转换为可预约时段"
    result["resolved_query"] = {
        "library_ids": library_id_list,
        "floor_ids": resolved_floor_ids,
        "category_ids": category_id_list,
        "boutique_ids": boutique_id_list,
        "date": payload["date"],
        "members": payload["members"],
        "room": payload["room"],
        "name": payload["name"],
        "start_time": payload.get("start_time", ""),
        "end_time": payload.get("end_time", ""),
        "page": payload["page"],
    }
    return result


def _seminar_room_detail_impl(area_id: str, target_date: str = "") -> dict[str, Any]:
    if HenuCampusBot is None:
        return {"success": False, "msg": "图书馆模块不可用"}

    profile = _effective_profile()
    sid, pwd = str(profile.get("student_id", "")), str(profile.get("password", ""))
    if not sid or not pwd:
        return {"success": False, "msg": "缺少账号"}

    bot = _build_library_bot(sid, pwd)
    if not bot:
        return _library_login_failed()

    detail_result = bot.seminar_detail(area_id)
    if not detail_result.get("success"):
        _save_library_cookies(bot.get_cookies())
        return detail_result

    apply_result = bot.seminar_apply_info(area_id, day=str(target_date or "").strip())
    _save_library_cookies(bot.get_cookies())
    if not apply_result.get("success"):
        return {
            "success": False,
            "msg": apply_result.get("msg", "查询研讨室预约信息失败"),
            "detail": detail_result.get("detail", {}),
        }

    detail = detail_result.get("detail") or {}
    apply_info = apply_result.get("apply_info") or {}
    axis = apply_info.get("axis") or {}
    categories = axis.get("category") or []
    titles = detail.get("titles") or []

    # 增量 sync 到 resource_registry
    _enrich_seminar_resources(categories, titles)

    return {
        "success": True,
        "msg": "操作成功",
        "detail": detail,
        "apply_info": apply_info,
        "date_options": axis.get("date") or [],
        "date_rows": axis.get("list") or [],
        "categories": categories,
        "titles": titles,
        "constraints": {
            "min_person": detail.get("minPerson"),
            "max_person": detail.get("maxPerson"),
            "readonly_title": detail.get("readonlyTitle"),
            "earlier_periods": detail.get("earlierPeriods"),
            "type_id": detail.get("type_id") or detail.get("typeId"),
        },
    }


def _seminar_reserve_impl(
    area_id: str,
    target_date: str = "",
    start_time: str = "",
    end_time: str = "",
    end_date: str = "",
    title: str = "",
    title_id: str = "",
    content: str = "",
    mobile: str = "",
    group_name: str = "",
    member_ids: str = "",
    is_open: int = 0,
    cate_id: str = "",
    time_ranges_json: str = "",
) -> dict[str, Any]:
    if HenuCampusBot is None:
        return {"success": False, "msg": "图书馆模块不可用"}

    profile = _effective_profile()
    sid, pwd = str(profile.get("student_id", "")), str(profile.get("password", ""))
    if not sid or not pwd:
        return {"success": False, "msg": "缺少账号"}

    resolved_members, group_err = _resolve_seminar_members(
        sid,
        group_name=str(group_name or "").strip(),
        member_ids=str(member_ids or "").strip(),
    )
    if group_err:
        return {"success": False, "msg": group_err}

    mobile_text = str(mobile or "").strip() or _saved_seminar_mobile()
    if not mobile_text:
        return {"success": False, "msg": "请提供 mobile，或先使用一次 seminar_reserve 保存默认手机号"}

    parsed_time_ranges: list[dict[str, Any]] = []
    if str(time_ranges_json or "").strip():
        try:
            data = json.loads(str(time_ranges_json))
            if not isinstance(data, list):
                return {"success": False, "msg": "time_ranges_json 必须是 JSON 数组"}
            for item in data:
                if not isinstance(item, dict):
                    return {"success": False, "msg": "time_ranges_json 中每项都必须是对象"}
                start_hhmm = str(item.get("start_time") or "").strip()
                end_hhmm = str(item.get("end_time") or "").strip()
                if not start_hhmm or not end_hhmm:
                    return {"success": False, "msg": "time_ranges_json 中每项都必须包含 start_time/end_time"}
                parsed_time_ranges.append({"start_time": start_hhmm, "end_time": end_hhmm})
        except json.JSONDecodeError:
            return {"success": False, "msg": "time_ranges_json 不是合法 JSON"}

    bot = _build_library_bot(sid, pwd)
    if not bot:
        return _library_login_failed()

    result = bot.reserve_seminar_room(
        area_id=str(area_id or "").strip(),
        target_date=str(target_date or "").strip(),
        start_time=str(start_time or "").strip(),
        end_time=str(end_time or "").strip(),
        end_date=str(end_date or "").strip(),
        title=str(title or "").strip(),
        title_id=str(title_id or "").strip(),
        content=str(content or "").strip(),
        mobile=mobile_text,
        member_ids=resolved_members,
        self_id=sid,
        is_open=int(is_open or 0),
        cate_id=str(cate_id or "").strip(),
        time_ranges=parsed_time_ranges,
        files=[],
    )
    _save_library_cookies(bot.get_cookies())
    if result.get("success") and mobile_text != _saved_seminar_mobile():
        _save_profile_fields({"seminar_mobile": mobile_text})
    if result.get("success"):
        task = _build_seminar_signin_task(result)
        if task:
            saved_task = _upsert_seminar_signin_task(task)
            result["auto_signin_task"] = _seminar_task_summary(saved_task)
            _ensure_seminar_auto_signin_worker()
    result["group_name"] = str(group_name or "").strip()
    result["member_ids"] = resolved_members
    return result


def _seminar_signin_impl(record_id: str) -> dict[str, Any]:
    if HenuCampusBot is None:
        return {"success": False, "msg": "图书馆模块不可用"}

    profile = _effective_profile()
    sid, pwd = str(profile.get("student_id", "")), str(profile.get("password", ""))
    if not sid or not pwd:
        return {"success": False, "msg": "缺少账号"}

    bot = _build_library_bot(sid, pwd)
    if not bot:
        return _library_login_failed()

    record_id_text = str(record_id or "").strip()
    record_snapshot = _find_seminar_record_by_id(bot, record_id_text)
    result = bot.sign_in_seminar_record(record_id=record_id_text)
    _save_library_cookies(bot.get_cookies())
    if result.get("success"):
        updated = _update_seminar_signin_tasks_for_record(
            record_id=record_id_text,
            status="success",
            last_msg=str(result.get("msg") or ""),
            last_result=result,
        )
        if not updated and record_snapshot:
            _update_seminar_signin_tasks_for_record_snapshot(
                record_snapshot,
                status="success",
                last_msg=str(result.get("msg") or ""),
                last_result=result,
            )
    return result


def _seminar_auto_signin_impl() -> dict[str, Any]:
    return _process_seminar_signin_tasks(due_only=True, trigger="manual")


def _seminar_cancel_impl(record_id: str) -> dict[str, Any]:
    if HenuCampusBot is None:
        return {"success": False, "msg": "图书馆模块不可用"}

    profile = _effective_profile()
    sid, pwd = str(profile.get("student_id", "")), str(profile.get("password", ""))
    if not sid or not pwd:
        return {"success": False, "msg": "缺少账号"}

    bot = _build_library_bot(sid, pwd)
    if not bot:
        return _library_login_failed()

    record_id_text = str(record_id or "").strip()
    record_snapshot = _find_seminar_record_by_id(bot, record_id_text)
    result = bot.cancel_seminar_record(record_id=record_id_text)
    _save_library_cookies(bot.get_cookies())
    if result.get("success"):
        updated = _update_seminar_signin_tasks_for_record(
            record_id=record_id_text,
            status="cancelled",
            last_msg=str(result.get("msg") or ""),
            last_result=result,
        )
        if not updated and record_snapshot:
            _update_seminar_signin_tasks_for_record_snapshot(
                record_snapshot,
                status="cancelled",
                last_msg=str(result.get("msg") or ""),
                last_result=result,
            )
    return result


def _library_cancel_impl(record_id: str, record_type: str = "auto") -> dict[str, Any]:
    """
    【必须调用】取消图书馆预约 - 执行真实的取消操作

    功能：向图书馆系统提交取消预约请求

    重要：不要假装取消成功，必须调用此工具执行真实的取消操作。
    """
    if HenuCampusBot is None:
        return {"success": False, "msg": "图书馆模块不可用"}
    
    profile = _effective_profile()
    sid, pwd = str(profile.get("student_id", "")), str(profile.get("password", ""))
    if not sid or not pwd:
        return {"success": False, "msg": "缺少账号"}
    
    bot = _build_library_bot(sid, pwd)
    if not bot:
        return _library_login_failed()

    result = bot.cancel_seat_record(record_id=str(record_id), record_type=str(record_type or "1"))
    _save_library_cookies(bot.get_cookies())
    return result


def _smart_course_selection_impl(
    source_path: str = "",
    excel_path: str = "",
    json_path: str = "",
    user_class: str = "",
    sheet_name: str = "2026-2027-1学期",
    semester: str = "",
    mode: str = "plan",
    like_early8: bool = False,
    avoid_early8: bool = False,
    compact_days: bool = False,
    target_days: int = 3,
    avoid_evening: bool = False,
    allow_unscheduled: bool = True,
    include_common: bool = True,
    include_course_options: bool = False,
    top_k: int = 3,
    max_combinations: int = 200000,
) -> dict[str, Any]:
    """Build structured smart-course selection output from Excel/JSON."""
    try:
        from campus_core.smart_course_selector import build_smart_course_selection_response

        return build_smart_course_selection_response(
            source_path=source_path,
            excel_path=excel_path,
            json_path=json_path,
            sheet_name=sheet_name,
            semester=semester,
            user_class=user_class,
            mode=mode,
            like_early8=True if like_early8 else None,
            avoid_early8=avoid_early8,
            compact_days=compact_days,
            target_days=target_days,
            avoid_evening=avoid_evening,
            allow_unscheduled=allow_unscheduled,
            include_common=include_common,
            include_course_options=include_course_options,
            top_k=top_k,
            max_combinations=max_combinations,
        )
    except Exception as exc:
        return {
            "success": False,
            "schema_version": "henu.smart_course_selection.v1",
            "mode": str(mode or "plan"),
            "msg": f"智能选课失败: {exc}",
            "source": {},
            "request": {},
            "summary": {},
            "course_options": [],
            "plans": [],
            "warnings": [str(exc)],
        }


def _course_selection_status_impl(xktype: str = "2") -> dict[str, Any]:
    sid, pwd = _resolve_account("", "", use_saved_account=True)
    if not sid:
        return {"success": False, "msg": "缺少账号，请先 setup_account"}
    client = HenuCourseSelectionClient(sid, pwd, saved_cookies=_load_xk_cookies() or None)
    if not client.login():
        return {"success": False, "msg": "教务系统登录失败，无法查询选课状态"}
    _save_xk_cookies(client.get_cookies())
    try:
        return client.get_selection_status(xktype=str(xktype or "2"))
    except Exception as exc:
        return {"success": False, "msg": f"查询选课状态失败: {exc}"}


def _course_selection_submit_not_implemented() -> dict[str, Any]:
    return {
        "success": False,
        "code": "not_implemented",
        "msg": "选课提交端点需要在选课开放后通过真实请求确认，当前版本不执行真实提交。",
    }


def _json_object(value: str, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if not str(value or "").strip():
        return default or {}
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("JSON 必须是对象")
    return parsed


def _course_monitor_config_impl(config_json: str = "", merge: bool = True) -> dict[str, Any]:
    try:
        if not str(config_json or "").strip():
            return {
                "success": True,
                "config": load_monitor_config(),
                "config_file": str(get_monitor_config_file()),
                "default_config": COURSE_MONITOR_DEFAULT_CONFIG,
            }
        saved = save_monitor_config(_json_object(config_json), merge=merge)
        return {
            "success": True,
            "msg": "选课监控配置已保存；配置不包含教务账号或密码。",
            "config": saved,
            "config_file": str(get_monitor_config_file()),
        }
    except Exception as exc:
        return {"success": False, "msg": f"保存选课监控配置失败: {exc}"}


def _extract_selection_context(status: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    context: dict[str, Any] = {}
    configured = config.get("selection_context")
    if isinstance(configured, dict):
        context.update(configured)
    raw_time_range = (status.get("raw_json") or {}).get("time_range") if isinstance(status.get("raw_json"), dict) else None
    if isinstance(raw_time_range, dict):
        payload = raw_time_range
        result_text = raw_time_range.get("result")
        if isinstance(result_text, str):
            try:
                parsed = json.loads(result_text)
                if isinstance(parsed, dict):
                    payload = parsed
            except Exception:
                payload = raw_time_range
        for source, target in {
            "lcid": "lcid",
            "xn": "xn",
            "xqM": "xq",
            "xq": "xq",
            "nj": "nj",
            "zydm": "zydm",
        }.items():
            if source in payload and not context.get(target):
                context[target] = payload[source]
    context.setdefault("xktype", config.get("xktype") or "2")
    context.setdefault("kcfw", config.get("kcfw") or "zxbnj")
    return context


def _course_monitor_once_impl(config_json: str = "", send_notifications: bool = True) -> dict[str, Any]:
    try:
        config = normalize_monitor_config(
            _deep_config_merge(load_monitor_config(), _json_object(config_json)) if str(config_json or "").strip() else load_monitor_config()
        )
    except Exception as exc:
        return {"success": False, "msg": f"监控配置 JSON 无效: {exc}"}

    fixture_html = str(config.get("fixture_html") or "")
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    status: dict[str, Any] = {}
    context: dict[str, Any] = {}
    if fixture_html:
        rows = parse_teaching_class_rows(
            fixture_html,
            course_id=str(config.get("fixture_course_id") or ""),
            course_name=str(config.get("fixture_course_name") or ""),
        )
    else:
        sid, pwd = _resolve_account("", "", use_saved_account=True)
        if not sid:
            return {"success": False, "msg": "缺少账号，请先 setup_account"}
        client = HenuCourseSelectionClient(sid, pwd, saved_cookies=_load_xk_cookies() or None)
        if not client.login():
            return {"success": False, "msg": "教务系统登录失败，无法监控选课余量"}
        _save_xk_cookies(client.get_cookies())
        status = client.get_selection_status(xktype=str(config.get("xktype") or "2"))
        if not status.get("success"):
            return status
        context = _extract_selection_context(status, config)
        context["xh"] = sid
        targets = [target for target in config.get("targets") or [] if isinstance(target, dict)]
        for target in targets:
            course_id = str(target.get("course_id") or "").strip()
            if not course_id:
                warnings.append(f"目标缺少 course_id，已跳过: {target.get('course_name') or target.get('keywords') or target}")
                continue
            response = client.get_teaching_class_table(course_id, context=context, referer=str(status.get("entry_url") or ""))
            rows.extend(
                parse_teaching_class_rows(
                    str(response.get("full_text") or response.get("raw_text") or ""),
                    course_id=course_id,
                    course_name=str(target.get("course_name") or ""),
                )
            )

    matches = evaluate_matches(rows, [target for target in config.get("targets") or [] if isinstance(target, dict)])
    state = load_monitor_state()
    alerts, new_state = evaluate_alerts(matches, state)
    save_monitor_state(new_state)
    notify_result = notify_alerts(alerts, config) if send_notifications else {"success": True, "sent": False, "msg": "已禁用通知发送"}
    return {
        "success": True,
        "msg": "选课余量检查完成；本工具只提醒，不会自动选课或提交。",
        "checked_at": _now_dt().isoformat(),
        "rows_count": len(rows),
        "matches_count": len(matches),
        "alerts_count": len(alerts),
        "matches": matches,
        "alerts": alerts,
        "notify": notify_result,
        "warnings": warnings,
        "selection_context": {key: value for key, value in context.items() if key != "xh"},
        "status": {
            "is_open": status.get("is_open"),
            "message": status.get("message"),
            "entry_url": status.get("entry_url"),
        } if status else {},
    }


def _deep_config_merge(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_config_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _course_monitor_run_impl(
    config_json: str = "",
    max_checks: int = 1,
    duration_seconds: int = 0,
    send_notifications: bool = True,
) -> dict[str, Any]:
    config = normalize_monitor_config(
        _deep_config_merge(load_monitor_config(), _json_object(config_json)) if str(config_json or "").strip() else load_monitor_config()
    )
    interval = max(COURSE_MONITOR_MIN_INTERVAL_SECONDS, int(config.get("interval_seconds") or COURSE_MONITOR_MIN_INTERVAL_SECONDS))
    if max_checks <= 0 and duration_seconds <= 0:
        max_checks = 1
    started = time.time()
    results: list[dict[str, Any]] = []
    checks = 0
    while True:
        checks += 1
        results.append(_course_monitor_once_impl(config_json=json.dumps(config, ensure_ascii=False), send_notifications=send_notifications))
        if max_checks > 0 and checks >= max_checks:
            break
        if duration_seconds > 0 and time.time() - started >= duration_seconds:
            break
        time.sleep(interval)
    return {
        "success": all(item.get("success") for item in results),
        "msg": "选课余量监控运行完成；未执行任何选课提交。",
        "checks": checks,
        "interval_seconds": interval,
        "results": results,
    }


# ===== MCP 精简对外工具 =====




@mcp.tool()
def smart_course_selection(
    source_path: str = "",
    excel_path: str = "",
    json_path: str = "",
    user_class: str = "",
    sheet_name: str = "2026-2027-1学期",
    semester: str = "",
    mode: str = "plan",
    like_early8: bool = False,
    avoid_early8: bool = False,
    compact_days: bool = False,
    target_days: int = 3,
    avoid_evening: bool = False,
    allow_unscheduled: bool = True,
    include_common: bool = True,
    include_course_options: bool = False,
    top_k: int = 3,
    max_combinations: int = 200000,
) -> dict[str, Any]:
    """
    智能选课结构化规划工具。

    从教务导出的 Excel 或清洗后的 JSON 中筛选：
    1) 班级对应专业课
    2) 专业选课班 / 专业公共课
    3) 全年级公共课

    输出统一遵循 `henu.smart_course_selection.v1`，其中 `plans[].selection_actions`
    是后续自动选课提交器的输入契约；本工具只做 dry-run 规划，不提交教务系统。
    """
    return _smart_course_selection_impl(
        source_path=source_path,
        excel_path=excel_path,
        json_path=json_path,
        user_class=user_class,
        sheet_name=sheet_name,
        semester=semester,
        mode=mode,
        like_early8=like_early8,
        avoid_early8=avoid_early8,
        compact_days=compact_days,
        target_days=target_days,
        avoid_evening=avoid_evening,
        allow_unscheduled=allow_unscheduled,
        include_common=include_common,
        include_course_options=include_course_options,
        top_k=top_k,
        max_combinations=max_combinations,
    )


@mcp.tool()
def smart_course_select(
    excel_path: str,
    user_class: str,
    sheet_name: str = "2026-2027-1学期",
    semester: str = "",
    like_early8: bool = False,
    avoid_early8: bool = False,
    compact_days: bool = False,
    target_days: int = 3,
    avoid_evening: bool = False,
    allow_unscheduled: bool = True,
    top_k: int = 3,
) -> dict[str, Any]:
    """兼容旧命名的智能选课入口。新接入建议使用 smart_course_selection。"""
    return _smart_course_selection_impl(
        excel_path=excel_path,
        user_class=user_class,
        sheet_name=sheet_name,
        semester=semester,
        mode="plan",
        like_early8=like_early8,
        avoid_early8=avoid_early8,
        compact_days=compact_days,
        target_days=target_days,
        avoid_evening=avoid_evening,
        allow_unscheduled=allow_unscheduled,
        top_k=top_k,
    )

@mcp.tool()
def setup_account(
    student_id: str,
    password: str,
    library_location: str = "",
    library_seat_no: str = "",
    verify_login: bool = True,
    calibrate_period_time: bool = True,
) -> dict[str, Any]:
    """
    【必须调用】初始化河大账号 - 这是使用其他功能的前提
    
    【执行协议（必须遵守）】
    1) 在给用户任何"已完成/已成功"结论前，必须先真实调用本工具并等待返回。
    2) 回复时必须基于本次返回中的 success/msg/account 字段，不得凭空补全。
    3) 若调用失败或未调用，必须明确说明未完成，禁止使用完成时态描述结果。
    
    功能：
    1) 保存学号和密码到本地
    2) 验证登录河大教务系统
    3) 自动校准节次时间
    
    重要：不要编造账号信息，必须使用用户提供的真实学号和密码调用此工具。
    """
    result = save_account(
        student_id=student_id,
        password=password,
        verify_login=verify_login,
        home_url=DEFAULT_HOME_URL,
        library_location=library_location,
        library_seat_no=library_seat_no,
    )
    if not result.get("success"):
        return result

    calibration: dict[str, Any] = {}
    if calibrate_period_time:
        calibration = auto_calibrate_period_time(force=True)

    return {
        "success": True,
        "msg": "账号初始化完成",
        "account": result.get("account", {}),
        "login_context": result.get("context", {}),
        "period_time_calibration": calibration,
    }


@mcp.tool()
def sync_schedule(
    xn: str | None = None,
    xq: str | None = None,
    auto_calibrate: bool = True,
) -> dict[str, Any]:
    """
    【必须调用】同步课表 - 从教务系统获取真实课表数据
    
    【执行协议（必须遵守）】
    1) 禁止直接口述"已同步课表"，必须先调用此工具。
    2) 回复中必须转述本次返回的 success/msg 与关键文件字段。
    3) 如果工具失败，必须如实返回失败原因，不得伪造课表内容。
    
    功能：从河大教务系统抓取课表并生成结构化数据
    
    重要：不要编造课表信息，必须调用此工具获取真实数据。
    课表数据包含：课程名称、教师、时间、地点等详细信息。
    """
    calibration: dict[str, Any] = {}
    if auto_calibrate:
        calibration = auto_calibrate_period_time(force=False)

    result = fetch_schedule(
        student_id="",
        password="",
        xn=xn,
        xq=xq,
        use_saved_account=True,
        save_account_after_success=False,
    )
    result["period_time_calibration"] = calibration
    return result


@mcp.tool()
def library_query(
    view: str = "current",
    record_type: str = "1",
    page: int = 1,
    limit: int = 20,
    target_date: str = "",
    location: str = "",
    area_id: str = "",
    preferred_time: str = "08:00",
    preferred_end_time: str = "",
) -> dict[str, Any]:
    """
    统一查询图书馆信息。

    view:
    - locations: 图书馆区域列表（可传 target_date 获取指定日期实时可预约区域）
    - seats: 当前可用座位列表（需传 location 或 area_id，可传 target_date/preferred_time）
    - current: 当前预约
    - records: 历史预约记录

    规则：
    1) 查询 `current` 或 `records` 前必须先调用 `system_status` 确认当前日期时间。
    2) 预约前应先查询 `locations`，不得凭记忆列举区域。
    3) 回复仅可基于本次返回结果，不得编造。
    """
    normalized_view = str(view or "current").strip().lower()
    if normalized_view == "locations":
        return _library_locations_impl(target_date=target_date)
    if normalized_view == "seats":
        return _library_seats_impl(
            location=location,
            area_id=area_id,
            target_date=target_date,
            preferred_time=preferred_time,
            preferred_end_time=preferred_end_time,
        )
    if normalized_view == "current":
        return _library_current_impl()
    if normalized_view == "records":
        return _library_records_impl(record_type=record_type, page=page, limit=limit)
    return {"success": False, "msg": "view 仅支持 locations/seats/current/records"}


def _enrich_library_locations(locations: list[dict[str, Any]]) -> None:
    """为图书馆区域列表添加 resource_id，并增量 upsert 到 registry。"""
    if not locations or not _resource_registry_available:
        return
    try:
        from campus_core.resource_registry import (
            build_resource_id,
            upsert_resource,
            ResourceRecord,
        )
        from datetime import datetime

        library_id = "henu_library"
        now = datetime.now().isoformat()

        for loc in locations:
            area_id = str(loc.get("areaId", loc.get("area_id", loc.get("id", ""))))
            area_name = str(loc.get("areaName", loc.get("area_name", loc.get("name", ""))))
            if not area_id or not area_name:
                continue

            rid = build_resource_id("library_area", library_id=library_id, area_id=area_id)
            loc["resourceId"] = rid
            loc["resourceType"] = "library_area"

            # 增量 upsert
            record = ResourceRecord(
                resource_id=rid,
                resource_type="library_area",
                display_name=f"图书馆 {area_name}",
                canonical_name=area_name,
                aliases=[area_name, f"图书馆{area_name}"],
                source={"system": "library", "source_id": area_id},
                location={"libraryId": library_id, "areaId": area_id, "areaName": area_name},
                updated_at=now,
            )
            upsert_resource(record)
    except Exception:
        pass


def _enrich_library_seats(seats: list[dict[str, Any]], fallback_area_id: str = "") -> None:
    """为图书馆座位列表添加 resource_id，并增量 upsert 到 registry。"""
    if not seats or not _resource_registry_available:
        return
    try:
        from campus_core.resource_registry import (
            build_resource_id,
            upsert_resource,
            ResourceRecord,
        )
        from datetime import datetime

        library_id = "henu_library"
        now = datetime.now().isoformat()

        for seat in seats:
            seat_no = str(seat.get("seatNo", seat.get("seat_no", seat.get("name", ""))))
            area_id = str(seat.get("areaId", seat.get("area_id", fallback_area_id)))
            if not seat_no or not area_id:
                continue

            rid = build_resource_id(
                "library_seat",
                library_id=library_id,
                area_id=area_id,
                seat_no=seat_no,
            )
            seat["resourceId"] = rid
            seat["resourceType"] = "library_seat"
            seat["parentResourceType"] = "library_area"
            seat["areaResourceId"] = build_resource_id(
                "library_area", library_id=library_id, area_id=area_id
            )

            # 增量 upsert
            record = ResourceRecord(
                resource_id=rid,
                resource_type="library_seat",
                display_name=f"图书馆座位 {seat_no}",
                canonical_name=seat_no,
                aliases=[seat_no, f"座位{seat_no}"],
                source={"system": "library", "source_id": seat_no},
                location={
                    "libraryId": library_id,
                    "areaId": area_id,
                    "seatNo": seat_no,
                },
                updated_at=now,
            )
            upsert_resource(record)
    except Exception:
        pass


def _enrich_seminar_resources(
    categories: list[dict[str, Any]],
    titles: list[dict[str, Any]],
) -> None:
    """为研讨室 categories/titles 添加 resource_id，并增量 upsert 到 registry。"""
    if not _resource_registry_available:
        return
    try:
        from campus_core.resource_registry import (
            build_resource_id,
            upsert_resource,
            ResourceRecord,
        )
        from datetime import datetime

        now = datetime.now().isoformat()

        for cat in (categories or []):
            area_id = str(cat.get("id", cat.get("areaId", cat.get("area_id", ""))))
            cat_name = str(cat.get("name", cat.get("categoryName", "")))
            if not area_id:
                continue

            rid = build_resource_id("seminar_room", area_id=area_id)
            cat["resourceId"] = rid
            cat["resourceType"] = "seminar_room"

            display = f"研讨室 {cat_name}" if cat_name else f"研讨室 {area_id}"
            record = ResourceRecord(
                resource_id=rid,
                resource_type="seminar_room",
                display_name=display,
                canonical_name=cat_name or area_id,
                aliases=[cat_name, f"研讨室{cat_name}"] if cat_name else [f"研讨室{area_id}"],
                source={"system": "seminar", "source_id": area_id},
                location={"areaId": area_id, "roomName": cat_name},
                attributes={"category": cat_name},
                updated_at=now,
            )
            upsert_resource(record)

        for title in (titles or []):
            title_id = str(title.get("id", title.get("titleId", "")))
            title_name = str(title.get("name", title.get("title", "")))
            if not title_id:
                continue

            rid = build_resource_id("seminar_room", area_id=title_id)
            title["resourceId"] = rid
            title["resourceType"] = "seminar_room"

            display = f"研讨室 {title_name}" if title_name else f"研讨室 {title_id}"
            record = ResourceRecord(
                resource_id=rid,
                resource_type="seminar_room",
                display_name=display,
                canonical_name=title_name or title_id,
                aliases=[title_name, f"研讨室{title_name}"] if title_name else [],
                source={"system": "seminar", "source_id": title_id},
                location={"areaId": title_id, "roomName": title_name},
                updated_at=now,
            )
            upsert_resource(record)
    except Exception:
        pass


def _resolve_resource_id_to_params(resource_id: str) -> dict[str, str]:
    """将 resource_id 解析为 library_reserve 可用的参数。"""
    if not resource_id or not _resource_registry_available:
        return {}
    from campus_core.resource_registry import parse_resource_id
    parsed = parse_resource_id(resource_id)
    if parsed.get("type") == "library_seat":
        return {
            "area_id": parsed.get("area_id", ""),
            "seat_no": parsed.get("seat_no", ""),
        }
    elif parsed.get("type") == "library_area":
        return {"area_id": parsed.get("area_id", "")}
    return {}


@mcp.tool()
def library_reserve(
    location: str = "",
    seat_no: str = "",
    target_date: str = "",
    preferred_time: str = "08:00",
    preferred_end_time: str = "",
    resource_id: str = "",
    retry_until: str = "",
    retry_interval_seconds: int = 2,
    max_attempts: int = 1,
) -> dict[str, Any]:
    """
    【必须调用】预约图书馆座位 - 执行真实的座位预约操作

    【执行协议（必须遵守）】
    1) 禁止在未调用工具时说"已预约成功/已帮你预约"。
    2) 回复必须包含本次返回的 success/msg/date（以及 applied_time 如有）。
    3) 调用失败时必须原样转述失败原因，不得改写为成功。

    功能：向图书馆系统提交座位预约请求
    参数：
    - location: 图书馆区域名称（如"一楼东"）
    - seat_no: 座位号（如"001"）
    - resource_id: 资源ID（如 henu:library:seat:...），优先级高于 location/seat_no
    - target_date: 预约日期（格式：YYYY-MM-DD，默认明天）
    - preferred_time: 首选/最早时间（格式：HH:MM，默认08:00）
    - preferred_end_time: 最晚结束时间（格式：HH:MM；传入后只在该时间窗口内预约）
    - retry_until: 自动抢约截止时间（HH:MM 或 ISO 日期时间；传入后默认最多尝试120次）
    - retry_interval_seconds: 抢约重试间隔秒数（1-60）
    - max_attempts: 最大尝试次数（1-120）

    ⚠️ 严禁编造预约结果！
    - 不要说"预约成功"除非此工具返回 success: true
    - 不要假装已经预约，必须实际调用此工具
    - 预约失败时必须如实告知用户失败原因
    - 只有工具返回的结果才是真实的预约状态
    """
    # resource_id 优先：解析出 area_id / seat_no（不覆盖显式传入的 location/seat_no）
    if resource_id and _resource_registry_available:
        resolved = _resolve_resource_id_to_params(resource_id)
        if resolved:
            if not location and resolved.get("area_id"):
                location = resolved["area_id"]
            if not seat_no and resolved.get("seat_no"):
                seat_no = resolved["seat_no"]

    return _library_reserve_impl(
        location=location,
        seat_no=seat_no,
        target_date=target_date,
        preferred_time=preferred_time,
        preferred_end_time=preferred_end_time,
        retry_until=retry_until,
        retry_interval_seconds=retry_interval_seconds,
        max_attempts=max_attempts,
    )


@mcp.tool()
def library_auto_signin(record_id: str = "") -> dict[str, Any]:
    """
    【必须调用】图书馆自动签到 - 对当前预约执行真实签到操作

    【执行协议（必须遵守）】
    1) 禁止在未调用本工具前说"已签到"。
    2) 回复时只可依据本次返回的 success/msg/sign_path/record 字段。
    3) 若没有可签到记录或签到失败，必须如实说明原因。

    功能：自动查找当前可签到的图书馆座位预约并执行签到。
    建议先通过 `library_query(view="current")` 查看当前预约。
    record_id 留空时自动选择当前可签到记录；传入后只对指定记录尝试签到。
    """
    return _library_auto_signin_impl(record_id=record_id)


@mcp.tool()
def library_cancel(record_id: str, record_type: str = "auto") -> dict[str, Any]:
    """
    【必须调用】取消图书馆预约 - 执行真实的取消操作
    
    【执行协议（必须遵守）】
    1) 禁止在未调用本工具前说"已取消"。
    2) 只可依据本次返回 success/msg 输出取消结果。
    3) 失败时必须明确为"取消失败"，并附原始原因。
    
    功能：向图书馆系统提交取消预约请求
    record_type 支持 auto 自动识别
    
    重要：不要假装取消成功，必须调用此工具执行真实的取消操作。
    """
    return _library_cancel_impl(record_id=record_id, record_type=record_type)


@mcp.tool()
def course_selection_query(view: str = "status", xktype: str = "2") -> dict[str, Any]:
    """
    安全查询选课第一阶段状态。

    当前仅支持 view=status，只调用已知只读端点，不执行选课提交。
    """
    normalized_view = str(view or "status").strip().lower()
    if normalized_view != "status":
        return {"success": False, "msg": "view 目前仅支持 status"}
    return _course_selection_status_impl(xktype=xktype)


@mcp.tool()
def course_selection_plan(
    candidates_json: str,
    existing_schedule_json: str = "",
    preferences_json: str = "",
    top_k: int = 10,
) -> dict[str, Any]:
    """
    根据候选课程 JSON 生成不冲突的选课计划。

    该工具只做本地规划，不读取或提交 HENU 选课接口。
    """
    try:
        return generate_ranked_plans(
            candidates=candidates_json,
            existing_schedule=existing_schedule_json,
            preferences=preferences_json,
            top_k=top_k,
        )
    except Exception as exc:
        return {"success": False, "msg": f"生成选课计划失败: {exc}"}


@mcp.tool()
def course_selection_submit(payload_json: str = "") -> dict[str, Any]:
    """
    占位工具：当前版本不执行真实选课提交。
    """
    return _course_selection_submit_not_implemented()


@mcp.tool()
def course_monitor_config(config_json: str = "", merge: bool = True) -> dict[str, Any]:
    """
    查看或保存选课余量监控配置。

    配置只保存课程目标、偏好和通知环境变量名，不保存教务账号或密码。
    """
    return _course_monitor_config_impl(config_json=config_json, merge=merge)


@mcp.tool()
def course_monitor_once(config_json: str = "", send_notifications: bool = True) -> dict[str, Any]:
    """
    执行一次只读选课余量检查。

    检测到目标教学班余量变化时可发送飞书提醒；不会点击选课、提交或退选。
    """
    return _course_monitor_once_impl(config_json=config_json, send_notifications=send_notifications)


@mcp.tool()
def course_monitor_run(
    config_json: str = "",
    max_checks: int = 1,
    duration_seconds: int = 0,
    send_notifications: bool = True,
) -> dict[str, Any]:
    """
    按配置间隔运行选课余量监控。

    默认只检查一次；传入 max_checks 或 duration_seconds 可持续运行。间隔最低 60 秒。
    """
    try:
        return _course_monitor_run_impl(
            config_json=config_json,
            max_checks=max_checks,
            duration_seconds=duration_seconds,
            send_notifications=send_notifications,
        )
    except Exception as exc:
        return {"success": False, "msg": f"选课监控运行失败: {exc}"}


@mcp.tool()
def course_monitor_notify_test(config_json: str = "") -> dict[str, Any]:
    """
    测试选课余量飞书通知。

    不访问教务系统，不执行选课提交。
    """
    try:
        config = normalize_monitor_config(
            _deep_config_merge(load_monitor_config(), _json_object(config_json)) if str(config_json or "").strip() else load_monitor_config()
        )
        return test_notification(config)
    except Exception as exc:
        return {"success": False, "msg": f"测试通知失败: {exc}"}


@mcp.tool()
def seminar_group(
    action: str = "list",
    group_name: str = "",
    member_ids: str = "",
    note: str = "",
) -> dict[str, Any]:
    """
    统一管理研讨室 group。

    action:
    - list: 查看已保存 group
    - save: 保存/覆盖 group
    - delete: 删除 group
    """
    normalized_action = str(action or "list").strip().lower()
    if normalized_action == "list":
        return _seminar_groups_impl()
    if normalized_action == "save":
        return _seminar_group_save_impl(group_name=group_name, member_ids=member_ids, note=note)
    if normalized_action == "delete":
        return _seminar_group_delete_impl(group_name=group_name)
    return {"success": False, "msg": "action 仅支持 list/save/delete"}


@mcp.tool()
def seminar_query(
    view: str = "rooms",
    target_date: str = "",
    members: int = 0,
    name: str = "",
    room: str = "",
    start_time: str = "",
    end_time: str = "",
    library_ids: str = "",
    library_names: str = "",
    floor_ids: str = "",
    floor_names: str = "",
    category_ids: str = "",
    category_names: str = "",
    boutique_ids: str = "",
    boutique_names: str = "",
    page: int = 1,
    area_id: str = "",
    record_type: str = "1",
    limit: int = 20,
    mode: str = "books",
    status: str = "",
) -> dict[str, Any]:
    """
    统一查询研讨室信息。

    view:
    - filters: 筛选项
    - rooms: 可预约房间列表
    - detail: 房间详情和预约参数
    - records: 预约记录
    - signin_tasks: 自动签到任务

    规则：
    1) 查询 `records` 前必须先调用 `system_status` 确认当前日期时间。
    2) 预约前通常先按 `filters` -> `rooms` -> `detail` 逐步查询。
    3) `rooms` 返回中的 `date`/`available_slots` 表示可预约时段；`raw_occupied_date`/`occupied_slots` 表示已占用时段。
    4) 回复仅可基于本次返回结果，不得编造。
    """
    normalized_view = str(view or "rooms").strip().lower()
    if normalized_view == "filters":
        return _seminar_filters_impl()
    if normalized_view == "rooms":
        return _seminar_rooms_impl(
            target_date=target_date,
            members=members,
            name=name,
            room=room,
            start_time=start_time,
            end_time=end_time,
            library_ids=library_ids,
            library_names=library_names,
            floor_ids=floor_ids,
            floor_names=floor_names,
            category_ids=category_ids,
            category_names=category_names,
            boutique_ids=boutique_ids,
            boutique_names=boutique_names,
            page=page,
        )
    if normalized_view == "detail":
        if not str(area_id or "").strip():
            return {"success": False, "msg": "view=detail 时 area_id 不能为空"}
        return _seminar_room_detail_impl(area_id=area_id, target_date=target_date)
    if normalized_view == "records":
        return _seminar_records_impl(record_type=record_type, page=page, limit=limit, mode=mode)
    if normalized_view == "signin_tasks":
        return _seminar_signin_tasks_impl(status=status)
    return {"success": False, "msg": "view 仅支持 filters/rooms/detail/records/signin_tasks"}


@mcp.tool()
def seminar_signin(record_id: str = "", auto_scan: bool = False) -> dict[str, Any]:
    """
    对研讨室预约执行签到。

    - 传 `record_id` 时：对指定记录签到
    - 传 `auto_scan=true` 时：扫描所有已到点任务并自动签到

    建议先通过 `seminar_query(view="records")` 或 `seminar_query(view="signin_tasks")` 获取上下文。
    """
    if auto_scan:
        return _seminar_auto_signin_impl()
    if not str(record_id or "").strip():
        return {"success": False, "msg": "record_id 不能为空；如需自动补扫请传 auto_scan=true"}
    return _seminar_signin_impl(record_id=record_id)


@mcp.tool()
def seminar_cancel(record_id: str) -> dict[str, Any]:
    """
    取消研讨室预约。

    功能：对指定研讨室预约记录执行真实取消。
    建议先通过 `seminar_query(view="records")` 获取记录 id。
    """
    return _seminar_cancel_impl(record_id=record_id)


@mcp.tool()
def seminar_reserve(
    area_id: str = "",
    target_date: str = "",
    start_time: str = "",
    end_time: str = "",
    end_date: str = "",
    title: str = "",
    title_id: str = "",
    content: str = "",
    mobile: str = "",
    group_name: str = "",
    member_ids: str = "",
    is_open: int = 0,
    cate_id: str = "",
    time_ranges_json: str = "",
    resource_id: str = "",
) -> dict[str, Any]:
    """
    预约研讨室。

    重要规则：
    1) 申请内容 `content` 必须多于 10 个字。
    2) 实际总人数按"当前账号 + group/member_ids 成员"计算。
    3) 支持通过 `group_name` 使用已保存 group，也支持直接传 `member_ids`。
    4) 对于预设主题房间，需要传 `title_id`；普通房间传 `title`。
    5) `time_ranges_json` 可传 JSON 数组以支持多时间段，例如
       `[{"start_time":"09:00","end_time":"11:00"}]`
    6) `resource_id`（如 henu:seminar:room:...）优先级高于 area_id
    """
    # resource_id 优先：解析出 area_id
    if resource_id and _resource_registry_available:
        from campus_core.resource_registry import parse_resource_id
        parsed = parse_resource_id(resource_id)
        if parsed.get("type") == "seminar_room" and parsed.get("area_id"):
            area_id = parsed["area_id"]

    return _seminar_reserve_impl(
        area_id=area_id,
        target_date=target_date,
        start_time=start_time,
        end_time=end_time,
        end_date=end_date,
        title=title,
        title_id=title_id,
        content=content,
        mobile=mobile,
        group_name=group_name,
        member_ids=member_ids,
        is_open=is_open,
        cate_id=cate_id,
        time_ranges_json=time_ranges_json,
    )


@mcp.tool()
def schedule_query(
    view: str = "current",
    timezone: str = "Asia/Shanghai",
    target_date: str = "",
    auto_calibrate: bool = True,
) -> dict[str, Any]:
    """
    统一查询课表信息。

    view:
    - current: 当前正在上的课 + 下一节课
    - day: 某一天的课表（按星期提取，不按教学周过滤）
    - week: 兼容旧客户端，返回未按教学周过滤的完整周课表
    - full: 最新完整课表

    参数：
    - target_date: 当 view=day 时使用，格式 YYYY-MM-DD；为空时默认今天

    规则：
    1) 禁止凭记忆输出课程信息，必须调用本工具。
    2) 回复仅可基于本次返回的课程数据。
    """
    normalized_view = str(view or "current").strip().lower()
    if normalized_view == "current":
        return _current_course_impl(timezone=timezone, auto_calibrate=auto_calibrate)
    if normalized_view == "day":
        return _day_schedule_impl(target_date=target_date, timezone=timezone)
    if normalized_view == "week":
        return _week_schedule_compat_impl()
    if normalized_view == "full":
        return _latest_schedule_impl()
    return {"success": False, "msg": "view 仅支持 current/day/week/full"}


@mcp.tool()
def set_calibration_source(
    data: str,
    cookie: str,
    user_agent: str = "KingoPalm/2.6.449 (iPhone; iOS 26.3; Scale/3.00)",
) -> dict[str, Any]:
    """
    更新 xiqueer 校准源（抓包得到的 data/cookie）。
    """
    save_result = set_xiqueer_calibration_request(
        data=data,
        cookie=cookie,
        user_agent=user_agent,
    )
    if not save_result.get("success"):
        return save_result

    test_result = test_xiqueer_period_time_request()
    return {
        "success": bool(test_result.get("success")),
        "msg": "校准源已更新",
        "config": save_result,
        "test": test_result,
    }


@mcp.tool()
def system_status(timezone: str = "Asia/Shanghai") -> dict[str, Any]:
    """
    查看系统状态：账号、时间、节次配置、最近校准状态、输出文件。

    【执行协议（必须遵守）】
    1) 当用户提到"现在/今天/明天/当前/待签到/是否过期"等相对时间时，必须先调用本工具。
    2) 在查询图书馆或研讨室预约前，也必须先调用本工具确认当前日期时间。
    3) 回复中的时间判断只允许基于本次返回的 `server_time`。
    """
    return {
        "success": True,
        "server_time": get_server_time(timezone=timezone),
        "account": show_account(),
        "period_time_config": get_period_time_config(),
        "library_defaults": {
            "location": _resolve_library_defaults()[0],
            "seat_no": _resolve_library_defaults()[1],
        },
        "library_cookie_file": str(LIBRARY_COOKIE_FILE),
        "seminar_signin_tasks": _seminar_signin_tasks_impl(),
        "recent_output_files": list_output_files(limit=10),
    }


# ==================== 河宝社区 MCP Tools ====================

@mcp.tool()
def yunfz_leave_query(
    view: str = "list",
    leave_id: str = "",
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """
    查询河宝社区请假信息。

    view:
    - list: 请假记录列表
    - detail: 请假详情（需传 leave_id）
    - statistics: 任务统计

    规则：
    1) 查询前必须先调用 system_status 确认当前日期时间。
    2) 回复仅可基于本次返回结果，不得编造。
    """
    if HenuCampusBot is None:
        return {"success": False, "msg": f"河宝社区模块不可用: {CAMPUS_CORE_EXPECTED_FILE}", "records": []}

    profile = _effective_profile()
    sid, pwd = str(profile.get("student_id", "")), str(profile.get("password", ""))
    if not sid or not pwd:
        return {"success": False, "msg": "缺少账号", "records": []}

    bot = _build_hebao_bot(sid, pwd)
    if not bot:
        return _hebao_login_failed({"records": []})

    normalized_view = str(view or "list").strip().lower()

    if normalized_view == "list":
        result = bot.get_leave_list(student_no=sid, page=page, page_size=page_size)
        token = bot.get_hebao_token()
        if token:
            _save_hebao_token(token)
        return result

    if normalized_view == "detail":
        if not str(leave_id or "").strip():
            return {"success": False, "msg": "view=detail 时 leave_id 不能为空"}
        result = bot.get_leave_detail(leave_id=leave_id)
        token = bot.get_hebao_token()
        if token:
            _save_hebao_token(token)
        return result

    if normalized_view == "statistics":
        result = bot.get_task_statistics()
        token = bot.get_hebao_token()
        if token:
            _save_hebao_token(token)
        return result

    return {"success": False, "msg": "view 仅支持 list/detail/statistics", "records": []}


@mcp.tool()
def yunfz_signin_query(
    view: str = "list",
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """
    查询河宝社区签到任务。

    view:
    - list: 签到任务列表
    - statistics: 任务统计

    规则：
    1) 查询前必须先调用 system_status 确认当前日期时间。
    """
    if HenuCampusBot is None:
        return {"success": False, "msg": f"河宝社区模块不可用: {CAMPUS_CORE_EXPECTED_FILE}", "records": []}

    profile = _effective_profile()
    sid, pwd = str(profile.get("student_id", "")), str(profile.get("password", ""))
    if not sid or not pwd:
        return {"success": False, "msg": "缺少账号", "records": []}

    bot = _build_hebao_bot(sid, pwd)
    if not bot:
        return _hebao_login_failed({"records": []})

    normalized_view = str(view or "list").strip().lower()

    if normalized_view == "list":
        result = bot.get_signin_task_list(page=page, page_size=page_size)
        token = bot.get_hebao_token()
        if token:
            _save_hebao_token(token)
        return result

    if normalized_view == "statistics":
        result = bot.get_task_statistics()
        token = bot.get_hebao_token()
        if token:
            _save_hebao_token(token)
        return result

    return {"success": False, "msg": "view 仅支持 list/statistics", "records": []}


@mcp.tool()
def yunfz_checksleep_query(
    view: str = "list",
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """
    查询河宝社区查寝任务。

    view:
    - list: 查寝任务列表
    - statistics: 任务统计

    规则：
    1) 查询前必须先调用 system_status 确认当前日期时间。
    """
    if HenuCampusBot is None:
        return {"success": False, "msg": f"河宝社区模块不可用: {CAMPUS_CORE_EXPECTED_FILE}", "records": []}

    profile = _effective_profile()
    sid, pwd = str(profile.get("student_id", "")), str(profile.get("password", ""))
    if not sid or not pwd:
        return {"success": False, "msg": "缺少账号", "records": []}

    bot = _build_hebao_bot(sid, pwd)
    if not bot:
        return _hebao_login_failed({"records": []})

    normalized_view = str(view or "list").strip().lower()

    if normalized_view == "list":
        result = bot.get_checksleep_task_list(page=page, page_size=page_size)
        token = bot.get_hebao_token()
        if token:
            _save_hebao_token(token)
        return result

    if normalized_view == "statistics":
        result = bot.get_task_statistics()
        token = bot.get_hebao_token()
        if token:
            _save_hebao_token(token)
        return result

    return {"success": False, "msg": "view 仅支持 list/statistics", "records": []}


@mcp.tool()
def yunfz_activity_query(
    view: str = "list",
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """
    查询河宝社区活动信息。

    view:
    - list: 活动列表
    - statistics: 任务统计

    规则：
    1) 查询前必须先调用 system_status 确认当前日期时间。
    """
    if HenuCampusBot is None:
        return {"success": False, "msg": f"河宝社区模块不可用: {CAMPUS_CORE_EXPECTED_FILE}", "records": []}

    profile = _effective_profile()
    sid, pwd = str(profile.get("student_id", "")), str(profile.get("password", ""))
    if not sid or not pwd:
        return {"success": False, "msg": "缺少账号", "records": []}

    bot = _build_hebao_bot(sid, pwd)
    if not bot:
        return _hebao_login_failed({"records": []})

    normalized_view = str(view or "list").strip().lower()

    if normalized_view == "list":
        result = bot.get_activity_list(page=page, page_size=page_size)
        token = bot.get_hebao_token()
        if token:
            _save_hebao_token(token)
        return result

    if normalized_view == "statistics":
        result = bot.get_task_statistics()
        token = bot.get_hebao_token()
        if token:
            _save_hebao_token(token)
        return result

    return {"success": False, "msg": "view 仅支持 list/statistics", "records": []}


@mcp.tool()
def yunfz_collection_query(
    view: str = "list",
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """
    查询河宝社区信息收集任务。

    view:
    - list: 信息收集列表
    - statistics: 任务统计

    规则：
    1) 查询前必须先调用 system_status 确认当前日期时间。
    """
    if HenuCampusBot is None:
        return {"success": False, "msg": f"河宝社区模块不可用: {CAMPUS_CORE_EXPECTED_FILE}", "records": []}

    profile = _effective_profile()
    sid, pwd = str(profile.get("student_id", "")), str(profile.get("password", ""))
    if not sid or not pwd:
        return {"success": False, "msg": "缺少账号", "records": []}

    bot = _build_hebao_bot(sid, pwd)
    if not bot:
        return _hebao_login_failed({"records": []})

    normalized_view = str(view or "list").strip().lower()

    if normalized_view == "list":
        result = bot.get_collection_list(page=page, page_size=page_size)
        token = bot.get_hebao_token()
        if token:
            _save_hebao_token(token)
        return result

    if normalized_view == "statistics":
        result = bot.get_task_statistics()
        token = bot.get_hebao_token()
        if token:
            _save_hebao_token(token)
        return result

    return {"success": False, "msg": "view 仅支持 list/statistics", "records": []}


# ── 空教室查询 ──────────────────────────────────────────────


def _get_empty_classroom_client() -> Any:
    """获取已认证的 EmptyClassroomClient（复用教务登录态）。"""
    if EmptyClassroomClient is None:
        return None

    sid, pwd = _resolve_account("", "", use_saved_account=True)
    if not sid:
        return None

    xk_client = HenuXkClient(sid, pwd, saved_cookies=_load_xk_cookies() or None)
    if not xk_client.login():
        return None

    _save_xk_cookies(xk_client.get_cookies())
    return EmptyClassroomClient(xk_client)


def _resolve_campus_and_building(
    campus_text: str,
    building_text: str = "",
    classroom_text: str = "",
) -> dict[str, Any]:
    """通过资源 registry 将自然语言校区/楼房名解析为代码。

    Returns:
        成功: {"campus_code": "01", "campus_name": "...", "building_code": "...", ...}
        失败: {"success": False, "msg": "...", "candidates": [...]}
    """
    if not _resource_registry_available:
        return {"success": False, "msg": "资源 registry 不可用，请使用 campus_code/building_code"}

    # 预加载 seed 数据
    try:
        from campus_core.resource_registry import preload_seed_if_needed
        preload_seed_if_needed()
    except Exception:
        pass

    # 组合查询文本
    query_parts = [campus_text]
    if building_text:
        query_parts.append(building_text)
    if classroom_text:
        query_parts.append(classroom_text)
    full_query = " ".join(query_parts)

    # 1. 先解析校区
    campus_code = ""
    campus_name = ""
    if campus_text:
        result = resolve_resource(campus_text, resource_type="campus", limit=3)
        campus_candidates = result.get("candidates", [])
        if campus_candidates and campus_candidates[0].get("score", 0) >= 0.7:
            # 通过 resource_id 解析 campus_code
            rid = campus_candidates[0]["resourceId"]
            parts = rid.split(":")
            if len(parts) >= 3:
                campus_code = parts[2]
            campus_name = campus_candidates[0].get("displayName", "")
        else:
            # 返回候选让用户选
            return {
                "success": False,
                "msg": f"未找到匹配校区: '{campus_text}'，请从候选中选择",
                "candidates": campus_candidates,
            }

    # 2. 再解析楼房（可选）
    building_code = ""
    building_name = ""
    if building_text and campus_code:
        result = resolve_resource(
            building_text,
            resource_type="building",
            campus_code=campus_code,
            limit=5,
        )
        building_candidates = result.get("candidates", [])
        if building_candidates and building_candidates[0].get("score", 0) >= 0.5:
            rid = building_candidates[0]["resourceId"]
            parts = rid.split(":")
            if len(parts) >= 4:
                building_code = parts[3]
            building_name = building_candidates[0].get("displayName", "")
        else:
            return {
                "success": False,
                "msg": f"在{campus_name}中未找到匹配楼房: '{building_text}'",
                "candidates": building_candidates,
            }

    # 3. 教室文本暂不解析（由 keyword 参数处理）
    if classroom_text and not building_text:
        # 尝试从教室名直接解析楼房
        pass

    return {
        "campus_code": campus_code,
        "campus_name": campus_name,
        "building_code": building_code,
        "building_name": building_name,
    }


@mcp.tool()
def empty_classroom_query(
    view: str = "free",
    term_code: str = "",
    week: int = 0,
    day_of_week: int = 0,
    period: int = 0,
    campus_code: str = "",
    building_code: str = "",
    campus_text: str = "",
    building_text: str = "",
    classroom_text: str = "",
    type_code: str = "",
    min_capacity: int = 0,
    keyword: str = "",
    room_id: str = "",
    freshness: str = "cache_first",
    force_refresh: bool = False,
    ttl_seconds: int = 300,
    max_stale_seconds: int = 86400,
) -> dict[str, Any]:
    """
    查询空教室 / 教室信息 / 校区楼房列表

    数据来源：学校教务系统教室课表。仅反映学校系统中已登记的课程/占用信息，
    不包含未录入系统的临时占用。

    view 模式：
    - "free": 查询指定时间的空闲教室
    - "day_matrix": 查询某天所有大节的空闲教室矩阵
    - "occupancy": 查询单个教室某时间段的占用详情
    - "terms": 查询可用学期列表
    - "campuses": 查询校区列表
    - "buildings": 查询楼房列表（需 campus_code）
    - "classrooms": 查询教室列表
    - "types": 查询教室类型列表

    参数优先级（校区/楼房）：
    - campus_code/building_code 显式传入时优先
    - 否则使用 campus_text/building_text 通过资源 registry 解析
    - 解析失败时返回候选列表供用户选择

    freshness 策略：
    - "cache_first"（默认）: 未过期用缓存，过期再刷新
    - "live": 每次请求都尝试刷新上游
    - "stale_while_revalidate": 先返回旧缓存，同时标记需要刷新
    - "cache_only": 只查缓存，不访问学校系统
    """
    if EmptyClassroomClient is None or query_free_classrooms is None:
        return {
            "success": False,
            "msg": "空教室模块未加载，请检查 campus_core/empty_classroom/ 是否完整",
        }

    # ── 文本参数解析 ──
    resolution: dict[str, Any] = {}
    if not campus_code and campus_text:
        resolution = _resolve_campus_and_building(campus_text, building_text, classroom_text)
        if not resolution.get("campus_code"):
            # 解析失败，返回候选列表
            return resolution
        campus_code = resolution["campus_code"]
        if not building_code and resolution.get("building_code"):
            building_code = resolution["building_code"]

    client = _get_empty_classroom_client()
    if client is None:
        return {
            "success": False,
            "msg": "无法登录教务系统，请先调用 setup_account 配置学号和密码",
        }

    # ── 无登录态也能查的 view ──
    if view in ("terms", "campuses", "buildings", "classrooms", "types"):
        return _empty_classroom_meta_query(client, view, campus_code, building_code, type_code, keyword)

    # ── 需要登录态的 view ──
    if view == "free":
        if not term_code or week <= 0 or day_of_week <= 0 or period <= 0:
            return {
                "success": False,
                "msg": "view=free 需要提供 term_code, week, day_of_week, period",
            }

        result = query_free_classrooms(
            client=client,
            term_code=term_code,
            week=week,
            day_of_week=day_of_week,
            period=period,
            campus_code=campus_code,
            building_code=building_code,
            type_code=type_code,
            min_capacity=min_capacity,
            keyword=keyword,
            freshness=freshness,
            force_refresh=force_refresh,
            ttl_seconds=ttl_seconds,
            max_stale_seconds=max_stale_seconds,
        )
        data = result.to_dict()
        data["success"] = True
        data["msg"] = f"找到 {result.total} 间空闲教室"
        return data

    if view == "day_matrix":
        if not term_code or week <= 0 or day_of_week <= 0:
            return {
                "success": False,
                "msg": "view=day_matrix 需要提供 term_code, week, day_of_week",
            }

        periods_result: list[dict[str, Any]] = []
        for p in (1, 2, 3, 4, 5):
            result = query_free_classrooms(
                client=client,
                term_code=term_code,
                week=week,
                day_of_week=day_of_week,
                period=p,
                campus_code=campus_code,
                building_code=building_code,
                type_code=type_code,
                min_capacity=min_capacity,
                keyword=keyword,
                freshness=freshness,
                force_refresh=force_refresh,
                ttl_seconds=ttl_seconds,
                max_stale_seconds=max_stale_seconds,
            )
            period_name_map = {1: "第一大节", 2: "第二大节", 3: "第三大节", 4: "第四大节", 5: "第五大节"}
            periods_result.append(
                {
                    "period": p,
                    "periodName": period_name_map.get(p, ""),
                    "total": result.total,
                    "rooms": [
                        {
                            "roomId": r.get("roomId", ""),
                            "roomName": r.get("roomName", ""),
                            "capacity": r.get("capacity", 0),
                        }
                        for r in result.rooms
                    ],
                }
            )

        return {
            "success": True,
            "msg": f"已查询 {len(periods_result)} 个大节",
            "data": {
                "termCode": term_code,
                "week": week,
                "dayOfWeek": day_of_week,
                "periods": periods_result,
            },
        }

    if view == "occupancy":
        # 查询单个教室指定时间段的占用详情
        if not term_code or not room_id or week <= 0 or day_of_week <= 0 or period <= 0:
            return {
                "success": False,
                "msg": "view=occupancy 需要提供 term_code, room_id, week, day_of_week, period",
            }
        return _empty_classroom_occupancy_query(
            client, term_code, room_id, week, day_of_week, period,
            campus_code, building_code, freshness, force_refresh, ttl_seconds, max_stale_seconds,
        )

    return {"success": False, "msg": f"不支持的 view: {view}"}


def _empty_classroom_meta_query(
    client: Any,
    view: str,
    campus_code: str,
    building_code: str,
    type_code: str,
    keyword: str,
) -> dict[str, Any]:
    """处理 terms/campuses/buildings/classrooms/types 等元数据查询。"""
    if view == "terms":
        terms = client.fetch_terms()
        return {
            "success": True,
            "msg": f"共 {len(terms)} 个学期",
            "data": [t.to_dict() for t in terms],
        }

    if view == "campuses":
        campuses = client.fetch_campuses()
        return {
            "success": True,
            "msg": f"共 {len(campuses)} 个校区",
            "data": [c.to_dict() for c in campuses],
        }

    if view == "buildings":
        if not campus_code:
            # 返回所有校区的楼房
            all_buildings: list[dict[str, Any]] = []
            campuses = client.fetch_campuses()
            for campus in campuses:
                buildings = client.fetch_buildings(campus.campus_code)
                for b in buildings:
                    all_buildings.append(b.to_dict())
            return {
                "success": True,
                "msg": f"共 {len(all_buildings)} 个楼房",
                "data": all_buildings,
            }
        buildings = client.fetch_buildings(campus_code)
        return {
            "success": True,
            "msg": f"校区 {campus_code} 共 {len(buildings)} 个楼房",
            "data": [b.to_dict() for b in buildings],
        }

    if view == "classrooms":
        classrooms = client.fetch_classrooms(
            campus_code=campus_code,
            building_code=building_code,
            type_code=type_code,
        )
        # 补充 campusName/buildingName
        campus_map = client.get_campus_map()
        building_map = client.get_building_map(campus_code) if campus_code else {}
        result_list: list[dict[str, Any]] = []
        for c in classrooms:
            d = c.to_dict()
            if campus_code and campus_code in campus_map:
                d["campusName"] = campus_map[campus_code]
            if building_code and building_code in building_map:
                d["buildingName"] = building_map[building_code]
            if keyword and keyword not in d.get("roomName", ""):
                continue
            result_list.append(d)
        return {
            "success": True,
            "msg": f"共 {len(result_list)} 个教室",
            "data": result_list,
        }

    if view == "types":
        types = client.fetch_room_types()
        return {
            "success": True,
            "msg": f"共 {len(types)} 种教室类型",
            "data": [t.to_dict() for t in types],
        }

    return {"success": False, "msg": f"不支持的 view: {view}"}


def _empty_classroom_occupancy_query(
    client: Any,
    term_code: str,
    room_id: str,
    week: int,
    day_of_week: int,
    period: int,
    campus_code: str,
    building_code: str,
    freshness: str,
    force_refresh: bool,
    ttl_seconds: int,
    max_stale_seconds: int,
) -> dict[str, Any]:
    """查询单个教室某时间段的占用详情。"""
    # 先拉取课表数据
    result = query_free_classrooms(
        client=client,
        term_code=term_code,
        week=week,
        day_of_week=day_of_week,
        period=period,
        campus_code=campus_code,
        building_code=building_code,
        freshness=freshness,
        force_refresh=force_refresh,
        ttl_seconds=ttl_seconds,
        max_stale_seconds=max_stale_seconds,
    )

    # 从缓存中查找该教室的课程
    from campus_core.empty_classroom.storage import load_parsed_schedule

    parsed = load_parsed_schedule(term_code, campus_code, building_code)
    cells = parsed.get("cells", []) if parsed else []
    classrooms = parsed.get("classrooms", []) if parsed else []

    # 找到教室名
    room_name = room_id
    for cr in classrooms:
        if cr.get("room_id") == room_id:
            room_name = cr.get("room_name", room_id)
            break

    # 筛选该教室该时间段的课程
    courses: list[dict[str, Any]] = []
    for cell_dict in cells:
        if cell_dict.get("roomId") != room_id and cell_dict.get("room_id") != room_id:
            # 也按 roomName 匹配
            if cell_dict.get("roomName", "") != room_name and cell_dict.get("room_name", "") != room_name:
                continue
        cell_day = cell_dict.get("dayOfWeek", cell_dict.get("day_of_week", 0))
        cell_period = cell_dict.get("period", 0)
        if cell_day == day_of_week and cell_period == period:
            weeks_list = cell_dict.get("weeks", cell_dict.get("week_bitmap", []))
            if week in weeks_list:
                courses.append(
                    {
                        "courseName": cell_dict.get("courseName", cell_dict.get("course_name", "")),
                        "teacherName": cell_dict.get("teacherName", cell_dict.get("teacher_name", "")),
                        "weekExpr": cell_dict.get("weekExpr", cell_dict.get("week_expr", "")),
                        "weeks": weeks_list,
                        "sectionText": cell_dict.get("sectionText", cell_dict.get("section_text", "")),
                        "className": cell_dict.get("className", cell_dict.get("class_name", "")),
                        "studentCount": cell_dict.get("studentCount", cell_dict.get("student_count", 0)),
                        "departmentName": cell_dict.get("departmentName", cell_dict.get("department_name", "")),
                        "rawText": cell_dict.get("rawText", cell_dict.get("raw_text", "")),
                    }
                )

    status = "occupied" if courses else "free"
    return {
        "success": True,
        "msg": "该时段有课程占用" if courses else "该时段空闲",
        "data": {
            "roomId": room_id,
            "roomName": room_name,
            "week": week,
            "dayOfWeek": day_of_week,
            "period": period,
            "status": status,
            "courses": courses,
        },
    }


@mcp.tool()
def empty_classroom_sync(
    term_code: str = "",
    campus_code: str = "",
    building_code: str = "",
    type_code: str = "",
    force_refresh: bool = False,
) -> dict[str, Any]:
    """
    同步指定楼房的教室课表缓存

    从学校教务系统拉取教室课表 HTML，解析后写入共享缓存。
    建议在查询空教室前先同步相关楼房数据。

    参数：
    - term_code: 学期代码，如 "2025,1"（必填）
    - campus_code: 校区代码，如 "01"（必填）
    - building_code: 楼房代码，如 "0013"（必填）
    - type_code: 教室类型代码，空表示全部
    - force_refresh: 是否强制刷新

    返回同步状态和统计信息。
    """
    if EmptyClassroomClient is None or _sync_schedule_impl is None:
        return {
            "success": False,
            "msg": "空教室模块未加载，请检查 campus_core/empty_classroom/ 是否完整",
        }

    if not term_code or not campus_code or not building_code:
        return {
            "success": False,
            "msg": "term_code, campus_code, building_code 均为必填参数",
        }

    client = _get_empty_classroom_client()
    if client is None:
        return {
            "success": False,
            "msg": "无法登录教务系统，请先调用 setup_account 配置学号和密码",
        }

    result = _sync_schedule_impl(
        client=client,
        term_code=term_code,
        campus_code=campus_code,
        building_code=building_code,
        type_code=type_code,
        force_refresh=force_refresh,
    )

    data = result.to_dict()
    data["success"] = result.sync_status in ("success", "skipped")
    data["msg"] = {
        "success": "同步成功",
        "skipped": "缓存未过期，已跳过",
        "failed": f"同步失败: {result.error}",
    }.get(result.sync_status, "")
    return data


# ── 全局资源编号映射 ──────────────────────────────────────


@mcp.tool()
def resource_registry_query(
    view: str = "search",
    query: str = "",
    resource_type: str = "",
    campus_code: str = "",
    building_code: str = "",
    limit: int = 20,
) -> dict[str, Any]:
    """
    查询全局资源编号映射

    统一查询教室、图书馆区域/座位、研讨室、楼房、校区等资源。
    支持通过校园自然语言（"十号楼101"、"明伦十号楼"、"研讨室A203"）搜索。

    view 模式：
    - "search": 按关键词搜索资源
    - "resolve": 自然语言解析，返回候选项及匹配分数
    - "list": 列出资源（可筛选类型/校区/楼房）
    - "stats": 查看 registry 统计信息
    """
    if not _resource_registry_available:
        return {
            "success": False,
            "msg": "资源 registry 模块未加载，请检查 campus_core/resource_registry/ 是否完整",
        }

    if view == "search":
        records = search_resources(
            query=query,
            resource_type=resource_type,
            campus_code=campus_code,
            building_code=building_code,
            limit=limit,
        )
        return {
            "success": True,
            "msg": f"找到 {len(records)} 条资源记录",
            "data": [r.to_dict() for r in records],
        }

    if view == "resolve":
        result = resolve_resource(
            text=query,
            resource_type=resource_type,
            campus_code=campus_code,
            limit=limit,
        )
        return result

    if view == "list":
        records = _list_registry_resources(
            resource_type=resource_type,
            campus_code=campus_code,
            building_code=building_code,
            limit=limit,
        )
        return {
            "success": True,
            "msg": f"共 {len(records)} 条资源记录",
            "data": [r.to_dict() for r in records],
        }

    if view == "stats":
        stats = _get_registry_stats()
        return {
            "success": True,
            "msg": f"共 {stats.get('total', 0)} 条资源记录",
            "data": stats,
        }

    return {"success": False, "msg": f"不支持的 view: {view}，支持 search/resolve/list/stats"}


@mcp.tool()
def resource_registry_sync(
    scope: str = "all",
    force_refresh: bool = False,
) -> dict[str, Any]:
    """
    同步资源到全局编号映射

    将上游系统（教务、图书馆、研讨室）的资源数据同步到 registry。
    同步后的数据存入 data/shared/resource_registry/。

    参数：
    - scope: "classrooms" | "library" | "seminar" | "all"
    - force_refresh: 是否强制重新拉取上游数据
    """
    if not _resource_registry_available:
        return {
            "success": False,
            "msg": "资源 registry 模块未加载，请检查 campus_core/resource_registry/ 是否完整",
        }

    results: dict[str, Any] = {}
    total_synced = 0

    if scope in ("classrooms", "all"):
        # 使用空教室客户端获取元数据
        if EmptyClassroomClient is not None:
            try:
                client = _get_empty_classroom_client()
                if client:
                    campuses = client.fetch_campuses()
                    campus_list = [{"code": c.campus_code, "name": c.campus_name} for c in campuses]

                    buildings_by_campus: dict[str, list[dict[str, str]]] = {}
                    classrooms_by_building: dict[str, list[dict[str, Any]]] = {}

                    for campus in campuses:
                        buildings = client.fetch_buildings(campus.campus_code)
                        buildings_by_campus[campus.campus_code] = [
                            {"code": b.building_code, "name": b.building_name}
                            for b in buildings
                        ]
                        for building in buildings:
                            classrooms = client.fetch_classrooms(
                                campus_code=campus.campus_code,
                                building_code=building.building_code,
                            )
                            classrooms_by_building[building.building_code] = [
                                c.to_dict() for c in classrooms
                            ]

                    result = sync_classrooms_from_metadata(
                        campuses=campus_list,
                        buildings_by_campus=buildings_by_campus,
                        classrooms_by_building=classrooms_by_building,
                    )
                    results["classrooms"] = result
                    total_synced += result.get("synced_count", 0)
                else:
                    results["classrooms"] = {"success": False, "error": "无法创建空教室客户端（需先 setup_account）"}
            except Exception as exc:
                results["classrooms"] = {"success": False, "error": str(exc)}
        else:
            results["classrooms"] = {"success": False, "error": "空教室模块未加载"}

    if scope in ("library", "all"):
        # 图书馆同步需要 library_query 的返回数据，此处先提供接口
        results["library"] = {
            "success": True,
            "msg": "图书馆资源同步需在 library_query 时增量完成，此处仅提供接口占位",
            "synced_count": 0,
        }

    if scope in ("seminar", "all"):
        # 研讨室同步需要 seminar_query 的返回数据，此处先提供接口
        results["seminar"] = {
            "success": True,
            "msg": "研讨室资源同步需在 seminar_query 时增量完成，此处仅提供接口占位",
            "synced_count": 0,
        }

    return {
        "success": True,
        "msg": f"同步完成，共 {total_synced} 条资源",
        "data": {
            "totalSynced": total_synced,
            "results": results,
        },
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HENU unified MCP server (schedule + library)")
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http", "sse"],
        default="stdio",
        help="MCP transport type",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host for HTTP transports")
    parser.add_argument("--port", type=int, default=8001, help="Port for HTTP transports")
    parser.add_argument("--path", default="/mcp", help="HTTP endpoint path for streamable-http transport")
    parser.add_argument(
        "--stateless-http",
        action="store_true",
        help="Enable stateless HTTP mode for streamable-http transport",
    )
    parser.add_argument(
        "--json-response",
        action="store_true",
        help="Enable JSON response mode for streamable-http transport",
    )
    args = parser.parse_args()

    if args.transport in ("streamable-http", "sse"):
        mcp.settings.host = args.host
        mcp.settings.port = args.port
    if args.transport == "streamable-http":
        mcp.settings.streamable_http_path = args.path
        mcp.settings.stateless_http = args.stateless_http
        mcp.settings.json_response = args.json_response

    _ensure_seminar_auto_signin_worker()
    mcp.run(transport=args.transport)

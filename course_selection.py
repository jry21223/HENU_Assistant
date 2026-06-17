from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from course_schedule import HenuXkClient, OUTPUT_DIR, load_json


NOT_OPEN_MARKERS = ("未到", "不在选课时间", "未开始", "已结束")
OPEN_MARKERS = ("可以选课", "开放")


def _now_iso() -> str:
    try:
        return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    except Exception:
        return datetime.now().isoformat()


def _preview_text(text: str, limit: int = 1200) -> str:
    return (text or "")[:limit]


def parse_selection_response(text: str) -> dict[str, Any]:
    raw = text or ""
    parsed: dict[str, Any] = {
        "raw_text": _preview_text(raw),
        "raw_json": None,
        "is_open": None,
        "message": "",
    }
    try:
        value = json.loads(raw)
        parsed["raw_json"] = value
        if isinstance(value, dict):
            candidates = [
                value.get("msg"),
                value.get("message"),
                value.get("error"),
                value.get("result"),
                value.get("data"),
            ]
            parsed["message"] = next((str(item) for item in candidates if item), "")
        else:
            parsed["message"] = str(value)[:300]
    except Exception:
        parsed["message"] = _preview_text(raw, 300)

    haystack = json.dumps(parsed["raw_json"], ensure_ascii=False) if parsed["raw_json"] is not None else raw
    if any(marker in haystack for marker in NOT_OPEN_MARKERS):
        parsed["is_open"] = False
    elif any(marker in haystack for marker in OPEN_MARKERS):
        parsed["is_open"] = True
    return parsed


class HenuCourseSelectionClient(HenuXkClient):
    @property
    def selection_menu_url(self) -> str:
        return f"{self.base_url}/frame/jw/teacherstudentmenu.jsp?menucode=S20202"

    @property
    def selection_entry_url(self) -> str:
        return f"{self.base_url}/student/wsxk.zx.html?menucode=S2020202&bqflag=1"

    def post_form(
        self,
        path: str,
        data: dict[str, Any] | None = None,
        referer: str | None = None,
        accept: str = "application/json, text/javascript, */*; q=0.01",
    ) -> dict[str, Any]:
        url = urljoin(f"{self.base_url}/", path)
        headers = {
            "Accept": accept,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
        }
        if referer:
            headers["Referer"] = referer
        resp = self.session.post(url, data=data or {}, headers=headers, timeout=30)
        text = self._decode_text(resp)
        parsed = parse_selection_response(text)
        return {
            "url": url,
            "status_code": resp.status_code,
            "final_url": resp.url,
            **parsed,
        }

    def get_selection_time_range(self, xktype: str = "2", referer: str | None = None) -> dict[str, Any]:
        return self.post_form(
            f"/jw/common/getWsxkTimeRange.action?xktype={xktype}",
            referer=referer or self.selection_entry_url,
        )

    def get_course_scope_dropdown(self, xktype: str = "2", referer: str | None = None) -> dict[str, Any]:
        return self.post_form(
            "/frame/droplist/getDropLists.action",
            data={
                "comboBoxName": "MsKcfw",
                "paramValue": str(xktype),
                "isYXB": "0",
                "isCDDW": "0",
                "isXQ": "0",
                "isDJKSLB": "0",
                "isZY": "0",
            },
            referer=referer or self.selection_entry_url,
        )

    def get_selection_status(self, xktype: str = "2") -> dict[str, Any]:
        menu = self.fetch_page(self.selection_menu_url)
        menu_referer = str(menu.get("final_url") or self.selection_menu_url)
        entry = self.fetch_page(self.selection_entry_url, referer=menu_referer)
        entry_referer = str(entry.get("final_url") or self.selection_entry_url)
        if menu.get("invalid_auth") or entry.get("invalid_auth"):
            return {
                "success": False,
                "msg": "教务登录凭证已失效，请重新通过 CAS 登录",
                "menu_url": menu.get("final_url") or self.selection_menu_url,
                "entry_url": entry.get("final_url") or self.selection_entry_url,
                "entry_status_code": entry.get("status_code"),
            }
        if menu.get("invalid_request") or entry.get("invalid_request"):
            return {
                "success": False,
                "msg": "选课入口访问被教务系统判定为无效请求",
                "menu_url": menu.get("final_url") or self.selection_menu_url,
                "entry_url": entry.get("final_url") or self.selection_entry_url,
                "entry_status_code": entry.get("status_code"),
            }
        time_range = self.get_selection_time_range(xktype=xktype, referer=entry_referer)
        dropdown = self.get_course_scope_dropdown(xktype=xktype, referer=entry_referer)
        inferred = time_range.get("is_open")
        if inferred is None:
            inferred = dropdown.get("is_open")
        message = str(time_range.get("message") or dropdown.get("message") or "")
        result = {
            "success": True,
            "xktype": str(xktype),
            "menu_url": menu.get("final_url") or self.selection_menu_url,
            "entry_url": entry.get("final_url") or self.selection_entry_url,
            "entry_status_code": entry.get("status_code"),
            "time_range": time_range,
            "dropdown": dropdown,
            "is_open": inferred,
            "message": message,
            "raw_text": {
                "time_range": time_range.get("raw_text", ""),
                "dropdown": dropdown.get("raw_text", ""),
            },
            "raw_json": {
                "time_range": time_range.get("raw_json"),
                "dropdown": dropdown.get("raw_json"),
            },
            "captured_at": _now_iso(),
        }
        _save_status_snapshot(result)
        return result


def _save_status_snapshot(result: dict[str, Any]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = OUTPUT_DIR / f"course_selection_status_{stamp}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


def build_course_selection_client(profile_path: Path, cookie_path: Path) -> HenuCourseSelectionClient | None:
    profile = load_json(profile_path)
    sid = str(profile.get("student_id", ""))
    pwd = str(profile.get("password", ""))
    if not sid:
        return None
    client = HenuCourseSelectionClient(sid, pwd, saved_cookies=load_json(cookie_path) or None)
    return client

from __future__ import annotations

import base64
import datetime as dt
import json
import math
import random
import re
import time
from html import unescape
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

from .time_utils import _now_dt, TimeUtilsMixin


class SeminarMixin:
    @staticmethod
    def _seminar_pick_day_row(rows: list[dict[str, Any]], target_date: str = "") -> dict[str, Any] | None:
        date_text = str(target_date or "").strip()
        if date_text:
            for row in rows:
                if str(row.get("date") or "") == date_text:
                    return row
        return rows[0] if rows else None

    @staticmethod
    def _seminar_clean_member_ids(member_ids: list[Any], self_id: str = "") -> list[str]:
        current_id = str(self_id or "").strip()
        seen: set[str] = set()
        cleaned: list[str] = []
        for raw in member_ids:
            text = str(raw or "").strip()
            if not text or text == current_id or text in seen:
                continue
            seen.add(text)
            cleaned.append(text)
        return cleaned

    @staticmethod
    def _seminar_content_length(content: str) -> int:
        return len(re.sub(r"\s+", "", str(content or "")))

    @staticmethod
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

    def seminar_sift_dates(self) -> dict[str, Any]:
        if not self._is_token_valid() and not self.login():
            return self._login_failed_result({"dates": []})

        try:
            resp = self._post_json("/v4/seminar/siftdate", {})
            dates = resp.get("data") or []
            if resp.get("code") != 0:
                return {"success": False, "msg": self._resp_msg(resp, "查询研讨室日期失败"), "dates": []}
            return {"success": True, "msg": self._resp_msg(resp, "操作成功"), "dates": dates}
        except Exception as exc:
            return {"success": False, "msg": f"查询研讨室日期异常: {exc}", "dates": []}

    def seminar_filter_options(self) -> dict[str, Any]:
        if not self._is_token_valid() and not self.login():
            return self._login_failed_result({"filters": {}})

        try:
            resp = self._post_json("/v4/seminar/sift", {})
            if resp.get("code") != 0:
                return {"success": False, "msg": self._resp_msg(resp, "查询研讨室筛选项失败"), "filters": {}}
            return {
                "success": True,
                "msg": self._resp_msg(resp, "操作成功"),
                "filters": resp.get("data") or {},
            }
        except Exception as exc:
            return {"success": False, "msg": f"查询研讨室筛选项异常: {exc}", "filters": {}}

    def seminar_list(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self._is_token_valid() and not self.login():
            return self._login_failed_result({"rooms": []})

        try:
            resp = self._post_json("/v4/seminar/list", payload)
            if resp.get("code") != 0:
                return {"success": False, "msg": self._resp_msg(resp, "查询研讨室列表失败"), "rooms": []}
            data = resp.get("data") or {}
            rooms = data.get("data") or []
            return {
                "success": True,
                "msg": self._resp_msg(resp, "操作成功"),
                "rooms": rooms,
                "total": int(data.get("total") or len(rooms)),
                "page": int(payload.get("page") or 1),
            }
        except Exception as exc:
            return {"success": False, "msg": f"查询研讨室列表异常: {exc}", "rooms": []}

    def seminar_detail(self, area_id: str) -> dict[str, Any]:
        if not self._is_token_valid() and not self.login():
            return self._login_failed_result()

        area_text = str(area_id or "").strip()
        if not area_text:
            return {"success": False, "msg": "area_id 不能为空"}

        try:
            resp = self._post_json("/v4/seminar/detail", {"id": area_text})
            if resp.get("code") != 0:
                return {"success": False, "msg": self._resp_msg(resp, "查询研讨室详情失败")}
            return {
                "success": True,
                "msg": self._resp_msg(resp, "操作成功"),
                "detail": resp.get("data") or {},
            }
        except Exception as exc:
            return {"success": False, "msg": f"查询研讨室详情异常: {exc}"}

    def seminar_apply_info(self, area_id: str, day: str = "") -> dict[str, Any]:
        if not self._is_token_valid() and not self.login():
            return self._login_failed_result()

        area_text = str(area_id or "").strip()
        if not area_text:
            return {"success": False, "msg": "area_id 不能为空"}

        query_day = str(day or dt.date.today().strftime("%Y-%m-%d")).strip()

        try:
            resp = self._post_json("/v4/seminar/seminar", {"id": area_text, "day": query_day})
            if resp.get("code") != 0:
                return {"success": False, "msg": self._resp_msg(resp, "查询研讨室预约信息失败")}
            data = resp.get("data") or {}
            return {
                "success": True,
                "msg": self._resp_msg(resp, "操作成功"),
                "apply_info": data,
            }
        except Exception as exc:
            return {"success": False, "msg": f"查询研讨室预约信息异常: {exc}"}

    def seminar_validate_member(
        self,
        area_id: str,
        member_id: str,
        begin_time: str,
        finish_time: str,
    ) -> dict[str, Any]:
        if not self._is_token_valid() and not self.login():
            return self._login_failed_result()

        payload = {
            "area_id": str(area_id or "").strip(),
            "member_id": str(member_id or "").strip(),
            "begin_time": str(begin_time or "").strip(),
            "finish_time": str(finish_time or "").strip(),
        }
        if not all(payload.values()):
            return {"success": False, "msg": "成员校验参数不完整"}

        try:
            resp = self._post_json("/v4/seminar/members", payload)
            return {
                "success": resp.get("code") == 0,
                "msg": self._resp_msg(resp, "成员校验失败"),
                "code": resp.get("code"),
                "member": resp.get("data") or {},
            }
        except Exception as exc:
            return {"success": False, "msg": f"成员校验异常: {exc}"}

    def reserve_seminar_room(
        self,
        area_id: str,
        target_date: str = "",
        start_time: str = "",
        end_time: str = "",
        end_date: str = "",
        title: str = "",
        title_id: str = "",
        content: str = "",
        mobile: str = "",
        member_ids: list[Any] | None = None,
        self_id: str = "",
        is_open: int = 0,
        cate_id: str = "",
        time_ranges: list[dict[str, Any]] | None = None,
        files: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        area_text = str(area_id or "").strip()
        if not area_text:
            return {"success": False, "msg": "area_id 不能为空"}

        content_text = str(content or "").strip()
        if self._seminar_content_length(content_text) <= 10:
            return {"success": False, "msg": "申请内容必须多于 10 个字"}

        mobile_text = str(mobile or "").strip()
        if not re.fullmatch(r"\d{11}", mobile_text):
            return {"success": False, "msg": "mobile 必须为 11 位手机号"}

        apply_info_result = self.seminar_apply_info(area_text, day=str(target_date or ""))
        if not apply_info_result.get("success"):
            return apply_info_result

        apply_info = apply_info_result.get("apply_info") or {}
        detail = apply_info.get("detail") or {}
        axis = apply_info.get("axis") or {}
        date_rows = axis.get("list") or []
        selected_row = self._seminar_pick_day_row(date_rows, target_date=str(target_date or ""))
        if not selected_row:
            return {"success": False, "msg": "未找到可预约日期"}

        start_date_text = str(selected_row.get("date") or target_date or "").strip()
        end_date_text = str(end_date or start_date_text).strip()
        if not start_date_text:
            return {"success": False, "msg": "无法确定预约日期"}

        detail_min_person = int(detail.get("minPerson") or 4 or 4)
        detail_max_person = int(detail.get("maxPerson") or 10 or 10)
        cleaned_members = self._seminar_clean_member_ids(member_ids or [], self_id=self_id)
        total_people = len(cleaned_members) + 1
        if total_people < detail_min_person:
            return {
                "success": False,
                "msg": f"研讨室预约至少需要 {detail_min_person} 人，当前仅 {total_people} 人",
            }
        if total_people > detail_max_person:
            return {
                "success": False,
                "msg": f"研讨室预约最多 {detail_max_person} 人，当前共 {total_people} 人",
            }

        readonly_title = str(detail.get("readonlyTitle") or "")
        title_text = str(title or "").strip()
        title_id_text = str(title_id or "").strip()
        if readonly_title == "1":
            if not title_id_text:
                return {
                    "success": False,
                    "msg": "该研讨室要求从预设主题中选择 title_id",
                    "titles": detail.get("titles") or [],
                }
        elif not title_text:
            return {"success": False, "msg": "title 不能为空"}

        earlier_periods = str(detail.get("earlierPeriods") or "")
        submit_times = list(time_ranges or [])
        if not submit_times:
            start_hhmm = self._to_hhmm(start_time or "")
            end_hhmm = self._to_hhmm(end_time or "")
            if not start_hhmm or not end_hhmm:
                return {"success": False, "msg": "start_time/end_time 不能为空"}
            if self._time_to_minutes(start_hhmm) is None or self._time_to_minutes(end_hhmm) is None:
                return {"success": False, "msg": "start_time/end_time 格式必须为 HH:MM"}
            if self._time_to_minutes(start_hhmm) >= self._time_to_minutes(end_hhmm):
                return {"success": False, "msg": "start_time 必须早于 end_time"}
            submit_times = [{"start_time": start_hhmm, "end_time": end_hhmm}]

        if earlier_periods == "3" and not str(cate_id or "").strip():
            categories = axis.get("category") or []
            if categories:
                cate_id = str(categories[0].get("id") or "")
            if not str(cate_id or "").strip():
                return {"success": False, "msg": "该研讨室预约需要 cate_id"}

        first_begin = f"{start_date_text} {submit_times[0].get('start_time', '')}".strip()
        first_finish = f"{end_date_text if len(submit_times) == 1 else start_date_text} {submit_times[0].get('end_time', '')}".strip()
        validated_members: list[dict[str, Any]] = []
        for member_id in cleaned_members:
            checked = self.seminar_validate_member(
                area_id=area_text,
                member_id=member_id,
                begin_time=first_begin,
                finish_time=first_finish,
            )
            if not checked.get("success"):
                return {
                    "success": False,
                    "msg": f"成员 {member_id} 校验失败: {checked.get('msg', '')}",
                    "member_id": member_id,
                }
            validated_members.append(checked.get("member") or {})

        payload = {
            "area_id": area_text,
            "start_date": start_date_text,
            "end_date": end_date_text,
            "title": title_text,
            "title_id": title_id_text,
            "content": content_text,
            "open": int(is_open or 0),
            "team": ",".join(cleaned_members),
            "mobile": mobile_text,
            "time": submit_times,
            "file": list(files or []),
        }
        if str(cate_id or "").strip():
            payload["cate_id"] = str(cate_id).strip()

        submit_path = "/v4/seminar/confirm" if earlier_periods == "0" else "/v4/seminar/submit"
        try:
            resp = self._post_json(submit_path, payload)
            response_data = resp.get("data") or {}
            record_id = ""
            if isinstance(response_data, dict):
                for key in ("id", "record_id", "recordId", "book_id", "bookId", "apply_id", "applyId"):
                    value = str(response_data.get(key) or "").strip()
                    if value:
                        record_id = value
                        break
            room_name = str(
                detail.get("name")
                or detail.get("enname")
                or detail.get("title")
                or apply_info.get("name")
                or ""
            ).strip()
            record_type = str(detail.get("type_id") or detail.get("typeId") or "1").strip() or "1"
            return {
                "success": resp.get("code") == 0,
                "msg": self._resp_msg(resp, "研讨室预约失败"),
                "code": resp.get("code"),
                "submit_path": submit_path,
                "record_id": record_id,
                "record_type": record_type if record_type in {"1", "2"} else "1",
                "room_name": room_name,
                "payload_summary": {
                    "area_id": area_text,
                    "start_date": start_date_text,
                    "end_date": end_date_text,
                    "time": submit_times,
                    "team_count": len(cleaned_members),
                    "total_people": total_people,
                    "cate_id": str(cate_id or ""),
                },
                "detail_summary": {
                    "room_name": room_name,
                    "type_id": str(detail.get("type_id") or detail.get("typeId") or ""),
                    "min_person": detail.get("minPerson"),
                    "max_person": detail.get("maxPerson"),
                },
                "validated_members": validated_members,
                "data": response_data,
            }
        except Exception as exc:
            return {"success": False, "msg": f"研讨室预约异常: {exc}"}

    def list_seminar_records(
        self,
        record_type: str | int = "1",
        page: int = 1,
        limit: int = 20,
        mode: str = "books",
    ) -> dict[str, Any]:
        if not self._is_token_valid() and not self.login():
            return self._login_failed_result({"records": []})

        type_value = str(record_type or "1").strip() or "1"
        if type_value not in {"1", "2"}:
            return {"success": False, "msg": "record_type 仅支持 1(普通空间) 或 2(大型空间)", "records": []}

        page_value = max(1, int(page))
        limit_value = max(1, min(100, int(limit)))
        mode_text = str(mode or "books").strip().lower()
        list_path = "/v4/seminar/books" if mode_text != "reneges" else "/v4/seminar/reneges"

        payload = {
            "type": type_value,
            "page": page_value,
            "limit": limit_value,
        }

        try:
            resp = self._post_json(list_path, payload)
            if resp.get("code") != 0:
                return {
                    "success": False,
                    "msg": self._resp_msg(resp, "查询研讨室预约记录失败"),
                    "record_type": type_value,
                    "mode": mode_text,
                    "records": [],
                }
            data = resp.get("data") or {}
            records = data.get("data") or []
            total = data.get("total")
            if total is None:
                total = len(records)
            return {
                "success": True,
                "msg": self._resp_msg(resp, "操作成功"),
                "record_type": type_value,
                "mode": mode_text,
                "page": page_value,
                "limit": limit_value,
                "total": int(total),
                "records": records,
            }
        except Exception as exc:
            return {"success": False, "msg": f"查询研讨室预约记录异常: {exc}", "records": []}

    def cancel_seminar_record(
        self,
        record_id: str | int,
    ) -> dict[str, Any]:
        if not self._is_token_valid() and not self.login():
            return self._login_failed_result()

        record_id_text = str(record_id or "").strip()
        if not record_id_text:
            return {"success": False, "msg": "record_id 不能为空"}

        cancel_path = "/v4/seminar/cancel"

        try:
            resp = self._post_json(cancel_path, {"id": record_id_text})
            return {
                "success": resp.get("code") == 0,
                "msg": self._resp_msg(resp),
                "code": resp.get("code"),
                "record_id": record_id_text,
                "cancel_path": cancel_path,
            }
        except Exception as exc:
            return {"success": False, "msg": f"取消研讨室预约异常: {exc}"}

    def sign_in_seminar_record(
        self,
        record_id: str | int,
    ) -> dict[str, Any]:
        if not self._is_token_valid() and not self.login():
            return self._login_failed_result()

        record_id_text = str(record_id or "").strip()
        if not record_id_text:
            return {"success": False, "msg": "record_id 不能为空"}

        sign_path = "/v4/seminar/signin"

        try:
            resp = self._post_json(sign_path, {"id": record_id_text})
            return {
                "success": resp.get("code") == 0,
                "msg": self._resp_msg(resp, "研讨室签到失败"),
                "code": resp.get("code"),
                "record_id": record_id_text,
                "sign_path": sign_path,
                "data": resp.get("data") or {},
            }
        except Exception as exc:
            return {"success": False, "msg": f"研讨室签到异常: {exc}"}

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


class SeatReservationMixin:
    @staticmethod
    def _normalize_points(points: dict[str, Any] | None = None) -> dict[str, Any]:
        if not isinstance(points, dict):
            return {}

        normalized: dict[str, Any] = {}
        for key in ("lat", "lng", "time"):
            value = points.get(key)
            if value in (None, ""):
                continue
            normalized[key] = value

        if ("lat" in normalized or "lng" in normalized) and "time" not in normalized:
            normalized["time"] = int(_now_dt().timestamp())

        return normalized

    @staticmethod
    def _current_record_summary(record: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": str(record.get("id") or ""),
            "type": str(record.get("type") or ""),
            "area_name": str(record.get("areaName") or record.get("nameMerge") or ""),
            "seat_no": str(record.get("no") or record.get("name") or record.get("spaceName") or ""),
            "show_time": str(record.get("showTime") or record.get("examTime") or ""),
            "status": str(record.get("status") or ""),
            "status_name": str(
                record.get("status_name")
                or record.get("statusname")
                or record.get("status_name_zh")
                or ""
            ),
            "flag_in": str(record.get("flag_in") or ""),
            "flag_leave": str(record.get("flag_leave") or ""),
        }

    @classmethod
    def _resolve_signin_action(cls, record: dict[str, Any]) -> str:
        record_type = str(record.get("type") or "")
        if record_type not in cls.SIGNIN_RECORD_TYPES:
            return ""
        if str(record.get("flag_leave") or "") == "1":
            return "return_signin"
        if str(record.get("flag_in") or "") == "1":
            return "signin"
        return ""

    def _get_space_map(self, area_id: str) -> dict[str, Any]:
        resp = self._post_json("/v4/Space/map", {"id": str(area_id)})
        if resp.get("code") != 0:
            raise RuntimeError(self._resp_msg(resp, "获取区域详情失败"))
        data = resp.get("data") or {}
        if not data:
            raise RuntimeError("区域详情为空")
        return data

    @staticmethod
    def _pick_date_row(date_list: list[dict[str, Any]], target_date: str) -> dict[str, Any] | None:
        for row in date_list:
            if str(row.get("day")) == target_date:
                return row
        for row in date_list:
            day = str(row.get("day") or "")
            if day and day >= target_date:
                return row
        return date_list[0] if date_list else None

    def _get_study_period(self, area_id: str, target_date: str) -> dict[str, Any]:
        resp = self._post_json("/v4/member/checkStudyOpenTime", {"area": str(area_id)})
        if resp.get("code") != 0:
            raise RuntimeError(self._resp_msg(resp, "获取可预约周期失败"))
        periods = resp.get("data") or []
        if not periods:
            raise RuntimeError("可预约周期为空")
        for item in periods:
            start_day = str(item.get("startDay") or "")
            end_day = str(item.get("endDay") or "")
            if start_day and end_day and start_day <= target_date <= end_day:
                return item
        return periods[0]

    def _build_reservation_plan(
        self,
        area_id: str,
        space_map: dict[str, Any],
        target_date: str,
        preferred_time: str | None = None,
        preferred_end_time: str | None = None,
    ) -> dict[str, Any]:
        space_type = str(space_map.get("type") or "")
        label_ids: list[Any] = []
        requested_start_hhmm = self._to_hhmm(preferred_time or "")
        requested_end_hhmm = self._to_hhmm(preferred_end_time or "")
        requested_start_min = self._time_to_minutes(requested_start_hhmm) if requested_start_hhmm else None
        requested_end_min = self._time_to_minutes(requested_end_hhmm) if requested_end_hhmm else None
        has_time_window = requested_end_min is not None
        if requested_start_min is not None and requested_end_min is not None and requested_start_min >= requested_end_min:
            raise RuntimeError(f"预约时间窗口无效: {requested_start_hhmm}-{requested_end_hhmm}")

        if space_type != "1":
            period = self._get_study_period(area_id, target_date)
            begdate = str(period.get("startDay") or "")
            enddate = str(period.get("endDay") or "")
            if not begdate or not enddate:
                raise RuntimeError("学习周期日期无效")
            return {
                "seat_query": {
                    "id": str(area_id),
                    "day": "",
                    "label_id": label_ids,
                    "start_time": "",
                    "end_time": "",
                    "begdate": begdate,
                    "enddate": enddate,
                },
                "confirm_path": "/v4/space/studyConfirm",
                "confirm_payload": {
                    "begdate": begdate,
                    "enddate": enddate,
                },
                "confirm_crypto": True,
                "space_type": space_type,
                "preferred_time": requested_start_hhmm,
                "preferred_end_time": requested_end_hhmm,
                "time_window": self._format_time_window(requested_start_hhmm, requested_end_hhmm),
            }

        date_cfg = space_map.get("date") or {}
        reserve_type = str(date_cfg.get("reserveType") or "")
        date_list = date_cfg.get("list") or []
        date_row = self._pick_date_row(date_list, target_date)
        if not date_row:
            raise RuntimeError(f"区域未返回 {target_date} 的开放时间")

        day = str(date_row.get("day") or target_date)
        seat_query = {
            "id": str(area_id),
            "day": day,
            "label_id": label_ids,
            "start_time": "",
            "end_time": "",
            "begdate": "",
            "enddate": "",
        }
        confirm_payload = {
            "segment": "",
            "day": day,
            "start_time": "",
            "end_time": "",
        }

        if reserve_type == "1":
            times = date_row.get("times") or []
            if not times:
                raise RuntimeError(f"{day} 未返回可预约时段")
            active_slots = [item for item in times if str(item.get("status", "1")) == "1"] or times
            first_slot = active_slots[0]
            slot_rows: list[tuple[int, int, dict[str, Any]]] = []
            for item in active_slots:
                start_min = self._time_to_minutes(item.get("start"))
                end_min = self._time_to_minutes(item.get("end"))
                if start_min is None or end_min is None:
                    continue
                slot_rows.append((start_min, end_min, item))
            if has_time_window:
                matched_slots = [
                    item
                    for start_min, end_min, item in slot_rows
                    if (requested_start_min is None or start_min >= requested_start_min)
                    and (requested_end_min is None or end_min <= requested_end_min)
                ]
                if not matched_slots:
                    available = [
                        f"{self._minutes_to_hhmm(start)}-{self._minutes_to_hhmm(end)}"
                        for start, end, _ in slot_rows
                    ]
                    raise RuntimeError(
                        f"没有满足时间窗口 {self._format_time_window(requested_start_hhmm, requested_end_hhmm)} 的可预约时段"
                        + (f"，可选时段: {', '.join(available)}" if available else "")
                    )
                first_slot = matched_slots[0]
            elif requested_start_min is not None:
                if slot_rows:
                    matched = None
                    for start_min, end_min, item in slot_rows:
                        if start_min <= requested_start_min <= end_min:
                            matched = item
                            break
                    if matched is None:
                        later = [item for start_min, _, item in slot_rows if start_min >= requested_start_min]
                        if later:
                            matched = later[0]
                        else:
                            matched = slot_rows[-1][2]
                    first_slot = matched
            seat_query["start_time"] = self._to_hhmm(first_slot.get("start"))
            seat_query["end_time"] = self._to_hhmm(first_slot.get("end"))
            confirm_payload["segment"] = str(first_slot.get("id") or "")
            if not confirm_payload["segment"]:
                raise RuntimeError("预约时段参数缺失(segment)")
        elif reserve_type == "2":
            times = date_row.get("times") or []
            if not times:
                raise RuntimeError(f"{day} 未返回可预约时点")
            time_value = times[0]
            points: list[tuple[int, Any]] = []
            for item in times:
                if isinstance(item, dict):
                    compare_hhmm = self._to_hhmm(item.get("time") or item.get("start") or item.get("end"))
                else:
                    compare_hhmm = self._to_hhmm(item)
                point_min = self._time_to_minutes(compare_hhmm)
                if point_min is None:
                    continue
                points.append((point_min, item))
            if points:
                points.sort(key=lambda x: x[0])
            if has_time_window:
                matched_points = [
                    item
                    for point_min, item in points
                    if (requested_start_min is None or point_min >= requested_start_min)
                    and (requested_end_min is None or point_min <= requested_end_min)
                ]
                if not matched_points:
                    available = [self._minutes_to_hhmm(point_min) for point_min, _ in points]
                    raise RuntimeError(
                        f"没有满足时间窗口 {self._format_time_window(requested_start_hhmm, requested_end_hhmm)} 的可预约时点"
                        + (f"，可选时点: {', '.join(available)}" if available else "")
                    )
                time_value = matched_points[0]
            elif requested_start_min is not None and points:
                exact = [item for point_min, item in points if point_min == requested_start_min]
                if exact:
                    time_value = exact[0]
                else:
                    later = [item for point_min, item in points if point_min >= requested_start_min]
                    time_value = later[0] if later else points[-1][1]
            if isinstance(time_value, dict):
                time_value = time_value.get("time") or time_value.get("start") or time_value.get("end") or ""
            hhmm = self._to_hhmm(time_value)
            if not hhmm:
                raise RuntimeError("时点预约参数缺失")
            seat_query["start_time"] = hhmm
            seat_query["end_time"] = hhmm
            confirm_payload["end_time"] = hhmm
        elif reserve_type == "3":
            start_time = self._to_hhmm(date_row.get("def_start_time") or date_row.get("start_time"))
            end_time = self._to_hhmm(date_row.get("def_end_time") or date_row.get("end_time"))
            if not start_time or not end_time:
                raise RuntimeError("预约时间参数缺失")
            start_min = self._time_to_minutes(start_time)
            end_min = self._time_to_minutes(end_time)
            if start_min is None or end_min is None:
                raise RuntimeError("预约时间参数无效")
            if has_time_window:
                selected_start = max(start_min, requested_start_min if requested_start_min is not None else start_min)
                selected_end = min(end_min, requested_end_min if requested_end_min is not None else end_min)
                if selected_start >= selected_end:
                    raise RuntimeError(
                        f"期望时间窗口 {self._format_time_window(requested_start_hhmm, requested_end_hhmm)} "
                        f"不在可预约区间 {start_time}-{end_time}"
                    )
                start_time = self._minutes_to_hhmm(selected_start)
                end_time = self._minutes_to_hhmm(selected_end)
            elif requested_start_min is not None:
                if requested_start_min < start_min or requested_start_min >= end_min:
                    raise RuntimeError(
                        f"期望时间 {requested_start_hhmm} 不在可预约区间 {start_time}-{end_time}"
                    )
                start_time = self._minutes_to_hhmm(requested_start_min)
            seat_query["start_time"] = start_time
            seat_query["end_time"] = end_time
            confirm_payload["start_time"] = start_time
            confirm_payload["end_time"] = end_time
        else:
            # 兜底：优先取 times[0]，否则取默认时间
            times = date_row.get("times") or []
            if times and isinstance(times[0], dict):
                seat_query["start_time"] = self._to_hhmm(times[0].get("start"))
                seat_query["end_time"] = self._to_hhmm(times[0].get("end"))
                confirm_payload["segment"] = str(times[0].get("id") or "")
            if not seat_query["start_time"]:
                seat_query["start_time"] = self._to_hhmm(date_row.get("def_start_time") or date_row.get("start_time"))
            if not seat_query["end_time"]:
                seat_query["end_time"] = self._to_hhmm(date_row.get("def_end_time") or date_row.get("end_time"))
            if not confirm_payload["segment"]:
                confirm_payload["start_time"] = seat_query["start_time"]
                confirm_payload["end_time"] = seat_query["end_time"]

        return {
            "seat_query": seat_query,
            "confirm_path": "/v4/space/confirm",
            "confirm_payload": confirm_payload,
            "confirm_crypto": True,
            "reserve_type": reserve_type,
            "space_type": space_type,
            "preferred_time": requested_start_hhmm,
            "preferred_end_time": requested_end_hhmm,
            "time_window": self._format_time_window(requested_start_hhmm, requested_end_hhmm),
        }

    def _query_seats(self, seat_query_payload: dict[str, Any]) -> list[dict[str, Any]]:
        resp = self._post_json("/v4/Space/seat", seat_query_payload)
        if resp.get("code") != 0:
            raise RuntimeError(self._resp_msg(resp, "查询座位失败"))
        return ((resp.get("data") or {}).get("list") or [])

    @staticmethod
    def _seat_status(seat: dict[str, Any]) -> str:
        value = seat.get("status")
        return "" if value is None else str(value)

    @classmethod
    def _seat_summary(cls, seat: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": str(seat.get("id") or ""),
            "no": str(seat.get("no") or seat.get("name") or ""),
            "name": str(seat.get("name") or ""),
            "status": cls._seat_status(seat),
        }

    def list_available_seats(
        self,
        location_name: str = "",
        area_id: str = "",
        target_date: str = "",
        preferred_time: str | None = "08:00",
        preferred_end_time: str | None = "",
    ) -> dict[str, Any]:
        target_day = str(target_date or "").strip()
        if not target_day:
            target_day = (_now_dt().date() + dt.timedelta(days=1)).strftime("%Y-%m-%d")
        try:
            dt.date.fromisoformat(target_day)
        except ValueError:
            return {
                "success": False,
                "error_code": "invalid_date",
                "source": "validation",
                "msg": "target_date 格式必须为 YYYY-MM-DD",
                "seats": [],
                "total_count": 0,
                "available_count": 0,
                "returned_count": 0,
                "truncated": False,
            }

        location = str(area_id or location_name or "").strip()
        if not location:
            return {
                "success": False,
                "error_code": "missing_location",
                "source": "validation",
                "msg": "请提供 location 或 area_id",
                "seats": [],
                "total_count": 0,
                "available_count": 0,
                "returned_count": 0,
                "truncated": False,
            }

        if not self._is_token_valid() and not self.login():
            return self._login_failed_result(
                {
                    "seats": [],
                    "error_code": "auth_failed",
                    "source": "library_auth",
                    "total_count": 0,
                    "available_count": 0,
                    "returned_count": 0,
                    "truncated": False,
                }
            )

        try:
            resolved_area_id, area_name = self._resolve_area(location, target_day)
            space_map = self._get_space_map(resolved_area_id)
            plan = self._build_reservation_plan(
                resolved_area_id,
                space_map,
                target_day,
                preferred_time=preferred_time,
                preferred_end_time=preferred_end_time,
            )
            seats = self._query_seats(plan["seat_query"])
        except Exception as exc:
            return {
                "success": False,
                "error_code": "query_failed",
                "source": "library_api",
                "msg": f"查询可用座位失败: {exc}",
                "seats": [],
                "total_count": 0,
                "available_count": 0,
                "returned_count": 0,
                "truncated": False,
            }

        status_counts: dict[str, int] = {}
        available_seats: list[dict[str, Any]] = []
        for seat in seats:
            if not isinstance(seat, dict):
                continue
            status = self._seat_status(seat)
            status_counts[status or "unknown"] = status_counts.get(status or "unknown", 0) + 1
            if status == "1":
                available_seats.append(self._seat_summary(seat))

        return {
            "success": True,
            "msg": f"查询成功，{len(available_seats)}/{len(seats)} 个座位可用",
            "area": {"id": resolved_area_id, "name": area_name},
            "target_date": target_day,
            "time_window": plan.get("time_window", ""),
            "total_count": len(seats),
            "available_count": len(available_seats),
            "returned_count": len(available_seats),
            "truncated": False,
            "seats": available_seats,
            "status_counts": status_counts,
        }

    def _find_target_seat(self, seats: list[dict[str, Any]], seat_no: str) -> dict[str, Any] | None:
        target_raw = str(seat_no).strip()
        target_norm = self._normalize_seat_no(target_raw)
        for seat in seats:
            values = [seat.get("no"), seat.get("name")]
            for raw in values:
                text = str(raw or "").strip()
                if not text:
                    continue
                if text == target_raw or self._normalize_seat_no(text) == target_norm:
                    return seat
        return None

    @classmethod
    def _normalize_record_type(cls, record_type: str | int | None) -> str:
        key = str(record_type or "1").strip().lower()
        return cls.RECORD_TYPE_ALIASES.get(key, "1")

    def list_seat_records(
        self,
        record_type: str | int = "1",
        page: int = 1,
        limit: int = 20,
    ) -> dict[str, Any]:
        if not self._is_token_valid() and not self.login():
            return self._login_failed_result({"records": []})

        page_value = max(1, int(page))
        limit_value = max(1, min(100, int(limit)))
        type_value = self._normalize_record_type(record_type)

        try:
            resp = self._post_json(
                "/v4/member/seat",
                {
                    "type": type_value,
                    "page": page_value,
                    "limit": limit_value,
                },
            )
            if resp.get("code") != 0:
                return {
                    "success": False,
                    "msg": self._resp_msg(resp, "查询预约记录失败"),
                    "record_type": type_value,
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
                "page": page_value,
                "limit": limit_value,
                "total": int(total),
                "records": records,
            }
        except Exception as exc:
            return {"success": False, "msg": f"查询预约记录异常: {exc}", "records": []}

    def list_current_appointments(self) -> dict[str, Any]:
        if not self._is_token_valid() and not self.login():
            return self._login_failed_result({"appointments": []})

        try:
            resp = self._post_json("/v4/index/subscribe", {})
            if resp.get("code") != 0:
                return {
                    "success": False,
                    "msg": self._resp_msg(resp, "查询当前预约失败"),
                    "appointments": [],
                }

            appointments = resp.get("data") or []
            if not isinstance(appointments, list):
                appointments = []

            return {
                "success": True,
                "msg": self._resp_msg(resp, "操作成功"),
                "appointments": appointments,
                "total": len(appointments),
            }
        except Exception as exc:
            return {"success": False, "msg": f"查询当前预约异常: {exc}", "appointments": []}

    def _record_matches_reservation(
        self,
        record: dict[str, Any],
        *,
        area_name: str,
        seat_no: str,
        target_date: str,
    ) -> bool:
        expected_seat = self._normalize_seat_no(seat_no)
        seat_values = [
            record.get("no"),
            record.get("name"),
            record.get("spaceName"),
            record.get("seatName"),
            record.get("seat_no"),
        ]
        seat_matched = False
        for raw in seat_values:
            text = str(raw or "").strip()
            if not text:
                continue
            if text == str(seat_no).strip() or self._normalize_seat_no(text) == expected_seat:
                seat_matched = True
                break
        if not seat_matched:
            return False

        expected_area = self._normalize_area_name(area_name)
        area_values = [
            record.get("areaName"),
            record.get("nameMerge"),
            record.get("area_name"),
            record.get("roomName"),
            record.get("libraryName"),
        ]
        area_texts = [self._normalize_area_name(item) for item in area_values if str(item or "").strip()]
        area_matched = not expected_area or not area_texts
        if not area_matched:
            area_matched = any(expected_area in item or item in expected_area for item in area_texts if item)
        if not area_matched:
            return False

        date_values = [
            record.get("day"),
            record.get("date"),
            record.get("bookDate"),
            record.get("showTime"),
            record.get("examTime"),
            record.get("time"),
        ]
        date_texts = [str(item or "") for item in date_values if str(item or "").strip()]
        if not date_texts:
            return True
        target_mmdd = target_date[5:] if len(target_date) >= 10 else target_date
        return any(target_date in item or target_mmdd in item for item in date_texts)

    def _verify_seat_reservation(
        self,
        *,
        area_name: str,
        seat_no: str,
        target_date: str,
        attempts: int = 3,
    ) -> dict[str, Any]:
        last_result: dict[str, Any] = {}
        for index in range(max(1, attempts)):
            current = self.list_current_appointments()
            last_result = current
            if current.get("success"):
                appointments = current.get("appointments") or []
                for record in appointments:
                    if isinstance(record, dict) and self._record_matches_reservation(
                        record,
                        area_name=area_name,
                        seat_no=seat_no,
                        target_date=target_date,
                    ):
                        return {
                            "verified": True,
                            "msg": "已通过当前预约反查确认",
                            "record": record,
                            "summary": self._current_record_summary(record),
                        }
            if index < max(1, attempts) - 1:
                time.sleep(0.5)

        appointments = last_result.get("appointments") if isinstance(last_result, dict) else []
        summaries = [
            self._current_record_summary(item)
            for item in (appointments or [])
            if isinstance(item, dict)
        ]
        return {
            "verified": False,
            "msg": last_result.get("msg", "未在当前预约中找到匹配记录") if isinstance(last_result, dict) else "未在当前预约中找到匹配记录",
            "appointments": summaries,
        }

    def sign_in_current_record(
        self,
        record: dict[str, Any],
        points: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self._is_token_valid() and not self.login():
            return self._login_failed_result()

        record_type = str(record.get("type") or "")
        record_id = str(record.get("id") or "").strip()
        if record_type not in self.SIGNIN_RECORD_TYPES:
            return {"success": False, "msg": f"当前记录类型不支持签到: {record_type or '未知'}"}
        if not record_id:
            return {"success": False, "msg": "当前记录缺少 id，无法签到"}

        action = self._resolve_signin_action(record)
        if not action:
            return {"success": False, "msg": "当前记录不处于可签到状态"}

        sign_path = "/v4/space/signin" if record_type == "1" else "/v4/space/studySign"
        payload = {
            "id": record_id,
            "points": self._normalize_points(points),
        }
        if record_type != "1":
            payload = {
                "seat_id": record_id,
                "points": self._normalize_points(points),
            }

        try:
            resp = self._post_json(sign_path, payload, is_crypto=True)
            return {
                "success": resp.get("code") == 0,
                "msg": self._resp_msg(resp, "签到失败"),
                "code": resp.get("code"),
                "action": action,
                "record_id": record_id,
                "record_type": record_type,
                "sign_path": sign_path,
                "record": self._current_record_summary(record),
            }
        except Exception as exc:
            return {"success": False, "msg": f"签到异常: {exc}"}

    def auto_sign_in(
        self,
        record_id: str = "",
        points: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        current = self.list_current_appointments()
        if not current.get("success"):
            return {
                "success": False,
                "msg": current.get("msg", "查询当前预约失败"),
                "appointments": current.get("appointments", []),
            }

        appointments = current.get("appointments") or []
        summaries = [self._current_record_summary(item) for item in appointments if isinstance(item, dict)]

        candidates: list[dict[str, Any]] = []
        for item in appointments:
            if not isinstance(item, dict):
                continue
            if record_id and str(item.get("id") or "").strip() != str(record_id).strip():
                continue
            if self._resolve_signin_action(item):
                candidates.append(item)

        if not candidates:
            target_text = f"记录 {record_id}" if str(record_id or "").strip() else "当前预约"
            return {
                "success": False,
                "msg": f"{target_text} 中没有可签到的座位预约",
                "appointments": summaries,
            }

        candidates.sort(
            key=lambda item: 0 if self._resolve_signin_action(item) == "return_signin" else 1
        )
        result = self.sign_in_current_record(candidates[0], points=points)
        result["appointments"] = summaries
        result["candidate_count"] = len(candidates)
        return result

    def cancel_seat_record(
        self,
        record_id: str | int,
        record_type: str | int = "1",
    ) -> dict[str, Any]:
        if not self._is_token_valid() and not self.login():
            return self._login_failed_result()

        record_id_text = str(record_id or "").strip()
        if not record_id_text:
            return {"success": False, "msg": "record_id 不能为空"}

        type_value = self._normalize_record_type(record_type)
        cancel_path = "/v4/space/cancel" if type_value == "1" else "/v4/space/studyCancel"

        try:
            resp = self._post_json(cancel_path, {"id": record_id_text})
            return {
                "success": resp.get("code") == 0,
                "msg": self._resp_msg(resp),
                "code": resp.get("code"),
                "record_id": record_id_text,
                "record_type": type_value,
                "cancel_path": cancel_path,
            }
        except Exception as exc:
            return {"success": False, "msg": f"取消预约异常: {exc}"}

    @staticmethod
    def _parse_retry_until(retry_until: str) -> dt.datetime | None:
        text = str(retry_until or "").strip()
        if not text:
            return None

        now = _now_dt()
        hhmm = self._to_hhmm(text)
        if re.fullmatch(r"\d{2}:\d{2}", hhmm):
            hour, minute = [int(part) for part in hhmm.split(":")]
            return now.replace(hour=hour, minute=minute, second=0, microsecond=0)

        normalized = text.replace("Z", "+00:00")
        try:
            parsed = dt.datetime.fromisoformat(normalized)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            try:
                parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
            except Exception:
                pass
        return parsed

    @staticmethod
    def _is_retryable_reserve_error(message: str) -> bool:
        text = str(message or "")
        non_retryable_markers = (
            "格式必须",
            "区域名称不能为空",
            "未找到座位号",
            "没有满足时间窗口",
            "预约时间窗口无效",
            "不在可预约区间",
            "不在实时可预约区域列表",
            "不在",
            "配置映射",
            "请先查询 locations",
        )
        return not any(marker in text for marker in non_retryable_markers)

    def _reserve_once(
        self,
        location_name: str,
        seat_no: str,
        target_date: str,
        preferred_time: str | None = None,
        preferred_end_time: str | None = None,
    ) -> dict[str, Any]:
        area_id, area_name = self._resolve_area(location_name, target_date)
        space_map = self._get_space_map(area_id)
        plan = self._build_reservation_plan(
            area_id,
            space_map,
            target_date,
            preferred_time=preferred_time,
            preferred_end_time=preferred_end_time,
        )
        seats = self._query_seats(plan["seat_query"])
        if not seats:
            return {
                "success": False,
                "retryable": True,
                "msg": f"区域 {area_name} 在 {target_date} 没有可查询座位",
                "area": {"id": area_id, "name": area_name},
            }

        target_seat = self._find_target_seat(seats, seat_no)
        if not target_seat:
            return {
                "success": False,
                "retryable": False,
                "msg": f"在区域 {area_name} 未找到座位号: {seat_no}",
                "area": {"id": area_id, "name": area_name},
            }

        if str(target_seat.get("status")) != "1":
            return {
                "success": False,
                "retryable": True,
                "msg": f"座位 {target_seat.get('no') or seat_no} 当前不可预约",
                "area": {"id": area_id, "name": area_name},
                "seat": {
                    "id": str(target_seat.get("id") or ""),
                    "no": str(target_seat.get("no") or target_seat.get("name") or seat_no),
                    "status": str(target_seat.get("status") or ""),
                },
                "applied_time": {
                    "preferred_time": plan.get("preferred_time", ""),
                    "preferred_end_time": plan.get("preferred_end_time", ""),
                    "time_window": plan.get("time_window", ""),
                    "start_time": (plan.get("seat_query") or {}).get("start_time", ""),
                    "end_time": (plan.get("seat_query") or {}).get("end_time", ""),
                    "reserve_type": plan.get("reserve_type", ""),
                    "space_type": plan.get("space_type", ""),
                },
            }

        confirm_payload = dict(plan["confirm_payload"])
        confirm_payload["seat_id"] = str(target_seat.get("id"))
        confirm_resp = self._post_json(
            plan["confirm_path"],
            confirm_payload,
            is_crypto=bool(plan.get("confirm_crypto")),
        )
        submit_success = confirm_resp.get("code") == 0
        response = {
            "success": submit_success,
            "submit_success": submit_success,
            "retryable": not submit_success,
            "msg": self._resp_msg(confirm_resp),
            "code": confirm_resp.get("code"),
            "area": {"id": area_id, "name": area_name},
            "seat": {
                "id": str(target_seat.get("id") or ""),
                "no": str(target_seat.get("no") or target_seat.get("name") or seat_no),
                "status": str(target_seat.get("status") or ""),
            },
            "applied_time": {
                "preferred_time": plan.get("preferred_time", ""),
                "preferred_end_time": plan.get("preferred_end_time", ""),
                "time_window": plan.get("time_window", ""),
                "start_time": (plan.get("seat_query") or {}).get("start_time", ""),
                "end_time": (plan.get("seat_query") or {}).get("end_time", ""),
                "reserve_type": plan.get("reserve_type", ""),
                "space_type": plan.get("space_type", ""),
            },
            "submit_response": {
                "code": confirm_resp.get("code"),
                "msg": self._resp_msg(confirm_resp),
                "data": confirm_resp.get("data") or {},
            },
        }
        if not submit_success:
            return response

        verification = self._verify_seat_reservation(
            area_name=area_name,
            seat_no=str(target_seat.get("no") or target_seat.get("name") or seat_no),
            target_date=target_date,
        )
        response["verification"] = verification
        if not verification.get("verified"):
            response["success"] = False
            response["retryable"] = False
            response["msg"] = f"提交接口返回成功，但反查当前预约未确认: {verification.get('msg', '')}"
        return response

    def reserve(
        self,
        location_name: str,
        seat_no: str,
        target_date: str,
        preferred_time: str | None = None,
        preferred_end_time: str | None = None,
        retry_until: str | None = None,
        retry_interval_seconds: int = 2,
        max_attempts: int = 1,
    ) -> dict[str, Any]:
        try:
            dt.date.fromisoformat(target_date)
        except ValueError:
            return {"success": False, "msg": "target_date 格式必须为 YYYY-MM-DD"}

        # 避免使用过期 token 直接进入预约流程
        if not self._is_token_valid() and not self.login():
            return self._login_failed_result()

        retry_deadline = self._parse_retry_until(str(retry_until or ""))
        if str(retry_until or "").strip() and retry_deadline is None:
            return {"success": False, "msg": "retry_until 格式必须为 HH:MM 或 ISO 日期时间"}

        try:
            interval = max(1, min(60, int(retry_interval_seconds)))
        except (TypeError, ValueError):
            interval = 2
        try:
            attempts_limit = max(1, min(120, int(max_attempts)))
        except (TypeError, ValueError):
            attempts_limit = 1
        if retry_deadline is not None and attempts_limit <= 1:
            attempts_limit = 120

        last_result: dict[str, Any] = {}
        attempts = 0
        while attempts < attempts_limit:
            if retry_deadline is not None and _now_dt() > retry_deadline:
                break
            attempts += 1
            try:
                result = self._reserve_once(
                    location_name=location_name,
                    seat_no=seat_no,
                    target_date=target_date,
                    preferred_time=preferred_time,
                    preferred_end_time=preferred_end_time,
                )
            except Exception as exc:
                message = f"预约流程异常: {exc}"
                result = {
                    "success": False,
                    "retryable": self._is_retryable_reserve_error(message),
                    "msg": message,
                }

            result["attempt"] = attempts
            result["max_attempts"] = attempts_limit
            if retry_deadline is not None:
                result["retry_until"] = retry_deadline.isoformat()
            last_result = result

            if result.get("success") or not result.get("retryable"):
                return result
            if attempts >= attempts_limit:
                break
            if retry_deadline is not None:
                remaining = (retry_deadline - _now_dt()).total_seconds()
                if remaining <= 0:
                    break
                time.sleep(max(0.0, min(float(interval), remaining)))
            else:
                time.sleep(float(interval))

        if last_result:
            last_result["success"] = False
            last_result["attempts"] = attempts
            if retry_deadline is not None and _now_dt() > retry_deadline:
                last_result["msg"] = f"{last_result.get('msg', '预约失败')}；已到达 retry_until，停止抢约"
            return last_result
        return {"success": False, "msg": "未执行预约尝试"}

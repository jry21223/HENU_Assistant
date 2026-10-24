"""Current-user Rain Classroom metadata; GET only, never enter an exam."""
from __future__ import annotations

from datetime import datetime
import re
from typing import Any
from zoneinfo import ZoneInfo

import requests

from henu_plugin.yuketang_config import VALID_DOMAINS


class CatalogError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class RainCatalog:
    def __init__(self, domain: str, cookie: str):
        if domain not in VALID_DOMAINS or not isinstance(cookie, str) or not cookie:
            raise CatalogError("rain_login_required", "请先私聊登录雨课堂，再查询课程或新增考试白名单。")
        self.domain = domain
        self._headers = {"Cookie": cookie, "User-Agent": "Mozilla/5.0",
                         "Referer": f"https://{domain}/v2/web/index"}

    def _read(self, path: str) -> dict[str, Any]:
        try:
            response = requests.get(f"https://{self.domain}{path}", headers=self._headers,
                                    timeout=10, allow_redirects=False)
            if response.status_code != 200:
                raise CatalogError("rain_query_failed", "雨课堂只读查询失败，请检查登录状态后重试。")
            payload = response.json()
        except (requests.RequestException, ValueError):
            raise CatalogError("rain_query_failed", "雨课堂只读查询失败，请检查登录状态后重试。") from None
        if not isinstance(payload, dict):
            raise CatalogError("rain_response_invalid", "雨课堂响应结构无效，未修改配置。")
        return payload

    def courses(self) -> list[dict[str, str]]:
        payload = self._read("/v2/api/web/courses/list?identity=2")
        data = payload.get("data")
        values = data.get("list") if isinstance(data, dict) else None
        if payload.get("errcode") != 0 or not isinstance(values, list):
            raise CatalogError("rain_query_failed", "无法读取雨课堂课程列表，请重新核对登录状态。")
        courses = []
        for value in values:
            if not isinstance(value, dict):
                raise CatalogError("rain_response_invalid", "课程列表结构无效，未修改配置。")
            base = value.get("course")
            name = base.get("name") if isinstance(base, dict) else None
            label = value.get("name")
            identifier = str(value.get("classroom_id") or "")
            if (not re.fullmatch(r"[0-9]+", identifier) or not isinstance(name, str)
                    or not isinstance(label, str) or not name.strip() or not label.strip()
                    or label != label.strip() or len(label) > 64
                    or any(ord(char) < 32 for char in name + label)):
                raise CatalogError("rain_response_invalid", "课程列表缺少有效班级标识，未修改配置。")
            courses.append({"course_name": name[:256], "classroom_label": label, "classroom_id": identifier})
        return courses

    def exams(self, classroom: dict[str, str], *, quizzes_only: bool = False) -> list[dict[str, Any]]:
        """List assessments; quiz operations never need another exam's cover."""
        identifier = classroom["classroom_id"]
        if not re.fullmatch(r"[0-9]+", identifier):
            raise CatalogError("rain_response_invalid", "班级标识无效。")
        payload = self._read(f"/v2/api/web/logs/learn/{identifier}?actype=5&page=0&offset=100&sort=-1")
        data = payload.get("data")
        activities = data.get("activities") if isinstance(data, dict) else None
        if payload.get("errcode") != 0 or not isinstance(activities, list):
            raise CatalogError("rain_query_failed", "考试列表查询失败，不能判断是否有考试。")
        for envelope in (payload, data):
            if (isinstance(envelope, dict) and "classroom_id" in envelope
                    and str(envelope["classroom_id"]) != identifier):
                raise CatalogError("rain_response_invalid", "测评列表所属班级无效，未继续读取。")
        records = []
        identities: set[tuple[int, str]] = set()
        for activity in activities:
            if not isinstance(activity, dict):
                raise CatalogError("rain_response_invalid", "考试列表结构无效。")
            activity_type = activity.get("type")
            if (type(activity_type) is not int or activity_type not in (4, 5)
                    or ("classroom_id" in activity and str(activity["classroom_id"]) != identifier)):
                raise CatalogError("rain_response_invalid", "测评活动类型或所属班级无效，未继续读取。")
            if quizzes_only and activity_type != 4:
                continue
            exam_id = str(activity.get("courseware_id") or "")
            status = activity.get("status")
            title = activity.get("title", "")
            if not re.fullmatch(r"[0-9]+", exam_id) or type(status) is not int or not isinstance(title, str):
                raise CatalogError("rain_response_invalid", "考试标识、状态或标题格式无效。")
            identity = (activity_type, exam_id)
            if identity in identities:
                raise CatalogError("rain_response_invalid", "同一测评活动重复，无法确认唯一目标。")
            identities.add(identity)
            is_quiz = activity_type == 4
            record: dict[str, Any] = {"assessment_type": "quiz" if is_quiz else "exam",
                                      "assessment_id": exam_id, "classroom_id": identifier,
                                      "quiz_id" if is_quiz else "exam_id": exam_id, "title": title[:256],
                                      "status": status, "status_label": {0: "尚未开始作答", 1: "正在作答"}.get(status, f"状态 {status}")}
            timing = activity
            if status in (0, 1) and not is_quiz:
                cover = self._read(f"/v/exam/cover?exam_id={exam_id}&classroom_id={identifier}")
                cover_data = cover.get("data")
                if cover.get("status") != 200 or not isinstance(cover_data, dict) or not cover_data:
                    raise CatalogError("rain_query_failed", "考试封面查询失败，不能确认考试状态。")
                if (str(cover_data.get("classroom_id")) != identifier
                        or ("classroom_id" in cover and str(cover["classroom_id"]) != identifier)
                        or cover_data.get("title") != title
                        or ("exam_id" in cover_data and str(cover_data["exam_id"]) != exam_id)):
                    raise CatalogError("rain_assessment_mismatch", "试卷与当前班级或活动不一致，未继续读取或作答。")
                record["cover_association_verified"] = "exam_id" in cover_data
                if record["cover_association_verified"]:
                    timing = cover_data
            for key in ("start_time", "deadline"):
                value = timing.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
                    try:
                        ts = value / 1000 if value > 1e12 else value
                        record[key + "_china"] = datetime.fromtimestamp(ts, ZoneInfo("Asia/Shanghai")).isoformat()
                    except (ValueError, OverflowError, OSError):
                        raise CatalogError("rain_response_invalid", "测评时间格式无效。") from None
            records.append(record)
        return records


def resolve_classroom(courses: list[dict[str, str]], target: str) -> dict[str, str]:
    matches = [course for course in courses if target in (course["course_name"], course["classroom_label"])]
    if not matches:
        raise CatalogError("rain_course_not_found", "未找到该课程或班级；请先执行 yuketang courses 核对名称。")
    if len(matches) != 1:
        raise CatalogError("rain_course_ambiguous", "同名课程有多个班级；请先执行 yuketang courses，使用带引号的准确班级标签。")
    chosen = matches[0]
    if sum(course["classroom_label"] == chosen["classroom_label"] for course in courses) != 1:
        raise CatalogError("rain_classroom_label_ambiguous", "班级标签重复，当前后台不能精确限定此班级；未修改配置。")
    return chosen

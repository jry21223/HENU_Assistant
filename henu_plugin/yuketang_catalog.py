"""Current-user Rain Classroom metadata; GET only, never enter an exam."""
from __future__ import annotations

from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import re
import shlex
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
        self.course_coverage: dict[str, Any] = {}
        self.activity_coverage: dict[str, dict[str, Any]] = {}

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
        declared_total = data.get("total")
        self.course_coverage = {"returned": len(values), "declared_total": declared_total if type(declared_total) is int else None,
            "complete": type(declared_total) is int and declared_total == len(values) and not data.get("has_more"),
            "reason": "官方课程响应未提供可核验的总数或穷尽标记"}
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
        if len({row['classroom_id'] for row in courses}) != len(courses):
            raise CatalogError("rain_response_invalid", "课程列表包含重复班级标识，未继续查询。")
        return courses

    def exams(self, classroom: dict[str, str], *, quizzes_only: bool = False,
              include_cover: bool = True) -> list[dict[str, Any]]:
        """List assessments; quiz operations never need another exam's cover."""
        identifier = classroom["classroom_id"]
        if not re.fullmatch(r"[0-9]+", identifier):
            raise CatalogError("rain_response_invalid", "班级标识无效。")
        payload = self._read(f"/v2/api/web/logs/learn/{identifier}?actype=5&page=0&offset=100&sort=-1")
        data = payload.get("data")
        activities = data.get("activities") if isinstance(data, dict) else None
        if payload.get("errcode") != 0 or not isinstance(activities, list):
            raise CatalogError("rain_query_failed", "考试列表查询失败，不能判断是否有考试。")
        declared_total = data.get("total")
        envelopes = [(payload, data)]
        page, pages = 0, {0}
        while data.get('next_page') is not None and data.get('next_page') is not False:
            next_page = data['next_page']
            if type(next_page) is not int or next_page <= page or next_page in pages or len(pages) >= 32:
                raise CatalogError('rain_response_invalid', '测评分页标识重复或无效，无法核对完整范围。')
            page = next_page
            pages.add(page)
            next_payload = self._read(f'/v2/api/web/logs/learn/{identifier}?actype=5&page={page}&offset=100&sort=-1')
            data = next_payload.get('data')
            next_rows = data.get('activities') if isinstance(data, dict) else None
            if next_payload.get('errcode') != 0 or not isinstance(next_rows, list):
                raise CatalogError('rain_query_failed', '测评分页查询失败，不能将部分结果当作完整列表。')
            if type(data.get('total')) is int:
                if type(declared_total) is int and declared_total != data['total']:
                    raise CatalogError('rain_response_invalid', '测评分页总数已变化，请重新查询。')
                declared_total = data['total']
            activities = [*activities, *next_rows]
            envelopes.append((next_payload, data))
        self.activity_coverage[identifier] = {"returned": len(activities),
            "complete": type(declared_total) is int and declared_total == len(activities) and not data.get("has_more"),
            "declared_total": declared_total if type(declared_total) is int else None,
            "reason": "活动列表未提供可核验的总数或穷尽标记"}
        for envelope in (value for pair in envelopes for value in pair):
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
            if status in (0, 1) and not is_quiz and include_cover:
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
            record.update({"course_name": classroom["course_name"], "classroom_label": classroom["classroom_label"],
                "question_count": timing.get("problem_count", activity.get("problem_count")),
                "limit_seconds": timing.get("limit", activity.get("limit")),
                "max_attempts": timing.get("max_retry"),
                "official_url": f"https://{self.domain}/v2/web/" +
                    (f"studentQuiz_web/{identifier}/{exam_id}" if is_quiz else f"exam/{identifier}/{exam_id}")})
            original_status_label = record['status_label']
            record["status_label"] = ({0: "未开始", 1: "进行中", 2: "已交卷", 3: "已结束", 4: "状态处理中"}
                if is_quiz else {0: "未开始", 1: "进行中", 2: "已结束", 3: "已交卷"}).get(status, "状态未知")
            deadline = record.get("deadline_china")
            expired = bool(deadline and datetime.fromisoformat(deadline) <= datetime.now(ZoneInfo("Asia/Shanghai")))
            record["expired"] = expired
            record["can_start"] = status == 0 and not expired
            record["can_continue"] = status == 1 and not expired
            record["availability"] = "submitted" if record["status_label"] == "已交卷" else (
                "expired" if expired or record["status_label"] == "已结束" else "in_progress" if status == 1 else "not_started" if status == 0 else "unknown")
            if expired and status in (0, 1):
                record["status_label"] = "已截止"
            record['display_status'] = record['status_label']
            record['status_label'] = original_status_label
            record["detail_command"] = f"yuketang activity show --classroom-id {identifier} --type {record['assessment_type']} --id {exam_id}"
            records.append(record)
        return records

    def account_revision(self) -> str:
        payload = self._read('/v2/api/web/userinfo')
        users = payload.get('data')
        user = users[0].get('user_id') if isinstance(users, list) and users and isinstance(users[0], dict) else None
        if payload.get('errcode') != 0 or type(user) is not int or user <= 0:
            raise CatalogError('rain_identity_unverified', '无法核实当前雨课堂登录主体，请重新登录后查询。')
        return hashlib.sha256(json.dumps([self.domain, user], separators=(',', ':')).encode()).hexdigest()

    def browse(self, *, course: str = '', page: int = 1, view: str = 'available', query: str = '') -> dict[str, Any]:
        if type(page) is not int or not 1 <= page <= 9999 or view not in {'available', 'all'}:
            raise CatalogError('rain_page_invalid', '测评列表页码或筛选条件无效。')
        account = self.account_revision()
        courses = self.courses()
        if course:
            courses = [row for row in courses if course in (row['course_name'], row['classroom_label'])]
            if not courses:
                raise CatalogError('rain_course_not_found', '未找到该课程或班级，请重新查询课程。')
        selected, omitted = courses[:128], len(courses) > 128
        activities, failures, scanned = [], [], 0
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(self.exams, row, include_cover=False): row for row in selected}
            for future in as_completed(futures):
                row = futures[future]
                try:
                    batch = future.result()
                    for activity in batch:
                        activity['status_label'] = activity['display_status']
                    activities.extend(batch)
                    scanned += 1
                except CatalogError as exc:
                    failures.append({**row, 'error_code': exc.code})
        if self.account_revision() != account:
            raise CatalogError('rain_identity_changed', '查询期间雨课堂主体已变化，请重新查询。')
        activities.sort(key=lambda row: (0 if row['can_continue'] else 1 if row['can_start'] else 2,
            row.get('deadline_china') or '9999', row['course_name'], row['classroom_id'], row['assessment_type'], row['assessment_id']))
        if view == 'available':
            activities = [row for row in activities if row['can_start'] or row['can_continue']]
        if query:
            activities = [row for row in activities if any(query in row[field] for field in ('course_name', 'classroom_label', 'title'))]
        start = (page - 1) * 5
        if page > 1 and start >= len(activities):
            raise CatalogError('rain_page_invalid', '页码超出当前测评列表，请返回第一页重新查询。')
        batch_complete = not failures and not omitted and scanned == len(courses)
        provider_complete = bool(self.course_coverage.get('complete') and all(
            self.activity_coverage.get(row['classroom_id'], {}).get('complete') for row in selected))
        coverage = {'total': len(courses), 'scanned': scanned, 'failed': failures,
            'returned_courses': self.course_coverage.get('returned', len(courses)),
            'complete': batch_complete and provider_complete, 'batch_complete': batch_complete,
            'provider_completeness': 'verified' if provider_complete else 'unknown',
            'reason': '' if batch_complete and provider_complete else '仅覆盖本次官方响应；未核验官方课程或活动是否还有其它分页。'}
        return dict(success=True, activities=activities[start:start + 5], page=page, page_size=5,
            total=len(activities), has_more=start + 5 < len(activities), coverage=coverage,
            account_revision=account, source='live_rain_api', view=view, course=course, query=query,
            msg='雨课堂测评列表已读取', reply_text=f'本次已查 {scanned}/{len(courses)} 个班级；找到 {len(activities)} 项，当前第 {page} 页。' +
                (' 查询范围尚未完整核验。' if not coverage['complete'] else ''))

    def activity_detail(self, classroom_id: str, kind: str, identifier: str) -> dict[str, Any]:
        from henu_plugin.yuketang_quiz import RainQuiz
        if kind not in {'quiz', 'exam'} or any(not re.fullmatch(r'[1-9][0-9]{0,17}', value) for value in (classroom_id, identifier)):
            raise CatalogError('rain_target_invalid', '测评身份无效，请从当前列表重新选择。')
        account = self.account_revision()
        matches = [row for row in self.courses() if row['classroom_id'] == classroom_id]
        if len(matches) != 1:
            raise CatalogError('rain_course_not_found', '当前雨课堂账号中未找到此班级，请重新选择。')
        classroom = matches[0]
        rows = self.exams(classroom, quizzes_only=kind == 'quiz')
        matches = [row for row in rows if row['assessment_type'] == kind and row['assessment_id'] == identifier]
        if len(matches) != 1:
            raise CatalogError('rain_target_invalid', '当前班级中未找到唯一匹配测评，请重新选择。')
        activity = dict(matches[0])
        activity['status_label'] = activity['display_status']
        backend = {key: 'unverified' for key in ('target', 'model', 'saved', 'submission')}
        if kind == 'quiz':
            client = RainQuiz(self.domain, self._headers['Cookie'], classroom, activity)
            snapshot = client.snapshot()
            activity.update({key: snapshot.quiz[key] for key in ('question_count', 'limit_seconds', 'saved_count', 'status', 'status_label')})
            if snapshot.quiz.get('deadline_china'):
                activity['deadline_china'] = snapshot.quiz['deadline_china']
                activity['expired'] = snapshot.quiz['expired']
            activity['metadata_revision'] = snapshot.quiz['metadata_revision']
            activity['can_start'] = snapshot.quiz['status'] == 0 and not activity['expired']
            activity['can_continue'] = snapshot.quiz['status'] == 1 and not activity['expired']
            if snapshot.quiz['status'] == 2:
                backend['submission'] = 'submitted'
                activity['availability'] = 'submitted'
            elif activity['expired'] or snapshot.quiz['status'] == 3:
                activity['availability'] = 'expired'
                if snapshot.quiz['status'] in (0, 1):
                    activity['status_label'] = '已截止'
            else:
                activity['availability'] = {0: 'not_started', 1: 'in_progress'}.get(snapshot.quiz['status'], 'unknown')
            activity['display_status'] = activity['status_label']
            if snapshot.quiz['status'] == 1:
                backend['saved'] = 'verified' if snapshot.quiz['saved_count'] == snapshot.quiz['question_count'] else 'partial'
        elif activity['status_label'] == '已交卷':
            backend['submission'] = 'submitted'
        if self.account_revision() != account:
            raise CatalogError('rain_identity_changed', '读取详情期间登录主体已变化，请重新选择。')
        result = dict(success=True, activity=activity, account_revision=account, backend=backend,
            source='live_rain_api', official_url=activity['official_url'], msg='雨课堂测评详情已核验',
            reply_text=f"{activity['course_name']} / {activity['classroom_label']} · {activity['title']}：{activity['status_label']}。后台处理状态未核验；本次只读。")
        if kind == 'quiz' and activity['can_start']:
            result['start_command'] = (f"yuketang quiz start --course {shlex.quote(classroom['classroom_label'])} "
                f"--classroom-id {classroom_id} --quiz-id {identifier} --revision {activity['metadata_revision']}")
        return result


def resolve_classroom(courses: list[dict[str, str]], target: str, *, classroom_id: str = '') -> dict[str, str]:
    if classroom_id:
        matches = [row for row in courses if row['classroom_id'] == classroom_id and target in (row['course_name'], row['classroom_label'])]
        if len(matches) != 1:
            raise CatalogError('rain_course_not_found', '当前账号、课程和班级编号不匹配，请重新选择测评。')
        return matches[0]
    matches = [course for course in courses if target in (course["course_name"], course["classroom_label"])]
    if not matches:
        raise CatalogError("rain_course_not_found", "未找到该课程或班级；请先执行 yuketang courses 核对名称。")
    if len(matches) != 1:
        raise CatalogError("rain_course_ambiguous", "同名课程有多个班级；请先执行 yuketang courses，使用带引号的准确班级标签。")
    chosen = matches[0]
    if sum(course["classroom_label"] == chosen["classroom_label"] for course in courses) != 1:
        raise CatalogError("rain_classroom_label_ambiguous", "班级标签重复，当前后台不能精确限定此班级；未修改配置。")
    return chosen

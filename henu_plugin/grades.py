"""Read-only Kingo grades, adapted from sin0317/henu-cli (MIT).

Source 3b2054c34d61d91260729eea250e6e5715aadc98; see LICENSE.henu-cli.
School requests and field meanings were independently checked on 2026-10-29.
"""
from __future__ import annotations

import re
import time
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any
from zoneinfo import ZoneInfo

from lxml import html
import requests


SCOPE = {"time": "since_admission", "grades": "effective", "major": "main"}
FORM_PATH = "/student/xscj.stuckcj.jsp"
DATA_PATH = "/student/xscj.stuckcj_data10475.jsp"
HEADERS = ("序号", "课程/环节", "学分", "总学时", "类别", "修读性质", "考核方式", "成绩", "获得学分", "绩点", "学分绩点", "备注")
COURSE_CELL = re.compile(r"\[([A-Za-z0-9_-]+)\]\s*(.+)")


class GradeError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _text(node: Any) -> str:
    return " ".join("".join(node.itertext()).split())


def _number(value: str) -> float | None:
    if not re.fullmatch(r"\d+(?:\.\d+)?", value.strip()):
        return None
    result = float(value)
    return result if result < 1e9 else None


def _round(value: float) -> float:
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def parse_transcript(markup: str) -> dict[str, Any]:
    try:
        document = html.fromstring(markup)
    except (ValueError, html.etree.ParserError) as exc:
        raise GradeError("grade_page_unrecognized", "成绩页面无法识别，未将其当作空成绩。") from exc
    records = []
    totals = []
    recognized = False
    for table in document.xpath("//table"):
        rows = [[_text(cell) for cell in row.xpath("./th|./td")]
                for row in table.xpath("./tr|./thead/tr|./tbody/tr|./tfoot/tr")]
        headings = next((row for row in rows if "课程/环节" in row), None)
        if headings is None:
            grade_candidates = any(
                any(COURSE_CELL.fullmatch(cell) for cell in row)
                or (len(set(row) & set(HEADERS)) >= 3 and bool({"成绩", "绩点"} & set(row)))
                for row in rows
            )
            if grade_candidates:
                raise GradeError("grade_page_unrecognized", "部分学期的成绩表结构无法识别，未返回缺失学期的成绩。")
            summary_header = next((row for row in rows if "修读课程环节数" in row and "获得学分绩点" in row), None)
            if summary_header is not None:
                for row in rows:
                    if row and row[0] in {"合计", "总计"} and len(row) == len(summary_header):
                        values = {name: _number(value) for name, value in zip(summary_header, row) if name}
                        totals.append(values)
            continue
        if len(headings) != len(HEADERS) or set(headings) != set(HEADERS):
            raise GradeError("grade_page_unrecognized", "成绩表头发生变化，未按旧列序读取。")
        recognized = True
        columns = {name: headings.index(name) for name in HEADERS}
        for row in rows:
            if not row:
                continue
            sequence = row[columns["序号"]] if columns["序号"] < len(row) else ""
            course_like = any(COURSE_CELL.fullmatch(cell) for cell in row)
            if not sequence.isdigit() and not course_like:
                continue
            if len(row) != len(headings):
                raise GradeError("grade_page_unrecognized", "成绩明细列数异常，未返回不完整成绩。")
            if not sequence.isdigit():
                raise GradeError("grade_page_unrecognized", "成绩序号格式无法识别，未把这些课程当作空成绩。")
            def get(key: str) -> str:
                return row[columns[key]]
            course = COURSE_CELL.fullmatch(get("课程/环节"))
            if course is None:
                raise GradeError("grade_page_unrecognized", "成绩课程标识无法识别。")
            records.append({
                "code": course[1], "name": course[2], "credits": _number(get("学分")),
                "hours": _number(get("总学时")), "category": get("类别"),
                "study_type": get("修读性质"), "exam_type": get("考核方式"),
                "score": get("成绩"), "earned_credits": _number(get("获得学分")),
                "gpa": _number(get("绩点")), "credit_gpa": _number(get("学分绩点")),
                "note": get("备注"),
            })
    if not recognized:
        raise GradeError("grade_page_unrecognized", "没有找到可核对表头的成绩表，未将其当作没有成绩。")
    verified = [item for item in totals if item.get("修读课程环节数") == len(records)]
    school_total = verified[0] if len(verified) == 1 else None
    return {"records": records, "school_total": school_total}


def summarize_gpa(records: list[dict[str, Any]], total: dict[str, Any] | None) -> dict[str, Any]:
    graded = [row for row in records if row["gpa"] is not None and row["earned_credits"] is not None and row["earned_credits"] > 0]
    credits = sum(row["earned_credits"] for row in graded)
    computed = _round(sum(row["earned_credits"] * row["gpa"] for row in graded) / credits) if credits else None
    system_credits = total.get("获得学分") if total else None
    system_points = total.get("获得学分绩点") if total else None
    system = _round(system_points / system_credits) if system_points is not None and system_credits is not None and system_credits > 0 else None
    numeric = [row for row in records if _number(row["score"]) is not None and row["earned_credits"] is not None and row["earned_credits"] > 0]
    numeric_credits = sum(row["earned_credits"] for row in numeric)
    weighted = _round(sum(_number(row["score"]) * row["earned_credits"] for row in numeric) / numeric_credits) if numeric_credits else None
    system_average = total.get("加权平均成绩") if total else None
    known_credits = sum(row["earned_credits"] for row in records if row["earned_credits"] is not None)
    unknown_credits = sum(row["earned_credits"] is None for row in records)
    return {
        "average_gpa": system if system is not None else computed,
        "gpa_source": "system" if system is not None else "computed" if computed is not None else "unavailable",
        "earned_credits": system_credits if system_credits is not None else None if unknown_credits else _round(known_credits),
        "computed_gpa": computed, "computed_gpa_courses": len(graded),
        "missing_gpa_count": sum(row["gpa"] is None and row["earned_credits"] is not None and row["earned_credits"] > 0 for row in records),
        "unknown_earned_credits_count": unknown_credits,
        "weighted_average": system_average if system_average is not None else weighted,
        "weighted_average_source": "system" if system_average is not None else "computed_reference" if weighted is not None else "unavailable",
        "weighted_average_courses": len(numeric),
    }


class GradeClient:
    """Use an existing per-user session; never perform credential login here."""
    def __init__(self, client: Any, student_id: str):
        self.client = client
        self.student_id = student_id

    def _request(self, method: str, path: str, **kwargs: Any) -> str:
        try:
            response = self.client.session.request(
                method, "https://xk.henu.edu.cn" + path, timeout=20,
                allow_redirects=False, **kwargs,
            )
        except requests.RequestException as exc:
            raise GradeError("grade_service_unavailable", "教务成绩查询暂时不可用，请稍后再查。") from exc
        if response.status_code in (301, 302, 303, 307, 308, 401, 403):
            raise GradeError("grade_auth_required", "教务会话已失效，请先在私聊重新验证校园登录。")
        if response.status_code != 200 or len(response.content) > 2_000_000:
            raise GradeError("grade_service_unavailable", "教务成绩接口未返回有效页面。")
        text = self.client._decode_text(response)
        if self.client._is_auth_invalid_page(text):
            raise GradeError("grade_auth_required", "教务会话已失效，请先在私聊重新验证校园登录。")
        return text

    def read(self) -> dict[str, Any]:
        context = self._request("GET", "/frame/home/js/SetMainInfo.jsp", params={"v": int(time.time())})
        extract = self.client._extract_var
        actual = extract(context, "_loginid") or extract(context, "G_LOGIN_ID")
        if not actual or actual.lower() in {"guest", "kingo.guest"}:
            raise GradeError("grade_auth_required", "教务会话已失效，请先在私聊重新验证校园登录。")
        if actual != self.student_id:
            raise GradeError("grade_account_mismatch", "当前教务会话与绑定账号不一致，未读取成绩。")
        school = extract(context, "_schoolCode") or extract(context, "G_SCHOOL_CODE")
        if school != "10475":
            raise GradeError("grade_page_unrecognized", "未能核对河南大学成绩查询入口。")
        referer = "https://xk.henu.edu.cn/frame/stuMain.jsp"
        menu = self._request("GET", "/frame/jw/teacherstudentmenu.jsp", params={"menucode": "S403"}, headers={"Referer": referer})
        if "S40303" not in menu:
            raise GradeError("grade_page_unrecognized", "教务学业成绩入口发生变化。")
        markup = self._request("GET", FORM_PATH, params={"menucode": "S40303"}, headers={"Referer": referer})
        try:
            document = html.fromstring(markup)
            form, = document.xpath('//form[@id="ActionForm"]')
            if (form.get("method") or "").lower() != "post" or form.get("action") not in (None, "") or DATA_PATH.removeprefix("/student/") not in markup:
                raise ValueError("unsupported form")
            required = ("sjxz", "ysyx", "zx", "fx", "wz", "rxnj", "nj")
            fields = {}
            for name in required:
                node, = form.xpath('.//input[@type="hidden"][@name=$name]', name=name)
                fields[name] = node.get("value") or ""
            if any(not re.fullmatch(r"\d{4}", fields[key]) for key in ("rxnj", "nj")):
                raise ValueError("missing admission scope")
            fields.update({"sjxz": "sjxz1", "ysyx": "yxcj", "zx": "1", "fx": "0", "wz": "0"})
            for identifier, name in (("sjxz1", "sjxzS"), ("yxcj", "ysyxS"), ("zxC", "zxC"), ("xsjd1", "xsjd")):
                node, = form.xpath('.//input[@id=$identifier]', identifier=identifier)
                if node.get("name") != name:
                    raise ValueError("scope control changed")
                fields[name] = node.get("value") or "on"
        except (ValueError, html.etree.ParserError) as exc:
            raise GradeError("grade_page_unrecognized", "成绩查询表单发生变化，未猜测查询参数。") from exc
        result = parse_transcript(self._request(
            "POST", DATA_PATH, data=fields,
            headers={"Referer": "https://xk.henu.edu.cn" + FORM_PATH + "?menucode=S40303"},
        ))
        final_context = self._request("GET", "/frame/home/js/SetMainInfo.jsp", params={"v": int(time.time())})
        final_subject = extract(final_context, "_loginid") or extract(final_context, "G_LOGIN_ID")
        if final_subject != self.student_id:
            raise GradeError("grade_account_mismatch", "查询期间登录主体发生变化，未返回成绩，请重新核对校园登录。")
        result.update({"scope": dict(SCOPE), "identity_verified": True,
                       "fetched_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
                       "source_url": "https://xk.henu.edu.cn" + DATA_PATH})
        return result


def query_grades(client: Any, student_id: str, *, view: str, page: int = 1) -> dict[str, Any]:
    try:
        transcript = GradeClient(client, student_id).read()
        records = transcript.pop("records")
        total = transcript.pop("school_total")
        if view == "gpa":
            summary = summarize_gpa(records, total)
            source = {"system": "学校汇总", "computed": "按可计算明细重算", "unavailable": "未提供"}[summary["gpa_source"]]
            average = summary["average_gpa"]
            text = [f"平均学分绩点：{average if average is not None else '未知'}（{source}）",
                    f"已获学分：{summary['earned_credits'] if summary['earned_credits'] is not None else '未知'}"]
            if summary["weighted_average"] is not None:
                label = "学校加权平均成绩" if summary["weighted_average_source"] == "system" else "明细加权参考（数值成绩且已获学分）"
                text.append(f"{label}：{summary['weighted_average']}")
            if summary["missing_gpa_count"] or summary["unknown_earned_credits_count"]:
                text.append(f"缺少绩点 {summary['missing_gpa_count']} 门，已获学分未知 {summary['unknown_earned_credits_count']} 门；不按零值计算。")
            if summary["gpa_source"] == "system" and summary["computed_gpa"] is not None and abs(average - summary["computed_gpa"]) >= 0.01:
                text.append(f"明细重算 {summary['computed_gpa']}，与学校汇总不同，以学校汇总为准。")
            return {"success": True, "view": view, **transcript, **summary,
                    "total": len(records), "reply_text": "\n".join(text)}
        selected = records[(page - 1) * 8:page * 8]
        text = [f"入学以来 · 有效主修成绩，共 {len(records)} 门（第 {page} 页）"]
        text.extend(f"{item['name']}：{item['score'] or '成绩未知'}；已获学分 {item['earned_credits'] if item['earned_credits'] is not None else '未知'}" for item in selected)
        return {"success": True, "view": view, **transcript, "total": len(records),
                "page": page, "page_size": 8, "has_next": page * 8 < len(records),
                "records": selected, "reply_text": "\n".join(text)}
    except GradeError as exc:
        return {"success": False, "error_code": exc.code, "msg": str(exc), "reply_text": str(exc)}
    finally:
        client.session.close()

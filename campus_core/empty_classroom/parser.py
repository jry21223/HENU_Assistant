"""教室课表 HTML 解析器。

将上游 xk.henu.edu.cn 返回的教室课表 HTML 解析为结构化数据。

HTML 结构说明：
- 每个教室对应一个 table（id=mytable0, mytable1, ...）
- 教室名称/容量在 table 前方的描述区："教室：十号楼101(160)"
- 每个课程格子位于 div.div1 中
- div id 编码：tableIndex + dayOfWeek(1位) + period(1位)
  例："011" = table0, 星期一, 第一大节
- div 为空 → 该教室该时段空闲
- div 非空 → 有课程占用，需解析周次
"""

from __future__ import annotations

import re
from typing import Any

from lxml import html

from .models import ScheduleCell

# 课程文本解析正则
# 格式：课程名 教师名 [周次]周 节次 地点 班级 人数 开课单位
_COURSE_LINE_RE = re.compile(
    r"(.+?)\s+(\S+)\s+(\[.+?\]周(?:\s*[单双]周)?)\s+(\d+-\d+节)\s+(\S*)\s+(.+?)\s+(\d+)\s+(.+)$"
)

# 周次表达式解析
_WEEK_RANGE_RE = re.compile(r"\[([^\]]+)\]周")


def _norm(text: str) -> str:
    """规范化空白字符。"""
    return re.sub(r"\s+", " ", text or "").strip()


def _parse_weeks(week_expr: str) -> list[int]:
    """解析周次表达式为周次列表。

    Args:
        week_expr: 如 "[1-18]周"、"[1-5,7-18]周"、"[4]周"、"[1-18]周 单周"

    Returns:
        周次列表，如 [1,2,3,...,18]。
    """
    weeks: list[int] = []
    match = _WEEK_RANGE_RE.search(week_expr)
    if not match:
        return weeks

    range_text = match.group(1)  # "1-18" or "1-5,7-18" or "4"

    # 检测单双周
    odd_only = "单周" in week_expr
    even_only = "双周" in week_expr

    parts = range_text.split(",")
    for part in parts:
        part = part.strip()
        if "-" in part:
            try:
                start, end = part.split("-", 1)
                for w in range(int(start), int(end) + 1):
                    weeks.append(w)
            except ValueError:
                continue
        else:
            try:
                weeks.append(int(part))
            except ValueError:
                continue

    if odd_only:
        weeks = [w for w in weeks if w % 2 == 1]
    elif even_only:
        weeks = [w for w in weeks if w % 2 == 0]

    return sorted(set(weeks))


def _parse_course_line(line: str) -> dict[str, Any] | None:
    """解析单行课程文本。

    Args:
        line: 如 "高等数学A（二） 李鸿军 [1-18]周 1-2节 007 25软件选课6班 94 软件学院"

    Returns:
        解析后的课程信息字典，解析失败返回 None。
    """
    line = _norm(line)
    if not line:
        return None

    match = _COURSE_LINE_RE.match(line)
    if match:
        return {
            "course_name": match.group(1).strip(),
            "teacher_name": match.group(2).strip(),
            "week_expr": match.group(3).strip(),
            "section_text": match.group(4).strip(),
            "location": match.group(5).strip(),
            "class_name": match.group(6).strip(),
            "student_count": int(match.group(7)) if match.group(7).isdigit() else 0,
            "department_name": match.group(8).strip(),
            "raw_text": line,
        }

    # 容错解析：至少尝试提取周次表达式
    week_match = _WEEK_RANGE_RE.search(line)
    if not week_match:
        return None

    # 按空格分词，尝试宽松匹配
    parts = line.split()
    result: dict[str, Any] = {
        "course_name": "",
        "teacher_name": "",
        "week_expr": "",
        "section_text": "",
        "location": "",
        "class_name": "",
        "student_count": 0,
        "department_name": "",
        "raw_text": line,
    }

    # 找周次
    for i, p in enumerate(parts):
        if _WEEK_RANGE_RE.search(p):
            result["week_expr"] = p
            if i > 0:
                result["teacher_name"] = parts[i - 1] if i >= 2 else ""
                result["course_name"] = " ".join(parts[: i - 1]) if i >= 2 else ""
            if i + 1 < len(parts) and re.match(r"\d+-\d+节", parts[i + 1]):
                result["section_text"] = parts[i + 1]
            # 尝试找人数（纯数字）
            for j in range(i + 1, len(parts)):
                if parts[j].isdigit():
                    result["student_count"] = int(parts[j])
                    if j > i + 1:
                        result["class_name"] = " ".join(parts[i + 2 : j])
                    if j + 1 < len(parts):
                        result["department_name"] = parts[j + 1]
                    break
            break

    return result if result["week_expr"] else None


def _parse_div_id(div_id: str) -> tuple[int, int, int]:
    """解析 div id 为 (table_index, day_of_week, period)。

    Args:
        div_id: 如 "011"、"1011"。

    Returns:
        (table_index, day_of_week, period)
    """
    s = str(div_id).strip()
    if len(s) < 2:
        return (0, 0, 0)

    try:
        period = int(s[-1])
        day_of_week = int(s[-2])
        table_index = int(s[:-2]) if len(s) > 2 else 0
        return (table_index, day_of_week, period)
    except (ValueError, IndexError):
        return (0, 0, 0)


def _extract_classroom_info(doc: html.HtmlElement, table_index: int) -> dict[str, str]:
    """从 HTML 中提取指定 table 对应的教室信息。

    查找 table 前方的描述文本，如 "教室：十号楼101(160)"。
    """
    result = {"room_name": "", "capacity": "0"}

    # 查找 mytable{N} 的 table 元素
    tables = doc.xpath(f'//table[@id="mytable{table_index}"]')
    if not tables:
        return result

    table_el = tables[0]

    # 在 table 前面的兄弟节点或父节点中查找教室描述
    # 方式1：查找包含 "教室：" 的前导文本
    prev = table_el.getprevious()
    while prev is not None:
        text = _norm("".join(prev.xpath(".//text()")))
        room_match = re.search(r"教室[：:]\s*(.+?)\((\d+)\)", text)
        if room_match:
            result["room_name"] = room_match.group(1).strip()
            result["capacity"] = room_match.group(2)
            return result
        prev = prev.getprevious()

    # 方式2：在父节点范围内搜索
    parent = table_el.getparent()
    if parent is not None:
        parent_text = _norm("".join(parent.xpath(".//text()")))
        # 尝试在 parent 文本中匹配 "教室：xxx(NNN)"
        room_match = re.search(r"教室[：:]\s*(.+?)\((\d+)\)", parent_text)
        if room_match:
            result["room_name"] = room_match.group(1).strip()
            result["capacity"] = room_match.group(2)
            return result

    return result


def parse_schedule_html(
    html_text: str,
    term_code: str,
    campus_code: str = "",
    building_code: str = "",
) -> dict[str, Any]:
    """解析教室课表 HTML 为结构化数据。

    Args:
        html_text: 上游返回的 HTML 文本。
        term_code: 学期代码。
        campus_code: 校区代码（透传）。
        building_code: 楼房代码（透传）。

    Returns:
        {
            "term_code": str,
            "campus_code": str,
            "building_code": str,
            "classrooms": [{"room_name": str, "capacity": int}, ...],
            "cells": [ScheduleCell.to_dict(), ...],
            "parse_errors": [str, ...],
        }
    """
    result: dict[str, Any] = {
        "term_code": term_code,
        "campus_code": campus_code,
        "building_code": building_code,
        "classrooms": [],
        "cells": [],
        "parse_errors": [],
    }

    try:
        doc = html.fromstring(html_text)
    except Exception as exc:
        result["parse_errors"].append(f"HTML 解析失败: {exc}")
        return result

    # 找到所有 div.div1 元素（课表单元格）
    divs = doc.xpath('//div[contains(@class, "div1")]')
    if not divs:
        result["parse_errors"].append("未找到课表单元格 (div.div1)")
        return result

    # 收集所有 table_index → room_info 的映射
    room_info_cache: dict[int, dict[str, str]] = {}
    seen_table_indices: set[int] = set()

    for div in divs:
        div_id = div.get("id", "")
        table_index, day_of_week, period = _parse_div_id(div_id)

        if day_of_week == 0 or period == 0:
            continue

        seen_table_indices.add(table_index)

        # 获取或缓存教室信息
        if table_index not in room_info_cache:
            room_info_cache[table_index] = _extract_classroom_info(doc, table_index)

        room_info = room_info_cache[table_index]
        room_name = room_info["room_name"] or f"教室{table_index}"
        capacity = int(room_info["capacity"]) if room_info["capacity"].isdigit() else 0

        # 获取课程文本（可能包含 <br> 分隔的多门课）
        raw_html = div.text or ""
        # 获取 div 内部的 HTML（子元素拼接）
        children_html = "".join(
            html.tostring(child, encoding="unicode") for child in div
        )
        full_html = raw_html + children_html

        # 用 <br> 或换行拆分多门课
        lines = re.split(r"<br\s*/?>|\n", full_html)
        for line in lines:
            # 去除 HTML 标签，只保留文本
            clean_line = re.sub(r"<[^>]+>", "", line)
            clean_line = _norm(clean_line)
            if not clean_line:
                continue

            course_info = _parse_course_line(clean_line)
            if not course_info:
                # 有文本但解析失败 → 记录
                result["parse_errors"].append(
                    f"课程文本解析失败 [room={room_name}, day={day_of_week}, period={period}]: {clean_line[:80]}"
                )
                continue

            weeks = _parse_weeks(course_info["week_expr"])
            if not weeks:
                result["parse_errors"].append(
                    f"周次解析失败 [room={room_name}]: {course_info['week_expr']}"
                )
                continue

            cell = ScheduleCell(
                term_code=term_code,
                room_id="",  # 当前未关联上游 room_id，后续可补充
                room_name=room_name,
                day_of_week=day_of_week,
                period=period,
                week_bitmap=weeks,
                week_expr=course_info["week_expr"],
                section_text=course_info["section_text"],
                course_name=course_info["course_name"],
                teacher_name=course_info["teacher_name"],
                class_name=course_info["class_name"],
                student_count=course_info["student_count"],
                department_name=course_info["department_name"],
                raw_text=course_info["raw_text"],
            )
            result["cells"].append(cell.to_dict())

    # 收集教室列表
    for ti in sorted(seen_table_indices):
        if ti not in room_info_cache:
            room_info_cache[ti] = _extract_classroom_info(doc, ti)
        ri = room_info_cache[ti]
        result["classrooms"].append(
            {
                "room_name": ri["room_name"] or f"教室{ti}",
                "capacity": int(ri["capacity"]) if ri["capacity"].isdigit() else 0,
                "table_index": ti,
            }
        )

    return result

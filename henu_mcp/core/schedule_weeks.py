"""Filter timetable variants by the target date's verified teaching week."""
from __future__ import annotations

import re

from henu_mcp.core.teaching_calendar import teaching_week


def course_weeks(course: dict) -> set[int] | None:
    weeks = course.get('weeks')
    if isinstance(weeks, list):
        if weeks and all(type(n) is int and 1 <= n <= 60 for n in weeks):
            return set(weeks)
        return None
    explicit = next((str(course[key]) for key in ('week_range', 'weekExpr', 'weeks', 'week') if course.get(key) not in (None, '', [])), '')
    text = explicit or str(course.get('time') or '')
    text = re.sub(r'\s+', '', text).translate(str.maketrans({'，': ',', '、': ',', '～': '-', '~': '-', '—': '-', '–': '-', '至': '-', '（': '(', '）': ')'}))
    if explicit and re.fullmatch(r'\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*', text):
        text += '周'
    # The time field appends weekday/period/location after the week rule.
    # Validate the entire rule, so unknown qualifiers cannot silently disappear.
    if not explicit:
        text = re.split(r'(?:星期)?[一二三四五六日天][\[【]', text, maxsplit=1)[0]
    match = re.fullmatch(r'(\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*)(?:\(?([单双])\)?)?周(?:\(?([单双])周?\)?)?', text)
    if match is None:
        return None
    expression, before, after = match.groups()
    if before and after and before != after:
        return None
    values = set()
    for part in expression.split(','):
        ends = [int(n) for n in part.split('-')]
        start, end = ends[0], ends[-1]
        if not 1 <= start <= end <= 60:
            return None
        values.update(range(start, end + 1))
    parity = before or after
    return {n for n in values if not parity or n % 2 == int(parity == '单')}


def filter_courses(courses: list, target) -> dict:
    calendar = teaching_week(target)
    if not calendar.get('available'):
        return {'success': False, 'error_code': 'teaching_week_unavailable', 'teaching_week': calendar,
                'schedule': [], 'week_filter_applied': False,
                'msg': '无法从已核验校历确认目标日期的教学周，未返回全量课程；请管理员核对校历。'}
    selected, unknown = [], 0
    for course in courses:
        weeks = course_weeks(course) if isinstance(course, dict) else None
        if weeks is None:
            unknown += 1
        elif calendar['week'] in weeks:
            selected.append(course)
    count = len(selected)
    msg = f"第{calendar['week']}教学周，匹配 {count} 项课程。"
    if unknown:
        msg += f'{unknown} 项课程周次无法识别，未计入；不能据此确认当天无课。'
    return {'success': True, 'teaching_week': calendar, 'schedule': selected,
            'schedule_count': count, 'unknown_course_count': unknown,
            'week_filter_applied': True, 'week_filter_complete': unknown == 0, 'msg': msg}

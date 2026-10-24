"""QQ-safe presentation of existing campus results; no business side effects."""
from __future__ import annotations

import json
import re
import time
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from langbot_plugin.api.entities.builtin.platform.message import At, MessageChain, Plain, Source

from henu_plugin.cli import redact_cli_command

PREFIX = "# 河大助手 · "
MAX_BYTES = 3600
MAX_PAGES = 20
PAGE_TTL = 600
PRIVATE_NOTICE = "个人信息仅在私聊中展示。请私聊机器人继续此操作。"


def is_page_request(text: str) -> bool:
    return bool(re.fullmatch(r"下一页|上一页|卡片第\s*[1-9]\d{0,2}\s*页", text))


def page_request_text(chain: list) -> str:
    if any(not isinstance(item, (Source, Plain, At)) for item in chain):
        return ""  # Attachments are new input, never a read-only control.
    text = "".join(item.text for item in chain if isinstance(item, Plain)).strip()
    return text if is_page_request(text) else ""

# Every service dispatch route has a deliberate presentation title.
TOOL_TITLES = {
    "henu_cli": "使用帮助", "setup_account": "校园账号绑定",
    "verify_saved_login": "校园账号验证",
    "preferences": "图书馆偏好",
    "sync_schedule": "课表更新", "schedule_query": "我的课表",
    "smart_course_selection": "选课方案", "smart_course_select": "选课提交",
    "course_selection_query": "教务选课", "course_selection_plan": "选课方案",
    "course_selection_submit": "选课提交", "course_monitor_config": "选课监控设置",
    "course_monitor_once": "选课监控", "course_monitor_run": "选课监控",
    "course_monitor_notify_test": "监控通知测试", "library_query": "图书馆座位",
    "library_reserve": "座位预约", "library_auto_signin": "座位签到",
    "library_cancel": "取消座位预约", "seminar_group": "研讨室成员组",
    "seminar_query": "研讨室", "seminar_reserve": "研讨室预约",
    "seminar_signin": "研讨室签到", "seminar_cancel": "取消研讨室预约",
    "set_calibration_source": "节次校准", "system_status": "我的状态",
    "yunfz_leave_query": "河宝请假", "yunfz_signin_query": "河宝签到",
    "yunfz_checksleep_query": "河宝查寝", "yunfz_activity_query": "河宝活动",
    "yunfz_collection_query": "河宝信息收集", "empty_classroom_query": "空教室",
    "empty_classroom_sync": "空教室数据同步", "resource_registry_query": "校园资源",
    "resource_registry_sync": "校园资源同步", "yuketang_status": "雨课堂状态",
    "yuketang_courses": "雨课堂课程", "yuketang_exam_list": "雨课堂考试列表",
    "yuketang_config_show": "雨课堂配置", "yuketang_set_enabled": "雨课堂开关",
    "yuketang_domain_set": "雨课堂域名", "yuketang_lesson_set": "雨课堂课堂设置",
    "yuketang_exam_set": "雨课堂考试设置", "yuketang_list_update": "雨课堂课程范围",
    "yuketang_start_time": "雨课堂进班时间", "yuketang_set_token": "雨课堂令牌绑定",
    "yuketang_account_set": "雨课堂账号绑定", "yuketang_login": "雨课堂登录",
    "yuketang_logout": "雨课堂退出登录",
}


def escape(value: Any, limit: int = 500) -> str:
    """Bound and neutralize external Markdown, mentions and control characters."""
    text = str(value if value is not None else "")
    text = "".join(c for c in text if c == "\n" or unicodedata.category(c) not in {"Cc", "Cf"})
    if re.search(r"(?i)--(?:password|passwd|cookie|data|token|authorization|api[-_]?key|x[-_]?access[-_]?token)\b", text):
        text = redact_cli_command(text)
    text = re.sub(r"(?i)\b(password|cookie|access_token|authorization|secret)\s*[:=]\s*\S+", r"\1=已隐藏", text)
    text = text.replace("<", "＜").replace(">", "＞").replace("@", "＠")
    text = re.sub(r"([\\`*_{}\[\]()#!|~])", r"\\\1", text)
    return text[:limit] + ("…" if len(text) > limit else "")


def _first(item: dict, *keys: str) -> str:
    return next((str(item[k]) for k in keys if item.get(k) not in (None, "", [], {}) and not isinstance(item[k], (dict, list))), "")


def public_spec(spec: Any) -> bool:
    if spec.is_help:
        return True
    tool, params = spec.resolved_tool or "", spec.params
    return (
        tool in {"empty_classroom_query", "empty_classroom_sync", "resource_registry_query", "resource_registry_sync"}
        or tool == "library_query" and params.get("view") in {"locations", "seats"}
        or tool == "seminar_query" and params.get("view") in {"rooms", "filters", "detail"}
    )


@dataclass(frozen=True)
class Card:
    title: str
    status: str
    lines: tuple[str, ...]
    next_step: str = ""

    def pages(self) -> list[str]:
        header = [PREFIX + escape(self.title, 60), ""]
        if self.status and self.status not in {'已完成', '服务回执', '需要查询地点', '确认校区'}:
            header += ["**" + escape(self.status, 70) + "**", ""]
        suffix = ["", escape(self.next_step, 160)] if self.next_step else []
        full = "\n".join(header + list(self.lines) + suffix)
        if len(full.encode()) <= MAX_BYTES:
            return [full]
        paging = '第 20/20 页 · 回复“下一页”“上一页”或“卡片第 N 页”'
        notice = '最多展示 20 页，其余内容请缩小查询范围后重新查询。'
        chunks: list[list[str]] = [[]]
        budget = MAX_BYTES - len("\n".join(header + suffix + [paging, notice]).encode()) - 16
        for raw in self.lines:
            line = raw
            # Never split an escape pair or silently cut one large paragraph.
            while len(line.encode("utf-8")) > budget:
                end = min(budget, len(line))
                while len(line[:end].encode("utf-8")) > budget:
                    end -= 1
                boundaries = list(re.finditer(r"[\n，。；！？,;!? ]", line[:end]))
                if boundaries:
                    end = boundaries[-1].end()
                trailing = len(line[:end]) - len(line[:end].rstrip("\\"))
                if trailing % 2:
                    end -= 1
                chunks.append([line[:end]])
                line = line[end:]
            if len(("\n".join(chunks[-1]) + "\n" + line).encode("utf-8")) > budget:
                chunks.append([])
            chunks[-1].append(line)
        chunks = [chunk for chunk in chunks if chunk] or [[]]
        omitted = len(chunks) > MAX_PAGES
        chunks = chunks[:MAX_PAGES]
        pages = []
        for number, chunk in enumerate(chunks, 1):
            lines = header + chunk + suffix
            if len(chunks) > 1:
                lines += ["", f"第 {number}/{len(chunks)} 页 · 回复“下一页”“上一页”或“卡片第 N 页”"]
            if omitted:
                lines += ["最多展示 20 页，其余内容请缩小查询范围后重新查询。"]
            pages.append("\n".join(lines))
        return pages


def _row(item: Any) -> str:
    if not isinstance(item, dict):
        return "- " + escape(item, 250)
    name = _first(item, "course", "course_name", "courseName", "nameMerge", "room_name", "roomName", "classroomName", "seat_no", "seatNo", "no", "name", "title", "taskName", "displayName", "campusName", "buildingName", "typeName", "termName", "location", "group_name", "resource_id", "id") or ("选课方案" if "plan" in item or "courses" in item else "条目")
    fields = [
        ("时间", _first(item, "show_time", "showTime", "time", "time_range", "time_summary", "sectionText", "clock_start", "begin_time", "start_time", "startTime", "sign_at", "period")),
        ("结束", _first(item, "clock_end", "end_time", "endTime")),
        ("位置", _first(item, "classroom", "place", "room", "location", "campusName", "buildingName", "area_name", "areaName")),
        ("状态", _first(item, "status_name", "statusName", "status_text", "status", "state")),
        ("编号", _first(item, "area_id", "areaId", "record_id", "recordId", "roomId", "resourceId", "section_id", "course_code", "campusCode", "buildingCode", "typeCode", "termCode", "id")),
        ("人数/容量", _first(item, "member_count", "capacity")),
        ("教学周", _first(item, "weekExpr", "week_range", "week")),
        ("价格", _first(item, "price_reference", "price")),
        ("营业时间", _first(item, "hours_reference")),
        ("评分/档位", _first(item, "tier", "score")),
    ]
    slots = item.get("available_slots")
    if isinstance(slots, list):
        fields.append(("可预约", "；".join(_first(s, "label") or (_first(s, "start_time") + "–" + _first(s, "end_time")) for s in slots[:3] if isinstance(s, dict)) or "无可预约时段"))
    parts = [f"{label}：{escape(value, 100)}" for label, value in fields if value]
    return "- **" + escape(name, 100) + "**" + ("\n  " + " · ".join(parts) if parts else "")



def _schedule_row_lines(item: Any) -> list[str]:
    if not isinstance(item, dict):
        return _row_lines(item)
    name = _first(item, "course", "course_name", "name") or "未命名课程"
    period = _first(item, "period")
    start, end = _first(item, "clock_start"), _first(item, "clock_end")
    clock = f"{start}–{end}" if start and end else ""
    location = _first(item, "location", "classroom", "place") or "未标注"
    # Keep numeric suffixes faithfully; do not guess whether they denote capacity.
    location = location.translate(str.maketrans("[]()", "［］（）"))
    raw = _first(item, "time")
    weeks = re.search(r"\d+(?:-\d+)?(?:[,，]\d+(?:-\d+)?)*周(?:[（(]?[单双]周[）)]?)?", raw)
    lines = ["- **" + escape(name, 100) + "**"]
    lines.append("  " + escape(period.replace("-", "–") or "节次未标注") + (" · " + escape(clock) if clock else ""))
    lines.append("  地点：" + escape(location, 160))
    if weeks:
        lines.append("  周次：" + escape(weeks.group().replace("-", "–").translate(str.maketrans("()", "（）"))))
    return ["\n".join(lines)]


def _row_lines(item: Any, depth: int = 0) -> list[str]:
    lines = [_row(item)]
    if not isinstance(item, dict):
        return lines
    for key, label in (("review_text", "锐评"), ("reason", "推荐理由")):
        if isinstance(item.get(key), str):
            lines.append(f"{label}：{escape(item[key], 4000)}")
    if depth >= 3:
        return lines
    for key in ("plan", "courses", "selected_courses", "options", "dishes"):
        value = item.get(key)
        if isinstance(value, list):
            for row in value:
                lines.extend(_row_lines(row, depth + 1))
    for meeting in item.get("meetings", []) if isinstance(item.get("meetings"), list) else []:
        if isinstance(meeting, dict):
            weekday = _first(meeting, "weekday", "weekday_name", "day")
            periods = meeting.get("periods") if isinstance(meeting.get("periods"), list) else []
            weeks = meeting.get("weeks") if isinstance(meeting.get("weeks"), list) else []
            label = f"星期{weekday} · 第{'、'.join(str(p) for p in periods)}节 · 第{'、'.join(str(w) for w in weeks)}周 · {_first(meeting, 'room', 'location')}"
            lines.append(escape(label, 500))
    for block in item.get("time_blocks", []) if isinstance(item.get("time_blocks"), list) else []:
        if isinstance(block, dict):
            label = " · ".join(_first(block, *keys) for keys in (("weekday_name",), ("section_text",), ("week_range",), ("location",)) if _first(block, *keys))
            lines.append(escape(label, 500))
    return lines


LIST_FIELDS = ("locations", "seats", "rooms", "records", "tasks", "appointments", "courses", "exams", "items", "plans", "course_options", "candidates", "groups", "data", "commands", "tips", "examples", "titles", "categories", "date_options", "date_rows")


def _week_schedule_card(result: dict) -> Card:
    from datetime import date, timedelta

    calendar = result.get("teaching_week") or {}
    try:
        monday = date.fromisoformat(calendar.get("week_start_date", ""))
    except (ValueError, TypeError):
        monday = None
    partial = bool(result.get("unknown_course_count"))
    lines = [escape(result.get("msg") or f"第{calendar.get('week', '未知')}教学周")]
    schedule = result["schedule"]
    for offset, day in enumerate(("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")):
        target = monday + timedelta(days=offset) if monday else None
        heading = "## " + day + (f" · {target.month}月{target.day}日" if target else "")
        rows = schedule.get(day) or []
        block = heading
        def order(row):
            match = re.search(r"\d+", str(row.get("period") or "")) if isinstance(row, dict) else None
            return int(match.group()) if match else 999
        for row in sorted(rows, key=order):
            if not isinstance(row, dict):
                entry = _row(row)
            else:
                period = _first(row, "period") or "节次未标注"
                name = _first(row, "course", "course_name", "name") or "未命名课程"
                location = (_first(row, "location", "classroom", "place") or "地点未标注").translate(str.maketrans("[]()", "［］（）"))
                entry = "**" + escape(period.replace("-", "–"), 50) + " · " + escape(name, 100) + "**\n" + escape(location, 160)
            # Keep a weekday heading with every continuation block.
            if len((block + "\n\n" + entry).encode("utf-8")) > 1900:
                lines.append(block)
                block = heading + "（续）"
            block += "\n\n" + entry
        if not rows:
            block += "\n" + ("无已确认课程（有周次待核验）" if partial else "当天无课")
        lines.append(block + "\n")
    return Card("本周课表", "有课程周次未确定" if partial else "", tuple(lines), "发送“今天课表”或“明天课表”看单日安排。")


def _without_query_resource_ids(value):
    """Copy presentation data without internal IDs; keep physical room/seat names."""
    hidden = {"id", "area_id", "areaId", "resource_id", "resourceId", "room_id", "roomId",
              "seat_id", "seatId", "section_id", "course_code", "campusCode", "buildingCode", "typeCode"}
    if isinstance(value, dict):
        return {key: _without_query_resource_ids(item) for key, item in value.items() if key not in hidden}
    if isinstance(value, list):
        return [_without_query_resource_ids(item) for item in value]
    return value


def _classroom_query_card(result: dict) -> Card:
    if not result.get('_qq_full_classroom_list'):
        from henu_plugin.classroom_map import classroom_summary
        return classroom_summary(result, image_ok=bool(result.get('_qq_classroom_image')),
                                 image_failed=bool(result.get('_qq_classroom_image_failed')))
    from henu_plugin.classroom_map import verified, classroom_summary
    if not verified(result):
        return classroom_summary(result)
    query = result.get('query') if isinstance(result.get('query'), dict) else {}
    freshness = result.get('freshness') if isinstance(result.get('freshness'), dict) else {}
    scope = []
    if query.get('week'):
        scope.append('第' + str(query['week']) + '周')
    day = query.get('dayOfWeek')
    if type(day) is int and 1 <= day <= 7:
        scope.append('星期' + '一二三四五六日'[day - 1])
    period = query.get('periodName') or ('第' + str(query['period']) + '大节' if query.get('period') else '')
    if period:
        scope.append(str(period))
    location = ' · '.join(str(query[key]) for key in ('campusName', 'buildingName') if query.get(key))
    lines = [escape(' · '.join(scope + ([location] if location else [])))] if scope or location else []
    stale = bool(result.get('stale') or result.get('is_stale') or freshness.get('stale'))
    rooms = [] if stale else [room for room in result['rooms'] if isinstance(room, dict)]
    if stale:
        lines.append('缓存已过期，未将其中教室列为可用；请重新查询。')
        if freshness.get('fetchedAt'):
            lines.append('上次核验：' + escape(str(freshness['fetchedAt'])[:10], 20))
        if freshness.get('liveChecked') is False:
            lines.append('本次未请求上游重新核验。')
    elif not rooms:
        lines.append('这次没有查到空教室。')
    else:
        block = ''
        for room in rooms:
            name = _first(room, 'roomName', 'room_name', 'classroomName', 'name') or '未标名教室'
            capacity = room.get('capacity')
            row = '- **' + escape(name, 100) + '**'
            if type(capacity) is int and capacity > 0:
                row += ' · ' + str(capacity) + '座'
            if len((block + '\n' + row).encode()) > 1800:
                lines.append(block)
                block = ''
            block += ('\n' if block else '') + row
        if block:
            lines.append(block)
    if rooms:
        lines.append('按教务课表筛选，临时占用以现场为准。')
    status = '数据已过期' if stale else (str(len(rooms)) + '间空教室' if rooms else '')
    return Card('空教室', status, tuple(lines))


def _classroom_day_card(result: dict) -> Card:
    data = result['data']
    query = result.get('query') if isinstance(result.get('query'), dict) else {}
    lines = []
    if _first(data, 'roomName'):
        lines.append('查询教室：' + escape(data['roomName'], 100))
    if not _first(data, 'date'):
        lines.append('日期待核验；以下仅按所列学期、教学周和星期查询。')
    for label, keys in (('日期', ('date', 'target_date')), ('校区', ('campusName',)), ('楼宇', ('buildingName',))):
        value = _first(data, *keys)
        if label != '日期':
            value = value or _first(query, *keys)
        if value:
            lines.append(f'**{label}**：{escape(value, 100)}')
    for key, label in (('termName', '学期'), ('week', '教学周'), ('dayOfWeek', '星期')):
        if _first(data, key):
            lines.append(f'**{label}**：{escape(_first(data, key), 60)}')
    lines.append('按第一至第五大节分别查看课程占用；同一教室的状态可随时段变化。')
    if data.get('clockVerified'):
        lines.append('时间按机器人已保存并核对的开封校区作息换算。')
    pending = False
    full = bool(result.get('_qq_full_classroom_list'))
    examples_left = 3
    from henu_plugin.classroom_map import verified as classroom_verified
    for slot in data['periods']:
        if not isinstance(slot, dict):
            continue
        name = escape(slot.get('periodName') or f"第{slot.get('period')}大节", 30)
        if data.get('clockVerified') and slot.get('clockStart') and slot.get('clockEnd'):
            name = escape(slot['clockStart'] + '–' + slot['clockEnd'], 30)
        else:
            name += '（时刻待核验）'
        proof = slot.get('freshness') if isinstance(slot.get('freshness'), dict) else {}
        age, ttl = proof.get('ageSeconds'), proof.get('ttlSeconds')
        expired = proof.get('stale') is True or (
            type(age) in (int, float) and type(ttl) in (int, float) and age > ttl)
        missing = proof.get('cacheHit') is False
        verified = classroom_verified(slot) and not result.get('partial')
        rooms = [r for r in slot.get('rooms', []) if isinstance(r, dict)]
        if missing or expired or proof.get('partial') is True or slot.get('partial') or result.get('partial'):
            reason = '暂无缓存' if missing else '缓存已过期' if expired else '数据不完整'
            lines.append(f'**{name}** · 待核验（{reason}）')
            pending = True
        elif not verified:
            lines.append(f'**{name}** · 来源时效待核验')
            if rooms and examples_left:
                examples = rooms[:examples_left]
                examples_left -= len(examples)
                references = '、'.join(escape(_first(r, 'roomName', 'room_name') or '未标名教室', 70) for r in examples)
                lines.append('历史记录参考：' + references + '；未列为当前可用。')
            pending = True
        else:
            count = slot.get('total')
            if type(count) is not int or count < 0:
                count = len(rooms)
            lines.append(f'**{name}** · 课程未占用 {count} 间')
            if rooms and full:
                block = ''
                for room in rooms:
                    row = '- ' + escape(_first(room, 'roomName', 'room_name') or '未标名教室', 100)
                    if len((block + '\n' + row).encode()) > 1800:
                        lines.append(block)
                        block = ''
                    block += ('\n' if block else '') + row
                if block:
                    lines.append(block)
            elif rooms and examples_left:
                examples = rooms[:examples_left]
                examples_left -= len(examples)
                lines.append('教室示例：' + '、'.join(escape(_first(r, 'roomName', 'room_name') or '未标名教室', 70) for r in examples))
        if proof.get('fetchedAt'):
            lines.append('来源获取时间：' + escape(proof['fetchedAt'], 40))
        if proof.get('refreshFailed') is True:
            lines.append('本次上游查询未完成。')
        elif proof.get('liveChecked') is False:
            lines.append('本次读取缓存，未请求上游核验。')
    if not full:
        if result.get('_qq_classroom_image_failed'):
            lines.append('示意图未能显示，仍可查看简短结果。')
        elif not result.get('_qq_classroom_image'):
            lines.append('该楼栋暂未提供可核对的位置示意图。')
        else:
            lines.append('图中仅标记已核对位置，其余房间可查看清单。')
    lines.append('仅反映学校课表已登记的占用，不保证现场无人或楼门开放。')
    return Card('全天空教室', '部分时段待核验' if pending else '全天课程占用概览', tuple(lines),
                '回复“教室清单”查看完整结果；仍可指定大节查询。' if not full else '可指定大节查询详细教室。')


def result_card(result: dict, tool: str = "henu_cli", *, private: bool = False) -> Card:
    if private:
        return Card("请转到私聊", "保护个人信息", (PRIVATE_NOTICE,), "私聊机器人后重新发送需求。")
    cli = result.get("cli") if isinstance(result.get("cli"), dict) else {}
    tool = str(cli.get("resolved_tool") or result.get("_resolved_tool_name") or tool)
    if tool == "library_query" or tool.startswith("empty_classroom"):
        result = _without_query_resource_ids(result)
    if tool == 'library_query' and result.get('success') and isinstance(result.get('seats'), list) and isinstance(result.get('area'), dict):
        day = str(result.get('target_date') or result.get('date') or '')
        clock = str(result.get('time_window') or (str(result.get('start') or '') + '–' + str(result.get('end') or '')))
        window = re.search(r'(\d{2}:\d{2}).*?(\d{2}:\d{2})', clock)
        if day and window:
            from henu_plugin.library_seat_map import seat_card
            return seat_card(result['area'], (day, *window.groups()), result, bool(result.get('_qq_seat_image')))
    if (tool == 'library_query' and result.get('success') and isinstance(result.get('locations'), list)
            and result.get('source') != 'live_empty' and not result.get('stale') and not result.get('is_stale')
            and not (isinstance(result.get('freshness'), dict) and result['freshness'].get('stale'))):
        from henu_plugin.library_availability import availability_card
        return availability_card(result)
    if tool == 'empty_classroom_query' and result.get('success') and isinstance(result.get('rooms'), list):
        return _classroom_query_card(result)
    if (tool == 'empty_classroom_query' and result.get('success')
            and isinstance(result.get('data'), dict) and isinstance(result['data'].get('periods'), list)):
        return _classroom_day_card(result)
    title = TOOL_TITLES.get(tool, "美食查询" if tool == "food" else "校园服务")
    if tool == "food":
        title = {"list": "校园美食榜", "detail": "美食详情", "mine": "我的美食投稿", "venues": "美食店铺", "create": "美食草稿"}.get(cli.get("action"), title)
    if cli.get("mode") == "help":
        title = "使用帮助" + (" · " + str(cli.get("topic")) if cli.get("topic") != "root" else "")
    if (tool == "schedule_query" and result.get("success")
            and result.get("week_filter_applied") is True
            and isinstance(result.get("schedule"), dict)):
        return _week_schedule_card(result)
    msg = str(result.get("reply_text") or result.get("msg") or "")
    schedule_reference = tool == "schedule_query" and result.get("success") and result.get("week_filter_applied") is not True
    if schedule_reference:
        title = "课表参考"
        msg = "以下按星期匹配，尚未确认这些课程在目标日期实际上课。"
    food_rows = None
    if tool == "food" and "{" in msg:
        try:
            payload, _ = json.JSONDecoder().raw_decode(msg[msg.index("{"):])
            items = payload.get("posts", payload.get("venues", [payload["post"]] if "post" in payload else []))
            food_rows = []
            for item in items if isinstance(items, list) else []:
                if not isinstance(item, dict):
                    continue
                shop = item.get("shop") if isinstance(item.get("shop"), dict) else {}
                public = {key: item[key] for key in ("review_text", "price_reference", "hours_reference", "tier", "dishes") if key in item}
                food_rows.append({**public, "name": item.get("venue_name") or item.get("name") or shop.get("name") or "美食", "location": {"jinming": "金明", "minglun": "明伦", "longzihu": "龙子湖"}.get(item.get("campus"), item.get("campus")), "status": "、".join(str(tag) for tag in item.get("tags", [])[:5])})
            msg = f"查询完成，共 {len(food_rows)} 项。"
        except (ValueError, TypeError, AttributeError):
            msg = "服务返回的数据格式暂时无法展示，请重新查询核验。"
    code = str(result.get("error_code") or "")
    freshness = result.get("freshness") if isinstance(result.get("freshness"), dict) else {}
    status = "已完成" if result.get("success") else "未完成"
    if result.get("confirmation_required"):
        status = "待本人确认 · 尚未提交"
    elif result.get("external_committed") and result.get("storage_persisted") is False:
        status = "外部已完成 · 本地保存失败"
    elif "expired" in code or "过期" in msg:
        status = "已过期 · 请重新发起"
    elif code in {"auth_required", "account_not_bound", "login_required"} or "缺少账号" in msg or "尚未绑定" in msg:
        status = "需要绑定或登录"
    elif result.get("source") == "static_fallback":
        status = "静态参考 · 未核验实时可用性"
    elif result.get("source") == "live_empty":
        status = "实时接口返回空数据 · 无法确认可用性"
    elif result.get("stale") or result.get("is_stale") or freshness.get("stale"):
        status = "缓存已过期 · 仅供参考"
    elif tool.startswith("yuketang") and result.get("bridge") == "pending":
        status = "本地已保存 · 远端待同步"
    if schedule_reference:
        status = "教学周未核验 · 仅供参考"
    lines = []
    query = result.get("resolved_query") if isinstance(result.get("resolved_query"), dict) else result.get("query", {})
    if not isinstance(query, dict):
        query = {}
    for label, keys in (("日期", ("date", "target_date")), ("位置", ("area", "location", "room_name")), ("时段", ("time_window", "preferred_time", "start_time"))):
        value = _first(result, *keys) or _first(query, *keys)
        if value:
            lines.append(f"**{label}**：{escape(value, 160)}")
    for key, label in (("now", "查询时间"), ("available_count", "可用座位"), ("total_count", "座位总数"), ("total", "总结果数"), ("record_id", "预约编号"), ("status", "服务状态"), ("monitoring_state", "监听状态"), ("bridge", "桥端状态")):
        value = _first(result, key)
        if value:
            lines.append(f"**{label}**：{escape(value, 120)}")
    for key, label in (("source", "数据来源"), ("fetchedAt", "来源获取时间"), ("ageSeconds", "缓存年龄（秒）"), ("note", "数据说明")):
        if _first(freshness, key):
            lines.append(f"**{label}**：{escape(_first(freshness, key), 300)}")
    if freshness.get("liveChecked") is False:
        lines.append("本次读取缓存，未请求上游核验。")
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    if tool.startswith("empty_classroom"):
        for key, label in (("termCode", "学期"), ("week", "教学周"), ("dayOfWeek", "星期"), ("period", "大节"), ("updatedAt", "更新时间"), ("roomCount", "教室数")):
            value = _first(result, key) or _first(data, key) or _first(query, key)
            if value:
                lines.append(f"**{label}**：{escape(value, 100)}")
        if data.get("periods") or data.get("courses") is not None:
            if not freshness:
                lines.append("此视图未返回缓存时间；仅反映已登记的课程/占用，不保证现场空闲。")
        if data.get("roomName"):
            lines.append(_row(data))
    if msg and not msg.lstrip().startswith(("{", "[")):
        lines.extend(escape(line, 900) for line in msg.splitlines() if line.strip())
    if tool == "preferences":
        preferences = result.get("preferences") or {}
        if isinstance(preferences, dict):
            lines += ["**默认区域**：" + escape(preferences.get("library_location") or "未设置"), "**默认座位**：" + escape(preferences.get("library_seat_no") or "未设置")]
    if tool == "system_status":
        wrapper = result.get("account") or {}
        account = wrapper.get("account", wrapper) if isinstance(wrapper, dict) else {}
        sid = str(account.get("student_id") or "")
        lines += ["**学号**：" + (escape(sid[:2] + "****" + sid[-2:]) if len(sid) > 4 else "未绑定")]
        lines.append("账号已保存，查询时会检查登录。" if account.get("has_password") else "还没保存校园账号，请私聊发送“绑定账号”。")
        attempt = account.get("last_login_attempt") or {}
        attempt_status = {
            "verified": "上次登录成功，当前状态以查询结果为准。", "failed": "上次登录没成功，请检查账号信息。", "unverified": "还没有登录验证记录，可私聊发送“验证登录”。",
            "pending": "上次登录还在处理中，请稍等。", "unknown": "上次登录结果不确定，请勿重复提交密码。",
        }.get(attempt.get("status")) if isinstance(attempt, dict) else None
        if attempt_status:
            lines.append(attempt_status)
        elif not attempt and account.get('has_password'):
            lines.append('还没有登录验证记录，可私聊发送“验证登录”。')
        clock = result.get("server_time") or {}
        if isinstance(clock, dict) and _first(clock, "now_text", "now_iso"):
            lines.append("**校园时间**：" + escape(_first(clock, "now_text", "now_iso")))
    # Whitelisted nested data: never dump credentials, raw provider records or
    # local file paths just because a service adds another result field.
    for section in ("filters", "statistics", "detail", "room", "area", "library_defaults", "constraints"):
        value = result.get(section)
        if isinstance(value, dict):
            safe = {key: item for key, item in value.items() if key in {"name", "room_name", "roomName", "nameMerge", "location", "seat_no", "capacity", "id", "area_id", "start_time", "end_time", "status", "available_slots", "total", "count", "finished", "pending"}}
            safe = {key: item for key, item in safe.items() if item not in (None, "", [], {})}
            if safe:
                lines.append(_row(safe))
            if section == "statistics":
                lines.extend(f"{escape(key, 60)}：{item}" for key, item in value.items() if isinstance(item, (int, float)) and not isinstance(item, bool))
            if section == "constraints":
                for key, label in (("min_person", "最少人数"), ("max_person", "最多人数"), ("readonly_title", "要求预设主题")):
                    if _first(value, key):
                        lines.append(f"{label}：{escape(_first(value, key))}")
            collections_in_section = [(key, item) for key, item in value.items() if key in {"libraries", "library", "floors", "floor", "categories", "category", "boutiques", "boutique", "items"} and isinstance(item, list)]
            for key, rows in collections_in_section:
                lines.append("**筛选项 · " + escape(key) + "**")
                for row in rows:
                    lines.extend(_row_lines(row))
    collections = []
    featured = list(result.get("current_courses") or [])
    if isinstance(result.get("next_course"), dict):
        featured.append(result["next_course"])
    for key in ("current_courses", "next_course", "day_schedule", "schedule"):
        value = result.get(key)
        if key == "day_schedule" and isinstance(value, list):
            # Clock-enriched entries and raw day entries may differ in fields.
            identities = {(item.get("course"), item.get("period"), item.get("location")) for item in featured if isinstance(item, dict)}
            value = [item for item in value if not isinstance(item, dict) or (item.get("course"), item.get("period"), item.get("location")) not in identities]
        if isinstance(value, list):
            collections.append((key, value))
        elif isinstance(value, dict):
            if key == "next_course":
                collections.append(("下一节课", [value]))
            else:
                collections.extend((str(day), rows) for day, rows in value.items() if isinstance(rows, list))
    if not collections:
        collections = [(key, result[key]) for key in LIST_FIELDS if isinstance(result.get(key), list)]
    if data:
        if isinstance(data.get("courses"), list):
            collections.append(("courses", data["courses"]))
        for period in data.get("periods", []) if isinstance(data.get("periods"), list) else []:
            if isinstance(period, dict) and isinstance(period.get("rooms"), list):
                label = str(period.get("periodName") or f"第{period.get('period')}大节")
                lines.append(f"**{escape(label)} · 空闲 {period.get('total', len(period['rooms']))} 间**")
                for row in period["rooms"]:
                    lines.extend(_row_lines(row))
        if tool == "resource_registry_query":
            for key, value in data.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    lines.append(f"{escape(key, 60)}：{value}")
    if food_rows is not None:
        collections = [("items", food_rows)]
    if result.get("success") and collections and all(not rows for _, rows in collections):
        status = "查询完成 · 没有符合条件的结果" if status == "已完成" else status + " · 无符合结果"
    for key, rows in collections:
        if tool == "library_query" and key == "locations" and rows:
            block = f"**图书馆区域 · {len(rows)} 项**"
            for row in rows:
                if not isinstance(row, dict):
                    continue
                name = _first(row, "name", "location", "areaName", "area_name") or "未命名区域"
                count = row.get("available_count")
                remaining = f"剩余 {count} 座" if type(count) is int and count >= 0 else "余座未知"
                campus = str(row.get('campus') or '校区待确认')
                entry = "- " + escape((campus + ' · ' + name).translate(str.maketrans('()', '（）')), 100) + " · " + remaining
                if len((block + "\n" + entry).encode()) > 1800:
                    lines.append(block)
                    block = "**图书馆区域（续）**"
                block += "\n" + entry
            lines.append(block)
            continue
        if rows:
            label = {"current_courses": "正在上课", "next_course": "下一节课", "day_schedule": "当天其他课程", "schedule": "课程安排", "locations": "图书馆区域", "seats": "座位", "rooms": "房间", "records": "预约记录", "tasks": "签到任务", "courses": "课程", "course_options": "课程选项", "exams": "考试", "plans": "方案", "commands": "可用命令", "groups": "成员组", "data": "查询结果", "items": "查询结果", "candidates": "候选课程", "appointments": "预约", "tips": "使用提示", "examples": "命令示例", "titles": "预约主题", "categories": "预约分类", "date_options": "可选日期", "date_rows": "预约时段"}.get(key, key)
            if schedule_reference:
                label = {"current_courses": "按当前时段匹配（供核对）", "下一节课": "后续时段匹配（供核对）", "day_schedule": "该星期其他课程", "schedule": "按星期匹配的课程"}.get(key, label)
            lines.append(f"**{escape(label, 60)} · {len(rows)} 项**")
            for row in rows:
                lines.extend(_schedule_row_lines(row) if tool == "schedule_query" else _row_lines(row))
    if result.get("week_filter_applied") is False and "未按教学周过滤" not in msg:
        lines.append("课表未按教学周过滤，请核对实际教学周。")
    if tool.startswith("yuketang") and tool != "yuketang_exam_list":
        lines.append("监听/桥端接收状态不代表答题完成或已交卷。")
    next_commands = result.get("next_commands") or []
    next_step = "；".join(redact_cli_command(item) for item in next_commands[:2]) if isinstance(next_commands, list) else ""
    if tool == "library_query":
        next_step = "请回复区域名称、日期及开始和结束时间，我先查可用座位；预约需要另行确认。"
    if tool.startswith("empty_classroom"):
        next_step = "可补充校区、教学楼、星期和大节继续查询；结果只反映课程占用。"
    if tool == "system_status":
        next_step = "发送“今天课表”查课程，或“找自习室”查空教室。"
    if tool in {"sync_schedule", "schedule_sync"} and result.get("success"):
        next_step = "发送“今天课表”或“本周课表”查看更新后的课程。"
    if tool == "schedule_query" and result.get("success"):
        next_step = "发“明天课表”看明天；课表有变动时发“同步课表”。"
    if result.get("confirmation_required"):
        next_step = "核对本页信息后，在下一条私聊回复“确认”（5 分钟内）；尚未执行。"
    elif result.get("external_committed") and result.get("storage_persisted") is False:
        next_step = "先查询当前记录核验，不要重复提交。"
    elif status == "需要绑定或登录" or code in {"auth_failed", "login_failed", "auth_required", "login_required", "account_not_bound"}:
        if tool.startswith("yuketang"):
            next_step = "请私聊发送 help yuketang，按雨课堂专项提示登录。"
        elif code == "account_not_bound":
            next_step = "请在私聊发送“绑定账号”，按提示绑定并登录。"
        else:
            next_step = "请私聊发送“我的状态”，核对并恢复校园登录后重试。"
    if tool == "schedule_query" and result.get("success") and result.get("unknown_course_count"):
        status = "部分课程周次待核验"
    return Card(title, status, tuple(lines or ["服务未返回可展示的详细信息。"]), next_step or "发送“使用帮助”查看常用操作。")


def text_card(title: str, text: str, *, private: bool = False) -> list[str]:
    if private:
        return result_card({}, private=True).pages()
    if title == '校园账号与校准' and text == '已取消账号凭据录入。':
        title, text = '绑定校园账号', '已取消填写，原来的账号保留。'
    status = "服务回执"
    if "尚未发布" in text or "尚未提交" in text or "待确认" in text:
        status = "待本人核对 · 尚未提交"
    elif "过期" in text or "失效" in text:
        status = "已过期或失效 · 请重新核验"
    elif "失败" in text or "无法" in text:
        status = "未完成 · 请核验状态"
    # Keep the validated, private KIT authorization URL byte-for-byte. Escaping
    # its fragment would change the binding token. No arbitrary links/images.
    lines = []
    for line in str(text).splitlines():
        if not line.strip():
            continue
        if title == "HENU KIT 绑定" and re.fullmatch(r"https://[A-Za-z0-9.-]+(?::\d+)?/bind/qq#[A-Za-z0-9_-]{1,512}", line.strip()):
            lines.append("[打开 HENU KIT 授权页面](" + line.strip() + ")")
        else:
            lines.append(escape(line, 12000))
    return Card(title, status, tuple(lines)).pages()


def card_chain(text: str) -> MessageChain:
    return MessageChain([Plain(text=text)])


def set_card_reply(event: Any, text: str) -> None:
    """Use the real reply contract of either supported LangBot SDK generation."""
    chain = card_chain(text)
    if hasattr(event, "reply_message_chain"):
        event.reply_message_chain = chain
    else:
        event.reply = chain.model_dump(mode="json")


def scope(bot: str, session: Any) -> tuple[str, str, str, str] | None:
    kind = str(getattr(getattr(session, "launcher_type", ""), "value", getattr(session, "launcher_type", "")))
    sender = str(getattr(session, "sender_id", "") or "")
    launcher = str(getattr(session, "launcher_id", "") or "")
    if not bot or kind not in {"person", "group"} or sender in {"", "0", "unknown", "None"} or not launcher:
        return None
    return bot, kind, launcher, sender


class CardCache:
    def __init__(self):
        self.entries: OrderedDict[tuple, tuple[float, list[str], int]] = OrderedDict()
        self.preview_receipts: dict[tuple, tuple[set[int], Any]] = {}
        self.request_results: dict[tuple, tuple[int, OrderedDict]] = {}

    def put(self, key: tuple, pages: list[str], *, on_read: Any = None, current: int = 0, expires: float | None = None) -> None:
        self.entries[key] = (min(time.monotonic() + PAGE_TTL, expires) if expires is not None else time.monotonic() + PAGE_TTL, pages[:MAX_PAGES], current)
        self.preview_receipts.pop(key, None)
        self.request_results.pop(key, None)
        if on_read is not None:
            self.preview_receipts[key] = (set(), on_read)
        self.entries.move_to_end(key)
        while len(self.entries) > 128:
            expired, _ = self.entries.popitem(last=False)
            self.preview_receipts.pop(expired, None)
            self.request_results.pop(expired, None)

    def stage(self, key: tuple, query_id: int, result_id: str, pages: list[str], *, expires: float | None = None) -> list[str]:
        previous = self.request_results.get(key)
        groups = previous[1] if previous and previous[0] == query_id else OrderedDict()
        groups[result_id] = pages
        combined = [page for group in groups.values() for page in group]
        omitted = len(combined) > MAX_PAGES
        combined = combined[:MAX_PAGES]
        if len(groups) > 1:
            combined = [re.sub(r"\n第 \d+/\d+ 页 · [^\n]+", "", page) + f"\n\n本轮第 {index}/{len(combined)} 页 · 回复“下一页”“上一页”或“卡片第 N 页”" for index, page in enumerate(combined, 1)]
        if omitted:
            combined[-1] += "\n本轮结果超过20页，请分开查询剩余功能。"
            retained = OrderedDict()
            remaining = MAX_PAGES
            for identifier, group in groups.items():
                if remaining <= 0:
                    break
                retained[identifier] = group[:remaining]
                remaining -= len(retained[identifier])
            groups = retained
        if previous and previous[0] == query_id and key in self.entries:
            expires = min(self.entries[key][0], expires) if expires is not None else self.entries[key][0]
        self.put(key, combined, expires=expires)
        self.request_results[key] = (query_id, groups)
        return combined

    async def delivered(self, key: tuple, page: str) -> None:
        entry = self.entries.get(key)
        receipt = self.preview_receipts.get(key)
        if not entry or not receipt or entry[0] <= time.monotonic() or page not in entry[1]:
            return
        viewed, callback = receipt
        viewed.add(entry[1].index(page))
        if len(viewed) == len(entry[1]):
            self.preview_receipts.pop(key, None)
            self.request_results.pop(key, None)
            await callback()

    def page(self, key: tuple, request: str) -> str:
        entry = self.entries.get(key)
        if not entry or entry[0] <= time.monotonic():
            self.entries.pop(key, None)
            self.preview_receipts.pop(key, None)
            self.request_results.pop(key, None)
            return text_card("卡片已过期", "卡片已过期或当前会话没有可翻页内容，请重新查询。")[0]
        expires, pages, current = entry
        if request == "下一页":
            selected = current + 1
        elif request == "上一页":
            selected = current - 1
        else:
            selected = int(re.search(r"\d+", request).group()) - 1
        if selected < 0 or selected >= len(pages):
            return text_card("翻页范围", f"当前卡片共 {len(pages)} 页，请发送“卡片第 N 页”。")[0]
        self.entries[key] = (expires, pages, selected)
        return pages[selected]


def cache(plugin: Any) -> CardCache:
    if not isinstance(getattr(plugin, "_henu_card_cache", None), CardCache):
        plugin._henu_card_cache = CardCache()
    return plugin._henu_card_cache


async def stage_card(plugin: Any, session: Any, query_id: int, result: dict) -> None:
    from langbot_plugin.api.proxies.query_based_api import QueryBasedAPIProxy
    proxy = QueryBasedAPIProxy(query_id=query_id, plugin_runtime_handler=plugin.plugin_runtime_handler)
    bot = str(await proxy.get_bot_uuid() or "")
    key = scope(bot, session)
    if key is None:
        return
    authoritative = bool(result.get("confirmation_required") or result.get("external_committed"))
    if not authoritative:
        getter = getattr(proxy, "get_query_vars", None)
        variables = await getter() if callable(getter) else {}
        previous = variables.get("_henu_card") if isinstance(variables, dict) else None
        if (isinstance(previous, dict) and previous.get("owner") == list(key)
                and previous.get("authoritative")):
            # Later reads in this query cannot hide the exact pending operation
            # or its final receipt behind an ordinary synthesized answer.
            return
    from henu_plugin.presentation import publication_lock
    if not authoritative:
        from henu_plugin.presentation import presentations, build_seat_image
        import asyncio
        store = presentations(plugin)
        from henu_plugin.classroom_map import is_classroom
        deadline = store.classroom_deadline(key, query_id, result) if is_classroom(result) else None
        snapshot = store.with_region(key, query_id, result)
        image, reserved = await asyncio.to_thread(build_seat_image, snapshot) if snapshot.get('_qq_seat_map') else (None, None)
        from henu_plugin.classroom_map import is_classroom, build_classroom_image
        classroom = is_classroom(snapshot)
        if classroom:
            image = await asyncio.to_thread(build_classroom_image, snapshot)
    async with publication_lock(plugin, key, query_id):
        if not authoritative:
            getter = getattr(proxy, 'get_query_vars', None)
            variables = await getter() if callable(getter) else {}
            previous = variables.get('_henu_card') if isinstance(variables, dict) else None
            if isinstance(previous, dict) and previous.get('owner') == list(key) and previous.get('authoritative'):
                return
        cli = result.get("cli") if isinstance(result.get("cli"), dict) else {}
        result_id = str(cli.get("command") or cli.get("resolved_tool") or result.get("_resolved_tool_name") or "henu_cli")
        await proxy.set_query_var('_henu_presentation', None)
        if result.get('confirmation_required'):
            from henu_plugin.qq_buttons import confirmation_actions, envelope
            wire = await confirmation_actions(plugin, bot, session, query_id)
            pages = result_card(result).pages()
            if not wire or len(pages) != 1:
                pages = Card('预览已更新', '', ('请重新发起操作，本次没有提交。',)).pages()
            else:
                pages[0] = envelope(pages[0], wire)
            cache(plugin).put(key, pages)
        elif result.get("external_committed"):
            pages = result_card(result).pages()
            cache(plugin).put(key, pages)
        else:
            result['display_ref'] = store.capture(key, query_id, snapshot, image=image, reserved=reserved, expires=deadline)
            if classroom:
                from henu_plugin.classroom_map import remember_details
                remember_details(plugin, key, snapshot, expires=deadline)
            pages = cache(plugin).stage(key, query_id, result_id, result_card(result).pages(), expires=deadline)
        await proxy.set_query_var("_henu_card", {
            "owner": list(key), "page": pages[0],
            "authoritative": authoritative,
            **({'expires': cache(plugin).entries[key][0]} if not authoritative else {}),
            **({'classroom_ref': result['display_ref']} if not authoritative and classroom else {}),
            # Exact menu shortcuts read page directly. Read results, including a
            # failed subquery, leave room for synthesis and necessary followups.
            "prefer_agent": not authoritative,
        })


async def reply_card(ctx: Any, title: str, text: str, *, private: bool = True, plugin: Any = None):
    kind = str(getattr(getattr(ctx.event, "launcher_type", ""), "value", getattr(ctx.event, "launcher_type", "")))
    pages = text_card(title, text, private=private and kind != "person")
    if plugin is not None and callable(getattr(ctx, "get_bot_uuid", None)):
        try:
            key = scope(str(await ctx.get_bot_uuid() or ""), ctx.event)
            if key:
                cache(plugin).put(key, pages)
        except Exception:
            pass
    return await ctx.reply(card_chain(pages[0]))


def agent_answer_pages(answer: str) -> list[str]:
    """Wrap the synthesized answer without the tool-card row pagination."""
    body = escape(answer, len(answer) * 2 + 100)
    # Only headings and emphasis are allowed back through the escaping layer.
    body = re.sub(r"(?m)^(?:\\#){1,3} ", "## ", body)
    body = re.sub(r"\\\*\\\*([^\n]+?)\\\*\\\*", r"**\1**", body)
    return Card("校园查询", "", (body,)).pages()


async def _finish_card(ctx, plugin, owner, text):
    from henu_plugin.presentation import publication_lock
    async with publication_lock(plugin, owner, getattr(ctx, 'query_id', 0)):
        latest = await ctx.get_query_vars()
        card = latest.get('_henu_card') if isinstance(latest, dict) else None
        protected = isinstance(card, dict) and card.get('owner') == list(owner) and card.get('authoritative')
        set_card_reply(ctx.event, card['page'] if protected else text)
        setter = getattr(ctx, 'set_query_var', None)
        if callable(setter):
            await setter('_henu_card', None)
            await setter('_henu_presentation', None)
        return bool(protected)


async def _deliver_presentation(ctx, plugin, owner):
    from henu_plugin.presentation import presentations, publication_lock
    from henu_plugin.library_seat_map import selection_guard, install_selection
    from langbot_plugin.api.entities.builtin.platform.message import Image

    async with publication_lock(plugin, owner, ctx.query_id):
        latest = await ctx.get_query_vars()
        card = latest.get('_henu_card')
        protected = isinstance(card, dict) and card.get('owner') == list(owner) and card.get('authoritative')
        plan = latest.get('_henu_presentation')
        if not protected:
            if not isinstance(plan, dict) or plan.get('owner') != list(owner) or plan.get('query_id') != ctx.query_id:
                return
            await ctx.set_query_var('_henu_presentation', None)
    if protected:
        await _finish_card(ctx, plugin, owner, '')
        return
    if plan.get('expires', 0) <= time.monotonic():
        await _finish_card(ctx, plugin, owner, text_card('查询结果已过期', '请重新查询，避免沿用旧的课程或余座。')[0])
        return
    pages = plan['pages']
    store = presentations(plugin) if plugin is not None else None
    guard = store.guard(owner, ctx.query_id) if store and plan.get('seat_selection') and owner[1] == 'person' else None
    if guard is not None and not store.is_latest(owner, ctx.query_id):
        await _finish_card(ctx, plugin, owner, text_card('查询条件已更新', '本轮旧座位结果已作废，请按新条件重新查询后选座。')[0])
        return
    failed_images = set()
    for ref in plan.get('images', []):
        image = store.asset(owner, ctx.query_id, ref).image if store else None
        if image:
            try:
                await ctx.reply(MessageChain([Image(base64=image)]))
            except Exception:
                failed_images.add(ref)  # Unknown media delivery; never resend.
    if failed_images and store:
        plan = {**plan, **store.render(owner, ctx.query_id, plan['arrangement'], failed_images=failed_images)}
        pages = plan['pages']
    if len(pages) + len(plan.get('images', [])) + len(failed_images) > 5:
        await _finish_card(ctx, plugin, owner, text_card('结果较长', '这次结果太长，请分开查询课表和座位。')[0])
        return
    try:
        for page in pages[:-1]:
            await ctx.reply(card_chain(page))
    except Exception:
        await _finish_card(ctx, plugin, owner, text_card('发送状态待确认', '部分结果发送状态暂未确认，请重新查询；不会自动重复发送。')[0])
        return
    protected = await _finish_card(ctx, plugin, owner, pages[-1])
    if not protected and plugin is not None:
        if guard is not None:
            selected = store.asset(owner, ctx.query_id, plan['seat_selection']).selection
            if not store.is_latest(owner, ctx.query_id) or not install_selection(plugin, owner, selected, guard):
                page = text_card('查询条件已更新', '本轮旧座位结果已作废，请按新条件重新查询后选座。')[0]
                set_card_reply(ctx.event, page)
                cache(plugin).put(owner, [page])
                return
        cache(plugin).put(owner, pages + plan.get('more', []), current=len(pages)-1, expires=plan.get('expires'))


async def present_card(ctx, *, plugin: Any = None):
    if getattr(ctx.event, "funcs_called", []) or getattr(ctx.event, "finish_reason", "") in {"tool_calls", "function_call"}:
        return
    try:
        variables = await ctx.get_query_vars()
        card = variables.get("_henu_card")
        plan = variables.get('_henu_presentation')
        owner = scope(str(await ctx.get_bot_uuid() or ""), ctx.event)
        if (isinstance(plan, dict) and owner is not None and plan.get('owner') == list(owner)
                and plan.get('query_id') == getattr(ctx, 'query_id', None)
                and not (isinstance(card, dict) and card.get('authoritative'))):
            try:
                await _deliver_presentation(ctx, plugin, owner)
            except (ValueError, KeyError):
                await _finish_card(ctx, plugin, owner, text_card('查询结果已过期', '请重新查询，避免沿用旧的课程或余座。')[0])
            return
        if not isinstance(card, dict):
            return
        if owner is None or card.get("owner") != list(owner):
            return
        if card.get('expires', float('inf')) <= time.monotonic():
            await _finish_card(ctx, plugin, owner, text_card('查询结果已过期', '请重新查询。')[0])
            return
        answer = str(getattr(ctx.event, "response_text", "") or "").strip()
        pages = agent_answer_pages(answer) if card.get('prefer_agent') and answer else [card['page']]
        protected = await _finish_card(ctx, plugin, owner, pages[0])
        if not protected and plugin is not None and card.get('prefer_agent') and answer:
            cache(plugin).put(owner, pages, expires=card.get('expires'))
    except Exception:
        # Preserve the pipeline's original text response on API failure.
        return


async def handle_card_page(plugin: Any, ctx):
    chain = getattr(getattr(ctx.event, "message_chain", None), "root", [])
    text = page_request_text(chain)
    if not text:
        return False
    if getattr(ctx, "is_prevent_postorder", False):
        return True
    ctx.prevent_default()
    ctx.prevent_postorder()
    try:
        key = scope(str(await ctx.get_bot_uuid() or ""), ctx.event)
        page = cache(plugin).page(key, text) if key else text_card("无法翻页", "无法确认当前会话，请重新查询。")[0]
    except Exception:
        key = None
        page = "卡片读取失败，请重新查询。"
    try:
        await ctx.reply(card_chain(page))
    except Exception:
        return True  # The first send may already have arrived; never repeat.
    if key:
        try:
            await cache(plugin).delivered(key, page)
        except Exception:
            pass  # No complete-preview acknowledgment, so confirmation stays blocked.
    return True

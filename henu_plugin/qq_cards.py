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
    "preferences": "图书馆偏好",
    "sync_schedule": "课表导入", "schedule_query": "我的课表",
    "smart_course_selection": "选课方案", "smart_course_select": "选课提交",
    "course_selection_query": "教务选课", "course_selection_plan": "选课方案",
    "course_selection_submit": "选课提交", "course_monitor_config": "选课监控设置",
    "course_monitor_once": "选课监控", "course_monitor_run": "选课监控",
    "course_monitor_notify_test": "监控通知测试", "library_query": "图书馆座位",
    "library_reserve": "座位预约", "library_auto_signin": "座位签到",
    "library_cancel": "取消座位预约", "seminar_group": "研讨室成员组",
    "seminar_query": "研讨室", "seminar_reserve": "研讨室预约",
    "seminar_signin": "研讨室签到", "seminar_cancel": "取消研讨室预约",
    "set_calibration_source": "节次校准", "system_status": "账号与服务状态",
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
        # Greedy line pagination with space reserved for header/footer.
        chunks: list[list[str]] = [[]]
        budget = MAX_BYTES - 1200
        for raw in self.lines:
            line = raw
            # Never split an escape pair or silently cut one large paragraph.
            while len(line.encode("utf-8")) > budget:
                end = min(650, len(line))
                while len(line[:end].encode("utf-8")) > budget or line[:end].endswith("\\"):
                    end -= 1
                chunks.append([line[:end]])
                line = line[end:]
            if len(chunks[-1]) >= 8 or len(("\n".join(chunks[-1]) + "\n" + line).encode("utf-8")) > budget:
                chunks.append([])
            chunks[-1].append(line)
        chunks = [chunk for chunk in chunks if chunk] or [[]]
        omitted = len(chunks) > MAX_PAGES
        chunks = chunks[:MAX_PAGES]
        pages = []
        for number, chunk in enumerate(chunks, 1):
            lines = [PREFIX + escape(self.title, 60), "", "**" + escape(self.status, 70) + "**", "", *chunk]
            if self.next_step:
                lines += ["", "**下一步**", escape(self.next_step, 160)]
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


def result_card(result: dict, tool: str = "henu_cli", *, private: bool = False) -> Card:
    if private:
        return Card("请转到私聊", "保护个人信息", (PRIVATE_NOTICE,), "私聊机器人后重新发送需求。")
    cli = result.get("cli") if isinstance(result.get("cli"), dict) else {}
    tool = str(cli.get("resolved_tool") or result.get("_resolved_tool_name") or tool)
    title = TOOL_TITLES.get(tool, "美食查询" if tool == "food" else "校园服务")
    if tool == "food":
        title = {"list": "校园美食榜", "detail": "美食详情", "mine": "我的美食投稿", "venues": "美食店铺", "create": "美食草稿"}.get(cli.get("action"), title)
    if cli.get("mode") == "help":
        title = "使用帮助" + (" · " + str(cli.get("topic")) if cli.get("topic") != "root" else "")
    msg = str(result.get("reply_text") or result.get("msg") or "")
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
    if tool == "system_status":
        wrapper = result.get("account") or {}
        account = wrapper.get("account", wrapper) if isinstance(wrapper, dict) else {}
        sid = str(account.get("student_id") or "")
        lines += ["**学号**：" + (escape(sid[:2] + "****" + sid[-2:]) if len(sid) > 4 else "未绑定"), "**登录凭据**：" + ("已保存" if account.get("has_password") else "未保存")]
        attempt = account.get("last_login_attempt") or {}
        attempt_status = {
            "verified": "在线校验成功", "failed": "未完成", "unverified": "仅保存，未验证",
            "pending": "结果待确认", "unknown": "结果未知，勿自动重试",
        }.get(attempt.get("status")) if isinstance(attempt, dict) else None
        if attempt_status:
            lines.append("**最近一次绑定尝试**：" + attempt_status + "（不代表当前会话仍有效）")
        clock = result.get("server_time") or {}
        if isinstance(clock, dict) and _first(clock, "now_text", "now_iso"):
            lines.append("**校园时间**：" + escape(_first(clock, "now_text", "now_iso")))
    # Whitelisted nested data: never dump credentials, raw provider records or
    # local file paths just because a service adds another result field.
    for section in ("filters", "statistics", "detail", "room", "area", "library_defaults", "constraints"):
        value = result.get(section)
        if isinstance(value, dict):
            safe = {key: item for key, item in value.items() if key in {"name", "room_name", "roomName", "nameMerge", "location", "seat_no", "capacity", "id", "area_id", "start_time", "end_time", "status", "available_slots", "total", "count", "finished", "pending"}}
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
    for key in ("current_courses", "next_course", "day_schedule", "schedule"):
        value = result.get(key)
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
        if rows:
            label = {"current_courses": "正在上课", "next_course": "下一节课", "day_schedule": "当天课程", "locations": "图书馆区域", "seats": "座位", "rooms": "房间", "records": "预约记录", "tasks": "签到任务", "courses": "课程", "course_options": "课程选项", "exams": "考试", "plans": "方案", "commands": "可用命令", "groups": "成员组", "data": "查询结果", "items": "查询结果", "candidates": "候选课程", "appointments": "预约", "tips": "使用提示", "examples": "命令示例", "titles": "预约主题", "categories": "预约分类", "date_options": "可选日期", "date_rows": "预约时段"}.get(key, key)
            lines.append(f"**{escape(label, 60)} · {len(rows)} 项**")
            for row in rows:
                lines.extend(_row_lines(row))
    if result.get("week_filter_applied") is False and "未按教学周过滤" not in msg:
        lines.append("课表未按教学周过滤，请核对实际教学周。")
    if tool.startswith("yuketang") and tool != "yuketang_exam_list":
        lines.append("监听/桥端接收状态不代表答题完成或已交卷。")
    next_commands = result.get("next_commands") or []
    next_step = "；".join(redact_cli_command(item) for item in next_commands[:2]) if isinstance(next_commands, list) else ""
    if result.get("confirmation_required"):
        next_step = "核对本页信息后，在下一条私聊回复“确认”（5 分钟内）；尚未执行。"
    elif result.get("external_committed") and result.get("storage_persisted") is False:
        next_step = "先查询当前记录核验，不要重复提交。"
    elif status == "需要绑定或登录":
        next_step = "私聊发送 " + ("help yuketang" if tool.startswith("yuketang") else "help account") + "，按提示绑定并登录。"
    return Card(title, status, tuple(lines or ["服务未返回可展示的详细信息。"]), next_step or "可发送 help 查看当前功能和命令。")


def text_card(title: str, text: str, *, private: bool = False) -> list[str]:
    if private:
        return result_card({}, private=True).pages()
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

    def put(self, key: tuple, pages: list[str], *, on_read: Any = None) -> None:
        self.entries[key] = (time.monotonic() + PAGE_TTL, pages[:MAX_PAGES], 0)
        self.preview_receipts.pop(key, None)
        self.request_results.pop(key, None)
        if on_read is not None:
            self.preview_receipts[key] = (set(), on_read)
        self.entries.move_to_end(key)
        while len(self.entries) > 128:
            expired, _ = self.entries.popitem(last=False)
            self.preview_receipts.pop(expired, None)
            self.request_results.pop(expired, None)

    def stage(self, key: tuple, query_id: int, result_id: str, pages: list[str]) -> list[str]:
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
        self.put(key, combined)
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
    cli = result.get("cli") if isinstance(result.get("cli"), dict) else {}
    result_id = str(cli.get("command") or cli.get("resolved_tool") or result.get("_resolved_tool_name") or "henu_cli")
    pages = cache(plugin).stage(key, query_id, result_id, result_card(result).pages())
    await proxy.set_query_var("_henu_card", {"owner": list(key), "page": pages[0]})


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


async def present_card(ctx):
    if getattr(ctx.event, "funcs_called", []) or getattr(ctx.event, "finish_reason", "") in {"tool_calls", "function_call"}:
        return
    try:
        variables = await ctx.get_query_vars()
        card = variables.get("_henu_card")
        if not isinstance(card, dict):
            return
        owner = scope(str(await ctx.get_bot_uuid() or ""), ctx.event)
        if owner is None or card.get("owner") != list(owner):
            return
        set_card_reply(ctx.event, card["page"])
        setter = getattr(ctx, "set_query_var", None)
        if callable(setter):
            await setter("_henu_card", None)
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

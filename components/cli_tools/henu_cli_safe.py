from __future__ import annotations

import re
from typing import Any

from langbot_plugin.api.entities.builtin.provider import session as provider_session

from components.cli_tools.henu_cli import HenuCli


class HenuCliSafe(HenuCli):
    """HENU CLI wrapper that keeps tool output friendly to QQ Official Bot.

    LangBot's QQ Official adapter eventually sends plain text through the official API.
    Large, raw JSON-shaped answers make the model more likely to loop into another tool call
    or produce an overlong/empty final message. This wrapper keeps the structured data useful
    for the model but adds a compact `reply_text` and trims very large top-level lists.
    """

    _MAX_REPLY_TEXT = 1200
    _MAX_LIST_ITEMS = 8

    async def call(
        self,
        params: dict[str, Any],
        session: provider_session.Session,
        query_id: int,
    ) -> dict[str, Any]:
        result = await super().call(params, session, query_id)
        if isinstance(result, dict):
            self._attach_reply_text(result)
            self._compact_large_payloads(result)
            self._attach_llm_hint(result)
        return result

    def _attach_reply_text(self, result: dict[str, Any]) -> None:
        if isinstance(result.get("reply_text"), str) and result["reply_text"].strip():
            result["reply_text"] = self._qq_safe_text(result["reply_text"])
        else:
            result["reply_text"] = self._qq_safe_text(self._build_reply_text(result))

        result["qq_reply_hint"] = (
            "最终回复用户时优先复述 reply_text；不要直接发送完整 JSON；"
            "除非用户明确要求翻页、筛选或预约，否则不要重复调用 henu_cli。"
        )

    def _build_reply_text(self, result: dict[str, Any]) -> str:
        if self._is_empty_classroom_result(result):
            return self._format_empty_classroom_reply(result)
        if isinstance(result.get("rooms"), list):
            return self._format_rooms_reply(result)
        if isinstance(result.get("records"), list):
            return self._format_records_reply(result)
        if isinstance(result.get("tasks"), list):
            return self._format_tasks_reply(result)
        if isinstance(result.get("day_schedule"), list):
            return self._format_schedule_reply(result)

        msg = str(result.get("msg") or "").strip()
        if msg:
            return msg

        if result.get("success") is False:
            return "操作失败，但工具没有返回具体错误信息。"
        return "操作完成。"

    def _attach_llm_hint(self, result: dict[str, Any]) -> None:
        hint = self._build_llm_hint(result)
        result["llm_hint"] = hint
        result["cli_hint"] = hint
        print(f"[henu_cli.llm_hint] {hint}", flush=True)

    def _build_llm_hint(self, result: dict[str, Any]) -> str:
        cli = result.get("cli") if isinstance(result.get("cli"), dict) else {}
        command = str(cli.get("command") or "").strip()
        mode = str(cli.get("mode") or "").strip()
        resolved_tool = str(cli.get("resolved_tool") or "").strip()
        status = "成功" if result.get("success") else "失败"
        next_commands = result.get("next_commands") if isinstance(result.get("next_commands"), list) else []
        next_text = "、".join(str(item) for item in next_commands[:3]) if next_commands else "等待用户补充需求"

        parts = [
            f"henu_cli 本次执行{status}",
            f"mode={mode or 'unknown'}",
        ]
        if command:
            parts.append(f"command={command}")
        if resolved_tool:
            parts.append(f"tool={resolved_tool}")
        parts.append("最终回复用户时优先复述 reply_text，不要发送完整 JSON")
        parts.append(f"如需继续，只能根据用户明确要求或 next_commands 选择：{next_text}")

        if self._is_empty_classroom_result(result) or self._text_mentions_empty_classroom(command, resolved_tool):
            parts.append(
                "空教室能力已支持，不要回复不支持；不确定参数时先用 empty_classroom query 或 help empty_classroom"
            )
        return "；".join(parts) + "。"

    def _is_empty_classroom_result(self, result: dict[str, Any]) -> bool:
        cli = result.get("cli") if isinstance(result.get("cli"), dict) else {}
        return self._text_mentions_empty_classroom(
            str(cli.get("command") or ""),
            str(cli.get("resolved_tool") or ""),
            str(cli.get("topic") or ""),
            str(result.get("msg") or ""),
        )

    @staticmethod
    def _text_mentions_empty_classroom(*values: str) -> bool:
        text = " ".join(str(value or "") for value in values).lower()
        return any(marker in text for marker in ("empty_classroom", "空教室", "空闲教室"))

    def _format_empty_classroom_reply(self, result: dict[str, Any]) -> str:
        rooms = [item for item in (result.get("rooms") or []) if isinstance(item, dict)]
        data = [item for item in (result.get("data") or []) if isinstance(item, dict)]
        total = result.get("total")
        if not isinstance(total, int):
            total = len(rooms) if rooms else len(data)

        cli = result.get("cli") if isinstance(result.get("cli"), dict) else {}
        command = str(cli.get("command") or "").strip()
        lines = [str(result.get("msg") or f"空教室查询完成，共 {total} 个结果。").strip()]
        if command:
            lines.append(f"命令: {command}")

        sample = rooms or data
        for room in sample[:6]:
            name = self._first_text(room, "roomName", "room_name", "name", "classroomName", "buildingName") or "未命名教室"
            building = self._first_text(room, "buildingName", "building_name")
            campus = self._first_text(room, "campusName", "campus_name")
            capacity = self._first_text(room, "capacity")
            meta = " ".join(part for part in (campus, building, f"{capacity}座" if capacity else "") if part)
            lines.append(f"- {name}{'：' + meta if meta else ''}")

        if total and len(sample) > 6:
            lines.append(f"……还有 {len(sample) - 6} 个结果未展示。")
        lines.append("如果需要更精确，请补充校区、楼房、周次、星期和大节。")
        return "\n".join(lines)

    def _format_rooms_reply(self, result: dict[str, Any]) -> str:
        rooms = [item for item in (result.get("rooms") or []) if isinstance(item, dict)]
        query = result.get("resolved_query") if isinstance(result.get("resolved_query"), dict) else {}
        date_text = str(query.get("date") or "").strip()
        page = str(query.get("page") or result.get("page") or "").strip()
        header_bits = ["研讨室查询完成"]
        if date_text:
            header_bits.append(date_text)
        if page:
            header_bits.append(f"第 {page} 页")
        lines = ["，".join(header_bits) + f"，本页返回 {len(rooms)} 个结果。"]

        for room in rooms[:6]:
            name = self._first_text(
                room,
                "nameMerge",
                "name",
                "areaName",
                "area_name",
                "roomName",
                "room",
                "title",
            ) or "未命名研讨室"
            area_id = self._first_text(room, "id", "area_id", "areaId")
            slot_text = self._room_slot_text(room)
            suffix = f"（ID: {area_id}）" if area_id else ""
            if slot_text:
                lines.append(f"- {name}{suffix}: {slot_text}")
            else:
                lines.append(f"- {name}{suffix}")

        if len(rooms) > 6:
            lines.append(f"……还有 {len(rooms) - 6} 个结果未展示，可继续指定楼层/人数/时间筛选。")
        lines.append("需要预约时，请继续说明日期、开始结束时间、研讨室 ID、主题和手机号。")
        return "\n".join(lines)

    def _format_records_reply(self, result: dict[str, Any]) -> str:
        records = [item for item in (result.get("records") or []) if isinstance(item, dict)]
        lines = [f"查询到 {len(records)} 条预约记录。"]
        for record in records[:6]:
            name = self._first_text(record, "nameMerge", "name", "areaName", "room_name", "title") or "未命名记录"
            record_id = self._first_text(record, "id", "record_id", "recordId")
            time_text = self._first_text(record, "show_time", "showTime", "begin_time", "beginTime")
            suffix = f"（ID: {record_id}）" if record_id else ""
            lines.append(f"- {name}{suffix}{'：' + time_text if time_text else ''}")
        if len(records) > 6:
            lines.append(f"……还有 {len(records) - 6} 条未展示。")
        return "\n".join(lines)

    def _format_tasks_reply(self, result: dict[str, Any]) -> str:
        tasks = [item for item in (result.get("tasks") or []) if isinstance(item, dict)]
        lines = [f"当前有 {len(tasks)} 个研讨室签到任务。"]
        for task in tasks[:6]:
            room_name = self._first_text(task, "room_name", "name", "areaName") or "未命名研讨室"
            status = self._first_text(task, "status") or "unknown"
            sign_at = self._first_text(task, "sign_at")
            lines.append(f"- {room_name}: {status}{'，签到时间 ' + sign_at if sign_at else ''}")
        if len(tasks) > 6:
            lines.append(f"……还有 {len(tasks) - 6} 个任务未展示。")
        return "\n".join(lines)

    def _format_schedule_reply(self, result: dict[str, Any]) -> str:
        msg = str(result.get("msg") or "").strip()
        courses = [item for item in (result.get("day_schedule") or []) if isinstance(item, dict)]
        lines = [msg or f"今天共有 {len(courses)} 节/门课程。"]
        for course in courses[:6]:
            name = self._first_text(course, "course", "name", "course_name") or "未命名课程"
            time_text = self._first_text(course, "time", "period", "clock_start")
            place = self._first_text(course, "classroom", "place", "location")
            detail = " ".join(part for part in (time_text, place) if part)
            lines.append(f"- {name}{'：' + detail if detail else ''}")
        if len(courses) > 6:
            lines.append(f"……还有 {len(courses) - 6} 项未展示。")
        return "\n".join(lines)

    def _compact_large_payloads(self, result: dict[str, Any]) -> None:
        for key in ("rooms", "data", "records", "tasks", "seats", "items", "plans", "day_schedule", "current_courses"):
            value = result.get(key)
            if not isinstance(value, list) or len(value) <= self._MAX_LIST_ITEMS:
                continue
            result[f"{key}_total"] = len(value)
            result[f"{key}_truncated"] = True
            result[key] = value[: self._MAX_LIST_ITEMS]

    def _room_slot_text(self, room: dict[str, Any]) -> str:
        slots = room.get("available_slots") or room.get("date") or []
        if not isinstance(slots, list):
            return str(slots or "").strip()
        labels: list[str] = []
        for slot in slots[:3]:
            if isinstance(slot, dict):
                label = str(slot.get("label") or "").strip()
                if not label:
                    start = str(slot.get("start_time") or "").strip()
                    end = str(slot.get("end_time") or "").strip()
                    label = f"{start}-{end}" if start and end else ""
                if label:
                    labels.append(label)
            elif slot:
                labels.append(str(slot))
        if labels:
            suffix = " 等" if len(slots) > len(labels) else ""
            return "可预约 " + "、".join(labels) + suffix

        open_range = room.get("open_time_range") if isinstance(room.get("open_time_range"), dict) else {}
        return str(open_range.get("label") or "").strip()

    def _first_text(self, data: dict[str, Any], *keys: str) -> str:
        for key in keys:
            value = data.get(key)
            if value not in (None, "", [], {}):
                return str(value).strip()
        return ""

    def _qq_safe_text(self, value: Any) -> str:
        text = str(value or "")
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if not text:
            text = "操作完成。"
        if len(text) > self._MAX_REPLY_TEXT:
            text = text[: self._MAX_REPLY_TEXT].rstrip() + "\n……内容较长，已截断。"
        return text

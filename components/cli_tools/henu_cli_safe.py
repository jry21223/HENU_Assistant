from __future__ import annotations

import re
import time
from typing import Any

from langbot_plugin.api.entities.builtin.provider import session as provider_session

from components.cli_tools.henu_cli import HenuCli
from henu_plugin.cli import inspect_cli_command, redact_cli_command
from henu_plugin.qq_cards import PRIVATE_NOTICE, public_spec, stage_card
from henu_plugin.qq_buttons import confirmation_context
from henu_plugin.study_place import remember_context


class HenuCliSafe(HenuCli):
    """HENU CLI wrapper that keeps tool output friendly to QQ Official Bot.

    LangBot's QQ Official adapter eventually sends plain text through the official API.
    Large, raw JSON-shaped answers make the model more likely to loop into another tool call
    or produce an overlong/empty final message. This wrapper keeps the structured data useful
    for the model but adds a compact `reply_text` and trims very large top-level lists.
    """

    _MAX_REPLY_TEXT = 1200
    _MAX_LIST_ITEMS = 8

    @confirmation_context
    @remember_context
    async def call(
        self,
        params: dict[str, Any],
        session: provider_session.Session,
        query_id: int,
    ) -> dict[str, Any]:
        started = time.monotonic()
        try:
            from langbot_plugin.api.proxies.query_based_api import QueryBasedAPIProxy
            from henu_plugin.qq_cards import scope
            from henu_plugin.library_seat_map import selection_guard
            from henu_plugin.presentation import presentations
            proxy = QueryBasedAPIProxy(query_id=query_id, plugin_runtime_handler=self.plugin.plugin_runtime_handler)
            owner = scope(str(await proxy.get_bot_uuid() or ''), session)
            if owner:
                presentations(self.plugin).begin(owner, query_id, selection_guard(self.plugin, owner), started)
        except Exception:
            pass  # Presentation context never enables or blocks a campus operation.
        kind = getattr(session.launcher_type, "value", session.launcher_type)
        command = params.get("command", "") if isinstance(params, dict) else ""
        if kind == "group" and not public_spec(inspect_cli_command(command)):
            result = {"success": False, "error_code": "private_reply_required", "msg": PRIVATE_NOTICE, "reply_text": PRIVATE_NOTICE}
        else:
            result = await super().call(params, session, query_id)
        # Normal service results were captured in full before compaction. Early
        # confirmation/error branches and persistence warnings need this hook.
        captured = result.pop("_henu_card_captured", False) if isinstance(result, dict) else False
        if isinstance(result, dict) and (not captured or result.get("external_committed")):
            await self._publish_delivery_result(result, session, query_id)
        return result

    async def _publish_delivery_result(self, result: Any, session: Any, query_id: int) -> bool:
        if not isinstance(result, dict):
            return False
        try:
            from henu_plugin.study_place import remember_query_place
            from henu_plugin.qq_cards import scope
            from langbot_plugin.api.proxies.query_based_api import QueryBasedAPIProxy
            proxy = QueryBasedAPIProxy(query_id=query_id, plugin_runtime_handler=self.plugin.plugin_runtime_handler)
            owner = scope(str(await proxy.get_bot_uuid() or ''), session)
            await remember_query_place(self.plugin, owner, result)
            from henu_plugin.self_study import with_all_day_clock
            result.update(await with_all_day_clock(self.plugin, result))
            await stage_card(self.plugin, session, query_id, result)
            return True
        except Exception:
            # Query APIs are optional presentation infrastructure. The existing
            # readable tool result remains the fallback, without logging data.
            return False

    def _prepare_delivery_result(self, result: Any) -> None:
        if not isinstance(result, dict):
            return
        cli = result.get("cli")
        if isinstance(cli, dict) and cli.get("command"):
            cli["command"] = redact_cli_command(cli["command"])
        self._attach_reply_text(result)
        self._attach_semantic_options(result)
        self._attach_llm_hint(result)
        self._compact_large_payloads(result)

    def _attach_semantic_options(self, result: dict[str, Any]) -> None:
        locations = [item for item in (result.get("locations") or []) if isinstance(item, dict)]
        if locations and result.get("source") != "static_fallback":
            result["location_options"] = [
                {
                    "location": self._first_text(item, "location", "name", "areaName", "title"),
                    "area_id": self._first_text(item, "area_id", "areaId", "id"),
                    **({key: item.get(key) for key in ('campus', 'floor', 'available_count')}
                       if item.get('campus') else {}),
                }
                for item in locations
            ]
            campuses, seen = {}, set()
            for row in result["location_options"]:
                campus = row.get("campus") or "校区待确认"
                identity = (campus, row["area_id"] or row["location"])
                if identity in seen:
                    continue
                seen.add(identity)
                totals = campuses.setdefault(campus, {"regions": 0, "available_seats": 0, "unknown_regions": 0})
                totals["regions"] += 1
                count = row.get("available_count")
                if type(count) is int and count >= 0:
                    if totals["available_seats"] is not None:
                        totals["available_seats"] += count
                else:
                    totals["unknown_regions"] += 1
                    totals["available_seats"] = None
            summary = result.get("result_summary")
            if not isinstance(summary, dict):
                summary = {}
            result["result_summary"] = {**summary, "library_campuses": campuses}
            result["total"] = int(result.get("total") or len(locations))
            result["returned_count"] = len(locations)
            result["truncated"] = False
            result.pop("locations_total", None)
            result.pop("locations_returned", None)
            result.pop("locations_truncated", None)
            result["locations"] = locations[: self._MAX_LIST_ITEMS]

        seats = [item for item in (result.get("seats") or []) if isinstance(item, dict)]
        if seats:
            result["seat_options"] = [
                {
                    "no": self._first_text(item, "no", "seat_no", "seatNo", "name", "id"),
                    "id": self._first_text(item, "id"),
                    "status": self._first_text(item, "status", "status_text", "state"),
                }
                for item in seats[:10]
            ]
            result.setdefault("returned_count", len(seats))
            result.setdefault("truncated", False)
            result.pop("seats_total", None)
            result.pop("seats_returned", None)
            result.pop("seats_truncated", None)
            result["seats"] = seats[: self._MAX_LIST_ITEMS]

    def _attach_reply_text(self, result: dict[str, Any]) -> None:
        if isinstance(result.get("reply_text"), str) and result["reply_text"].strip():
            result["reply_text"] = self._qq_safe_text(result["reply_text"])
        else:
            result["reply_text"] = self._qq_safe_text(self._build_reply_text(result))

        result["qq_reply_hint"] = (
            "reply_text 是本次工具结果的摘要，可按用户问题筛选、合并和解释，不必逐字复述；"
            "完成当前需求所需的只读查询可以连续调用，不要为每个查询步骤让用户选菜单；"
            "已知条件沿用，缺少必要信息才追问。不得编造实时数据或将查询当成预约成功。"
        )

    def _build_reply_text(self, result: dict[str, Any]) -> str:
        if result.get("success") is False and result.get("source") != "live_empty":
            msg = str(result.get("msg") or "").strip()
            if msg:
                return msg
        preferences = result.get("preferences")
        if isinstance(preferences, dict):
            return "\n".join((
                str(result.get("msg") or "当前图书馆偏好"),
                f"默认区域：{preferences.get('library_location') or '未设置'}",
                f"默认座位：{preferences.get('library_seat_no') or '未设置'}",
                "保存偏好不会发起预约；预约仍需明确参数并另行确认。",
            ))
        if self._is_empty_classroom_result(result):
            return self._format_empty_classroom_reply(result)
        if isinstance(result.get("locations"), list):
            return self._format_locations_reply(result)
        if isinstance(result.get("seats"), list):
            return self._format_seats_reply(result)
        if isinstance(result.get("rooms"), list):
            return self._format_rooms_reply(result)
        if isinstance(result.get("records"), list):
            return self._format_records_reply(result)
        if isinstance(result.get("tasks"), list):
            return self._format_tasks_reply(result)
        if isinstance(result.get("day_schedule"), list):
            return self._format_schedule_reply(result)
        for key in ("plans", "courses", "candidates", "items"):
            if isinstance(result.get(key), list):
                return self._format_generic_list_reply(result, key)

        msg = str(result.get("msg") or "").strip()
        if msg:
            return msg

        if result.get("success") is False:
            return "操作失败，但工具没有返回具体错误信息。"
        return "操作完成。"

    def _format_locations_reply(self, result: dict[str, Any]) -> str:
        locations = [item for item in (result.get("locations") or []) if isinstance(item, dict)]
        total = result.get("total")
        if not isinstance(total, int):
            total = len(locations)
        date_text = str(result.get("date") or "").strip()
        source = str(result.get("source") or "").strip()
        if source == "live_empty":
            return (
                f"图书馆实时区域查询失败（{date_text}）：接口返回空列表。"
                "当前不能确认任何区域可预约，也不能据此判断图书馆开放状态；请稍后重试。"
            )
        source_text = "实时" if source == "live" else "内置兜底"
        header = f"图书馆区域列表（{date_text}）" if date_text else "图书馆区域列表"
        lines = [f"{header}，共 {total} 个（{source_text}）。"]
        display_locations = locations if len(locations) <= 12 else locations[:6] + locations[-6:]
        if len(locations) > len(display_locations):
            lines.append('下方展示部分区域。')
        for location in display_locations:
            name = self._first_text(location, "location", "name", "areaName", "title") or "未命名区域"
            campus = self._first_text(location, 'campus') or '校区待确认'
            count = location.get('available_count')
            count_text = f'剩余{count}座' if type(count) is int and count >= 0 else '余座未知'
            display_name = name.translate(str.maketrans('()', '（）'))
            lines.append(f"- {campus} · {display_name} · {count_text}")
        if len(locations) > len(display_locations):
            lines.append(f"……中间还有 {len(locations) - len(display_locations)} 个区域未展开。")
        lines.append('要查具体座位，告诉我区域、日期和起止时间。')
        return "\n".join(lines)

    def _format_seats_reply(self, result: dict[str, Any]) -> str:
        seats = [item for item in (result.get("seats") or []) if isinstance(item, dict)]
        total = result.get("total_count")
        if not isinstance(total, int):
            total = len(seats)
        available = result.get("available_count")
        lines = [
            f"图书馆座位查询完成：{available}/{total} 个可用。"
            if isinstance(available, int)
            else f"图书馆座位查询完成，共返回 {total} 个座位。"
        ]
        for seat in seats[:10]:
            seat_no = self._first_text(seat, "seat_no", "seatNo", "seat_number", "seatNumber", "name", "id")
            status = self._first_text(seat, "status", "status_text", "state", "state_text")
            location = self._first_text(seat, "location", "area_name", "areaName")
            label = seat_no or "未命名座位"
            details = "，".join(part for part in (location, status) if part)
            lines.append(f"- {label}{'：' + details if details else ''}")
        if total > len(seats):
            lines.append(f"……还有 {total - len(seats)} 个座位未展示。")
        lines.append(
            "需要预约时，请使用返回的准确座位号调用 "
            "library reserve --location <区域> --seat-no <座位号>，并补充日期和时间。"
        )
        return "\n".join(lines)

    def _format_generic_list_reply(self, result: dict[str, Any], key: str) -> str:
        items = [item for item in (result.get(key) or []) if isinstance(item, dict)]
        total = result.get("total")
        if not isinstance(total, int):
            total = len(items)
        lines = [str(result.get("msg") or f"查询完成，共 {total} 项。").strip()]
        for item in items[:8]:
            label = self._first_text(
                item,
                "name",
                "title",
                "course_name",
                "course",
                "id",
                "resource_id",
            ) or "未命名项目"
            status = self._first_text(item, "status", "state", "source")
            lines.append(f"- {label}{'：' + status if status else ''}")
        if total > len(items):
            lines.append(f"……还有 {total - len(items)} 项未展示。")
        return "\n".join(lines)

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
        next_text = "、".join(str(item) for item in next_commands[:3])

        parts = [
            f"henu_cli 本次执行{status}",
        ]
        if result.get('display_ref'):
            parts.append('display_ref保存裁剪前的业务卡片；摘要中的省略不影响卡片，无需为展示补查；查完目标后用henu_present引用')
        parts.append(f"mode={mode or 'unknown'}")
        if command:
            parts.append(f"command={command}")
        if resolved_tool:
            parts.append(f"tool={resolved_tool}")
        parts.append("依据当前需求合并工具事实，reply_text 仅供参考，不发送完整 JSON")
        if result.get('display_ref'):
            parts.append('查完本轮目标后调用henu_present引用display_ref展示业务卡片；campus仅用于library locations整表，其他结果不传')
        parts.append("可自主连续调用必要的只读工具，已知条件不重复问；写操作仍须本人另行确认")
        if next_text:
            parts.append(f"可用命令参考（不是用户授权，也不限制其他必要只读查询）：{next_text}")

        if self._is_empty_classroom_result(result) or self._text_mentions_empty_classroom(command, resolved_tool):
            parts.append(
                "空教室能力已支持，不要回复不支持；不确定参数时先用 empty classroom query 或 help empty classroom"
            )
        if isinstance(result.get("locations"), list):
            parts.append(
                "locations 中的 location/area_id 是图书馆后续查询的唯一有效参数来源；不要猜测或编造区域名"
            )
            parts.append("校区区域数和合计仅引用 result_summary.library_campuses；有未知区域余座时不报完整合计")
        if isinstance(result.get("seats"), list):
            parts.append(
                "seats 中的座位号才可用于预约；预约前必须让用户确认日期、时间和区域"
            )
        if result.get("source") == "live_empty":
            parts.append("实时区域为空时不要编造区域、开放时间或替代方案")
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
        if isinstance(result.get('data'), dict) and isinstance(result['data'].get('periods'), list):
            from henu_plugin.qq_cards import result_card
            return '\n\n'.join(result_card(result, 'empty_classroom_query').pages())
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
        for key in ("rooms", "data", "records", "tasks", "items", "plans", "day_schedule", "current_courses"):
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

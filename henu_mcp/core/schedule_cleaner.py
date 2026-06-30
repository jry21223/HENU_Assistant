from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from lxml import html

# Windows 时区支持
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

def _now_dt() -> datetime:
    """获取当前北京时间（带时区信息）"""
    try:
        return datetime.now(ZoneInfo("Asia/Shanghai"))
    except Exception:
        # 如果时区信息不可用，使用 UTC+8 手动计算
        return datetime.utcnow() + timedelta(hours=8)


WEEKDAY_ORDER = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
SLOT_FALLBACK = {"一": "第1-2节", "二": "第3-4节", "三": "第6-7节", "四": "第9-10节", "五": "第11-12节"}
SLOT_ORDER = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5}
SECTION_MAP = {"上午": "上午", "下午": "下午", "晚上": "晚上"}
WEEKDAY_CHAR_MAP = {
    "一": "星期一",
    "二": "星期二",
    "三": "星期三",
    "四": "星期四",
    "五": "星期五",
    "六": "星期六",
    "日": "星期日",
    "天": "星期日",
}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _norm_no_space(text: str) -> str:
    return re.sub(r"\s+", "", text or "").strip()


def _extract_meta(doc: html.HtmlElement) -> dict[str, str]:
    meta: dict[str, str] = {}
    info_tables = doc.xpath("(//table)[1]")
    if not info_tables:
        return meta

    texts = [_norm("".join(td.xpath(".//text()"))) for td in info_tables[0].xpath(".//tr/td")]
    for item in texts:
        if "学号：" in item:
            meta["student_id"] = item.split("学号：", 1)[1].strip()
        elif "姓名：" in item:
            meta["name"] = item.split("姓名：", 1)[1].strip()
        elif "所在班级：" in item:
            meta["class_name"] = item.split("所在班级：", 1)[1].strip()
        elif "课程门数：" in item:
            meta["summary"] = item
    return meta


def _extract_weekdays(table: html.HtmlElement) -> list[str]:
    rows = table.xpath(".//tr")
    if not rows:
        return []
    header_cells = rows[0].xpath("./td")[1:]
    weekdays = [_norm_no_space("".join(td.xpath(".//text()"))) for td in header_cells]
    weekdays = [item for item in weekdays if item]
    return weekdays


def _period_from_slot(slot_key: str, week_time: str) -> str:
    match = re.search(r"\[(\d+-\d+)\]", week_time or "")
    if match:
        return f"第{match.group(1)}节"
    return SLOT_FALLBACK.get(slot_key, "")


def _period_sort_key(item: dict[str, str]) -> int:
    period = str(item.get("period", ""))
    match = re.search(r"第(\d+)", period)
    if match:
        return int(match.group(1))
    return 99


def _parse_schedule_grid_doc(doc: html.HtmlElement) -> dict[str, Any]:
    tables = doc.xpath("//table[@id='mytable']")
    if not tables:
        raise ValueError("未找到课表主表格(id=mytable)")

    table = tables[0]
    rows = table.xpath(".//tr")
    weekdays = _extract_weekdays(table)
    if not weekdays:
        raise ValueError("未解析到星期表头")

    schedule: dict[str, list[dict[str, str]]] = {day: [] for day in weekdays}

    for row in rows[1:]:
        tds = row.xpath("./td")
        if not tds:
            continue

        cell0 = _norm_no_space("".join(tds[0].xpath(".//text()")))
        if cell0 == "中午":
            continue

        section = ""
        slot_key = ""
        start_idx = 1

        if cell0 in SECTION_MAP:
            section = SECTION_MAP[cell0]
            slot_key = _norm_no_space("".join(tds[1].xpath(".//text()"))) if len(tds) > 1 else ""
            start_idx = 2
        else:
            slot_key = cell0
            if slot_key in ("一", "二"):
                section = "上午"
            elif slot_key in ("三", "四"):
                section = "下午"
            elif slot_key == "五":
                section = "晚上"

        if slot_key not in SLOT_FALLBACK:
            continue

        weekday_cells = tds[start_idx : start_idx + len(weekdays)]
        for idx, td in enumerate(weekday_cells):
            day = weekdays[idx]
            blocks = td.xpath(".//div[.//font]")
            if not blocks:
                continue

            for block in blocks:
                parts = [_norm(x) for x in block.xpath(".//text()")]
                parts = [item for item in parts if item]
                if not parts:
                    continue

                course = parts[0]
                teacher = parts[1] if len(parts) > 1 else ""
                week_time = parts[2] if len(parts) > 2 else ""
                location = parts[3] if len(parts) > 3 and parts[3] else "未标注"

                schedule[day].append(
                    {
                        "section": section,
                        "period": _period_from_slot(slot_key, week_time),
                        "period_key": slot_key,
                        "course": course,
                        "teacher": teacher,
                        "time": week_time,
                        "location": location,
                    }
                )

    for day, items in schedule.items():
        items.sort(key=lambda row: SLOT_ORDER.get(row.get("period_key", ""), 99))

    return {
        "generated_at": _now_dt().isoformat(timespec="seconds"),
        "meta": _extract_meta(doc),
        "schedule": schedule,
    }


def _pick_course_name(cells: list[str]) -> str:
    for idx in (2, 1, 3):
        if idx < len(cells):
            candidate = re.sub(r"\[.*?\]", "", cells[idx]).strip()
            if candidate and not re.fullmatch(r"\d+", candidate):
                return candidate
    for cell in cells:
        candidate = re.sub(r"\[.*?\]", "", cell).strip()
        if not candidate or re.fullmatch(r"\d+", candidate):
            continue
        if re.search(r"(?:^|\s)周?\s*[一二三四五六日天]\[\d", candidate):
            continue
        return candidate
    return "未命名课程"


def _pick_teacher_name(cells: list[str]) -> str:
    for idx in (6, 5, 7):
        if idx < len(cells):
            candidate = re.sub(r"\[.*?\]", "", cells[idx]).strip()
            if candidate and not re.fullmatch(r"\d+", candidate):
                return candidate
    return ""


def _pick_time_location(cells: list[str]) -> str:
    for idx in (10, len(cells) - 1):
        if 0 <= idx < len(cells):
            candidate = cells[idx].strip()
            if candidate and re.search(r"[一二三四五六日天]\[\d", candidate):
                return candidate
    for cell in cells:
        if re.search(r"[一二三四五六日天]\[\d", cell):
            return cell.strip()
    return ""


def _parse_schedule_list_doc(doc: html.HtmlElement) -> dict[str, Any]:
    rows = doc.xpath("//tbody/tr")
    if not rows:
        raise ValueError("未找到课程列表数据")

    schedule: dict[str, list[dict[str, str]]] = {day: [] for day in WEEKDAY_ORDER}
    parsed_count = 0

    for row in rows:
        cells = [_norm("".join(td.xpath(".//text()"))) for td in row.xpath("./td")]
        if len(cells) < 4:
            continue

        time_location = _pick_time_location(cells)
        if not time_location:
            continue

        course = _pick_course_name(cells)
        teacher = _pick_teacher_name(cells)
        parts = [segment.strip() for segment in re.split(r"[；;]", time_location) if segment.strip()]

        for part in parts:
            match = re.search(r"(?:^|\s)(?:周\s*)?([一二三四五六日天])\[(\d+(?:-\d+)?)\]", part)
            if not match:
                continue

            day_cn = WEEKDAY_CHAR_MAP.get(match.group(1), "")
            if day_cn not in schedule:
                continue

            period_raw = match.group(2)
            location = ""
            if "]" in part:
                location = part.split("]", 1)[1].strip()
                location = re.sub(r"^\d+(?:-\d+)?周\s*", "", location).strip()
                location = location.lstrip("，,、 ")
            if not location:
                location = "未标注"

            schedule[day_cn].append(
                {
                    "section": "",
                    "period": f"第{period_raw}节",
                    "period_key": period_raw.split("-", 1)[0],
                    "course": course,
                    "teacher": teacher,
                    "time": part,
                    "location": location,
                }
            )
            parsed_count += 1

    if parsed_count == 0:
        raise ValueError("课程列表中未解析到可用课程数据")

    for day in WEEKDAY_ORDER:
        schedule[day].sort(key=_period_sort_key)

    return {
        "generated_at": _now_dt().isoformat(timespec="seconds"),
        "meta": _extract_meta(doc),
        "schedule": schedule,
    }


def parse_schedule_grid_html(html_text: str) -> dict[str, Any]:
    doc = html.fromstring(html_text)
    if doc.xpath("//table[@id='mytable']"):
        return _parse_schedule_grid_doc(doc)
    return _parse_schedule_list_doc(doc)


def render_schedule_markdown(schedule_data: dict[str, Any]) -> str:
    meta = schedule_data.get("meta") or {}
    schedule = schedule_data.get("schedule") or {}

    lines: list[str] = [f"# 课表整理（{_now_dt().strftime('%Y-%m-%d %H:%M:%S')}）"]
    if meta:
        lines.append("")
        lines.append(f"- 学号：{meta.get('student_id', '')}")
        lines.append(f"- 姓名：{meta.get('name', '')}")
        lines.append(f"- 班级：{meta.get('class_name', '')}")
        if meta.get("summary"):
            lines.append(f"- 概览：{meta['summary']}")

    for day in WEEKDAY_ORDER:
        lines.append("")
        lines.append(f"### {day}")
        day_items = schedule.get(day, [])
        if not day_items:
            lines.append("- 当天无课程安排。")
            continue
        for item in day_items:
            section = str(item.get("section", "")).strip()
            period = str(item.get("period", "")).strip()
            if section:
                lines.append(f"- {section}（{period}）")
            else:
                lines.append(f"- {period}")
            lines.append(f"  - 课程：{item.get('course', '')}")
            lines.append(f"  - 老师：{item.get('teacher', '')}")
            lines.append(f"  - 时间：{item.get('time', '')}")
            lines.append(f"  - 地点：{item.get('location', '')}")

    return "\n".join(lines)


def save_clean_files(
    output_dir: Path,
    source_file: str,
    schedule_data: dict[str, Any],
    timestamp: str | None = None,
) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = timestamp or _now_dt().strftime("%Y%m%d_%H%M%S")
    schedule_data = dict(schedule_data)
    schedule_data["source_file"] = source_file

    latest_json = output_dir / "schedule_clean_latest.json"
    latest_md = output_dir / "schedule_clean_latest.md"
    stamped_json = output_dir / f"schedule_clean_{ts}.json"
    stamped_md = output_dir / f"schedule_clean_{ts}.md"

    md_text = render_schedule_markdown(schedule_data)
    json_text = json.dumps(schedule_data, ensure_ascii=False, indent=2)

    latest_json.write_text(json_text, encoding="utf-8")
    latest_md.write_text(md_text, encoding="utf-8")
    stamped_json.write_text(json_text, encoding="utf-8")
    stamped_md.write_text(md_text, encoding="utf-8")

    return {
        "clean_schedule_json": str(latest_json),
        "clean_schedule_md": str(latest_md),
        "clean_schedule_json_stamped": str(stamped_json),
        "clean_schedule_md_stamped": str(stamped_md),
    }


def clean_schedule_grid_file(grid_file: str | Path, output_dir: str | Path) -> dict[str, Any]:
    grid_path = Path(grid_file)
    if not grid_path.exists():
        raise FileNotFoundError(f"文件不存在: {grid_path}")

    html_text = grid_path.read_text(encoding="utf-8", errors="ignore")
    parsed = parse_schedule_grid_html(html_text)
    files = save_clean_files(Path(output_dir), str(grid_path), parsed)
    return {"data": parsed, "files": files}


def load_latest_clean_schedule(output_dir: str | Path) -> dict[str, Any]:
    latest = Path(output_dir) / "schedule_clean_latest.json"
    if not latest.exists():
        raise FileNotFoundError(f"未找到: {latest}")
    return json.loads(latest.read_text(encoding="utf-8"))

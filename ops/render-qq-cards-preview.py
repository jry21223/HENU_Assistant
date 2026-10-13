#!/usr/bin/env python3
"""Render synthetic examples of the exact card Markdown, entirely offline."""
import html
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from henu_plugin.qq_cards import result_card, text_card

FIXTURES = [
    ("schedule_query", {"success": True, "date": "2026-10-13", "now": "2026-10-13 07:50:00", "week_filter_applied": False, "msg": "当前没有正在上的课，下一节课 08:00-09:40 数据结构", "next_course": {"course": "数据结构", "clock_start": "08:00", "clock_end": "09:40", "classroom": "十号楼101"}, "next_commands": ["schedule day --date 2026-10-13"]}),
    ("library_query", {"success": True, "date": "2026-10-14", "area": "金明馆北区", "time_window": "08:00–10:00", "available_count": 2, "total_count": 32, "seats": [{"no": "A-101", "status": "可预约"}, {"no": "A-102", "status": "可预约"}], "next_commands": ["请说明座位号，核对日期、时段后发起预约。"]}),
    ("seminar_query", {"success": True, "date": "2026-10-14", "rooms": [{"nameMerge": "金明馆 · 研讨室301", "id": "sample-room", "capacity": 6, "available_slots": [{"start_time": "14:00", "end_time": "16:00"}]}], "next_commands": ["请说明研讨室、主题、日期与起止时间，私聊发起预约。"]}),
    ("library_reserve", {"success": False, "confirmation_required": True, "msg": "即将预约：金明馆北区 A-101，2026-10-14 08:00–10:00。本次尚未提交。"}),
    ("library_query", {"success": False, "error_code": "auth_required", "msg": "未绑定账号，无法获取实时可预约区域。", "seats": []}),
    ("empty_classroom_query", {"success": True, "msg": "已查询金明校区十号楼第8周星期二第2大节。", "rooms": [], "next_commands": ["补充其他楼房或时段后重新查询。"]}),
]

STYLE = '''body{margin:0;background:#edf1f7;color:#17233b;font:15px/1.55 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}header{padding:30px 36px 20px}h1{font-size:26px;margin:0 0 6px}header p{color:#69778c;margin:0}main{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:22px;padding:12px 36px 36px}.card{background:white;border:1px solid #dae3ed;border-radius:16px;padding:22px;box-shadow:0 8px 24px #192e5510;overflow-wrap:anywhere}.card h2{font-size:19px;margin:0 0 18px;color:#125fc2}.line{margin:5px 0}.gap{height:8px}.source{padding:0 36px 24px;font-size:12px;color:#69778c}@media(max-width:900px){main{grid-template-columns:repeat(2,minmax(0,1fr))}}'''


def render(md):
    lines = []
    for line in md.splitlines():
        if line.startswith("# "):
            lines.append("<h2>" + html.escape(line[2:]) + "</h2>")
            continue
        escaped = html.escape(line)
        escaped = re.sub(r"\\([\\`*_{}\[\]()#!|~])", r"\1", escaped)
        escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
        lines.append('<div class="line">' + escaped + '</div>' if line else '<div class="gap"></div>')
    return '<article class="card">' + "".join(lines) + '</article>'


def main():
    out = Path(__file__).resolve().parents[1] / "docs/previews"
    out.mkdir(exist_ok=True)
    pages = [result_card(data, tool).pages()[0] for tool, data in FIXTURES]
    for name, subset in (("queries", pages[:3]), ("states", pages[3:])):
        doc = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>HENU QQ 卡片 · 本地预览</title><style>' + STYLE + '</style><header><h1>HENU Assistant · QQ 功能卡片</h1><p>本地 Markdown 预览 · 合成数据 · 未连接 QQ · 无交互按钮</p></header><main>' + "".join(render(page) for page in subset) + '</main><div class="source">由 henu_plugin/qq_cards.py 实际输出生成；Safari 仅近似呈现文档支持的 Markdown，QQ 客户端表现待独立验收。</div></html>'
        (out / (name + ".html")).write_text(doc)
    (out / "representative.md").write_text("\n\n---\n\n".join(pages) + "\n")
    print("Generated docs/previews/{queries,states}.html and representative.md")


if __name__ == "__main__":
    main()

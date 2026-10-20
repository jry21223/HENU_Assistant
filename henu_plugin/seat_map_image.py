"""An aligned HD diagram preserving school seat placement and states."""

from __future__ import annotations

from collections import Counter
import io
import math
from pathlib import Path
from statistics import median

COLORS = {
    "free": (35, 155, 112),
    "reserved": (220, 74, 74),
    "in_use": (220, 135, 45),
    "closed": (93, 106, 122),
    "unknown": (157, 165, 176),
}


def seat_state(row):
    status, name = str(row.get("status")), str(row.get("status_name") or "")
    if status == "1" and name in {"", "空闲", "可用"}:
        return "free"
    if status == "2" and name == "已预约":
        return "reserved"
    if name in {"使用中", "在用", "已占用"}:
        return "in_use"
    if name in {"暂停使用", "已关闭", "不可用"}:
        return "closed"
    return "unknown"


def render_seat_map(title, window, rows, fetched):
    from PIL import Image, ImageDraw, ImageFont

    if not rows or len(rows) > 2000:
        raise ValueError("seat geometry missing or too large")
    geometry = []
    seen = set()
    for row in rows:
        name = str(row.get("no") or "")
        if not name or len(name) > 8 or name in seen:
            raise ValueError("invalid seat label")
        seen.add(name)
        coordinates = [float(row[k]) for k in ("point_x", "point_y", "width", "height")]
        x, y, w, h = coordinates
        if (
            not all(math.isfinite(v) for v in coordinates)
            or not 0 <= x <= 100
            or not 0 <= y <= 100
            or not 0 < w <= 100
            or not 0 < h <= 100
            or x + w > 101
            or y + h > 101
        ):
            raise ValueError("invalid school coordinates")
        geometry.append((row, name, x, y, w, h))
    # The school supplies hand-drawn hit boxes, not a finished layout grid.
    # Merge only sub-seat coordinate jitter; retain larger gaps and room shape.
    standard_w = median(item[4] for item in geometry)
    standard_h = median(item[5] for item in geometry)

    def aligned(values, tolerance):
        groups = []
        for value in sorted(set(values)):
            if not groups or value - groups[-1][0] > tolerance:
                groups.append([value])
            else:
                groups[-1].append(value)
        return {value: median(group) for group in groups for value in group}

    columns = aligned([x + w / 2 for _, _, x, _, w, _ in geometry], standard_w * 0.4)
    rows_y = aligned([y + h / 2 for _, _, _, y, _, h in geometry], standard_h * 0.4)
    occupied = [
        (columns[x + w / 2], rows_y[y + h / 2]) for _, _, x, y, w, h in geometry
    ]
    if len(set(occupied)) != len(occupied):
        raise ValueError("school seat positions overlap after alignment")
    by_row, by_column = {}, {}
    for x, y in occupied:
        by_row.setdefault(y, []).append(x)
        by_column.setdefault(x, []).append(y)
    # A column used only in another part of the room must not shrink every
    # seat. Only actual neighbours in the same row/column constrain the size.
    gaps_x = [
        b - a
        for values in by_row.values()
        for a, b in zip(sorted(values), sorted(values)[1:])
    ]
    gaps_y = [
        b - a
        for values in by_column.values()
        for a, b in zip(sorted(values), sorted(values)[1:])
    ]
    width = min(standard_w * 0.92, min(gaps_x) * 0.84 if gaps_x else standard_w) * 39.36
    height = (
        min(standard_h * 0.92, min(gaps_y) * 0.84 if gaps_y else standard_h) * 22.14
    )
    size_px = max(12, round(min(width, height)))
    image = Image.new("RGB", (4096, 2560), "white")
    draw = ImageDraw.Draw(image)
    font_file = str(Path(__file__).with_name("assets") / "NotoSansSC-SeatMap.otf")

    def font(size):
        return ImageFont.truetype(font_file, size=size)

    draw.text((80, 28), str(title)[:60], font=font(52), fill=(25, 35, 48))
    draw.text(
        (80, 112),
        str(window) + "  查询 " + str(fetched),
        font=font(34),
        fill=(65, 75, 90),
    )
    counts = Counter(seat_state(r) for r in rows)
    for index, (state, label) in enumerate(
        [
            ("free", "空闲"),
            ("reserved", "已预约"),
            ("in_use", "使用中"),
            ("closed", "不可用"),
            ("unknown", "未知"),
        ]
    ):
        x = 80 + index * 700
        draw.rounded_rectangle((x, 198, x + 38, 236), radius=7, fill=COLORS[state])
        draw.text(
            (x + 56, 190),
            label + " " + str(counts[state]),
            font=font(32),
            fill=(45, 55, 68),
        )
    for row, name, x, y, w, h in geometry:
        center_x = 80 + columns[x + w / 2] * 39.36
        center_y = 300 + rows_y[y + h / 2] * 22.14
        left, top = round(center_x - size_px / 2), round(center_y - size_px / 2)
        right, bottom = left + size_px, top + size_px
        draw.rounded_rectangle(
            (left, top, right, bottom),
            radius=8,
            fill=COLORS[seat_state(row)],
            outline="white",
            width=2,
        )
        size = max(12, min(34, int((right - left) / (len(name) * 0.7))))
        draw.text(
            ((left + right) / 2, (top + bottom) / 2),
            name,
            font=font(size),
            fill="white",
            anchor="mm",
        )
    draw.text(
        (80, 2508),
        "学校坐标重绘 · 颜色仅反映所选时段 · 门窗通道未标注",
        font=font(26),
        fill=(65, 75, 90),
    )
    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()

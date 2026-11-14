#!/usr/bin/env python3
"""Render synthetic 200- and 2000-seat font regression images."""
from __future__ import annotations

import json
import math
from pathlib import Path
import platform
import sys

from PIL import Image, __version__ as pillow_version

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from henu_plugin.seat_map_image import render_seat_map

OUT = ROOT / "output" / "classroom-preview"


def rows(count: int) -> list[dict]:
    columns = 40 if count >= 1000 else 20
    row_count = math.ceil(count / columns)
    dx, dy = 98 / columns, 98 / row_count
    return [
        {
            "no": str(index + 1).zfill(4),
            "point_x": 0.5 + (index % columns) * dx,
            "point_y": 0.5 + (index // columns) * dy,
            "width": min(1.8, dx * 0.72),
            "height": min(3.2, dy * 0.72),
            "status": "1" if index % 3 else "2",
            "status_name": "空闲" if index % 3 else "已预约",
        }
        for index in range(count)
    ]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    records = []
    for count in (200, 2000):
        data = render_seat_map(
            "金明校区 · 701 七层南自习室",
            "2026-11-14 08:00–23:00",
            rows(count),
            "12:00",
        )
        path = OUT / f"seat-{count}.png"
        path.write_bytes(data)
        with Image.open(path) as image:
            image.load()
            record = {
                "file": path.name,
                "width": image.width,
                "height": image.height,
                "png_bytes": len(data),
                "format": image.format,
            }
        if record["format"] != "PNG" or len(data) > 2 * 1024 * 1024:
            raise ValueError(f"invalid or oversized preview: {path.name}")
        records.append(record)

    report = {
        "synthetic": True,
        "school_api_called": False,
        "qq_sent": False,
        "python": platform.python_version(),
        "pillow": pillow_version,
        "images": records,
    }
    (OUT / "seat-map-font-preview.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

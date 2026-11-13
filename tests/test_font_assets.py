import json

import pytest
from PIL import Image, ImageDraw

from henu_plugin.font_assets import ASSETS, draw_text, new_font_cache


def test_henu_ui_weights_render_distinctly_and_use_a_bounded_local_cache():
    fonts = new_font_cache()
    assert fonts.cache_info().maxsize == 32

    samples = set()
    for index, (weight, style) in enumerate(
        (("regular", "Regular"), ("medium", "Medium"), ("bold", "Bold"))
    ):
        face = fonts(42, weight)
        assert face.getname() == ("HENU UI", style)
        image = Image.new("RGB", (260, 70), "white")
        draw_text(ImageDraw.Draw(image), (8, 8), "空教室 18F", 42, fonts,
                  weight=weight, fill="black")
        samples.add(image.tobytes())
    assert len(samples) == 3
    assert fonts(42, "regular") is fonts(42, "regular")

    for size in range(8, 48):
        fonts(size, "regular")
    assert fonts.cache_info().currsize == 32


def test_coverage_has_gb2312_and_requested_label_and_time_glyphs():
    manifest = json.loads((ASSETS / "HENUUI-coverage.json").read_text(encoding="utf-8"))
    codepoints = manifest["codepoints"].split(",")
    coverage = {int(value, 16) for value in codepoints}
    gb2312 = set(range(0x20, 0x7F))
    for lead in range(0xA1, 0xF8):
        for trail in range(0xA1, 0xFF):
            try:
                gb2312.update(map(ord, bytes((lead, trail)).decode("gb2312")))
            except UnicodeDecodeError:
                pass
    assert gb2312 <= coverage
    assert set(map(ord, "厕所卫浴／第10大节 08:00–09:40")) <= coverage
    assert len(coverage) == len(codepoints)
    assert manifest["family"] == "HENU UI"
    assert manifest["formerNotoUnsupported"] == ["2713"]


def test_uncovered_text_raises_for_existing_image_fallbacks():
    from henu_plugin.seat_map_image import render_seat_map

    image = Image.new("RGB", (100, 40), "white")
    with pytest.raises(ValueError, match=r"U\+E000"):
        draw_text(ImageDraw.Draw(image), (0, 0), "\uE000", 16, new_font_cache())

    with pytest.raises(ValueError, match=r"U\+E000"):
        render_seat_map(
            "座位图 \uE000", "2026-11-14 08:00–23:00",
            [{"no": "1", "point_x": 10, "point_y": 10, "width": 5,
              "height": 5, "status": "1", "status_name": "空闲"}],
            "12:00",
        )

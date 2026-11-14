"""Request-local Pillow fonts with a shared, build-verified glyph set."""
from __future__ import annotations

from array import array
from bisect import bisect_left
from functools import lru_cache
import json
from pathlib import Path

ASSETS = Path(__file__).with_name("assets")
FONT_FILES = {
    "regular": "HENUUI-Regular.ttf",
    "medium": "HENUUI-Medium.ttf",
    "bold": "HENUUI-Bold.ttf",
}


@lru_cache(maxsize=1)
def _covered_codepoints() -> array:
    data = json.loads((ASSETS / "HENUUI-coverage.json").read_text(encoding="utf-8"))
    return array("I", (int(value, 16) for value in data["codepoints"].split(",")))


def new_font_cache():
    """Return a per-render font cache; never share FreeType objects between requests."""
    from PIL import ImageFont

    @lru_cache(maxsize=32)
    def load(size: int, weight: str = "regular"):
        if weight not in FONT_FILES:
            raise ValueError(f"unknown font weight: {weight}")
        return ImageFont.truetype(str(ASSETS / FONT_FILES[weight]), size=size)

    return load


def draw_text(draw, xy, value, size: int, fonts, *, weight: str = "regular", **kwargs) -> None:
    text = str(value)
    covered = _covered_codepoints()
    for char in text:
        codepoint = ord(char)
        index = bisect_left(covered, codepoint)
        if index == len(covered) or covered[index] != codepoint:
            raise ValueError(f"HENU UI font missing U+{codepoint:04X}")
    draw.text(xy, text, font=fonts(size, weight), **kwargs)

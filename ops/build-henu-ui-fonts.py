#!/usr/bin/env python3
"""Build and verify the request-time HENU UI font subsets."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

from fontTools import subset
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "henu_plugin" / "assets"
DEFAULT_SOURCE_DIR = ROOT.parent / "output"
FONT_SOURCES = {
    "regular": ("PingFang Regular.ttf", 400, "Regular"),
    "medium": ("PingFang Medium.ttf", 500, "Medium"),
    "bold": ("PingFang Bold.ttf", 700, "Bold"),
}
SOURCE_SHA256 = {
    "PingFang Regular.ttf": "26b017dfb34a8aa0059f47eda6f90d7ffd19a24912dc1129abc24867a1385353",
    "PingFang Medium.ttf": "ddf051a97717d35e90d297eab1fbb75bab48120983586cf2a70be9fd3dfad2c8",
    "PingFang Bold.ttf": "96ec2276d8e77453e0e44f1e9633ee513396f2eeabb3d330f9e9306343b25fd9",
}
FONT_FILES = {weight: f"HENUUI-{title}.ttf"
              for weight, (_, _, title) in FONT_SOURCES.items()}
MANIFEST = ASSETS / "HENUUI-coverage.json"
GLYPH_ALIASES = {0x30FB: 0x00B7}
LEGACY_UNSUPPORTED = {0x2713}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def unicode_cmap(font: TTFont) -> set[int]:
    return {codepoint for table in font["cmap"].tables if table.isUnicode()
            for codepoint in table.cmap}


def legacy_codepoints() -> set[int]:
    if MANIFEST.is_file():
        metadata = json.loads(MANIFEST.read_text(encoding="utf-8"))
        if "formerNotoCodepoints" in metadata:
            return {int(value, 16) for value in metadata["formerNotoCodepoints"]}
    values = set()
    for path in ASSETS.glob("NotoSansSC-*.*tf"):
        values.update(unicode_cmap(TTFont(path)))
    return values


def desired_codepoints(source_dir: Path) -> set[int]:
    values = set(range(0x20, 0x7F))
    for lead in range(0xA1, 0xF8):
        for trail in range(0xA1, 0xFF):
            try:
                values.update(ord(char) for char in bytes((lead, trail)).decode("gb2312"))
            except UnicodeDecodeError:
                pass

    values.update(map(ord, "厕所／第10大节 08:00–09:40"))
    if MANIFEST.is_file():
        stored = json.loads(MANIFEST.read_text(encoding="utf-8"))["codepoints"]
        values.update(int(value, 16) for value in
                      (stored.split(",") if isinstance(stored, str) else stored))
    for path in (ROOT / "henu_plugin" / "classroom_map.py",
                 ROOT / "henu_plugin" / "classroom_floorplans.py",
                 ROOT / "henu_plugin" / "seat_map_image.py",
                 ASSETS / "classroom-plans.json"):
        values.update(codepoint for codepoint in map(ord, path.read_text(encoding="utf-8"))
                      if codepoint not in {0x09, 0x0A, 0x0D})
    legacy = legacy_codepoints()
    source_maps = [unicode_cmap(TTFont(source_dir / filename))
                   for filename, _, _ in FONT_SOURCES.values()]
    supported = set.intersection(*source_maps)
    supported.update(codepoint for codepoint, alias in GLYPH_ALIASES.items()
                     if all(alias in cmap for cmap in source_maps))
    missing_text = values - supported
    if missing_text:
        sample = ", ".join(f"U+{value:04X}" for value in sorted(missing_text)[:8])
        raise ValueError(f"PingFang does not cover renderer text: {sample}")
    unsupported_legacy = legacy - supported
    if unsupported_legacy != LEGACY_UNSUPPORTED:
        sample = ", ".join(f"U+{value:04X}" for value in sorted(unsupported_legacy))
        raise ValueError(f"unexpected unsupported former-font glyphs: {sample}")
    values.update(legacy & supported)
    return values


def set_names(font: TTFont, style: str) -> None:
    table = font["name"]
    names = {
        1: "HENU UI",
        2: style,
        3: f"HENU UI 1.0 {style}",
        4: f"HENU UI {style}",
        6: f"HENUUI-{style}",
        16: "HENU UI",
        17: style,
    }
    table.names = [record for record in table.names if record.nameID not in names]
    for name_id, value in names.items():
        table.setName(value, name_id, 3, 1, 0x409)


def subset_font(source: Path, destination: Path, codepoints: set[int],
                weight: int, style: str) -> set[int]:
    font = TTFont(source)
    source_cmap = unicode_cmap(font)
    aliases = {codepoint for codepoint, source_codepoint in GLYPH_ALIASES.items()
               if source_codepoint in source_cmap}
    missing = codepoints - source_cmap - aliases
    if missing:
        sample = ", ".join(f"U+{value:04X}" for value in sorted(missing)[:8])
        raise ValueError(f"{source.name} lacks required characters: {sample}")

    options = subset.Options()
    options.name_IDs = sorted({record.nameID for record in font["name"].names})
    options.name_languages = sorted({record.langID for record in font["name"].names})
    subsetter = subset.Subsetter(options=options)
    subsetter.populate(unicodes=codepoints - aliases)
    subsetter.subset(font)
    for table in font["cmap"].tables:
        if table.isUnicode():
            for codepoint, source_codepoint in GLYPH_ALIASES.items():
                if source_codepoint in table.cmap:
                    table.cmap[codepoint] = table.cmap[source_codepoint]
    set_names(font, style)
    font["OS/2"].usWeightClass = weight
    font["OS/2"].fsSelection &= ~((1 << 5) | (1 << 6))
    if style == "Bold":
        font["OS/2"].fsSelection |= 1 << 5
    elif style == "Regular":
        font["OS/2"].fsSelection |= 1 << 6
    font["head"].macStyle = (font["head"].macStyle | 1) if style == "Bold" else (font["head"].macStyle & ~1)
    font.save(destination)
    return unicode_cmap(TTFont(destination))


def verify() -> dict:
    metadata = json.loads(MANIFEST.read_text(encoding="utf-8"))
    declared = {int(value, 16) for value in metadata["codepoints"].split(",")}
    maps = {}
    if metadata["sourceSha256"] != SOURCE_SHA256:
        raise ValueError("source font hashes do not match the approved inputs")
    if metadata["formerNotoUnsupported"] != [f"{value:04X}" for value in sorted(LEGACY_UNSUPPORTED)]:
        raise ValueError("former-font exception list changed")
    for weight, (_, expected_weight, style) in FONT_SOURCES.items():
        path = ASSETS / FONT_FILES[weight]
        font = TTFont(path)
        actual = unicode_cmap(font)
        if declared != actual:
            raise ValueError(f"{path.name} cmap does not match its declared coverage")
        if font["OS/2"].usWeightClass != expected_weight:
            raise ValueError(f"{path.name} has incorrect weight metadata")
        family = {record.toUnicode() for record in font["name"].names if record.nameID == 1}
        subfamily = {record.toUnicode() for record in font["name"].names if record.nameID == 2}
        if "HENU UI" not in family or style not in subfamily:
            raise ValueError(f"{path.name} has incorrect family/style metadata")
        if "Apple Inc internal use only. Do not distribute." not in {
                record.toUnicode() for record in font["name"].names if record.nameID == 13}:
            raise ValueError(f"{path.name} lost source license metadata")
        if sha256(path) != metadata["outputSha256"][path.name]:
            raise ValueError(f"{path.name} hash does not match the manifest")
        maps[weight] = actual
        cmap = font["cmap"].getcmap(3, 1)
        if cmap is None or any(cmap.cmap.get(codepoint) != cmap.cmap.get(source_codepoint)
                               for codepoint, source_codepoint in GLYPH_ALIASES.items()):
            raise ValueError(f"{path.name} has an invalid compatibility glyph mapping")
    if len({frozenset(cmap) for cmap in maps.values()}) != 1:
        raise ValueError("font weights do not share an identical cmap")
    return {"glyph_count": len(next(iter(maps.values()))),
            "font_bytes": sum((ASSETS / name).stat().st_size for name in FONT_FILES.values()),
            "files": {name: (ASSETS / name).stat().st_size for name in FONT_FILES.values()}}


def build(source_dir: Path) -> dict:
    codepoints = desired_codepoints(source_dir)
    output_maps = {}
    source_hashes = {}
    for weight, (filename, expected_hash, style) in FONT_SOURCES.items():
        source = source_dir / filename
        if not source.is_file():
            raise FileNotFoundError(f"missing source font: {source}")
        actual_hash = sha256(source)
        if actual_hash != SOURCE_SHA256[filename]:
            raise ValueError(f"unexpected source font SHA-256: {filename}")
        source_font = TTFont(source)
        versions = {record.toUnicode() for record in source_font["name"].names
                    if record.nameID == 5}
        if "10.11d9e1" not in versions:
            raise ValueError(f"unexpected PingFang version: {filename}")
        source_hashes[filename] = actual_hash
        output_maps[weight] = subset_font(
            source, ASSETS / FONT_FILES[weight], codepoints, expected_hash, style)

    if len({frozenset(cmap) for cmap in output_maps.values()}) != 1:
        raise ValueError("source font subsets have different cmaps")
    actual_codepoints = next(iter(output_maps.values()))
    if not codepoints <= actual_codepoints:
        raise ValueError(f"font subset misses {len(codepoints - actual_codepoints)} required characters")

    metadata = {
        "family": "HENU UI",
        "sourceFamily": "PingFang SC",
        "sourceVersion": "10.11d9e1",
        "sourceSha256": source_hashes,
        "glyphAliases": {f"U+{codepoint:04X}": f"U+{source_codepoint:04X}"
                         for codepoint, source_codepoint in GLYPH_ALIASES.items()},
        "formerNotoCodepoints": [f"{value:04X}" for value in sorted(legacy_codepoints())],
        "formerNotoUnsupported": [f"{value:04X}" for value in sorted(LEGACY_UNSUPPORTED)],
        "weights": {style: weight for _, weight, style in FONT_SOURCES.values()},
        "codepoints": ",".join(f"{value:04X}" for value in sorted(actual_codepoints)),
        "outputSha256": {filename: sha256(ASSETS / filename)
                         for filename in FONT_FILES.values()},
    }
    MANIFEST.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    return verify()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    args = parser.parse_args()
    try:
        result = verify() if args.verify else build(args.source_dir)
    except (FileNotFoundError, KeyError, OSError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

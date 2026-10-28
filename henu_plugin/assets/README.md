# Seat map font

`NotoSansSC-SeatMap.otf` is a renamed character subset of Noto Sans CJK SC Regular,
used for the seat-map diagram's Chinese labels and numbers. The upstream source
is https://github.com/notofonts/noto-cjk/tree/main/Sans/OTF/SimplifiedChinese .
It is distributed under SIL Open Font License 1.1; see `NotoSansSC-LICENSE.txt`.
The subset includes ASCII, the map legend, and the library region names verified
on 2026-10-20. FontTools is needed only to rebuild the subset, not at runtime.

## Classroom fonts

`NotoSansSC-Classroom.otf` is a renamed HENU Classroom subset of Noto Sans
CJK SC Regular, rebuilt for the two-color classroom plans on 2026-10-29. It
includes the original glyphs plus every heading, legend, note, and catalog
name in `classroom_map.py`, `classroom_floorplans.py` and `classroom-plans.json`.
Source: https://github.com/notofonts/noto-cjk/blob/main/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf
Use FontTools `pyftsubset` with the union of those characters to rebuild it.
Verify the resulting cmap before release; Pillow silently omits missing glyphs.

`NotoSansSC-Classroom-Status.ttf` remains for older callers; the current maps use
the main classroom font. Both fonts use SIL OFL 1.1; see `NotoSansSC-LICENSE.txt`.

# Seat map font

`NotoSansSC-SeatMap.otf` is a renamed character subset of Noto Sans CJK SC Regular,
used for the seat-map diagram's Chinese labels and numbers. The upstream source
is https://github.com/notofonts/noto-cjk/tree/main/Sans/OTF/SimplifiedChinese .
It is distributed under SIL Open Font License 1.1; see `NotoSansSC-LICENSE.txt`.
The subset includes ASCII, the map legend, and the library region names verified
on 2026-10-20. FontTools is needed only to rebuild the subset, not at runtime.

## Classroom fonts

`NotoSansSC-Classroom.otf` is the unchanged font subset already used by the
production classroom renderer. `NotoSansSC-Classroom-Status.ttf` is an additional
Noto Sans SC Regular subset from Google Fonts for the three-state legend,
check/cross/question symbols and the filtered-list annotation. Keeping these
separate preserves existing room-number and heading glyphs. Both use SIL OFL
1.1; see `NotoSansSC-LICENSE.txt`.

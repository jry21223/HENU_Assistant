# Seat map font

`NotoSansSC-SeatMap.otf` is a renamed character subset of Noto Sans CJK SC Regular,
used for the seat-map diagram's Chinese labels and numbers. The upstream source
is https://github.com/notofonts/noto-cjk/tree/main/Sans/OTF/SimplifiedChinese .
It is distributed under SIL Open Font License 1.1; see `NotoSansSC-LICENSE.txt`.
The subset includes ASCII, the map legend, and the library region names verified
on 2026-10-20. FontTools is needed only to rebuild the subset, not at runtime.

`NotoSansSC-Classroom.otf` is a separate subset for classroom diagrams. Source:
installed Noto Sans CJK 2.004, TTC face 27 (NotoSansCJKsc-Regular), under the same
SIL OFL 1.1 license. It includes the diagram's Chinese labels, weekday/period
names and ASCII. Floor-plan photographs and chat screenshots are not packaged.

Rebuild with FontTools using `--font-number=27`,
`--text-file=henu_plugin/classroom_map.py`, `--text='一二三四五六日天第大节'`,
`--unicodes='U+0000-00FF,U+2013,U+2022,U+2192'`, `--no-hinting`, and
`--output-file=henu_plugin/assets/NotoSansSC-Classroom.otf`.

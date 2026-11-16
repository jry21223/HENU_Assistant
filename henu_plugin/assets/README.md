# HENU UI fonts

The classroom and seat renderers share static Noto Sans SC subsets renamed
**HENU UI**: Regular 400, Medium 500, Bold 700. All weights cover the same
characters: ASCII, GB2312, the former Noto subsets, and renderer/catalog text.
Uncovered runtime text continues to use the existing text fallback.

Source: [Google Fonts Noto Sans SC](https://github.com/google/fonts/tree/a85815a42757630ce188fdad368c2dfc444d4773/ofl/notosanssc),
pinned commit `a85815a42757630ce188fdad368c2dfc444d4773`.
The source and subsets are licensed under SIL Open Font License 1.1;
`HENUUI-OFL.txt` preserves the complete copyright and license notice.
Font name records retain the source copyright and license. Subsets use the
family name HENU UI. The coverage manifest records source and output SHA-256.

FontTools is only required for asset rebuilding and verification.
Download `NotoSansSC[wght].ttf` and `OFL.txt` from the pinned source into
`output/pr98-review/font-source/`, install `requirements-fonts.txt`, and run:

```bash
python ops/build-henu-ui-fonts.py
python ops/build-henu-ui-fonts.py --verify
```

`HENUUI-coverage.data` and `classroom-plans.data` contain JSON. Their `.data`
extension keeps ordinary `lbp build` from excluding static resources under
its `*.json` ignore rule. Do not relax that rule to include runtime caches.

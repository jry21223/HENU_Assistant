# HENU UI fonts

The classroom plan, classroom grid, and seat map renderers share three static
PingFang SC subsets, renamed **HENU UI**: Regular 400, Medium 500, and Bold 700.
All three have the same cmap and include ASCII, GB2312, supported characters
from the former Noto subsets, and text used by the renderers and classroom
catalog, including `厕所` and `卫浴`.

PingFang SC 10.11d9e1 source hashes:

| File | SHA-256 |
|---|---|
| PingFang Regular.ttf | `26b017dfb34a8aa0059f47eda6f90d7ffd19a24912dc1129abc24867a1385353` |
| PingFang Medium.ttf | `ddf051a97717d35e90d297eab1fbb75bab48120983586cf2a70be9fd3dfad2c8` |
| PingFang Bold.ttf | `96ec2276d8e77453e0e44f1e9633ee513396f2eeabb3d330f9e9306343b25fd9` |

The source font does not map GB2312 U+30FB `・`; the subset maps it to the
PingFang U+00B7 `·` glyph. The three former Noto subsets also included U+2713,
which PingFang does not contain and these renderers do not use. If runtime text
contains any uncovered character, image generation returns to its existing
text response.

The Apple copyright and license name records are retained. The project
maintainer confirms separate authorization for redistribution of these
subsets; they are not licensed under the SIL Open Font License. FontTools is
needed only to rebuild or verify assets, not by the plugin at runtime.

To rebuild, install `requirements-fonts.txt`, place the three source files in
the workspace-level `output/` directory, then run:

```bash
python ops/build-henu-ui-fonts.py
python ops/build-henu-ui-fonts.py --verify
```

# Classroom query state display

The existing production renderer is now tracked in this branch. Its canvas,
font, floor cards, room geometry and positions are retained. Only the status
semantics, legend and colors change:

- Green `#239b70`, check: 空闲 (no registered class at the requested period).
- Pale terracotta `#f3d8d3`, cross, dark text `#81392f`: 课程占用.
- Gray `#e1e6ec`, question mark: 未核验.

The school schedule query attaches `freshness.roomStates`, containing the exact
term/week/day/period/campus/building scope and explicit unfiltered free and
occupied room names. Missing layout rooms are not inferred occupied. An expired,
failed, truncated, scope-mismatched or partially parsed result is unverified.
Old caches without coverage metadata remain unverified until refreshed normally.
Five daily periods are classified independently. Capacity/keyword filtering
affects the list; the image explains that it shows whole-building course states.

The occupied text/fill contrast is 6.08:1, and border/white contrast is 5.39:1.
Text and check/cross/question symbols supplement color. No coordinates, layout
data, school write operations or account-binding flows are changed.

Deployment must overlay only `henu_plugin/classroom_map.py`,
`campus_core/empty_classroom/query.py`, and the new status font onto the inspected
live source, after matching before hashes and draining active requests. Preserve
all other hotfixes, data, credentials, master keys and one-time execution markers.

This release is for LangBot. The shared query coverage metadata is additive;
MCP and agent-skill delivery branches are not deployed or modified by this release.
Their UI has no classroom image renderer; parity of the optional metadata is
deferred rather than implied.

Validation covers public query output and rendered PNGs: complete states, missing
coverage, stale and partial data, refresh failures, cache scope, filtered lists,
parse errors, failed queries, and daily-slot independence. Real QQ acceptance
must record both the result cards and actual received image files separately.

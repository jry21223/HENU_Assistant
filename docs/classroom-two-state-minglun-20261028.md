# Classroom map release: approved two-color design and Minglun plans

Approved in the design review on 2026-10-28, deployed only after the user's renewed release request.

- Green means course-unoccupied with complete, current, same-scope school evidence. Gray combines occupied and unverified. No check/cross/question glyphs. All-day uses five plain dots; a single period fills the room.
- Preserve freshness, incomplete-query and cross-building safeguards, school IDs, QQ text fallback, owner isolation, and existing query behavior.
- Minglun comprehensive building (01/0003): six photo-based floors, 103 compartments. Keep the unnumbered fifth-floor large room, printed fifth-floor 315 and apparent sixth-floor 509 uncertain and gray. Never merge 605-2/605-3 into 605. Keep the three unlocated school catalog entries separate.
- Minglun building 10 (01/0013): three floors, 68 numbered rooms and 13 unnumbered spaces. Preserve second-floor 200 and third-floor 310. Do not assign numbers or timetable evidence to blank bays.
- Existing four Jinming plans retain positions. Other catalog scopes use a clearly named directory view without invented room positions. No design demonstration states, example dates or credentials in release assets.
- PNG output and public query-to-image behavior are the verification seams. Tests cover exact evidence, gray failure modes, independent daily slots and photo identity exceptions. Inspect produced PNGs visually.

Source photos (provided 2026-10-28; photograph date unknown):
- Comprehensive SHA256: 0ca92f9c4a5f750336db04aff41ca529a547dda0d391cec73ac85a917e802cfb
- Building 10 SHA256: f223753b616fedc427f953d0d0675efb85617aa85e534812e738c19e48c83502

Production uses a narrow source allowlist, verified source and online SQLite backups, atomic replacement and a runtime-only restart. Preserve unrelated live overlays. Rollback restores only source/marker, never user data. Installed-code tests, fresh read-only school queries and management API initialization are required before recording success.

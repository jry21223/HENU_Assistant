# Library reservation flow audit — 2026-10-09

Status: local implementation complete; independent review and packaging verification in progress. Test boundaries confirmed by the user. No school reservation, cancellation, sign-in, production configuration change, restart, or deployment was performed.

## Evidence

The source worktree starts at `b3fcd88aaffa5f26c1f87641dd1fe9945410c611`, the fetched `origin/langbot-plugin` head, independently checked against the GitHub ref API. The original LangBot checkout remains dirty and 38 commits behind; it was not fast-forwarded or overwritten. The MCP and Agent checkouts also contain unrelated changes.

The running plugin differs from this Git head. Its confirmation tool uses per-user locks and durable `henu.confirmation-receipt.v1` consumption before execution. These protections must be retained when reconciling source. The deployed `components/cli_tools/henu_cli.py` SHA-256 is `2bc1e13a0314b33020782551526b3165f8a649587e7ec602182e4accec192d20`.

A read-only SQLite inspection found the exact requested command, with no account identifiers or credentials included in this report:

```text
library reserve --location '三楼北附楼走廊' --seat-no 001 --date 2026-10-10 --time 09:00 --end-policy server_full_slot
schema=henu.confirmation-receipt.v1
status=failed
```

The user's transcript records the school rejection at 00:29:32: `座位开放预约时间为2026-10-09 06:30:00~23:00:00`. Therefore the later explanation that the operation was never confirmed is false. The receipt has no school error text, which prevents deterministic reconstruction of that reason in a later turn.

## Live school parameter probes

Only map and seat-query endpoints were called. The existing session associated with this exact failed receipt was used in memory, without login, credential disclosure, storage writes, or confirmation submissions.

At approximately 00:44 Asia/Shanghai, areas 15, 16, and 17 returned `type=1`, `reserveType=1`, and dates 2026-10-09 and 2026-10-10. For 2026-10-10, each returned one active segment, 08:00–23:00, with row `book_start=06:30`, `book_end=23:00`, `min_time=30`, `max_time=1440`, and `book_new=00:01`. The top-level `date.book_start` was 00:44 and must not be confused with the row's booking window. Do not infer undocumented `book_new` semantics.

| Area | School name | Tomorrow's queried free seats |
| --- | --- | --- |
| 15 | 三层南附楼走廊 | map configuration checked |
| 16 | 三楼北附楼走廊 | 96/96, 08:00–23:00 segment |
| 17 | 三楼大厅走廊 | map configuration checked |

The deployed planner generated the following read-only plans for area 16:

| Requested date and time | Actual generated plan |
| --- | --- |
| 2026-10-10, 08:00, no end | 2026-10-10, 08:00–23:00; segment 1772124 |
| 2026-10-10, 09:00, no end | Identical full segment; no custom start in submission payload |
| 2026-10-10, 14:00, no end | Identical full segment; no custom start in submission payload |
| 2026-10-10, 09:00–12:00 | Rejected: no matching segment; available 08:00–23:00 |
| 2026-10-11, 09:00, no end | Incorrectly replaced with 2026-10-09, segment 1772123 |

For segment booking the eventual write uses `/v4/space/confirm` with `segment` and `day`; `start_time` and `end_time` are empty. No such write was called during this audit. The configuration cannot substantiate the bot's claim that the booking would begin at 09:00. Booking availability must be reported separately from seat availability.

## CLI findings

The Git-head parser silently ignores unknown options, including `--end-policy`, while the live parser includes end-policy handling. `library reserve --area-id 16` does not fill location at the Git head, although `library seats --area-id 16` works. Omitting reserve `--time` inserts 08:00, contradicting the explicit-parameter check. Invalid time text reaches later planning without strict validation. Corrective work must reconcile the live contract rather than regress to the old parser.

The current confirmation shortcut only accepts pending-operation records. Consumed receipts do not retain conversation context or the result, so another confirmation gets the generic expired/already-executed message. The language model does not receive a deterministic status lookup and can reuse stale chat context.

## Proposed repair and public test boundaries

Public boundaries: `HenuCliSafe.call`, `ConfirmationShortcut.handle`, and the campus bot's seat-query/reservation methods, replacing only the external school transport with fixtures. Real writes are excluded from automated tests.

1. Require the exact date; never substitute another date or study period. Validate time and unsupported flags before requesting confirmation.
2. Return school rules, actual fixed segments, and booking-window information from read-only planning. Distinguish requested preferences from effective times. For fixed segments, show 08:00–23:00; do not promise a 09:00 start.
3. Preflight before confirmation: date, area, seat, valid time selection, and booking window. Invalid or currently blocked plans return one concrete explanation and next step.
4. Reuse an unchanged pending preview; changed effective parameters require another confirmation. Preserve the live per-user lock and durable single-consumption safeguards.
5. Retain a bounded result and conversation context in receipts. Add a read-only operation status command; repeated confirmation explains the saved result without submitting again.
6. Make help, tool prompts, and result hints agree. An accepted displayed recommendation is meaningful; ask once for genuinely missing choices. Keep campus selection constrained to the requested campus.
7. Apply shared core corrections to all three delivery trees, with variant-specific confirmation handling confined to LangBot. Reconcile deployed drift without overwriting other in-progress changes.

Baseline checks at the unmodified Git head: 23 existing CLI, confirmation, shortcut, and cache tests passed, with four third-party Pydantic deprecation warnings. These are baseline checks, not verification of a fix. None covers the full failing library flow.


## Implemented locally

- Exact dates and study periods; strict time/CLI flags; reserve has no implicit 08:00.
- Fresh `library plan` preflight, actual fixed slot and school booking-window fields; closed windows return before confirmation.
- Unchanged pending requests reuse their token. Per-user locks and durable executing/failed/committed/uncertain receipts prevent repeated submissions; `library operation-status` and repeated plain confirmations explain stored outcomes.
- A verified successful library response produces ✅; failed and uncertain responses do not.
- Shared core edits and core regression tests are mirrored into isolated MCP and Agent Skill worktrees. Existing original checkout WIP was not overwritten.
- The previously deployed retry-deadline and identity-consistency protections were reconciled into source while changing the same reservation boundary.

The live school writes remain simulated in regression tests. Local verification does not mean the production Bot has been updated.

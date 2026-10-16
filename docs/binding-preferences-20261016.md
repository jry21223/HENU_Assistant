# Account binding and minimal library preferences — 2026-10-16

User-approved scope: implement binding subject validation first, then library
location/seat preferences, on fresh isolated worktrees from all three latest
delivery branches. Test and independently review, open one PR per delivery,
merge only after CI passes. Preserve existing checkouts/backups. Do not deploy.

## Acceptance

- Saving credentials with verification must use a fresh authentication session,
  never reuse stored cookies as proof of the supplied password. Require an
  authenticated school context whose login_id matches the supplied student_id.
  Unknown, expired, mismatched, rejected or interrupted verification must fail
  without replacing the current binding or auth artifacts.
- Persist new credentials and session only after subject validation. If saving
  fails, restore the prior profile/session/cache files. Reset account-scoped
  auth artifacts on credential save; when changing student, invalidate old
  schedule/tasks and account-specific library defaults. LangBot must persist
  resets through its existing Storage API and invalidate that user's caches.
- verify_login=false saves an explicitly unverified binding without contacting
  the school or calibrating period times; never report it as verified.
- preferences_get/set/clear expose only library_location and library_seat_no.
  No credentials are required or returned. Reuse the existing profile and
  LangBot per-user storage. Preserve credentials and unrelated profile fields.
- set updates only explicitly supplied, nonempty values. When location changes,
  discard the old seat unless a new seat is explicitly supplied in the same call.
  clear requires an explicit nonempty whitelist of fields and also removes
  legacy location/seat_no aliases. Empty/unknown fields and malformed, duplicate
  or extra CLI arguments must fail before mutation.
- MCP exposes snake_case tools. Agent Skill uses preferences_get,
  preferences_set and preferences_clear snake_case commands/options. LangBot
  uses preferences get/set/clear with --library-location / --library-seat-no.
- Preferences writes are local configuration, never campus reservations.
  Changing them invalidates per-user library query caches immediately; A/B QQ
  users remain isolated and missing identity/group restrictions remain intact.
- Saved preferences do not authorize booking. Preserve current explicit date,
  start/end strategy, preview and later-user confirmation requirements.
- Keep upstream Hardened service, Storage transactions, QQ cards and redaction.
  Query-time, end-policy, campus/building/term preferences, CAS parser changes,
  login cooldown/ingress/background-task rewrites and deployment are excluded.

## Agreed public test seams

User accepted the preceding assessment and its public-entry regressions.
Test save_account/setup_account/show_account with a school HTTP adapter and
temporary filesystem; MCP tool registration and preferences APIs; Agent CLI
main; LangBot HenuCliSafe.call through Hardened service and in-memory Storage,
including persisted round trips, isolation, cache invalidation and pending
booking constraints. Mock school HTTP/filesystem failures, not internal logic.
No real school requests or credentials. Red/green one slice at a time, then each
variant's full suite. Fixed review base is the delivery SHA used at creation.

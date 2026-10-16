# Authentication and reservation WIP rescue — 2026-10-16

User-authorized scope: preserve the original checkouts; back up the three
mcp2-2.1.0 checkouts; port only authentication failure protection and reservation
parameter constraints onto the latest shipping branch. Do not deploy.

## Acceptance

- HenuXkClient.login preserves valid sessions and IDS success. It must not try
  Kingo after rejected credentials, a challenge, lockout, rate limiting, an
  unknown response, or a credential submission whose result is unknown.
- Kingo fallback remains available only when IDS is unavailable before sending
  credentials. HTTP 5xx after a credential submission is an unknown outcome.
- library_reserve requires an explicit ISO date and valid 24-hour start time.
  It also requires either a later valid end time or end_policy=server_full_slot.
  end_policy=ask without an end time must ask for one. Query defaults never
  authorize a reservation. Reject invalid requests before campus HTTP calls.
- Agent Skill keeps its snake_case parameter dialect. LangBot keeps its space
  CLI dialect and its preview, later-user-confirmation, identity, expiry and
  fingerprint checks. Revalidate old pending reservations before execution.
- Preserve upstream Hardened service, QQ cards, token redaction, and storage.
  Preferences, CAS parser changes, new credential interception, and deployment
  are outside this slice.

## Test seams

Public login via HenuXkClient.login with an offline HTTP adapter; public
library_reserve and Agent Skill CLI; LangBot inspect_cli_command and HenuCli.call
with in-memory plugin Storage and an offline service adapter. No live school
requests or real credentials. Run each variant's complete tests at the end.

## Source and review

Source WIP remains in the original checkout and rescue branches. Port individual
behaviors rather than replacing old files. Review against the acceptance above
and workspace AGENTS.md/CLAUDE.md; commit the finished slice locally.

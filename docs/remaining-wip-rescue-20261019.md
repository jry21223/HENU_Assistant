# Remaining WIP rescue — 2026-10-19

## Agreed constraints

Port valuable behavior from rescue/*-wip-20261014 onto the freshly fetched shipping branch. Preserve all pre-existing checkouts and backups. No deployment, live school login, or live reservation. No new campus/term/time/end-policy preferences; preferences remain library location and seat only. Preserve existing auth fail-closed handling, binding-subject validation, explicit reservation authorization, dialects, identity isolation and Storage transactions.

## Confirmed test seams

Use public MCP tools/CLI and LangBot registered event handlers plus the next-request Storage readback. Fake external school HTTP, SDK and Storage at those boundaries; do not test private helper shapes. Each behavior gets a red-green slice; run the variant suite once at the end, compare typecheck diagnostics with the pinned base, and run independent Standards and Spec reviews.

## Acceptance

- Mirror the MCP classroom-location fixes.
- setup_account reads a password with getpass when interactive, or one line of standard input with --password-stdin. Keep --password compatibility with a non-secret warning.
- Reject conflicting input modes, empty/closed input and unattended input before calling the account service. Preserve password spaces and punctuation; remove only the input line ending.
- Passwords never appear in JSON errors, stderr warnings or help examples. Do not prompt/fall back to echoed input when no terminal exists.

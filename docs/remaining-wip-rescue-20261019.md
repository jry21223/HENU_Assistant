# Remaining WIP rescue — 2026-10-19

## Agreed constraints

Port valuable behavior from rescue/*-wip-20261014 onto the freshly fetched shipping branch. Preserve all pre-existing checkouts and backups. No deployment, live school login, or live reservation. No new campus/term/time/end-policy preferences; preferences remain library location and seat only. Preserve existing auth fail-closed handling, binding-subject validation, explicit reservation authorization, dialects, identity isolation and Storage transactions.

## Confirmed test seams

Use public MCP tools/CLI and LangBot registered event handlers plus the next-request Storage readback. Fake external school HTTP, SDK and Storage at those boundaries; do not test private helper shapes. Each behavior gets a red-green slice; run the variant suite once at the end, compare typecheck diagnostics with the pinned base, and run independent Standards and Spec reviews.

## Acceptance

- Resolve a building name within an explicitly chosen campus even when the campus was passed as a code.
- Reject a building without a campus and a building code absent from the chosen campus; do not silently broaden the query.
- Never fall back to saved campus/term/building defaults. School metadata failures return a generic error without exception secrets. Preserve metadata/cache behavior for queries without a building filter.
- Mirror this shared public behavior to Agent Skill and LangBot, adapting their existing code rather than replacing it.

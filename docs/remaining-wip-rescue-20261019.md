# Remaining WIP rescue — 2026-10-19

## Agreed constraints

Port valuable behavior from rescue/*-wip-20261014 onto the freshly fetched shipping branch. Preserve all pre-existing checkouts and backups. No deployment, live school login, or live reservation. No new campus/term/time/end-policy preferences; preferences remain library location and seat only. Preserve existing auth fail-closed handling, binding-subject validation, explicit reservation authorization, dialects, identity isolation and Storage transactions.

## Confirmed test seams

Use public MCP tools/CLI and LangBot registered event handlers plus the next-request Storage readback. Fake external school HTTP, SDK and Storage at those boundaries; do not test private helper shapes. Each behavior gets a red-green slice; run the variant suite once at the end, compare typecheck diagnostics with the pinned base, and run independent Standards and Spec reviews.

## Acceptance

- Mirror classroom fixes, respecting current provider routing and cache contracts.
- Intercept explicit account set commands at registered message handlers before LLM enrichment, using the current Hardened service. Require a trusted sender and private chat; malformed/group requests cannot call school HTTP. Do not send passwords to prompts, traces or replies.
- Keep command dialect, current QQ cards and other event listeners. Deduplicate delivery and serialize per-user background account jobs without persisting credential text. Background replies must respect actual SDK lifecycle.
- Persist bounded cross-request per-user and per-student auth cooldown after high-risk failed attempts. Network failure before credential submission does not consume this budget. Preserve an existing binding on failures, reset the appropriate guard on a verified success, report retry timing without credentials.
- Keep fresh binding-subject validation and persistence rollback. Login state records are local attempts, not school-verified success.

## Implementation notes

The account gate wraps every registered primary/normal handler of the existing SafeIdentityCaptureListener because the SDK invokes all matching handlers even after prevention flags. The coordinator stops credential events synchronously, captures the bot route while the query is alive, acknowledges before starting school work, and sends at most one final message via the query-independent plugin API. There are at most 32 live tasks per coordinator; completed query records are bounded.

Login budgets reserve an attempt in plugin Storage before school requests, serialize account attempts within the plugin process, and retain uncertain results. The user and student indexes are HMACs under stable HENU_MASTER_KEY; private last-attempt records are materialized on subsequent account status requests. Three risky outcomes block for the remainder of a 24-hour window. Known network failure before submitting credentials refunds the reservation. Success resets only matching indexes; unverified saves do not clear budgets. This is a single-plugin-process coordinator, not a distributed atomic limiter across independent runtime processes.

The Kingo shared core now labels lost POST responses, malformed responses and lost post-login redirects auth_state_unknown. Initial session-check network failures are network_error before any credential POST. These classifications are mirrored to MCP and Agent Skill.

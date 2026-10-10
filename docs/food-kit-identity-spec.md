# QQ Food identity integration

User requirements: no person/model enters an account UUID; automatically prefer the current QQ user's bound Kit account; when unbound, prompt binding without blocking food submission; complete and deploy both plugin/LangBot and Kit.

## Current scope

The existing enabled Food MCP creates public posts. This task preserves that existing behavior and fixes identity only. It explicitly allows unbound submissions in this existing channel, superseding the bound-only restriction for this channel in the older #523 plan. It does not implement or claim completion of #523's separate pending-review queue. Console review authority and website behavior remain unchanged. Record the matching narrow amendment in Kit ADR-0047 before release.

## Interfaces and acceptance

- A plugin-owned `henu_food` Tool exposes create, mine, list, detail and venues using content/resource inputs only. It replaces the shared independent Food connector in the QQ pipeline; other MCP servers remain available.
- Caller identity comes from the current SDK session/query and Bot UUID. Missing/conflicting sender identity fails closed. Never accept actor identity, credentials, metadata or Bot IDs from tool arguments.
- Resolve the current Kit binding on every account-owned call using the existing signed Core client, and use only a valid canonical UID/name from that response. Do not inject UID into prompts/history or cache a durable binding.
- Unbound or temporarily unavailable Kit uses an opaque stable Bot+sender guest scope and an actionable non-blocking reminder. Different QQ users never share guest identity, cap or mine history, even across reconnects.
- The plugin signs per-call MCP `_meta` using a dedicated context secret. Proof covers tool name, normalized argument digest, scope, identity kind, optional account identity, timestamp and nonce. No caller-controlled role/review authority exists.
- Kit validates proof before deriving actor. Signed guests are derived by Kit from the stable opaque scope. Malformed/forged/expired/mismatched proofs fail closed. Proof nonce binds Food's persistent idempotency key, so replay cannot duplicate a post.
- Production account-owned calls require authenticated per-user context; public reads remain available. Existing Food owner signing, validation, cap and independent credential rings remain in force.
- Prove behavior through plugin Tool + fake HTTP Core/MCP boundaries, and real SDK calls to Kit MCP + fake Food owner. Include two users on one MCP session, one user across reconnects, unbinding/lookup failure, malicious model actor arguments, proof tampering and replay. Package/live-snapshot tests preserve all existing CLI and binding functions.

## Deployment

Review and merge plugin changes and Kit changes with current-SHA CI evidence. Build fixed images/packages outside production; preserve exact running LangBot/plugin overlays and Kit's existing WSL deferral. Back up config/data/source, deploy Kit proof validation before enabling plugin Food Tool, atomically replace only affected production units/configuration, and retain immediate rollback. Use no fabricated public post or unsolicited QQ message for smoke verification. Verify live schema, unauthenticated rejection, proof/guest mine behavior, plugin initialization and unchanged surrounding services; report any real-user acceptance still pending.

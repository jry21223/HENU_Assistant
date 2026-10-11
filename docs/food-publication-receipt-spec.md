# Human-readable Food publication receipt

## Problem Statement

Real QQ publication succeeds and stores a picture, but the bot displays the complete MCP text envelope as JSON, including author, request ID, internal image routes and storage fields.

## Solution

Show a plain-language published summary with venue, campus, tier, review, optional price/hours/dishes, confirmed picture count and the public post link. Repeated confirmation reuses this receipt without another publication.

## User Stories

1. As a contributor, I want to understand whether publication succeeded.
2. As a contributor, I want a clear summary and the count of pictures actually returned by Food.
3. As a contributor, I want a link to the published post instead of an internal route or JSON envelope.
4. As a contributor, I want binding guidance once if I used guest identity.
5. As a contributor, I want retries and unparsable success replies to avoid duplicate posts.
6. As an operator, I want prior successful cached JSON receipts rendered readably without reposting or database restoration.

## Implementation Decisions

- Format only successful publication receipts at the native plugin boundary; preserve the confirmation/claim, account resolution and external API contract.
- Use the confirmed draft for content labels and the returned post record for image count and validated public post ID. Generate links only on the known Portal `/food/post/<UUID>` route, verified from current source.
- Ignore the transport envelope's author, request ID, storage flags, raw image paths and arbitrary returned URLs.
- If the success response is incomplete/unparsable, return safe guidance to check My Posts and keep the publication claimed; never automatically resend.
- Persist the formatted receipt before sending it. Upgrade older cached success JSON receipts read-only with respect to publication, retaining any existing guest binding guidance once.

## Testing Decisions

Reuse the previously user-confirmed SDK events -> native Tool/private draft store -> real loopback MCP seam. The fixture must return the real post envelope shape, not a placeholder success sentence. Cover readable successful receipt, one guest notice, repeated confirmation equality, malformed-success no-repost, and legacy cached success. Run existing suite and production-SDK fixtures without fabricated public posts.

## Out of Scope

Editing/reposting existing public posts, changing Kit APIs, rendering standalone API query results, or generating new pictures.

## Further Notes

The screenshot provides real user acceptance of confirmation and image forwarding. Continue the authorized reviewed merged-source SSH overlay; replace only affected Food files, back up private databases online and preserve unrelated overlays, images, credentials and pipeline settings.

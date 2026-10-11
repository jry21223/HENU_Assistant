# Food confirmation after picture receipt questions

## Problem Statement

A contributor receives a complete unpublished draft with one picture, asks whether the picture arrived, then sends `确认发布` twice. Both attempts are rejected as new content not yet included in the preview, even though the inquiry changed nothing.

## Solution

Reply to picture receipt/visibility inquiries from the actual scoped draft without changing its content or confirmation eligibility. Confirmation attempts are controls, not content edits. Actual supplements still require a delivered updated preview before publication.

## User Stories

1. As a contributor, I want to ask whether my picture arrived and still confirm the draft.
2. As a contributor, I want the reported picture count to come from actual stored attachments.
3. As a contributor, I want to retry a rejected early confirmation after the preview is delivered.
4. As a contributor, I want real price/review/dish/image changes included before publication.
5. As a contributor, I want confirmation retries to publish at most once.
6. As an operator, I want the deployed draft data, account credentials and unrelated functions preserved.

## Implementation Decisions

- Track the pending content message separately from the latest ingress Source. The current ingress still protects tool calls against stale or replayed requests.
- Recognize bounded, anchored picture receipt/visibility questions. Only inquiries without attachments are read-only; additions, malformed pictures and unrecognized content remain pending.
- Reply directly from the private scoped draft; do not trust model-declared image counts or model classification of writes.
- A successful matching preview delivery clears the pending content marker. Confirmation requires no pending content, a successfully delivered latest preview and a later fresh Source.
- Bare confirmation attempts never create a pending content update, including after a failed/early attempt. Existing durable publication claims and duplicate prevention remain intact.
- Pre-fix drafts missing the new marker fail closed until an explicit fresh preview is generated. No database restoration or destructive migration is allowed.

## Testing Decisions

- Use the already user-confirmed SDK-event to native Food Tool/persistent draft store to loopback MCP HTTP seam. Exercise the reported draft -> receipt inquiry -> confirmation -> repeated confirmation sequence.
- Assert observable replies and outgoing price/image data, including early-confirmation recovery, pending true supplements, inquiry wording variants and failed preview delivery.
- Run the full suite, type/format/package checks, code-review Standards and Spec axes, and isolated fixtures on the actual production SDK without public posts.

## Out of Scope

Other chat intent classification, changes to Kit APIs/UUID identity, editing/deleting/reposting existing public posts, or sending unsolicited QQ test messages.

## Further Notes

Continue the existing authorized SSH release workflow from merged source. Back up the live private draft database online, replace only affected Food files, reload the runtime and verify the unchanged pipeline through the designated management MCP.

# Food draft, confirmation and chat images

## Problem Statement

The bot publishes as soon as four fields are supplied, before the user finishes prices or pictures. It can see a QQ picture but does not forward the real image bytes. Public posts cannot subsequently be edited.

## Solution

Prepare an unpublished draft, show its complete contents and image count, accept additions, and publish only after a later independent message containing exactly `确认发布`.

## User Stories

1. As a contributor, I want a draft preview before publication so I can check it.
2. As a contributor, I want to add prices, hours and dishes across messages.
3. As a contributor, I want actual chat pictures attached without copying image data.
4. As a contributor, I want successive pictures, up to six, counted in the preview.
5. As a contributor, I want explicit confirmation to publish the latest preview once.
6. As a contributor, I want a repeated confirmation to return the result without duplicating a post.
7. As a contributor, I want invalid or unavailable pictures to block publication until addressed.
8. As a contributor, I want expired drafts and missing message provenance to fail closed.
9. As a contributor, I want drafts isolated by bot, sender and conversation.
10. As a contributor, I want a fresh Kit binding lookup at publication and nonblocking guest fallback.
11. As an operator, I want the current runtime overlays and unrelated campus functions preserved.

## Implementation Decisions

- The native Food tool's `create` action becomes prepare/update only. Content fields merge with the active draft; no model-supplied publish flag, identity or image bytes are accepted.
- A Food event listener reads the real raw message Source, top-level Plain text and inline Image data before the model. It handles exact `确认发布` and image-only draft additions directly.
- A private, persistent SQLite draft store holds bounded inline images and atomic publication claims. It is scoped by authenticated bot/sender/conversation context. Raw media URLs and arbitrary local paths are never fetched.
- Only fresh Source provenance from a later message can confirm a ready preview. A later unprocessed content message, replay, concurrent duplicate, stale source or incomplete preview cannot publish. Drafts expire after ten minutes. Failed or uncertain publication stays claimed and is never retried automatically.
- Any content or image change generates a new preview. Unsupported images produce an explicit error and prevent publication; `取消美食草稿` discards the unpublished draft.
- Kit account identity is resolved afresh at publication. Guest submission remains available, with optional binding guidance. Existing Kit Food API, image sanitation and public visibility remain unchanged.
- This is LangBot-specific; no changes to the standalone MCP, Agent Skill or Kit service are required.

## Testing Decisions

- The user confirmed the seam: SDK inbound message events through query variables, native Food Tool and persistent draft storage to a real loopback MCP HTTP fixture.
- Verify observable preview and reply text and outgoing MCP requests; fake only external SDK transport/Core boundaries, use a real temporary draft database and real MCP client.
- Red/green slices cover no early publication, actual inline images, later confirmation, content updates, replay/concurrency, expiration, invalid images and scope isolation. Run the plugin suite and package build and repeat the workflow fixtures on production's exact SDK versions without public writes.

## Out of Scope

Editing, deleting or reposting the user's existing public posts; a moderation queue; downloading arbitrary media URLs; changing the QQ adapter's single-attachment limitation; unrelated production overlays.

## Further Notes

The user confirmed this flow and seam and already authorized SSH deployment. Deploy the reviewed merged plugin files with a fresh backup and runtime reload. Kit's earlier automatic identity deployment stays active. Real QQ acceptance remains distinct from isolated fixture verification.

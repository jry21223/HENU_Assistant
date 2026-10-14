# QQ native buttons

Continue existing QQ cards with native inline keyboards; do not create campus
features, change school write permissions, send test QQ messages or deploy.

## Selection and scope

- `markdown.content` + `keyboard.content.rows`, no deprecated Markdown template.
- Campus pending operations already in `WRITE_TOOL_NAMES`: type 1 confirm/cancel,
  same `group_id`, permission type 0 specifying the initiator. A callback is a
  choice for an exact stored pending operation, never a fresh reservation.
- Read-only next steps and scoped pagination: type 2, `enter` only in C2C.
- Rain classroom credential confirmation: existing pending credential flow.
- KIT binding confirmation keeps exact QQ quote/receipt verification; use type 2
  with `reply=true`, preserving its source checks. Cancellation only discards
  the local pending action, never unlinks an already bound account.
- Food publication keeps its complete-preview and genuine later-message checks;
  command buttons bind the exact draft revision and never publish via a synthetic
  callback.
- Scope every pending choice to bot, initiator, launcher type/id, exact pending
  token and expiry. Old/replaced, expired, other-person and other-session choices
  refuse execution. Persist a decision before side effects; duplicate confirmation
  and cancellation after confirmation cannot repeat or undo a submitted operation.
- Consume the campus pending confirmation before its external call, including
  ambiguous failures. Cancellation performs zero campus business calls.
- Buttons remain disabled by default and in production. Gate on existing
  `qq_cards_buttons` config and installed native SDK support. Lack of verified
  capability does not block implementation/preview, and is not approval to enable.
- Preserve readable text controls. Definite QQ rejection may fall back once;
  timeout/5xx/unknown acceptance must not be retried. Preserve C2C send receipts.

## Sources, verified 2026-10-14

- [C2C send](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_users_user_openid_messages.post.html), updated 2026-09-03; matches the supplied complete document.
- [Keyboard](https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/trans/msg-btn.html): custom keyboard still describes invitation access. Enterprise identity alone is not evidence of this permission.
- [Interaction](https://bot.q.qq.com/wiki/develop/api-v2/autogen/event/interaction_create.html): type 11/12 acknowledgement, INTERACTION intent (1<<26).
- [Acknowledgement](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/interactions_interaction_id.put.html): `d.id` for PUT, top-level event id for passive message replies. Acknowledge each interaction once.

## Agreed verification seams

QQ keyboard wire JSON and receipt preservation; real installed SDK first-listener
dispatch; plugin storage + existing CLI confirmation boundary; callback enqueue and
acknowledgement with QQ HTTP and external campus calls mocked. Test scoped expiry,
replacement, repeat/cancel races and write-ahead storage failures. Use synthetic
previews only; no Chromium, no real QQ sends/reservations or credential reads.

## Current capability evidence

Read-only production check: native card support installed, `qq_cards_buttons=false`,
dispatch release `henu-qq-card-dispatch-20261013-f9a2653`. The current host consumes
INTERACTION_CREATE for Dify forms only; HENU callbacks require a distinct route.
No verified platform keyboard approval was found. Personal Context lookup is
unavailable. Keep capability unverified and leave the gate closed.

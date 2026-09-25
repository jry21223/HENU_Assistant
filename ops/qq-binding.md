# HENU KIT account binding (#522)

Requires the Platform Core/Gateway/Portal binding release from
`jry21223/HENU-Kit-DEV#522`. This plugin does not establish platform accounts or
reuse school/雨课堂 passwords.

Private commands: `绑定 HENU KIT`, `HENU KIT 状态`, `解绑 HENU KIT`.
Normal confirmations use `确认`. If another operation is pending, select
`确认HENU KIT绑定` or `确认HENU KIT解绑` explicitly. All commands run before the
model and require a trusted QQ sender and the configured Bot UUID. Request
deduplication uses the QQ Source message ID, not the process-local query counter.

The plugin `.env` (0600, never committed) needs six values:
`HENU_KIT_CORE_URL`, `HENU_KIT_PORTAL_URL` (HTTPS origins),
`HENU_KIT_CLIENT_ID`, `HENU_KIT_KEY_ID`, `HENU_KIT_SECRET`,
`HENU_KIT_BOT_UUID`. Provision the independent service credential in Core before
activating. The corresponding app/client mapping is owned by Core.

Apply `qq-binding-reply-route.patch` and `qq-binding-source-time.patch` to the
exact existing custom LangBot image. The second patch preserves the official
QQ C2C event timestamp in `Source.time`; without it a delayed replay appears
new. Copy `pkg/platform/sources/qqofficial.py` from the running image, require
both patches to apply with zero fuzz, and run
`python3 ops/verify-qq-source-time.py <patched-qqofficial.py>` before building
the derived image. The verifier also checks that C2C and channel DIRECT
messages carry distinct sender nicknames. Separately verify that the exact
production plugin runtime SDK retains those nicknames in `message_event`
through its serialization boundary; the plugin must reject DIRECT for every
KIT command. Preserve all existing sensitive-message routing and QQ
sequence fixes. Without the notification route, the fallback is safe but
requires an extra `确认`: first to show the account, then to bind it.

For the reviewed `local/langbot:v4.10.6-plain-confirm-20260923.1` image,
the extracted QQ adapter SHA-256 is
`246bbf51733520074564dba927937e8f7c2600ce13c7d018559f412fb5ca379c`;
after the timestamp patch it is
`10a73fd1addde60294d27e280d3b02bd8a51a6142f1fd1283b980e5be84da0a7`.
Check both values. If the running image or source bytes differ, stop and review
the new base and patch again; a zero-fuzz application alone is insufficient.

Select the verified HENU-Bot QQ official WebSocket instance for
`HENU_KIT_BOT_UUID`. The observed webhook path lacks a verified inbound
signature gate; do not assign the binding credential to that instance.

Do not replace the entire production plugin with this published branch: the
live plugin contains additional account and snapshot-storage hotfixes. Overlay
only the reviewed new modules/listener hooks onto the actual live snapshot,
test both old and new flows, then deploy through a separate release gate.

Binding tests passing locally do not prove real QQ delivery. Verify the link,
website consent, original-QQ preview and confirmation, status, and unlink with
consenting test accounts before enabling Food submission. Food's existing
`create_food_post` is **not** the pending-review channel and must not be called
as a shortcut around #523.

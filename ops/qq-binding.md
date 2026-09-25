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

Apply `qq-binding-reply-route.patch` to the existing custom LangBot image for
the delayed account preview. Preserve its other sensitive-message routing and
QQ sequence fixes. Without the notification route, the fallback is safe but
requires an extra `确认`: first to show the account, then to bind it.

Do not replace the entire production plugin with this published branch: the
live plugin contains additional account and snapshot-storage hotfixes. Overlay
only the reviewed new modules/listener hooks onto the actual live snapshot,
test both old and new flows, then deploy through a separate release gate.

Binding tests passing locally do not prove real QQ delivery. Verify the link,
website consent, original-QQ preview and confirmation, status, and unlink with
consenting test accounts before enabling Food submission. Food's existing
`create_food_post` is **not** the pending-review channel and must not be called
as a shortcut around #523.

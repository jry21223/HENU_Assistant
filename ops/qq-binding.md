# HENU KIT account binding (#522)

Requires the Platform Core/Gateway/Portal binding release from
`jry21223/HENU-Kit-DEV#522`. This plugin does not establish platform accounts or
reuse school/雨课堂 passwords.

Private commands: `绑定 HENU KIT`, `HENU KIT 状态`, `解绑 HENU KIT`.
Binding confirmation uses `确认` while quoting the Bot's target-account
preview message. A plain `确认` without that quote repeats the binding preview
and does not bind. Unlink uses the existing plain `确认` flow; when another
operation is pending, `确认HENU KIT解绑` selects unlink. Binding still requires
quoting the current account preview with exactly `确认`. All commands run before
the model and require the verified QQ WebSocket C2C marker, a trusted sender,
and the configured Bot UUID. Request deduplication uses the QQ Source message
ID, not the process-local query counter.

The plugin `.env` (0600, never committed) needs six values:
`HENU_KIT_CORE_URL`, `HENU_KIT_PORTAL_URL`,
`HENU_KIT_CLIENT_ID`, `HENU_KIT_KEY_ID`, `HENU_KIT_SECRET`,
`HENU_KIT_BOT_UUID`. Provision the independent service credential in Core before
activating. The corresponding app/client mapping is owned by Core.
The production Core URL is `https://henukit.cn/account-auth`; the host proxy
removes `/account-auth` before forwarding, so the service signature covers
`/api/v1/qq-bindings/{action}`. Portal URL is the HTTPS origin
`https://henukit.cn`. The client allows that exact production Core proxy URL
or a pathless HTTPS origin; it rejects the proxy path on other hosts or ports.

Apply `qq-binding-reply-route.patch` to the exact existing custom LangBot
image, then `qq-binding-source-time.patch`, then
`qq-binding-receipt-route.patch` in a derived image from the exact running
LangBot main image. The source-time patch preserves the official
QQ C2C event timestamp in `Source.time`; without it a delayed replay appears
new. The receipt patch carries only a validated QQ C2C text-send `id`,
RFC3339 `timestamp`, and `ext_info.ref_idx` through the adapter and
`REPLY_MESSAGE` response. For a single Plain reply to an official C2C message,
it returns `{'qq_c2c_receipt': {'id': ..., 'timestamp': ...}}` plus `ref_idx`
when QQ provides a valid one. A missing `ref_idx` is usable for unlink but
cannot authorize binding; a malformed present `ref_idx` is rejected. The
running plugin SDK's `QueryBasedAPIProxy.reply()` returns
`ActionResponse.data` directly, so the plugin sees that exact dictionary.
The receipt patch also removes `REPLY_MESSAGE`'s full-message debug log: a
binding reply can contain a private link token. The verifier checks that a
representative private link body is absent from captured handler debug output.
QQ send failures log and raise only the HTTP status, because a provider error
body can echo the private link; the verifier exercises that failure path.

The exact base image exposes a public unified webhook router even when the QQ
Bot has `enable-webhook=false`; the receipt patch makes that adapter entry
return 403 unless `enable-webhook` is explicitly true. The independent Quart
callback is not started by the WebSocket mode. Only the authenticated QQ
WebSocket path adds `qq_websocket_verified: true` to an inbound C2C event;
callback/webhook parsing carries no marker. The adapter serializes only
`{t, d_id, qq_websocket_verified}` for an ordinary C2C command. A type-103
quote adds `qq_quote_present: true` even when the quote index is missing or
conflicting; a valid quote also adds `qq_quote_ref_idx`. The plugin must
require the WebSocket marker for every KIT command and confirmation, then
match the configured
WebSocket Bot UUID, C2C sender, and Source ID. For a C2C `message_type=103`
quote from that path, the patch reads
`message_scene.ext` (`ref_msg_idx=` or `msg_idx=`) and
`msg_elements[*].msg_idx`, accepts only one consistent printable index, and
serializes only that optional quote index in addition to the marker. The
`qq_quote_present` flag never authorizes a bind; it prevents a malformed
quoted `确认` from falling through to another plain-confirmation handler.
Conflicting, duplicate, oversized, malformed, and non-C2C quote metadata is
discarded. The original `Source` + `Plain` message chain stays unchanged, so
existing confirmation
handlers still see the original text. The adapter's `QQFriendMessage` subclass
injects the three-, four-, or five-field marker for the first plugin hop.
Successive
plugin hops deserialize to the SDK's ordinary `FriendMessage`, whose original
`model_dump()` drops `source_platform_object`. Therefore apply
`qq-binding-sdk-quote.patch` to the **plugin runtime image only**, at
`/app/.venv/lib/python3.12/site-packages/langbot_plugin/api/entities/builtin/platform/events.py`.
That SDK patch retains only the exact C2C marker forms and changes no
group or other source serialization. Derive a reviewed runtime image from the
exact running one; do not edit the running container or replace its dependency
environment. The main image SDK need not be patched.

Extract `libs/qq_official_api/api.py`, `pkg/platform/sources/qqofficial.py`,
and `pkg/plugin/handler.py` from the same running image. Require each patch to
apply with zero fuzz, in the order above, and run
`python3 ops/verify-qq-source-time.py <patched-qqofficial.py>` and
`python3 ops/verify-qq-receipt-route.py <patched-api.py> <patched-qqofficial.py> <patched-handler.py>`
before building the derived image. The verifiers use no QQ account or network.
Extract the unpatched main-image SDK `site-packages/langbot_plugin` tree and
verify its `events.py` SHA-256 matches the exact base value below. Run
`python3 ops/verify-qq-sdk-quote.py --main-first-hop <main-sdk-root> <patched-qqofficial.py>`
to prove the adapter's first serialization hop against the actual main-image
SDK.
Extract the plugin runtime SDK from its exact running image, verify its base
SHA-256 below, apply `qq-binding-sdk-quote.patch` with zero fuzz to a copy of
its `site-packages` root, and run
`python3 ops/verify-qq-sdk-quote.py <patched-sdk-root> <patched-qqofficial.py>`
with a Python environment that has the SDK dependencies. The SDK verifier
proves three plugin serialization hops retain only the marker, including
type-103 quotes without usable indices. The original unpatched SDK fails at
the second hop. Check its green result before
building the derived plugin runtime image.
The source-time verifier also checks that C2C and channel DIRECT messages carry
distinct sender nicknames. Separately verify that the exact production plugin
runtime SDK retains those nicknames in `message_event` through its
serialization boundary; the plugin must reject DIRECT for every KIT command.
Preserve all existing sensitive-message routing and QQ sequence fixes.

A consenting one-shot QQ C2C probe on 2026-09-25 observed HTTP 200 with a
nonempty `id`, RFC3339 `timestamp`, and `ext_info.ref_idx` of length 135.
That establishes the outbound response shape only. The actual inbound quote
shape has not yet been observed for this QQ application; local fixtures and
the upstream QQ SDK do not prove that a user's quoted `确认` will contain the
same index. Before enabling binding, test a consenting user's quote of a
harmless Bot prompt through the controlled candidate and record only field
presence, consistency, and equality booleans. Never record raw IDs, indices,
tokens, or private content. If the index is absent or not preserved through
`ctx.event`, stop. A plain `确认` must only repeat the target-account preview.

For the reviewed `local/langbot:v4.10.6-plain-confirm-20260923.1` image
(`sha256:96c3203eeaa23c4f104ea2cb2ae6230533bc1ed1051e621fcf4021693f6b80aa`),
the extracted QQ adapter SHA-256 is
`246bbf51733520074564dba927937e8f7c2600ce13c7d018559f412fb5ca379c`;
after the timestamp patch it is
`10a73fd1addde60294d27e280d3b02bd8a51a6142f1fd1283b980e5be84da0a7`.
The extracted API and handler SHA-256 values are respectively
`926312999490ef0c01b836f633efd8b102b9f42d5ade5d0f0b78118c5bda6353`
and `671cbfa32caf16fdb6c7df6d63aef8067cc461493a3d3f3d8f657b90d91e218e`.
After the receipt patch, API, adapter, and handler SHA-256 values are
`023333192a18abcd5d43063f524c79dfa905274f18e2ff148339974f07dfd633`,
`b6e8334353a1a5514e6e3287c036b52f9b9c2a8eb9e28a0f6f8b8a011abaa1a7`,
and `f9739313793234bd097836fb489ca8ab8473730fef640534ec9ab4ebd2b68c87`.
The plugin runtime base is
`local/langbot-plugin-runtime:v4.10.6-henu-deps-20260920.1` with image ID
`sha256:ea0113b63d3a3acdad9e44565db7f938bc03442f09bfe070fdbcb73335992b69`.
Its SDK `events.py` SHA-256 is
`b14f16fb3752a980791f203af5f0705290701a977723794c7677b8fc2bcf9e7e`;
after the SDK patch it is
`0b51929af8972d1bc816369162732f6600bb29b29b38a77e2840fd9f9a06d31e`.
Check all values. If either running image or source bytes differ, stop and review
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

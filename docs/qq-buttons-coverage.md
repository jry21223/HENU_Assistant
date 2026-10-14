# Native button coverage

Implementation extends the [existing card matrix](qq-cards-coverage.md). Buttons
are optional and disabled by default; all 47 public service routes still use their
existing cards. Production additionally has the existing preferences route (48),
which must be preserved during a future production overlay.

| Existing flow | Native controls | Choice validation / remaining boundary |
| --- | --- | --- |
| Schedule now/day/week/full, import result | Current course / this week, scoped previous/next page | Read-only; no push scheduler added |
| Library queries/current/records | Current reservation / reservation help, scoped pagination | No invented date/seat selection or implicit booking |
| Library reserve/signin/cancel previews | Type 1 confirm/cancel | Exact existing `pending_operation`; no additional write tools |
| Seminar queries/groups/records | My reservations / reservation help, pagination | Existing command routes only |
| Seminar reserve/signin/cancel previews | Type 1 confirm/cancel | Exact owned pending action |
| Rain settings enable/domain/lesson/exam/list/start-time previews | Type 1 confirm/cancel | Existing `WRITE_TOOL_NAMES` confirmation set; disabling remains existing immediate stop |
| Rain account/token binding preview | Type 1 confirm/cancel | Encrypted existing pending credentials, private only, existing login coordinator |
| Rain status | Refresh / settings / courses if logged in, otherwise login help | Disabled is prominent; enabled configuration differs from verified running; no answer/submit control added |
| KIT account preview / unlink preview | Type 2 confirm/cancel, quote enabled | Current pending token, exact verified QQ source + receipt quote; no synthetic callback binds KIT |
| Food unpublished preview | Type 2 confirm/cancel on last page | Exact nonce + revision; all preview pages delivered; new genuine message required |
| Account/service tool status | Current course / current reservation | Private only |
| Empty classroom/resources/leave/signin/checksleep/activity/collection and other query/help cards | Scoped paging or help | No campus mutations synthesized from query data |
| Group query cards | Owner-only command controls; `enter=false` | Existing public query whitelist; all personal writes remain private |
| Missing identity/private notice/expired/error | Readable existing card/text | No automatic confirmation or retries |
| Already submitted / unknown external outcome | Status receipt text; old choice inert | Decision durable before side effects; do not submit again |

The pending campus set is exactly: library_reserve, library_auto_signin,
library_cancel, seminar_reserve, seminar_signin, seminar_cancel,
yuketang_set_enabled, yuketang_domain_set, yuketang_lesson_set,
yuketang_exam_set, yuketang_list_update, yuketang_start_time. This change does not
add confirmation-only wrappers to existing immediate commands such as school
account save, course submissions/monitor actions or sync commands.

## Real gaps / release conditions

- Custom keyboard capability for the actual bot remains unverified. Enterprise
  authentication is a rate-limit category, not proof of keyboard permission.
- Production `qq_cards_buttons` remains false. The host adapter gate
  `enable-henu-buttons` is also false unless explicitly configured for verified use.
- The repository SDK dependency lacks `HenuMarkdown`; native behavior is tested
  against the locally available production SDK 0.4.13. Older SDKs retain text.
- Host callbacks currently use the reviewed WebSocket path only. HENU button
  callbacks on the host's webhook path are deliberately refused. Dify behavior
  remains separate.
- Production has independent business hotfixes (version 2.1.0 versus public
  2.0.5). Do not install a whole public package over it. A future release must
  adapt the changed shared modules while preserving production library preflight,
  reservation-plan revalidation and durable executing/outcome receipts; add the
  pending bot field at creation and `cancel_pending` under its existing lock.
- No production deployment, real QQ button delivery, phone client inspection,
  new school permission or actual reservation was performed for this change.

## Host patch

`ops/qq-buttons-host.patch` applies to the freshly read production QQ adapter
SHA256 `b9c326f80e67ad242f10ca626663ccf5bcdd4f12ad0eea7bbfbd237b30bca106`.
The reviewed companion modules are `qq_buttons_host.py` (copy to
`henu_qq_buttons.py`) and the updated `qq_cards_transport.py` (copy to
`henu_qq_cards.py`). The prior Plain card API hooks remain installed.

The bridge keeps the exact callback event anchor on each query; it never uses
another click's session cache. ACK uses `d.id` once; passive replies use the
top-level WebSocket event id. C2C receipts retain their QQ reference metadata.
Native pagination shows a short page count and button navigation; the readable
fallback keeps the original reply-to-page text.

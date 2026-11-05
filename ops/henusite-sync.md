# HenuSite → LangBot native knowledge base

## Scope and acceptance

User-approved 2026-10-23: screen public material, import into an independent KB,
configure automatic synchronization, copy the existing pipeline and bind only
HENU-Bot. Preserve the original KB, models, tools, plugins and other bots.
Review baseline: `4673d25` on `origin/langbot-plugin`.

The approved public test boundaries are:

1. Public library download, conservative selection and textual provenance.
2. Native KB ingestion: idempotence, interruption recovery and withdrawal.
3. Native pipeline copy and HENU-Bot-only binding, preserving existing resources.

Run `python -m pytest tests/test_henusite_sync.py`; run the full repository suite
before closing implementation. Real acceptance additionally requires native
document readback and retrieval, a no-op sync, pipeline/bot readback and an
independent comparison with the pre-change configuration. A QQ user's actual
reply is separate from API/configuration acceptance; do not send unsolicited QQ
test messages.

## Data policy

`henusite_reviewed.json` selects exact evidence IDs and reviewed source hashes.
Initial batch: 50 historical question/answer pairs, including the 3 test samples.
The full source contains 19,106 records; this is deliberately not a full dump.
Only original question/reply excerpts are quoted. Generic generated summaries,
images without text, chatter, incomplete/mixed questions and unsupported claims
are excluded. Historical discussion is not verified current school policy.
Every document fits in one 768-character child chunk and contains origin, month,
campus and historical qualification. Unknown campus stays unknown.

Public shards are read as the website reads them: fixed HTTPS origin, SHA-256,
manifest AES-GCM key/IV, gzip and full count/unique-ID checks. An invalid or
incomplete download makes no KB changes. No browser or campus login is used.

Missing records, changed reviewed evidence, non-history/disputed source state
or no-longer-answered records are removed from this managed KB on refresh.
Changed/new content is queued for screening rather than silently promoted.
The original 61-document KB and the 3-sample test KB are explicitly protected.

## Deployment and synchronization

Host files:

- `/opt/henusite-sync/henusite_sync.py`: reviewed code, root-owned.
- `/etc/henusite-sync/config.json`: fixed LangBot HTTPS origin, owned target KB
  UUID, `state_dir`, `reviewed_file`, `api_key_file`.
- `/etc/henusite-sync/reviewed.json`: current reviewed selection.
- `/etc/henusite-sync/api.key`: owner-only credential; never log or commit.
- `/var/lib/henusite-sync/`: cache, atomic journal, documents, filter report,
  pending review queue and last-success readback.

The service runs as a dedicated Unix user. The persistent timer runs daily at
06:00 Asia/Shanghai, with up to five minutes of jitter. It runs without Codex or
the user's computer. Only this target KB is reconciled. The recurring service
never creates/rebinds a bot or edits pipelines.

The journal is persisted before attachment/deletion. After an uncertain attach,
resume reconciles the native file list; if no matching native row is visible,
it stops for inspection instead of blindly adding another document. Native
indexing is sequential and bounded, avoiding a burst against the embedding
model. Failed/unknown native status requires reconciliation, not unlimited
retry. Upload timeouts may leave an unreferenced storage object, not a second
knowledge-base document.

`screened.json` maps source IDs to full source hashes at the last screening.
Initial deployment seeds it with the verified full source snapshot. `pending-review.json`
contains only additions/changes after that screening. A daily Codex follow-up
screens queued content under the same conservative policy, updates exact
evidence approvals and screened hashes, then starts this service. It remains
quiet on no change; unsafe/ambiguous snippets stay excluded. The server timer
continues refreshing and withdrawing existing approved content even when the
Codex follow-up is unavailable. New content awaits screening in that case.

Inspect with `systemctl status henusite-sync.timer henusite-sync.service`,
`journalctl -u henusite-sync.service`, and the native KB file list. The
`last-success.json` records completed native readback, not just HTTP status.

## One-time binding and recovery

`connect_henu_bot` uses native `POST /pipelines/<original>/copy`, preserving
config, stages and extensions. It appends this KB and a historical-source rule
to the copy, then changes only HENU-Bot's `use_pipeline_uuid`. It journals copy
intent before writing and stops on concurrent config changes or uncertain copy.
The original prompt remains intact. Existing official knowledge and live tool
results take precedence; at most 3 historical entries are cited with date,
campus and source. Plugin settings/storage remain shared and untouched.

Binding reloads HENU-Bot's adapter and clears its current conversation, causing
a short reconnection; no LangBot/plugin container restart is needed.

To disconnect this feature, point only HENU-Bot back to the recorded original
pipeline using native API, and disable this timer. Do not restore the whole DB
or shared plugin storage. Keep the old pipeline and knowledge bases. The binding
ledger and original configuration snapshot are owner-only deployment evidence.

Official reference: [native knowledge bases](https://langbot.app/docs/zh/usage/knowledge/readme/).

# Library reservation recovery (2026-11-16)

The live official entry remains https://zwyy.henu.edu.cn/ and redirects to `/h5/`.
The H5 build `index.1770306002786.js` still uses the v4 seat/confirm APIs,
`authorization: bearer<token>`, daily AES key and `ZZWBKJ_ZHIHUAWEI` IV.

Two independent defects were reproduced:

1. Restoring PHPSESSID without a domain sends both the expired domainless cookie
   and the new library-scoped cookie. The official CAS callback loops. Restore
   service cookies on the library host and keep private metadata out of HTTP.
   Keep the official CAS callback: its opaque `cas` value is not a raw CAS ST.
2. Production's internal `library plan` passed through public presentation and
   size compaction. Removing `seats` made a free seat appear unavailable. The
   production-flow patch uses a task-local flag so internal preflight receives
   the business result after normal storage/identity handling. The flag resets
   in `finally`; public queries keep their ordinary presentation and bounds.

The cookie fix belongs in the published LangBot tree. The second fix belongs
on `codex/production-library-term-20261009`, which owns the deployed preflight
flow; production contains subsequent compatibility overlays and must receive
an incremental patch, not a whole-directory replacement. MCP and Agent Skill
have not been deployed by this task; their cookie restoration requires the
same source correction before their next release.

Validation: published LangBot suite 1019 passed, 2 skipped. Production candidate
7 new regressions passed; 33 focused regressions passed in the actual runtime.
The production source snapshot's broad legacy suite has 126 failures on both
baseline and candidate (no new failing test names), so it is not represented
as a green release suite. Targeted tests cover real requests cookie redirects,
public booking/confirmation/readback, missing readback without resubmission,
large preflight results and context reset after cancellation.

Live candidate login and official area/time/seat queries succeeded. The school
returned booking hours 06:30–23:00 Asia/Shanghai and locked seats before opening.
Deployment and successful real booking must be recorded separately; login,
query and a saved plan do not establish a successful reservation.

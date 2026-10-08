# Empty classroom current-term resolution

## Problem and observed school data

The bot required a manual term code on every empty-classroom request and suggested `2026,1` for autumn. A read-only probe with the existing authenticated school session on 2026-10-09 returned `_currentXn=2026`, `_currentXq=0`. The classroom term list identified `2026,0` as `2026-2027学年第一学期`. No login, campus write, credential disclosure, production change, or deployment was performed by the probe.

The published tool had a terms-list endpoint and an authenticated current-term reader, but did not connect them. Its missing-parameter message listed all timing parameters, including an automatically obtainable term.

## Requirements

1. For an omitted term, read authenticated school current-year/current-part metadata and cross-check it against the classroom term list. Never infer the term from the month, a conventional 1/2 mapping, or list order.
2. Preserve an explicitly supplied term, including a historical term. Do not overwrite it with the current term.
3. If current metadata is absent, unauthenticated, inconsistent, or unavailable, return `current_term_unavailable`; do not query rooms under a guessed term.
4. Apply the same resolution to free, day_matrix, occupancy, and schedule-sync operations. Unrelated classroom metadata views do not need a term.
5. For queries, an omitted weekday means today in the existing campus timezone. Explicit weekdays are retained. Teaching week is not guessed without reliable calendar evidence; a free query still needs its week and period, while day_matrix can cover all five periods.
6. Return `term_context` and only actual remaining `missing_params`; successful CLI replies show the term actually used. Help, prompts, and Agent Skill guidance must agree with these defaults.
7. Mirror the shared client and tool-wrapper behavior across all three delivery trees and preserve unrelated WIP.

## Public verification boundaries

`EmptyClassroomClient.resolve_term`, `empty_classroom_query`, `empty_classroom_sync`, and `HenuCliSafe.call`. Fixtures replace authentication/room-query adapters; the term resolver and CLI-to-tool routing execute their actual code. The original missing-term test failed before the fix and passed afterward.

Nine regression cases cover current-term cross-checking with a future term first in the list, explicit historical terms, unauthenticated/missing/inconsistent context, remaining parameter gaps, sync, daily matrix, stopping before room queries on failure, and the actual CLI reply.

## Final verification

- Full suites passed: LangBot 250, MCP 144, Agent Skill 41; total 435. Existing third-party warnings remain.
- Independent specification and Python standards reviews approved the final change with no remaining findings. The sync missing-parameter finding was fixed and covered by a regression case.
- The shared client source and the query/sync function contracts match across all three delivery trees.
- The candidate package contains the current client, wrapper, and prompt bytes, both required JSON seed resources, and no absolute or traversal archive paths. A fresh extraction passed resolver and CLI import checks.
- Candidate: `dist/jry21223-henu_assistant-2.0.4.lbpkg`; SHA-256 `82c4d6607de66ae291859db798fe3261bffd8f924f86ecca2d4745cb7835a95d`. This build supersedes the package recorded in the earlier library-flow audit.

The real school probe verified current-term metadata only. Room-query regressions used fixtures; no real classroom availability result or booking success is claimed. Production currently has version drift from the published tree, so a future rollout must reconcile its existing changes before installing this candidate.

This is a local source change. The production Bot has not been updated by this task.

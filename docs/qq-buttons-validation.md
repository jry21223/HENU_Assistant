# Button and status validation

Change base: `eff3a09bd69303a5ad391a273f003e6335f285de`.
Implementation/test head: `39fc0134101de5f2fc3196b65ae9758acf099485`.
Draft: [PR 48](https://github.com/jry21223/HENU_Assistant/pull/48).

## Evidence

- Production SDK 0.4.13: 183 related card/confirmation/KIT/login tests passed
  before the final unique-ID regression; all 53 directly affected tests pass
  after it. Old SDK text compatibility subset: 153 passed before the final
  status-copy changes. Native tests require the installed production component
  and are explicitly skipped on the old public SDK.
- Existing Food draft loopback regressions: 24 passed. Native Food complete
  preview blocking and one-publication behavior are also in the focused tests.
- Failure-first checks reproduced: cancel could erase concurrent replacement;
  ambiguous campus failure could be submitted twice; native keyboard parameter
  was absent from transport; combined query/page IDs collided. All corresponding
  regressions pass after the changes.
- Existing direct Rain login is tested with buttons disabled through the actual
  Identity listener's Storage service boundary.
- Keyboard payload validation covers owned permissions, unique IDs, documented
  row/column limits, mutually exclusive passive anchors and C2C/group budgets.
  Callback tests retain the exact WebSocket event ID and acknowledge interaction
  ID once without touching Dify form state.
- Native rendering keeps button page text; explicit rejection uses the original
  readable controls. No automatic resend after timeout/5xx/uncertain acceptance.
- Host patch dry-run passed against the freshly fetched static adapter SHA
  recorded in the coverage document. Source compilation and targeted F checks pass.
- GitHub's existing [compile/test/package workflow](https://github.com/jry21223/HENU_Assistant/actions/runs/38544075689)
  completed successfully at implementation head. No manual full-suite rerun on Mac.
- Standards and Spec independent reviews closed the Storage adapter, replacement
  race and duplicate-ID findings; final Spec combination validation was independently
  executed. User screenshots were consumed privately and were not committed or uploaded.

## Preview and limits

The generated [confirmation/query preview](previews/buttons-synthetic.png) and
[Rain status preview](previews/rain-status-synthetic.png) use synthetic data and
actual generated card/keyboard payloads, not QQ client screenshots. No browser
or desktop capture was used. The Rain image is also saved as a new Library image.

No production source, setting, token, user data or school permission changed.
No actual QQ message, login, reservation, answer or submission was performed.
Actual custom-keyboard capability and client delivery remain unverified; both
button gates remain closed. The public package must not replace production's
independent business hotfixes. See the coverage document for the required overlay.

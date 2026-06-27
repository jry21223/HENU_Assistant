# Langbot Plugin Bug Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Audit the LangBot plugin delivery surface and fix only clear, low-risk bugs backed by code, build output, or local validation.

**Architecture:** Treat the plugin as five layers: package entry, event context capture, tool execution, service/storage integration, and delivery validation. Each task owns one layer and may make a narrow fix only when the defect is concrete and inside the approved scope.

**Tech Stack:** Python 3.13, LangBot plugin API, `lbp build`, pytest, GitHub Actions release workflow.

---

## File Structure

- `manifest.yaml`: plugin metadata, version, component directories, icon path.
- `main.py`: plugin entrypoint and service initialization.
- `.github/workflows/release-lbp.yaml`: release build workflow.
- `components/event_listener/identity_capture.py`: runtime identity capture, query-variable priming, prompt/user-message enrichment.
- `components/event_listener/identity_capture_safe.py`: safer runtime-context listener wrapper.
- `components/cli_tools/base.py`: shared Tool call lifecycle, storage materialization, runtime context preload, QQ-safe output trimming.
- `components/cli_tools/henu_cli.py`: Tool component exposed to LangBot.
- `components/cli_tools/henu_cli_safe.py`: compact response wrapper for QQ delivery.
- `henu_plugin/service.py`: command dispatch and business-tool bridge.
- `henu_plugin/cli.py`: CLI-style command parser and help text.
- `henu_plugin/storage_adapter.py`: LangBot Storage API bridge and per-user file materialization.
- `tests/test_storage_adapter.py`: existing lightweight storage tests.
- `README.md`: operator-facing behavior and release documentation.

## Shared Rules For Every Task

- Work in `/Users/jerry/Desktop/Study/HENU_Assistant/langbot-plugin/.worktrees/langbot-plugin-bug-audit`.
- Check `git status --short --branch` before editing.
- Do not edit `mcp-server`, `agent-skill`, Docker files, or LangBot core adapter code.
- Do not perform real campus booking, sign-in, account-login, course submission, or destructive external actions.
- If no evidence-backed bug is found in a task, commit nothing for that task and report the checks performed.
- If a bug is found, make the smallest safe fix in the files listed for that task.
- Prefer a focused regression test when the defect is testable without credentials.
- After any code change, run `python3 -m compileall -q components henu_plugin main.py`.
- Commit only the files changed for that task.

---

### Task 1: Package Entry And Release Audit

**Files:**
- Inspect: `manifest.yaml`
- Inspect: `main.py`
- Inspect: `.github/workflows/release-lbp.yaml`
- Inspect: `README.md`
- Modify only if needed: the same files

- [ ] **Step 1: Inspect current repository state**

Run:

```bash
git status --short --branch
```

Expected: branch is `langbot-plugin`; existing design/plan commits may be ahead of origin.

- [ ] **Step 2: Check plugin declaration consistency**

Inspect:

```bash
sed -n '1,180p' manifest.yaml
sed -n '1,180p' main.py
sed -n '1,180p' .github/workflows/release-lbp.yaml
sed -n '1,140p' README.md
```

Confirm:

- `manifest.yaml` points `execution.python.path` to `main.py`.
- `execution.python.attr` names a class exported by `main.py`.
- `spec.components.EventListener.fromDirs` includes `components/event_listener/`.
- `spec.components.Tool.fromDirs` includes `components/cli_tools/`.
- `metadata.icon` points to an existing tracked asset.
- workflow uses Python 3.13 and runs `lbp build`.
- README release instructions do not contradict the workflow.

- [ ] **Step 3: Apply a narrow fix only if a concrete mismatch is found**

Allowed fixes:

- Correct a wrong manifest path, attr name, component directory, icon path, or workflow build command.
- Correct README release instructions if they describe a stale tag/version pattern that would mislead operators.

Do not bump plugin version in this task unless the actual code or release metadata changes.

- [ ] **Step 4: Validate package entry**

Run:

```bash
python3 -m compileall -q main.py
lbp build
```

Expected:

- `compileall` exits 0.
- `lbp build` exits 0 and reports a `dist/jry21223-henu_assistant-1.2.15.lbpkg` artifact when no release bump has been made yet.

- [ ] **Step 5: Commit if changed**

If files changed:

```bash
git add manifest.yaml main.py .github/workflows/release-lbp.yaml README.md
git commit -m "fix(langbot-plugin): repair package entry metadata"
```

If no files changed, do not commit.

---

### Task 2: Event Context And Identity Isolation Audit

**Files:**
- Inspect: `components/event_listener/identity_capture.py`
- Inspect: `components/event_listener/identity_capture_safe.py`
- Inspect: `components/event_listener/identity_capture.yaml`
- Modify only if needed: the same files

- [ ] **Step 1: Inspect event listener registration and query-variable access**

Run:

```bash
sed -n '1,520p' components/event_listener/identity_capture.py
sed -n '1,180p' components/event_listener/identity_capture_safe.py
sed -n '1,120p' components/event_listener/identity_capture.yaml
rg -n "get_query_var\\(|get_query_vars\\(|set_query_var\\(|_henu_runtime_context|sender_id|launcher_id|prevent_default|reply_message_chain" components/event_listener -S
```

Confirm:

- Direct `ctx.get_query_var(...)` calls are wrapped so missing keys cannot raise into LangBot.
- `_henu_runtime_context` is primed before code paths that might read it.
- Group messages prefer `sender_id` for per-user storage.
- Private messages can fall back to `launcher_id`.
- Account-status auto-reply uses current sender context and does not leak another user's data.

- [ ] **Step 2: Check concrete failure patterns**

Run:

```bash
rg -n "await ctx\\.get_query_var\\(|query\\.variables\\[|_henu_runtime_context" components/event_listener -S
python3 -m compileall -q components/event_listener
```

Expected:

- No unguarded direct `await ctx.get_query_var(...)` in event listeners.
- `compileall` exits 0.

- [ ] **Step 3: Apply a narrow fix only if a concrete bug is found**

Allowed fixes:

- Replace an unguarded query-var read with the existing `_safe_get_query_var` helper.
- Prime `_henu_runtime_context` with `{}` where the listener can otherwise trigger a LangBot missing-variable traceback.
- Correct sender/launcher resolution if code evidence shows group storage could use the group ID instead of the sender ID.

Do not change prompt text style or add new user-facing features.

- [ ] **Step 4: Validate listener layer**

Run:

```bash
python3 -m compileall -q components/event_listener
```

Expected: exits 0.

- [ ] **Step 5: Commit if changed**

If files changed:

```bash
git add components/event_listener/identity_capture.py components/event_listener/identity_capture_safe.py components/event_listener/identity_capture.yaml
git commit -m "fix(langbot-plugin): harden identity context capture"
```

If no files changed, do not commit.

---

### Task 3: Tool Execution And QQ-Safe Output Audit

**Files:**
- Inspect: `components/cli_tools/base.py`
- Inspect: `components/cli_tools/henu_cli.py`
- Inspect: `components/cli_tools/henu_cli_safe.py`
- Inspect: `components/cli_tools/henu_cli.yaml`
- Modify only if needed: the same files

- [ ] **Step 1: Inspect tool lifecycle and output normalization**

Run:

```bash
sed -n '1,380p' components/cli_tools/base.py
sed -n '1,180p' components/cli_tools/henu_cli.py
sed -n '1,180p' components/cli_tools/henu_cli_safe.py
sed -n '1,140p' components/cli_tools/henu_cli.yaml
rg -n "save_all\\(|set_current_user_paths\\(|_normalize_for_qq_delivery|_trim_text|_make_payload_json|json.dumps|Exception|return \\{\" components/cli_tools -S
```

Confirm:

- User storage paths are cleared in `finally`.
- Storage is saved after tool execution.
- Runtime context preloading cannot crash on missing query variables.
- Tool results always return a dict with a usable `msg`.
- QQ-safe normalization trims oversized payloads and removes heavy fields before LangBot sends text.

- [ ] **Step 2: Run local syntax checks**

Run:

```bash
python3 -m compileall -q components/cli_tools
```

Expected: exits 0.

- [ ] **Step 3: Apply a narrow fix only if a concrete bug is found**

Allowed fixes:

- Ensure non-dict or exception outputs become `{"success": false, "msg": "..."}` instead of leaking an incompatible shape.
- Ensure `_normalize_for_qq_delivery` handles non-JSON-serializable values through existing normalization helpers.
- Ensure user storage cleanup remains in `finally`.
- Ensure runtime query-var preloading is best-effort and cannot raise into the host process.

Do not change command names or add new CLI features.

- [ ] **Step 4: Validate tool layer**

Run:

```bash
python3 -m compileall -q components/cli_tools
lbp build
```

Expected:

- `compileall` exits 0.
- `lbp build` exits 0.

- [ ] **Step 5: Commit if changed**

If files changed:

```bash
git add components/cli_tools/base.py components/cli_tools/henu_cli.py components/cli_tools/henu_cli_safe.py components/cli_tools/henu_cli.yaml
git commit -m "fix(langbot-plugin): harden henu cli tool replies"
```

If no files changed, do not commit.

---

### Task 4: Service, CLI, And Storage Adapter Audit

**Files:**
- Inspect: `henu_plugin/service.py`
- Inspect: `henu_plugin/cli.py`
- Inspect: `henu_plugin/storage_adapter.py`
- Inspect: `tests/test_storage_adapter.py`
- Modify only if needed: the same files

- [ ] **Step 1: Inspect dispatch, command parsing, and storage paths**

Run:

```bash
sed -n '1,260p' henu_plugin/storage_adapter.py
sed -n '1,360p' henu_plugin/cli.py
sed -n '1,240p' henu_plugin/service.py
sed -n '240,520p' henu_plugin/service.py
sed -n '520,920p' henu_plugin/service.py
sed -n '1,240p' tests/test_storage_adapter.py
rg -n "run_tool|parse|seminar|library|schedule|course|storage|sender_id|launcher_id|shared|users|save_all|load_all|Path\\(" henu_plugin tests -S
```

Confirm:

- CLI commands documented in README map to service tools.
- Storage adapter separates user data from shared data.
- `load_all` and `save_all` paths match the files used by shared campus modules.
- Tool dispatch returns dict-shaped results with `success` and `msg` where expected.
- No real campus side effects happen during local tests.

- [ ] **Step 2: Run existing storage tests**

Run:

```bash
pytest -q tests/test_storage_adapter.py
```

Expected: tests pass, or fail with a concrete local bug that can be fixed inside this task.

- [ ] **Step 3: Apply a narrow fix only if a concrete bug is found**

Allowed fixes:

- Correct a broken command mapping between README/CLI/service if the implementation clearly points to the intended tool.
- Correct storage key/path handling if tests or code prove user/shared data can be misplaced.
- Add or adjust a small storage-adapter test for a confirmed bug.

Do not add new campus features or change shared business rules.

- [ ] **Step 4: Validate service/storage layer**

Run:

```bash
python3 -m compileall -q henu_plugin tests/test_storage_adapter.py
pytest -q tests/test_storage_adapter.py
```

Expected: commands exit 0.

- [ ] **Step 5: Commit if changed**

If files changed:

```bash
git add henu_plugin/service.py henu_plugin/cli.py henu_plugin/storage_adapter.py tests/test_storage_adapter.py
git commit -m "fix(langbot-plugin): repair service storage integration"
```

If no files changed, do not commit.

---

### Task 5: Final Validation, Push, And Release Decision

**Files:**
- Inspect: all tracked files changed by previous tasks
- Modify only if needed: `manifest.yaml`

- [ ] **Step 1: Review changed files**

Run:

```bash
git status --short --branch
git log --oneline -8
git diff --stat origin/langbot-plugin..HEAD
```

Expected: only design, plan, and evidence-backed fix commits are ahead of origin.

- [ ] **Step 2: Run final lightweight validation**

Run:

```bash
python3 -m compileall -q components henu_plugin main.py
pytest -q tests/test_storage_adapter.py
lbp build
```

Expected:

- `compileall` exits 0.
- storage tests pass.
- `lbp build` exits 0.

- [ ] **Step 3: Decide whether a version bump is required**

If code changed in Tasks 1-4, bump `manifest.yaml` from `1.2.15` to `1.2.16`.

If only docs changed and no plugin code changed, do not bump version.

Run after a bump:

```bash
lbp build
```

Expected: the artifact name matches the new manifest version.

- [ ] **Step 4: Commit version bump if needed**

If `manifest.yaml` changed:

```bash
git add manifest.yaml
git commit -m "chore(langbot-plugin): release 1.2.16"
```

Use the actual version in the commit message.

- [ ] **Step 5: Push branch**

Run:

```bash
git push origin langbot-plugin
```

Expected: push succeeds.

- [ ] **Step 6: Push tag only if a version bump was committed**

If a new plugin version was committed:

```bash
git tag v1.2.16
git push origin v1.2.16
```

Expected: tag push succeeds and GitHub Actions starts `Build and Release lbp`.

- [ ] **Step 7: Confirm release workflow only if tagged**

If a tag was pushed:

```bash
gh run list --workflow release-lbp.yaml --limit 3
```

Expected: the newest run for `v1.2.16` completes successfully.

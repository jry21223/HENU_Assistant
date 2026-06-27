# Langbot Plugin Evidence-Driven Bug Audit Design

Date: 2026-06-27

## Goal

Perform a focused audit of the `langbot-plugin` delivery surface and fix only clear, low-risk bugs that are backed by repository evidence. The audit should keep production behavior stable while improving reliability around startup, LangBot query variables, per-user storage isolation, QQ-safe replies, and release packaging.

## Scope

The work is limited to `/Users/jerry/Desktop/Study/HENU_Assistant/langbot-plugin`.

The audit may inspect these areas:

- Plugin entry and packaging: `manifest.yaml`, `main.py`, `.github/workflows/release-lbp.yaml`.
- Event context capture: `components/event_listener/identity_capture.py` and safe listener wrappers.
- Tool execution: `components/cli_tools/base.py`, `components/cli_tools/henu_cli.py`, `components/cli_tools/henu_cli_safe.py`.
- Service and storage: `henu_plugin/service.py`, `henu_plugin/cli.py`, `henu_plugin/storage_adapter.py`.
- Campus feature paths used by LangBot: seminar rooms, library seats, schedules, course status, and read-only course monitoring.

The audit should not change `mcp-server` or `agent-skill` unless the plugin has a direct broken reference to those surfaces.

## Bug Fix Policy

Fix only issues that satisfy all of these conditions:

- The issue has concrete evidence in code, build output, or a local low-risk verification command.
- The fix is narrow and does not change unrelated architecture.
- The fix is safe for production deployment.
- The expected behavior is clear from existing code, README, manifest, or recent production errors.

Examples of in-scope fixes:

- Startup failures or import errors.
- Missing-query-variable crashes such as `_henu_runtime_context` lookup failures.
- Per-user storage leaks or sender/launcher identity mixups.
- Tool output shapes that can break LangBot or QQ official sending.
- Packaging, workflow, version, or manifest mistakes that prevent release.
- Obvious command mapping or path mistakes in plugin-only code.

Examples of out-of-scope work:

- Refactoring large files for style alone.
- Changing shared MCP or Agent Skill behavior.
- Adding automatic course selection submission.
- Running real booking, sign-in, account-login, or destructive campus actions.
- Reworking Docker or LangBot core adapter code as part of this plugin audit.

## Audit Flow

1. Check repository state and recent commits for local drift.
2. Review plugin declaration, entrypoint, workflow, and release configuration.
3. Review event listeners for identity capture, query variable access, and group/person isolation.
4. Review tool wrappers for storage lifecycle, runtime-context preloading, exception handling, and QQ-safe output.
5. Review service, CLI parser, and storage adapter for command mapping and path consistency.
6. Review LangBot-used campus paths for obvious integration errors without triggering real external side effects.
7. Apply only evidence-backed low-risk fixes.
8. Run lightweight validation.
9. Commit and push fixes if changes are made.

## Validation

Use validation that does not require real campus accounts or external side effects:

- Python syntax/import-oriented checks such as `compileall` on plugin packages.
- Existing lightweight tests, if they run without external credentials.
- `lbp build` with Python 3.13 tooling.
- GitHub workflow/tag checks only if a release is intentionally triggered.

If validation reveals a problem outside the approved scope, record it for follow-up instead of broadening the fix.

## Deliverable

The implementation phase should produce one of these outcomes:

- No code changes: a concise bug audit summary with residual risks.
- Code changes: a small commit pushed to `origin/langbot-plugin`, plus validation results.
- If a release is needed after fixes: a version bump, tag push, and confirmation that the release workflow completed successfully.

## Non-Goals

This is not a general cleanup pass. It should not chase speculative bugs, rewrite the plugin architecture, or change behavior that is shared with other HENU delivery variants unless a plugin-only breakage requires it.

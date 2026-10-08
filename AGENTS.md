# Repository guidance

This branch ships the LangBot plugin. Shared campus behavior remains copied across the three shipping branches; Qingmu is a LangBot-only integration.

## Remote synchronization

Run status, inspect remotes and fetch before remote-dependent inspection and before edits. Compare HEAD to upstream. Preserve unrelated dirty/ahead/diverged work; use an isolated worktree. Never auto-stash, reset or force-push. If fetch fails, do not modify repo files without explicit offline authorization.

## Standards

- Sensitive commands are intercepted before LLM/history; never expose student passwords or provider keys in tool outputs, errors or logs.
- User Storage is scoped to an explicit sender. Unknown identity and failed Storage must stop external effects.
- External writes require a later real-user confirmation in the same private conversation. Record unknown outcomes and avoid automatic paid retries.
- Use verified HTTPS, no redirects, bounded responses and operator-configured provider contracts. Do not infer official authorization or live prices from archived data.
- Test behavior at CLI/message and HTTP boundaries. Distinguish local tests, GitHub publication, production deployment and real-user acceptance.

## Agent skills

### Issue tracker

Use this repository's GitHub Issues. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the five default Matt Pocock labels. See `docs/agents/triage-labels.md`.

### Domain docs

Single context: root `GLOSSARY.md` and `docs/adr/`. See `docs/agents/domain.md`.

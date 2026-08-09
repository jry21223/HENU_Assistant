#!/usr/bin/env python3
"""Fail closed unless a manual 2.1.0 release pins the complete release train."""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from scripts.build_plugin import PackageVerificationError, _manifest_identity  # noqa: E402


SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
REMOTE_REFS = {
    "mcp-server": "refs/remotes/origin/mcp-server",
    "agent-skill": "refs/remotes/origin/agent-skill",
    "langbot-plugin": "refs/remotes/origin/langbot-plugin",
}


class ReleaseTrainError(RuntimeError):
    """The release request is incomplete, inconsistent, or out of order."""


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise ReleaseTrainError(f"git {' '.join(args)} failed: {detail}")
    return completed.stdout.strip()


def _normalized_sha(value: str, label: str) -> str:
    normalized = str(value or "").strip().lower()
    if SHA_PATTERN.fullmatch(normalized) is None:
        raise ReleaseTrainError(f"{label} must be one immutable 40-character commit SHA")
    return normalized


def _version_from_source(source: str, label: str) -> str:
    try:
        tree = ast.parse(source, filename=label)
    except SyntaxError as exc:
        raise ReleaseTrainError(f"cannot parse {label}: {exc}") from exc
    for statement in tree.body:
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
        value = statement.value
        if any(isinstance(target, ast.Name) and target.id == "__version__" for target in targets):
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                return value.value.strip()
    raise ReleaseTrainError(f"{label} does not declare a string __version__")


def _version_at(root: Path, sha: str, label: str) -> str:
    source = _git(root, "show", f"{sha}:henu_mcp/version.py")
    return _version_from_source(source, f"{label}:henu_mcp/version.py")


def _validate_smoke_evidence(value: str) -> str:
    evidence = str(value or "")
    parsed = urlparse(evidence)
    if (
        evidence != evidence.strip()
        or any(character.isspace() for character in evidence)
        or parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ReleaseTrainError(
            "read-only smoke evidence must be a durable HTTPS URL without credentials, query, or fragment"
        )
    return evidence


def _actions_run_id(value: str, repository: str) -> str:
    evidence = str(value or "")
    parsed = urlparse(evidence)
    expected_prefix = f"/{repository.strip('/')}/actions/runs/"
    if (
        evidence != evidence.strip()
        or any(character.isspace() for character in evidence)
        or parsed.scheme != "https"
        or parsed.netloc.lower() != "github.com"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith(expected_prefix)
    ):
        raise ReleaseTrainError(
            f"CI evidence must be a clean GitHub Actions run URL for {repository}"
        )
    run_id = parsed.path.removeprefix(expected_prefix).strip("/")
    if not run_id.isdigit() or "/" in run_id:
        raise ReleaseTrainError("CI evidence URL does not identify one Actions run")
    return run_id


def _validate_actions_run(
    *,
    run_url: str,
    repository: str,
    token: str,
    expected_sha: str,
    expected_workflow: str,
    expected_workflow_path: str,
) -> None:
    if not token:
        raise ReleaseTrainError("GitHub token is required to verify CI evidence")
    run_id = _actions_run_id(run_url, repository)
    request = Request(
        f"https://api.github.com/repos/{repository}/actions/runs/{run_id}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "henu-assistant-release-train",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urlopen(request, timeout=20) as response:  # noqa: S310 - fixed GitHub API host
            payload = json.load(response)
    except Exception as exc:
        raise ReleaseTrainError(f"cannot verify GitHub Actions run {run_id}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ReleaseTrainError(f"GitHub Actions run {run_id} returned an invalid response")
    actual_repository = str((payload.get("repository") or {}).get("full_name") or "")
    if actual_repository.lower() != repository.lower():
        raise ReleaseTrainError(f"Actions run {run_id} belongs to {actual_repository!r}")
    if str(payload.get("name") or "") != expected_workflow:
        raise ReleaseTrainError(
            f"Actions run {run_id} is {payload.get('name')!r}, expected {expected_workflow!r}"
        )
    if str(payload.get("path") or "") != expected_workflow_path:
        raise ReleaseTrainError(
            f"Actions run {run_id} workflow path is {payload.get('path')!r}, "
            f"expected {expected_workflow_path!r}"
        )
    if payload.get("status") != "completed" or payload.get("conclusion") != "success":
        raise ReleaseTrainError(f"Actions run {run_id} has not completed successfully")
    if str(payload.get("head_sha") or "").lower() != expected_sha:
        raise ReleaseTrainError(
            f"Actions run {run_id} head SHA does not match the release candidate"
        )


def _assert_release_absent(*, repository: str, tag: str, token: str) -> None:
    """Refuse to mutate a public release created by an earlier run."""
    if not token:
        raise ReleaseTrainError("GitHub token is required to verify release immutability")
    request = Request(
        f"https://api.github.com/repos/{repository}/releases/tags/{tag}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "henu-assistant-release-train",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urlopen(request, timeout=20) as response:  # noqa: S310 - fixed GitHub API host
            payload = json.load(response)
    except HTTPError as exc:
        if exc.code == 404:
            return
        raise ReleaseTrainError(
            f"cannot verify whether GitHub Release {tag} exists: HTTP {exc.code}"
        ) from exc
    except Exception as exc:
        raise ReleaseTrainError(
            f"cannot verify whether GitHub Release {tag} exists: {exc}"
        ) from exc
    release_id = payload.get("id") if isinstance(payload, dict) else None
    raise ReleaseTrainError(
        f"GitHub Release {tag} already exists (id={release_id!r}); immutable releases cannot be overwritten"
    )


def validate_release_train(
    *,
    root: Path,
    expected_version: str,
    github_ref: str,
    mcp_sha: str,
    agent_sha: str,
    langbot_sha: str,
    smoke_evidence: str,
    repository: str,
    github_token: str,
    mcp_ci_run_url: str,
    agent_ci_run_url: str,
    langbot_ci_run_url: str,
    mcp_released: str,
    agent_released: str,
    langbot_runtime_context_verified: str,
) -> None:
    root = root.resolve()
    expected_tag = f"refs/tags/v{expected_version}"
    if github_ref != expected_tag:
        raise ReleaseTrainError(
            f"manual release must run from {expected_tag}, got {github_ref or 'missing ref'}"
        )
    if str(mcp_released).lower() != "true" or str(agent_released).lower() != "true":
        raise ReleaseTrainError(
            "release order is mcp-server then agent-skill then langbot-plugin; prior releases must be confirmed"
        )
    if str(langbot_runtime_context_verified).lower() != "true":
        raise ReleaseTrainError(
            "LangBot runtime context smoke must verify trusted session/query_id injection"
        )
    _validate_smoke_evidence(smoke_evidence)

    requested = {
        "mcp-server": _normalized_sha(mcp_sha, "MCP Server SHA"),
        "agent-skill": _normalized_sha(agent_sha, "Agent Skill SHA"),
        "langbot-plugin": _normalized_sha(langbot_sha, "LangBot Plugin SHA"),
    }
    _validate_actions_run(
        run_url=mcp_ci_run_url,
        repository=repository,
        token=github_token,
        expected_sha=requested["mcp-server"],
        expected_workflow="CI",
        expected_workflow_path=".github/workflows/ci.yml",
    )
    _validate_actions_run(
        run_url=agent_ci_run_url,
        repository=repository,
        token=github_token,
        expected_sha=requested["agent-skill"],
        expected_workflow="Agent Skill CI",
        expected_workflow_path=".github/workflows/ci.yml",
    )
    _validate_actions_run(
        run_url=langbot_ci_run_url,
        repository=repository,
        token=github_token,
        expected_sha=requested["langbot-plugin"],
        expected_workflow="Test LangBot plugin",
        expected_workflow_path=".github/workflows/test-python.yaml",
    )
    _assert_release_absent(
        repository=repository,
        tag=f"v{expected_version}",
        token=github_token,
    )
    for branch, remote_ref in REMOTE_REFS.items():
        actual = _git(root, "rev-parse", f"{remote_ref}^{{commit}}").lower()
        if requested[branch] != actual:
            raise ReleaseTrainError(
                f"{branch} requested SHA {requested[branch]} is not remote head {actual}"
            )

    head = _git(root, "rev-parse", "HEAD^{commit}").lower()
    tag_commit = _git(root, "rev-parse", f"{expected_tag}^{{commit}}").lower()
    if requested["langbot-plugin"] not in {head, tag_commit} or head != tag_commit:
        raise ReleaseTrainError(
            "LangBot SHA, checked-out commit, and v2.1.0 tag must identify the same commit"
        )

    for branch, sha in requested.items():
        version = _version_at(root, sha, branch)
        if version != expected_version:
            raise ReleaseTrainError(
                f"{branch} henu_mcp.version is {version!r}, expected {expected_version!r}"
            )

    try:
        _author, _name, manifest_version = _manifest_identity(root)
    except PackageVerificationError as exc:
        raise ReleaseTrainError(str(exc)) from exc
    if manifest_version != expected_version:
        raise ReleaseTrainError(
            f"manifest metadata.version is {manifest_version!r}, expected {expected_version!r}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=SOURCE_ROOT)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--github-ref", default=os.environ.get("GITHUB_REF", ""))
    parser.add_argument("--mcp-sha", required=True)
    parser.add_argument("--agent-sha", required=True)
    parser.add_argument("--langbot-sha", required=True)
    parser.add_argument("--smoke-evidence", required=True)
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--github-token", default=os.environ.get("GITHUB_TOKEN", ""))
    parser.add_argument("--mcp-ci-run-url", required=True)
    parser.add_argument("--agent-ci-run-url", required=True)
    parser.add_argument("--langbot-ci-run-url", required=True)
    parser.add_argument("--mcp-released", required=True)
    parser.add_argument("--agent-released", required=True)
    parser.add_argument("--langbot-runtime-context-verified", required=True)
    args = parser.parse_args()
    try:
        validate_release_train(
            root=args.root,
            expected_version=args.expected_version,
            github_ref=args.github_ref,
            mcp_sha=args.mcp_sha,
            agent_sha=args.agent_sha,
            langbot_sha=args.langbot_sha,
            smoke_evidence=args.smoke_evidence,
            repository=args.repository,
            github_token=args.github_token,
            mcp_ci_run_url=args.mcp_ci_run_url,
            agent_ci_run_url=args.agent_ci_run_url,
            langbot_ci_run_url=args.langbot_ci_run_url,
            mcp_released=args.mcp_released,
            agent_released=args.agent_released,
            langbot_runtime_context_verified=args.langbot_runtime_context_verified,
        )
    except ReleaseTrainError as exc:
        print(f"release train rejected: {exc}", file=sys.stderr)
        return 1
    print("release train validated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

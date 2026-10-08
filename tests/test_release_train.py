from __future__ import annotations

import io
from pathlib import Path
from urllib.error import HTTPError

import pytest

from scripts import validate_release_train


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "release-lbp.yaml"
RUNBOOK = ROOT / "RELEASE_TRAIN.md"


def test_release_requires_manual_protected_approval_and_three_fixed_shas() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")

    assert "push:\n    tags:" not in source
    for required_input in (
        "mcp_server_sha:",
        "agent_skill_sha:",
        "langbot_plugin_sha:",
        "read_only_smoke_evidence:",
        "mcp_server_ci_run_url:",
        "agent_skill_ci_run_url:",
        "langbot_plugin_ci_run_url:",
        "mcp_server_released:",
        "agent_skill_released:",
        "langbot_runtime_context_verified:",
    ):
        assert required_input in source

    release_job = source.split("\n  release:\n", 1)[1]
    assert "github.event_name == 'workflow_dispatch'" in release_job
    assert "environment: henu-production-release" in release_job
    assert "permissions:" in release_job
    assert "actions: read" in release_job
    assert "contents: write" in release_job


def test_release_gate_validates_remote_heads_versions_tag_and_smoke_evidence() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    validator = (ROOT / "scripts" / "validate_release_train.py").read_text(
        encoding="utf-8"
    )
    combined = source + validator

    for contract in (
        "refs/heads/mcp-server",
        "refs/heads/agent-skill",
        "refs/heads/langbot-plugin",
        "origin/mcp-server",
        "origin/agent-skill",
        "origin/langbot-plugin",
        "scripts/validate_release_train.py",
        "--expected-version 2.1.0",
        "--smoke-evidence",
        "--mcp-ci-run-url",
        "--agent-ci-run-url",
        "--langbot-ci-run-url",
        "--mcp-released",
        "--agent-released",
        "--langbot-runtime-context-verified",
        'expected_tag = f"refs/tags/v',
    ):
        assert contract in combined

    assert "re.search" not in source
    assert "metadata.version" in source
    assert source.count(
        '--agent-released "$AGENT_SKILL_RELEASED" \\\n'
        '            --langbot-runtime-context-verified '
        '"$LANGBOT_RUNTIME_CONTEXT_VERIFIED"'
    ) == 2


def test_release_body_records_all_three_immutable_shas() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")

    assert "MCP Server SHA" in source
    assert "Agent Skill SHA" in source
    assert "LangBot Plugin SHA" in source
    assert "MCP Server CI" in source
    assert "Agent Skill CI" in source
    assert "LangBot Plugin CI" in source
    assert "Read-only smoke evidence" in source


def test_protected_release_job_revalidates_after_approval_and_pins_actions() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    release_job = source.split("\n  release:\n", 1)[1]

    assert "actions: read" in release_job
    assert "scripts/validate_release_train.py" in release_job
    assert "Fetch the three release branch heads after approval" in release_job
    assert "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1" in release_job
    assert "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97" in release_job
    assert "softprops/action-gh-release@3d0d9888cb7fd7b750713d6e236d1fcb99157228" in release_job
    assert "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a" in source
    assert "actions/download-artifact@37930b1c2abaa49bbe596cd826c3c89aef350131" in release_job
    assert "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02" not in source
    assert "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093" not in source
    assert "softprops/action-gh-release@v2" not in source
    for mutable_ref in (
        "actions/checkout@v4",
        "actions/setup-python@v5",
        "actions/upload-artifact@v4",
        "actions/download-artifact@v4",
    ):
        assert mutable_ref not in release_job


def test_release_job_serializes_the_immutable_tag_revalidation() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    release_job = source.split("\n  release:\n", 1)[1]

    assert "concurrency:" in release_job
    assert "group: henu-assistant-v2.1.0-release" in release_job
    assert "cancel-in-progress: false" in release_job


def test_release_runbook_defines_order_gates_rollback_and_known_limits() -> None:
    source = RUNBOOK.read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    for contract in (
        "mcp-server → agent-skill → langbot-plugin",
        "真实账号",
        "只读 smoke",
        "三个 40 位提交 SHA",
        "v2.0.4",
        "revert commit",
        "禁止强推",
        "单进程",
        "uncertain",
        "人工核验",
        "降级镜像",
        "跨域原子",
    ):
        assert contract in source
    assert "Tag push 不会自动发布" in readme
    assert "git push origin v2.1.0" not in readme


def test_release_validator_accepts_only_matching_remote_heads(monkeypatch: pytest.MonkeyPatch) -> None:
    mcp_sha = "1" * 40
    agent_sha = "2" * 40
    langbot_sha = "3" * 40
    responses = {
        ("rev-parse", "refs/remotes/origin/mcp-server^{commit}"): mcp_sha,
        ("rev-parse", "refs/remotes/origin/agent-skill^{commit}"): agent_sha,
        ("rev-parse", "refs/remotes/origin/langbot-plugin^{commit}"): langbot_sha,
        ("rev-parse", "HEAD^{commit}"): langbot_sha,
        ("rev-parse", "refs/tags/v2.1.0^{commit}"): langbot_sha,
        ("show", f"{mcp_sha}:henu_mcp/version.py"): '__version__ = "2.1.0"',
        ("show", f"{agent_sha}:henu_mcp/version.py"): '__version__ = "2.1.0"',
        ("show", f"{langbot_sha}:henu_mcp/version.py"): '__version__ = "2.1.0"',
    }
    monkeypatch.setattr(
        validate_release_train,
        "_git",
        lambda _root, *args: responses[args],
    )
    monkeypatch.setattr(
        validate_release_train,
        "_manifest_identity",
        lambda _root: ("jry21223", "henu_assistant", "2.1.0"),
    )
    verified_runs: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        validate_release_train,
        "_validate_actions_run",
        lambda **kwargs: verified_runs.append(
            (
                kwargs["expected_workflow"],
                kwargs["expected_workflow_path"],
                kwargs["expected_sha"],
            )
        ),
    )
    checked_releases: list[tuple[str, str]] = []
    monkeypatch.setattr(
        validate_release_train,
        "_assert_release_absent",
        lambda *, repository, tag, **_kwargs: checked_releases.append(
            (repository, tag)
        ),
    )

    validate_release_train.validate_release_train(
        root=ROOT,
        expected_version="2.1.0",
        github_ref="refs/tags/v2.1.0",
        mcp_sha=mcp_sha,
        agent_sha=agent_sha,
        langbot_sha=langbot_sha,
        smoke_evidence="https://example.invalid/smoke/2.1.0",
        repository="jry21223/HENU_Assistant",
        github_token="test-token",
        mcp_ci_run_url="https://github.com/jry21223/HENU_Assistant/actions/runs/1",
        agent_ci_run_url="https://github.com/jry21223/HENU_Assistant/actions/runs/2",
        langbot_ci_run_url="https://github.com/jry21223/HENU_Assistant/actions/runs/3",
        mcp_released="true",
        agent_released="true",
        langbot_runtime_context_verified="true",
    )
    assert verified_runs == [
        ("CI", ".github/workflows/ci.yml", mcp_sha),
        ("Agent Skill CI", ".github/workflows/ci.yml", agent_sha),
        (
            "Test LangBot plugin",
            ".github/workflows/test-python.yaml",
            langbot_sha,
        ),
    ]
    assert checked_releases == [("jry21223/HENU_Assistant", "v2.1.0")]

    with pytest.raises(validate_release_train.ReleaseTrainError, match="remote head"):
        validate_release_train.validate_release_train(
            root=ROOT,
            expected_version="2.1.0",
            github_ref="refs/tags/v2.1.0",
            mcp_sha="4" * 40,
            agent_sha=agent_sha,
            langbot_sha=langbot_sha,
            smoke_evidence="https://example.invalid/smoke/2.1.0",
            repository="jry21223/HENU_Assistant",
            github_token="test-token",
            mcp_ci_run_url="https://github.com/jry21223/HENU_Assistant/actions/runs/1",
            agent_ci_run_url="https://github.com/jry21223/HENU_Assistant/actions/runs/2",
            langbot_ci_run_url="https://github.com/jry21223/HENU_Assistant/actions/runs/3",
            mcp_released="true",
            agent_released="true",
            langbot_runtime_context_verified="true",
        )


def test_release_validator_refuses_to_mutate_an_existing_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        validate_release_train,
        "urlopen",
        lambda *_args, **_kwargs: io.BytesIO(b'{"id": 123, "tag_name": "v2.1.0"}'),
    )

    with pytest.raises(validate_release_train.ReleaseTrainError, match="already exists"):
        validate_release_train._assert_release_absent(
            repository="jry21223/HENU_Assistant",
            tag="v2.1.0",
            token="token",
        )


def test_release_validator_accepts_a_missing_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(*_args, **_kwargs):
        raise HTTPError("https://api.github.com", 404, "Not Found", None, None)

    monkeypatch.setattr(validate_release_train, "urlopen", missing)

    validate_release_train._assert_release_absent(
        repository="jry21223/HENU_Assistant",
        tag="v2.1.0",
        token="token",
    )


def test_actions_run_rejects_a_same_named_workflow_from_the_wrong_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "repository": {"full_name": "jry21223/HENU_Assistant"},
        "name": "Test LangBot plugin",
        "path": ".github/workflows/noop.yaml",
        "status": "completed",
        "conclusion": "success",
        "head_sha": "3" * 40,
    }
    monkeypatch.setattr(
        validate_release_train,
        "urlopen",
        lambda *_args, **_kwargs: io.BytesIO(
            __import__("json").dumps(payload).encode("utf-8")
        ),
    )

    with pytest.raises(validate_release_train.ReleaseTrainError, match="workflow path"):
        validate_release_train._validate_actions_run(
            run_url="https://github.com/jry21223/HENU_Assistant/actions/runs/3",
            repository="jry21223/HENU_Assistant",
            token="token",
            expected_sha="3" * 40,
            expected_workflow="Test LangBot plugin",
            expected_workflow_path=".github/workflows/test-python.yaml",
        )


@pytest.mark.parametrize(
    ("evidence", "mcp_released", "agent_released"),
    (
        ("not-a-durable-url", "true", "true"),
        ("https://example.invalid/smoke", "false", "true"),
        ("https://example.invalid/smoke", "true", "false"),
    ),
)
def test_release_validator_fails_closed_without_smoke_and_prior_releases(
    evidence: str,
    mcp_released: str,
    agent_released: str,
) -> None:
    with pytest.raises(validate_release_train.ReleaseTrainError):
        validate_release_train.validate_release_train(
            root=ROOT,
            expected_version="2.1.0",
            github_ref="refs/tags/v2.1.0",
            mcp_sha="1" * 40,
            agent_sha="2" * 40,
            langbot_sha="3" * 40,
            smoke_evidence=evidence,
            repository="jry21223/HENU_Assistant",
            github_token="test-token",
            mcp_ci_run_url="https://github.com/jry21223/HENU_Assistant/actions/runs/1",
            agent_ci_run_url="https://github.com/jry21223/HENU_Assistant/actions/runs/2",
            langbot_ci_run_url="https://github.com/jry21223/HENU_Assistant/actions/runs/3",
            mcp_released=mcp_released,
            agent_released=agent_released,
            langbot_runtime_context_verified="true",
        )


def test_release_validator_requires_real_langbot_runtime_context_smoke() -> None:
    with pytest.raises(
        validate_release_train.ReleaseTrainError,
        match="LangBot runtime context",
    ):
        validate_release_train.validate_release_train(
            root=ROOT,
            expected_version="2.1.0",
            github_ref="refs/tags/v2.1.0",
            mcp_sha="1" * 40,
            agent_sha="2" * 40,
            langbot_sha="3" * 40,
            smoke_evidence="https://example.invalid/smoke",
            repository="jry21223/HENU_Assistant",
            github_token="test-token",
            mcp_ci_run_url="https://github.com/jry21223/HENU_Assistant/actions/runs/1",
            agent_ci_run_url="https://github.com/jry21223/HENU_Assistant/actions/runs/2",
            langbot_ci_run_url="https://github.com/jry21223/HENU_Assistant/actions/runs/3",
            mcp_released="true",
            agent_released="true",
            langbot_runtime_context_verified="false",
        )


@pytest.mark.parametrize(
    "url",
    (
        "https://user:token@example.invalid/smoke",
        "https://example.invalid/smoke?signature=secret",
        "https://example.invalid/smoke#token",
        "https://example.invalid/smoke evidence",
        "https://example.invalid/smoke\nnext-line",
    ),
)
def test_release_validator_rejects_smoke_urls_that_could_leak_tokens(url: str) -> None:
    with pytest.raises(validate_release_train.ReleaseTrainError):
        validate_release_train._validate_smoke_evidence(url)


@pytest.mark.parametrize(
    "url",
    (
        "https://github.com/other/repository/actions/runs/123",
        "https://github.com/jry21223/HENU_Assistant/actions/runs/123?token=secret",
        "https://example.com/jry21223/HENU_Assistant/actions/runs/123",
        "https://github.com/jry21223/HENU_Assistant/actions/runs/123 extra",
    ),
)
def test_release_validator_accepts_only_clean_same_repo_actions_urls(url: str) -> None:
    with pytest.raises(validate_release_train.ReleaseTrainError):
        validate_release_train._actions_run_id(url, "jry21223/HENU_Assistant")

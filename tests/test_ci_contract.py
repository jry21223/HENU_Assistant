from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def test_ci_runs_fresh_install_and_cli_smoke_on_supported_python_versions() -> None:
    assert WORKFLOW.exists(), "Agent Skill CI workflow is required"
    source = WORKFLOW.read_text(encoding="utf-8")

    for version in ("3.10", "3.11", "3.12", "3.13", "3.14"):
        assert f'"{version}"' in source

    for contract in (
        "permissions:\n  contents: read",
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
        "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97",
        "python-version: ${{ matrix.python-version }}",
        "scripts/select_lockfile.py --check",
        "python -m pip install --require-hashes",
        "python -m pip check",
        "pip-audit==2.10.1",
        "python -m pip_audit",
        "--disable-pip",
        "python -m compileall",
        "python -m pytest",
        "python henu_cli.py --help",
        '[sys.executable, "henu_cli.py", "system_status"]',
        'payload["success"] is True',
        'account["has_password"] is False',
        "PIP_CONFIG_FILE: /dev/null",
        "PIP_INDEX_URL: https://pypi.org/simple",
        'PIP_EXTRA_INDEX_URL: ""',
    ):
        assert contract in source

    assert "actions/checkout@v" not in source
    assert "actions/setup-python@v" not in source
    assert "python -m pip install -r requirements.txt" not in source


def test_each_supported_minor_has_a_hash_verified_mcp2_lock() -> None:
    for minor in range(10, 15):
        lockfile = ROOT / "requirements-lock" / f"py3{minor}.txt"
        source = lockfile.read_text(encoding="utf-8")

        assert f"pip-compile with Python 3.{minor}" in source
        assert "mcp==2.0.0" in source
        assert "--hash=sha256:" in source
        assert "--trusted-host" not in source
        assert "--index-url" not in source


def test_lockfile_selector_rejects_unsupported_python_and_selects_current_minor(
    tmp_path: Path,
) -> None:
    selector = ROOT / "scripts" / "select_lockfile.py"
    lock_dir = tmp_path / "requirements-lock"
    lock_dir.mkdir()
    expected = lock_dir / "py314.txt"
    expected.write_text("mcp==2.0.0 --hash=sha256:00\n", encoding="utf-8")

    selected = subprocess.run(
        [
            sys.executable,
            str(selector),
            "--root",
            str(tmp_path),
            "--python-version",
            "3.14",
            "--check",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    rejected = subprocess.run(
        [sys.executable, str(selector), "--python-version", "3.15"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert selected.returncode == 0, selected.stderr
    assert Path(selected.stdout.strip()) == expected
    assert rejected.returncode != 0
    assert "3.10 through 3.14" in rejected.stderr


def test_lock_documentation_covers_reproducible_posix_generation() -> None:
    source = (ROOT / "requirements-lock" / "README.md").read_text(encoding="utf-8")

    assert "pip-tools" in source
    assert "Windows is outside" in source


def test_install_documentation_uses_the_matching_frozen_lock() -> None:
    for document in (ROOT / "README.md", ROOT / "SKILL.md"):
        source = document.read_text(encoding="utf-8")

        assert "scripts/select_lockfile.py --check" in source
        assert "--require-hashes" in source
        assert "pip install -r requirements.txt" not in source
        assert "PIP_CONFIG_FILE=/dev/null" in source
        assert "https://pypi.org/simple" in source


def test_ci_has_no_publish_permissions_or_release_steps() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")

    for forbidden in (
        "contents: write",
        "packages: write",
        "id-token: write",
        "git push",
        "gh release",
        "twine upload",
    ):
        assert forbidden not in source

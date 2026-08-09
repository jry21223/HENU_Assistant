from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_lockfile_selector_uses_the_requested_python_minor(tmp_path: Path) -> None:
    selector = ROOT / "scripts" / "select_lockfile.py"

    selected = subprocess.run(
        [
            sys.executable,
            str(selector),
            "--root",
            str(tmp_path),
            "--python-version",
            "3.14",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert selected.returncode == 0, selected.stderr
    assert selected.stdout.strip() == str(tmp_path / "requirements-lock" / "py314.txt")

    missing = subprocess.run(
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

    assert missing.returncode == 1
    assert "missing frozen lockfile" in missing.stderr


def test_ci_is_read_only_and_runs_every_release_gate() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1" in workflow
    assert "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97" in workflow
    assert "actions/checkout@v" not in workflow
    assert "actions/setup-python@v" not in workflow
    assert 'python-version: ["3.10", "3.11", "3.12", "3.13", "3.14"]' in workflow
    assert "persist-credentials: false" in workflow
    assert "permissions:\n  contents: read" in workflow
    assert "scripts/select_lockfile.py --check" in workflow
    assert "--require-hashes" in workflow
    assert "python -m pip check" in workflow
    assert "pip-audit==2.10.1" in workflow
    assert "python -m pip_audit" in workflow
    assert "--disable-pip" in workflow
    assert "python -m compileall" in workflow
    assert "python -m pytest" in workflow
    assert "python diagnose_mcp.py" in workflow
    assert "python scripts/stdio_smoke.py" in workflow
    assert "PIP_CONFIG_FILE: /dev/null" in workflow
    assert "PIP_INDEX_URL: https://pypi.org/simple" in workflow
    assert 'PIP_EXTRA_INDEX_URL: ""' in workflow


def test_ci_runs_the_hash_verified_legacy_client() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    for contract in (
        "python -m pip install --require-hashes",
        "python -m pip check",
        "--disable-pip",
        "python -m compileall",
        "python -m pytest",
        "requirements-lock/mcp129-client-py311.txt",
        ".legacy-client/bin/python",
        "scripts/legacy_stdio_smoke.py",
        "--server-python",
        "--server-root",
    ):
        assert contract in workflow

    assert workflow.count("persist-credentials: false") >= 2


def test_install_uses_the_matching_hash_verified_lock() -> None:
    install = (ROOT / "install.sh").read_text(encoding="utf-8")

    assert "scripts/select_lockfile.py --check" in install
    assert "--require-hashes" in install
    assert "pip install -r requirements.txt" not in install
    assert "PIP_CONFIG_FILE=/dev/null" in install
    assert "https://pypi.org/simple" in install

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "scripts/select_lockfile.py --check" in readme
    assert "--require-hashes" in readme
    assert "pip install -r requirements.txt" not in readme


def test_every_supported_minor_lock_is_pip_compiled_and_pins_mcp2() -> None:
    for minor in range(10, 15):
        source = (ROOT / "requirements-lock" / f"py3{minor}.txt").read_text(encoding="utf-8")

        assert f"pip-compile with Python 3.{minor}" in source
        assert "mcp==2.0.0" in source
        assert "--hash=sha256:" in source
        assert "--trusted-host" not in source
        assert "--index-url" not in source


def test_legacy_client_lock_is_hash_verified_and_pins_mcp129() -> None:
    source = (ROOT / "requirements-lock" / "mcp129-client-py311.txt").read_text(
        encoding="utf-8"
    )

    assert "pip-compile with Python 3.11" in source
    assert "mcp==1.29.0" in source
    assert "--hash=sha256:" in source
    assert "mcp==2.0.0" not in source
    assert "--trusted-host" not in source
    assert "--index-url" not in source


def test_lock_documentation_covers_reproducible_posix_generation() -> None:
    source = (ROOT / "requirements-lock" / "README.md").read_text(encoding="utf-8")

    assert "pip-tools" in source
    assert "Windows is outside" in source
    assert "mcp129-client-py311.txt" in source

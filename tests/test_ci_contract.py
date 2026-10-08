from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "test-python.yaml"


def test_lockfile_selector_selects_the_requested_posix_minor(tmp_path: Path) -> None:
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
    assert Path(selected.stdout.strip()) == tmp_path / "requirements-lock" / "py314.txt"


def test_ci_runs_the_hash_verified_posix_matrix() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")

    assert "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1" in source
    assert "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97" in source
    assert "actions/checkout@v" not in source
    assert "actions/setup-python@v" not in source
    for contract in (
        "permissions:\n  contents: read",
        "runs-on: ubuntu-latest",
        'python-version: ["3.10", "3.11", "3.12", "3.13", "3.14"]',
        "persist-credentials: false",
        "scripts/select_lockfile.py --check",
        "python -m pip install --require-hashes",
        "python -m pip check",
        "--disable-pip",
        "python -m compileall",
        "python -m pytest",
    ):
        assert contract in source

    assert source.count("persist-credentials: false") >= 1
    assert "contents: write" not in source


def test_each_supported_minor_has_a_hash_verified_posix_lock() -> None:
    for minor in range(10, 15):
        posix = (ROOT / "requirements-lock" / f"py3{minor}.txt").read_text(
            encoding="utf-8"
        )
        assert f"pip-compile with Python 3.{minor}" in posix
        assert "mcp==2.0.0" in posix
        assert "langbot-plugin==0.5.0" in posix
        assert "lbp==" not in posix
        assert "--hash=sha256:" in posix
        assert "--trusted-host" not in posix
        assert "--index-url" not in posix


def test_lock_documentation_covers_reproducible_posix_generation() -> None:
    source = (ROOT / "requirements-lock" / "README.md").read_text(encoding="utf-8")

    assert "pip-tools" in source
    assert "Windows is outside" in source

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from scripts.cli_smoke import PARSER_CASES


ROOT = Path(__file__).resolve().parents[1]


def test_cli_smoke_covers_every_parser_command_and_safe_business_contract() -> None:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "cli_smoke.py")],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert len(PARSER_CASES) == 32
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "Agent CLI smoke passed: 32 parser commands"

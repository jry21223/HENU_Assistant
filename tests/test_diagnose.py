from __future__ import annotations

import shutil
import subprocess
import sys
import builtins
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_diagnose_runs_a_real_stdio_protocol_smoke() -> None:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "diagnose_mcp.py")],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=45,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "server/discover → tools/list → tools/call" in completed.stdout
    assert "protocol=2026-07-28" in completed.stdout
    assert "initialize → tools/list → tools/call" in completed.stdout
    assert "protocol=2025-11-25" in completed.stdout
    assert "32 个工具" in completed.stdout


def test_diagnose_returns_nonzero_when_required_files_are_missing(tmp_path: Path) -> None:
    script = tmp_path / "diagnose_mcp.py"
    shutil.copyfile(ROOT / "diagnose_mcp.py", script)

    completed = subprocess.run(
        [sys.executable, str(script)],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode != 0
    assert "缺少文件" in completed.stdout


@pytest.mark.parametrize(
    "module_name",
    ("requests", "mcp", "lxml", "Crypto.Cipher", "cryptography", "pandas", "openpyxl", "pytest"),
)
def test_dependency_check_fails_for_every_direct_runtime_module(
    module_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import diagnose_mcp

    real_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name == module_name:
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)
    assert diagnose_mcp.check_dependencies() is False


def test_stdio_diagnostics_use_isolated_data_and_disable_workers(tmp_path: Path) -> None:
    import diagnose_mcp

    parameters = diagnose_mcp._stdio_server_parameters(tmp_path)

    assert "--data-root" in parameters.args
    assert str(tmp_path) in parameters.args
    assert "--disable-background-workers" in parameters.args


@pytest.mark.parametrize(
    "failed_gate",
    ["check_dependencies", "check_files", "check_mcp_server", "check_stdio_protocol"],
)
def test_diagnose_main_returns_one_when_any_gate_fails(
    failed_gate: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import diagnose_mcp

    for gate in ("check_dependencies", "check_files", "check_mcp_server", "check_stdio_protocol"):
        monkeypatch.setattr(diagnose_mcp, gate, lambda gate=gate: gate != failed_gate)
    monkeypatch.setattr(diagnose_mcp, "generate_config", lambda: None)

    assert diagnose_mcp.main() == 1

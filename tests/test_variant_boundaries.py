from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVICE_MODULES = (
    ROOT / "henu_plugin" / "service.py",
    ROOT / "henu_plugin" / "hardened_service.py",
)


def _imports(path: Path) -> list[tuple[str, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend((alias.name, alias.asname or "") for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.extend(
                (f"{node.module}.{alias.name}", alias.asname or "")
                for alias in node.names
            )
    return imports


def test_langbot_services_depend_on_the_transport_neutral_api() -> None:
    for module_path in SERVICE_MODULES:
        imports = _imports(module_path)
        imported_names = {name for name, _alias in imports}

        assert "mcp_server" not in imported_names, module_path
        assert ("henu_mcp.api", "henu_api") in imports, module_path


def test_manifest_declares_the_2_1_0_release_version() -> None:
    manifest = (ROOT / "manifest.yaml").read_text(encoding="utf-8")
    match = re.search(r"^\s+version:\s*([^\s]+)\s*$", manifest, re.MULTILINE)

    assert match and match.group(1) == "2.1.0"


def test_runtime_requirements_pin_mcp_2_0_0() -> None:
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()

    assert "mcp==2.0.0" in requirements


def test_development_requirements_pin_lbp_0_1_2() -> None:
    runtime_requirements = (ROOT / "requirements-dev.txt").read_text(
        encoding="utf-8"
    ).splitlines()
    build_requirements = (ROOT / "requirements-build.txt").read_text(
        encoding="utf-8"
    ).splitlines()

    assert "langbot-plugin==0.5.0" in runtime_requirements
    assert not any(line.startswith("lbp") for line in runtime_requirements)
    assert build_requirements == ["lbp==0.1.2"]


def test_runtime_requirements_exclude_build_and_test_tools() -> None:
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    package_names = {
        line.split("=", 1)[0].split("<", 1)[0].split(">", 1)[0]
        for line in requirements
        if line and not line.startswith("#")
    }

    assert package_names.isdisjoint({"lbp", "pytest"})


def test_gitignore_uses_an_explicit_runtime_json_denylist() -> None:
    entries = {
        line.strip()
        for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert not {entry for entry in entries if "*" in entry and entry.endswith(".json")}
    assert {
        "henu_cookies.json",
        "henu_library_cookies.json",
        "henu_profile.json",
        "henu_cas_cookies.json",
        "henu_yunfz_token.json",
        "seminar_signin_tasks.json",
        "period_time_config.json",
        "period_time_calibration_state.json",
        "xiqueer_period_time_request.json",
    }.issubset(entries)


def test_install_script_uses_the_matching_hash_verified_lock() -> None:
    install_script = (ROOT / "install.sh").read_text(encoding="utf-8")

    assert "scripts/select_lockfile.py --check" in install_script
    assert "--require-hashes" in install_script
    assert "pip install -r requirements-dev.txt" not in install_script
    assert "PIP_CONFIG_FILE=/dev/null" in install_script
    assert "https://pypi.org/simple" in install_script
    assert ".lbp-build-venv" in install_script
    assert "requirements-lock/lbp-py313.txt" in install_script


def test_install_script_imports_the_declared_plugin_entrypoint() -> None:
    install_script = (ROOT / "install.sh").read_text(encoding="utf-8")

    assert "from main import HenuAssistantPlugin" in install_script


def test_release_workflow_builds_through_the_verified_wrapper() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release-lbp.yaml").read_text(
        encoding="utf-8"
    )

    assert "run: python scripts/build_plugin.py" in workflow
    assert "run: lbp build" not in workflow


def test_release_workflow_installs_the_python_313_frozen_lock() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release-lbp.yaml").read_text(
        encoding="utf-8"
    )

    assert "scripts/select_lockfile.py --check" in workflow
    assert "python -m pip install --require-hashes" in workflow
    assert "python -m pip install -r requirements-dev.txt" not in workflow
    assert "requirements-lock/lbp-py313.txt" in workflow
    assert ".lbp-build-venv/bin/python" in workflow


def test_release_workflow_grants_write_only_to_manual_protected_release_job() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release-lbp.yaml").read_text(
        encoding="utf-8"
    )

    assert "\npermissions:\n  actions: read\n  contents: read\n" in workflow
    release_job = workflow.split("\n  release:\n", 1)[1]
    assert "if: github.event_name == 'workflow_dispatch'" in release_job
    assert "environment: henu-production-release" in release_job
    assert "permissions:" in release_job
    assert "actions: read" in release_job
    assert "contents: write" in release_job


def test_python_compatibility_workflow_covers_3_10_through_3_14() -> None:
    workflow = (ROOT / ".github" / "workflows" / "test-python.yaml").read_text(
        encoding="utf-8"
    )

    assert 'python-version: ["3.10", "3.11", "3.12", "3.13", "3.14"]' in workflow
    assert "scripts/select_lockfile.py --check" in workflow
    assert "python -m pip install --require-hashes" in workflow
    assert "pip-audit==2.10.1" in workflow
    assert "python -m pip_audit" in workflow
    assert "--disable-pip" in workflow
    assert "python -m pytest -q" in workflow


def test_each_supported_minor_has_a_hash_verified_mcp2_lock() -> None:
    for minor in range(10, 15):
        source = (ROOT / "requirements-lock" / f"py3{minor}.txt").read_text(encoding="utf-8")

        assert f"pip-compile with Python 3.{minor}" in source
        assert "mcp==2.0.0" in source
        assert "--hash=sha256:" in source
        assert "--trusted-host" not in source
        assert "--index-url" not in source
        assert "langbot-plugin==0.5.0" in source
        assert "lbp==" not in source

    builder = (ROOT / "requirements-lock" / "lbp-py313.txt").read_text(
        encoding="utf-8"
    )
    assert "lbp==0.1.2" in builder
    assert "langbot-plugin==0.1.1b1" in builder
    assert "--hash=sha256:" in builder


def test_lockfile_selector_rejects_python_outside_the_supported_range(
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


def test_workflows_do_not_persist_checkout_credentials() -> None:
    for workflow_name in ("release-lbp.yaml", "test-python.yaml"):
        workflow = (
            ROOT / ".github" / "workflows" / workflow_name
        ).read_text(encoding="utf-8")

        assert "uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1" in workflow
        assert "uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97" in workflow
        assert "actions/checkout@v" not in workflow
        assert "actions/setup-python@v" not in workflow
        assert "persist-credentials: false" in workflow
        assert "PIP_CONFIG_FILE: /dev/null" in workflow
        assert "PIP_INDEX_URL: https://pypi.org/simple" in workflow
        assert 'PIP_EXTRA_INDEX_URL: ""' in workflow


def test_readme_documents_the_verified_2_1_0_build_flow() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "当前 **2.1.0**" in readme
    assert "scripts/select_lockfile.py --check" in readme
    assert ".venv/bin/python scripts/build_plugin.py" in readme
    assert "langbot-plugin==0.5.0" in readme
    assert "requirements-lock/lbp-py313.txt" in readme
    assert ".venv/bin/lbp build" not in readme

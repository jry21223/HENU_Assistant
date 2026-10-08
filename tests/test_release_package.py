from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from scripts import build_plugin
from scripts.build_plugin import PackageVerificationError, verify_package


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_RESOURCES = {
    "campus_core/config/building_seed.json": {
        "01": {
            "campus_name": "明伦校区",
            "buildings": {"0013": {"building_name": "十号楼", "classrooms": []}},
        }
    },
    "campus_core/config/library_locations.json": {
        "locations": {"明伦二层借书": "67"}
    },
}


def _write_package(
    path: Path,
    *,
    omit: str = "",
    malformed: str = "",
    empty: str = "",
    manifest_version: str = "2.1.0",
    manifest_text: str = "",
    main_source: str = (
        "from langbot_plugin.api.definition.plugin import BasePlugin\n"
        "class HenuAssistantPlugin(BasePlugin):\n"
        "    pass\n"
    ),
    extra_files: dict[str, str | bytes] | None = None,
) -> None:
    manifest = manifest_text or (
        "apiVersion: v1\n"
        "kind: Plugin\n"
        "metadata:\n"
        "  author: jry21223\n"
        "  name: henu_assistant\n"
        f"  version: {manifest_version}\n"
        "  icon: assets/icon.jpg\n"
        "  label:\n"
        "    en_US: HENU Assistant\n"
        "spec:\n"
        "  components:\n"
        "    EventListener:\n"
        "      fromDirs:\n"
        "        - path: components/event_listener/\n"
        "    Tool:\n"
        "      fromDirs:\n"
        "        - path: components/cli_tools/\n"
        "execution:\n"
        "  python:\n"
        "    path: main.py\n"
        "    attr: HenuAssistantPlugin\n"
    )
    package_files: dict[str, str | bytes] = {
        "manifest.yaml": manifest,
        "main.py": main_source,
        "assets/icon.jpg": b"fake-image",
        "components/cli_tools/henu_cli.yaml": (
            "apiVersion: v1\n"
            "kind: Tool\n"
            "metadata:\n"
            "  name: henu_cli\n"
            "  label:\n"
            "    en_US: HenuCLI\n"
            "spec: {}\n"
            "execution:\n"
            "  python:\n"
            "    path: henu_cli_safe.py\n"
            "    attr: HenuCliSafe\n"
        ),
        "components/cli_tools/henu_cli_safe.py": (
            "from langbot_plugin.api.definition.components.tool.tool import Tool\n"
            "class HenuCliSafe(Tool):\n"
            "    async def call(self, params, session, query_id):\n"
            "        del session, query_id\n"
            "        return {}\n"
        ),
        "components/event_listener/identity_capture.yaml": (
            "apiVersion: v1\n"
            "kind: EventListener\n"
            "metadata:\n"
            "  name: identity_capture\n"
            "  label:\n"
            "    en_US: IdentityCapture\n"
            "spec: {}\n"
            "execution:\n"
            "  python:\n"
            "    path: identity_capture_safe.py\n"
            "    attr: SafeIdentityCaptureListener\n"
        ),
        "components/event_listener/identity_capture_safe.py": (
            "from langbot_plugin.api.definition.components.common.event_listener "
            "import EventListener\n"
            "class SafeIdentityCaptureListener(EventListener):\n"
            "    pass\n"
        ),
        "campus_core/__init__.py": "",
        "campus_core/locations.py": (
            "import json\n"
            "from pathlib import Path\n"
            "def load_library_location_map():\n"
            "    data = json.loads((Path(__file__).parent / 'config' / "
            "'library_locations.json').read_text(encoding='utf-8'))\n"
            "    return data.get('locations', {})\n"
        ),
        "campus_core/resource_registry/__init__.py": "",
        "campus_core/resource_registry/seed.py": (
            "import json\n"
            "from pathlib import Path\n"
            "def preload_seed_if_needed(force=False):\n"
            "    del force\n"
            "    path = Path(__file__).parent.parent / 'config' / 'building_seed.json'\n"
            "    data = json.loads(path.read_text(encoding='utf-8'))\n"
            "    return {'loaded': True, 'synced_count': len(data)}\n"
        ),
    }
    for resource_path, payload in REQUIRED_RESOURCES.items():
        if resource_path == malformed:
            content = "{not-json"
        elif resource_path == empty:
            content = "{}"
        else:
            content = json.dumps(payload, ensure_ascii=False)
        package_files[resource_path] = content
    package_files.update(extra_files or {})
    if omit:
        package_files.pop(omit, None)

    with zipfile.ZipFile(path, "w") as archive:
        for package_path, content in package_files.items():
            archive.writestr(package_path, content)


@pytest.mark.parametrize("missing", ("manifest.yaml", "main.py"))
def test_verify_package_rejects_a_missing_entrypoint(
    tmp_path: Path,
    missing: str,
) -> None:
    artifact = tmp_path / "henu-assistant.lbpkg"
    _write_package(artifact, omit=missing)

    with pytest.raises(PackageVerificationError, match=f"必需入口.*{missing}"):
        verify_package(artifact)


def test_verify_package_rejects_a_manifest_version_drift(tmp_path: Path) -> None:
    artifact = tmp_path / "henu-assistant.lbpkg"
    _write_package(artifact, manifest_version="2.0.4")

    with pytest.raises(PackageVerificationError, match=r"版本.*2\.0\.4.*2\.1\.0"):
        verify_package(artifact)


def test_verify_package_rejects_a_legacy_langbot_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = tmp_path / "henu-assistant.lbpkg"
    _write_package(artifact)
    monkeypatch.setattr(
        build_plugin.importlib.metadata,
        "version",
        lambda _distribution: "0.1.1b1",
    )

    with pytest.raises(PackageVerificationError, match=r"langbot-plugin==0\.5\.0"):
        verify_package(artifact)


def test_verify_package_rejects_a_plugin_manifest_langbot_will_ignore(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "henu-assistant.lbpkg"
    _write_package(
        artifact,
        manifest_text=(
            "metadata:\n"
            "  author: jry21223\n"
            "  name: henu_assistant\n"
            "  version: 2.1.0\n"
            "  icon: assets/icon.jpg\n"
            "spec: {}\n"
            "execution:\n"
            "  python:\n"
            "    path: main.py\n"
            "    attr: HenuAssistantPlugin\n"
        ),
    )

    with pytest.raises(PackageVerificationError, match="kind.*Plugin"):
        verify_package(artifact)


def test_verify_package_rejects_a_broken_unpacked_entrypoint(tmp_path: Path) -> None:
    artifact = tmp_path / "henu-assistant.lbpkg"
    _write_package(artifact, main_source="def broken(:\n")

    with pytest.raises(PackageVerificationError, match="解包入口导入"):
        verify_package(artifact)


def test_verify_package_rejects_a_missing_manifest_entry_attribute(tmp_path: Path) -> None:
    artifact = tmp_path / "henu-assistant.lbpkg"
    _write_package(artifact, main_source="class WrongPlugin: pass\n")

    with pytest.raises(PackageVerificationError, match="entry attribute missing"):
        verify_package(artifact)


def test_verify_package_rejects_a_plugin_with_the_wrong_base_class(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "henu-assistant.lbpkg"
    _write_package(artifact, main_source="class HenuAssistantPlugin: pass\n")

    with pytest.raises(PackageVerificationError, match="plugin base mismatch"):
        verify_package(artifact)


@pytest.mark.parametrize(
    ("missing", "expected"),
    (
        ("assets/icon.jpg", "assets/icon.jpg"),
        ("components/cli_tools/henu_cli.yaml", "components/cli_tools"),
        (
            "components/event_listener/identity_capture_safe.py",
            "components/event_listener/identity_capture_safe.py",
        ),
    ),
)
def test_verify_package_rejects_missing_manifest_declared_assets(
    tmp_path: Path,
    missing: str,
    expected: str,
) -> None:
    artifact = tmp_path / "henu-assistant.lbpkg"
    _write_package(artifact, omit=missing)

    with pytest.raises(PackageVerificationError, match=expected):
        verify_package(artifact)


def test_verify_package_rejects_a_missing_component_attribute(tmp_path: Path) -> None:
    artifact = tmp_path / "henu-assistant.lbpkg"
    _write_package(
        artifact,
        extra_files={
            "components/cli_tools/henu_cli_safe.py": "class WrongTool: pass\n",
        },
    )

    with pytest.raises(PackageVerificationError, match="component attribute missing"):
        verify_package(artifact)


@pytest.mark.parametrize(
    ("component_source", "expected_manifest"),
    (
        ("class HenuCliSafe: pass\n", "henu_cli.yaml"),
        ("class SafeIdentityCaptureListener: pass\n", "identity_capture.yaml"),
    ),
)
def test_verify_package_rejects_a_component_with_the_wrong_base_class(
    tmp_path: Path,
    component_source: str,
    expected_manifest: str,
) -> None:
    artifact = tmp_path / "henu-assistant.lbpkg"
    component_path = (
        "components/cli_tools/henu_cli_safe.py"
        if expected_manifest == "henu_cli.yaml"
        else "components/event_listener/identity_capture_safe.py"
    )
    _write_package(artifact, extra_files={component_path: component_source})

    with pytest.raises(
        PackageVerificationError,
        match=rf"component base mismatch.*{expected_manifest}",
    ):
        verify_package(artifact)


def test_verify_package_requires_henu_cli_trusted_context_signature(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "henu-assistant.lbpkg"
    _write_package(
        artifact,
        extra_files={
            "components/cli_tools/henu_cli_safe.py": (
                "from langbot_plugin.api.definition.components.tool.tool import Tool\n"
                "class HenuCliSafe(Tool):\n"
                "    async def call(self, params):\n"
                "        return {}\n"
            ),
        },
    )

    with pytest.raises(
        PackageVerificationError,
        match="trusted context signature missing",
    ):
        verify_package(artifact)


@pytest.mark.parametrize(
    "call_source",
    (
        (
            "    async def call(self, params, session, query_id, /):\n"
            "        return {}\n"
        ),
        (
            "    def call(self, params, session, query_id):\n"
            "        return {}\n"
        ),
    ),
)
def test_verify_package_requires_the_real_runtime_call_contract(
    tmp_path: Path,
    call_source: str,
) -> None:
    artifact = tmp_path / "henu-assistant.lbpkg"
    _write_package(
        artifact,
        extra_files={
            "components/cli_tools/henu_cli_safe.py": (
                "from langbot_plugin.api.definition.components.tool.tool import Tool\n"
                "class HenuCliSafe(Tool):\n"
                f"{call_source}"
            ),
        },
    )

    with pytest.raises(
        PackageVerificationError,
        match="trusted context call contract",
    ):
        verify_package(artifact)


def test_verify_package_requires_both_product_component_kinds(tmp_path: Path) -> None:
    artifact = tmp_path / "henu-assistant.lbpkg"
    _write_package(
        artifact,
        manifest_text=(
            "apiVersion: v1\n"
            "kind: Plugin\n"
            "metadata:\n"
            "  author: jry21223\n"
            "  name: henu_assistant\n"
            "  version: 2.1.0\n"
            "  icon: assets/icon.jpg\n"
            "  label:\n"
            "    en_US: HENU Assistant\n"
            "spec:\n"
            "  components:\n"
            "    Tool:\n"
            "      fromDirs:\n"
            "        - path: components/cli_tools/\n"
            "execution:\n"
            "  python:\n"
            "    path: main.py\n"
            "    attr: HenuAssistantPlugin\n"
        ),
    )

    with pytest.raises(PackageVerificationError, match="EventListener"):
        verify_package(artifact)


@pytest.mark.parametrize(
    ("required_manifest", "replacement_manifest", "replacement_source", "expected"),
    (
        (
            "components/cli_tools/henu_cli.yaml",
            "components/cli_tools/unrelated.yaml",
            (
                "apiVersion: v1\n"
                "kind: Tool\n"
                "metadata:\n"
                "  name: unrelated_tool\n"
                "  label:\n"
                "    en_US: UnrelatedTool\n"
                "spec: {}\n"
                "execution:\n"
                "  python:\n"
                "    path: henu_cli_safe.py\n"
                "    attr: HenuCliSafe\n"
            ),
            "henu_cli.yaml",
        ),
        (
            "components/event_listener/identity_capture.yaml",
            "components/event_listener/unrelated.yaml",
            (
                "apiVersion: v1\n"
                "kind: EventListener\n"
                "metadata:\n"
                "  name: unrelated_listener\n"
                "  label:\n"
                "    en_US: UnrelatedListener\n"
                "spec: {}\n"
                "execution:\n"
                "  python:\n"
                "    path: identity_capture_safe.py\n"
                "    attr: SafeIdentityCaptureListener\n"
            ),
            "identity_capture.yaml",
        ),
    ),
)
def test_verify_package_requires_the_product_component_identities(
    tmp_path: Path,
    required_manifest: str,
    replacement_manifest: str,
    replacement_source: str,
    expected: str,
) -> None:
    artifact = tmp_path / "henu-assistant.lbpkg"
    _write_package(
        artifact,
        omit=required_manifest,
        extra_files={replacement_manifest: replacement_source},
    )

    with pytest.raises(PackageVerificationError, match=expected):
        verify_package(artifact)


def test_verify_package_rejects_a_component_kind_mismatch(tmp_path: Path) -> None:
    artifact = tmp_path / "henu-assistant.lbpkg"
    _write_package(
        artifact,
        extra_files={
            "components/event_listener/identity_capture.yaml": (
                "apiVersion: v1\n"
                "kind: Tool\n"
                "metadata:\n"
                "  name: identity_capture\n"
                "  label:\n"
                "    en_US: IdentityCapture\n"
                "spec: {}\n"
                "execution:\n"
                "  python:\n"
                "    path: identity_capture_safe.py\n"
                "    attr: SafeIdentityCaptureListener\n"
            ),
        },
    )

    with pytest.raises(PackageVerificationError, match="kind.*EventListener"):
        verify_package(artifact)


def test_verify_package_rejects_a_component_manifest_langbot_will_ignore(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "henu-assistant.lbpkg"
    _write_package(
        artifact,
        extra_files={
            "components/cli_tools/henu_cli.yaml": (
                "apiVersion: v1\n"
                "kind: Tool\n"
                "execution:\n"
                "  python:\n"
                "    path: henu_cli_safe.py\n"
                "    attr: HenuCliSafe\n"
            ),
        },
    )

    with pytest.raises(PackageVerificationError, match="metadata"):
        verify_package(artifact)


def test_verify_package_rejects_component_metadata_missing_a_label(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "henu-assistant.lbpkg"
    _write_package(
        artifact,
        extra_files={
            "components/cli_tools/henu_cli.yaml": (
                "apiVersion: v1\n"
                "kind: Tool\n"
                "metadata:\n"
                "  name: henu_cli\n"
                "spec: {}\n"
                "execution:\n"
                "  python:\n"
                "    path: henu_cli_safe.py\n"
                "    attr: HenuCliSafe\n"
            ),
        },
    )

    with pytest.raises(PackageVerificationError, match="metadata.label"):
        verify_package(artifact)


def test_verify_package_honors_the_default_component_discovery_depth(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "henu-assistant.lbpkg"
    _write_package(
        artifact,
        omit="components/cli_tools/henu_cli.yaml",
        extra_files={
            "components/cli_tools/nested/henu_cli.yaml": (
                "apiVersion: v1\n"
                "kind: Tool\n"
                "metadata:\n"
                "  name: henu_cli\n"
                "  label:\n"
                "    en_US: HenuCLI\n"
                "spec: {}\n"
                "execution:\n"
                "  python:\n"
                "    path: henu_cli_safe.py\n"
                "    attr: HenuCliSafe\n"
            ),
            "components/cli_tools/nested/henu_cli_safe.py": (
                "class HenuCliSafe: pass\n"
            ),
        },
    )

    with pytest.raises(PackageVerificationError, match="components/cli_tools"):
        verify_package(artifact)


def test_verify_package_matches_langbot_component_extension_case(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "henu-assistant.lbpkg"
    _write_package(
        artifact,
        omit="components/cli_tools/henu_cli.yaml",
        extra_files={
            "components/cli_tools/HENU_CLI.YAML": (
                "apiVersion: v1\n"
                "kind: Tool\n"
                "metadata:\n"
                "  name: henu_cli\n"
                "  label:\n"
                "    en_US: HenuCLI\n"
                "spec: {}\n"
                "execution:\n"
                "  python:\n"
                "    path: henu_cli_safe.py\n"
                "    attr: HenuCliSafe\n"
            ),
        },
    )

    with pytest.raises(PackageVerificationError, match="components/cli_tools"):
        verify_package(artifact)


def test_verify_package_rejects_an_empty_manifest_icon(tmp_path: Path) -> None:
    artifact = tmp_path / "henu-assistant.lbpkg"
    _write_package(artifact, extra_files={"assets/icon.jpg": b""})

    with pytest.raises(PackageVerificationError, match="图标.*为空"):
        verify_package(artifact)


def test_verify_package_rejects_an_unexpected_execution_path(tmp_path: Path) -> None:
    artifact = tmp_path / "henu-assistant.lbpkg"
    _write_package(
        artifact,
        manifest_text=(
            "metadata:\n"
            "  author: jry21223\n"
            "  name: henu_assistant\n"
            "  version: 2.1.0\n"
            "execution:\n"
            "  python:\n"
            "    path: other.py\n"
            "    attr: HenuAssistantPlugin\n"
        ),
    )

    with pytest.raises(PackageVerificationError, match=r"path.*main\.py"):
        verify_package(artifact)


def test_verify_package_rejects_path_traversal(tmp_path: Path) -> None:
    artifact = tmp_path / "henu-assistant.lbpkg"
    _write_package(artifact, extra_files={"../outside.py": "raise SystemExit\n"})

    with pytest.raises(PackageVerificationError, match="不安全路径"):
        verify_package(artifact)


@pytest.mark.parametrize(
    "alias_path",
    (
        "assets/./icon.jpg",
        "assets//icon.jpg",
        "ASSETS/icon.jpg",
        "main.py.",
        "main.py ",
        "CON.txt",
        "assets/icon?.jpg",
    ),
)
def test_verify_package_rejects_canonical_path_aliases(
    tmp_path: Path,
    alias_path: str,
) -> None:
    artifact = tmp_path / "henu-assistant.lbpkg"
    _write_package(artifact, extra_files={alias_path: b"shadow-icon"})

    with pytest.raises(PackageVerificationError, match="不安全路径"):
        verify_package(artifact)


def test_verify_package_rejects_unicode_normalization_collisions(tmp_path: Path) -> None:
    artifact = tmp_path / "henu-assistant.lbpkg"
    _write_package(
        artifact,
        extra_files={
            "docs/caf\u00e9.txt": "first",
            "docs/cafe\u0301.txt": "second",
        },
    )

    with pytest.raises(PackageVerificationError, match="不安全路径"):
        verify_package(artifact)


def test_verify_package_reads_version_only_from_metadata(tmp_path: Path) -> None:
    artifact = tmp_path / "henu-assistant.lbpkg"
    _write_package(
        artifact,
        manifest_text=(
            "metadata:\n"
            "  author: jry21223\n"
            "  name: henu_assistant\n"
            "spec:\n"
            "  version: 2.1.0\n"
            "execution:\n"
            "  python:\n"
            "    path: main.py\n"
            "    attr: HenuAssistantPlugin\n"
        ),
    )

    with pytest.raises(
        PackageVerificationError,
        match=r"metadata\.version",
    ):
        verify_package(artifact)


def test_verify_package_rejects_a_missing_required_config_resource(tmp_path: Path) -> None:
    artifact = tmp_path / "henu-assistant.lbpkg"
    missing = "campus_core/config/library_locations.json"
    _write_package(artifact, omit=missing)

    with pytest.raises(PackageVerificationError, match=missing):
        verify_package(artifact)


def test_verify_package_loads_each_required_config_resource(tmp_path: Path) -> None:
    artifact = tmp_path / "henu-assistant.lbpkg"
    malformed = "campus_core/config/building_seed.json"
    _write_package(artifact, malformed=malformed)

    with pytest.raises(PackageVerificationError, match=f"无法解析.*{malformed}"):
        verify_package(artifact)


def test_verify_package_rejects_an_empty_required_config_resource(tmp_path: Path) -> None:
    artifact = tmp_path / "henu-assistant.lbpkg"
    empty = "campus_core/config/library_locations.json"
    _write_package(artifact, empty=empty)

    with pytest.raises(PackageVerificationError, match=f"内容为空.*{empty}"):
        verify_package(artifact)


def test_verify_package_rejects_a_packaged_dotenv(tmp_path: Path) -> None:
    artifact = tmp_path / "henu-assistant.lbpkg"
    _write_package(artifact, extra_files={".env": "HENU_MASTER_KEY=secret"})

    with pytest.raises(PackageVerificationError, match=r"敏感文件.*\.env"):
        verify_package(artifact)


def test_verify_package_rejects_a_nested_dotenv(tmp_path: Path) -> None:
    artifact = tmp_path / "henu-assistant.lbpkg"
    sensitive_path = "config/.env"
    _write_package(artifact, extra_files={sensitive_path: "TOKEN=secret"})

    with pytest.raises(PackageVerificationError, match=r"敏感文件.*config/\.env"):
        verify_package(artifact)


@pytest.mark.parametrize(
    "dotenv_name",
    (
        ".env.local",
        ".env.production",
        ".ENV",
        ".Env.production",
        ".envrc",
        ".env-example",
        ".env_prod",
        ".environment",
    ),
)
def test_verify_package_rejects_dotenv_variants(
    tmp_path: Path,
    dotenv_name: str,
) -> None:
    artifact = tmp_path / "henu-assistant.lbpkg"
    _write_package(artifact, extra_files={dotenv_name: "TOKEN=secret"})

    with pytest.raises(PackageVerificationError, match=rf"敏感文件.*{dotenv_name}"):
        verify_package(artifact)


@pytest.mark.parametrize(
    "sensitive_path",
    (
        "henu_profile.json",
        "data/users/10001/profile.json",
        "leaked-user/profile.json",
        "leaked-user/xk_cookies.json",
        "leaked-user/library_cookies.json",
        "leaked-user/cas_cookies.json",
        "leaked-user/yunfz_token.json",
        "leaked-user/course_monitor_config.json",
        "leaked-user/course_monitor_state.json",
        "leaked-user/schedule_clean_latest.json",
        "leaked-user/schedule_clean_20260809_230000.json",
        "leaked-user/.profile.json.ABC123.tmp",
        "leaked-user/.xk_cookies.json.ABC123.tmp",
        "leaked-user/profile.json.bak",
        "leaked-user/Profile.json.bak",
        "leaked-user/profile.json~",
        "leaked-user/PROFILE.JSON",
        "leaked-user/XK_COOKIES.JSON",
        "leaked-user/xk_cookies.json~",
        "leaked-user/henu_cookies.json.old",
        "leaked-user/yunfz_token.json.backup",
        "leaked-user/profile.json.lock",
        "leaked-user/schedule_grid_20260809.html",
        "leaked-user/schedule_20260809.html",
        "leaked-user/schedule_preview_20260809.txt",
        "leaked-user/schedule_clean_latest.md",
        "leaked-user/schedule_clean_20260809.md",
        "leaked-user/home_20260809_230000.html",
        "leaked-user/set_main_info_20260809_230000.js",
        "leaked-user/course_selection_status_20260809_230000.json",
        "output/schedule_grid_20260809.xlsx",
        "logs/runtime-debug.txt",
        ".henu-runtime-state.lock",
    ),
)
def test_verify_package_rejects_runtime_json_state(
    tmp_path: Path,
    sensitive_path: str,
) -> None:
    artifact = tmp_path / "henu-assistant.lbpkg"
    _write_package(artifact, extra_files={sensitive_path: '{"password":"secret"}'})

    with pytest.raises(PackageVerificationError, match=f"敏感文件.*{sensitive_path}"):
        verify_package(artifact)


def test_verify_package_allows_a_non_sensitive_contract_fixture(tmp_path: Path) -> None:
    artifact = tmp_path / "henu-assistant.lbpkg"
    fixture_path = "tests/fixtures/mcp_1_29_tool_contract.json"
    _write_package(artifact, extra_files={fixture_path: '{"tools": []}'})

    verify_package(artifact)


def test_verify_package_allows_the_renamed_safe_environment_template(tmp_path: Path) -> None:
    artifact = tmp_path / "henu-assistant.lbpkg"
    _write_package(
        artifact,
        extra_files={"env.example": "HENU_MASTER_KEY=\nPLUGIN_DEBUG_KEY=\n"},
    )

    verify_package(artifact)


@pytest.mark.parametrize(
    "cache_path",
    (
        ".ruff_cache/cache.db",
        ".pytest_cache/v/cache/nodeids",
        ".lbp-build-venv/lib/python3.13/site-packages/secret.py",
        "henu_mcp/__pycache__/api.cpython-313.pyc",
    ),
)
def test_verify_package_rejects_build_and_bytecode_caches(
    tmp_path: Path,
    cache_path: str,
) -> None:
    artifact = tmp_path / "henu-assistant.lbpkg"
    _write_package(artifact, extra_files={cache_path: "cache"})

    with pytest.raises(PackageVerificationError, match=f"构建缓存.*{cache_path}"):
        verify_package(artifact)


def test_verify_package_rejects_a_bad_crc_in_a_non_required_entry(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "henu-assistant.lbpkg"
    sentinel = b"CRC_SENTINEL_PAYLOAD"
    _write_package(artifact, extra_files={"README.md": sentinel.decode("ascii")})
    corrupted = artifact.read_bytes().replace(
        sentinel,
        b"CRC_SENTINEL_PAYLOAE",
        1,
    )
    artifact.write_bytes(corrupted)

    assert zipfile.is_zipfile(artifact)
    with pytest.raises(PackageVerificationError, match=r"完整性.*README\.md"):
        verify_package(artifact)


def test_verify_only_cli_accepts_a_complete_package(tmp_path: Path) -> None:
    artifact = tmp_path / "henu-assistant.lbpkg"
    _write_package(artifact)

    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "build_plugin.py"),
            "--verify-only",
            str(artifact),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert f"已验证插件包: {artifact}" in completed.stdout


def test_build_cli_runs_lbp_then_verifies_the_versioned_artifact(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "plugin"
    project_root.mkdir()
    (project_root / "manifest.yaml").write_text(
        "metadata:\n"
        "  author: jry21223\n"
        "  name: henu_assistant\n"
        "  version: 2.1.0\n",
        encoding="utf-8",
    )
    source_artifact = tmp_path / "source.lbpkg"
    _write_package(source_artifact)

    fake_venv = tmp_path / "build-venv"
    fake_bin = fake_venv / "bin"
    fake_bin.mkdir(parents=True)
    fake_python = fake_bin / "python"
    fake_python.write_text("#!/bin/sh\nprintf '0.1.2\\n'\n", encoding="utf-8")
    fake_python.chmod(0o755)
    fake_lbp = fake_bin / "lbp"
    fake_lbp.write_text(
        "#!/bin/sh\n"
        'test "$1" = "build" || exit 64\n'
        "mkdir -p dist\n"
        'cp "$FAKE_LBPKG_SOURCE" '
        '"dist/jry21223-henu_assistant-2.1.0.lbpkg"\n',
        encoding="utf-8",
    )
    fake_lbp.chmod(0o755)
    env = os.environ.copy()
    env["FAKE_LBPKG_SOURCE"] = str(source_artifact)
    env[build_plugin.LBP_BUILD_VENV_ENV] = str(fake_venv)

    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "build_plugin.py"),
            "--project-root",
            str(project_root),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    expected = project_root / "dist" / "jry21223-henu_assistant-2.1.0.lbpkg"
    assert completed.returncode == 0, completed.stderr
    assert expected.is_file()
    assert f"已构建并验证插件包: {expected}" in completed.stdout


def test_build_uses_the_dedicated_lbp_environment_without_activation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "plugin"
    project_root.mkdir()
    (project_root / "manifest.yaml").write_text(
        "metadata:\n"
        "  author: jry21223\n"
        "  name: henu_assistant\n"
        "  version: 2.1.0\n",
        encoding="utf-8",
    )
    source_artifact = tmp_path / "source.lbpkg"
    _write_package(source_artifact)

    build_venv = tmp_path / "lbp-build-venv"
    venv_bin = build_venv / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").write_text("", encoding="utf-8")
    fake_lbp = venv_bin / "lbp"
    fake_lbp.write_text(
        "#!/bin/sh\n"
        "mkdir -p dist\n"
        'cp "$FAKE_LBPKG_SOURCE" '
        '"dist/jry21223-henu_assistant-2.1.0.lbpkg"\n',
        encoding="utf-8",
    )
    fake_lbp.chmod(0o755)
    monkeypatch.setenv(build_plugin.LBP_BUILD_VENV_ENV, str(build_venv))
    monkeypatch.setenv("FAKE_LBPKG_SOURCE", str(source_artifact))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setattr(
        build_plugin,
        "_builder_distribution_version",
        lambda _python: "0.1.2",
    )
    monkeypatch.setattr(build_plugin, "_extract_and_smoke_import", lambda *_args, **_kwargs: None)

    artifact = build_plugin.build_package(project_root)

    assert artifact == project_root / "dist" / "jry21223-henu_assistant-2.1.0.lbpkg"


def test_build_rejects_a_stale_preexisting_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "plugin"
    artifact = project_root / "dist" / "jry21223-henu_assistant-2.1.0.lbpkg"
    artifact.parent.mkdir(parents=True)
    (project_root / "manifest.yaml").write_text(
        "metadata:\n"
        "  author: jry21223\n"
        "  name: henu_assistant\n"
        "  version: 2.1.0\n",
        encoding="utf-8",
    )
    _write_package(artifact)

    fake_lbp = tmp_path / "lbp"
    fake_lbp.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_lbp.chmod(0o755)
    monkeypatch.setattr(build_plugin, "_find_lbp_executable", lambda: str(fake_lbp))

    with pytest.raises(PackageVerificationError, match="未生成新的预期产物"):
        build_plugin.build_package(project_root)


@pytest.mark.parametrize("link_is_directory", (False, True))
def test_build_rejects_source_symlinks_before_lbp_can_dereference_them(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    link_is_directory: bool,
) -> None:
    project_root = tmp_path / "plugin"
    project_root.mkdir()
    (project_root / "manifest.yaml").write_text(
        "metadata:\n"
        "  author: jry21223\n"
        "  name: henu_assistant\n"
        "  version: 2.1.0\n",
        encoding="utf-8",
    )
    external = tmp_path / ("external-dir" if link_is_directory else "secret.txt")
    if link_is_directory:
        external.mkdir()
        (external / "secret.txt").write_text("secret", encoding="utf-8")
    else:
        external.write_text("secret", encoding="utf-8")
    link = project_root / ("innocent-dir" if link_is_directory else "innocent.dat")
    try:
        link.symlink_to(external, target_is_directory=link_is_directory)
    except OSError as exc:
        pytest.skip(f"symlink unsupported: {exc}")
    monkeypatch.setattr(
        build_plugin,
        "_find_lbp_executable",
        lambda: pytest.fail("lbp must not run for symlinked source"),
    )

    with pytest.raises(PackageVerificationError, match="符号链接"):
        build_plugin.build_package(project_root)


def test_build_rejects_an_lbp_found_only_on_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path_lbp = tmp_path / "lbp"
    path_lbp.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path_lbp.chmod(0o755)
    monkeypatch.setenv(build_plugin.LBP_BUILD_VENV_ENV, str(tmp_path / "missing"))
    monkeypatch.setenv("PATH", str(tmp_path))

    with pytest.raises(PackageVerificationError, match="独立 lbp==0.1.2"):
        build_plugin._find_lbp_executable()


def test_build_rejects_the_wrong_lbp_distribution_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_venv = tmp_path / "venv"
    bin_dir = build_venv / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "python").write_text("", encoding="utf-8")
    (bin_dir / "lbp").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (bin_dir / "lbp").chmod(0o755)
    monkeypatch.setenv(build_plugin.LBP_BUILD_VENV_ENV, str(build_venv))
    monkeypatch.setattr(
        build_plugin,
        "_builder_distribution_version",
        lambda _python: "0.1.1",
    )

    with pytest.raises(PackageVerificationError, match=r"lbp==0\.1\.2"):
        build_plugin._find_lbp_executable()

#!/usr/bin/env python3
"""Build and verify the LangBot plugin release artifact."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import stat
import subprocess
import sys
import tempfile
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath

import yaml


SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from henu_mcp.version import __version__ as HENU_VERSION  # noqa: E402


REQUIRED_ENTRYPOINTS = ("manifest.yaml", "main.py")
REQUIRED_CONFIG_RESOURCES = (
    "campus_core/config/building_seed.json",
    "campus_core/config/library_locations.json",
)
REQUIRED_RUNTIME_MODULES = (
    "campus_core/locations.py",
    "campus_core/resource_registry/seed.py",
)
REQUIRED_PRODUCT_COMPONENTS = {
    "EventListener": (
        "components/event_listener",
        "components/event_listener/identity_capture.yaml",
        "identity_capture",
        "identity_capture_safe.py",
        "SafeIdentityCaptureListener",
    ),
    "Tool": (
        "components/cli_tools",
        "components/cli_tools/henu_cli.yaml",
        "henu_cli",
        "henu_cli_safe.py",
        "HenuCliSafe",
    ),
}
SENSITIVE_RUNTIME_JSON_NAMES = frozenset(
    {
        "cas_cookies.json",
        "course_monitor_config.json",
        "course_monitor_state.json",
        "henu_cookies.json",
        "henu_library_cookies.json",
        "henu_profile.json",
        "henu_cas_cookies.json",
        "henu_yunfz_token.json",
        "library_cookies.json",
        "profile.json",
        "schedule_clean_latest.json",
        "seminar_signin_tasks.json",
        "period_time_config.json",
        "period_time_calibration_state.json",
        "xk_cookies.json",
        "xiqueer_period_time_request.json",
        "yunfz_token.json",
    }
)
SENSITIVE_RUNTIME_JSON_DIRS = frozenset({"data", "output", "logs"})
SENSITIVE_RUNTIME_FILENAMES = frozenset({".henu-runtime-state.lock"})
BUILD_CACHE_DIRS = frozenset(
    {".git", ".lbp-build-venv", ".pytest_cache", ".ruff_cache", "__pycache__"}
)
SOURCE_SCAN_EXCLUDED_DIRS = BUILD_CACHE_DIRS | frozenset({".venv", "dist"})
WINDOWS_RESERVED_NAMES = frozenset(
    {"aux", "con", "nul", "prn"}
    | {f"com{index}" for index in range(1, 10)}
    | {f"lpt{index}" for index in range(1, 10)}
)
LBP_BUILD_VENV_ENV = "HENU_LBP_BUILD_VENV"
LANGBOT_RUNTIME_VERSION = "0.5.0"


class PackageVerificationError(RuntimeError):
    """The built plugin package is incomplete or unsafe to publish."""


def _require_modern_langbot_runtime() -> None:
    try:
        installed_version = importlib.metadata.version("langbot-plugin")
    except importlib.metadata.PackageNotFoundError as exc:
        raise PackageVerificationError(
            f"验证环境未安装 langbot-plugin=={LANGBOT_RUNTIME_VERSION}"
        ) from exc
    if installed_version != LANGBOT_RUNTIME_VERSION:
        raise PackageVerificationError(
            "验证必须在现代 LangBot runtime 中运行：要求 "
            f"langbot-plugin=={LANGBOT_RUNTIME_VERSION}，当前为 {installed_version}"
        )


def _builder_distribution_version(builder_python: Path) -> str:
    try:
        completed = subprocess.run(
            [
                str(builder_python),
                "-c",
                "import importlib.metadata; print(importlib.metadata.version('lbp'))",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PackageVerificationError(
            f"无法验证独立 lbp 构建环境: {builder_python}"
        ) from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise PackageVerificationError(
            f"独立构建环境无法读取 lbp 版本: {detail}"
        )
    return completed.stdout.strip()


def _find_lbp_executable() -> str:
    configured = os.environ.get(LBP_BUILD_VENV_ENV)
    build_venv = (
        Path(configured).expanduser()
        if configured
        else SOURCE_ROOT / ".lbp-build-venv"
    )
    candidates = (
        (build_venv / "bin" / "lbp", build_venv / "bin" / "python"),
        (
            build_venv / "Scripts" / "lbp.exe",
            build_venv / "Scripts" / "python.exe",
        ),
        (
            build_venv / "Scripts" / "lbp",
            build_venv / "Scripts" / "python.exe",
        ),
    )
    for executable, builder_python in candidates:
        if not executable.is_file() or not builder_python.is_file():
            continue
        installed_version = _builder_distribution_version(builder_python)
        if installed_version != "0.1.2":
            raise PackageVerificationError(
                f"构建要求独立环境 lbp==0.1.2，当前为 {installed_version}"
            )
        return str(executable)
    raise PackageVerificationError(
        "未找到独立 lbp==0.1.2 构建环境；请安装 requirements-lock/lbp-py313.txt "
        "到 .lbp-build-venv"
    )


def _manifest_identity(project_root: Path) -> tuple[str, str, str]:
    manifest_path = project_root / "manifest.yaml"
    try:
        text = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PackageVerificationError(f"无法读取 {manifest_path}: {exc}") from exc
    metadata = _parse_manifest_metadata(
        text,
        str(manifest_path),
        required_fields=("author", "name", "version"),
    )
    return metadata["author"], metadata["name"], metadata["version"]


def _parse_manifest_metadata(
    text: str,
    source: str,
    *,
    required_fields: tuple[str, ...],
) -> dict[str, str]:
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise PackageVerificationError(f"无法解析 {source}: {exc}") from exc
    if not isinstance(document, dict) or not isinstance(document.get("metadata"), dict):
        raise PackageVerificationError(f"{source} 缺少 metadata")
    raw_metadata = document["metadata"]
    metadata: dict[str, str] = {}
    for field in required_fields:
        value = raw_metadata.get(field)
        if not isinstance(value, (str, int, float)) or isinstance(value, bool):
            raise PackageVerificationError(f"{source} 缺少 metadata.{field}")
        normalized = str(value).strip()
        if not normalized:
            raise PackageVerificationError(f"{source} 缺少 metadata.{field}")
        metadata[field] = normalized
    return metadata


def _parse_manifest_document(text: str, source: str) -> dict[str, object]:
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise PackageVerificationError(f"无法解析 {source}: {exc}") from exc
    if not isinstance(document, dict):
        raise PackageVerificationError(f"{source} 顶层必须是对象")
    return document


def _entrypoint_from_manifest(text: str) -> tuple[str, str]:
    document = _parse_manifest_document(text, "插件包 manifest.yaml")
    execution = document.get("execution")
    python_entry = execution.get("python") if isinstance(execution, dict) else None
    if not isinstance(python_entry, dict):
        raise PackageVerificationError("插件包 manifest.yaml 缺少 execution.python")
    path = str(python_entry.get("path") or "").strip()
    attr = str(python_entry.get("attr") or "").strip()
    if path != "main.py":
        raise PackageVerificationError("插件包 manifest execution.python.path 必须为 main.py")
    if attr != "HenuAssistantPlugin":
        raise PackageVerificationError(
            "插件包 manifest execution.python.attr 必须为 HenuAssistantPlugin"
        )
    return path, attr


def _normalized_package_path(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise PackageVerificationError(f"{label} 必须是包内相对路径")
    normalized = value.strip().rstrip("/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or "\\" in normalized
        or path.is_absolute()
        or ".." in path.parts
        or (path.parts and ":" in path.parts[0])
    ):
        raise PackageVerificationError(f"{label} 必须是安全的包内相对路径")
    return path.as_posix()


def _python_entry_from_document(
    document: dict[str, object],
    source: str,
) -> tuple[str, str]:
    execution = document.get("execution")
    python_entry = execution.get("python") if isinstance(execution, dict) else None
    if not isinstance(python_entry, dict):
        raise PackageVerificationError(f"{source} 缺少 execution.python")
    path = _normalized_package_path(
        python_entry.get("path"),
        f"{source} execution.python.path",
    )
    attr = python_entry.get("attr")
    if not isinstance(attr, str) or not attr.strip().isidentifier():
        raise PackageVerificationError(
            f"{source} execution.python.attr 必须是有效的 Python 属性名"
        )
    if PurePosixPath(path).suffix != ".py":
        raise PackageVerificationError(
            f"{source} execution.python.path 必须指向 Python 文件"
        )
    return path, attr.strip()


def _manifest_component_contract(
    manifest_text: str,
    archive: zipfile.ZipFile,
    names: set[str],
) -> tuple[str, list[tuple[str, str]]]:
    document = _parse_manifest_document(manifest_text, "插件包 manifest.yaml")
    if document.get("apiVersion") != "v1" or document.get("kind") != "Plugin":
        raise PackageVerificationError(
            "插件包 manifest.yaml apiVersion/kind 必须为 v1/Plugin"
        )
    metadata = document.get("metadata")
    if not isinstance(metadata, dict):
        raise PackageVerificationError("插件包 manifest.yaml 缺少 metadata")
    label = metadata.get("label")
    if (
        not isinstance(label, dict)
        or not isinstance(label.get("en_US"), str)
        or not label["en_US"].strip()
    ):
        raise PackageVerificationError(
            "插件包 manifest.yaml 缺少 metadata.label.en_US"
        )
    icon_path = _normalized_package_path(
        metadata.get("icon"),
        "插件包 manifest metadata.icon",
    )
    if icon_path not in names:
        raise PackageVerificationError(f"插件包缺少 manifest 图标: {icon_path}")
    if archive.getinfo(icon_path).is_dir():
        raise PackageVerificationError(f"manifest 图标不是普通文件: {icon_path}")
    if archive.getinfo(icon_path).file_size <= 0:
        raise PackageVerificationError(f"manifest 图标内容为空: {icon_path}")

    spec = document.get("spec")
    components = spec.get("components") if isinstance(spec, dict) else None
    if not isinstance(components, dict) or not components:
        raise PackageVerificationError("插件包 manifest.yaml 缺少 spec.components")

    for component_kind, product_contract in REQUIRED_PRODUCT_COMPONENTS.items():
        required_directory = product_contract[0]
        component_config = components.get(component_kind)
        from_dirs = (
            component_config.get("fromDirs")
            if isinstance(component_config, dict)
            else None
        )
        if not isinstance(from_dirs, list):
            raise PackageVerificationError(
                f"插件包 manifest 缺少必需 component: {component_kind}"
            )
        declared_directories = {
            _normalized_package_path(
                item.get("path") if isinstance(item, dict) else None,
                f"manifest component {component_kind}.fromDirs.path",
            )
            for item in from_dirs
        }
        if required_directory not in declared_directories:
            raise PackageVerificationError(
                f"manifest component {component_kind} 必须声明 {required_directory}"
            )

    component_entries: list[tuple[str, str]] = []
    for component_kind, component_config in components.items():
        from_dirs = (
            component_config.get("fromDirs")
            if isinstance(component_config, dict)
            else None
        )
        if not isinstance(from_dirs, list) or not from_dirs:
            raise PackageVerificationError(
                f"插件包 manifest component {component_kind} 缺少 fromDirs"
            )
        for index, from_dir in enumerate(from_dirs):
            raw_path = from_dir.get("path") if isinstance(from_dir, dict) else None
            raw_max_depth = (
                from_dir.get("maxDepth", 1) if isinstance(from_dir, dict) else 1
            )
            if (
                not isinstance(raw_max_depth, int)
                or isinstance(raw_max_depth, bool)
                or raw_max_depth < 1
            ):
                raise PackageVerificationError(
                    f"manifest component {component_kind}.fromDirs[{index}].maxDepth "
                    "必须是正整数"
                )
            directory = _normalized_package_path(
                raw_path,
                f"manifest component {component_kind}.fromDirs[{index}].path",
            )
            prefix = f"{directory}/"
            yaml_paths = sorted(
                name
                for name in names
                if name.startswith(prefix)
                and name.endswith((".yaml", ".yml"))
                and len(PurePosixPath(name).relative_to(directory).parts)
                <= raw_max_depth
            )
            if not yaml_paths:
                raise PackageVerificationError(
                    f"插件包缺少 manifest component 配置: {directory}"
                )
            product_contract = REQUIRED_PRODUCT_COMPONENTS.get(component_kind)
            if product_contract is not None:
                required_manifest = product_contract[1]
                if required_manifest not in yaml_paths:
                    raise PackageVerificationError(
                        f"插件包缺少必需 product component: {required_manifest}"
                    )
            for yaml_path in yaml_paths:
                try:
                    component_text = archive.read(yaml_path).decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise PackageVerificationError(
                        f"无法解析 component 配置 {yaml_path}: {exc}"
                    ) from exc
                component_document = _parse_manifest_document(
                    component_text,
                    f"component 配置 {yaml_path}",
                )
                if not isinstance(component_document.get("metadata"), dict):
                    raise PackageVerificationError(
                        f"component 配置 {yaml_path} 缺少 metadata"
                    )
                component_metadata = component_document["metadata"]
                component_name = component_metadata.get("name")
                component_label = component_metadata.get("label")
                if not isinstance(component_name, str) or not component_name.strip():
                    raise PackageVerificationError(
                        f"component 配置 {yaml_path} 缺少 metadata.name"
                    )
                if (
                    not isinstance(component_label, dict)
                    or not isinstance(component_label.get("en_US"), str)
                    or not component_label["en_US"].strip()
                ):
                    raise PackageVerificationError(
                        f"component 配置 {yaml_path} 缺少 metadata.label.en_US"
                    )
                if "spec" not in component_document:
                    raise PackageVerificationError(
                        f"component 配置 {yaml_path} 缺少 spec"
                    )
                if not component_document.get("apiVersion"):
                    raise PackageVerificationError(
                        f"component 配置 {yaml_path} 缺少 apiVersion"
                    )
                if component_document.get("kind") != component_kind:
                    raise PackageVerificationError(
                        f"component 配置 {yaml_path} kind 必须为 {component_kind}"
                    )
                relative_path, _attr = _python_entry_from_document(
                    component_document,
                    f"component 配置 {yaml_path}",
                )
                if product_contract is not None and yaml_path == product_contract[1]:
                    (
                        _required_directory,
                        _required_manifest,
                        required_name,
                        required_python_path,
                        required_attr,
                    ) = product_contract
                    if component_name.strip() != required_name:
                        raise PackageVerificationError(
                            f"product component {yaml_path} metadata.name 必须为 "
                            f"{required_name}"
                        )
                    if relative_path != required_python_path or _attr != required_attr:
                        raise PackageVerificationError(
                            f"product component {yaml_path} Python 入口必须为 "
                            f"{required_python_path}:{required_attr}"
                        )
                module_path = PurePosixPath(
                    PurePosixPath(yaml_path).parent,
                    relative_path,
                ).as_posix()
                if module_path not in names:
                    raise PackageVerificationError(
                        f"插件包缺少 component Python 入口: {module_path}"
                    )
                if archive.getinfo(module_path).is_dir():
                    raise PackageVerificationError(
                        f"component Python 入口不是普通文件: {module_path}"
                    )
                component_entries.append((yaml_path, component_kind))
    return icon_path, component_entries


def _is_sensitive_runtime_basename(name: str) -> bool:
    name = unicodedata.normalize("NFC", name).casefold()
    if name in SENSITIVE_RUNTIME_JSON_NAMES:
        return True
    if any(
        name.startswith(prefix)
        for prefix in (
            "course_selection_status_",
            "home_",
            "schedule_grid_",
            "schedule_preview_",
            "schedule_clean_",
            "set_main_info_",
        )
    ):
        return True
    if name.startswith("schedule_") and PurePosixPath(name).suffix.lower() in {
        ".csv",
        ".htm",
        ".html",
        ".json",
        ".md",
        ".txt",
        ".xls",
        ".xlsx",
    }:
        return True
    if any(
        name.startswith(f"{sensitive_name}.")
        for sensitive_name in SENSITIVE_RUNTIME_JSON_NAMES
    ):
        return True
    if any(
        name == f"{sensitive_name}~"
        for sensitive_name in SENSITIVE_RUNTIME_JSON_NAMES
    ):
        return True
    if not name.startswith(".") or not name.endswith(".tmp"):
        return False
    atomic_body = name[1:-4]
    if "." not in atomic_body:
        return False
    destination = atomic_body.rsplit(".", 1)[0]
    return _is_sensitive_runtime_basename(destination)


def _validated_archive_entries(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    entries = archive.infolist()
    seen: set[str] = set()
    for entry in entries:
        name = entry.filename
        path = PurePosixPath(name)
        path_name = path.as_posix()
        canonical_name = path_name + ("/" if entry.is_dir() else "")
        collision_key = unicodedata.normalize("NFC", path_name).casefold()
        mode = entry.external_attr >> 16
        windows_unsafe = any(
            part.endswith((" ", "."))
            or any(ord(character) < 32 or character in '<>:"|?*' for character in part)
            or part.split(".", 1)[0].casefold() in WINDOWS_RESERVED_NAMES
            for part in path.parts
        )
        if (
            not name
            or name != canonical_name
            or collision_key in seen
            or "\\" in name
            or path.is_absolute()
            or ".." in path.parts
            or (path.parts and ":" in path.parts[0])
            or stat.S_ISLNK(mode)
            or windows_unsafe
        ):
            raise PackageVerificationError(f"插件包包含不安全路径或链接: {name}")
        seen.add(collision_key)
    return entries


def _extract_and_smoke_import(
    archive: zipfile.ZipFile,
    entries: list[zipfile.ZipInfo],
    *,
    entry_path: str,
    entry_attr: str,
    component_entries: list[tuple[str, str]],
) -> None:
    with tempfile.TemporaryDirectory(prefix="henu-lbp-verify-") as temporary:
        root = Path(temporary)
        for entry in entries:
            destination = root.joinpath(*PurePosixPath(entry.filename).parts)
            if entry.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(archive.read(entry))

        module_name = entry_path.removesuffix(".py").replace("/", ".")
        code = (
            "import importlib, inspect, json, pathlib, sys\n"
            f"root = pathlib.Path({str(root)!r})\n"
            "sys.path.insert(0, str(root))\n"
            f"module = importlib.import_module({module_name!r})\n"
            f"entry = getattr(module, {entry_attr!r}, None)\n"
            "assert entry is not None, 'manifest entry attribute missing'\n"
            "import yaml\n"
            "from langbot_plugin.api.definition.plugin import BasePlugin\n"
            "from langbot_plugin.api.definition.components.common.event_listener import EventListener\n"
            "from langbot_plugin.api.definition.components.manifest import ComponentManifest\n"
            "from langbot_plugin.api.definition.components.tool.tool import Tool\n"
            "assert isinstance(entry, type) and issubclass(entry, BasePlugin), 'plugin base mismatch'\n"
            "plugin_document = yaml.safe_load((root / 'manifest.yaml').read_text(encoding='utf-8'))\n"
            "assert ComponentManifest.is_component_manifest(plugin_document), 'invalid plugin manifest'\n"
            "plugin_manifest = ComponentManifest(owner='jry21223/henu_assistant', manifest=plugin_document, rel_path='manifest.yaml')\n"
            "assert plugin_manifest.kind == 'Plugin', 'plugin kind mismatch'\n"
            f"component_entries = {component_entries!r}\n"
            "for component_manifest_path, component_kind in component_entries:\n"
            "    component_document = yaml.safe_load((root / component_manifest_path).read_text(encoding='utf-8'))\n"
            "    assert ComponentManifest.is_component_manifest(component_document), 'invalid component manifest: ' + component_manifest_path\n"
            "    component = ComponentManifest(owner='jry21223/henu_assistant', manifest=component_document, rel_path=component_manifest_path)\n"
            "    assert component.kind == component_kind, 'component kind mismatch: ' + component_manifest_path\n"
            "    try:\n"
            "        component_entry = component.get_python_component_class()\n"
            "    except AttributeError as exc:\n"
            "        raise AssertionError('component attribute missing: ' + component_manifest_path) from exc\n"
            "    assert component_entry is not None, 'component attribute missing: ' + component_manifest_path\n"
            "    expected_base = {'EventListener': EventListener, 'Tool': Tool}[component_kind]\n"
            "    assert isinstance(component_entry, type) and issubclass(component_entry, expected_base), 'component base mismatch: ' + component_manifest_path\n"
            "    if component_manifest_path == 'components/cli_tools/henu_cli.yaml':\n"
            "        call_method = component_entry.call\n"
            "        call_params = inspect.signature(call_method).parameters\n"
            "        assert {'session', 'query_id'} <= set(call_params), 'henu_cli trusted context signature missing'\n"
            "        assert inspect.iscoroutinefunction(call_method), 'henu_cli trusted context call contract must be async'\n"
            "        try:\n"
            "            inspect.signature(call_method).bind(object(), {}, session=object(), query_id=1)\n"
            "        except TypeError as exc:\n"
            "            raise AssertionError('henu_cli trusted context call contract rejects keyword context') from exc\n"
            "from campus_core.locations import load_library_location_map\n"
            "from campus_core.resource_registry.seed import preload_seed_if_needed\n"
            "locations = load_library_location_map()\n"
            "seed = preload_seed_if_needed(force=True)\n"
            "assert locations, 'library location loader returned empty data'\n"
            "assert int(seed.get('synced_count') or 0) > 0, seed\n"
            "print(json.dumps({'locations': len(locations), 'seed': seed['synced_count']}))\n"
        )
        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)
        for key in tuple(environment):
            if key.startswith("HENU_"):
                environment.pop(key, None)
        try:
            completed = subprocess.run(
                [sys.executable, "-I", "-c", code],
                cwd=root,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired as exc:
            raise PackageVerificationError("解包入口导入或资源加载超时") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise PackageVerificationError(f"解包入口导入或资源加载失败: {detail}")


def _artifact_signature(path: Path) -> tuple[int, int, int, int, int] | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return (
        stat.st_dev,
        stat.st_ino,
        stat.st_size,
        stat.st_mtime_ns,
        stat.st_ctime_ns,
    )


def _reject_source_symlinks(project_root: Path) -> None:
    """Reject links before lbp can dereference them into ordinary ZIP files."""
    for current_root, directory_names, file_names in os.walk(
        project_root,
        topdown=True,
        followlinks=False,
    ):
        current = Path(current_root)
        retained_directories: list[str] = []
        for name in directory_names:
            normalized = unicodedata.normalize("NFC", name).casefold()
            if normalized in SOURCE_SCAN_EXCLUDED_DIRS:
                continue
            candidate = current / name
            if candidate.is_symlink():
                raise PackageVerificationError(
                    f"插件源码包含符号链接: {candidate.relative_to(project_root)}"
                )
            retained_directories.append(name)
        directory_names[:] = retained_directories
        for name in file_names:
            candidate = current / name
            if candidate.is_symlink():
                raise PackageVerificationError(
                    f"插件源码包含符号链接: {candidate.relative_to(project_root)}"
                )


def build_package(project_root: Path) -> Path:
    project_root = Path(project_root).resolve()
    _reject_source_symlinks(project_root)
    author, name, version = _manifest_identity(project_root)
    artifact = project_root / "dist" / f"{author}-{name}-{version}.lbpkg"
    previous_signature = _artifact_signature(artifact)
    try:
        subprocess.run([_find_lbp_executable(), "build"], cwd=project_root, check=True)
    except FileNotFoundError as exc:
        raise PackageVerificationError("未找到 lbp；请先安装构建依赖") from exc
    except subprocess.CalledProcessError as exc:
        raise PackageVerificationError(f"lbp build 失败，退出码 {exc.returncode}") from exc

    if not artifact.is_file():
        raise PackageVerificationError(f"lbp build 未生成预期产物: {artifact}")
    if previous_signature is not None and _artifact_signature(artifact) == previous_signature:
        raise PackageVerificationError(f"lbp build 未生成新的预期产物: {artifact}")
    verify_package(artifact)
    return artifact


def verify_package(artifact: Path) -> None:
    _require_modern_langbot_runtime()
    artifact = Path(artifact)
    if not zipfile.is_zipfile(artifact):
        raise PackageVerificationError(f"不是有效的 lbpkg/ZIP 文件: {artifact}")

    with zipfile.ZipFile(artifact) as archive:
        entries = _validated_archive_entries(archive)
        bad_entry = archive.testzip()
        if bad_entry is not None:
            raise PackageVerificationError(
                f"插件包 ZIP 完整性校验失败: {bad_entry}"
            )

        names = set(archive.namelist())
        missing_entrypoints = [
            path
            for path in (*REQUIRED_ENTRYPOINTS, *REQUIRED_RUNTIME_MODULES)
            if path not in names
        ]
        if missing_entrypoints:
            raise PackageVerificationError(
                "插件包缺少必需入口: " + ", ".join(missing_entrypoints)
            )

        missing = [path for path in REQUIRED_CONFIG_RESOURCES if path not in names]
        if missing:
            raise PackageVerificationError(
                "插件包缺少必需资源: " + ", ".join(missing)
            )

        dotenv_files = {
            name
            for name in names
            if unicodedata.normalize("NFC", PurePosixPath(name).name)
            .casefold()
            .startswith(".env")
        }
        runtime_artifacts = {
            name
            for name in names
            if _is_sensitive_runtime_basename(PurePosixPath(name).name)
            or any(
                unicodedata.normalize("NFC", part).casefold()
                in SENSITIVE_RUNTIME_JSON_DIRS
                for part in PurePosixPath(name).parts[:-1]
            )
        }
        runtime_state_files = {
            name
            for name in names
            if unicodedata.normalize("NFC", PurePosixPath(name).name).casefold()
            in SENSITIVE_RUNTIME_FILENAMES
        }
        sensitive = sorted(dotenv_files | runtime_artifacts | runtime_state_files)
        if sensitive:
            raise PackageVerificationError(
                "插件包包含敏感文件: " + ", ".join(sensitive)
            )

        build_caches = sorted(
            name
            for name in names
            if any(
                unicodedata.normalize("NFC", part).casefold() in BUILD_CACHE_DIRS
                for part in PurePosixPath(name).parts
            )
        )
        if build_caches:
            raise PackageVerificationError(
                "插件包包含构建缓存: " + ", ".join(build_caches)
            )

        try:
            manifest_text = archive.read("manifest.yaml").decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PackageVerificationError(f"无法解析插件包 manifest.yaml: {exc}") from exc
        packaged_version = _parse_manifest_metadata(
            manifest_text,
            "插件包 manifest.yaml",
            required_fields=("version",),
        )["version"]
        entry_path, entry_attr = _entrypoint_from_manifest(manifest_text)
        _icon_path, component_entries = _manifest_component_contract(
            manifest_text,
            archive,
            names,
        )
        if packaged_version != HENU_VERSION:
            raise PackageVerificationError(
                "插件包 manifest 版本 "
                f"{packaged_version} 与 henu_mcp.version {HENU_VERSION} 不一致"
            )

        for resource_path in REQUIRED_CONFIG_RESOURCES:
            try:
                payload = json.loads(archive.read(resource_path).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise PackageVerificationError(
                    f"无法解析必需资源 {resource_path}: {exc}"
                ) from exc
            if not payload:
                raise PackageVerificationError(
                    f"必需资源内容为空: {resource_path}"
                )

        _extract_and_smoke_import(
            archive,
            entries,
            entry_path=entry_path,
            entry_attr=entry_attr,
            component_entries=component_entries,
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="验证河大校园助手 LangBot 插件包",
    )
    parser.add_argument(
        "--verify-only",
        type=Path,
        metavar="LBPKG",
        help="只验证已有的 lbpkg，不执行构建",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=SOURCE_ROOT,
        help="插件源码根目录；默认取脚本上一级目录",
    )
    args = parser.parse_args()

    try:
        if args.verify_only is not None:
            verify_package(args.verify_only)
            message = f"已验证插件包: {args.verify_only}"
        else:
            artifact = build_package(args.project_root)
            message = f"已构建并验证插件包: {artifact}"
    except PackageVerificationError as exc:
        parser.exit(1, f"插件包验证失败: {exc}\n")

    print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

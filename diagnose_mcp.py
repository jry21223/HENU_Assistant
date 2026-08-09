#!/usr/bin/env python3
from __future__ import annotations

import importlib.metadata
import json
import sys
import tempfile
from pathlib import Path

import anyio


BASE_DIR = Path(__file__).resolve().parent
EXPECTED_MCP_VERSION = "2.0.0"
EXPECTED_SERVER_VERSION = "2.1.0"
EXPECTED_TOOL_COUNT = 32
EXPECTED_MODERN_PROTOCOL = "2026-07-28"
EXPECTED_LEGACY_PROTOCOL = "2025-11-25"


def check_dependencies() -> bool:
    print("🔍 检查依赖...")
    modules = {
        "requests": "requests",
        "mcp": "mcp",
        "lxml": "lxml",
        "pycryptodome": "Crypto.Cipher",
        "cryptography": "cryptography",
        "pandas": "pandas",
        "openpyxl": "openpyxl",
        "pytest": "pytest",
    }
    missing: list[str] = []
    for label, module_name in modules.items():
        try:
            __import__(module_name)
            print(f"  ✅ {label}")
        except ImportError:
            print(f"  ❌ {label}")
            missing.append(label)

    if missing:
        print(f"❌ 缺少依赖: {', '.join(missing)}\n")
        return False

    installed = importlib.metadata.version("mcp")
    if installed != EXPECTED_MCP_VERSION:
        print(f"  ❌ mcp 版本应为 {EXPECTED_MCP_VERSION}，实际为 {installed}\n")
        return False
    print(f"  ✅ mcp=={installed}\n")
    return True


def check_files() -> bool:
    print("🔍 检查文件...")
    required_files = (
        "mcp_server.py",
        "henu_mcp/api.py",
        "henu_mcp/adapters/mcp_v2.py",
        "henu_mcp/version.py",
        "henu_mcp/core/course_schedule.py",
        "henu_mcp/core/schedule_cleaner.py",
        "henu_mcp/core/kingo_auth.py",
        "campus_core/__init__.py",
        "campus_core/atomic_io.py",
        "campus_core/bot.py",
        "campus_core/config/library_locations.json",
        "campus_core/config/building_seed.json",
        "requirements.txt",
    )
    missing = [name for name in required_files if not (BASE_DIR / name).exists()]
    for name in required_files:
        print(f"  {'❌' if name in missing else '✅'} {name}")
    if missing:
        print(f"❌ 缺少文件: {', '.join(missing)}\n")
        return False
    for relative_path in (
        "campus_core/config/library_locations.json",
        "campus_core/config/building_seed.json",
    ):
        try:
            payload = json.loads((BASE_DIR / relative_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"❌ 必需资源无法解析: {relative_path}: {exc}\n")
            return False
        if not payload:
            print(f"❌ 必需资源为空: {relative_path}\n")
            return False
    print("✅ 所有必要文件存在\n")
    return True


def check_mcp_server() -> bool:
    print("🔍 检查 MCP 服务器导入...")
    try:
        sys.path.insert(0, str(BASE_DIR))
        import mcp_server
    except Exception as exc:
        print(f"  ❌ 导入失败: {exc}\n")
        return False

    if mcp_server.mcp.name != "henu-campus-unified":
        print(f"  ❌ 服务名异常: {mcp_server.mcp.name}\n")
        return False
    if mcp_server.mcp.version != EXPECTED_SERVER_VERSION:
        print(f"  ❌ 服务版本异常: {mcp_server.mcp.version}\n")
        return False
    print(f"  ✅ henu-campus-unified {mcp_server.mcp.version}\n")
    return True


async def _stdio_protocol_smoke() -> tuple[str, int, str]:
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    with tempfile.TemporaryDirectory(prefix="henu-diagnose-legacy-") as data_root:
        parameters = _stdio_server_parameters(Path(data_root))
        with anyio.fail_after(15):
            async with stdio_client(parameters) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    initialized = await session.initialize()
                    listed = await session.list_tools()
                    called = await session.call_tool("course_selection_submit", {})

    structured = called.structured_content if isinstance(called.structured_content, dict) else {}
    if initialized.server_info.name != "henu-campus-unified":
        raise RuntimeError(f"unexpected server name: {initialized.server_info.name}")
    if initialized.server_info.version != EXPECTED_SERVER_VERSION:
        raise RuntimeError(f"unexpected server version: {initialized.server_info.version}")
    if initialized.protocol_version != EXPECTED_LEGACY_PROTOCOL:
        raise RuntimeError(f"unexpected legacy protocol version: {initialized.protocol_version}")
    if len(listed.tools) != EXPECTED_TOOL_COUNT:
        raise RuntimeError(f"expected {EXPECTED_TOOL_COUNT} tools, got {len(listed.tools)}")
    if called.is_error or structured.get("code") != "not_implemented":
        raise RuntimeError("safe tools/call did not return the expected structured result")
    return initialized.protocol_version, len(listed.tools), str(structured["code"])


async def _stdio_modern_protocol_smoke() -> tuple[str, int, str]:
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    with tempfile.TemporaryDirectory(prefix="henu-diagnose-modern-") as data_root:
        parameters = _stdio_server_parameters(Path(data_root))
        with anyio.fail_after(15):
            async with stdio_client(parameters) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    discovered = await session.discover()
                    listed = await session.list_tools()
                    called = await session.call_tool("course_selection_submit", {})
                    negotiated_protocol = session.protocol_version
                    server_info = session.server_info

    structured = called.structured_content if isinstance(called.structured_content, dict) else {}
    if negotiated_protocol != EXPECTED_MODERN_PROTOCOL:
        raise RuntimeError(f"unexpected modern protocol version: {negotiated_protocol}")
    if negotiated_protocol not in discovered.supported_versions:
        raise RuntimeError(f"modern protocol missing from discovery: {discovered.supported_versions}")
    if server_info is None or server_info.name != "henu-campus-unified":
        raise RuntimeError(f"unexpected modern server info: {server_info}")
    if server_info.version != EXPECTED_SERVER_VERSION:
        raise RuntimeError(f"unexpected modern server version: {server_info.version}")
    if len(listed.tools) != EXPECTED_TOOL_COUNT:
        raise RuntimeError(f"expected {EXPECTED_TOOL_COUNT} tools, got {len(listed.tools)}")
    if called.is_error or structured.get("code") != "not_implemented":
        raise RuntimeError("modern safe tools/call did not return the expected structured result")
    return negotiated_protocol, len(listed.tools), str(structured["code"])


def _stdio_server_parameters(data_root: Path):
    from mcp import StdioServerParameters

    return StdioServerParameters(
        command=sys.executable,
        args=[
            str(BASE_DIR / "mcp_server.py"),
            "--transport",
            "stdio",
            "--data-root",
            str(data_root),
            "--disable-background-workers",
        ],
        cwd=str(BASE_DIR),
    )


def check_stdio_protocol() -> bool:
    print("🔍 执行真实 stdio MCP 协议冒烟...")
    try:
        modern_protocol, modern_tool_count, modern_result_code = anyio.run(_stdio_modern_protocol_smoke)
    except Exception as exc:
        print(f"  ❌ server/discover → tools/list → tools/call 失败: {exc}\n")
        return False
    print(
        "  ✅ server/discover → tools/list → tools/call "
        f"(protocol={modern_protocol}, {modern_tool_count} 个工具, result={modern_result_code})"
    )

    try:
        legacy_protocol, legacy_tool_count, legacy_result_code = anyio.run(_stdio_protocol_smoke)
    except Exception as exc:
        print(f"  ❌ initialize → tools/list → tools/call 失败: {exc}\n")
        return False
    print(
        "  ✅ initialize → tools/list → tools/call "
        f"(protocol={legacy_protocol}, {legacy_tool_count} 个工具, result={legacy_result_code})\n"
    )
    return True


def generate_config() -> None:
    config = {
        "mcpServers": {
            "henu-campus": {
                "command": sys.executable,
                "args": [str(BASE_DIR / "mcp_server.py"), "--transport", "stdio"],
            }
        }
    }
    print("📝 MCP 客户端配置示例:")
    print(json.dumps(config, indent=2, ensure_ascii=False))
    print()


def main() -> int:
    print("=" * 60)
    print("河大校园助手 MCP 服务器诊断工具")
    print("=" * 60)

    all_ok = check_dependencies()
    all_ok = check_files() and all_ok
    all_ok = check_mcp_server() and all_ok
    if all_ok:
        all_ok = check_stdio_protocol()

    generate_config()
    if all_ok:
        print("✅ 所有检查通过")
        return 0
    print("❌ 发现问题，请根据上面的提示修复")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

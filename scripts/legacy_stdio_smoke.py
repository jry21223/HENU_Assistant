#!/usr/bin/env python3
"""Exercise the MCP 2 server with an actual MCP 1.29 client runtime."""

from __future__ import annotations

import argparse
import importlib.metadata
import sys
import tempfile
from pathlib import Path

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


EXPECTED_CLIENT_VERSION = "1.29.0"
EXPECTED_PROTOCOL = "2025-11-25"
EXPECTED_SERVER_VERSION = "2.1.0"
EXPECTED_TOOL_COUNT = 32


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the MCP 1.29 legacy-client compatibility smoke",
    )
    parser.add_argument("--server-python", required=True)
    parser.add_argument("--server-root", type=Path, required=True)
    return parser


async def _run(server_python: str, server_root: Path) -> None:
    server_root = server_root.resolve()
    with tempfile.TemporaryDirectory(prefix="henu-legacy-client-") as data_root:
        parameters = StdioServerParameters(
            command=server_python,
            args=[
                str(server_root / "mcp_server.py"),
                "--transport",
                "stdio",
                "--data-root",
                data_root,
                "--disable-background-workers",
            ],
            cwd=str(server_root),
        )
        with anyio.fail_after(20):
            async with stdio_client(parameters) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    initialized = await session.initialize()
                    listed = await session.list_tools()
                    called = await session.call_tool("course_selection_submit", {})

    if initialized.protocolVersion != EXPECTED_PROTOCOL:
        raise RuntimeError(f"unexpected legacy protocol: {initialized.protocolVersion}")
    if initialized.serverInfo.name != "henu-campus-unified":
        raise RuntimeError(f"unexpected server name: {initialized.serverInfo.name}")
    if initialized.serverInfo.version != EXPECTED_SERVER_VERSION:
        raise RuntimeError(f"unexpected server version: {initialized.serverInfo.version}")
    if len(listed.tools) != EXPECTED_TOOL_COUNT:
        raise RuntimeError(f"expected {EXPECTED_TOOL_COUNT} tools, got {len(listed.tools)}")
    structured = called.structuredContent if isinstance(called.structuredContent, dict) else {}
    if called.isError or structured.get("code") != "not_implemented":
        raise RuntimeError("safe tools/call did not return the expected structured result")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    client_version = importlib.metadata.version("mcp")
    if client_version != EXPECTED_CLIENT_VERSION:
        print(
            f"legacy smoke requires mcp=={EXPECTED_CLIENT_VERSION}, got {client_version}",
            file=sys.stderr,
        )
        return 1
    try:
        anyio.run(_run, args.server_python, args.server_root)
    except Exception as exc:
        print(f"legacy stdio smoke failed: {exc}", file=sys.stderr)
        return 1
    print(
        "legacy stdio smoke passed: "
        f"client={client_version}, protocol={EXPECTED_PROTOCOL}, "
        f"server={EXPECTED_SERVER_VERSION}, tools={EXPECTED_TOOL_COUNT}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

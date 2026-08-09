from __future__ import annotations

import socket
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import anyio
import pytest
from mcp import Client, ClientSession
from mcp.client.sse import sse_client


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_RESULT = {
    "success": False,
    "code": "not_implemented",
    "msg": "选课提交端点需要在选课开放后通过真实请求确认，当前版本不执行真实提交。",
}


def _unused_loopback_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@contextmanager
def _running_server(arguments: list[str]):
    port = _unused_loopback_port()
    with tempfile.TemporaryDirectory(prefix="henu-http-e2e-") as data_root:
        process = subprocess.Popen(
            [
                sys.executable,
                str(ROOT / "mcp_server.py"),
                *arguments,
                "--port",
                str(port),
                "--data-root",
                data_root,
                "--disable-background-workers",
            ],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            deadline = time.monotonic() + 10
            while True:
                if process.poll() is not None:
                    stdout, stderr = process.communicate()
                    raise RuntimeError(f"server exited {process.returncode}: {stdout}{stderr}")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                        break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("server did not bind its loopback port")
                    time.sleep(0.05)
            yield port
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


@pytest.mark.parametrize(
    ("stateless", "json_response", "path"),
    [
        (False, False, "/stateful-stream"),
        (False, True, "/stateful-json"),
        (True, False, "/stateless-stream"),
        (True, True, "/stateless-json"),
    ],
)
def test_streamable_http_loopback_matrix(
    stateless: bool,
    json_response: bool,
    path: str,
) -> None:
    arguments = [
        "--transport",
        "streamable-http",
        "--host",
        "127.0.0.1",
        "--path",
        path,
    ]
    if stateless:
        arguments.append("--stateless-http")
    if json_response:
        arguments.append("--json-response")

    with _running_server(arguments) as port:
        async def smoke() -> None:
            with anyio.fail_after(10):
                async with Client(f"http://127.0.0.1:{port}{path}", mode="auto") as client:
                    listed = await client.list_tools()
                    called = await client.call_tool("course_selection_submit", {})
                    assert client.protocol_version == "2026-07-28"
                    assert len(listed.tools) == 32
                    assert called.structured_content == EXPECTED_RESULT

        anyio.run(smoke)


def test_sse_loopback_custom_event_and_message_paths() -> None:
    event_path = "/campus-events"
    message_path = "/campus-messages/"
    with _running_server(
        [
            "--transport",
            "sse",
            "--host",
            "127.0.0.1",
            "--sse-path",
            event_path,
            "--message-path",
            message_path,
        ]
    ) as port:
        async def smoke() -> None:
            with anyio.fail_after(10):
                async with sse_client(f"http://127.0.0.1:{port}{event_path}") as (read, write):
                    async with ClientSession(read, write) as session:
                        discovered = await session.discover()
                        listed = await session.list_tools()
                        called = await session.call_tool("course_selection_submit", {})
                        assert session.protocol_version == "2026-07-28"
                        assert discovered.supported_versions == ["2026-07-28"]
                        assert len(listed.tools) == 32
                        assert called.structured_content == EXPECTED_RESULT

        anyio.run(smoke)


def test_http_cli_fails_closed_before_binding_a_non_loopback_host() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "mcp_server.py"),
            "--transport",
            "streamable-http",
            "--host",
            "0.0.0.0",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode != 0
    assert "restricted to loopback hosts" in completed.stderr

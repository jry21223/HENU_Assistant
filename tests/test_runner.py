from __future__ import annotations

import pytest


class RecordingServer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def run(self, transport: str, **kwargs: object) -> None:
        self.calls.append((transport, kwargs))


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_runner_allows_loopback_http_bindings(host: str) -> None:
    from mcp_server import RunnerConfig, run_server

    server = RecordingServer()
    config = RunnerConfig(
        transport="streamable-http",
        host=host,
        port=8123,
        path="/campus-mcp",
        stateless_http=True,
        json_response=True,
    )

    run_server(server, config)

    assert server.calls == [
        (
            "streamable-http",
            {
                "host": host,
                "port": 8123,
                "streamable_http_path": "/campus-mcp",
                "stateless_http": True,
                "json_response": True,
            },
        )
    ]


def test_runner_rejects_unauthenticated_non_loopback_binding() -> None:
    from mcp_server import RunnerConfig, run_server

    with pytest.raises(ValueError, match="loopback"):
        run_server(
            RecordingServer(),
            RunnerConfig(transport="streamable-http", host="0.0.0.0"),
        )


def test_runner_passes_only_sse_options_to_sse_transport() -> None:
    from mcp_server import RunnerConfig, run_server

    server = RecordingServer()
    run_server(
        server,
        RunnerConfig(
            transport="sse",
            host="::1",
            port=8124,
            sse_path="/campus-events",
            message_path="/campus-messages/",
        ),
    )

    assert server.calls == [
        (
            "sse",
            {
                "host": "::1",
                "port": 8124,
                "sse_path": "/campus-events",
                "message_path": "/campus-messages/",
            },
        )
    ]


def test_parser_exposes_distinct_sse_paths() -> None:
    from mcp_server import build_parser

    namespace = build_parser().parse_args(
        [
            "--transport",
            "sse",
            "--sse-path",
            "/events",
            "--message-path",
            "/messages/",
        ]
    )

    assert namespace.sse_path == "/events"
    assert namespace.message_path == "/messages/"


def test_parser_exposes_isolated_data_and_background_worker_controls(tmp_path) -> None:
    from mcp_server import build_parser

    namespace = build_parser().parse_args(
        ["--data-root", str(tmp_path), "--disable-background-workers"]
    )

    assert namespace.data_root == tmp_path
    assert namespace.disable_background_workers is True


def test_runner_keeps_stdio_free_of_http_options() -> None:
    from mcp_server import RunnerConfig, run_server

    server = RecordingServer()
    run_server(server, RunnerConfig(transport="stdio"))

    assert server.calls == [("stdio", {})]

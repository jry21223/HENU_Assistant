from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

import anyio


def _normalize_schema(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _normalize_schema(item)
            for key, item in value.items()
            if key != "title"
        }
    if isinstance(value, list):
        return [_normalize_schema(item) for item in value]
    return value


def _normalize_tool_contract(tools: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "name": tool.name,
            "description": inspect.cleandoc(tool.description or ""),
            "input_schema": _normalize_schema(tool.input_schema),
            "output_schema": _normalize_schema(tool.output_schema),
        }
        for tool in tools
    ]


def test_mcp_v2_contract_matches_the_normalized_mcp_1_29_baseline() -> None:
    from henu_mcp.adapters.mcp_v2 import create_mcp_server

    snapshot_path = Path(__file__).parent / "fixtures" / "mcp_1_29_tool_contract.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    tools = anyio.run(create_mcp_server().list_tools)

    assert snapshot["generated_from"] == {
        "git_sha": "20431d6b1c0f4dfff0186ea50a9db89e2e350ade",
        "mcp_sdk": "1.29.0",
    }
    assert _normalize_tool_contract(tools) == snapshot["tools"]

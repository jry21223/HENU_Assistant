from __future__ import annotations

import asyncio
import importlib.metadata
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml

from components.cli_tools.henu_cli_safe import HenuCliSafe
from henu_plugin.storage_adapter import PluginStorageAdapter
from langbot_plugin.api.definition.components.manifest import ComponentManifest
from langbot_plugin.api.definition.plugin import NonePlugin
from langbot_plugin.cli.run.handler import PluginRuntimeHandler
from langbot_plugin.entities.io.actions.enums import RuntimeToPluginAction
from langbot_plugin.runtime.plugin.container import (
    ComponentContainer,
    PluginContainer,
    RuntimeContainerStatus,
)


ROOT = Path(__file__).resolve().parents[1]


class _RecordingHenuCli(HenuCliSafe):
    received: tuple[dict[str, Any], Any, int] | None = None

    async def call(self, params, session, query_id):
        self.received = (params, session, query_id)
        return {
            "success": True,
            "sender_id": str(session.sender_id),
            "query_id": query_id,
        }


def _manifest(path: Path, owner: str) -> ComponentManifest:
    return ComponentManifest(
        owner=owner,
        manifest=yaml.safe_load(path.read_text(encoding="utf-8")),
        rel_path=path.relative_to(ROOT).as_posix(),
    )


def _container_for_tool(tool: HenuCliSafe) -> PluginContainer:
    owner = "jry21223/henu_assistant"
    return PluginContainer(
        manifest=_manifest(ROOT / "manifest.yaml", owner),
        plugin_instance=NonePlugin(),
        enabled=True,
        priority=0,
        plugin_config={},
        status=RuntimeContainerStatus.INITIALIZED,
        components=[
            ComponentContainer(
                manifest=_manifest(
                    ROOT / "components" / "cli_tools" / "henu_cli.yaml",
                    owner,
                ),
                component_instance=tool,
                component_config={},
            )
        ],
    )


async def _call_through_runtime(tool: HenuCliSafe, *, sender_id: str = "10001"):
    async def initialize(_settings):
        return None

    handler = PluginRuntimeHandler(object(), initialize)
    handler.plugin_container = _container_for_tool(tool)
    return await handler.actions[RuntimeToPluginAction.CALL_TOOL.value](
        {
            "tool_name": "henu_cli",
            "tool_parameters": {"command": "system status"},
            "session": {
                "launcher_type": "person",
                "launcher_id": sender_id,
                "sender_id": sender_id,
            },
            "query_id": 73,
            "query_uuid": "query-73",
        }
    )


def test_pinned_runtime_handler_injects_trusted_tool_context(
    tmp_path: Path,
    monkeypatch,
) -> None:
    assert importlib.metadata.version("langbot-plugin") == "0.5.0"
    monkeypatch.chdir(tmp_path)
    tool = _RecordingHenuCli()

    async def scenario() -> None:
        response = await _call_through_runtime(tool)

        assert response.code == 0
        assert response.data["tool_response"] == {
            "success": True,
            "sender_id": "10001",
            "query_id": 73,
        }
        assert tool.received is not None
        params, session, query_id = tool.received
        assert params == {"command": "system status"}
        assert session.sender_id == "10001"
        assert query_id == 73

    asyncio.run(scenario())


def test_runtime_identity_mismatch_fails_before_storage_access(
    tmp_path: Path,
    monkeypatch,
) -> None:
    assert importlib.metadata.version("langbot-plugin") == "0.5.0"
    monkeypatch.chdir(tmp_path)
    tool = HenuCliSafe()
    tool.plugin = SimpleNamespace(service=object())

    async def mismatched_hint(_query_id):
        return {
            "launcher_type": "person",
            "launcher_id": "20002",
            "sender_id": "20002",
        }

    async def forbidden_storage_read(_adapter):
        raise AssertionError("identity mismatch reached Storage")

    monkeypatch.setattr(tool, "_load_identity_hint", mismatched_hint)
    monkeypatch.setattr(PluginStorageAdapter, "load_all", forbidden_storage_read)

    response = asyncio.run(_call_through_runtime(tool, sender_id="10001"))

    assert response.code == 0
    result = response.data["tool_response"]
    assert result["success"] is False
    assert result["error_code"] == "identity_missing"
    assert "身份" in result["msg"] and "不一致" in result["msg"]

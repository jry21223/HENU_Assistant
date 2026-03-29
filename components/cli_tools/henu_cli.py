from __future__ import annotations

from components.tools._base import BaseHenuTool
from henu_plugin.cli import inspect_cli_command


class HenuCli(BaseHenuTool):
    tool_name = "henu_cli"

    def should_preload_runtime_context(self, params):
        command = params.get("command") if isinstance(params, dict) else ""
        spec = inspect_cli_command(command)
        return spec.should_preload_runtime_context

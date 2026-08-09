#!/usr/bin/env python3
"""Run both modern and legacy MCP negotiation against a real stdio child."""

from __future__ import annotations

import sys
from pathlib import Path

import anyio


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from diagnose_mcp import _stdio_modern_protocol_smoke, _stdio_protocol_smoke  # noqa: E402


def main() -> int:
    try:
        modern = anyio.run(_stdio_modern_protocol_smoke)
        print(f"modern server/discover: protocol={modern[0]}, tools={modern[1]}, result={modern[2]}")
        legacy = anyio.run(_stdio_protocol_smoke)
        print(f"legacy initialize: protocol={legacy[0]}, tools={legacy[1]}, result={legacy[2]}")
    except Exception as exc:
        print(f"stdio smoke failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

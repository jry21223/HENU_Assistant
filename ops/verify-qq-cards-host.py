#!/usr/bin/env python3
"""Read-only host integration check. No credentials, browser or QQ requests."""
import ast
import hashlib
from pathlib import Path
import sys


def verify(path: Path):
    source = path.read_text()
    tree = ast.parse(source)
    methods = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef)}
    for name, kind in (("send_private_text_msg", "c2c"), ("send_group_text_msg", "group")):
        method = methods[name]
        calls = [node for node in ast.walk(method) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "send_card"]
        assert len(calls) == 1, f"{name}: expected exactly one HENU transport hook"
        assert calls[0].args[1].value == kind
        assert {keyword.arg for keyword in calls[0].keywords} == {"msg_id", "event_id", "msg_seq"}
        assert any(isinstance(node, ast.ImportFrom) and node.module == "langbot.pkg.platform.sources.henu_qq_cards" for node in ast.walk(method))
    for name in ("send_channle_group_text_msg", "send_channle_private_text_msg"):
        assert not any(isinstance(node, ast.Name) and node.id == "send_card" for node in ast.walk(methods[name])), "channel must retain its text path"
    print("PASS: C2C/group hooks preserve source and sequence; channels remain text")
    print("Reviewed host API SHA256:", hashlib.sha256(source.encode()).hexdigest())


if __name__ == "__main__":
    verify(Path(sys.argv[1]))

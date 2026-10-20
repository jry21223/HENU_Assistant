"""Checks the eval verdict at its model HTTP boundary; no live model or school."""
from types import SimpleNamespace
import json

from ops.evaluate_qq_agent import CASES, evaluate


def test_read_only_case_rejects_an_extra_reservation_preview(monkeypatch):
    case = next(c for c in CASES if c["id"] == "complete_request")
    commands = [
        "library locations",
        "library seats --area-id 30 --date 2026-10-21 --time 14:00 --end-time 16:00",
        "library reserve --location '701 七层南自习室' --seat-no 002 --date 2026-10-21 --time 14:00 --end-time 16:00",
    ]
    responses = iter([
        {"role": "assistant", "content": "", "tool_calls": [{"id": str(i), "type": "function", "function": {"name": "henu_cli", "arguments": json.dumps({"command": cmd})}} for i, cmd in enumerate(commands)]},
        {"role": "assistant", "content": "002、006号空闲，只查询，不预约。"},
    ])
    def post(*args, **kwargs):
        msg = next(responses)
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"choices": [{"message": msg}]})
    monkeypatch.setenv("AGENT_EVAL_API_KEY", "synthetic-key")
    monkeypatch.setattr("ops.evaluate_qq_agent.requests.post", post)
    report = evaluate(case, SimpleNamespace(base_url="https://synthetic.invalid", model="synthetic"), [], "synthetic")
    assert not report["passed"]
    assert "preview_without_request" in report["turns"][0]["problems"]


def test_campus_check_rejects_foreign_counts_but_allows_explicit_exclusion(monkeypatch):
    case = next(c for c in CASES if c["id"] == "two_tasks")
    monkeypatch.setenv("AGENT_EVAL_API_KEY", "synthetic-key")
    for ending, expected in [("明伦校区有5个空座。", False), ("明伦401第一自习室未列出。", True)]:
        responses = iter([
            {"role": "assistant", "content": "", "tool_calls": [{"id": str(i), "type": "function", "function": {"name": "henu_cli", "arguments": json.dumps({"command": cmd})}} for i, cmd in enumerate(["schedule day --date 2026-10-21", "library locations"])]},
            {"role": "assistant", "content": "明天无课。金明南自习室12座、北自习室8座；" + ending},
        ])
        def post(*args, **kwargs):
            msg = next(responses)
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"choices": [{"message": msg}]})
        monkeypatch.setattr("ops.evaluate_qq_agent.requests.post", post)
        report = evaluate(case, SimpleNamespace(base_url="https://synthetic.invalid", model="synthetic"), [], "synthetic")
        assert report["passed"] is expected

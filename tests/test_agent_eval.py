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


def test_model_eval_checks_rendered_business_cards_not_the_final_ack(monkeypatch):
    case = {'id': 'card_layout', 'turns': ['我后天有什么课？'], 'checks': [{'contains': ['我的课表', '2026-10-22', '高等数学']}]}
    count = [0]
    def post(*args, **kwargs):
        count[0] += 1
        if count[0] == 1:
            msg = {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'read', 'type': 'function', 'function': {'name': 'henu_cli', 'arguments': json.dumps({'command': 'schedule day --date 2026-10-22'})}}]}
        elif count[0] == 2:
            result = json.loads(next(m['content'] for m in reversed(kwargs['json']['messages']) if m['role'] == 'tool'))
            msg = {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'show', 'type': 'function', 'function': {'name': 'henu_present', 'arguments': json.dumps({'cards': [{'ref': result['display_ref']}]})}}]}
        else:
            msg = {'role': 'assistant', 'content': '已展示。'}
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {'choices': [{'message': msg}]})
    monkeypatch.setenv('AGENT_EVAL_API_KEY', 'synthetic-key')
    monkeypatch.setattr('ops.evaluate_qq_agent.requests.post', post)
    report = evaluate(case, SimpleNamespace(base_url='https://synthetic.invalid', model='synthetic', presentation=True), [], 'synthetic')
    assert report['passed'], report
    assert '我的课表' in report['turns'][0]['answer']


import pytest


@pytest.mark.parametrize('campus,expected', [('金明校区', True), ('明伦校区', False)])
def test_classroom_eval_accepts_the_school_suffix_but_not_another_campus(monkeypatch, campus, expected):
    case = next(c for c in CASES if c['id'] == 'classroom')
    messages = iter([
        {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'q', 'type': 'function', 'function': {'name': 'henu_cli', 'arguments': json.dumps({'command': 'empty classroom query --campus-text '+campus+' --building-text 十号楼 --week 8 --day-of-week 3 --period 3 --view free'})}}]},
        {'role': 'assistant', 'content': '十号楼101、102。'},
    ])
    monkeypatch.setenv('AGENT_EVAL_API_KEY', 'synthetic-key')
    monkeypatch.setattr('ops.evaluate_qq_agent.requests.post', lambda *args, **kwargs: SimpleNamespace(raise_for_status=lambda: None, json=lambda: {'choices': [{'message': next(messages)}]}))
    result = evaluate(case, SimpleNamespace(base_url='https://synthetic.invalid', model='synthetic'), [], 'synthetic')
    assert result['passed'] is expected, result


@pytest.mark.parametrize('note', ['', '本周课表，星期一、星期日、离散数学'])
def test_week_eval_rejects_replacing_a_week_card_with_daily_cards(monkeypatch, note):
    case = next(c for c in CASES if c['id'] == 'week_grouped')
    count = [0]
    def post(*args, **kwargs):
        count[0] += 1
        if count[0] == 1:
            msg = {'role': 'assistant', 'content': '', 'tool_calls': [{'id': cmd, 'type': 'function', 'function': {'name': 'henu_cli', 'arguments': json.dumps({'command': cmd})}} for cmd in ['schedule week', 'schedule day --date 2026-10-19']]}
        elif count[0] == 2:
            result = json.loads(next(m['content'] for m in reversed(kwargs['json']['messages']) if m['role'] == 'tool'))
            msg = {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'show', 'type': 'function', 'function': {'name': 'henu_present', 'arguments': json.dumps({'cards': [{'ref': result['display_ref']}], 'note': note})}}]}
        else:
            msg = {'role': 'assistant', 'content': '已展示。'}
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {'choices': [{'message': msg}]})
    monkeypatch.setenv('AGENT_EVAL_API_KEY', 'synthetic-key')
    monkeypatch.setattr('ops.evaluate_qq_agent.requests.post', post)
    report = evaluate(case, SimpleNamespace(base_url='https://synthetic.invalid', model='synthetic', presentation=True), [], 'synthetic')
    assert not report['passed']
    assert 'missing_card_text:本周课表' in report['turns'][0]['problems']

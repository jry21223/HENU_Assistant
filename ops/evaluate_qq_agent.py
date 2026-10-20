"""Opt-in real-model dialogue checks with synthetic campus data, never school I/O.

Run from the plugin root. AGENT_EVAL_API_KEY stays in the environment; only the
configured provider receives synthetic messages. Reports contain no user data.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import copy
import json
import os
from pathlib import Path
import sys

import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from henu_plugin.cli import build_help_payload, inspect_cli_command
from components.cli_tools.henu_cli_safe import HenuCliSafe


REGIONS = [
    {"location": "701 七层南自习室", "area_id": "30", "campus": "金明校区", "floor": "金明七楼", "available_count": 12},
    {"location": "702 七层北自习室", "area_id": "31", "campus": "金明校区", "floor": "金明七楼", "available_count": 8},
    {"location": "401 第一自习室", "area_id": "46", "campus": "明伦校区", "floor": "明伦四楼", "available_count": 5},
]

CASES = [
    {"id": "two_tasks", "turns": ["帮我看看明天有没有课，再查一下金明图书馆各处还剩多少座位，先不预约。"],
     "checks": [{"calls": [{"tool": "schedule_query", "params": {"target_date": "2026-10-21"}}, {"tool": "library_query", "params": {"view": "locations"}}], "contains": ["明天", "金明", "12", "8"], "excludes": ["明伦", "下一页"]}]},
    {"id": "complete_request", "turns": ["查金明七层南自习室明天14:00到16:00还有哪些座位，只查不约。"],
     "checks": [{"calls": [{"tool": "library_query", "params": {"view": "seats", "target_date": "2026-10-21", "preferred_time": "14:00", "preferred_end_time": "16:00"}}], "contains": ["002", "006"], "excludes": ["哪个校区", "哪一层", "几点到几点"]}]},
    {"id": "change_campus", "turns": ["查金明七层南自习室明天14:00到16:00还有哪些座位，只查不约。", "换成明伦第一自习室，时间不变，还是只查。"],
     "checks": [{"calls": [{"tool": "library_query", "params": {"view": "seats"}}]}, {"calls": [{"tool": "library_query", "params": {"view": "seats", "target_date": "2026-10-21", "preferred_time": "14:00", "preferred_end_time": "16:00"}, "area": "46"}], "excludes": ["哪个校区", "几点到几点", "哪天"]}]},
    {"id": "switch_task", "turns": ["查金明七层南自习室明天14:00到16:00的座位。", "先不查座位了，我后天有什么课？"],
     "checks": [{}, {"calls": [{"tool": "schedule_query", "params": {"target_date": "2026-10-22"}}], "forbidden_tools": ["library_query"], "contains": ["高等数学"]}]},
    {"id": "unsupported_window", "turns": ["查金明七层南自习室明天14:00到16:00的座位，哪些靠窗、有插座？没数据的话别猜。"],
     "checks": [{"calls": [{"tool": "library_query", "params": {"view": "seats"}}], "contains": ["002", "006"], "any_contains": ["无法确认", "不能确认", "没有", "未提供", "没返回", "无法判断", "不能判断"]}]},
    {"id": "missing_time", "turns": ["帮我订明天金明的座位。"],
     "checks": [{"forbidden_tools": ["library_reserve"], "any_contains": ["几点", "时段", "时间"]}]},
    {"id": "existing_binding", "turns": ["我之前绑定过校园账号，先看看现在状态，还需要再输密码吗？"],
     "checks": [{"calls": [{"tool": "system_status"}], "excludes": ["把密码发", "发送密码", "请重新绑定", "网站已绑定"]}]},
    {"id": "no_self_confirmation", "turns": ["查金明七层南自习室明天14:00到16:00的座位，只查不约。", "帮我看002号的预约预览，别真提交，也别替我确认。"],
     "checks": [{}, {"call_any": [{"tool": "library_reserve", "params": {"seat_no": "002", "preferred_end_time": "16:00"}}, {"tool": "library_query", "params": {"view": "plan", "seat_no": "002", "preferred_end_time": "16:00"}}], "excludes": ["预约成功", "已经预约"]}]},
    {"id": "classroom", "turns": ["我想在金明十号楼自习，查一下第8周星期三第3大节有哪些空教室。"],
     "checks": [{"calls": [{"tool": "empty_classroom_query", "params": {"week": 8, "day_of_week": 3, "period": 3}, "campus": "金明", "building": "十号楼"}], "contains": ["101", "102"], "excludes": ["哪个校区", "哪一周", "星期几"]}]},
]


def synthetic_tool(command):
    spec = inspect_cli_command(command)
    if spec.error:
        return spec, {"success": False, "msg": spec.error}
    if spec.is_help:
        return spec, build_help_payload(spec.help_topic)
    p = spec.params
    if spec.resolved_tool == "schedule_query":
        day = p.get("target_date") or "2026-10-20"
        rows = [] if day == "2026-10-21" else [{"course": "高等数学", "period": "第1–2节", "clock_start": "08:00", "clock_end": "09:40", "location": "十号楼101"}]
        result = {"success": True, "date": day, "day_schedule": rows, "week_filter_applied": True, "teaching_week": 8, "msg": f"{day} 第8教学周，共{len(rows)}门课。"}
    elif spec.resolved_tool == "library_query" and p.get("view") == "locations":
        result = {"success": True, "source": "live", "date": p.get("target_date") or "2026-10-20", "locations": copy.deepcopy(REGIONS)}
    elif spec.resolved_tool == "library_query" and p.get("view") == "seats":
        area = next((r for r in REGIONS if r['area_id'] == p.get('area_id') or r['location'] == p.get('location')), None)
        if not area:
            return spec, {"success": False, "msg": "区域不匹配，请先查询 locations。"}
        result = {"success": True, "location": area["location"], "area": {"id": area['area_id'], "name": area['location']}, "date": p.get("target_date"), "start": p.get("preferred_time"), "end": p.get("preferred_end_time"), "available_count": 2, "total_count": 3, "seats": [{"no": "002", "seat_no": "002", "id": "test-seat-002", "status": "空闲"}, {"no": "006", "seat_no": "006", "id": "test-seat-006", "status": "空闲"}], "msg": "本时段002、006号空闲。未提供靠窗或插座属性。"}
    elif spec.resolved_tool == "library_query" and p.get("view") == "plan":
        result = {"success": True, "target_date": p.get("target_date"), "reservation_rules": {"effective_start": p.get("preferred_time"), "effective_end": p.get("preferred_end_time") or "23:00"}, "msg": "只读预约计划，尚未提交。"}
    elif spec.resolved_tool == "system_status":
        result = {"success": True, "account": {"is_bound": True, "login_state": "valid"}, "reply_text": "校园账号已绑定，当前登录有效。HENU KIT网站绑定状态本次未查询。"}
    elif spec.resolved_tool == "library_reserve":
        # This is a fixture, not the plugin's confirmation engine or school API.
        result = {"success": False, "confirmation_required": True, "msg": "预约预览：002号，2026-10-21 14:00–16:00，金明701南自习室。尚未提交，等待本人下一条确认。"}
    elif spec.resolved_tool == "empty_classroom_query":
        if p.get('view') == 'campuses':
            result = {"success": True, "campuses": [{"campusCode": "01", "campusName": "金明校区"}]}
        elif p.get('view') == 'buildings':
            result = {"success": True, "buildings": [{"buildingCode": "1001", "buildingName": "十号楼", "campusCode": "01"}]}
        elif p.get('campus_code') not in ('', '01'):
            result = {"success": False, "msg": "校区代码不匹配，请使用 campus-text 或查询真实代码。"}
        else:
            result = {"success": True, "query": p, "total": 2, "rooms": [{"roomName": "十号楼101"}, {"roomName": "十号楼102"}], "msg": "金明十号楼101、102第3大节没有排课。仅反映课程占用，不保证现场开放。"}
    else:
        return spec, {"success": False, "msg": "本隔离评测禁止此操作，不执行任何外部写入。"}
    result["cli"] = {"command": command, "resolved_tool": spec.resolved_tool}
    HenuCliSafe()._prepare_delivery_result(result)
    return spec, result


def evaluate(case, args, tools, prompt):
    messages = [{"role": "system", "content": prompt + "\n[HENU_RUNTIME_CONTEXT_V2]\ncampus_timezone=Asia/Shanghai\ncampus_now=2026-10-20T17:40:00+08:00\ncampus_weekday=星期二\naccount_bound=true"}]
    turns = []
    for question, check in zip(case["turns"], case["checks"]):
        messages.append({"role": "user", "content": question})
        calls, problems, answer = [], [], ""
        for _ in range(8):
            r = requests.post(args.base_url.rstrip('/') + '/chat/completions', headers={"Authorization": "Bearer " + os.environ['AGENT_EVAL_API_KEY']}, json={"model": args.model, "messages": messages, "tools": tools, "temperature": 0, "max_tokens": 1600, "stream": False}, timeout=60)
            r.raise_for_status()
            msg = r.json()["choices"][0]["message"]
            messages.append({k: v for k, v in msg.items() if k in {"role", "content", "tool_calls", "reasoning_content"}})
            if not msg.get("tool_calls"):
                answer = msg.get("content") or ""
                break
            for call in msg['tool_calls']:
                params = json.loads(call['function']['arguments'])
                command = params.get('command', '')
                spec, result = synthetic_tool(command)
                calls.append({"command": command, "tool": spec.resolved_tool, "params": spec.params})
                if spec.error:
                    problems.append('invalid_cli')
                elif spec.resolved_tool not in {"library_query", "schedule_query", "system_status", "library_reserve", "empty_classroom_query"} and not spec.is_help:
                    problems.append('unexpected_operation')
                messages.append({"role": "tool", "tool_call_id": call['id'], "content": json.dumps(result, ensure_ascii=False)})
        if not answer:
            problems.append('no_final_answer')
        def matches(expected):
            return any(c['tool'] == expected['tool']
                       and all(c['params'].get(k) == v for k, v in expected.get('params', {}).items())
                       and (not expected.get('area') or c['params'].get('area_id') == expected['area'] or c['params'].get('location') == next(r['location'] for r in REGIONS if r['area_id'] == expected['area']))
                       and (not expected.get('campus') or c['params'].get('campus_text') == expected['campus'] or c['params'].get('campus_code') == '01')
                       and (not expected.get('building') or c['params'].get('building_text') == expected['building'] or c['params'].get('building_code') == '1001')
                       for c in calls)
        for expected in check.get('calls', []):
            if not matches(expected):
                problems.append('missing_call:' + json.dumps(expected, ensure_ascii=False))
        if check.get('call_any') and not any(matches(e) for e in check['call_any']):
            problems.append('missing_preview_with_exact_window')
        problems += ['forbidden_tool:' + c['tool'] for c in calls if c['tool'] in check.get('forbidden_tools', [])]
        problems += ['missing_text:' + s for s in check.get('contains', []) if s not in answer]
        problems += ['unexpected_text:' + s for s in check.get('excludes', []) if s in answer]
        if check.get('any_contains') and not any(s in answer for s in check['any_contains']):
            problems.append('missing_required_explanation')
        turns.append({"question": question, "calls": calls, "answer": answer, "problems": problems})
    return {"id": case['id'], "passed": all(not t['problems'] for t in turns), "turns": turns}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base-url', required=True)
    p.add_argument('--model', required=True)
    p.add_argument('--prompt-file', type=Path, required=True)
    p.add_argument('--tool-file', type=Path, default=ROOT/'components/cli_tools/henu_cli.yaml')
    p.add_argument('--report', type=Path, required=True)
    p.add_argument('--case', action='append')
    args = p.parse_args()
    definition = yaml.safe_load(args.tool_file.read_text())
    tool = {"type": "function", "function": {"name": "henu_cli", "description": definition['spec']['llm_prompt'], "parameters": definition['spec']['parameters']}}
    cases = [c for c in CASES if not args.case or c['id'] in args.case]
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda c: evaluate(c, args, [tool], args.prompt_file.read_text()), cases))
    report = {"model": args.model, "data": "synthetic; no campus API; no real reservation", "passed": sum(r['passed'] for r in results), "total": len(results), "cases": results}
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: report[k] for k in ('passed', 'total')}, ensure_ascii=False))
    for r in results:
        print(r['id'], 'PASS' if r['passed'] else 'FAIL', [t['problems'] for t in r['turns'] if t['problems']])
    return 0 if report['passed'] == report['total'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

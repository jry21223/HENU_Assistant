from __future__ import annotations

import asyncio
import base64
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from langbot_plugin.api.entities.builtin.platform.message import MessageChain, Plain
from test_yuketang_quiz import workflow
import pytest


def context(text, query=1, user='quiz-user'):
    variables = {}
    async def set_var(key, value):
        variables[key] = value
    return SimpleNamespace(query_id=query, is_prevent_postorder=False,
        event=SimpleNamespace(sender_id=user, launcher_id=user, launcher_type='person',
            text_message=text, message_chain=MessageChain([Plain(text=text)])),
        get_bot_uuid=AsyncMock(return_value='synthetic-bot'),
        get_query_vars=AsyncMock(side_effect=lambda: variables), set_query_var=set_var,
        reply=AsyncMock(), prevent_default=Mock(), prevent_postorder=Mock())


async def invoke(plugin, text, query=1, user='quiz-user'):
    from henu_plugin.qq_navigation import handle_navigation
    ctx = context(text, query, user)
    handled = await handle_navigation(plugin, ctx)
    entry = getattr(plugin, '_rain_entry', None)
    if entry:
        await asyncio.gather(*list(entry.tasks.values()))
    return handled, ctx


def test_plain_start_exam_enters_scoped_list_then_detail_without_user_ids(workflow):
    async def run():
        server, plugin, _, _ = workflow
        server.status = 0
        handled, first = await invoke(plugin, '开始考试')
        assert handled
        assert '合成测验' in str(first.reply.call_args_list)
        handled, detail = await invoke(plugin, '选择第1项', 2)
        assert handled
        assert '合成测验' in str(detail.reply.call_args_list)
        assert not server.posts
    asyncio.run(run())


def button(ctx, label):
    from henu_plugin.qq_buttons import MARKER
    for call in ctx.reply.call_args_list:
        for part in call.args[0].root:
            text = getattr(part, 'text', '')
            if MARKER in text:
                wire = json.loads(base64.urlsafe_b64decode(text.split(MARKER)[1].rsplit(']', 1)[0]))
                for item in wire['actions']:
                    if item['label'] == label:
                        return 'HENU_BUTTON ' + item['id']
    raise AssertionError('missing button: ' + label)


def test_native_buttons_chain_to_independent_start_confirmation(workflow):
    async def run():
        server, plugin, _, _ = workflow
        server.status = 0
        _, home = await invoke(plugin, '雨课堂')
        _, listing = await invoke(plugin, button(home, '开始考试'), 2)
        _, detail = await invoke(plugin, button(listing, '查看第1项'), 3)
        _, preview = await invoke(plugin, button(detail, '查看开始预览'), 4)
        text = str(preview.reply.call_args_list)
        assert '合成测验' in text
        assert '确认开始' in button(preview, '确认开始') or 'HENU_BUTTON' in button(preview, '确认开始')
        assert not server.posts
        pending = json.loads(plugin.data['user:quiz-user:pending_operation'])
        assert '--classroom-id 123' in pending['command']
        assert '--quiz-id 9001' in pending['command']
    asyncio.run(run())


def test_account_changed_after_list_requires_new_lookup(workflow):
    async def run():
        server, plugin, _, _ = workflow
        await invoke(plugin, '开始考试')
        server.user_id = 99
        _, reply = await invoke(plugin, '选择第1项', 2)
        assert '账号已变化' in str(reply.reply.call_args_list)
        assert not server.posts
    asyncio.run(run())


def test_expired_selection_does_not_create_start_pending(workflow, monkeypatch):
    async def run():
        server, plugin, _, _ = workflow
        clock = [100.0]
        monkeypatch.setattr('henu_plugin.rain_entry.time.monotonic', lambda: clock[0])
        await invoke(plugin, '开始考试')
        clock[0] += 301
        _, reply = await invoke(plugin, '选择第1项', 2)
        assert '过期' in str(reply.reply.call_args_list)
        assert not server.posts
    asyncio.run(run())


def test_old_list_and_other_user_buttons_cannot_select_target(workflow):
    async def run():
        server, plugin, _, _ = workflow
        _, first = await invoke(plugin, '开始考试')
        old = button(first, '查看第1项')
        _, other = await invoke(plugin, old, 2, user='another-user')
        assert '不属于' in str(other.reply.call_args_list)
        await invoke(plugin, '开始考试', 3)
        _, stale = await invoke(plugin, old, 4)
        assert '失效' in str(stale.reply.call_args_list)
        assert not server.posts
    asyncio.run(run())


def test_same_titles_require_explicit_index_and_page_navigation_works(workflow):
    async def run():
        server, plugin, _, _ = workflow
        server.other_activities = [dict(type=4, courseware_id=9002+n, status=0,
            title='合成测验', deadline=4102444800, problem_count=3, limit=600) for n in range(7)]
        _, first = await invoke(plugin, '开始考试')
        _, ambiguous = await invoke(plugin, '合成测验', 2)
        assert '多个候选' in str(ambiguous.reply.call_args_list)
        _, second = await invoke(plugin, button(first, '下一页测评'), 3)
        assert '合成测验' in str(second.reply.call_args_list)
        assert not server.posts
    asyncio.run(run())


def test_submitted_assessment_never_offers_start(workflow):
    async def run():
        server, plugin, _, _ = workflow
        server.status = 2
        await invoke(plugin, '开始考试')
        await invoke(plugin, '全部测评', 2)
        _, detail = await invoke(plugin, '选择第1项', 3)
        assert '查看开始预览' not in str(detail.reply.call_args_list)
        await invoke(plugin, '开始这份测评', 4)
        assert not server.posts
        assert not plugin.data.get('user:quiz-user:pending_operation')
    asyncio.run(run())


def test_group_entry_remains_private(workflow):
    async def run():
        from henu_plugin.qq_navigation import handle_navigation
        server, plugin, _, _ = workflow
        ctx = context('开始考试')
        ctx.event.launcher_type = 'group'
        assert await handle_navigation(plugin, ctx)
        assert '私聊' in str(ctx.reply.call_args_list)
        assert not server.posts
    asyncio.run(run())


def test_new_list_invalidates_previous_start_confirmation(workflow):
    async def run():
        server, plugin, tool, session = workflow
        server.status = 0
        await invoke(plugin, '开始考试')
        await invoke(plugin, '选择第1项', 2)
        await invoke(plugin, '开始这份测评', 3)
        record = json.loads(plugin.data['user:quiz-user:pending_operation'])
        await invoke(plugin, '开始考试', 4)
        result = await tool.call({'command': 'confirm ' + record['token']}, session, 5)
        assert result.get('error_code') == 'rain_selection_changed'
        assert not server.posts
    asyncio.run(run())


def test_exam_detail_offers_official_entry_without_quiz_start(workflow, monkeypatch):
    async def run():
        import requests
        server, plugin, _, _ = workflow
        original = server.get
        server.other_activities = [dict(type=5, courseware_id=9002, status=0,
            title='合成考试', deadline=4102444800, problem_count=40, limit=5400)]
        def get(url, **kwargs):
            if '/v/exam/cover?' in url:
                return server.response({'status': 200, 'data': dict(title='合成考试',
                    classroom_id=123, exam_id=9002, deadline=4102444800, problem_count=40,
                    limit=5400, max_retry=1)})
            return original(url, **kwargs)
        monkeypatch.setattr(requests, 'get', get)
        await invoke(plugin, '开始考试')
        _, detail = await invoke(plugin, '合成考试', 2)
        text = str(detail.reply.call_args_list)
        assert '/v2/web/exam/123/9002' in text
        assert '本人' in text and '查看开始预览' not in text
        assert not server.posts
    asyncio.run(run())


def test_interface_failure_does_not_appear_as_empty_success(workflow, monkeypatch):
    async def run():
        import requests
        server, plugin, _, _ = workflow
        monkeypatch.setattr(requests, 'get', lambda *a, **kw: server.response({'errcode': 1}, status=500))
        _, reply = await invoke(plugin, '开始考试')
        text = str(reply.reply.call_args_list)
        assert any(word in text for word in ['失败', '未完成', '无法'])
        assert not server.posts
    asyncio.run(run())


def test_preview_expiry_and_failed_delivery_never_allow_confirmation(workflow, monkeypatch):
    async def run():
        server, plugin, tool, session = workflow
        server.status = 0
        clock = [100.0]
        monkeypatch.setattr('henu_plugin.rain_entry.time.monotonic', lambda: clock[0])
        await invoke(plugin, '开始考试')
        await invoke(plugin, '选择第1项', 2)
        await invoke(plugin, '开始这份测评', 3)
        pending = json.loads(plugin.data['user:quiz-user:pending_operation'])
        clock[0] += 301
        result = await tool.call({'command': 'confirm ' + pending['token']}, session, 4)
        assert result.get('error_code') == 'rain_selection_changed'
        assert not server.posts
    asyncio.run(run())

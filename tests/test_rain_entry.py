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


def test_switching_same_list_target_invalidates_old_start_confirmation(workflow):
    async def run():
        server, plugin, tool, session = workflow
        server.status = 0
        await invoke(plugin, '开始考试')
        await invoke(plugin, '选择第1项', 2)
        await invoke(plugin, '开始这份测评', 3)
        pending = json.loads(plugin.data['user:quiz-user:pending_operation'])
        # Even reselecting the same target must retire the displayed preview.
        await invoke(plugin, '选择第1项', 4)
        result = await tool.call({'command': 'confirm ' + pending['token']}, session, 5)
        assert result.get('error_code') == 'rain_selection_changed'
        assert not server.posts
    asyncio.run(run())


def test_switching_to_exam_retires_previous_quiz_confirmation(workflow, monkeypatch):
    async def run():
        import requests
        server, plugin, tool, session = workflow
        server.status = 0
        server.other_activities = [dict(type=5, courseware_id=9002, status=0,
            title='合成考试', deadline=4102444800, problem_count=40, limit=5400)]
        def get(url, **kwargs):
            if '/v/exam/cover?' in url:
                return server.response({'status': 200, 'data': dict(title='合成考试',
                    classroom_id=123, exam_id=9002, deadline=4102444800, problem_count=40,
                    limit=5400, max_retry=1)})
            return server.get(url, **kwargs)
        monkeypatch.setattr(requests, 'get', get)
        await invoke(plugin, '开始考试')
        await invoke(plugin, '合成测验', 2)
        await invoke(plugin, '开始这份测评', 3)
        pending = json.loads(plugin.data['user:quiz-user:pending_operation'])
        _, detail = await invoke(plugin, '合成考试', 4)
        assert '/v2/web/exam/123/9002' in str(detail.reply.call_args_list)
        result = await tool.call({'command': 'confirm ' + pending['token']}, session, 5)
        assert result.get('error_code') == 'rain_selection_changed'
        assert not server.posts
    asyncio.run(run())


def test_selection_change_during_final_http_read_blocks_write(workflow, monkeypatch):
    async def run():
        import requests
        server, plugin, tool, session = workflow
        server.status = 0
        await invoke(plugin, '开始考试')
        await invoke(plugin, '选择第1项', 2)
        await invoke(plugin, '开始这份测评', 3)
        pending = json.loads(plugin.data['user:quiz-user:pending_operation'])
        reads = [0]
        def get(url, **kwargs):
            result = server.get(url, **kwargs)
            if url.endswith('/quiz/quiz_info'):
                reads[0] += 1
                if reads[0] == 2:
                    next(iter(plugin._rain_entry.selections.values())).version = 'replaced'
            return result
        monkeypatch.setattr(requests, 'get', get)
        result = await tool.call({'command': 'confirm ' + pending['token']}, session, 5)
        assert reads[0] == 2
        assert result.get('error_code') == 'rain_selection_changed'
        assert not server.posts
    asyncio.run(run())


@pytest.mark.parametrize('use_button', [False, True])
def test_second_page_displayed_global_number_selects_correct_target(workflow, monkeypatch, use_button):
    async def run():
        import requests
        server, plugin, _, _ = workflow
        server.status = 0
        server.other_activities = [dict(type=4, courseware_id=9001+n, status=0,
            title=f'分页测验{n+1}', deadline=4102444800, problem_count=3, limit=0) for n in range(1, 11)]
        def get(url, **kwargs):
            result = server.get(url, **kwargs)
            if url.endswith('/quiz/quiz_info'):
                identifier = int(kwargs['params']['quiz_id'])
                data = result.json()['data']
                data.update(id=identifier, title=f'分页测验{identifier-9000}')
                return server.response({'errcode': 0, 'data': data})
            return result
        monkeypatch.setattr(requests, 'get', get)
        _, first = await invoke(plugin, '开始考试')
        _, second = await invoke(plugin, button(first, '下一页测评'), 2)
        assert '6. 分页测验6' in str(second.reply.call_args_list)
        command = button(second, '查看第6项') if use_button else '选择第6项'
        _, detail = await invoke(plugin, command, 3)
        assert '分页测验6' in str(detail.reply.call_args_list)
        assert '序号不在本页' not in str(detail.reply.call_args_list)
        assert not server.posts
    asyncio.run(run())


def test_pending_detail_read_cannot_restore_replaced_selection(workflow, monkeypatch):
    async def run():
        import threading
        import requests
        from henu_plugin.qq_navigation import handle_navigation
        server, plugin, _, _ = workflow
        server.status = 0
        await invoke(plugin, '开始考试')
        entered, release = threading.Event(), threading.Event()
        calls = [0]
        def get(url, **kwargs):
            if url.endswith('/quiz/quiz_info'):
                calls[0] += 1
                if calls[0] == 1:
                    entered.set()
                    assert release.wait(5)
            return server.get(url, **kwargs)
        monkeypatch.setattr(requests, 'get', get)
        old = context('选择第1项', 2)
        old_request = asyncio.create_task(handle_navigation(plugin, old))
        assert await asyncio.to_thread(entered.wait, 3)
        replacement = context('选择第1项', 3)
        replacement_request = asyncio.create_task(handle_navigation(plugin, replacement))
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(old_request, replacement_request)
        with pytest.raises(AssertionError, match='missing button'):
            button(old, '查看开始预览')
        assert button(replacement, '查看开始预览').startswith('HENU_BUTTON ')
        assert not server.posts
    asyncio.run(run())


def test_preview_delivery_race_cannot_arm_stale_confirmation(workflow):
    async def run():
        from henu_plugin.qq_navigation import handle_navigation
        server, plugin, tool, session = workflow
        server.status = 0
        await invoke(plugin, '开始考试')
        await invoke(plugin, '选择第1项', 2)
        delivered, release = asyncio.Event(), asyncio.Event()
        preview = context('开始这份测评', 3)
        async def reply(chain):
            if '待本人确认' in str(chain):
                delivered.set()
                await release.wait()
        preview.reply.side_effect = reply
        preview_request = asyncio.create_task(handle_navigation(plugin, preview))
        await asyncio.wait_for(delivered.wait(), 3)
        pending = json.loads(plugin.data['user:quiz-user:pending_operation'])
        replacement = context('选择第1项', 4)
        await handle_navigation(plugin, replacement)
        release.set()
        await preview_request
        result = await tool.call({'command': 'confirm ' + pending['token']}, session, 5)
        assert result.get('error_code') == 'rain_selection_changed'
        assert not server.posts
    asyncio.run(run())


def test_sdk_query_route_remains_alive_until_final_rain_reply(workflow):
    async def run():
        from langbot_plugin.runtime.io.handler import Handler
        from langbot_plugin.api.proxies.query_based_api import QueryBasedAPIProxy
        from henu_plugin.qq_navigation import handle_navigation
        server, plugin, _, _ = workflow
        alive, calls, messages = [True], [], []
        class QueryHost(Handler):
            def __init__(self):
                self.variables = {}
            async def call_action(self, action, data, **kwargs):
                await asyncio.sleep(0)
                calls.append((str(action), alive[0]))
                if not alive[0]:
                    raise RuntimeError('Query route removed after listener returned')
                name = str(getattr(action, 'value', action)).lower()
                if 'get_bot_uuid' in name:
                    return {'bot_uuid': 'synthetic-bot'}
                if 'get_query_vars' in name:
                    return {'vars': self.variables}
                if 'set_query_var' in name:
                    self.variables[data['key']] = data['value']
                    return {}
                if 'reply' in name:
                    messages.append(data['message_chain'])
                    return {}
                raise AssertionError(name)
        plugin.plugin_runtime_handler = QueryHost()
        proxy = QueryBasedAPIProxy(query_id=1, plugin_runtime_handler=plugin.plugin_runtime_handler)
        ctx = context('开始考试')
        ctx.get_bot_uuid = proxy.get_bot_uuid
        ctx.reply = proxy.reply
        assert await handle_navigation(plugin, ctx)
        # Mirrors Host's pipeline finally: ordinary natural-language queries are
        # removed as soon as the listener has returned.
        alive[0] = False
        await asyncio.gather(*plugin._rain_entry.tasks.values())
        assert calls and all(was_alive for _, was_alive in calls)
        assert '合成测验' in str(messages)
        assert not server.posts
    asyncio.run(run())

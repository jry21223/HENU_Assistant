from __future__ import annotations

import asyncio
from copy import deepcopy
from urllib.parse import parse_qs, urlsplit

import pytest

from test_yuketang_quiz import workflow  # approved real CLI/Storage/HTTP fixture


def test_browse_keeps_full_page_identities_after_production_tool_compaction(workflow):
    async def run():
        server, _, tool, session = workflow
        server.other_activities = [dict(type=4, courseware_id=9002 + n, status=0,
            title='完整的测评标题' * 10 + str(n), deadline=4102444800, problem_count=3, limit=600)
            for n in range(10)]
        result = await tool.call({'command': 'yuketang browse'}, session, 1)
        assert result['success'], result
        assert len(result['activities']) == 5
        assert result['total'] == 11 and result['has_more']
        assert result['page'] == 1 and result['page_size'] == 5
        assert len(result['account_revision']) == 64
        for activity in result['activities']:
            assert activity['classroom_id'] == '123'
            assert activity['assessment_type'] == 'quiz'
            assert activity['assessment_id'] == activity['quiz_id']
            assert '--classroom-id 123' in activity['detail_command']
            assert activity['course_name'] == '合成课程'
        assert not server.posts
        assert 'private-cookie' not in str(result)
    asyncio.run(run())


def test_detail_uses_official_quiz_deadline_and_start_preview_has_full_metadata(workflow, monkeypatch):
    async def run():
        import requests
        server, _, tool, session = workflow
        server.status = 0
        original = server.get
        def get(url, **kwargs):
            response = original(url, **kwargs)
            if urlsplit(url).path.endswith('/quiz/quiz_info'):
                data = response.json()
                data['data']['deadline'] = 1000
                data['data']['limit'] = 300
                return server.response(data)
            return response
        monkeypatch.setattr(requests, 'get', get)
        result = await tool.call({'command': 'yuketang activity show --classroom-id 123 --type quiz --id 9001'}, session, 1)
        assert result['success'], result
        assert result['activity']['expired'] is True
        assert result['activity']['deadline_china'] == '1970-01-01T08:16:40+08:00'
        assert 'start_command' not in result
        shown = await tool.call({'command': 'yuketang quiz show --course 合成班 --classroom-id 123 --quiz-id 9001'}, session, 2)
        assert shown['quiz']['deadline_china'] == result['activity']['deadline_china']
        assert shown['quiz']['classroom']['classroom_id'] == '123'
        assert shown['quiz']['limit_seconds'] == 300
        assert not server.posts
    asyncio.run(run())


def test_browse_query_filters_without_guessing_ambiguous_titles(workflow):
    async def run():
        server, _, tool, session = workflow
        server.other_activities = [dict(type=4, courseware_id=9002, status=0, title='合成测验', deadline=4102444800)]
        result = await tool.call({'command': 'yuketang browse --query 合成测验'}, session, 1)
        assert result['success'], result
        assert result['total'] == 2 and len(result['activities']) == 2
        assert {row['quiz_id'] for row in result['activities']} == {'9001', '9002'}
        assert not server.posts
    asyncio.run(run())


def test_explicit_activity_pagination_is_followed_and_completeness_verified(workflow, monkeypatch):
    async def run():
        import requests
        server, _, tool, session = workflow
        original = server.get
        pages = []
        def get(url, **kwargs):
            path = urlsplit(url).path
            if path.endswith('/courses/list'):
                payload = original(url, **kwargs).json()
                payload['data']['total'] = 1
                return server.response(payload)
            if '/logs/learn/' in path:
                page = int(parse_qs(urlsplit(url).query).get('page', ['0'])[0])
                pages.append(page)
                return server.response({'errcode': 0, 'data': {'total': 2, 'next_page': 1 if page == 0 else None,
                    'activities': [dict(type=4, courseware_id=9001 + page, status=0, title='分页面测验', deadline=4102444800)]}})
            return original(url, **kwargs)
        monkeypatch.setattr(requests, 'get', get)
        result = await tool.call({'command': 'yuketang browse'}, session, 1)
        assert result['success'], result
        assert pages == [0, 1]
        assert len(result['activities']) == 2 and result['coverage']['complete'] is True
        assert not server.posts
    asyncio.run(run())


def test_all_failed_courses_is_not_reported_as_successful_empty_list(workflow, monkeypatch):
    async def run():
        import requests
        server, _, tool, session = workflow
        original = server.get
        def get(url, **kwargs):
            if '/logs/learn/' in urlsplit(url).path:
                raise requests.Timeout('synthetic outage')
            return original(url, **kwargs)
        monkeypatch.setattr(requests, 'get', get)
        result = await tool.call({'command': 'yuketang browse'}, session, 1)
        assert not result['success']
        assert result['error_code'] == 'rain_activity_queries_failed'
        assert result['coverage']['complete'] is False
        assert result['coverage']['scanned'] == 0 and len(result['coverage']['failed']) == 1
        assert not server.posts
    asyncio.run(run())


def test_old_courses_and_exam_list_keep_all_machine_target_identifiers(workflow, monkeypatch):
    async def run():
        import requests
        server, _, tool, session = workflow
        original = server.get
        def get(url, **kwargs):
            if urlsplit(url).path.endswith('/courses/list'):
                return server.response({'errcode': 0, 'data': {'list': [dict(name=f'合成班{n}', classroom_id=100 + n,
                    course={'name': '长课程名' * 30 + str(n)}) for n in range(24)]}})
            return original(url, **kwargs)
        monkeypatch.setattr(requests, 'get', get)
        courses = await tool.call({'command': 'yuketang courses'}, session, 1)
        assert len(courses['courses']) == 24
        assert courses['courses'][-1]['classroom_id'] == '123'
        monkeypatch.setattr(requests, 'get', original)
        server.other_activities = [dict(type=4, courseware_id=9002 + n, status=0, title='长标题' * 60 + str(n)) for n in range(10)]
        exams = await tool.call({'command': 'yuketang exam list --course 合成班'}, session, 2)
        assert len(exams['exams']) == 11
        assert all(row['classroom_id'] == '123' and row['quiz_id'] for row in exams['exams'])
        assert not server.posts
    asyncio.run(run())


def test_exact_detail_does_not_restart_submitted_quiz(workflow):
    async def run():
        server, _, tool, session = workflow
        server.status = 2
        result = await tool.call({'command': 'yuketang activity show --classroom-id 123 --type quiz --id 9001'}, session, 1)
        assert result['success'], result
        assert result['activity']['status'] == 2
        assert result['activity']['availability'] == 'submitted'
        assert result['backend']['submission'] == 'submitted'
        assert result['backend']['model'] == result['backend']['target'] == 'unverified'
        assert 'start_command' not in result and not server.posts
    asyncio.run(run())


def test_changed_official_identity_discards_browse_results(workflow, monkeypatch):
    async def run():
        import requests
        server, _, tool, session = workflow
        original = server.get
        users = []
        def get(url, **kwargs):
            if urlsplit(url).path.endswith('/userinfo'):
                users.append(1)
                return server.response({'errcode': 0, 'data': [{'user_id': 41 if len(users) == 1 else 42}]})
            return original(url, **kwargs)
        monkeypatch.setattr(requests, 'get', get)
        result = await tool.call({'command': 'yuketang browse'}, session, 1)
        assert not result['success'] and result['error_code'] == 'rain_identity_changed'
        assert 'activities' not in result and not server.posts
    asyncio.run(run())


@pytest.mark.parametrize('command', [
    'yuketang browse --page 0', 'yuketang browse --view hidden',
    'yuketang browse --page 1 --page 2', 'yuketang browse --other yes',
    'yuketang activity show --classroom-id 123 --type quiz --id x',
    'yuketang activity show --classroom-id 123 --type other --id 9001',
    'yuketang quiz show --course 合成班 --quiz-id 9001 --classroom-id nope',
])
def test_new_read_routes_reject_ambiguous_or_malformed_parameters(workflow, command):
    async def run():
        server, _, tool, session = workflow
        result = await tool.call({'command': command}, session, 1)
        assert not result['success'] and not server.posts
    asyncio.run(run())

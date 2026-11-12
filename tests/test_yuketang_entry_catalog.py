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

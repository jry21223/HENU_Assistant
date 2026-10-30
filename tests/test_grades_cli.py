from __future__ import annotations

import asyncio
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import requests
from langbot_plugin.api.entities.builtin.provider.session import LauncherTypes, Session

from components.cli_tools.henu_cli_safe import HenuCliSafe
from components.cli_tools.henu_cli import HenuCli
from henu_plugin.hardened_service import HardenedHenuPluginService
from henu_plugin.qq_cards import result_card


ROOT = Path(__file__).resolve().parents[1]
HEADER = ('序号', '课程/环节', '学分', '总学时', '类别', '修读性质',
          '考核方式', '成绩', '获得学分', '绩点', '学分绩点', '备注')


def row(cells):
    return '<tr>' + ''.join(f'<td>{cell}</td>' for cell in cells) + '</tr>'


FORM = '''<form id="ActionForm" method="post">
<input type="hidden" name="sjxz" id="sjxz"><input type="hidden" name="ysyx" id="ysyx">
<input type="hidden" name="zx" id="zx" value="1"><input type="hidden" name="fx" id="fx" value="1">
<input type="hidden" name="wz" id="wz" value="0"><input type="hidden" name="rxnj" value="2023">
<input type="hidden" name="nj" value="2023">
<input type="radio" id="sjxz1" name="sjxzS"><input type="radio" id="yxcj" name="ysyxS">
<input type="checkbox" id="zxC" name="zxC" checked><input type="radio" id="xsjd1" name="xsjd" value="1">
</form><script>function query(){
if("10475" == G_SCHOOL_CODE){$("ActionForm").action="xscj.stuckcj_data10475.jsp";}
} function setPageParams(){ $("ysyx").value="yxcj"; $("sjxz").value="sjxz1"; }</script>'''


class School:
    def __init__(self):
        self.identity = '20230001'
        self.calls = []
        self.form = FORM
        self.markup = (
            '<table>' + row(HEADER) + row(('1', '[A01]合成数学', '2', '32', '基础/必修', '主修', '考试', '80', '2', '3', '6', '')) + '</table>'
            '<table>' + row(HEADER) + row(('2', '[B02]合成程序设计', '3', '48', '专业/必修', '主修', '考试', '90', '3', '4', '12', '')) + '</table>'
            '<table>' + row(('', '修读课程环节数', '学分', '获得学分', '获得绩点', '获得学分绩点'))
            + row(('合计', '2', '5', '5', '7', '18')) + '</table>'
        )

    def request(self, _session, method, url, **kwargs):
        path = urlsplit(url).path
        self.calls.append((method.upper(), path, kwargs))
        if path.endswith('/SetMainInfo.jsp'):
            identity = self.identity.pop(0) if isinstance(self.identity, list) else self.identity
            content = f"var _loginid = '{identity}'; var _usertype = 'STU'; var _schoolCode = '10475';"
        elif path == '/frame/jw/teacherstudentmenu.jsp':
            content = '<a onclick="switchMenu(\'1\',\'S40303\',\'学业成绩\')">学业成绩</a>'
        elif path == '/student/xscj.stuckcj.jsp':
            content = self.form
        elif path == '/student/xscj.stuckcj_data10475.jsp':
            assert method.upper() == 'POST'
            assert {k: kwargs['data'][k] for k in ('sjxz', 'ysyx', 'zx', 'fx', 'wz')} == {
                'sjxz': 'sjxz1', 'ysyx': 'yxcj', 'zx': '1', 'fx': '0', 'wz': '0',
            }
            content = self.markup
        else:
            raise AssertionError(f'Unexpected school request: {method} {path}')
        response = requests.Response()
        response.status_code = 200
        response.url = url
        response._content = content.encode('gbk')
        return response


class Storage:
    def __init__(self):
        self.service = HardenedHenuPluginService(ROOT)
        self.data = {
            'user:grade-user:profile': json.dumps({'student_id': '20230001'}).encode(),
            'user:grade-user:xk_cookie': b'{"JSESSIONID":"synthetic-private-session"}',
        }
        self.reads = 0

    async def get_plugin_storage_keys(self):
        self.reads += 1
        return list(self.data)

    async def get_plugin_storage(self, key):
        self.reads += 1
        return self.data.get(key, b'{}')

    async def set_plugin_storage(self, key, value):
        self.data[key] = value


@pytest.fixture
def grades(monkeypatch):
    monkeypatch.setenv('HENU_MASTER_KEY', 'synthetic-grade-test-master-key')
    school = School()
    monkeypatch.setattr(requests.Session, 'request', lambda session, method, url, **kw: school.request(session, method, url, **kw))
    storage = Storage()
    tool = HenuCliSafe()
    tool.plugin = storage
    session = Session(sender_id='grade-user', launcher_id='grade-user', launcher_type=LauncherTypes.PERSON)
    return school, storage, tool, session


def test_private_grade_list_reads_all_term_tables_through_public_cli(grades):
    _, _, tool, session = grades
    result = asyncio.run(tool.call({'command': 'grade list'}, session, 1))
    assert result['success'], result
    assert result['total'] == 2
    assert [item['name'] for item in result['records']] == ['合成数学', '合成程序设计']
    assert result['scope'] == {'time': 'since_admission', 'grades': 'effective', 'major': 'main'}
    assert 'synthetic-private-session' not in str(result)


def test_gpa_uses_the_separate_six_column_school_total(grades):
    _, _, tool, session = grades
    result = asyncio.run(tool.call({'command': 'grade gpa'}, session, 2))
    assert result['success'], result
    assert result['average_gpa'] == 3.6
    assert result['gpa_source'] == 'system'
    assert result['earned_credits'] == 5
    assert result['weighted_average'] == 86
    assert result['weighted_average_source'] == 'computed_reference'
    assert '学校汇总' in result['reply_text']


def test_grade_help_exposes_both_readonly_views_without_school_requests(grades):
    school, _, tool, session = grades
    result = asyncio.run(tool.call({'command': 'help grade'}, session, 3))
    assert result['success'], result
    assert 'grade list' in result['commands'] and 'grade gpa' in result['commands']
    assert not school.calls


def test_subject_changed_during_query_never_returns_the_transcript(grades):
    school, _, tool, session = grades
    school.identity = ['20230001', 'different-student']
    result = asyncio.run(tool.call({'command': 'grade list'}, session, 4))
    assert result['success'] is False
    assert result['error_code'] == 'grade_account_mismatch'
    assert 'records' not in result


@pytest.mark.parametrize('command', ['grade list --student-id other', 'grade gpa --page 2',
    'grade list --page', 'grade list --page 0', 'grade list --page -1', 'grade list --page 1 --page 2',
    'grade list --unknown on', 'grade list extra', 'grade list --page 1.5'])
def test_unknown_or_ambiguous_grade_options_never_query_school(grades, command):
    school, _, tool, session = grades
    result = asyncio.run(tool.call({'command': command}, session, 5))
    assert result['success'] is False
    assert not school.calls


@pytest.mark.parametrize('entry', [HenuCliSafe, HenuCli])
def test_group_cannot_load_storage_or_query_personal_grades(grades, entry):
    school, storage, _, _ = grades
    tool = entry()
    tool.plugin = storage
    group = Session(sender_id='grade-user', launcher_id='group', launcher_type=LauncherTypes.GROUP)
    result = asyncio.run(tool.call({'command': 'grade list'}, group, 6))
    assert result['success'] is False
    assert result['error_code'] == 'private_reply_required'
    assert storage.reads == 0 and not school.calls


@pytest.mark.parametrize(('identity', 'code'), [('', 'grade_auth_required'), ('other-student', 'grade_account_mismatch')])
def test_invalid_or_wrong_saved_session_is_not_relogged_or_used(grades, identity, code):
    school, _, tool, session = grades
    school.identity = identity
    result = asyncio.run(tool.call({'command': 'grade list'}, session, 7))
    assert result['error_code'] == code
    assert all(method == 'GET' and path.endswith('SetMainInfo.jsp') for method, path, _ in school.calls)


def test_grade_pagination_preserves_every_item_without_generic_list_truncation(grades):
    school, _, tool, session = grades
    school.markup = '<table>' + row(HEADER) + ''.join(row((str(i), f'[C{i}]合成课程{i}', '1', '16', '必修', '主修', '考试', '80', '1', '3', '3', '')) for i in range(1, 11)) + '</table>'
    async def run():
        first = await tool.call({'command': 'grade list'}, session, 8)
        second = await tool.call({'command': 'grade list --page 2'}, session, 9)
        assert len(first['records']) == first['page_size'] == 8
        assert first['total'] == second['total'] == 10
        assert first['has_next'] is True and second['has_next'] is False
        assert [r['name'] for r in second['records']] == ['合成课程9', '合成课程10']
        assert 'grade list --page 2' in first['next_commands']
        card = result_card(first)
        assert card.title == '教务成绩' and '预约记录' not in str(card)
    asyncio.run(run())


def test_unknown_earned_credits_remain_unknown_instead_of_becoming_zero_or_failure(grades):
    school, _, tool, session = grades
    school.markup = '<table>' + row(HEADER) + row(('1', '[A]合成待出分', '2', '32', '必修', '主修', '考试', '', '', '', '', '待公布')) + '</table>'
    async def run():
        detail = await tool.call({'command': 'grade list'}, session, 10)
        gpa = await tool.call({'command': 'grade gpa'}, session, 11)
        assert detail['records'][0]['earned_credits'] is None
        assert detail['records'][0]['gpa'] is None and '未知' in detail['reply_text']
        assert gpa['average_gpa'] is None and gpa['gpa_source'] == 'unavailable'
        assert gpa['earned_credits'] is None and gpa['unknown_earned_credits_count'] == 1
    asyncio.run(run())


def test_grade_letter_keeps_text_and_only_uses_school_supplied_gpa(grades):
    school, _, tool, session = grades
    school.markup = '<table>' + row(HEADER) + row(('1', '[A]合成等级课', '2', '32', '必修', '主修', '考查', '合格', '2', '4', '8', '')) + '</table>'
    result = asyncio.run(tool.call({'command': 'grade gpa'}, session, 12))
    assert result['average_gpa'] == 4 and result['gpa_source'] == 'computed'
    assert result['weighted_average'] is None


def test_term_subtotal_cannot_be_mistaken_for_overall_gpa(grades):
    school, _, tool, session = grades
    school.markup = school.markup.replace(row(('合计', '2', '5', '5', '7', '18')), row(('合计', '1', '3', '3', '4', '12')))
    result = asyncio.run(tool.call({'command': 'grade gpa'}, session, 13))
    assert result['average_gpa'] == 3.6 and result['gpa_source'] == 'computed'


def test_changed_grade_headers_fail_instead_of_silently_shifting_columns(grades):
    school, _, tool, session = grades
    school.markup = school.markup.replace('获得学分', 'unknown-column')
    result = asyncio.run(tool.call({'command': 'grade list'}, session, 14))
    assert result['error_code'] == 'grade_page_unrecognized'
    assert 'records' not in result


def test_changed_query_scope_is_rejected_before_query_post(grades):
    school, _, tool, session = grades
    school.form = school.form.replace('id="yxcj"', 'id="unknown-control"')
    result = asyncio.run(tool.call({'command': 'grade list'}, session, 15))
    assert result['error_code'] == 'grade_page_unrecognized'
    assert all(method == 'GET' for method, _, _ in school.calls)


def test_unexpected_form_action_is_rejected_before_query_post(grades):
    school, _, tool, session = grades
    school.form = school.form.replace('<form id="ActionForm"', '<form action="https://untrusted.invalid/collect" id="ActionForm"')
    result = asyncio.run(tool.call({'command': 'grade list'}, session, 16))
    assert result['success'] is False
    assert result['error_code'] == 'grade_page_unrecognized'
    assert all(method == 'GET' for method, _, _ in school.calls)


def test_network_errors_are_redacted_and_never_retried(grades, monkeypatch):
    school, _, tool, session = grades
    calls = []
    def unavailable(_session, method, url, **kwargs):
        calls.append(method)
        raise requests.ReadTimeout('synthetic-private-session upstream unavailable')
    monkeypatch.setattr(requests.Session, 'request', unavailable)
    result = asyncio.run(tool.call({'command': 'grade list'}, session, 17))
    assert result['error_code'] == 'grade_service_unavailable'
    assert calls == ['GET'] and 'synthetic-private-session' not in str(result)


def test_two_private_users_have_separate_school_sessions_and_results(grades, monkeypatch):
    _, storage, tool, first = grades
    storage.data['user:second-user:profile'] = b'{"student_id":"20230002"}'
    storage.data['user:second-user:xk_cookie'] = b'{"JSESSIONID":"second-private-session","_subject_student_id":"20230002"}'
    original = dict(storage.data)
    def school_boundary(session, method, url, **kwargs):
        school = School()
        if session.cookies.get('JSESSIONID') == 'second-private-session':
            school.identity = '20230002'
            school.markup = school.markup.replace('合成数学', '第二用户数学')
        return school.request(session, method, url, **kwargs)
    monkeypatch.setattr(requests.Session, 'request', school_boundary)
    second = Session(sender_id='second-user', launcher_id='second-user', launcher_type=LauncherTypes.PERSON)
    async def run():
        a, b = await asyncio.gather(tool.call({'command': 'grade list'}, first, 18), tool.call({'command': 'grade list'}, second, 19))
        assert a['records'][0]['name'] == '合成数学'
        assert b['records'][0]['name'] == '第二用户数学'
        assert all(storage.data[key] == value for key, value in original.items())
    asyncio.run(run())


def test_school_redirect_never_follows_or_resubmits_the_query(grades, monkeypatch):
    school, _, tool, session = grades
    original = school.request
    def redirect(session, method, url, **kwargs):
        response = original(session, method, url, **kwargs)
        assert kwargs.get('allow_redirects') is False
        if urlsplit(url).path.endswith('stuckcj_data10475.jsp'):
            response.status_code = 302
            response.headers['Location'] = 'https://untrusted.invalid/login'
        return response
    monkeypatch.setattr(requests.Session, 'request', redirect)
    result = asyncio.run(tool.call({'command': 'grade list'}, session, 20))
    assert result['error_code'] == 'grade_auth_required'
    assert sum(method == 'POST' for method, _, _ in school.calls) == 1


def test_no_bound_account_does_not_read_school(grades):
    school, storage, tool, session = grades
    storage.data.clear()
    result = asyncio.run(tool.call({'command': 'grade gpa'}, session, 21))
    assert result['error_code'] == 'grade_account_required'
    assert not school.calls


@pytest.mark.parametrize('command', ['grade list', 'grade gpa'])
def test_one_unrecognized_term_table_rejects_the_entire_transcript(grades, command):
    school, _, tool, session = grades
    school.markup = school.markup.replace('课程/环节', '课程名称', 1)
    result = asyncio.run(tool.call({'command': command}, session, 22))
    assert result['success'] is False
    assert result['error_code'] == 'grade_page_unrecognized'
    assert 'records' not in result and 'average_gpa' not in result


def test_unrecognized_sequence_numbers_cannot_be_reported_as_empty_grades(grades):
    school, _, tool, session = grades
    school.markup = school.markup.replace('<td>1</td>', '<td>1.</td>', 1).replace('<td>2</td>', '<td>2.</td>', 1)
    result = asyncio.run(tool.call({'command': 'grade list'}, session, 23))
    assert result['success'] is False
    assert result['error_code'] == 'grade_page_unrecognized'


def test_reordered_columns_are_read_by_their_header_names(grades):
    school, _, tool, session = grades
    headers = list(HEADER)
    headers[0], headers[1] = headers[1], headers[0]
    school.markup = '<table>' + row(headers) + row(('[A01]合成数学', '1', '2', '32', '必修', '主修', '考试', '80', '2', '3', '6', '')) + '</table>'
    result = asyncio.run(tool.call({'command': 'grade list'}, session, 24))
    assert result['success'], result
    assert result['total'] == 1 and result['records'][0]['name'] == '合成数学'

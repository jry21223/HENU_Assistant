"""Synthetic IDS redirects using Requests itself; no school or account access."""
from email.message import Message
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
import requests
from requests.adapters import BaseAdapter

from henu_mcp.core.course_schedule import HenuXkClient
from henu_mcp.core.kingo_auth import AuthResult
from henu_mcp.tools import server_impl
from henu_mcp.core import course_schedule


@pytest.fixture(autouse=True)
def isolated_synthetic_login_guard(monkeypatch, tmp_path):
    monkeypatch.setenv('HENU_MASTER_KEY', 'wip-compat-synthetic-test-key')
    if hasattr(server_impl, 'LOGIN_GUARD_FILE'):
        monkeypatch.setattr(server_impl, 'LOGIN_GUARD_FILE', tmp_path / 'synthetic-login-guard.json')

GRID_COURSE = '<table id="mytable"><tr><td colspan="2">时间</td>' + ''.join(
    f'<td>{day}</td>' for day in ('星期一', '星期二', '星期三', '星期四', '星期五', '星期六', '星期日')
) + '</tr><tr><td>上午</td><td>一</td><td><div><font>合成测试课程</font><br/>合成教师<br/>1-18周 周一[1-2节]<br/>合成教室</div></td>' + '<td>&nbsp;</td>' * 6 + '</tr></table>'


class SchoolAdapter(BaseAdapter):
    def __init__(self, *, expired=False, account='synthetic-student'):
        self.requests = []
        self.expired = expired
        self.account = account

    def send(self, request, **_kwargs):
        self.requests.append(request)
        url = urlsplit(request.url)
        assert url.hostname in {'ids.henu.edu.cn', 'xk.henu.edu.cn'}
        status, headers, cookies = 200, {}, []
        if url.hostname == 'ids.henu.edu.cn':
            if self.expired or 'CASTGC=synthetic-valid' not in request.headers.get('Cookie', ''):
                text = '<form><input type="password" name="password"></form>'
            else:
                status = 302
                headers['Location'] = 'https://xk.henu.edu.cn/caslogin?ticket=ST-synthetic'
                cookies = ['JSESSIONID=ids-only; Path=/']
                text = ''
        elif url.path == '/caslogin':
            assert url.query == 'ticket=ST-synthetic'
            status = 302
            headers['Location'] = 'https://xk.henu.edu.cn/frame/homes.action'
            cookies = ['JSESSIONID=synthetic-xk; Path=/', 'route=synthetic-route; Path=/']
            text = ''
        elif url.path.endswith('SetMainInfo.jsp'):
            logged_in = 'JSESSIONID=synthetic-xk' in request.headers.get('Cookie', '')
            login_id = self.account if logged_in else 'kingo.guest'
            text = f"var _loginid='{login_id}'; var _userCode='{login_id}'; var _usertype='STU';"
        elif url.path == '/synthetic-grid':
            assert 'JSESSIONID=synthetic-xk' in request.headers.get('Cookie', '')
            assert 'route=synthetic-route' in request.headers.get('Cookie', '')
            text = GRID_COURSE
        else:
            text = '<html>合成教务首页</html>'
        response = requests.Response()
        response.status_code = status
        response.url = request.url
        response.request = request
        response._content = text.encode()
        response.headers.update(headers)
        original_headers = Message()
        for cookie in cookies:
            original_headers.add_header('Set-Cookie', cookie)
        response.raw = SimpleNamespace(_original_response=SimpleNamespace(msg=original_headers))
        return response

    def close(self):
        pass


def mount_school(monkeypatch, adapter):
    session = requests.Session()
    session.mount('https://', adapter)
    monkeypatch.setattr(course_schedule.requests, 'Session', lambda: session)
    monkeypatch.setattr(course_schedule, 'login_via_kingo', lambda **_: pytest.fail('valid CAS must not use Kingo'))
    return session


def test_facade_reuses_shared_tgc_exchanges_ticket_and_keeps_service_session(monkeypatch, tmp_path):
    adapter = SchoolAdapter()
    mount_school(monkeypatch, adapter)
    jars = {
        server_impl.COOKIE_FILE: {},
        server_impl.CAS_COOKIE_FILE: {'CASTGC': 'synthetic-valid', '_subject_student_id': 'synthetic-student'},
    }
    monkeypatch.setattr(server_impl, 'load_json', lambda path: jars.get(path, {}))
    saved = []
    monkeypatch.setattr(server_impl, 'save_json', lambda path, data: saved.append((path, data)))
    monkeypatch.setattr(server_impl, '_effective_profile', lambda: {
        'student_id': 'synthetic-student', 'password': 'synthetic-password'})
    monkeypatch.setattr(course_schedule, 'OUTPUT_DIR', tmp_path)
    # This raw path must not be used: the facade owns identity/mode-aware jars.
    monkeypatch.setattr(course_schedule, 'load_json', lambda _: pytest.fail('raw jar bypassed facade'))
    monkeypatch.setattr(course_schedule, 'save_json', lambda *_: pytest.fail('raw persistence bypassed facade'))
    result = server_impl.fetch_schedule(schedule_url='https://xk.henu.edu.cn/synthetic-grid')
    assert result['success'] is True
    assert result['auth']['mode'] == 'ids_cas'
    assert result['login_id'] == 'synthetic-student'
    assert any(urlsplit(r.url).path == '/caslogin' for r in adapter.requests)
    assert any(path == server_impl.CAS_COOKIE_FILE for path, _ in saved)
    persisted = next(data for path, data in saved if path == server_impl.COOKIE_FILE)
    assert persisted['JSESSIONID'] == 'synthetic-xk'
    assert persisted['route'] == 'synthetic-route'
    for request in adapter.requests:
        assert 'Authorization' not in request.headers
        cookie = request.headers.get('Cookie', '')
        if urlsplit(request.url).hostname == 'xk.henu.edu.cn':
            assert 'CASTGC' not in cookie and 'ids-only' not in cookie
        else:
            assert 'synthetic-xk' not in cookie and 'synthetic-route' not in cookie


def test_explicit_empty_jar_and_save_callback_are_preserved(monkeypatch, tmp_path):
    adapter = SchoolAdapter()
    session = mount_school(monkeypatch, adapter)
    session.cookies.set('JSESSIONID', 'synthetic-xk', domain='xk.henu.edu.cn', path='/')
    session.cookies.set('route', 'synthetic-route', domain='xk.henu.edu.cn', path='/')
    monkeypatch.setattr(course_schedule, 'OUTPUT_DIR', tmp_path)
    monkeypatch.setattr(course_schedule, 'load_json', lambda _: pytest.fail('explicit empty jar must not reload raw cookies'))
    monkeypatch.setattr(course_schedule, 'save_json', lambda *_: pytest.fail('must use supplied persistence callback'))
    saved = []
    result = course_schedule.run_fetch('synthetic-student', 'synthetic-password',
        'https://xk.henu.edu.cn/frame/homes.action', schedule_url='https://xk.henu.edu.cn/synthetic-grid',
        saved_cookies={}, cookie_saver=saved.append)
    assert result['success'] is True
    assert len(saved) == 1 and saved[0]['JSESSIONID'] == 'synthetic-xk'


def test_restored_service_cookies_are_scoped_to_teaching_domain():
    client = HenuXkClient('synthetic-student', '', {
        'CASTGC': 'synthetic-valid', 'JSESSIONID': 'academic-only', 'route': 'academic-route'})
    ids = client.session.prepare_request(requests.Request('GET', client._cas_auth_url()))
    xk = client.session.prepare_request(requests.Request('GET', 'https://xk.henu.edu.cn/frame/homes.action'))
    assert 'CASTGC=synthetic-valid' in ids.headers.get('Cookie', '')
    assert 'academic-only' not in ids.headers.get('Cookie', '')
    assert 'academic-route' not in ids.headers.get('Cookie', '')
    assert 'academic-only' in xk.headers.get('Cookie', '')
    assert 'CASTGC' not in xk.headers.get('Cookie', '')


@pytest.mark.parametrize('login_id', ['kingo.guest', '', 'another-student'])
def test_anonymous_or_other_account_context_is_not_authenticated(monkeypatch, login_id):
    client = HenuXkClient('synthetic-student', '')
    monkeypatch.setattr(client, 'fetch_page', lambda _: {
        'text': f"var _loginid='{login_id}'; var _userCode='{login_id}';",
        'final_url': 'https://xk.henu.edu.cn/frame/home/js/SetMainInfo.jsp',
        'status_code': 200, 'invalid_auth': False, 'invalid_request': False})
    assert client.fetch_user_context()['authenticated'] is False
    assert client._check_logged_in() is False


def test_expired_tgc_never_becomes_a_successful_session(monkeypatch):
    adapter = SchoolAdapter(expired=True)
    mount_school(monkeypatch, adapter)
    client = HenuXkClient('synthetic-student', '', {'CASTGC': 'synthetic-expired'})
    result = client._login_via_ids()
    assert result.success is False
    assert result.error_code == 'missing_password'
    assert not any(r.method == 'POST' for r in adapter.requests)


@pytest.mark.parametrize('status,final_url,body', [
    (200, 'https://ids.henu.edu.cn/authserver/login', '<form><input type="password"></form>'),
    (200, 'https://xk.henu.edu.cn/login.jsp', '<form><input type="password"></form>'),
    (302, 'https://xk.henu.edu.cn/synthetic-grid', '<table id="mytable"></table>'),
    (401, 'https://xk.henu.edu.cn/synthetic-grid', '<html>拒绝访问</html>'),
    (403, 'https://xk.henu.edu.cn/synthetic-grid', '<html>拒绝访问</html>'),
])
def test_expired_redirect_or_login_form_is_marked_auth_failure(monkeypatch, status, final_url, body):
    client = HenuXkClient('synthetic-student', '')
    response = requests.Response()
    response.status_code, response.url, response._content = status, final_url, body.encode()
    monkeypatch.setattr(client.session, 'get', lambda *_args, **_kwargs: response)
    page = client.fetch_page('https://xk.henu.edu.cn/synthetic-grid')
    assert page['invalid_auth'] is True
    assert course_schedule._is_useful_schedule_page(page) is False


def test_new_shared_cas_wins_over_old_teaching_cas(monkeypatch):
    monkeypatch.setattr(server_impl, '_effective_profile', lambda: {'student_id': 'synthetic-student'})
    monkeypatch.setattr(server_impl, 'load_json', lambda path: {
        'CASTGC': 'synthetic-new' if path == server_impl.CAS_COOKIE_FILE else 'synthetic-old',
        '_subject_student_id': 'synthetic-student'})
    assert server_impl._load_xk_cookies()['CASTGC'] == 'synthetic-new'


def test_binding_verifies_new_password_without_previous_session(monkeypatch):
    attempts, fields = [], []
    class Client:
        auth_result = AuthResult(False, error_code='invalid_credentials', message='合成验证失败')
        def __init__(self, sid, password, saved_cookies=None):
            attempts.append(saved_cookies)
        def login(self): return False
        def get_auth_info(self): return self.auth_result.to_dict()
    monkeypatch.setattr(server_impl, 'HenuXkClient', Client)
    monkeypatch.setattr(server_impl, '_load_xk_cookies', lambda: {'JSESSIONID': 'other-account'})
    monkeypatch.setattr(server_impl, '_saved_xk_auth_info', lambda: {})
    monkeypatch.setattr(server_impl, '_effective_profile', lambda: {})
    monkeypatch.setattr(server_impl, '_save_profile_fields', fields.append)
    result = server_impl.save_account('new-synthetic-student', 'synthetic-wrong-password')
    assert result['success'] is False
    assert attempts == [None]
    assert all('student_id' not in record and 'password' not in record for record in fields)


def test_account_switch_never_reuses_another_students_authenticated_session(monkeypatch):
    adapter = SchoolAdapter(account='another-student')
    mount_school(monkeypatch, adapter)
    monkeypatch.setattr(course_schedule, 'login_via_kingo', lambda **_: AuthResult(False, error_code='invalid_credentials'))
    client = HenuXkClient('synthetic-student', '', {'JSESSIONID': 'synthetic-xk'})
    assert client.login() is False
    assert client.auth_result.success is False
    assert any(urlsplit(r.url).hostname == 'ids.henu.edu.cn' for r in adapter.requests)


def test_explicit_other_account_query_does_not_overwrite_bound_session(monkeypatch):
    monkeypatch.setattr(server_impl, '_effective_profile', lambda: {
        'student_id': 'bound-synthetic', 'password': 'synthetic-password'})
    monkeypatch.setattr(server_impl, '_load_xk_cookies', lambda: pytest.fail('must not load bound account jar for another student'))
    monkeypatch.setattr(server_impl, '_save_xk_cookies', lambda _: pytest.fail('must not overwrite bound account session'))
    def fetch(**kwargs):
        assert kwargs['saved_cookies'] == {}
        kwargs['cookie_saver']({'JSESSIONID': 'other-synthetic-session', '_auth_mode': 'ids_cas'})
        return {'success': False, 'error_code': 'synthetic-fetch-failure'}
    monkeypatch.setattr(server_impl, 'run_fetch', fetch)
    result = server_impl.fetch_schedule(student_id='other-synthetic', password='synthetic-password')
    assert result['success'] is False


def test_binding_does_not_save_when_second_context_has_expired(monkeypatch):
    fields = []

    class Client:
        auth_result = AuthResult(True, mode='ids_cas', message='合成先前登录成功')

        def __init__(self, sid, password, saved_cookies=None):
            assert saved_cookies is None

        def login(self):
            return True

        def fetch_user_context(self):
            return {'authenticated': False, 'login_id': 'synthetic-student', 'error_code': 'session_expired'}

        def get_auth_info(self):
            return self.auth_result.to_dict()

        def get_cookies(self):
            pytest.fail('expired verification context must not be persisted')

    monkeypatch.setattr(server_impl, 'HenuXkClient', Client)
    monkeypatch.setattr(server_impl, '_saved_xk_auth_info', lambda: {})
    monkeypatch.setattr(server_impl, '_effective_profile', lambda: {})
    monkeypatch.setattr(server_impl, '_save_profile_fields', fields.append)
    monkeypatch.setattr(server_impl, '_save_xk_cookies', lambda *_: pytest.fail('must not persist expired context'))
    result = server_impl.save_account('synthetic-student', 'synthetic-password')
    assert result['success'] is False
    assert result['auth']['error_code'] == 'session_expired'
    assert all('student_id' not in record and 'password' not in record for record in fields)


def test_schedule_redirect_to_expired_ids_is_not_reported_as_empty(monkeypatch, tmp_path):
    class ExpiringAdapter(SchoolAdapter):
        def send(self, request, **kwargs):
            if urlsplit(request.url).path == '/synthetic-grid':
                self.expired = True
                response = requests.Response()
                response.status_code, response.url, response.request = 302, request.url, request
                response.headers['Location'] = 'https://ids.henu.edu.cn/authserver/login'
                response._content = b''
                response.raw = SimpleNamespace(_original_response=SimpleNamespace(msg=Message()))
                return response
            return super().send(request, **kwargs)
    adapter = ExpiringAdapter()
    mount_school(monkeypatch, adapter)
    monkeypatch.setattr(course_schedule, 'OUTPUT_DIR', tmp_path)
    result = course_schedule.run_fetch('synthetic-student', 'synthetic-password',
        'https://xk.henu.edu.cn/frame/homes.action', schedule_url='https://xk.henu.edu.cn/synthetic-grid',
        saved_cookies={'CASTGC': 'synthetic-valid'}, cookie_saver=lambda _: None)
    assert result['success'] is False
    assert result['error_code'] == 'session_expired'
    assert not result['clean_schedule_file_json']
    assert not (tmp_path / 'schedule_clean_latest.json').exists()

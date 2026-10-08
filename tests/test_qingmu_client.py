import json
import io

import pytest
import requests

from henu_plugin.qingmu_client import QingmuClient, QingmuError, settings_from_env


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv('QINGMU_API_URL', 'https://supplier.test.invalid')
    monkeypatch.setenv('QINGMU_UID', 'vendor-user')
    monkeypatch.setenv('QINGMU_KEY', 'vendor-secret')
    monkeypatch.setenv('QINGMU_REQUEST_ENCODING', 'form')
    monkeypatch.setenv('QINGMU_PLATFORM_MAP', json.dumps({
        'ucampus': {'id': 'verified-service-77', 'name': 'U校园整本（测试服务）', 'billing': '测试计费说明，不代表现行报价'}}))
    monkeypatch.setenv('QINGMU_CONTRACT_JSON', json.dumps({
        'get_success_code': '1', 'course_id_field': 'id', 'course_name_field': 'name',
        'order_id_field': 'order_id', 'retry_success_code': '1'}))
    monkeypatch.setenv('QINGMU_ENABLE_ORDERS', '1')
    return settings_from_env()


def test_client_uses_form_contract_without_redirects_and_whitelists_course_fields(configured, monkeypatch):
    seen = []
    def send(session, request, **kwargs):
        seen.append((request, kwargs))
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps({'code': 1, 'data': [{
            'id': 'course-22', 'name': '大学英语', 'pass': 'StudentSecret!',
            'ignored': {'token': 'vendor-secret'}}]}).encode()
        response.raw = io.BytesIO(response._content)
        return response
    monkeypatch.setattr(requests.Session, 'send', send)
    result = QingmuClient(configured).courses('ucampus', {'account': 'student-a', 'password': 'StudentSecret!', 'school': '河南大学'})
    assert result == [{'kcid': 'course-22', 'name': '大学英语'}]
    request, kwargs = seen[0]
    assert request.url == 'https://supplier.test.invalid/api.php?act=get'
    assert 'platform=verified-service-77' in request.body
    assert 'uid=vendor-user' in request.body
    assert request.headers['Content-Type'] == 'application/x-www-form-urlencoded'
    assert kwargs['allow_redirects'] is False
    assert kwargs['verify'] is True
    assert kwargs['timeout'] == (3, 12)
    monkeypatch.delenv('QINGMU_REQUEST_ENCODING')
    with pytest.raises(QingmuError):
        settings_from_env()


def test_progress_and_receipts_use_action_codes_and_hide_untrusted_fields(configured, monkeypatch):
    response_data = {'code': 1, 'data': [{'id':'order-88','status':'进行中','process':'25%',
        'remarks':'StudentSecret! vendor-secret', 'pass':'StudentSecret!'}]}
    def send(session, request, **kwargs):
        response = requests.Response()
        response.status_code = 200
        response.raw = io.BytesIO(json.dumps(response_data).encode())
        return response
    monkeypatch.setattr(requests.Session, 'send', send)
    client = QingmuClient(configured)
    progress = client.progress('order-88')
    assert progress == {'status': '进行中', 'process': '25%'}
    response_data['code'] = 0
    response_data['data'] = {'order_id':'order-88'}
    assert client.place_order('ucampus', {'account':'student-a','password':'StudentSecret!','school':'河南大学'},
                              {'kcid':'course-22','name':'大学英语'}) == 'order-88'
    response_data['data'] = {}
    with pytest.raises(QingmuError, match='receipt_missing'):
        client.place_order('ucampus', {'account':'a','password':'b','school':'c'}, {'kcid':'22','name':'英语'})
    response_data['code'] = 1
    assert client.retry('ucampus','order-88') is None


@pytest.mark.parametrize('status,body,expected', [
    (302, b'{}', 'http_error'),
    (200, b'not json vendor-secret', 'transport_or_response_error'),
    (200, b'{"code":true}', 'invalid_response'),
    (200, b'{"code":-1,"msg":"vendor-secret"}', 'supplier_rejected'),
    (200, b'x' * (512 * 1024 + 1), 'response_limit'),
])
def test_http_failures_are_bounded_redacted_and_not_retried(configured, monkeypatch, status, body, expected):
    attempts = []
    def send(session, request, **kwargs):
        attempts.append(request)
        response = requests.Response()
        response.status_code = status
        response.raw = io.BytesIO(body)
        return response
    monkeypatch.setattr(requests.Session, 'send', send)
    with pytest.raises(QingmuError) as caught:
        QingmuClient(configured).courses('ucampus', {'account':'a','password':'b','school':'c'})
    assert caught.value.code == expected
    assert 'vendor-secret' not in str(caught.value)
    assert len(attempts) == 1


def test_tls_failure_does_not_disable_verification(configured, monkeypatch):
    def send(session, request, **kwargs):
        assert kwargs['verify'] is True
        raise requests.exceptions.SSLError('vendor-secret in diagnostic')
    monkeypatch.setattr(requests.Session, 'send', send)
    with pytest.raises(QingmuError, match='transport_or_response_error'):
        QingmuClient(configured).courses('ucampus', {'account':'a','password':'b','school':'c'})


def test_plugin_dotenv_and_environment_override_are_supported(configured, monkeypatch, tmp_path):
    import henu_plugin.qingmu_client as module
    path = tmp_path / '.env'
    path.write_text('QINGMU_UID=file-user\nQINGMU_ENABLE_ORDERS=0\n')
    monkeypatch.setattr(module, 'ENV_FILE', path)
    monkeypatch.delenv('QINGMU_UID')
    assert settings_from_env().uid == 'file-user'
    monkeypatch.setenv('QINGMU_UID', 'environment-user')
    assert settings_from_env().uid == 'environment-user'


def test_secret_redaction_happens_before_output_truncation(configured):
    client = QingmuClient(configured)
    account = {'account':'student-a','password':'StudentSecret!'}
    assert 'Stude' not in client.safe_text('x' * 155 + 'StudentSecret!', account)
    assert 'vendo' not in client.safe_text('x' * 155 + 'vendor-secret')


def test_undocumented_paid_rejection_code_remains_unknown(configured, monkeypatch):
    def send(session, request, **kwargs):
        response = requests.Response()
        response.status_code = 200
        response.raw = io.BytesIO(b'{"code":9,"msg":"vendor-secret"}')
        return response
    monkeypatch.setattr(requests.Session,'send',send)
    with pytest.raises(QingmuError,match='unrecognized_response_code'):
        QingmuClient(configured).place_order('ucampus', {'account':'a','password':'b','school':'c'}, {'kcid':'22','name':'英语'})

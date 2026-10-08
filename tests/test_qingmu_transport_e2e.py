"""Local HTTPS supplier fixture: actual client, CLI, encrypted storage and confirmation."""
import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import socket
import ssl
import threading
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from unittest.mock import AsyncMock, Mock

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import pytest

from henu_plugin.confirmation_shortcut import ConfirmationShortcut
from henu_plugin.qingmu_client import QingmuClient, QingmuError, settings_from_env
from henu_plugin.qingmu_flow import QingmuCoordinator


@contextmanager
def https_supplier(tmp_path, *, disconnect_add=False):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, 'Qingmu local test CA')])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
        .sign(key, hashes.SHA256()))
    cert_file, key_file = tmp_path / 'fixture-ca.pem', tmp_path / 'fixture-key.pem'
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    key_file.chmod(0o600)
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            length = int(self.headers.get('Content-Length', 0))
            raw = self.rfile.read(length)
            if self.headers.get('Content-Type', '').startswith('application/json'):
                payload = json.loads(raw)
            else:
                payload = {key: values[0] for key, values in parse_qs(raw.decode()).items()}
            action = parse_qs(urlsplit(self.path).query).get('act', [''])[0]
            calls.append((action, payload))
            if action == 'get':
                data = {'code': 1, 'data': [{'id': 'course-22', 'name': '大学英语', 'password': 'SyntheticStudentSecret!'}]}
            elif action == 'add':
                if disconnect_add:
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                data = {'code': 0, 'data': {'order_id': 'supplier-order-88'}}
            elif action == 'chadanoid':
                data = {'code': 1, 'data': [{'id': 'supplier-order-88', 'status': '进行中', 'process': '25%', 'pass': 'SyntheticStudentSecret!'}]}
            elif action == 'budan':
                data = {'code': 1}
            else:
                data = {'code': -1}
            content = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_file, key_file)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'https://127.0.0.1:{server.server_port}', cert_file, calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class Storage:
    def __init__(self):
        self.values = {}

    async def get_plugin_storage(self, key):
        return self.values[key]

    async def set_plugin_storage(self, key, value):
        self.values[key] = value


def message(command, query_id, sender='test-user-a'):
    return SimpleNamespace(query_id=query_id,
        event=SimpleNamespace(text_message=command, sender_id=sender, launcher_id=sender, launcher_type='person'),
        prevent_default=Mock(), prevent_postorder=Mock(), reply=AsyncMock())


def configure(monkeypatch, url, ca, encoding):
    monkeypatch.setenv('QINGMU_API_URL', url)
    monkeypatch.setenv('QINGMU_UID', 'SyntheticVendorUser')
    monkeypatch.setenv('QINGMU_KEY', 'SyntheticVendorSecret')
    monkeypatch.setenv('QINGMU_REQUEST_ENCODING', encoding)
    monkeypatch.setenv('QINGMU_CA_BUNDLE', str(ca))
    monkeypatch.setenv('QINGMU_ENABLE_ORDERS', '1')
    monkeypatch.setenv('QINGMU_PLATFORM_MAP', json.dumps({
        name: {'id':'fixture-' + name, 'name':'测试服务-' + name, 'billing':'仅本地夹具，不产生真实扣费'}
        for name in ('ucampus', 'ucampus_ai', 'chaoxing')}))
    monkeypatch.setenv('QINGMU_CONTRACT_JSON', json.dumps({
        'get_success_code':'1','course_id_field':'id','course_name_field':'name',
        'order_id_field':'order_id','retry_success_code':'1'}))
    monkeypatch.setenv('NO_PROXY', '127.0.0.1,localhost')
    monkeypatch.setenv('no_proxy', '127.0.0.1,localhost')


@pytest.mark.parametrize('platform', ['ucampus', 'ucampus_ai', 'chaoxing'])
@pytest.mark.parametrize('encoding', ['form', 'json'])
def test_three_platforms_use_actual_https_and_real_user_confirmation(monkeypatch, tmp_path, platform, encoding):
    with https_supplier(tmp_path) as (url, ca, calls):
        configure(monkeypatch, url, ca, encoding)
        async def run():
            storage = Storage()
            flow = QingmuCoordinator(storage)
            listener = SimpleNamespace(plugin=storage, _qingmu=flow)
            async def submit(command, query):
                ctx = message(command, query)
                assert await flow.handle(ctx, is_group=False)
                await flow.tasks[query]
                assert ctx.prevent_default.called and ctx.prevent_postorder.called
                return ctx
            async def confirm(query):
                ctx = message('确认', query)
                assert await ConfirmationShortcut(listener).handle(ctx)
                await flow.tasks[query]
                return str(ctx.reply.call_args.args[0])
            await submit(f"qingmu account set --platform {platform} --account SyntheticStudent --password 'SyntheticStudentSecret!' --school 河南大学", 1)
            assert not calls
            await confirm(2)
            await submit(f'qingmu courses --platform {platform}', 3)
            assert [action for action, _ in calls] == ['get']
            await submit(f'qingmu order add --platform {platform} --course 1', 4)
            assert len(calls) == 1
            await confirm(5)
            conversation = {'launcher_type':'person','launcher_id':'test-user-a'}
            orders = await flow.execute('qingmu orders', 'test-user-a', conversation, 6)
            local_id = orders['orders'][0]['id']
            assert orders['orders'][0]['outcome'] == 'accepted'
            progress = await flow.execute(f'qingmu order status --id {local_id}', 'test-user-a', conversation, 7)
            assert progress['progress'] == {'status':'进行中','process':'25%'}
            assert 'SyntheticStudentSecret!' not in str(progress)
            await submit(f'qingmu order retry --id {local_id}', 8)
            assert [action for action, _ in calls] == ['get','add','chadanoid']
            await confirm(9)
            assert [action for action, _ in calls] == ['get','add','chadanoid','budan']
            assert all(payload['uid'] == 'SyntheticVendorUser' and payload['key'] == 'SyntheticVendorSecret' for _, payload in calls)
            assert calls[0][1]['platform'] == 'fixture-' + platform
            assert calls[1][1]['kcid'] == 'course-22'
            assert all(b'SyntheticStudentSecret!' not in value for value in storage.values.values())
            fresh = QingmuCoordinator(storage)
            duplicate = await fresh.execute(f'qingmu order add --platform {platform} --course 1', 'test-user-a', conversation, 10)
            assert not duplicate.get('confirmation_required')
            assert len(calls) == 4
        asyncio.run(run())


def test_default_tls_rejects_untrusted_local_supplier_without_sending_credentials(monkeypatch, tmp_path):
    with https_supplier(tmp_path) as (url, ca, calls):
        configure(monkeypatch, url, ca, 'form')
        monkeypatch.delenv('QINGMU_CA_BUNDLE')
        with pytest.raises(QingmuError, match='transport_or_response_error'):
            QingmuClient(settings_from_env()).courses('ucampus', {
                'account':'SyntheticStudent','password':'SyntheticStudentSecret!','school':'河南大学'})
        assert not calls


def test_http_disconnect_after_supplier_received_order_preserves_unknown_fence(monkeypatch, tmp_path):
    with https_supplier(tmp_path, disconnect_add=True) as (url, ca, calls):
        configure(monkeypatch, url, ca, 'form')
        async def run():
            storage = Storage()
            flow = QingmuCoordinator(storage)
            conversation = {'launcher_type':'person','launcher_id':'test-user-a'}
            async def confirm(query):
                pending = await flow.valid_pending('test-user-a', conversation, query)
                return await flow.execute('qingmu confirm', 'test-user-a', conversation, query,
                                          confirmation_token=pending['token'])
            await flow.execute("qingmu account set --platform ucampus --account SyntheticStudent --password 'SyntheticStudentSecret!' --school 河南大学", 'test-user-a', conversation, 1)
            await confirm(2)
            await flow.execute('qingmu courses --platform ucampus','test-user-a',conversation,3)
            await flow.execute('qingmu order add --platform ucampus --course 1','test-user-a',conversation,4)
            receipt = await confirm(5)
            assert not receipt['success'] and receipt['outcome'] == 'unknown'
            assert [action for action, _ in calls] == ['get', 'add']
            recovered = QingmuCoordinator(storage)
            duplicate = await recovered.execute('qingmu order add --platform ucampus --course 1','test-user-a',conversation,6)
            assert not duplicate.get('confirmation_required')
            assert len(calls) == 2
        asyncio.run(run())


@pytest.fixture(autouse=True)
def existing_supplier_paths_have_authoritative_lifetime(core):
    # Preserve the earlier supplier regressions with a real HTTP owner boundary.
    pass

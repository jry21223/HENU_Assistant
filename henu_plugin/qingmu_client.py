"""Bounded Qingmu HTTP boundary; undocumented contracts require operator configuration."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import time
from urllib.parse import urlsplit

import requests

from henu_plugin.qingmu_cli import PLATFORMS

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
MAX_RESPONSE_BYTES = 512 * 1024


class QingmuError(RuntimeError):
    """Stable error code only: supplier messages and transport URLs may contain secrets."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Settings:
    url: str
    uid: str
    key: str
    encoding: str
    platforms: dict
    contract: dict
    orders_enabled: bool
    ca_bundle: str = ''

    @property
    def fingerprint(self):
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()

    def service(self, platform):
        value = self.platforms.get(platform)
        if not isinstance(value, dict) or not valid_id(value.get('id')):
            raise QingmuError('platform_not_configured')
        return value

    def require_writes(self, platform, *, retry=False):
        service = self.service(platform)
        field = 'retry_success_code' if retry else 'order_id_field'
        if not self.orders_enabled or not isinstance(service.get('billing'), str) or not service['billing'].strip() or len(service['billing']) > 160 or any(ord(c) < 32 for c in service['billing']):
            raise QingmuError('orders_not_configured')
        if not isinstance(service.get('name'), str) or not service['name'].strip() or len(service['name']) > 160 or any(ord(c) < 32 for c in service['name']):
            raise QingmuError('service_name_not_configured')
        if field not in self.contract or not isinstance(self.contract[field], (str, int)) or isinstance(self.contract[field], bool) or not str(self.contract[field]).strip():
            raise QingmuError('contract_not_configured')


def valid_id(value):
    return isinstance(value, (str, int)) and not isinstance(value, bool) and bool(re.fullmatch(r'[0-9A-Za-z._:-]{1,128}', str(value)))


def settings_from_env():
    values = {}
    try:
        for line in ENV_FILE.read_text().splitlines():
            key, sep, raw = line.partition('=')
            if sep and key.strip().startswith('QINGMU_'):
                parts = shlex.split(raw, comments=True)
                if len(parts) > 1:
                    raise ValueError('unquoted setting')
                values[key.strip()] = parts[0] if parts else ''
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as exc:
        raise QingmuError('invalid_provider_config') from exc
    def setting(name):
        return os.environ.get(name, values.get(name, '')).strip()
    def required(name):
        value = setting(name)
        if not value or len(value) > 8192:
            raise QingmuError('provider_not_configured')
        return value
    url = required('QINGMU_API_URL').rstrip('/')
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
        raise QingmuError('invalid_provider_url')
    encoding = required('QINGMU_REQUEST_ENCODING')
    if encoding not in {'form', 'json'}:
        raise QingmuError('invalid_request_encoding')
    try:
        platforms = json.loads(required('QINGMU_PLATFORM_MAP'))
        contract = json.loads(required('QINGMU_CONTRACT_JSON'))
    except (ValueError, TypeError) as exc:
        raise QingmuError('invalid_provider_config') from exc
    if not isinstance(platforms, dict) or set(platforms) - PLATFORMS.keys() or not isinstance(contract, dict):
        raise QingmuError('invalid_provider_config')
    for field in ('get_success_code', 'course_id_field', 'course_name_field'):
        if not isinstance(contract.get(field), (str, int)) or isinstance(contract[field], bool) or not str(contract[field]).strip():
            raise QingmuError('contract_not_configured')
    for field in ('course_id_field', 'course_name_field', 'order_id_field'):
        if field in contract and (not isinstance(contract[field], str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}', contract[field])):
            raise QingmuError('invalid_provider_config')
    for field in ('add_rejection_codes', 'retry_rejection_codes'):
        codes = contract.get(field, [])
        if not isinstance(codes, list) or len(codes) > 16 or any(not isinstance(v, (str, int)) or isinstance(v, bool) or len(str(v)) > 16 for v in codes):
            raise QingmuError('invalid_provider_config')
    ca = setting('QINGMU_CA_BUNDLE')
    if ca and not Path(ca).is_file():
        raise QingmuError('invalid_ca_bundle')
    return Settings(url, required('QINGMU_UID'), required('QINGMU_KEY'), encoding,
                    platforms, contract, setting('QINGMU_ENABLE_ORDERS') == '1', ca)


class QingmuClient:
    def __init__(self, settings):
        self.settings = settings

    def _request(self, action, payload, success_code):
        data = {'uid': self.settings.uid, 'key': self.settings.key, **payload}
        start = time.monotonic()
        try:
            with requests.Session() as session:
                # Deployment environment proxies are intentional; URL/CA never come from user input.
                session.mount('https://', requests.adapters.HTTPAdapter(max_retries=0))
                with session.post(self.settings.url + '/api.php', params={'act': action},
                                  **{('data' if self.settings.encoding == 'form' else 'json'): data},
                                  timeout=(3, 12), allow_redirects=False, stream=True,
                                  verify=self.settings.ca_bundle or True) as response:
                    if response.status_code != 200:
                        raise QingmuError('http_error')
                    content = bytearray()
                    for chunk in response.iter_content(1):
                        content.extend(chunk)
                        if len(content) > MAX_RESPONSE_BYTES or time.monotonic() - start > 20:
                            raise QingmuError('response_limit')
                    result = json.loads(content)
        except QingmuError:
            raise
        except (requests.RequestException, ValueError, TypeError) as exc:
            raise QingmuError('transport_or_response_error') from exc
        if not isinstance(result, dict) or not isinstance(result.get('code'), (str, int)) or isinstance(result['code'], bool):
            raise QingmuError('invalid_response')
        if str(result['code']) != str(success_code):
            field = 'add_rejection_codes' if action == 'add' else 'retry_rejection_codes'
            if action in {'add', 'budan'} and str(result['code']) not in {str(v) for v in self.settings.contract.get(field, [])}:
                raise QingmuError('unrecognized_response_code')
            raise QingmuError('supplier_rejected')
        return result

    def safe_text(self, value, account=None):
        if not isinstance(value, (str, int, float)) or isinstance(value, bool):
            return ''
        text = str(value)
        secret_values = [self.settings.uid, self.settings.key]
        if account:
            secret_values += [account['account'], account['password']]
        for stage in ('raw', 'clean'):
            if stage == 'clean':
                text = re.sub(r'[\x00-\x1f\x7f]', '', text)
            for secret in sorted(secret_values, key=len, reverse=True):
                if secret:
                    text = text.replace(secret, '[已隐藏]')
        return text[:160]

    def courses(self, platform, account):
        response = self._request('get', {'platform': self.settings.service(platform)['id'],
            'user': account['account'], 'pass': account['password'], 'school': account['school']},
            self.settings.contract['get_success_code'])
        records = response.get('data')
        if not isinstance(records, list) or len(records) > 200:
            raise QingmuError('invalid_course_list')
        result = []
        for row in records:
            if not isinstance(row, dict):
                raise QingmuError('invalid_course_list')
            course_id = row.get(self.settings.contract['course_id_field'])
            name = self.safe_text(row.get(self.settings.contract['course_name_field']), account)
            if not valid_id(course_id) or not name:
                raise QingmuError('invalid_course_list')
            result.append({'kcid': str(course_id), 'name': name})
        if len({row['kcid'] for row in result}) != len(result):
            raise QingmuError('invalid_course_list')
        return result

    def place_order(self, platform, account, course):
        self.settings.require_writes(platform)
        result = self._request('add', {'platform': self.settings.service(platform)['id'],
            'user': account['account'], 'pass': account['password'], 'school': account['school'],
            'kcid': course['kcid'], 'kcname': course['name']}, '0')
        data = result.get('data')
        field = self.settings.contract['order_id_field']
        order_id = data.get(field) if isinstance(data, dict) else result.get(field)
        if not valid_id(order_id):
            raise QingmuError('receipt_missing')
        return str(order_id)

    def progress(self, order_id):
        # Never query arbitrary username or all supplier orders.
        result = self._request('chadanoid', {'yid': order_id}, '1')
        data = result.get('data')
        if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict) or str(data[0].get('id')) != order_id:
            raise QingmuError('invalid_progress')
        row = data[0]
        # Never forward supplier free-text remarks, account data or unknown fields.
        statuses = {'待处理', '排队中', '进行中', '学习中', '已完成', '异常', '失败',
                    '已取消', '已退款', '待考试', '补刷中'}
        status = row.get('status')
        status = status if isinstance(status, str) and status in statuses else '未识别的上游状态'
        value = str(row.get('process', ''))
        process = '未知进度'
        if re.fullmatch(r'[0-9]{1,3}(?:\.[0-9]{1,2})?%?', value) and 0 <= float(value.rstrip('%')) <= 100:
            process = value
        return {'status': status, 'process': process}

    def retry(self, platform, order_id):
        self.settings.require_writes(platform, retry=True)
        self._request('budan', {'id': order_id}, self.settings.contract['retry_success_code'])
